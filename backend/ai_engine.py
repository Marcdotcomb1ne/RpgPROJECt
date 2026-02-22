"""
AI Engine — Claude API (Anthropic)
-----------------------------------
Narrador principal, analista de arcos, sumarizador.
Suporta: dois tipos de cena, NPCs emergentes, personagem do jogador.
"""

import httpx
import json
import re
from config import get_settings
from schemas import AIResponse, WorldState, PlayerAction

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"


async def _call_claude(system_prompt: str, user_content: str, max_tokens: int = 1200) -> str:
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
        return resp.json()["content"][0]["text"]


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _build_narrator_system(
    world_state: WorldState,
    pack: dict | None,
    player_info: dict | None,
    characters: list[dict],
    backgrounds: list[dict],
) -> str:
    world_concept = ""
    tone = "dramatico e tenso"
    rules = ""
    if pack:
        world_concept = pack.get("world_concept", pack.get("description", ""))
        tone = pack.get("tone", tone)
        rules = pack.get("rules_of_world", "")

    # Personagem do jogador
    player_name = "Protagonista"
    player_desc = ""
    if player_info:
        player_name = player_info.get("name", "Protagonista")
        player_desc = player_info.get("description", "")

    # Relacionamentos com personagens do Pack
    rel_lines = []
    for name, data in (world_state.relationships or {}).items():
        if isinstance(data, dict):
            aff = data.get("affinity", 0)
            status = data.get("status", "neutro")
            rel_lines.append(f"  - {name}: {status} (afinidade {aff:+d})")
        else:
            rel_lines.append(f"  - {name}: {data}")
    rel_text = "\n".join(rel_lines) if rel_lines else "  Nenhum relacionamento ainda."

    # NPCs emergentes existentes
    npc_lines = []
    for name, data in (world_state.emergent_npcs or {}).items():
        if isinstance(data, dict):
            desc = data.get("description", "")
            status = data.get("status", "neutro")
            aff = data.get("affinity", 0)
            npc_lines.append(f"  - {name}: {desc} | {status} (afinidade {aff:+d})")
    npc_text = "\n".join(npc_lines) if npc_lines else "  Nenhum NPC emergente ainda."

    # Personagens do Pack disponíveis
    char_lines = []
    for c in characters:
        p = c.get("personality_json") or {}
        if isinstance(p, str):
            try: p = json.loads(p)
            except: p = {}
        desc = p.get("description", p.get("descricao", ""))
        char_lines.append(f"  - {c['name']}: {desc}")
    char_text = "\n".join(char_lines) if char_lines else "  Nenhum personagem definido no Pack."

    # Backgrounds disponíveis
    bg_names = [b["name"] for b in backgrounds] if backgrounds else []
    bg_text = ", ".join(bg_names) if bg_names else "Nenhum background definido"

    return f"""Você é o narrador de um RPG estilo Visual Novel / Manhwa. Você controla 100% da narrativa.

=== UNIVERSO ===
{world_concept or "Escola pública brasileira. Gangues, status social, identidade, violência cotidiana."}

=== TOM ===
{tone}

=== REGRAS ===
{rules or "Mundo realista. Sem poderes. Consequências reais e permanentes para cada ação."}

=== PERSONAGEM DO JOGADOR ===
Nome: {player_name}
{("Descrição: " + player_desc) if player_desc else "O protagonista é o ponto de vista do jogador — não aparece como imagem na tela."}
Stats atuais: Sanidade {world_state.sanity} | Confiança {world_state.confidence} | Violência {world_state.violence} | Status Social {world_state.social_status} | Meta-consciência {world_state.meta_awareness}
Dia {world_state.current_day} — {world_state.current_phase}

=== PERSONAGENS DO PACK (persistentes, têm imagem) ===
{char_text}

=== RELACIONAMENTOS ATUAIS ===
{rel_text}

=== NPCs EMERGENTES (criados durante o save) ===
{npc_text}

=== BACKGROUNDS DISPONÍVEIS ===
{bg_text}

=== TIPOS DE CENA ===
Você decide o tipo de cena com base no contexto:
- "narrative": apenas texto + background. Use para: ambiente, introspecção, transições, momentos sem personagem focado.
- "character_focus": um personagem aparece na tela + texto. Use para: confrontos diretos, conversas importantes, momentos emocionais.

Nunca force personagem em cena quando não fizer sentido narrativo.

=== NPCs EMERGENTES ===
Você pode criar personagens secundários/figurantes/antagonistas espontâneos. Eles NÃO têm imagem (use null).
Se um NPC emergente aparecer, inclua-o em "emergent_npcs" com nome, descrição, personalidade e atributos iniciais.
Personagens do Pack têm precedência sobre emergentes quando fazem sentido na cena.

=== FORMATO DE RESPOSTA ===
Responda SEMPRE em JSON válido:
{{
  "narration": "Texto narrativo rico em português. 2-4 parágrafos. Inclui diálogos de NPCs, descrições sensoriais, consequências. Seja dramático e coerente com o tom.",
  "scene_type": "narrative|character_focus",
  "active_characters": ["Nome do personagem em cena (apenas 1, só se scene_type=character_focus, deve ser nome exato do Pack ou NPC emergente)"],
  "background_hint": "nome exato do background ou null",
  "world_state_deltas": {{
    "sanity": <0-100, omitir se não mudar>,
    "confidence": <0-100, omitir se não mudar>,
    "violence": <0-100, omitir se não mudar>,
    "social_status": <-100 a 100, omitir se não mudar>,
    "meta_awareness": <0-100, omitir se não mudar>
  }},
  "relationship_updates": {{
    "<nome_exato_do_personagem_do_pack>": {{"affinity": <-100 a 100>, "status": "amigo|rival|neutro|aliado|inimigo"}}
  }},
  "emergent_npcs": {{
    "<nome_do_npc>": {{
      "description": "descrição física e contextual",
      "personality": "personalidade em 1-2 frases",
      "status": "neutro|rival|aliado|antagonista",
      "affinity": <-100 a 100>,
      "traits": {{"agressividade": 0-100, "lealdade": 0-100}}
    }}
  }},
  "arc_signal": "start|close|none"
}}

Nunca quebre o personagem. Nunca mencione que você é IA. Nunca repita a mesma cena duas vezes."""


def _build_opening_system(pack: dict | None, player_info: dict | None) -> str:
    world_concept = ""
    tone = "dramatico"
    rules = ""
    if pack:
        world_concept = pack.get("world_concept", "")
        tone = pack.get("tone", tone)
        rules = pack.get("rules_of_world", "")

    player_name = player_info.get("name", "Protagonista") if player_info else "Protagonista"
    player_desc = player_info.get("description", "") if player_info else ""

    return f"""Você é o narrador de um RPG Visual Novel estilo manhwa. 
Escreva a cena de abertura da história — o momento em que o jogador entra no universo pela primeira vez.

Universo: {world_concept or "Escola pública brasileira, gangues, status social."}
Tom: {tone}
Regras: {rules or "Mundo realista, sem poderes."}
Personagem do jogador: {player_name}{"— " + player_desc if player_desc else ""}

A cena de abertura deve:
- Estabelecer o ambiente e o tom imediatamente
- Apresentar um detalhe provocativo que gera curiosidade
- Ter 2-3 parágrafos
- Terminar com o jogador em uma situação que pede uma ação

Responda APENAS com o texto narrativo (sem JSON, sem formatação extra). Em português."""


def _build_arc_system() -> str:
    return """Você é um analista narrativo de RPG.
Analise os eventos e decida se um arco narrativo deve ser iniciado, continuado ou encerrado.

Responda SEMPRE em JSON:
{
  "arc_signal": "start|close|none",
  "arc_title": "<título dramático ou null>",
  "arc_summary": "<resumo do arco ou null>"
}

- "start": tensão nova emergindo, conflito se desenvolvendo, decisão importante
- "close": resolução chegou (confronto resolvido, aliança formada, objetivo alcançado/falhado)
- "none": meio de arco ou transição calma"""


def _build_summarizer_system() -> str:
    return """Você resume memória narrativa de um RPG.
Produza um resumo compacto e específico que capture: personagens envolvidos, eventos-chave, estado emocional do protagonista, e tensões abertas.
Máximo 250 palavras. Português. Sem formatação extra — apenas o texto do resumo."""


async def call_opening_narration(
    pack: dict | None,
    player_info: dict | None,
    backgrounds: list[dict],
) -> str:
    """Gera a narração de abertura quando um save é criado."""
    settings = get_settings()
    if not settings.ai_engine_enabled:
        name = player_info.get("name", "Protagonista") if player_info else "Protagonista"
        return (
            f"[PLACEHOLDER — ative AI_ENGINE_ENABLED=true para narração real]\n\n"
            f"A história de {name} começa aqui. O mundo espera sua primeira ação."
        )

    bg_hint = ""
    if backgrounds:
        bg_hint = f"\nBackgrounds disponíveis: {', '.join(b['name'] for b in backgrounds)}"

    system = _build_opening_system(pack, player_info)
    return await _call_claude(system, f"Escreva a cena de abertura.{bg_hint}", max_tokens=600)


async def call_narrator(
    action: PlayerAction,
    world_state: WorldState,
    memory_summary: str,
    recent_events: list[dict],
    pack: dict | None = None,
    player_info: dict | None = None,
    characters: list[dict] | None = None,
    backgrounds: list[dict] | None = None,
) -> AIResponse:
    settings = get_settings()

    if not settings.ai_engine_enabled:
        phase_flavor = {
            "morning": "A escola ainda está vazia.",
            "afternoon": "O corredor ferve de barulho.",
            "night": "As luzes piscam. Você está quase sozinho.",
        }
        return AIResponse(
            narration=(
                f"[PLACEHOLDER — configure ANTHROPIC_API_KEY e AI_ENGINE_ENABLED=true]\n\n"
                f"{phase_flavor.get(world_state.current_phase, '')}\n\n"
                f"Você disse: {action.dialogues}\nVocê fez: {action.actions}\n\n"
                f"Dia {world_state.current_day} — {world_state.current_phase.upper()}"
            ),
            scene_type="narrative",
        )

    system = _build_narrator_system(
        world_state, pack, player_info,
        characters or [], backgrounds or []
    )

    # Histórico recente
    history_lines = []
    for ev in (recent_events or [])[-15:]:
        t = ev.get("type", "")
        c = ev.get("content", "")
        if t == "player_action":
            history_lines.append(f"[JOGADOR]: {c}")
        elif t == "narration":
            history_lines.append(f"[NARRADOR]: {c[:300]}")
        elif t == "arc_event":
            history_lines.append(f"[ARCO]: {c}")

    user_content = f"""=== MEMÓRIA ===
{memory_summary or "Início da história."}

=== HISTÓRICO RECENTE ===
{chr(10).join(history_lines) or "Nenhum evento anterior."}

=== AÇÃO DO JOGADOR ===
Input completo: {action.raw_input}
Diálogos: {action.dialogues}
Ações: {action.actions}

Narre o que acontece a seguir. Lembre-se de decidir o tipo de cena."""

    raw = await _call_claude(system, user_content, max_tokens=1400)
    data = _extract_json(raw)

    return AIResponse(
        narration=data.get("narration", raw),
        scene_type=data.get("scene_type", "narrative"),
        active_characters=data.get("active_characters", []),
        background_hint=data.get("background_hint"),
        world_state_deltas=data.get("world_state_deltas", {}),
        relationship_updates=data.get("relationship_updates", {}),
        emergent_npcs=data.get("emergent_npcs", {}),
        arc_signal=data.get("arc_signal"),
    )


async def call_arc_analyst(
    world_state: WorldState,
    recent_events: list[dict],
    active_arc: dict | None,
) -> AIResponse:
    settings = get_settings()
    if not settings.ai_engine_enabled:
        return AIResponse(narration="", arc_signal=None)

    lines = [f"[{e.get('type','')}] {e.get('content','')[:150]}" for e in (recent_events or [])[-20:]]
    user_content = f"""Dia {world_state.current_day}, fase {world_state.current_phase}
Confiança: {world_state.confidence} | Violência: {world_state.violence} | Status: {world_state.social_status}
Arco ativo: {active_arc['title'] if active_arc else 'Nenhum'} | Eventos neste arco: {world_state.event_counter_arc}

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


async def call_summarizer(current_summary: str, recent_events: list[dict]) -> str:
    settings = get_settings()
    if not settings.ai_engine_enabled:
        return current_summary

    lines = [f"[{e.get('type','')}] {e.get('content','')[:200]}" for e in recent_events]
    user_content = f"""Resumo atual:
{current_summary or "Nenhum resumo ainda."}

Eventos a incorporar:
{chr(10).join(lines)}"""

    return await _call_claude(_build_summarizer_system(), user_content, max_tokens=400)


def calculate_fight_probability(world_state: WorldState) -> float:
    score = (
        world_state.violence * 0.4
        + world_state.confidence * 0.4
        + world_state.social_status * 0.2
    )
    normalized = (score + 20) / 120
    return max(0.05, min(0.95, normalized))