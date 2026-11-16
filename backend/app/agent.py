"""Loop de Function Calling com o SDK oficial `google-genai` (modelo gemini-3.5-flash por padrão).

Usamos o loop MANUAL (automatic_function_calling desligado) para: (1) passar toda chamada pelo cache
Redis via ToolRunner, (2) capturar os dados estruturados que viram `cards` e (3) limitar o nº de rodadas.
"""

import asyncio
import logging

from google import genai
from google.genai import errors, types

from .config import settings
from .onepiece_api import OnePieceClient
from .prompts import Arc, build_system_instruction
from .schemas import ChatResponse, HistoryItem
from .tools import TOOL_DECLARATIONS, ToolRunner

log = logging.getLogger(__name__)

FALLBACK_REPLY = "Hmm, a corrente do Grand Line me desviou do curso. 🌊 Pode reformular a pergunta?"


class ChatUnavailable(Exception):
    """Erro 'esperado' (cota, chave inválida, modelo fora do ar) com mensagem amigável."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def _history_to_contents(history: list[HistoryItem], max_items: int) -> list[types.Content]:
    contents: list[types.Content] = []
    for item in history[-max_items:]:
        text = item.content.strip()
        if not text:
            continue
        role = "user" if item.role == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))
    while contents and contents[0].role != "user":  # o diálogo deve começar pelo usuário
        contents.pop(0)
    return contents


class LogPoseAgent:
    def __init__(self, api: OnePieceClient) -> None:
        self.api = api
        self._client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_configured else None

    def _config(self, arc: Arc, allow_tools: bool) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=build_system_instruction(arc),
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO" if allow_tools else "NONE")
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_budget=settings.gemini_thinking_budget),
            temperature=0.4,
            max_output_tokens=4096,
        )

    async def chat(self, message: str, arc: Arc, history: list[HistoryItem]) -> ChatResponse:
        if self._client is None:
            raise ChatUnavailable("GEMINI_API_KEY não configurada no servidor (.env).", 503)

        contents = _history_to_contents(history, settings.history_max_items)
        # Lembrete do arco junto da pergunta: reforça o filtro anti-spoiler mesmo com histórico longo.
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"[Arco atual do usuário: {arc.label}]\n{message.strip()}")],
        ))

        runner = ToolRunner(self.api, arc)
        response: types.GenerateContentResponse | None = None
        try:
            for round_no in range(settings.max_tool_rounds + 1):
                last_round = round_no == settings.max_tool_rounds
                response = await self._client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=contents,
                    config=self._config(arc, allow_tools=not last_round),
                )
                calls = response.function_calls or []
                if not calls:
                    break
                # Devolve o turno do modelo (preserva thought_signature) + as respostas das funções.
                contents.append(response.candidates[0].content)
                results = await asyncio.gather(*(runner.run(c.name, dict(c.args or {})) for c in calls))
                log.info("tools: %s", [c.name for c in calls])
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=c.name, response={"result": r})
                           for c, r in zip(calls, results, strict=True)],
                ))
        except errors.APIError as exc:
            log.error("Gemini APIError %s: %s", exc.code, exc.message)
            if exc.code == 429:
                raise ChatUnavailable("Muitos piratas pedindo ao mesmo tempo (limite da API Gemini). "
                                      "Tente de novo em alguns segundos.", 429) from exc
            if exc.code in (400, 401, 403) and "API key" in str(exc.message):
                raise ChatUnavailable("A GEMINI_API_KEY configurada é inválida.", 503) from exc
            raise ChatUnavailable("O serviço de IA está indisponível no momento.", 502) from exc

        reply = (response.text or "").strip() if response else ""
        if not reply:
            reply = FALLBACK_REPLY
        cards = runner.cards if runner.cards is not None else runner.fallback_cards(reply)
        return ChatResponse(reply=reply, cards=cards)
