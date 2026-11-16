# SPEC 01 — Arquitetura & Fluxo

**Status:** vigente · **Implementação:** `docker-compose.yml`, `nginx/`, `backend/app/`, `frontend/`

## 1. Topologia

```
                         Internet
                            │ HTTP :80 (única porta publicada)
                ┌───────────▼────────────┐
 Usuário ─────▶ │ nginx (1.30-alpine)    │  /        → frontend estático
 (navegador)    │ rate limit · headers   │  /api/*   → backend:8000
                └───────────┬────────────┘
        rede Docker interna │ logpose-net (bridge)
                ┌───────────▼────────────┐        HTTPS
                │ backend (FastAPI)      │ ─────────────▶ Gemini API (gemini-3.5-flash)
                │ uvicorn · non-root     │ ─────────────▶ www.onepieceapi.com (só em cache miss)
                └───────────┬────────────┘
                ┌───────────▼────────────┐
                │ redis (alpine)         │  cache JSON, TTL 1h, sem persistência
                └────────────────────────┘
```

## 2. Contrato da API interna

### `POST /api/chat`

Request (`Content-Type: application/json`, corpo ≤ 16 KB):

```json
{
  "message": "Qual a recompensa do Zoro?",
  "current_arc": "water-7",
  "history": [
    { "role": "user", "content": "Quem é o Luffy?" },
    { "role": "assistant", "content": "**Luffy** é o capitão..." }
  ]
}
```

| Campo | Regra |
|---|---|
| `message` | string, 1–500 caracteres |
| `current_arc` | id de `GET /api/arcs` (`east-blue` … `egghead`) ou o rótulo (`Wano`) |
| `history[]` | opcional; `role` ∈ `user` \| `assistant` \| `model`; só as **6 últimas** mensagens são usadas |

Response `200`:

```json
{
  "reply": "Depois de Enies Lobby, **Zoro** vale ฿ 120.000.000...",
  "cards": [{
    "type": "character", "id": "faf3b05c-...", "name": "Roronoa Zoro", "subtitle": "ロロノア・ゾロ",
    "image_url": "https://...webp", "bounty": 120000000, "devil_fruit": null,
    "affiliation": "Straw Hat Pirates", "note": null, "details": [{ "label": "1ª aparição", "value": "..." }]
  }]
}
```

`type` ∈ `character | devil_fruit | ship | island | affiliation`. `cards` pode vir vazio.

Erros (sempre `{"detail": "<mensagem>"}`): `422` validação/arco · `429` rate limit (nginx) ou cota Gemini ·
`502` Gemini falhou · `503` chave ausente/inválida.

### Rotas auxiliares
`GET /api/health` (sem rate limit) · `GET /api/arcs` · `GET /api/docs` (Swagger).

## 3. Function Calling → onepieceapi.com

| Tool | Rota real | Busca |
|---|---|---|
| `search_characters` | `GET /api/characters?q=&limit=` | servidor (`q`) |
| `get_character` | `GET /api/characters/{id}` | — |
| `list_top_bounties` | `GET /api/bounties?sort=high\|low&isActive=` | — |
| `search_devil_fruits` | `GET /api/devil-fruits?limit=100&page=N` | local |
| `search_ships` | `GET /api/ships?limit=100` | local |
| `search_islands` | `GET /api/islands?limit=100` | local |
| `search_affiliations` | `GET /api/affiliations?limit=100&page=N` | local |
| `show_cards` | — (monta `cards` com dados já obtidos) | — |

Particularidades: `limit` máx. 100; só `characters` aceita `q`; personagens trazem o **histórico completo** de
recompensas (inclui valores futuros → ver SPEC 03); 404 = `{"error":"Not found"}`.

## 4. Cache e resiliência

| Item | Valor |
|---|---|
| Chave | `opapi:v1:<rota>?<query ordenada>`; listas completas em `opapi:v1:all:<recurso>` |
| TTL | 3600 s (`CACHE_TTL_SECONDS`) |
| Memória | `maxmemory 64mb`, `allkeys-lru`, sem RDB/AOF |
| 404 da API | também cacheado (evita repetir buscas inválidas) |

Fallbacks (para a aula não parar):

| Falha | Comportamento |
|---|---|
| Redis fora | log `WARNING`, consulta a API direto; o chat continua funcionando (só mais lento) |
| onepieceapi.com fora/timeout (10 s) | a tool devolve erro ao modelo, que responde com conhecimento próprio, sem spoilers; sem cards |
| Gemini 429/5xx | HTTP 429/502 com mensagem amigável no chat |
| Modelo não chamou `show_cards` | cards conservadores: só entidades citadas no texto, recompensa oculta (exceto Egghead) |

Dica de aula: rode as perguntas do roteiro uma vez antes (SPEC 06) — o cache fica "quente" por 1 h.
