"""Configuração lida de variáveis de ambiente (injetadas pelo docker-compose via .env)."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    # Orçamento de "thinking" do Gemini: 0 desliga, -1 dinâmico.
    # 1024 é um bom meio-termo entre custo/latência e qualidade do filtro anti-spoiler.
    gemini_thinking_budget: int = _int("GEMINI_THINKING_BUDGET", 1024)
    max_tool_rounds: int = _int("MAX_TOOL_ROUNDS", 5)
    history_max_items: int = _int("HISTORY_MAX_ITEMS", 6)  # SPEC 03: últimas 6 mensagens

    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    cache_ttl_seconds: int = _int("CACHE_TTL_SECONDS", 3600)

    onepiece_api_base: str = os.getenv("ONEPIECE_API_BASE", "https://www.onepieceapi.com")
    http_timeout_seconds: int = _int("HTTP_TIMEOUT_SECONDS", 10)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key) and self.gemini_api_key != "sua_chave_aqui"


settings = Settings()
