from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime
import re


# ============================================================
# World State
# ============================================================

class WorldState(BaseModel):
    sanity: int = Field(default=100, ge=0, le=100)
    confidence: int = Field(default=50, ge=0, le=100)
    violence: int = Field(default=0, ge=0, le=100)
    social_status: int = Field(default=0, ge=-100, le=100)
    meta_awareness: int = Field(default=0, ge=0, le=100)
    current_day: int = Field(default=1, ge=1)
    current_phase: str = Field(default="morning")
    event_counter_global: int = Field(default=0, ge=0)
    event_counter_arc: int = Field(default=0, ge=0)
    relationships: dict[str, Any] = Field(default_factory=dict)
    current_background: Optional[str] = Field(default=None)
    active_characters: list[str] = Field(default_factory=list)

    def next_phase(self) -> "WorldState":
        phases = ["morning", "afternoon", "night"]
        idx = phases.index(self.current_phase)
        if idx == len(phases) - 1:
            return self.model_copy(update={
                "current_phase": "morning",
                "current_day": self.current_day + 1,
            })
        return self.model_copy(update={"current_phase": phases[idx + 1]})


# ============================================================
# Save Slots
# ============================================================

class SaveSlotSummary(BaseModel):
    id: str
    slot_number: int
    title: str
    created_at: datetime
    last_played: datetime
    current_day: int
    current_phase: str


class SaveSlotDetail(SaveSlotSummary):
    world_state: WorldState
    memory_summary: str
    timeline: list[Any]


class CreateSlotRequest(BaseModel):
    slot_number: int = Field(..., ge=1, le=5)
    title: str = Field(default="Nova Historia", max_length=80)
    pack_id: Optional[str] = Field(default=None)


class UpdateSlotTitleRequest(BaseModel):
    title: str = Field(..., max_length=80)


# ============================================================
# Player Action
# ============================================================

DIALOGUE_PATTERN = re.compile(r'"([^"]+)"')
ACTION_PATTERN = re.compile(r'\*([^*]+)\*')


class PlayerAction(BaseModel):
    raw_input: str = Field(..., min_length=1, max_length=1000)

    @property
    def dialogues(self) -> list[str]:
        return DIALOGUE_PATTERN.findall(self.raw_input)

    @property
    def actions(self) -> list[str]:
        return ACTION_PATTERN.findall(self.raw_input)

    def is_valid_format(self) -> bool:
        return bool(self.dialogues or self.actions)

    def to_structured(self) -> dict:
        return {
            "raw": self.raw_input,
            "dialogues": self.dialogues,
            "actions": self.actions,
        }


# ============================================================
# Events
# ============================================================

class EventEntry(BaseModel):
    id: str
    type: str
    content: str
    created_at: datetime


# ============================================================
# Story Arcs
# ============================================================

class StoryArc(BaseModel):
    id: str
    title: str
    start_day: int
    end_day: Optional[int]
    status: str
    summary: str
    impact: str


# ============================================================
# Roleplay Pack / World
# ============================================================

class CreateWorldRequest(BaseModel):
    title: str = Field(..., max_length=100)
    world_concept: str = Field(..., min_length=20, max_length=2000)
    tone: str = Field(default="dramatico", max_length=100)
    rules_of_world: str = Field(default="", max_length=1000)
    logo_url: Optional[str] = Field(default=None)
    is_public: bool = Field(default=False)


class CreateCharacterRequest(BaseModel):
    world_id: str
    name: str = Field(..., max_length=80)
    image_url: Optional[str] = Field(default=None)
    personality_json: dict = Field(default_factory=dict)
    base_traits_json: dict = Field(default_factory=dict)


class CreateBackgroundRequest(BaseModel):
    world_id: str
    name: str = Field(..., max_length=80)
    image_url: Optional[str] = Field(default=None)
    description: str = Field(default="", max_length=500)


# ============================================================
# AI Response
# ============================================================

class AIResponse(BaseModel):
    narration: str
    world_state_deltas: dict[str, Any] = Field(default_factory=dict)
    arc_signal: Optional[str] = None        # "start", "close", or None
    arc_title: Optional[str] = None
    arc_summary: Optional[str] = None
    memory_update: Optional[str] = None
    relationship_updates: dict[str, Any] = Field(default_factory=dict)
    background_hint: Optional[str] = None
    active_characters: list[str] = Field(default_factory=list)