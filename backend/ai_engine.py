"""
AI Engine Interface
-------------------
This module is the single integration point for the LLM backend.
Replace the placeholder implementations with real calls to your
FastAPI AI service (Ollama / llama.cpp) when the model is ready.

Expected external service contract:
    POST /narrate         -> narration + world_state_deltas
    POST /analyze_arc     -> arc_signal + arc metadata
    POST /summarize       -> updated memory_summary
"""

import httpx
import random
from config import get_settings
from schemas import AIResponse, WorldState, PlayerAction


async def call_narrator(
    action: PlayerAction,
    world_state: WorldState,
    memory_summary: str,
    recent_events: list[dict],
) -> AIResponse:
    """
    Main narrator call. Generates scene narration and NPC responses.
    Currently returns a placeholder response.
    """
    settings = get_settings()

    if settings.ai_engine_enabled:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.ai_engine_url}/narrate",
                json={
                    "action": action.to_structured(),
                    "world_state": world_state.model_dump(),
                    "memory_summary": memory_summary,
                    "recent_events": recent_events[-20:],
                },
            )
            resp.raise_for_status()
            return AIResponse(**resp.json())

    # --------------------------------------------------------
    # PLACEHOLDER - remove when AI engine is connected
    # --------------------------------------------------------
    phase_flavor = {
        "morning": "A escola ainda esta vazia. O sol entra pelas janelas sujas.",
        "afternoon": "O corredor ferve de barulho. Grupos se formam pelos cantos.",
        "night": "As luzes do corredor piscam. Voce esta praticamente sozinho.",
    }

    return AIResponse(
        narration=(
            f"[PLACEHOLDER - IA NAO CONECTADA]\n\n"
            f"{phase_flavor.get(world_state.current_phase, '')}\n\n"
            f"Voce disse: {action.dialogues}\n"
            f"Voce fez: {action.actions}\n\n"
            f"Dia {world_state.current_day} - {world_state.current_phase.upper()}"
        ),
        world_state_deltas={},
    )


async def call_arc_analyst(
    world_state: WorldState,
    recent_events: list[dict],
    active_arc: dict | None,
) -> AIResponse:
    """
    Checks whether a narrative arc should start, evolve, or close.
    """
    settings = get_settings()

    if settings.ai_engine_enabled:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.ai_engine_url}/analyze_arc",
                json={
                    "world_state": world_state.model_dump(),
                    "recent_events": recent_events[-30:],
                    "active_arc": active_arc,
                },
            )
            resp.raise_for_status()
            return AIResponse(**resp.json())

    # PLACEHOLDER
    return AIResponse(narration="", arc_signal=None)


async def call_summarizer(
    current_summary: str,
    recent_events: list[dict],
) -> str:
    """
    Periodically compresses event history into memory_summary.
    Called every 10 events to keep context manageable.
    """
    settings = get_settings()

    if settings.ai_engine_enabled:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.ai_engine_url}/summarize",
                json={
                    "current_summary": current_summary,
                    "recent_events": recent_events,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("summary", current_summary)

    # PLACEHOLDER
    return current_summary


def calculate_fight_probability(world_state: WorldState) -> float:
    """
    Returns a 0.0-1.0 probability of success in a fight.
    Sent to the AI so it can generate a coherent narrative outcome.
    Formula can be tuned freely.
    """
    score = (
        world_state.violence * 0.4
        + world_state.confidence * 0.4
        + world_state.social_status * 0.2
    )
    # Normalize to 0-1 range. social_status goes -100 to 100.
    normalized = (score + 20) / 120
    return max(0.05, min(0.95, normalized))
