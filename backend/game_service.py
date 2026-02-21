"""
Game Service
------------
Orquestra o loop completo de acao com suporte a Roleplay Packs.
"""

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


async def _get_pack_context(db: SupabaseClient, pack_id: str | None) -> tuple[dict | None, list, list]:
    """Returns (pack, characters, backgrounds) for the given pack_id."""
    if not pack_id:
        return None, [], []
    try:
        pack_result = await (
            db.table("roleplay_worlds")
            .select("*")
            .eq("id", pack_id)
            .execute()
        )
        pack = pack_result.first()
        if not pack:
            return None, [], []

        chars_result = await (
            db.table("roleplay_characters")
            .select("*")
            .eq("world_id", pack_id)
            .execute()
        )
        bgs_result = await (
            db.table("roleplay_backgrounds")
            .select("*")
            .eq("world_id", pack_id)
            .execute()
        )
        return pack, chars_result.as_list(), bgs_result.as_list()
    except Exception:
        return None, [], []


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
    pack_id: str | None = slot_row.get("pack_id")

    # 2. Valida input
    action = PlayerAction(raw_input=raw_input)
    if not action.is_valid_format():
        raise ValueError(
            'Formato invalido. Use "fala entre aspas" e *acao entre asteriscos*'
        )

    # 3. Carrega contexto do Pack
    pack, characters, backgrounds = await _get_pack_context(db, pack_id)

    # 4. Avanca fase do dia
    world_state = world_state.next_phase()

    # 5. Eventos recentes
    recent_result = await (
        db.table("events_log")
        .select("type, content, created_at")
        .eq("save_id", save_id)
        .order("created_at", desc=True)
        .limit(30)
        .execute()
    )
    recent_events = list(reversed(recent_result.as_list()))

    # 6. Chama IA com contexto do Pack
    ai_response: AIResponse = await call_narrator(
        action=action,
        world_state=world_state,
        memory_summary=memory_summary,
        recent_events=recent_events,
        pack=pack,
        characters=characters,
        backgrounds=backgrounds,
    )

    # 7. Aplica deltas de world_state
    state_dict = world_state.model_dump()
    if ai_response.world_state_deltas:
        for key, value in ai_response.world_state_deltas.items():
            if key in state_dict and isinstance(value, (int, float)):
                state_dict[key] = value

    # 8. Atualiza relacionamentos
    if ai_response.relationship_updates:
        rels = dict(state_dict.get("relationships", {}))
        for name, info in ai_response.relationship_updates.items():
            rels[name] = info
        state_dict["relationships"] = rels

    # 9. Atualiza background e personagens ativos se a IA sugerir
    if ai_response.background_hint:
        state_dict["current_background"] = ai_response.background_hint
    if ai_response.active_characters:
        state_dict["active_characters"] = ai_response.active_characters

    state_dict["event_counter_global"] = state_dict["event_counter_global"] + 1
    state_dict["event_counter_arc"] = state_dict["event_counter_arc"] + 1
    world_state = WorldState(**state_dict)

    # 10. Loga eventos
    await db.table("events_log").insert({
        "save_id": save_id,
        "type": "player_action",
        "content": raw_input,
    }).execute()

    await db.table("events_log").insert({
        "save_id": save_id,
        "type": "narration",
        "content": ai_response.narration,
    }).execute()

    # 11. Analise de arcos
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

    # 12. Sumarizacao periodica
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

    # 13. Persiste slot atualizado
    await db.table("save_slots").update({
        "world_state": world_state.model_dump(),
        "memory_summary": memory_summary,
        "last_played": "now()",
    }).eq("id", save_id).eq("user_id", user_id).execute()

    # Resolve background URL para o frontend
    current_bg_url = None
    if world_state.current_background and backgrounds:
        for bg in backgrounds:
            if bg["name"].lower() == world_state.current_background.lower():
                current_bg_url = bg.get("image_url")
                break

    # Resolve character images para o frontend
    active_char_data = []
    if world_state.active_characters and characters:
        char_map = {c["name"]: c for c in characters}
        for cname in world_state.active_characters[:3]:
            c = char_map.get(cname)
            if c:
                active_char_data.append({
                    "name": c["name"],
                    "image_url": c.get("image_url"),
                })

    return {
        "narration": ai_response.narration,
        "world_state": world_state.model_dump(),
        "current_day": world_state.current_day,
        "current_phase": world_state.current_phase,
        "current_background_url": current_bg_url,
        "active_characters": active_char_data,
    }


async def _handle_arc_signal(
    db: SupabaseClient,
    save_id: str,
    world_state: WorldState,
    arc_response: AIResponse,
    active_arc: dict | None,
):
    signal = arc_response.arc_signal
    if not signal or signal == "none":
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