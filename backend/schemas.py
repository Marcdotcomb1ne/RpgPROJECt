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

    def next_phase(self) -> "WorldState":
        """Advances the time phase. Returns updated state."""
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
        """Input must contain at least one dialogue or action marker."""
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
# AI Response (placeholder structure)
# ============================================================

class AIResponse(BaseModel):
    narration: str
    world_state_deltas: dict[str, Any] = Field(default_factory=dict)
    arc_signal: Optional[str] = None  # "start", "evolve", "close", or None
    arc_title: Optional[str] = None
    arc_summary: Optional[str] = None
    memory_update: Optional[str] = None
