"""
RPG Manhwa — FastAPI Backend v0.3
Endpoints: Auth, Slots, Game Actions, Roleplay Packs, NPCs Emergentes
"""

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from config import get_settings
from database import get_db, SupabaseClient
from auth import get_current_user
from schemas import (
    CreateSlotRequest, UpdateSlotTitleRequest, WorldState,
    CreateWorldRequest, CreateCharacterRequest, UpdateCharacterRequest,
    CreateBackgroundRequest, PromoteNPCRequest,
)
from game_service import (
    process_action, get_slot_history, get_slot_arcs,
    advance_phase, initialize_save, promote_npc,
)

app = FastAPI(title="RPG Manhwa API", version="0.3.0")

ALLOWED_ORIGINS = [
    "http://localhost", "http://localhost:3000",
    "http://localhost:5500", "http://127.0.0.1", "http://127.0.0.1:5500",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Health
# ============================================================

@app.get("/health", tags=["system"])
def health():
    s = get_settings()
    return {
        "status": "ok",
        "ai_enabled": s.ai_engine_enabled,
        "anthropic_configured": bool(s.anthropic_api_key),
    }


# ============================================================
# Profile
# ============================================================

@app.get("/profile", tags=["profile"])
async def get_profile(user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    row = (await db.table("profiles").select("id, username, created_at").eq("id", user["user_id"]).execute()).first()
    if not row:
        raise HTTPException(status_code=404, detail="Perfil não encontrado")
    return row


# ============================================================
# Roleplay Worlds (Packs)
# ============================================================

@app.get("/worlds", tags=["worlds"])
async def list_worlds(user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    public = (await db.table("roleplay_worlds").select("id, owner_id, title, world_concept, tone, logo_url, is_public").eq("is_public", "true").execute()).as_list()
    own = (await db.table("roleplay_worlds").select("id, owner_id, title, world_concept, tone, logo_url, is_public").eq("owner_id", user["user_id"]).execute()).as_list()
    seen, result = set(), []
    for row in public + own:
        if row["id"] not in seen:
            seen.add(row["id"])
            result.append(row)
    return result


@app.get("/worlds/{world_id}", tags=["worlds"])
async def get_world(world_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    world = (await db.table("roleplay_worlds").select("*").eq("id", world_id).execute()).first()
    if not world:
        raise HTTPException(status_code=404, detail="World não encontrado")
    if not world.get("is_public") and world.get("owner_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    chars = (await db.table("roleplay_characters").select("*").eq("world_id", world_id).execute()).as_list()
    bgs = (await db.table("roleplay_backgrounds").select("*").eq("world_id", world_id).execute()).as_list()
    return {**world, "characters": chars, "backgrounds": bgs}


@app.post("/worlds", tags=["worlds"], status_code=201)
async def create_world(body: CreateWorldRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    raw = await db.table("roleplay_worlds").insert({
        "owner_id": user["user_id"],
        "title": body.title,
        "world_concept": body.world_concept,
        "tone": body.tone,
        "rules_of_world": body.rules_of_world,
        "logo_url": body.logo_url,
        "is_public": body.is_public,
    }).execute()
    rows = raw.as_list()
    if not rows:
        raise HTTPException(status_code=500, detail="Erro ao criar world")
    return rows[0]


@app.patch("/worlds/{world_id}", tags=["worlds"])
async def update_world(world_id: str, body: CreateWorldRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", world_id).execute()).first()
    if not w:
        raise HTTPException(status_code=404, detail="World não encontrado")
    if w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Apenas o criador pode editar")
    await db.table("roleplay_worlds").update({
        "title": body.title, "world_concept": body.world_concept,
        "tone": body.tone, "rules_of_world": body.rules_of_world,
        "logo_url": body.logo_url, "is_public": body.is_public,
    }).eq("id", world_id).execute()
    return {"message": "World atualizado"}


@app.delete("/worlds/{world_id}", tags=["worlds"])
async def delete_world(world_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", world_id).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    await db.table("roleplay_characters").delete().eq("world_id", world_id).execute()
    await db.table("roleplay_backgrounds").delete().eq("world_id", world_id).execute()
    await db.table("roleplay_worlds").delete().eq("id", world_id).execute()
    return {"message": "World deletado"}


# ============================================================
# Characters
# ============================================================

@app.post("/worlds/{world_id}/characters", tags=["worlds"], status_code=201)
async def add_character(world_id: str, body: CreateCharacterRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", world_id).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    raw = await db.table("roleplay_characters").insert({
        "world_id": world_id,
        "name": body.name,
        "image_url": body.image_url,
        "personality_json": body.personality_json,
        "base_traits_json": body.base_traits_json,
    }).execute()
    return raw.as_list()[0]


@app.patch("/characters/{character_id}", tags=["worlds"])
async def update_character(character_id: str, body: UpdateCharacterRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    char = (await db.table("roleplay_characters").select("id, world_id").eq("id", character_id).execute()).first()
    if not char:
        raise HTTPException(status_code=404, detail="Personagem não encontrado")
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", char["world_id"]).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")
    await db.table("roleplay_characters").update(update_data).eq("id", character_id).execute()
    return {"message": "Personagem atualizado"}


@app.delete("/characters/{character_id}", tags=["worlds"])
async def delete_character(character_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    char = (await db.table("roleplay_characters").select("id, world_id").eq("id", character_id).execute()).first()
    if not char:
        raise HTTPException(status_code=404, detail="Personagem não encontrado")
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", char["world_id"]).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    await db.table("roleplay_characters").delete().eq("id", character_id).execute()
    return {"message": "Personagem deletado"}


# ============================================================
# Backgrounds
# ============================================================

@app.post("/worlds/{world_id}/backgrounds", tags=["worlds"], status_code=201)
async def add_background(world_id: str, body: CreateBackgroundRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", world_id).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    raw = await db.table("roleplay_backgrounds").insert({
        "world_id": world_id,
        "name": body.name,
        "image_url": body.image_url,
        "description": body.description,
    }).execute()
    return raw.as_list()[0]


@app.delete("/backgrounds/{background_id}", tags=["worlds"])
async def delete_background(background_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    bg = (await db.table("roleplay_backgrounds").select("id, world_id").eq("id", background_id).execute()).first()
    if not bg:
        raise HTTPException(status_code=404, detail="Background não encontrado")
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", bg["world_id"]).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    await db.table("roleplay_backgrounds").delete().eq("id", background_id).execute()
    return {"message": "Background deletado"}


# ============================================================
# Save Slots
# ============================================================

@app.get("/slots", tags=["slots"])
async def list_slots(user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    rows = (await db.table("save_slots")
            .select("id, slot_number, title, created_at, last_played, world_state, pack_id, player_name")
            .eq("user_id", user["user_id"]).order("slot_number").execute()).as_list()
    slot_map = {r["slot_number"]: r for r in rows}
    result = []
    for i in range(1, 6):
        if i in slot_map:
            r = slot_map[i]
            ws = r.get("world_state", {})
            result.append({
                "slot_number": i, "occupied": True,
                "id": r["id"], "title": r["title"],
                "pack_id": r.get("pack_id"),
                "player_name": r.get("player_name", "Protagonista"),
                "created_at": r["created_at"], "last_played": r["last_played"],
                "current_day": ws.get("current_day", 1),
                "current_phase": ws.get("current_phase", "morning"),
            })
        else:
            result.append({"slot_number": i, "occupied": False})
    return result


@app.post("/slots", tags=["slots"], status_code=201)
async def create_slot(body: CreateSlotRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    existing = (await db.table("save_slots").select("id")
                .eq("user_id", user["user_id"]).eq("slot_number", body.slot_number).execute()).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Slot {body.slot_number} já está ocupado.")

    if body.pack_id:
        pack = (await db.table("roleplay_worlds").select("id, is_public, owner_id").eq("id", body.pack_id).execute()).first()
        if not pack:
            raise HTTPException(status_code=404, detail="Roleplay Pack não encontrado")
        if not pack.get("is_public") and pack.get("owner_id") != user["user_id"]:
            raise HTTPException(status_code=403, detail="Pack não disponível")

    default_ws = WorldState()
    insert_data = {
        "user_id": user["user_id"],
        "slot_number": body.slot_number,
        "title": body.title,
        "player_name": body.player_name,
        "player_description": body.player_description,
        "world_state": default_ws.model_dump(),
        "memory_summary": "",
        "timeline": [],
    }
    if body.pack_id:
        insert_data["pack_id"] = body.pack_id

    raw = await db.table("save_slots").insert(insert_data).execute()
    rows = raw.as_list()
    slot_id = rows[0]["id"]

    # Gera narração de abertura
    try:
        opening = await initialize_save(db, slot_id, user["user_id"])
    except Exception:
        opening = {"narration": "A história começa aqui."}

    return {"message": "Slot criado", "slot_id": slot_id, "opening": opening.get("narration", "")}


@app.delete("/slots/{slot_number}", tags=["slots"])
async def delete_slot(slot_number: int, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    if not 1 <= slot_number <= 5:
        raise HTTPException(status_code=400, detail="Slot deve ser entre 1 e 5")
    slot = (await db.table("save_slots").select("id").eq("user_id", user["user_id"]).eq("slot_number", slot_number).execute()).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")
    sid = slot["id"]
    await db.table("events_log").delete().eq("save_id", sid).execute()
    await db.table("story_arcs").delete().eq("save_id", sid).execute()
    await db.table("save_slots").delete().eq("id", sid).execute()
    return {"message": f"Slot {slot_number} deletado"}


@app.patch("/slots/{slot_id}/title", tags=["slots"])
async def rename_slot(slot_id: str, body: UpdateSlotTitleRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    raw = await db.table("save_slots").update({"title": body.title}).eq("id", slot_id).eq("user_id", user["user_id"]).execute()
    if not raw.as_list():
        raise HTTPException(status_code=404, detail="Slot não encontrado")
    return {"message": "Título atualizado"}


@app.get("/slots/{slot_id}", tags=["slots"])
async def get_slot(slot_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    row = (await db.table("save_slots").select("*").eq("id", slot_id).eq("user_id", user["user_id"]).execute()).first()
    if not row:
        raise HTTPException(status_code=404, detail="Slot não encontrado")
    return row


# ============================================================
# Game Actions
# ============================================================

class ActionRequest(BaseModel):
    input: str


@app.post("/slots/{slot_id}/action", tags=["game"])
async def player_action(slot_id: str, body: ActionRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    try:
        return await process_action(db=db, save_id=slot_id, user_id=user["user_id"], raw_input=body.input)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")


@app.post("/slots/{slot_id}/advance", tags=["game"])
async def advance_time(slot_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    """Avança a fase do dia sem processar ação do jogador."""
    try:
        return await advance_phase(db=db, save_id=slot_id, user_id=user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/slots/{slot_id}/promote-npc", tags=["game"])
async def promote_npc_endpoint(slot_id: str, body: PromoteNPCRequest, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    """Promove NPC emergente a personagem permanente do Pack."""
    try:
        return await promote_npc(
            db=db, save_id=slot_id, user_id=user["user_id"],
            npc_name=body.npc_name, image_url=body.image_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/slots/{slot_id}/history", tags=["game"])
async def slot_history(slot_id: str, limit: int = 50, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    try:
        return await get_slot_history(db, slot_id, user["user_id"], limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/slots/{slot_id}/arcs", tags=["game"])
async def slot_arcs(slot_id: str, user: dict = Depends(get_current_user), db: SupabaseClient = Depends(get_db)):
    try:
        return await get_slot_arcs(db, slot_id, user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Username check
# ============================================================

@app.get("/check-username", tags=["profile"])
async def check_username(username: str, db: SupabaseClient = Depends(get_db)):
    raw = (await db.table("profiles").select("id").eq("username", username).execute())
    return {"taken": raw.first() is not None}


@app.get("/check-email", tags=["profile"])
async def check_email(email: str):
    return {"taken": False}


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run("main:app", host=s.app_host, port=s.app_port, reload=s.debug)