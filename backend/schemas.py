from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime
import re


# ============================================================
# World State
# ============================================================

class WorldState(BaseModel):
    # Stats do jogador
    sanity: int = Field(default=100, ge=0, le=100)
    confidence: int = Field(default=50, ge=0, le=100)
    violence: int = Field(default=0, ge=0, le=100)
    social_status: int = Field(default=0, ge=-100, le=100)
    meta_awareness: int = Field(default=0, ge=0, le=100)

    # Tempo
    current_day: int = Field(default=1, ge=1)
    current_phase: str = Field(default="morning")  # morning | afternoon | night

    # Contadores
    event_counter_global: int = Field(default=0, ge=0)
    event_counter_arc: int = Field(default=0, ge=0)

    # Relacionamentos com personagens do Pack (persistentes)
    relationships: dict[str, Any] = Field(default_factory=dict)

    # NPCs emergentes criados pela IA durante o save
    emergent_npcs: dict[str, Any] = Field(default_factory=dict)

    # Cena atual
    current_background: Optional[str] = Field(default=None)
    active_characters: list[str] = Field(default_factory=list)
    scene_type: str = Field(default="narrative")

    # FIX: registra a última passagem de tempo para a IA perceber
    # Formato: "Dia 2 manhã → Dia 2 tarde" ou None se não houve skip recente
    last_time_skip: Optional[str] = Field(default=None)

    def next_phase(self) -> "WorldState":
        phases = ["morning", "afternoon", "night"]
        idx = phases.index(self.current_phase)
        if idx == len(phases) - 1:
            new_day   = self.current_day + 1
            new_phase = "morning"
            skip_desc = f"Dia {self.current_day} noite → Dia {new_day} manhã"
        else:
            new_day   = self.current_day
            new_phase = phases[idx + 1]
            phase_labels = {"morning": "manhã", "afternoon": "tarde", "night": "noite"}
            skip_desc = f"Dia {self.current_day} {phase_labels[self.current_phase]} → {phase_labels[new_phase]}"

        return self.model_copy(update={
            "current_phase":     new_phase,
            "current_day":       new_day,
            "active_characters": [],
            "scene_type":        "narrative",
            "last_time_skip":    skip_desc,  # FIX: registra o skip
        })

    def clear_time_skip(self) -> "WorldState":
        """Limpa o flag de time skip após a IA processá-lo."""
        return self.model_copy(update={"last_time_skip": None})


# ============================================================
# Save Slots
# ============================================================

class CreateSlotRequest(BaseModel):
    slot_number: int = Field(..., ge=1, le=5)
    title: str = Field(default="Nova Historia", max_length=80)
    pack_id: Optional[str] = Field(default=None)
    player_name: str = Field(default="Jogador", max_length=60)
    player_description: str = Field(default="", max_length=500)


class UpdateSlotTitleRequest(BaseModel):
    title: str = Field(..., max_length=80)


# ============================================================
# Player Action
# ============================================================

DIALOGUE_PATTERN = re.compile(r'"([^"]+)"')
ACTION_PATTERN   = re.compile(r'\*([^*]+)\*')


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
            "raw":       self.raw_input,
            "dialogues": self.dialogues,
            "actions":   self.actions,
        }


# ============================================================
# Roleplay Pack
# ============================================================

class CreateWorldRequest(BaseModel):
    title: str = Field(..., max_length=100)
    world_concept: str = Field(..., min_length=20, max_length=2000)
    tone: str = Field(default="dramatico", max_length=400)
    rules_of_world: str = Field(default="", max_length=1000)
    logo_url: Optional[str] = Field(default=None)
    is_public: bool = Field(default=False)


class CreateCharacterRequest(BaseModel):
    world_id: str
    name: str = Field(..., max_length=80)
    image_url: Optional[str] = Field(default=None)
    personality_json: dict = Field(default_factory=dict)
    base_traits_json: dict = Field(default_factory=dict)


class UpdateCharacterRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    image_url: Optional[str] = Field(default=None)
    personality_json: Optional[dict] = Field(default=None)
    base_traits_json: Optional[dict] = Field(default=None)


class CreateBackgroundRequest(BaseModel):
    world_id: str
    name: str = Field(..., max_length=80)
    image_url: Optional[str] = Field(default=None)
    description: str = Field(default="", max_length=500)


class PromoteNPCRequest(BaseModel):
    npc_name: str
    image_url: Optional[str] = Field(default=None)


# FIX: request para salvar imagem customizada de NPC emergente sem promovê-lo
class NpcImageRequest(BaseModel):
    npc_name: str
    image_url: Optional[str] = Field(default=None)


# ============================================================
# AI Response
# ============================================================

class AIResponse(BaseModel):
    narration: str
    # FIX: diálogo direto do personagem separado da narração
    character_dialogue: Optional[str] = None
    world_state_deltas: dict[str, Any] = Field(default_factory=dict)
    arc_signal: Optional[str] = None
    arc_title: Optional[str] = None
    arc_summary: Optional[str] = None
    memory_update: Optional[str] = None
    relationship_updates: dict[str, Any] = Field(default_factory=dict)
    background_hint: Optional[str] = None
    active_characters: list[str] = Field(default_factory=list)
    scene_type: str = "narrative"
    emergent_npcs: dict[str, Any] = Field(default_factory=dict)