"""Cliente da https://onepieceapi.com com cache Redis (TTL 1h).

Rotas reais (inspecionadas via curl — ver CLAUDE.md):
  GET /api/characters?q=&page=&limit=   -> lista; cada item traz `bounties[]` (histórico completo)
  GET /api/characters/{id}              -> um personagem (404 => {"error": "Not found"})
  GET /api/bounties?sort=high|low&isActive=true|false -> lista; embute `characters{name,status,image_url}`
  GET /api/devil-fruits | /api/ships | /api/islands | /api/affiliations -> listas paginadas, SEM busca
`limit` máximo = 100. Só /api/characters aceita `?q=`; nos demais buscamos tudo e filtramos localmente.
"""

import logging
import re
import unicodedata
from typing import Any
from urllib.parse import urlencode

import httpx

from .cache import JsonCache

log = logging.getLogger(__name__)

PAGE_LIMIT = 100  # máximo aceito pela API
MAX_PAGES = 10  # trava de segurança na paginação
LIST_RESOURCES = ("devil-fruits", "ships", "islands", "affiliations")


class OnePieceAPIError(Exception):
    pass


class OnePieceClient:
    def __init__(self, base_url: str, cache: JsonCache, timeout: float = 10) -> None:
        self.cache = cache
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=True,  # onepieceapi.com -> www.onepieceapi.com (308)
            headers={"User-Agent": "LogPoseAI/1.0 (projeto academico)", "Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ HTTP + cache
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in sorted((params or {}).items()) if v is not None}
        key = f"opapi:v1:{path}?{urlencode(clean)}"

        async def load() -> Any:
            try:
                resp = await self._http.get(path, params=clean)
            except httpx.HTTPError as exc:
                raise OnePieceAPIError(f"Falha de rede ao consultar {path}: {exc}") from exc
            if resp.status_code == 404:
                return None  # cacheia o "não encontrado" também
            if resp.status_code >= 400:
                raise OnePieceAPIError(f"onepieceapi.com respondeu {resp.status_code} em {path}")
            return resp.json()

        return await self.cache.get_or_set(key, load)

    async def _get_all(self, resource: str) -> list[dict]:
        """Busca todas as páginas de um recurso sem filtro de busca (cacheado como um bloco só)."""

        async def load() -> list[dict]:
            items: list[dict] = []
            for page in range(1, MAX_PAGES + 1):
                batch = await self._get(f"/api/{resource}", {"limit": PAGE_LIMIT, "page": page})
                items.extend(batch or [])
                if not batch or len(batch) < PAGE_LIMIT:
                    break
            return items

        return await self.cache.get_or_set(f"opapi:v1:all:{resource}", load)

    # ------------------------------------------------------------------ endpoints
    async def search_characters(self, query: str, limit: int = 5) -> list[dict]:
        data = await self._get("/api/characters", {"q": query.strip().lower(), "limit": limit})
        return data or []

    async def get_character(self, character_id: str) -> dict | None:
        if not _looks_like_uuid(character_id):
            return None
        return await self._get(f"/api/characters/{character_id}")

    async def list_bounties(self, order: str = "high", active_only: bool = True, limit: int = 10) -> list[dict]:
        params: dict[str, Any] = {"sort": order, "limit": limit}
        if active_only:
            params["isActive"] = "true"
        return await self._get("/api/bounties", params) or []

    async def search_resource(self, resource: str, query: str, limit: int = 8, **filters: str | None) -> list[dict]:
        if resource not in LIST_RESOURCES:
            raise ValueError(f"recurso desconhecido: {resource}")
        items = await self._get_all(resource)
        needle = _norm(query)
        out = []
        for item in items:
            if needle and not any(needle in name for name in _searchable(item)):
                continue
            if any(v and _norm(v) != _norm(str(item.get(k) or "")) for k, v in filters.items()):
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    async def find_in_resource(self, resource: str, item_id: str) -> dict | None:
        items = await self._get_all(resource)
        return next((i for i in items if i.get("id") == item_id), None)


# ---------------------------------------------------------------------- helpers
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def _looks_like_uuid(value: str) -> bool:
    return bool(_UUID.match(value or ""))


def _norm(text: str) -> str:
    """minúsculas, sem acento, só [a-z0-9] — 'Mera-Mera no Mi' == 'meramera no mi' == 'MERAMERA'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _searchable(item: dict) -> list[str]:
    """Cada variante de nome (en/jp/romaji do nome e do 'model') normalizada separadamente."""
    parts: list[str] = []
    for field in ("name", "model"):
        value = item.get(field)
        if isinstance(value, dict):
            parts.extend(str(v) for v in value.values() if v)
        elif value:
            parts.append(str(value))
    return [_norm(p) for p in parts]


def pick_name(value: Any) -> str | None:
    """Nome localizado -> en > romaji > jp (convenção documentada pela API)."""
    if isinstance(value, dict):
        return value.get("en") or value.get("romaji") or value.get("jp")
    return value or None
