"""
Game Service
------------
Loop de jogo com suporte a:
- Tipos de cena (narrative / character_focus)
- NPCs emergentes com promoção automática
- Personagem do jogador
- Botão de avançar fase com narração contextual de passagem de tempo (IA percebe o skip)
- Narração de abertura ao criar save (com background)
- Imagem customizável de NPC emergente sem promover ao Pack

FIXES:
- advance_phase agora chama call_time_skip_narration para a IA narrar o que aconteceu
- last_time_skip é injetado no world_state antes de chamar o narrador
- initialize_save retorna background_hint e ativa o background correto
- promote_npc usa custom_image_url do emergent_npc se image_url não for fornecida
- Novo set_npc_image para salvar imagem custom de NPC no world_state
"""

from database import SupabaseClient
from schemas import WorldState, PlayerAction, AIResponse
from ai_engine import (
    call_narrator, call_arc_analyst, call_summarizer,
    call_opening_narration, call_time_skip_narration,
)

SUMMARIZE_EVERY = 10
ARC_CHECK_EVERY = 7


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
        bgs   = (await db.table("roleplay_backgrounds").select("*").eq("world_id", pack_id).execute()).as_list()
        return pack, chars, bgs
    except Exception:
        return None, [], []


def _resolve_bg_url(background_hint: str | None, backgrounds: list[dict]) -> str | None:
    """Resolve nome do background para URL."""
    if not background_hint or not backgrounds:
        return None
    for bg in backgrounds:
        if bg["name"].lower() == background_hint.lower():
            return bg.get("image_url")
    return None


def _resolve_scene(world_state: WorldState, ai_response: AIResponse, characters: list[dict]) -> dict:
    """Resolve URLs e dados de personagens para o frontend."""
    active_char_data = []
    if ai_response.scene_type == "character_focus" and ai_response.active_characters:
        char_map = {c["name"]: c for c in characters}
        for cname in ai_response.active_characters[:1]:
            c = char_map.get(cname)
            # FIX: também verifica custom_image_url nos NPCs emergentes
            emergent_img = None
            emergent_data = (world_state.emergent_npcs or {}).get(cname, {})
            if isinstance(emergent_data, dict):
                emergent_img = emergent_data.get("custom_image_url")

            if c:
                active_char_data.append({
                    "name":        c["name"],
                    "image_url":   c.get("image_url"),
                    "is_emergent": False,
                })
            else:
                active_char_data.append({
                    "name":        cname,
                    "image_url":   emergent_img,
                    "is_emergent": True,
                })

    return {"active_char_data": active_char_data}


async def initialize_save(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
) -> dict:
    """
    FIX: Retorna narration + background_url (não mais só narration).
    Ativa o background correto desde o início.
    """
    slot_row  = await _get_slot(db, save_id, user_id)
    pack_id   = slot_row.get("pack_id")
    player_info = {
        "name":        slot_row.get("player_name", "Protagonista"),
        "description": slot_row.get("player_description", ""),
    }

    pack, characters, backgrounds = await _get_pack_context(db, pack_id)

    # FIX: call_opening_narration agora retorna dict {narration, background_hint}
    opening_data = await call_opening_narration(pack, player_info, backgrounds)
    narration      = opening_data.get("narration", "A história começa aqui.")
    background_hint = opening_data.get("background_hint")

    # Resolve background URL
    bg_url = _resolve_bg_url(background_hint, backgrounds)

    # Salva background no world_state se encontrado
    if background_hint:
        ws_result = await db.table("save_slots").select("world_state").eq("id", save_id).execute()
        ws_row    = ws_result.first()
        if ws_row:
            ws_dict = dict(ws_row["world_state"])
            ws_dict["current_background"] = background_hint
            validated = WorldState(**ws_dict)
            await db.table("save_slots").update({
                "world_state": validated.model_dump(),
            }).eq("id", save_id).execute()

    # Salva narração de abertura no log
    await db.table("events_log").insert({
        "save_id": save_id,
        "type":    "narration",
        "content": narration,
    }).execute()

    return {
        "narration":        narration,
        "background_url":   bg_url,
        "background_hint":  background_hint,
    }


async def advance_phase(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
) -> dict:
    """
    FIX: Avança a fase e usa a IA para narrar a passagem de tempo contextualmente.
    O world_state recebe last_time_skip preenchido, e a narração reflete
    o que aconteceu durante esse intervalo — personagens se dispersaram,
    ambiente mudou, etc.
    """
    slot_row = await _get_slot(db, save_id, user_id)
    world_state = WorldState(**slot_row["world_state"])
    pack_id     = slot_row.get("pack_id")

    # Carrega contexto do Pack
    pack, characters, backgrounds = await _get_pack_context(db, pack_id)

    # Carrega eventos recentes para contexto da narração
    recent_result = await (
        db.table("events_log")
        .select("type, content, created_at")
        .eq("save_id", save_id)
        .order("created_at", desc=True)
        .limit(15)
        .execute()
    )
    recent_events = list(reversed(recent_result.as_list()))

    # Avança a fase — last_time_skip é preenchido aqui
    world_state = world_state.next_phase()

    # FIX: chama IA para narrar a passagem de tempo
    skip_data = await call_time_skip_narration(
        world_state=world_state,
        pack=pack,
        recent_events=recent_events,
        backgrounds=backgrounds,
    )
    narration       = skip_data.get("narration", f"O tempo avança.")
    background_hint = skip_data.get("background_hint")

    # Atualiza background se a IA sugeriu um
    if background_hint:
        world_state = world_state.model_copy(update={"current_background": background_hint})

    bg_url = _resolve_bg_url(world_state.current_background, backgrounds)

    # Salva no banco
    await db.table("save_slots").update({
        "world_state": world_state.model_dump(),
    }).eq("id", save_id).eq("user_id", user_id).execute()

    # Loga o evento de sistema + a narração do skip
    phase_labels = {"morning": "Manhã", "afternoon": "Tarde", "night": "Noite"}
    system_msg = f"[{phase_labels.get(world_state.current_phase, world_state.current_phase).upper()} — DIA {world_state.current_day}]"

    await db.table("events_log").insert({
        "save_id": save_id,
        "type":    "system",
        "content": system_msg,
    }).execute()

    await db.table("events_log").insert({
        "save_id": save_id,
        "type":    "narration",
        "content": narration,
    }).execute()

    return {
        "narration":            narration,
        "character_dialogue":   None,
        "world_state":          world_state.model_dump(),
        "current_day":          world_state.current_day,
        "current_phase":        world_state.current_phase,
        "scene_type":           "narrative",
        "active_characters":    [],
        "current_background_url": bg_url,
    }


async def process_action(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
    raw_input: str,
) -> dict:
    # 1. Carrega slot
    slot_row     = await _get_slot(db, save_id, user_id)
    world_state  = WorldState(**slot_row["world_state"])
    memory_summary: str = slot_row.get("memory_summary", "")
    pack_id: str | None = slot_row.get("pack_id")
    player_info = {
        "name":        slot_row.get("player_name", "Protagonista"),
        "description": slot_row.get("player_description", ""),
    }

    # 2. Valida input
    action = PlayerAction(raw_input=raw_input)
    if not action.is_valid_format():
        raise ValueError('Formato inválido. Use "fala entre aspas" e *ação entre asteriscos*')

    # 3. Carrega contexto do Pack
    pack, characters, backgrounds = await _get_pack_context(db, pack_id)

    # 4. Eventos recentes — inclui eventos de sistema (time skips)
    # Nota: last_time_skip já está no world_state se o jogador chamou advance_phase antes.
    # A IA vai perceber isso no prompt e narrar as consequências. Após processar, limpamos.
    recent_result = await (
        db.table("events_log")
        .select("type, content, created_at")
        .eq("save_id", save_id)
        .order("created_at", desc=True)
        .limit(30)
        .execute()
    )
    recent_events = list(reversed(recent_result.as_list()))

    # 6. Chama IA — world_state ainda tem last_time_skip se veio de advance_phase
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

    # 7. Limpa last_time_skip após a IA processar
    world_state = world_state.clear_time_skip()

    # 8. Aplica deltas de world_state
    state_dict = world_state.model_dump()
    if ai_response.world_state_deltas:
        for key, value in ai_response.world_state_deltas.items():
            if key in state_dict and isinstance(value, (int, float)):
                state_dict[key] = value

    # 9. Atualiza relacionamentos
    if ai_response.relationship_updates:
        rels = dict(state_dict.get("relationships", {}))
        for name, info in ai_response.relationship_updates.items():
            rels[name] = info
        state_dict["relationships"] = rels

    # 10. Processa NPCs emergentes
    emergent = dict(state_dict.get("emergent_npcs", {}))
    for npc_name, npc_data in (ai_response.emergent_npcs or {}).items():
        if npc_name in emergent:
            existing = dict(emergent[npc_name])
            existing["mention_count"] = existing.get("mention_count", 1) + 1
            # FIX: preserva custom_image_url ao fazer merge
            custom_img = existing.get("custom_image_url")
            existing.update({k: v for k, v in npc_data.items() if k != "mention_count"})
            if custom_img:
                existing["custom_image_url"] = custom_img
            emergent[npc_name] = existing
        else:
            emergent[npc_name] = {**npc_data, "mention_count": 1, "promoted": False}
    state_dict["emergent_npcs"] = emergent

    # 11. Cena atual
    state_dict["scene_type"] = ai_response.scene_type
    if ai_response.background_hint:
        state_dict["current_background"] = ai_response.background_hint
    if ai_response.active_characters is not None:
        state_dict["active_characters"] = ai_response.active_characters

    # 12. Contadores
    state_dict["event_counter_global"] = state_dict["event_counter_global"] + 1
    state_dict["event_counter_arc"]    = state_dict["event_counter_arc"] + 1
    world_state = WorldState(**state_dict)

    # 13. Loga eventos
    await db.table("events_log").insert({
        "save_id": save_id,
        "type":    "player_action",
        "content": raw_input,
    }).execute()

    # Se tem diálogo separado do personagem, salva narração + diálogo como eventos distintos
    speaker = ai_response.active_characters[0] if ai_response.active_characters else None

    if ai_response.character_dialogue and ai_response.scene_type == "character_focus" and speaker:
        # Narração de contexto (ambiente, ações)
        if ai_response.narration:
            await db.table("events_log").insert({
                "save_id": save_id,
                "type":    "narration",
                "content": ai_response.narration,
            }).execute()
        # Diálogo direto com speaker embutido no content usando prefixo
        await db.table("events_log").insert({
            "save_id": save_id,
            "type":    "character_speech",
            "content": f"{speaker}||{ai_response.character_dialogue}",
        }).execute()
    elif ai_response.scene_type == "character_focus" and speaker:
        # IA não separou o diálogo (modelo fraco ou falhou) — trata narration como fala do personagem
        await db.table("events_log").insert({
            "save_id": save_id,
            "type":    "character_speech",
            "content": f"{speaker}||{ai_response.narration}",
        }).execute()
    else:
        await db.table("events_log").insert({
            "save_id": save_id,
            "type":    "narration",
            "content": ai_response.narration,
        }).execute()

    # 14. Análise de arcos
    if world_state.event_counter_arc % ARC_CHECK_EVERY == 0:
        arc_result = await (
            db.table("story_arcs").select("*")
            .eq("save_id", save_id).eq("status", "active").limit(1).execute()
        )
        active_arc   = arc_result.first()
        arc_response = await call_arc_analyst(world_state, recent_events, active_arc)
        await _handle_arc_signal(db, save_id, world_state, arc_response, active_arc)

    # 15. Sumarização periódica
    if world_state.event_counter_global % SUMMARIZE_EVERY == 0:
        recent_result = await (
            db.table("events_log")
            .select("type, content")
            .eq("save_id", save_id)
            .order("created_at", desc=True)
            .limit(SUMMARIZE_EVERY * 2)
            .execute()
        )
        all_recent = list(reversed(recent_result.as_list()))
        memory_summary = await call_summarizer(memory_summary, all_recent)

    # 16. Persiste slot
    await db.table("save_slots").update({
        "world_state":   world_state.model_dump(),
        "memory_summary": memory_summary,
    }).eq("id", save_id).eq("user_id", user_id).execute()

    # 17. Resolve URLs para o frontend
    bg_url = _resolve_bg_url(world_state.current_background, backgrounds)

    # FIX: _resolve_scene agora passa world_state para pegar custom_image_url de NPCs
    scene_data = _resolve_scene(world_state, ai_response, characters)

    return {
        "narration":          ai_response.narration,
        "character_dialogue": ai_response.character_dialogue,  # FIX: inclui diálogo separado
        "world_state":        world_state.model_dump(),
        "current_day":        world_state.current_day,
        "current_phase":      world_state.current_phase,
        "scene_type":         ai_response.scene_type,
        "current_background_url": bg_url,
        "active_characters":  scene_data["active_char_data"],
        "emergent_npcs":      world_state.emergent_npcs,
        "pack_characters": [{"name": c["name"], "image_url": c.get("image_url")} for c in characters],
    }


async def set_npc_image(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
    npc_name: str,
    image_url: str | None,
) -> dict:
    """
    FIX: Salva imagem customizada de NPC emergente no world_state.
    Persiste no banco sem promover ao Pack.
    """
    slot_row    = await _get_slot(db, save_id, user_id)
    world_state = WorldState(**slot_row["world_state"])

    emergent = dict(world_state.emergent_npcs or {})
    if npc_name not in emergent:
        raise ValueError(f"NPC '{npc_name}' não encontrado")

    npc_data = dict(emergent[npc_name])
    if image_url:
        npc_data["custom_image_url"] = image_url
    else:
        npc_data.pop("custom_image_url", None)

    emergent[npc_name] = npc_data
    state_dict = world_state.model_dump()
    state_dict["emergent_npcs"] = emergent

    validated = WorldState(**state_dict)
    await db.table("save_slots").update({
        "world_state": validated.model_dump(),
    }).eq("id", save_id).eq("user_id", user_id).execute()

    return {"message": f"Imagem de {npc_name} atualizada", "image_url": image_url}


async def promote_npc(
    db: SupabaseClient,
    save_id: str,
    user_id: str,
    npc_name: str,
    image_url: str | None,
) -> dict:
    """Promove NPC emergente a personagem permanente do Pack."""
    slot_row    = await _get_slot(db, save_id, user_id)
    world_state = WorldState(**slot_row["world_state"])
    pack_id     = slot_row.get("pack_id")

    if not pack_id:
        raise ValueError("Este save não tem um Pack vinculado")

    pack = (await db.table("roleplay_worlds").select("owner_id").eq("id", pack_id).execute()).first()
    if not pack or pack["owner_id"] != user_id:
        raise ValueError("Apenas o criador do Pack pode adicionar personagens permanentes")

    npc_data = world_state.emergent_npcs.get(npc_name)
    if not npc_data:
        raise ValueError(f"NPC '{npc_name}' não encontrado nos NPCs emergentes")

    # FIX: usa custom_image_url do NPC se image_url não for fornecida
    final_image_url = image_url or (npc_data.get("custom_image_url") if isinstance(npc_data, dict) else None)

    personality = {
        "description": npc_data.get("description", "") if isinstance(npc_data, dict) else "",
        "personality": npc_data.get("personality", "") if isinstance(npc_data, dict) else "",
    }
    traits = npc_data.get("traits", {}) if isinstance(npc_data, dict) else {}

    new_char = (await db.table("roleplay_characters").insert({
        "world_id":         pack_id,
        "name":             npc_name,
        "image_url":        final_image_url,
        "personality_json": personality,
        "base_traits_json": traits,
    }).execute()).as_list()

    # Marca como promovido no world_state
    state_dict = world_state.model_dump()
    state_dict["emergent_npcs"][npc_name]["promoted"] = True
    await db.table("save_slots").update({
        "world_state": state_dict,
    }).eq("id", save_id).execute()

    return {
        "message":   f"{npc_name} promovido a personagem permanente",
        "character": new_char[0] if new_char else {},
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
            "save_id":   save_id,
            "title":     arc_response.arc_title or "Novo Arco",
            "start_day": world_state.current_day,
            "status":    "active",
            "summary":   arc_response.arc_summary or "",
            "impact":    "",
        }).execute()
        await db.table("events_log").insert({
            "save_id": save_id,
            "type":    "arc_event",
            "content": f"[ARCO INICIADO] {arc_response.arc_title}",
        }).execute()

    elif signal == "close" and active_arc:
        await db.table("story_arcs").update({
            "status":    "closed",
            "end_day":   world_state.current_day,
            "summary":   arc_response.arc_summary or active_arc.get("summary", ""),
            "impact":    arc_response.arc_title or "",
        }).eq("id", active_arc["id"]).execute()
        await db.table("events_log").insert({
            "save_id": save_id,
            "type":    "arc_event",
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