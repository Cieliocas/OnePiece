"""Contratos HTTP de POST /api/chat."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


MAX_MESSAGE_CHARS = 500  # SPEC 03
MAX_HISTORY_ITEM_CHARS = 4000
MAX_HISTORY_LIST = 20  # rejeita payloads abusivos; só as 6 últimas são usadas (HISTORY_MAX_ITEMS)


class HistoryItem(BaseModel):
    role: Literal["user", "assistant", "model"]
    content: str = Field(max_length=MAX_HISTORY_ITEM_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    current_arc: str = Field(min_length=1, max_length=60, description="id do arco (ex.: 'wano') ou rótulo ('Wano')")
    history: list[HistoryItem] = Field(default_factory=list, max_length=MAX_HISTORY_LIST)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message não pode ser vazia")
        return value


class CardDetail(BaseModel):
    label: str
    value: str


class Card(BaseModel):
    type: Literal["character", "devil_fruit", "ship", "island", "affiliation"]
    id: str
    name: str
    subtitle: str | None = None
    image_url: str | None = None
    bounty: int | None = None  # em Berries; já filtrado pelo anti-spoiler
    devil_fruit: str | None = None
    affiliation: str | None = None
    note: str | None = None
    details: list[CardDetail] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    cards: list[Card] = Field(default_factory=list)
