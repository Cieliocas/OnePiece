"""Cache JSON em Redis com degradação graciosa: se o Redis cair, o app segue sem cache."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis

log = logging.getLogger(__name__)


class JsonCache:
    def __init__(self, url: str, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._redis = redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001 - qualquer falha = indisponível
            return False

    async def get_or_set(self, key: str, loader: Callable[[], Awaitable[Any]]) -> Any:
        """Retorna o valor cacheado em `key`; senão executa `loader`, grava (TTL) e retorna."""
        try:
            cached = await self._redis.get(key)
            if cached is not None:
                log.info("cache HIT  %s", key)
                return json.loads(cached)
            log.info("cache MISS %s", key)
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis indisponível na leitura (%s); seguindo sem cache", exc)

        value = await loader()

        try:
            await self._redis.set(key, json.dumps(value, ensure_ascii=False), ex=self.ttl)
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis indisponível na escrita (%s)", exc)
        return value

    async def close(self) -> None:
        await self._redis.aclose()
