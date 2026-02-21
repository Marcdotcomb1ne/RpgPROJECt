"""
Game Service
------------
Orquestra o loop completo de acao:
1. Parse do input
2. Avanca fase do dia
3. Chama IA
4. Aplica deltas no world_state
5. Loga evento
6. Verifica arcos
7. Sumariza memoria periodicamente
"""

import asyncio

from database import SupabaseClient
from schemas import WorldState, PlayerAction, AIResponse
from ai_engine import call_narrator, call_arc_analyst, call_summarizer

SUMMARIZE_EVERY = 10


async def _get_slot(db: SupabaseClient, save_id: str, user_id: str) -> dict:
    result = await (
        db.table("save_slots")
        .select("*")
        .eq("id", save_id)
        .eq("user_id", user_id)
        .execute()
    )
    row = result.first()
    if not row:
        raise ValueError("Slot nao encontrado")
    return row


async def process_action(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
    raw_input: str,
) -> dict:
    # 1. Carrega slot
    slot_row = await _get_slot(db, save_id, user_id)
    world_state = WorldState(**slot_row["world_state"])
    memory_summary: str = slot_row.get("memory_summary", "")

    # 2. Valida input
    action = PlayerAction(raw_input=raw_input)
    if not action.is_valid_format():
        raise ValueError(
            'Formato invalido. Use "fala entre aspas" e *acao entre asteriscos*'
        )

    # 3. Avanca fase do dia
    world_state = world_state.next_phase()

    # 4. Eventos recentes para contexto da IA
    recent_result = await (
        db.table("events_log")
        .select("type, content, created_at")
        .eq("save_id", save_id)
        .order("created_at", desc=True)
        .limit(30)
        .execute()
    )
    recent_events = list(reversed(recent_result.as_list()))

    # 5. Chama IA
    ai_response: AIResponse = await call_narrator(
        action=action,
        world_state=world_state,
        memory_summary=memory_summary,
        recent_events=recent_events,
    )

    # 6. Aplica deltas
    if ai_response.world_state_deltas:
        state_dict = world_state.model_dump()
        for key, value in ai_response.world_state_deltas.items():
            if key in state_dict and isinstance(value, (int, float)):
                state_dict[key] = value
        world_state = WorldState(**state_dict)

    world_state = WorldState(**{
        **world_state.model_dump(),
        "event_counter_global": world_state.event_counter_global + 1,
        "event_counter_arc": world_state.event_counter_arc + 1,
    })

    # 7. Loga acao do jogador
    await db.table("events_log").insert({
        "save_id": save_id,
        "type": "player_action",
        "content": raw_input,
    }).execute()

    # 8. Loga narracao
    await db.table("events_log").insert({
        "save_id": save_id,
        "type": "narration",
        "content": ai_response.narration,
    }).execute()

    # 9. Analise de arcos
    arc_result = await (
        db.table("story_arcs")
        .select("*")
        .eq("save_id", save_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    active_arc = arc_result.first()

    arc_response: AIResponse = await call_arc_analyst(
        world_state=world_state,
        recent_events=recent_events,
        active_arc=active_arc,
    )
    await _handle_arc_signal(db, save_id, world_state, arc_response, active_arc)

    # 10. Sumarizacao periodica
    if world_state.event_counter_global % SUMMARIZE_EVERY == 0:
        all_recent_result = await (
            db.table("events_log")
            .select("type, content")
            .eq("save_id", save_id)
            .order("created_at", desc=True)
            .limit(SUMMARIZE_EVERY * 2)
            .execute()
        )
        all_recent = list(reversed(all_recent_result.as_list()))
        memory_summary = await call_summarizer(memory_summary, all_recent)

    # 11. Persiste slot atualizado
    await db.table("save_slots").update({
        "world_state": world_state.model_dump(),
        "memory_summary": memory_summary,
    }).eq("id", save_id).eq("user_id", user_id).execute()

    return {
        "narration": ai_response.narration,
        "world_state": world_state.model_dump(),
        "current_day": world_state.current_day,
        "current_phase": world_state.current_phase,
    }


async def _handle_arc_signal(
    db: SupabaseClient,
    save_id: str,
    world_state: WorldState,
    arc_response: AIResponse,
    active_arc: dict | None,
):
    signal = arc_response.arc_signal
    if not signal:
        return

    if signal == "start":
        await db.table("story_arcs").insert({
            "save_id": save_id,
            "title": arc_response.arc_title or "Novo Arco",
            "start_day": world_state.current_day,
            "status": "active",
            "summary": arc_response.arc_summary or "",
            "impact": "",
        }).execute()
        await db.table("events_log").insert({
            "save_id": save_id,
            "type": "arc_event",
            "content": f"[ARCO INICIADO] {arc_response.arc_title}",
        }).execute()

    elif signal == "close" and active_arc:
        await db.table("story_arcs").update({
            "status": "closed",
            "end_day": world_state.current_day,
            "summary": arc_response.arc_summary or active_arc.get("summary", ""),
            "impact": arc_response.arc_title or "",
        }).eq("id", active_arc["id"]).execute()
        await db.table("events_log").insert({
            "save_id": save_id,
            "type": "arc_event",
            "content": f"[ARCO ENCERRADO] {active_arc['title']}",
        }).execute()


async def get_slot_history(db: SupabaseClient, save_id: str, user_id: str, limit: int = 50) -> list:
    slot_result = await (
        db.table("save_slots")
        .select("id")
        .eq("id", save_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not slot_result.first():
        raise ValueError("Slot nao encontrado")

    raw = await (
        db.table("events_log")
        .select("id, type, content, created_at")
        .eq("save_id", save_id)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return raw.as_list()


async def get_slot_arcs(db: SupabaseClient, save_id: str, user_id: str) -> list:
    slot_result = await (
        db.table("save_slots")
        .select("id")
        .eq("id", save_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not slot_result.first():
        raise ValueError("Slot nao encontrado")

    raw = await (
        db.table("story_arcs")
        .select("*")
        .eq("save_id", save_id)
        .order("start_day", desc=False)
        .execute()
    )
    return raw.as_list()