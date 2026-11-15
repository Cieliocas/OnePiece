# CLAUDE.md — Log Pose AI

Chatbot guia de **One Piece** com filtro anti-spoiler. Projeto acadêmico pensado para rodar barato numa
EC2 `t3.micro` (1 GB RAM) via Docker Compose. Este arquivo é o guia técnico para quem for continuar o trabalho.

> **Fonte da verdade: [`specs/`](specs/)** (Spec-Driven Development). Mudou limite, rota ou contrato?
> Atualize a spec no mesmo PR. 01 arquitetura · 02 segurança · 03 limitadores · 04 economia AWS ·
> 05 repositório/handoff · 06 deploy e roteiro da aula.

## Arquitetura

```
 Navegador ──HTTP:80──▶ nginx ─┬─ /        → frontend/ (HTML + Tailwind CDN + Vanilla JS)
                               └─ /api/*   → backend:8000 (FastAPI + Uvicorn)
                                                │  google-genai (gemini-2.5-flash, Function Calling)
                                                ├──▶ Gemini API
                                                ├──▶ redis:6379 (cache JSON, TTL 1h)
                                                └──▶ https://www.onepieceapi.com (só em cache miss)
```

Os 3 serviços ficam na rede `logpose-net` (bridge). **Só o nginx publica porta (80)**; backend e redis
são internos. O nginx resolve `backend` pelo DNS do Docker (`resolver 127.0.0.11`) a cada 30 s, então um
restart do backend não exige reiniciar o nginx.

## Estrutura

```
.
├── README.md                 # vitrine do GitHub: requisitos da avaliação, uso do endpoint
├── specs/                    # especificações SDD (01–06)
├── docs/img/                 # capturas de tela do README
├── docker-compose.yml        # nginx + backend + redis (mem_limit 64m/512m/128m, logs 3x10m)
├── setup-ec2.sh              # swap 2 GB + Docker + .env + compose up (Ubuntu 24.04)
├── .env.example              # GEMINI_API_KEY=sua_chave_aqui
├── nginx/
│   ├── Dockerfile            # nginx:1.30-alpine + config + frontend; roda `nginx -t` no build
│   └── nginx.conf            # estáticos, proxy /api/, rate limit 10 req/min/IP (burst 5), 429 em JSON, gzip
├── frontend/
│   ├── index.html            # layout, temas noite/dia (variáveis CSS --c-* + tokens no tailwind.config)
│   └── app.js                # chat, sugestões por arco, tema, Markdown (marked + DOMPurify), cards visuais
└── backend/
    ├── Dockerfile            # multi-stage python:3.13-slim, usuário não-root
    ├── requirements.txt      # versões fixadas
    ├── requirements-dev.txt  # + pytest
    ├── tests/test_backend.py # testes sem rede (Gemini falso + API mockada)
    └── app/
        ├── main.py           # rotas: POST /api/chat, GET /api/health, GET /api/arcs, /api/docs
        ├── agent.py          # loop de Function Calling com o Gemini
        ├── tools.py          # declarações das tools, execução, montagem/validação dos cards
        ├── onepiece_api.py   # cliente httpx da onepieceapi.com + cache
        ├── cache.py          # cache JSON no Redis (degrada sem Redis)
        ├── prompts.py        # ARCS (cronologia) + system_instruction
        ├── schemas.py        # contratos Pydantic
        └── config.py         # variáveis de ambiente
```

## Fluxo de uma pergunta

1. O frontend envia `POST /api/chat` com `message`, `current_arc` (id do `<select>`) e as últimas 6 mensagens de `history` (`message` ≤ 500 caracteres).
   Ao voltar para um arco anterior, o frontend zera `history` (respostas de arcos mais avançados não voltam ao modelo).
2. `main.py` valida o arco (`prompts.resolve_arc`; aceita id `wano` ou rótulo `Wano`) → 422 se inválido.
3. `agent.py` monta `system_instruction` **específico do arco** (cronologia com "USUÁRIO ESTÁ AQUI" e arcos
   futuros marcados como proibidos), converte o histórico (`assistant` → `model`) e prefixa a mensagem com
   `[Arco atual do usuário: X]`.
4. Loop manual (até `MAX_TOOL_ROUNDS`=5): o Gemini pede funções → `ToolRunner` executa em paralelo
   (cada GET passa pelo Redis) → resultados voltam como `function_response`. Na última rodada as tools são
   desligadas (`mode=NONE`) para forçar uma resposta em texto.
5. Para mostrar cards, o modelo chama `show_cards` escolhendo entidades e os valores **seguros no arco**
   (recompensa, fruta, afiliação). O backend monta o card com dados da API e **descarta qualquer recompensa
   que não exista no histórico real** do personagem.
6. Resposta: `{ "reply": "<markdown>", "cards": [...] }`.

### Contrato

```jsonc
// POST /api/chat
{ "message": "Qual a recompensa do Zoro?", "current_arc": "water-7",
  "history": [{ "role": "user", "content": "oi" }, { "role": "assistant", "content": "Ahoy!" }] }

// 200
{ "reply": "Depois de Enies Lobby, **Zoro** vale ฿ 120.000.000 ...",
  "cards": [{ "type": "character", "id": "faf3b05c-...", "name": "Roronoa Zoro", "subtitle": "ロロノア・ゾロ",
              "image_url": "https://...webp", "bounty": 120000000, "devil_fruit": null,
              "affiliation": "Straw Hat Pirates", "note": null, "details": [] }] }
```

`type` ∈ `character | devil_fruit | ship | island | affiliation`. Erros: `422` (validação/arco),
`429` (nginx ou cota Gemini), `502/503` (Gemini fora / sem chave) — sempre `{ "detail": "..." }`.

## API externa: onepieceapi.com (inspecionada via curl)

| Rota | Busca | Observações |
|---|---|---|
| `GET /api/characters?q=&limit=&page=` | `q` (en/jp/romaji, case-insensitive) | cada item embute `bounties[]` = **histórico completo** (inclui valores futuros!) |
| `GET /api/characters/{id}` | — | inclui `bounties[]`; 404 → `{"error":"Not found"}` |
| `GET /api/bounties?sort=high\|low&isActive=` | — | embute `characters{name,status,image_url}` (não documentado) |
| `GET /api/devil-fruits` (114) | **não tem** `q` | `name`, `model`, `type`, `sub_type`; **não diz quem comeu** |
| `GET /api/ships` (50) / `islands` (86) / `affiliations` (221) | **não tem** `q` | `extra_data` com afiliação, 1ª aparição etc. |

- `onepieceapi.com` redireciona (308) para `www.onepieceapi.com` — usamos o `www` direto.
- `limit` máximo = 100. Para recursos sem busca, `_get_all` pagina tudo, cacheia como um bloco
  (`opapi:v1:all:<recurso>`) e filtra localmente (`_norm`: sem acento/pontuação → "Mera-Mera" = "mera mera").
- Personagens principais vêm com `extra_data: null` → fruta/afiliação desses cards vêm do modelo (ciente do arco).

### Tools (Function Calling) → endpoints

| Tool | Endpoint |
|---|---|
| `search_characters(query, limit)` | `/api/characters?q=` |
| `get_character(character_id)` | `/api/characters/{id}` |
| `list_top_bounties(order, active_only, limit)` | `/api/bounties?sort=&isActive=` |
| `search_devil_fruits(query, type)` | `/api/devil-fruits` (filtro local) |
| `search_ships(query)` / `search_islands(query, sea)` / `search_affiliations(query)` | listas completas + filtro local |
| `show_cards(cards[])` | não chama a API; monta os cards da resposta |

## Anti-spoiler (3 camadas + interface)

1. **Prompt**: `build_system_instruction(arc)` lista a cronologia e marca arcos futuros como proibidos; regras
   explícitas para recompensas, `status: Deceased`, rankings e personagens que ainda não apareceram.
2. **Lembrete por mensagem**: `[Arco atual do usuário: X]` antes de cada pergunta.
3. **Cards**: recompensa só aparece se o modelo a declarou válida no arco **e** ela existe na API.
   Se o modelo esquecer `show_cards`, o fallback mostra só entidades citadas no texto, com recompensa oculta
   (exceto no arco mais recente, Egghead). `status` de personagem e de navio nunca vão para o card.
4. **Interface** (`frontend/app.js`): a API só tem retratos **pós-timeskip**, então antes de `fishman-island` o retrato
   de personagem vem borrado, com botão "Revelar"; voltar para um arco anterior zera o histórico enviado ao modelo e
   descarta resposta a caminho; as sugestões (`SUGGESTIONS_BY_ARC`) só citam o que já apareceu até o fim do arco.

Escopo/jailbreak: o system prompt recusa qualquer tema fora de One Piece e trata resultados de ferramentas e
histórico como dados, não instruções. Isso é mitigação por prompt — não é garantia absoluta.

## Comandos

```bash
cp .env.example .env                  # e preencha GEMINI_API_KEY
docker compose config                 # valida o compose
docker compose up -d --build          # sobe tudo → http://localhost
docker compose ps
docker compose logs -f backend        # logs do backend (mostra as tools chamadas)
docker compose restart backend        # após mudar .env
docker compose exec redis redis-cli --scan --pattern 'opapi:*'   # chaves de cache
docker compose exec redis redis-cli FLUSHALL                       # limpa o cache
docker compose down
```

Teste rápido da API:
```bash
curl -s localhost/api/health
curl -s localhost/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"Quem é o Zoro?","current_arc":"alabasta","history":[]}'
```

Testes (sem rede, sem chave):
```bash
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pytest -q
```

Rodar o backend fora do Docker: `REDIS_URL=redis://localhost:6379/0 uvicorn app.main:app --reload`
(dentro de `backend/`); sem Redis ele funciona, só sem cache. O frontend chama `/api/*` no mesmo endereço da
página, então fora do Docker sirva `frontend/` e a API na mesma origem (é o papel do nginx).

## Configuração (.env)

| Variável | Padrão | Uso |
|---|---|---|
| `GEMINI_API_KEY` | — | obrigatória para `/api/chat` (sem ela: 503) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | modelo |
| `GEMINI_THINKING_BUDGET` | `1024` | tokens de raciocínio (0 desliga; -1 dinâmico) |
| `MAX_TOOL_ROUNDS` | `5` | rodadas de function calling por pergunta |
| `HISTORY_MAX_ITEMS` | `6` | mensagens de histórico enviadas ao modelo |
| `CACHE_TTL_SECONDS` | `3600` | TTL do cache Redis |
| `ONEPIECE_API_BASE` | `https://www.onepieceapi.com` | base da API externa |

## Deploy na EC2

1. Ubuntu 24.04, `t3.micro`, disco ≥ 16 GB; Security Group: 80 aberto, 22 só para o seu IP.
2. `git clone <repo> && cd <repo> && sudo ./setup-ec2.sh` (pede a `GEMINI_API_KEY`, ou `sudo GEMINI_API_KEY=... ./setup-ec2.sh`).
3. Atualizar: `git pull && sudo ./setup-ec2.sh` (idempotente).

## Como estender

- **Novo arco**: adicione em `backend/app/prompts.py::ARCS` **e** em `frontend/app.js::ARCS` com o mesmo `id`
  (ordem cronológica importa: o último é tratado como "mais recente"), e as sugestões em
  `frontend/app.js::SUGGESTIONS_BY_ARC` (só o que já apareceu até o fim do arco).
- **Cores/tema**: os tokens ficam em `frontend/index.html` como variáveis `--c-*` (`:root` = noite,
  `[data-theme="light"]` = dia) e o `tailwind.config` aponta para elas. Use `text-ink`/`text-ink-2`/`text-muted`
  em vez de `slate-*` e mantenha texto pequeno com contraste ≥ 4,5:1 (WCAG AA) nos dois temas. A escolha do
  usuário fica no `localStorage` (`logpose.theme`); sem escolha, segue `prefers-color-scheme`.
- **Nova tool**: declare em `tools.py::TOOL_DECLARATIONS`, implemente o handler em `ToolRunner` e registre em
  `_handlers`; se a entidade puder virar card, chame `_remember` e trate o tipo em `build_card`.
- **Novos campos no card**: `schemas.Card` + `build_card` + `frontend/app.js::renderCard`.

## Limitações conhecidas

- Faixas de episódio dos arcos são aproximadas; o filtro depende do conhecimento do modelo sobre a cronologia.
- A API não liga frutas a usuários; essa associação vem do modelo.
- Tailwind via Play CDN é ótimo para protótipo, mas não é recomendado para produção (gera CSS no navegador).
- Sem HTTPS: para produção, coloque um domínio + Certbot ou um ALB/CloudFront na frente.
