"""
AI Engine — Real Implementation using Claude via Anthropic API
--------------------------------------------------------------
Uses claude-sonnet-4-6 for narration, arc analysis and summarization.
Set ANTHROPIC_API_KEY in your .env file.
"""

import httpx
import json
import re
from config import get_settings
from schemas import AIResponse, WorldState, PlayerAction


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"


async def _call_claude(system_prompt: str, user_content: str, max_tokens: int = 1024) -> str:
    settings = get_settings()
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(ANTHROPIC_API_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


def _build_narrator_system(world_state: WorldState, pack: dict | None) -> str:
    # Pack context
    world_concept = ""
    tone = "dramatico e tenso"
    rules = ""
    if pack:
        world_concept = pack.get("world_concept", pack.get("description", ""))
        tone = pack.get("tone", tone)
        rules = pack.get("rules_of_world", "")

    # Build relationship context
    rel_lines = []
    for name, data in (world_state.relationships or {}).items():
        if isinstance(data, dict):
            rel_lines.append(f"- {name}: afinidade={data.get('affinity', 0)}, status={data.get('status', 'desconhecido')}")
        else:
            rel_lines.append(f"- {name}: {data}")
    rel_text = "\n".join(rel_lines) if rel_lines else "Nenhum relacionamento registrado ainda."

    return f"""Você é o narrador de um RPG estilo Visual Novel / Manhwa.

=== UNIVERSO ===
{world_concept or "Escola urbana brasileira, gangues, status social, identidade."}

=== TOM NARRATIVO ===
{tone}

=== REGRAS DO MUNDO ===
{rules or "Mundo realista. Sem poderes sobrenaturais. Consequências reais para cada ação."}

=== ESTADO ATUAL DO PROTAGONISTA ===
- Sanidade: {world_state.sanity}/100
- Confiança: {world_state.confidence}/100
- Violência: {world_state.violence}/100
- Status Social: {world_state.social_status} (-100 a 100)
- Meta-consciência: {world_state.meta_awareness}/100
- Dia atual: {world_state.current_day}
- Fase: {world_state.current_phase}

=== RELACIONAMENTOS ===
{rel_text}

=== INSTRUÇÕES ===
Você DEVE responder SEMPRE em JSON válido com a seguinte estrutura:
{{
  "narration": "Texto narrativo rico e imersivo em português, 2-4 parágrafos. Inclui diálogos de NPCs, descrições de ambiente e consequências das ações.",
  "world_state_deltas": {{
    "sanity": <número 0-100 ou omitir se não mudar>,
    "confidence": <número 0-100 ou omitir>,
    "violence": <número 0-100 ou omitir>,
    "social_status": <número -100 a 100 ou omitir>,
    "meta_awareness": <número 0-100 ou omitir>
  }},
  "relationship_updates": {{
    "<nome_personagem>": {{"affinity": <-100 a 100>, "status": "<amigo|rival|neutro|aliado|inimigo>"}}
  }},
  "background_hint": "<nome_do_cenario_atual ou null>",
  "active_characters": ["<nome1>", "<nome2>"]
}}

Seja criativo, dramático e coerente com o tom. Os deltas devem refletir as consequências reais das ações do jogador.
Nunca quebre o personagem. Nunca mencione que você é uma IA."""


def _build_arc_system() -> str:
    return """Você é um analista narrativo de RPG.
Analise os eventos recentes e o estado do mundo e decida se um arco narrativo deve ser iniciado, continuado ou encerrado.

Responda SEMPRE em JSON:
{
  "arc_signal": "<start|close|none>",
  "arc_title": "<título dramático do arco ou null>",
  "arc_summary": "<resumo do que aconteceu neste arco ou null>"
}

Critérios:
- "start": quando há uma tensão clara se desenvolvendo (conflito novo, decisão importante, ameaça emergindo)
- "close": quando uma situação chegou a uma resolução (confronto resolvido, aliança formada, objetivo alcançado/falhado)
- "none": quando a narrativa ainda está no meio de um arco ou em momento de transição calma"""


def _build_summarizer_system() -> str:
    return """Você é um resumidor de memória narrativa para um RPG.
Dado o resumo atual e os eventos recentes, produza um novo resumo compacto que capture os pontos mais importantes da história.

Responda APENAS com o texto do resumo (sem JSON, sem formatação extra).
Máximo 300 palavras. Escreva em português. Seja específico sobre personagens, eventos-chave e o estado emocional do protagonista."""


def _extract_json(text: str) -> dict:
    """Extract JSON from Claude's response, handling markdown code blocks."""
    # Try direct parse
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding raw JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


async def call_narrator(
    action: PlayerAction,
    world_state: WorldState,
    memory_summary: str,
    recent_events: list[dict],
    pack: dict | None = None,
    characters: list[dict] | None = None,
    backgrounds: list[dict] | None = None,
) -> AIResponse:
    settings = get_settings()

    # Build character context
    char_context = ""
    if characters:
        lines = []
        for c in characters:
            p = c.get("personality_json") or {}
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    p = {}
            traits = c.get("base_traits_json") or {}
            if isinstance(traits, str):
                try:
                    traits = json.loads(traits)
                except Exception:
                    traits = {}
            lines.append(f"- {c['name']}: {p.get('description', '')} | traços: {traits}")
        char_context = "\nPersonagens disponíveis:\n" + "\n".join(lines)

    bg_context = ""
    if backgrounds:
        bg_context = "\nCenários disponíveis: " + ", ".join(b["name"] for b in backgrounds)

    system = _build_narrator_system(world_state, pack)

    # Build recent history context
    history = ""
    if recent_events:
        lines = []
        for ev in recent_events[-15:]:
            t = ev.get("type", "")
            c = ev.get("content", "")
            if t == "player_action":
                lines.append(f"[JOGADOR]: {c}")
            elif t == "narration":
                lines.append(f"[NARRADOR]: {c[:200]}...")
            elif t == "arc_event":
                lines.append(f"[ARCO]: {c}")
        history = "\n".join(lines)

    user_content = f"""=== MEMÓRIA ===
{memory_summary or "Início da história."}

=== HISTÓRICO RECENTE ===
{history or "Nenhum evento anterior."}
{char_context}
{bg_context}

=== AÇÃO DO JOGADOR ===
Diálogos: {action.dialogues}
Ações: {action.actions}
Input completo: {action.raw_input}

Narre o que acontece a seguir."""

    if not settings.ai_engine_enabled:
        # Placeholder quando IA não está habilitada
        phase_flavor = {
            "morning": "A escola ainda está vazia. O sol entra pelas janelas.",
            "afternoon": "O corredor ferve de barulho.",
            "night": "As luzes piscam. Você está quase sozinho.",
        }
        return AIResponse(
            narration=(
                f"[MODO PLACEHOLDER — configure ANTHROPIC_API_KEY e AI_ENGINE_ENABLED=true]\n\n"
                f"{phase_flavor.get(world_state.current_phase, '')}\n\n"
                f"Você disse: {action.dialogues}\nVocê fez: {action.actions}\n\n"
                f"Dia {world_state.current_day} — {world_state.current_phase.upper()}"
            ),
            world_state_deltas={},
        )

    raw = await _call_claude(system, user_content, max_tokens=1200)
    data = _extract_json(raw)

    # Apply relationship updates back into world_state relationships
    rel_updates = data.get("relationship_updates", {})
    new_relationships = dict(world_state.relationships or {})
    for name, info in rel_updates.items():
        new_relationships[name] = info

    return AIResponse(
        narration=data.get("narration", raw),
        world_state_deltas=data.get("world_state_deltas", {}),
        relationship_updates=rel_updates,
        background_hint=data.get("background_hint"),
        active_characters=data.get("active_characters", []),
    )


async def call_arc_analyst(
    world_state: WorldState,
    recent_events: list[dict],
    active_arc: dict | None,
) -> AIResponse:
    settings = get_settings()
    if not settings.ai_engine_enabled:
        return AIResponse(narration="", arc_signal=None)

    lines = []
    for ev in recent_events[-20:]:
        lines.append(f"[{ev.get('type','')}] {ev.get('content','')[:150]}")

    user_content = f"""Estado atual: Dia {world_state.current_day}, fase {world_state.current_phase}
Confiança: {world_state.confidence}, Violência: {world_state.violence}, Status: {world_state.social_status}
Arco ativo: {active_arc['title'] if active_arc else 'Nenhum'}
Contador de eventos neste arco: {world_state.event_counter_arc}

Eventos recentes:
{chr(10).join(lines)}"""

    raw = await _call_claude(_build_arc_system(), user_content, max_tokens=300)
    data = _extract_json(raw)

    return AIResponse(
        narration="",
        arc_signal=data.get("arc_signal"),
        arc_title=data.get("arc_title"),
        arc_summary=data.get("arc_summary"),
    )


async def call_summarizer(
    current_summary: str,
    recent_events: list[dict],
) -> str:
    settings = get_settings()
    if not settings.ai_engine_enabled:
        return current_summary

    lines = []
    for ev in recent_events:
        lines.append(f"[{ev.get('type','')}] {ev.get('content','')[:200]}")

    user_content = f"""Resumo atual:
{current_summary or "Nenhum resumo ainda."}

Eventos recentes para incorporar:
{chr(10).join(lines)}

Produza um novo resumo consolidado."""

    return await _call_claude(_build_summarizer_system(), user_content, max_tokens=400)


def calculate_fight_probability(world_state: WorldState) -> float:
    score = (
        world_state.violence * 0.4
        + world_state.confidence * 0.4
        + world_state.social_status * 0.2
    )
    normalized = (score + 20) / 120
    return max(0.05, min(0.95, normalized))