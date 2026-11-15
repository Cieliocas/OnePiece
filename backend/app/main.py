"""Log Pose AI — API FastAPI. Todas as rotas ficam sob /api (o Nginx repassa /api/* sem reescrever)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .agent import ChatUnavailable, LogPoseAgent
from .cache import JsonCache
from .config import settings
from .onepiece_api import OnePieceClient
from .prompts import ARCS, resolve_arc
from .schemas import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("logpose")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = JsonCache(settings.redis_url, settings.cache_ttl_seconds)
    api = OnePieceClient(settings.onepiece_api_base, cache, timeout=settings.http_timeout_seconds)
    app.state.cache, app.state.api, app.state.agent = cache, api, LogPoseAgent(api)
    if not settings.gemini_configured:
        log.warning("GEMINI_API_KEY ausente: /api/chat responderá 503 até ser configurada.")
    yield
    await api.close()
    await cache.close()


app = FastAPI(
    title="Log Pose AI",
    version="1.0.0",
    description="Chatbot guia de One Piece (Gemini + onepieceapi.com + Redis).",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "redis": await app.state.cache.ping(),
        "gemini_configured": settings.gemini_configured,
        "model": settings.gemini_model,
    }


@app.get("/api/arcs")
async def arcs() -> list[dict]:
    """Lista oficial de arcos aceitos em `current_arc` (útil para depuração/integração)."""
    return [{"id": a.id, "label": a.label, "episodes": a.episodes} for a in ARCS]


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    arc = resolve_arc(req.current_arc)
    if arc is None:
        raise HTTPException(422, f"current_arc inválido: '{req.current_arc}'. Use um id de GET /api/arcs.")
    try:
        return await app.state.agent.chat(req.message, arc, req.history)
    except ChatUnavailable as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
