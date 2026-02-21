"""
RPG Manhwa - FastAPI Backend
"""

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_settings
from database import get_db, SupabaseClient
from auth import get_current_user
from schemas import CreateSlotRequest, UpdateSlotTitleRequest, WorldState
from game_service import process_action, get_slot_history, get_slot_arcs


app = FastAPI(title="RPG Manhwa API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return {"status": "ok", "ai_enabled": s.ai_engine_enabled}


# ============================================================
# Profile
# ============================================================

@app.get("/profile", tags=["profile"])
def get_profile(
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = (
        db.table("profiles")
        .select("id, username, created_at")
        .eq("id", user["user_id"])
        .execute()
    ).data
    rows = raw if isinstance(raw, list) else ([raw] if raw else [])
    if not rows:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")
    return rows[0]


# ============================================================
# Save Slots
# ============================================================

@app.get("/slots", tags=["slots"])
def list_slots(
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = (
        db.table("save_slots")
        .select("id, slot_number, title, created_at, last_played, world_state")
        .eq("user_id", user["user_id"])
        .order("slot_number")
        .execute()
    ).data or []
    rows = raw if isinstance(raw, list) else [raw]

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
                "created_at": r["created_at"],
                "last_played": r["last_played"],
                "current_day": ws.get("current_day", 1),
                "current_phase": ws.get("current_phase", "morning"),
            })
        else:
            result.append({"slot_number": i, "occupied": False})
    return result


@app.post("/slots", tags=["slots"], status_code=status.HTTP_201_CREATED)
def create_slot(
    body: CreateSlotRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    existing = (
        db.table("save_slots")
        .select("id")
        .eq("user_id", user["user_id"])
        .eq("slot_number", body.slot_number)
        .execute()
    ).data
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Slot {body.slot_number} ja esta ocupado.",
        )

    default_ws = WorldState()
    raw = (
        db.table("save_slots").insert({
            "user_id": user["user_id"],
            "slot_number": body.slot_number,
            "title": body.title,
            "world_state": default_ws.model_dump(),
            "memory_summary": "",
            "timeline": [],
        }).execute()
    ).data
    rows = raw if isinstance(raw, list) else [raw]
    return {"message": "Slot criado", "slot_id": rows[0]["id"]}


@app.delete("/slots/{slot_number}", tags=["slots"])
def delete_slot(
    slot_number: int,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    if not 1 <= slot_number <= 5:
        raise HTTPException(status_code=400, detail="Slot deve ser entre 1 e 5")

    raw = (
        db.table("save_slots")
        .delete()
        .eq("user_id", user["user_id"])
        .eq("slot_number", slot_number)
        .execute()
    ).data
    if not raw:
        raise HTTPException(status_code=404, detail="Slot nao encontrado")
    return {"message": f"Slot {slot_number} deletado"}


@app.patch("/slots/{slot_id}/title", tags=["slots"])
def rename_slot(
    slot_id: str,
    body: UpdateSlotTitleRequest,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = (
        db.table("save_slots")
        .update({"title": body.title})
        .eq("id", slot_id)
        .eq("user_id", user["user_id"])
        .execute()
    ).data
    if not raw:
        raise HTTPException(status_code=404, detail="Slot nao encontrado")
    return {"message": "Titulo atualizado"}


@app.get("/slots/{slot_id}", tags=["slots"])
def get_slot(
    slot_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    raw = (
        db.table("save_slots")
        .select("*")
        .eq("id", slot_id)
        .eq("user_id", user["user_id"])
        .execute()
    ).data
    rows = raw if isinstance(raw, list) else ([raw] if raw else [])
    if not rows:
        raise HTTPException(status_code=404, detail="Slot nao encontrado")
    return rows[0]


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
def slot_history(
    slot_id: str,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    try:
        return get_slot_history(db, slot_id, user["user_id"], limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/slots/{slot_id}/arcs", tags=["game"])
def slot_arcs(
    slot_id: str,
    user: dict = Depends(get_current_user),
    db: SupabaseClient = Depends(get_db),
):
    try:
        return get_slot_arcs(db, slot_id, user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run("main:app", host=s.app_host, port=s.app_port, reload=s.debug)
