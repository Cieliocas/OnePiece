"""Testes sem rede: Gemini falso + onepieceapi mockada (httpx.MockTransport) + cache em memória.

Rodar:  cd backend && pip install -r requirements-dev.txt && pytest -q
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app.agent import LogPoseAgent
from app.main import app
from app.onepiece_api import OnePieceClient
from app.prompts import resolve_arc
from app.schemas import HistoryItem

LUFFY_ID = "7cc1d5ca-fb8c-46d4-b521-521fc7b28b3c"
LUFFY = {
    "id": LUFFY_ID,
    "name": {"en": "Monkey D. Luffy", "jp": "モンキー・D・ルフィ", "romaji": "Monkī Dī Rufi"},
    "age": 19, "status": "Alive", "height": 174, "blood_type": "F", "birthday": None,
    "image_url": "https://example.test/luffy.webp", "extra_data": None,
    "bounties": [
        {"amount": 3000000000, "is_active": True},
        {"amount": 30000000, "is_active": False},
        {"amount": 100000000, "is_active": False},
    ],
}
FRUITS = [
    {"id": "f1", "name": {"en": "Flame-Flame Fruit", "romaji": "Mera Mera no Mi"}, "model": None,
     "type": "Logia", "sub_type": None, "image_url": None},
    {"id": "f2", "name": {"en": "Mutt-Mutt Fruit", "romaji": "Inu Inu no Mi"}, "model": {"en": "Dachshund"},
     "type": "Zoan", "sub_type": None, "image_url": None},
]


def fake_onepiece_api(request: httpx.Request) -> httpx.Response:
    path, q = request.url.path, request.url.params.get("q", "")
    if path == "/api/characters":
        return httpx.Response(200, json=[LUFFY] if "luffy" in q else [])
    if path == f"/api/characters/{LUFFY_ID}":
        return httpx.Response(200, json=LUFFY)
    if path.startswith("/api/characters/"):
        return httpx.Response(404, json={"error": "Not found"})
    if path == "/api/devil-fruits":
        return httpx.Response(200, json=FRUITS if request.url.params.get("page") == "1" else [])
    return httpx.Response(500, json={"error": "unexpected"})


class MemoryCache:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get_or_set(self, key, loader):
        if key not in self.store:
            self.store[key] = await loader()
        return self.store[key]

    async def ping(self) -> bool:
        return True


def make_api() -> OnePieceClient:
    api = OnePieceClient("https://api.test", MemoryCache())
    api._http = httpx.AsyncClient(base_url="https://api.test", transport=httpx.MockTransport(fake_onepiece_api))
    return api


def calls_response(*calls: tuple[str, dict]) -> types.GenerateContentResponse:
    parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in calls]
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


def text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


class FakeGemini:
    """Imita client.aio.models.generate_content devolvendo respostas roteirizadas."""

    def __init__(self, script: list[types.GenerateContentResponse]) -> None:
        self.script, self.requests = list(script), []
        self.aio = self
        self.models = self

    async def generate_content(self, *, model, contents, config):
        self.requests.append({"contents": list(contents), "config": config})
        return self.script.pop(0)


def run_agent(script, arc_id="east-blue", message="Quem é o Luffy?", history=None):
    agent = LogPoseAgent(make_api())
    agent._client = FakeGemini(script)
    history = history if history is not None else [HistoryItem(role="user", content="oi")]
    result = asyncio.run(agent.chat(message, resolve_arc(arc_id), history))
    return result, agent._client


# ----------------------------------------------------------------------------- HTTP
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_health_and_arcs(client):
    assert client.get("/api/health").json()["status"] == "ok"
    arcs = client.get("/api/arcs").json()
    assert arcs[0]["id"] == "east-blue" and arcs[-1]["id"] == "egghead" and len(arcs) == 11


def test_invalid_arc_is_rejected(client):
    r = client.post("/api/chat", json={"message": "oi", "current_arc": "naruto", "history": []})
    assert r.status_code == 422


def test_chat_without_api_key_returns_503(client):
    app.state.agent._client = None
    r = client.post("/api/chat", json={"message": "Quem é Zoro?", "current_arc": "Wano", "history": []})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"message": "a" * 500}, 503),  # limite exato passa na validação (503 = sem chave)
        ({"message": "a" * 501}, 422),
        ({"message": "   "}, 422),
        ({"message": "oi", "history": [{"role": "user", "content": "x"}] * 21}, 422),
        ({"message": "oi", "history": [{"role": "system", "content": "x"}]}, 422),
    ],
)
def test_input_limits_spec03(client, payload, status):
    app.state.agent._client = None
    r = client.post("/api/chat", json={"current_arc": "wano", "history": [], **payload})
    assert r.status_code == status


def test_only_last_6_history_messages_reach_model():
    history = [HistoryItem(role="user" if i % 2 == 0 else "assistant", content=f"msg{i}") for i in range(20)]
    _, fake = run_agent([text_response("ok")], history=history)
    sent = fake.requests[0]["contents"]
    assert len(sent) == 7  # 6 do histórico + a pergunta atual
    assert sent[0].parts[0].text == "msg14" and sent[0].role == "user"


# ----------------------------------------------------------------------------- agente + cards
def test_tool_loop_builds_validated_cards():
    result, fake = run_agent([
        calls_response(("search_characters", {"query": "Luffy"})),
        calls_response(("show_cards", {"cards": [{
            "entity_type": "character", "id": LUFFY_ID, "bounty": 30000000.0,  # float, como o Gemini pode mandar
            "devil_fruit": "Gomu Gomu no Mi", "affiliation": "Piratas do Chapéu de Palha",
        }]})),
        text_response("**Luffy** é o capitão dos Chapéus de Palha!"),
    ])
    assert result.reply.startswith("**Luffy**")
    [card] = result.cards
    assert (card.name, card.bounty, card.devil_fruit) == ("Monkey D. Luffy", 30000000, "Gomu Gomu no Mi")
    # A 2ª chamada ao Gemini recebeu o resultado da busca como function_response
    fn_parts = [p.function_response for p in fake.requests[1]["contents"][-1].parts]
    assert fn_parts[0].name == "search_characters"
    assert fn_parts[0].response["result"]["results"][0]["bounty_history"][0]["amount"] == 30000000
    # O lembrete do arco vai junto da mensagem do usuário
    assert "[Arco atual do usuário: East Blue]" in fake.requests[0]["contents"][-1].parts[0].text


def test_bounty_not_in_api_history_is_dropped():
    result, _ = run_agent([
        calls_response(("show_cards", {"cards": [{"entity_type": "character", "id": LUFFY_ID, "bounty": 123}]})),
        text_response("Luffy!"),
    ])
    assert result.cards[0].bounty is None  # resolvido via GET /api/characters/{id}, mas valor inventado é descartado


def test_fallback_cards_hide_bounty_before_latest_arc():
    script = [calls_response(("search_characters", {"query": "luffy"})), text_response("O Luffy é elástico.")]
    early, _ = run_agent(list(script), arc_id="alabasta")
    latest, _ = run_agent(list(script), arc_id="egghead")
    assert early.cards[0].bounty is None
    assert latest.cards[0].bounty == 3000000000


def test_last_round_forces_text_answer():
    from app.config import settings

    loop = [calls_response(("search_characters", {"query": "luffy"}))] * settings.max_tool_rounds
    result, fake = run_agent(loop + [text_response("Resposta final.")])
    assert result.reply == "Resposta final."
    modes = [r["config"].tool_config.function_calling_config.mode for r in fake.requests]
    assert modes[-1] == "NONE" and all(m == "AUTO" for m in modes[:-1])


# ----------------------------------------------------------------------------- busca local
def test_local_fruit_search_matches_romaji_and_type():
    api = make_api()
    by_romaji = asyncio.run(api.search_resource("devil-fruits", "mera mera"))
    by_type = asyncio.run(api.search_resource("devil-fruits", "", type="Zoan"))
    by_model = asyncio.run(api.search_resource("devil-fruits", "dachshund"))
    assert [f["id"] for f in by_romaji] == ["f1"]
    assert [f["id"] for f in by_type] == ["f2"]
    assert [f["id"] for f in by_model] == ["f2"]
