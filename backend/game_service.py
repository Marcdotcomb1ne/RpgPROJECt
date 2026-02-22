"""
Game Service
------------
Loop de jogo com suporte a:
- Tipos de cena (narrative / character_focus)
- NPCs emergentes com promoção automática
- Personagem do jogador
- Botão de avançar fase (sem ação)
- Narração de abertura ao criar save
"""

from database import SupabaseClient
from schemas import WorldState, PlayerAction, AIResponse
from ai_engine import (
    call_narrator, call_arc_analyst, call_summarizer,
    call_opening_narration,
)

SUMMARIZE_EVERY = 10
NPC_PROMOTE_THRESHOLD = 3  # aparições para virar NPC permanente automaticamente


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
        raise ValueError("Slot não encontrado")
    return row


async def _get_pack_context(db: SupabaseClient, pack_id: str | None) -> tuple[dict | None, list, list]:
    if not pack_id:
        return None, [], []
    try:
        pack = (await db.table("roleplay_worlds").select("*").eq("id", pack_id).execute()).first()
        if not pack:
            return None, [], []
        chars = (await db.table("roleplay_characters").select("*").eq("world_id", pack_id).execute()).as_list()
        bgs = (await db.table("roleplay_backgrounds").select("*").eq("world_id", pack_id).execute()).as_list()
        return pack, chars, bgs
    except Exception:
        return None, [], []


def _resolve_scene(world_state: WorldState, ai_response: AIResponse, characters: list[dict]) -> dict:
    """Resolve URLs e dados de personagens/backgrounds para o frontend."""
    # Background URL
    bg_url = None
    # (backgrounds são passados ao caller para não duplicar lookup)

    # Personagens ativos
    active_char_data = []
    if ai_response.scene_type == "character_focus" and ai_response.active_characters:
        char_map = {c["name"]: c for c in characters}
        for cname in ai_response.active_characters[:1]:  # máximo 1 em character_focus
            c = char_map.get(cname)
            if c:
                active_char_data.append({
                    "name": c["name"],
                    "image_url": c.get("image_url"),
                    "is_emergent": False,
                })
            else:
                # NPC emergente (sem imagem)
                active_char_data.append({
                    "name": cname,
                    "image_url": None,
                    "is_emergent": True,
                })

    return {"active_char_data": active_char_data}


async def initialize_save(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
) -> dict:
    """
    Gera narração de abertura ao criar um save.
    Chamado logo após criar o slot.
    """
    slot_row = await _get_slot(db, save_id, user_id)
    pack_id = slot_row.get("pack_id")
    player_info = slot_row.get("player_info") or {
        "name": slot_row.get("player_name", "Protagonista"),
        "description": slot_row.get("player_description", ""),
    }

    pack, characters, backgrounds = await _get_pack_context(db, pack_id)

    opening = await call_opening_narration(pack, player_info, backgrounds)

    # Salva narração de abertura
    await db.table("events_log").insert({
        "save_id": save_id,
        "type": "narration",
        "content": opening,
    }).execute()

    return {"narration": opening}


async def advance_phase(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
) -> dict:
    """Avança a fase do dia sem processar ação do jogador."""
    slot_row = await _get_slot(db, save_id, user_id)
    world_state = WorldState(**slot_row["world_state"])

    world_state = world_state.next_phase()

    await db.table("save_slots").update({
        "world_state": world_state.model_dump(),
    }).eq("id", save_id).eq("user_id", user_id).execute()

    phase_msgs = {
        "morning": "A manhã chegou. O dia recomeça.",
        "afternoon": "A tarde se instala. O ritmo da escola muda.",
        "night": "A noite cobre tudo. Poucos ainda estão por aqui.",
    }

    msg = phase_msgs.get(world_state.current_phase, "O tempo avança.")
    if world_state.current_phase == "morning" and world_state.current_day > 1:
        msg = f"Dia {world_state.current_day}. A manhã recomeça."

    await db.table("events_log").insert({
        "save_id": save_id,
        "type": "system",
        "content": f"[{world_state.current_phase.upper()} — DIA {world_state.current_day}] {msg}",
    }).execute()

    return {
        "narration": msg,
        "world_state": world_state.model_dump(),
        "current_day": world_state.current_day,
        "current_phase": world_state.current_phase,
        "scene_type": "narrative",
        "active_characters": [],
        "current_background_url": None,
    }


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
    player_info = {
        "name": slot_row.get("player_name", "Protagonista"),
        "description": slot_row.get("player_description", ""),
    }

    # 2. Valida input
    action = PlayerAction(raw_input=raw_input)
    if not action.is_valid_format():
        raise ValueError('Formato inválido. Use "fala entre aspas" e *ação entre asteriscos*')

    # 3. Carrega contexto do Pack
    pack, characters, backgrounds = await _get_pack_context(db, pack_id)

    # 4. Avança fase
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

    # 6. Chama IA
    ai_response: AIResponse = await call_narrator(
        action=action,
        world_state=world_state,
        memory_summary=memory_summary,
        recent_events=recent_events,
        pack=pack,
        player_info=player_info,
        characters=characters,
        backgrounds=backgrounds,
    )

    # 7. Aplica deltas de world_state
    state_dict = world_state.model_dump()
    if ai_response.world_state_deltas:
        for key, value in ai_response.world_state_deltas.items():
            if key in state_dict and isinstance(value, (int, float)):
                state_dict[key] = value

    # 8. Atualiza relacionamentos com personagens do Pack
    if ai_response.relationship_updates:
        rels = dict(state_dict.get("relationships", {}))
        for name, info in ai_response.relationship_updates.items():
            rels[name] = info
        state_dict["relationships"] = rels

    # 9. Processa NPCs emergentes
    emergent = dict(state_dict.get("emergent_npcs", {}))
    for npc_name, npc_data in (ai_response.emergent_npcs or {}).items():
        if npc_name in emergent:
            # Incrementa mention_count e atualiza dados
            existing = dict(emergent[npc_name])
            existing["mention_count"] = existing.get("mention_count", 1) + 1
            existing.update({k: v for k, v in npc_data.items() if k != "mention_count"})
            emergent[npc_name] = existing
        else:
            emergent[npc_name] = {**npc_data, "mention_count": 1, "promoted": False}
    state_dict["emergent_npcs"] = emergent

    # 10. Cena atual
    state_dict["scene_type"] = ai_response.scene_type
    if ai_response.background_hint:
        state_dict["current_background"] = ai_response.background_hint
    if ai_response.active_characters is not None:
        state_dict["active_characters"] = ai_response.active_characters

    # 11. Contadores
    state_dict["event_counter_global"] = state_dict["event_counter_global"] + 1
    state_dict["event_counter_arc"] = state_dict["event_counter_arc"] + 1
    world_state = WorldState(**state_dict)

    # 12. Loga eventos
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

    # 13. Análise de arcos
    arc_result = await (
        db.table("story_arcs").select("*")
        .eq("save_id", save_id).eq("status", "active").limit(1).execute()
    )
    active_arc = arc_result.first()
    arc_response = await call_arc_analyst(world_state, recent_events, active_arc)
    await _handle_arc_signal(db, save_id, world_state, arc_response, active_arc)

    # 14. Sumarização periódica
    if world_state.event_counter_global % SUMMARIZE_EVERY == 0:
        all_recent = list(reversed((await (
            db.table("events_log").select("type, content")
            .eq("save_id", save_id).order("created_at", desc=True)
            .limit(SUMMARIZE_EVERY * 2).execute()
        ).as_list()))
        )
        memory_summary = await call_summarizer(memory_summary, all_recent)

    # 15. Persiste slot
    await db.table("save_slots").update({
        "world_state": world_state.model_dump(),
        "memory_summary": memory_summary,
    }).eq("id", save_id).eq("user_id", user_id).execute()

    # 16. Resolve URLs para o frontend
    bg_url = None
    if world_state.current_background and backgrounds:
        for bg in backgrounds:
            if bg["name"].lower() == world_state.current_background.lower():
                bg_url = bg.get("image_url")
                break

    scene_data = _resolve_scene(world_state, ai_response, characters)

    return {
        "narration": ai_response.narration,
        "world_state": world_state.model_dump(),
        "current_day": world_state.current_day,
        "current_phase": world_state.current_phase,
        "scene_type": ai_response.scene_type,
        "current_background_url": bg_url,
        "active_characters": scene_data["active_char_data"],
        "emergent_npcs": world_state.emergent_npcs,
    }


async def promote_npc(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
    npc_name: str,
    image_url: str | None,
) -> dict:
    """Promove um NPC emergente a personagem permanente do Pack."""
    slot_row = await _get_slot(db, save_id, user_id)
    world_state = WorldState(**slot_row["world_state"])
    pack_id = slot_row.get("pack_id")

    if not pack_id:
        raise ValueError("Este save não tem um Pack vinculado")

    # Verifica se o usuário é dono do Pack
    pack = (await db.table("roleplay_worlds").select("owner_id").eq("id", pack_id).execute()).first()
    if not pack or pack["owner_id"] != user_id:
        raise ValueError("Apenas o criador do Pack pode adicionar personagens permanentes")

    npc_data = world_state.emergent_npcs.get(npc_name)
    if not npc_data:
        raise ValueError(f"NPC '{npc_name}' não encontrado nos NPCs emergentes")

    # Cria personagem no Pack
    personality = {
        "description": npc_data.get("description", ""),
        "personality": npc_data.get("personality", ""),
    }
    traits = npc_data.get("traits", {})

    new_char = (await db.table("roleplay_characters").insert({
        "world_id": pack_id,
        "name": npc_name,
        "image_url": image_url,
        "personality_json": personality,
        "base_traits_json": traits,
    }).execute()).as_list()

    # Marca como promovido no world_state
    state_dict = world_state.model_dump()
    state_dict["emergent_npcs"][npc_name]["promoted"] = True
    await db.table("save_slots").update({
        "world_state": state_dict,
    }).eq("id", save_id).execute()

    return {"message": f"{npc_name} promovido a personagem permanente", "character": new_char[0] if new_char else {}}


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
    if not (await db.table("save_slots").select("id").eq("id", save_id).eq("user_id", user_id).execute()).first():
        raise ValueError("Slot não encontrado")
    return (await db.table("events_log").select("id, type, content, created_at")
            .eq("save_id", save_id).order("created_at", desc=False).limit(limit).execute()).as_list()


async def get_slot_arcs(db: SupabaseClient, save_id: str, user_id: str) -> list:
    if not (await db.table("save_slots").select("id").eq("id", save_id).eq("user_id", user_id).execute()).first():
        raise ValueError("Slot não encontrado")
    return (await db.table("story_arcs").select("*")
            .eq("save_id", save_id).order("start_day", desc=False).execute()).as_list()