"""
RPG Manhwa - FastAPI Backend
Includes: Auth, Slots, Game Actions, Roleplay Packs (Worlds/Characters/Backgrounds)
"""

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_settings
from database import get_db, SupabaseClient
from auth import get_current_user
from schemas import (
    CreateSlotRequest, UpdateSlotTitleRequest, WorldState,
    CreateWorldRequest, CreateCharacterRequest, CreateBackgroundRequest,
)
from game_service import process_action, get_slot_history, get_slot_arcs


app = FastAPI(title="RPG Manhwa API", version="0.2.0")

settings = get_settings()

ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1",
    "http://127.0.0.1:5500",
    # Adicione URL de producao aqui
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
async def get_profile(
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = await (
        db.table("profiles")
        .select("id, username, created_at")
        .eq("id", user["user_id"])
        .execute()
    )
    row = raw.first()
    if not row:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")
    return row


# ============================================================
# Roleplay Worlds (Packs)
# ============================================================

@app.get("/worlds", tags=["worlds"])
async def list_worlds(
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    """Lista worlds publicos + worlds do proprio usuario."""
    public_raw = await (
        db.table("roleplay_worlds")
        .select("id, owner_id, title, world_concept, tone, logo_url, is_public")
        .eq("is_public", "true")
        .execute()
    )
    own_raw = await (
        db.table("roleplay_worlds")
        .select("id, owner_id, title, world_concept, tone, logo_url, is_public")
        .eq("owner_id", user["user_id"])
        .execute()
    )
    # Merge deduplicando por id
    seen = set()
    result = []
    for row in public_raw.as_list() + own_raw.as_list():
        if row["id"] not in seen:
            seen.add(row["id"])
            result.append(row)
    return result


@app.get("/worlds/{world_id}", tags=["worlds"])
async def get_world(
    world_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = await (
        db.table("roleplay_worlds")
        .select("*")
        .eq("id", world_id)
        .execute()
    )
    world = raw.first()
    if not world:
        raise HTTPException(status_code=404, detail="World nao encontrado")
    if not world.get("is_public") and world.get("owner_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")

    chars = await (
        db.table("roleplay_characters")
        .select("*")
        .eq("world_id", world_id)
        .execute()
    )
    bgs = await (
        db.table("roleplay_backgrounds")
        .select("*")
        .eq("world_id", world_id)
        .execute()
    )
    return {
        **world,
        "characters": chars.as_list(),
        "backgrounds": bgs.as_list(),
    }


@app.post("/worlds", tags=["worlds"], status_code=status.HTTP_201_CREATED)
async def create_world(
    body: CreateWorldRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = await db.table("roleplay_worlds").insert({
        "owner_id": user["user_id"],
        "title": body.title,
        "world_concept": body.world_concept,
        "tone": body.tone,
        "rules_of_world": body.rules_of_world,
        "logo_url": body.logo_url,
        "is_public": body.is_public,
    }).execute()
    row = raw.as_list()
    if not row:
        raise HTTPException(status_code=500, detail="Erro ao criar world")
    return row[0]


@app.patch("/worlds/{world_id}", tags=["worlds"])
async def update_world(
    world_id: str,
    body: CreateWorldRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    existing = await (
        db.table("roleplay_worlds").select("id, owner_id").eq("id", world_id).execute()
    )
    w = existing.first()
    if not w:
        raise HTTPException(status_code=404, detail="World nao encontrado")
    if w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Somente o criador pode editar")

    await db.table("roleplay_worlds").update({
        "title": body.title,
        "world_concept": body.world_concept,
        "tone": body.tone,
        "rules_of_world": body.rules_of_world,
        "logo_url": body.logo_url,
        "is_public": body.is_public,
    }).eq("id", world_id).execute()
    return {"message": "World atualizado"}


@app.delete("/worlds/{world_id}", tags=["worlds"])
async def delete_world(
    world_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    existing = await (
        db.table("roleplay_worlds").select("id, owner_id").eq("id", world_id).execute()
    )
    w = existing.first()
    if not w:
        raise HTTPException(status_code=404, detail="World nao encontrado")
    if w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Somente o criador pode deletar")

    await db.table("roleplay_characters").delete().eq("world_id", world_id).execute()
    await db.table("roleplay_backgrounds").delete().eq("world_id", world_id).execute()
    await db.table("roleplay_worlds").delete().eq("id", world_id).execute()
    return {"message": "World deletado"}


# ============================================================
# Characters (dentro de um World)
# ============================================================

@app.post("/worlds/{world_id}/characters", tags=["worlds"], status_code=201)
async def add_character(
    world_id: str,
    body: CreateCharacterRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
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


@app.delete("/characters/{character_id}", tags=["worlds"])
async def delete_character(
    character_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    char = (await db.table("roleplay_characters").select("id, world_id").eq("id", character_id).execute()).first()
    if not char:
        raise HTTPException(status_code=404, detail="Personagem nao encontrado")
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", char["world_id"]).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    await db.table("roleplay_characters").delete().eq("id", character_id).execute()
    return {"message": "Personagem deletado"}


# ============================================================
# Backgrounds (dentro de um World)
# ============================================================

@app.post("/worlds/{world_id}/backgrounds", tags=["worlds"], status_code=201)
async def add_background(
    world_id: str,
    body: CreateBackgroundRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
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
async def delete_background(
    background_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    bg = (await db.table("roleplay_backgrounds").select("id, world_id").eq("id", background_id).execute()).first()
    if not bg:
        raise HTTPException(status_code=404, detail="Background nao encontrado")
    w = (await db.table("roleplay_worlds").select("owner_id").eq("id", bg["world_id"]).execute()).first()
    if not w or w["owner_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Acesso negado")
    await db.table("roleplay_backgrounds").delete().eq("id", background_id).execute()
    return {"message": "Background deletado"}


# ============================================================
# Save Slots
# ============================================================

@app.get("/slots", tags=["slots"])
async def list_slots(
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = await (
        db.table("save_slots")
        .select("id, slot_number, title, created_at, last_played, world_state, pack_id")
        .eq("user_id", user["user_id"])
        .order("slot_number")
        .execute()
    )
    rows = raw.as_list()
    slot_map = {r["slot_number"]: r for r in rows}
    result = []
    for i in range(1, 6):
        if i in slot_map:
            r = slot_map[i]
            ws = r.get("world_state", {})
            result.append({
                "slot_number": i,
                "occupied": True,
                "id": r["id"],
                "title": r["title"],
                "pack_id": r.get("pack_id"),
                "created_at": r["created_at"],
                "last_played": r["last_played"],
                "current_day": ws.get("current_day", 1),
                "current_phase": ws.get("current_phase", "morning"),
            })
        else:
            result.append({"slot_number": i, "occupied": False})
    return result


@app.post("/slots", tags=["slots"], status_code=status.HTTP_201_CREATED)
async def create_slot(
    body: CreateSlotRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    existing = await (
        db.table("save_slots")
        .select("id")
        .eq("user_id", user["user_id"])
        .eq("slot_number", body.slot_number)
        .execute()
    )
    if existing.first():
        raise HTTPException(status_code=400, detail=f"Slot {body.slot_number} ja esta ocupado.")

    # If pack_id provided, validate it exists and is accessible
    if body.pack_id:
        pack_raw = await (
            db.table("roleplay_worlds").select("id, is_public, owner_id")
            .eq("id", body.pack_id).execute()
        )
        pack = pack_raw.first()
        if not pack:
            raise HTTPException(status_code=404, detail="Roleplay Pack nao encontrado")
        if not pack.get("is_public") and pack.get("owner_id") != user["user_id"]:
            raise HTTPException(status_code=403, detail="Pack nao disponivel")

    default_ws = WorldState()
    insert_data = {
        "user_id": user["user_id"],
        "slot_number": body.slot_number,
        "title": body.title,
        "world_state": default_ws.model_dump(),
        "memory_summary": "",
        "timeline": [],
    }
    if body.pack_id:
        insert_data["pack_id"] = body.pack_id

    raw = await db.table("save_slots").insert(insert_data).execute()
    rows = raw.as_list()
    return {"message": "Slot criado", "slot_id": rows[0]["id"]}


@app.delete("/slots/{slot_number}", tags=["slots"])
async def delete_slot(
    slot_number: int,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    if not 1 <= slot_number <= 5:
        raise HTTPException(status_code=400, detail="Slot deve ser entre 1 e 5")

    # Get slot id first to delete events
    slot_raw = await (
        db.table("save_slots").select("id")
        .eq("user_id", user["user_id"]).eq("slot_number", slot_number).execute()
    )
    slot = slot_raw.first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot nao encontrado")

    slot_id = slot["id"]
    # Delete related data
    await db.table("events_log").delete().eq("save_id", slot_id).execute()
    await db.table("story_arcs").delete().eq("save_id", slot_id).execute()
    await db.table("save_slots").delete().eq("id", slot_id).execute()
    return {"message": f"Slot {slot_number} deletado"}


@app.patch("/slots/{slot_id}/title", tags=["slots"])
async def rename_slot(
    slot_id: str,
    body: UpdateSlotTitleRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = await (
        db.table("save_slots")
        .update({"title": body.title})
        .eq("id", slot_id)
        .eq("user_id", user["user_id"])
        .execute()
    )
    if not raw.as_list():
        raise HTTPException(status_code=404, detail="Slot nao encontrado")
    return {"message": "Titulo atualizado"}


@app.get("/slots/{slot_id}", tags=["slots"])
async def get_slot(
    slot_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = await (
        db.table("save_slots")
        .select("*")
        .eq("id", slot_id)
        .eq("user_id", user["user_id"])
        .execute()
    )
    row = raw.first()
    if not row:
        raise HTTPException(status_code=404, detail="Slot nao encontrado")
    return row


# ============================================================
# Game Actions
# ============================================================

class ActionRequest(BaseModel):
    input: str


@app.post("/slots/{slot_id}/action", tags=["game"])
async def player_action(
    slot_id: str,
    body: ActionRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    try:
        result = await process_action(
            db=db,
            save_id=slot_id,
            user_id=user["user_id"],
            raw_input=body.input,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")
    return result


@app.get("/slots/{slot_id}/history", tags=["game"])
async def slot_history(
    slot_id: str,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    try:
        return await get_slot_history(db, slot_id, user["user_id"], limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/slots/{slot_id}/arcs", tags=["game"])
async def slot_arcs(
    slot_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    try:
        return await get_slot_arcs(db, slot_id, user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Username / email check
# ============================================================

@app.get("/check-username", tags=["profile"])
async def check_username(
    username: str,
    db: SupabaseClient = Depends(get_db),
):
    raw = await (
        db.table("profiles")
        .select("id")
        .eq("username", username)
        .execute()
    )
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