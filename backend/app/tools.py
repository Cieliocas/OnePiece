"""Function Calling: declarações das ferramentas (Gemini) + execução + montagem dos cards.

Fluxo dos cards (anti-spoiler):
  1. As ferramentas de busca devolvem dados da API ao modelo e registram cada entidade em `self.seen`.
  2. O modelo chama `show_cards` escolhendo QUAIS entidades mostrar e QUAIS valores são seguros no arco
     atual (recompensa, fruta, afiliação). O backend monta o card a partir dos dados da API e valida a
     recompensa: ela precisa existir no histórico real do personagem (o modelo não inventa números).
  3. Se o modelo não chamar `show_cards`, há um fallback conservador (ver `fallback_cards`).
"""

import logging
import re
from typing import Any

from google.genai import types

from .onepiece_api import OnePieceAPIError, OnePieceClient, pick_name
from .prompts import Arc, is_latest_arc
from .schemas import Card, CardDetail

log = logging.getLogger(__name__)

MAX_CARDS = 4
# palavras genéricas que não identificam uma entidade sozinhas (fallback de cards)
_STOPWORDS = {"fruit", "pirates", "kingdom", "island", "family", "crew", "monkey", "charlotte", "donquixote"}
ENTITY_RESOURCES = {
    "devil_fruit": "devil-fruits",
    "ship": "ships",
    "island": "islands",
    "affiliation": "affiliations",
}


def _decl(name: str, description: str, properties: dict, required: list[str] | None = None) -> types.FunctionDeclaration:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return types.FunctionDeclaration(name=name, description=description, parameters_json_schema=schema)


_QUERY = {"type": "string", "description": "Nome curto em inglês ou romaji. Use \"\" para listar sem filtro."}

TOOL_DECLARATIONS: list[types.FunctionDeclaration] = [
    _decl(
        "search_characters",
        "Busca personagens por nome (GET /api/characters?q=). Retorna idade, status, altura, aniversário, "
        "extra_data (epíteto, ocupação, afiliação, origem, 1ª aparição) e o HISTÓRICO COMPLETO de recompensas "
        "em ordem crescente — inclui valores FUTUROS, aplique o filtro anti-spoiler.",
        {
            "query": {"type": "string", "description": "Nome ou parte do nome, ex.: 'Luffy', 'Zoro', 'Trafalgar'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Máx. de resultados (padrão 5)."},
        },
        ["query"],
    ),
    _decl(
        "get_character",
        "Detalhes de um personagem pelo id (GET /api/characters/{id}), incluindo histórico de recompensas.",
        {"character_id": {"type": "string", "description": "UUID retornado por outra ferramenta."}},
        ["character_id"],
    ),
    _decl(
        "list_top_bounties",
        "Ranking de recompensas (GET /api/bounties?sort=). ATENÇÃO: os maiores valores são quase todos pós-timeskip; "
        "filtre pelo arco do usuário antes de responder.",
        {
            "order": {"type": "string", "enum": ["high", "low"], "description": "high = maiores primeiro."},
            "active_only": {"type": "boolean", "description": "Só recompensas vigentes hoje (padrão true)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Padrão 10."},
        },
    ),
    _decl(
        "search_devil_fruits",
        "Busca Akuma no Mi (GET /api/devil-fruits) por nome em inglês ('Flame-Flame') ou romaji ('Mera Mera'). "
        "Retorna nome, modelo, tipo e subtipo. A API NÃO informa quem comeu a fruta.",
        {
            "query": _QUERY,
            "type": {"type": "string", "enum": ["Paramecia", "Zoan", "Logia"], "description": "Filtro opcional."},
        },
        ["query"],
    ),
    _decl(
        "search_ships",
        "Busca navios (GET /api/ships): tipo, status, afiliação, 1ª aparição, dimensões.",
        {"query": _QUERY},
        ["query"],
    ),
    _decl(
        "search_islands",
        "Busca ilhas/reinos (GET /api/islands): mar, tipo, população, afiliação, 1ª aparição.",
        {
            "query": _QUERY,
            "sea": {"type": "string", "description": "Filtro opcional, ex.: 'East Blue', 'Grand Line', 'New World'."},
        },
        ["query"],
    ),
    _decl(
        "search_affiliations",
        "Busca tripulações, famílias e organizações (GET /api/affiliations) com número de membros.",
        {"query": _QUERY},
        ["query"],
    ),
    _decl(
        "show_cards",
        "Exibe cards visuais na interface para as entidades centrais da resposta (máx. 4). Chame no máximo uma vez, "
        "depois das buscas. Preencha os campos opcionais SOMENTE com valores válidos no arco atual do usuário; "
        "omita o campo quando for spoiler ou desconhecido.",
        {
            "cards": {
                "type": "array",
                "maxItems": MAX_CARDS,
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_type": {"type": "string", "enum": ["character", *ENTITY_RESOURCES]},
                        "id": {"type": "string", "description": "id retornado por uma ferramenta de busca."},
                        "bounty": {
                            "type": "integer",
                            "description": "Recompensa vigente NO ARCO DO USUÁRIO; precisa ser um valor do bounty_history.",
                        },
                        "devil_fruit": {"type": "string", "description": "Fruta como conhecida no arco do usuário."},
                        "affiliation": {"type": "string", "description": "Afiliação no arco do usuário."},
                        "note": {"type": "string", "description": "Legenda curta e sem spoiler (ex.: epíteto)."},
                    },
                    "required": ["entity_type", "id"],
                },
            }
        },
        ["cards"],
    ),
]


# --------------------------------------------------------------------------- formatação p/ o modelo
def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _localized(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _bounty_history(raw: dict) -> list[dict]:
    rows = [b for b in raw.get("bounties") or [] if b.get("amount")]
    return [{"amount": int(b["amount"]), "is_active": b.get("is_active")} for b in sorted(rows, key=lambda b: b["amount"])]


def character_for_model(raw: dict) -> dict:
    name, extra = _localized(raw.get("name")), raw.get("extra_data") or {}
    birthday = _localized((raw.get("birthday") or {}).get("label"))
    return _compact({
        "id": raw.get("id"),
        "name": pick_name(name),
        "name_jp": name.get("jp"),
        "age": raw.get("age"),
        "status": raw.get("status"),
        "height_cm": raw.get("height"),
        "blood_type": raw.get("blood_type"),
        "birthday": birthday.get("pt") or birthday.get("en"),
        "epithet": extra.get("epithet"),
        "occupation": extra.get("occupation"),
        "affiliation": extra.get("affiliation"),
        "origin": extra.get("origin"),
        "first_appearance": extra.get("first_appearance"),
        "bounty_history": _bounty_history(raw),
    })


def fruit_for_model(raw: dict) -> dict:
    name, model = _localized(raw.get("name")), _localized(raw.get("model"))
    return _compact({
        "id": raw.get("id"), "name": pick_name(name), "romaji": name.get("romaji"), "name_jp": name.get("jp"),
        "model": pick_name(model), "type": raw.get("type"), "sub_type": raw.get("sub_type"),
    })


def ship_for_model(raw: dict) -> dict:
    name, extra = _localized(raw.get("name")), raw.get("extra_data") or {}
    return _compact({
        "id": raw.get("id"), "name": pick_name(name), "romaji": name.get("romaji"), "type": raw.get("type"),
        "status": raw.get("status"), **{k: extra.get(k) for k in ("affiliation", "first_appearance", "length", "height")},
    })


def island_for_model(raw: dict) -> dict:
    name, extra = _localized(raw.get("name")), raw.get("extra_data") or {}
    return _compact({
        "id": raw.get("id"), "name": pick_name(name), "romaji": name.get("romaji"), "sea": raw.get("sea"),
        **{k: extra.get(k) for k in ("type", "population", "affiliation", "first_appearance")},
    })


def affiliation_for_model(raw: dict) -> dict:
    return _compact({"id": raw.get("id"), "name": pick_name(raw.get("name")), "member_count": raw.get("memberCount")})


# --------------------------------------------------------------------------- cards
def _details(*pairs: tuple[str, Any]) -> list[CardDetail]:
    return [CardDetail(label=label, value=str(value)) for label, value in pairs if value not in (None, "")]


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))  # o Gemini pode devolver inteiros grandes como float
    except (TypeError, ValueError):
        return None


def build_card(
    entity_type: str,
    raw: dict,
    *,
    bounty: Any = None,
    devil_fruit: str | None = None,
    affiliation: str | None = None,
    note: str | None = None,
) -> Card:
    name = _localized(raw.get("name"))
    extra = raw.get("extra_data") or {}
    base = {"type": entity_type, "id": raw["id"], "name": pick_name(raw.get("name")) or "???",
            "image_url": raw.get("image_url"), "note": note or None}

    if entity_type == "character":
        amounts = {b["amount"] for b in _bounty_history(raw)}
        wanted = _as_int(bounty)
        return Card(
            **base,
            subtitle=name.get("jp"),
            bounty=wanted if wanted in amounts else None,  # só valores reais da API
            devil_fruit=devil_fruit or None,
            affiliation=affiliation or None,  # personagem: só o que o modelo declarou seguro
            details=_details(("1ª aparição", extra.get("first_appearance"))),
        )
    if entity_type == "devil_fruit":
        kind = " · ".join(x for x in (raw.get("type"), raw.get("sub_type")) if x and x != "Unknown")
        return Card(**base, subtitle=name.get("romaji"),
                    details=_details(("Tipo", kind), ("Modelo", pick_name(raw.get("model")))))
    if entity_type == "ship":
        return Card(**base, subtitle=name.get("romaji"), affiliation=affiliation or extra.get("affiliation"),
                    details=_details(("Tipo", raw.get("type")), ("1ª aparição", extra.get("first_appearance")),
                                     ("Comprimento", extra.get("length"))))
    if entity_type == "island":
        return Card(**base, subtitle=name.get("romaji"), affiliation=affiliation or extra.get("affiliation"),
                    details=_details(("Mar", raw.get("sea")), ("Tipo", extra.get("type")),
                                     ("População", extra.get("population")),
                                     ("1ª aparição", extra.get("first_appearance"))))
    # affiliation
    return Card(**base, details=_details(("Membros na API", raw.get("memberCount"))))


# --------------------------------------------------------------------------- execução
class ToolRunner:
    """Executa as chamadas de função de UMA requisição de chat e guarda o que foi visto."""

    def __init__(self, api: OnePieceClient, arc: Arc) -> None:
        self.api = api
        self.arc = arc
        self.seen: dict[str, tuple[str, dict]] = {}  # id -> (entity_type, raw)
        self.cards: list[Card] | None = None  # preenchido por show_cards
        self._handlers = {
            "search_characters": self._search_characters,
            "get_character": self._get_character,
            "list_top_bounties": self._list_top_bounties,
            "search_devil_fruits": self._search_devil_fruits,
            "search_ships": self._search_ships,
            "search_islands": self._search_islands,
            "search_affiliations": self._search_affiliations,
            "show_cards": self._show_cards,
        }

    async def run(self, name: str, args: dict[str, Any] | None) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"ferramenta desconhecida: {name}"}
        try:
            return await handler(**(args or {}))
        except TypeError as exc:
            return {"error": f"argumentos inválidos para {name}: {exc}"}
        except OnePieceAPIError as exc:
            log.warning("onepieceapi falhou em %s: %s", name, exc)
            return {"error": "A onepieceapi.com não respondeu agora. Responda com seu conhecimento, sem spoilers."}

    def _remember(self, entity_type: str, items: list[dict]) -> None:
        for item in items:
            if item and item.get("id"):
                self.seen[item["id"]] = (entity_type, item)

    # --- buscas -------------------------------------------------------------
    async def _search_characters(self, query: str, limit: int = 5) -> dict:
        items = await self.api.search_characters(query, max(1, min(_as_int(limit) or 5, 10)))
        self._remember("character", items)
        return {"count": len(items), "results": [character_for_model(c) for c in items]}

    async def _get_character(self, character_id: str) -> dict:
        raw = await self.api.get_character(character_id)
        if not raw:
            return {"error": "personagem não encontrado"}
        self._remember("character", [raw])
        return character_for_model(raw)

    async def _list_top_bounties(self, order: str = "high", active_only: bool = True, limit: int = 10) -> dict:
        order = order if order in ("high", "low") else "high"
        rows = await self.api.list_bounties(order, bool(active_only), max(1, min(_as_int(limit) or 10, 25)))
        results = []
        for b in rows:
            ch = b.get("characters") or {}
            results.append(_compact({
                "character_id": b.get("character_id"), "character_name": pick_name(ch.get("name")),
                "amount": _as_int(b.get("amount")), "is_active": b.get("is_active"), "status": ch.get("status"),
            }))
        return {"count": len(results), "results": results,
                "hint": "Para mostrar card, use o character_id com entity_type='character'."}

    async def _search_list(self, entity_type: str, formatter, query: str, **filters: str | None) -> dict:
        items = await self.api.search_resource(ENTITY_RESOURCES[entity_type], query, **filters)
        self._remember(entity_type, items)
        return {"count": len(items), "results": [formatter(i) for i in items]}

    async def _search_devil_fruits(self, query: str, type: str | None = None) -> dict:  # noqa: A002
        return await self._search_list("devil_fruit", fruit_for_model, query, type=type)

    async def _search_ships(self, query: str) -> dict:
        return await self._search_list("ship", ship_for_model, query)

    async def _search_islands(self, query: str, sea: str | None = None) -> dict:
        return await self._search_list("island", island_for_model, query, sea=sea)

    async def _search_affiliations(self, query: str) -> dict:
        return await self._search_list("affiliation", affiliation_for_model, query)

    # --- cards --------------------------------------------------------------
    async def _resolve(self, entity_type: str, entity_id: str) -> dict | None:
        if entity_id in self.seen and self.seen[entity_id][0] == entity_type:
            return self.seen[entity_id][1]
        if entity_type == "character":
            return await self.api.get_character(entity_id)
        if entity_type in ENTITY_RESOURCES:
            return await self.api.find_in_resource(ENTITY_RESOURCES[entity_type], entity_id)
        return None

    async def _show_cards(self, cards: list[dict]) -> dict:
        built: list[Card] = []
        for spec in (cards or [])[:MAX_CARDS]:
            if not isinstance(spec, dict):
                continue
            entity_type, entity_id = spec.get("entity_type"), str(spec.get("id") or "")
            raw = await self._resolve(entity_type, entity_id) if entity_type and entity_id else None
            if not raw:
                continue
            built.append(build_card(entity_type, raw, bounty=spec.get("bounty"), devil_fruit=spec.get("devil_fruit"),
                                    affiliation=spec.get("affiliation"), note=spec.get("note")))
        self.cards = built
        return {"ok": True, "cards_shown": len(built),
                "instruction": "Cards exibidos. Agora escreva a resposta final em texto."}

    def fallback_cards(self, reply: str) -> list[Card]:
        """Se o modelo não chamou show_cards: mostra só entidades citadas no texto, com campos
        sensíveis ocultos (recompensa só aparece se o usuário estiver no arco mais recente)."""
        text = reply.lower()
        out: list[Card] = []
        for entity_type, raw in self.seen.values():
            name = (pick_name(raw.get("name")) or "").lower()
            # nome completo OU alguma palavra marcante dele (≥4 letras) citada no texto: "Luffy" ∈ "Monkey D. Luffy"
            words = [w for w in re.findall(r"[a-z0-9]{4,}", name) if w not in _STOPWORDS]
            if not name or not (name in text or any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)):
                continue
            bounty = None
            if entity_type == "character" and is_latest_arc(self.arc):
                active = [b["amount"] for b in _bounty_history(raw) if b["is_active"]]
                bounty = max(active) if active else None
            out.append(build_card(entity_type, raw, bounty=bounty))
            if len(out) >= MAX_CARDS:
                break
        return out
