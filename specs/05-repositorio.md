# SPEC 05 — Padrão de Repositório & Colaboração

**Status:** vigente

## 1. Árvore oficial

```
.
├── README.md                 # visão geral, uso do endpoint, requisitos da avaliação
├── CLAUDE.md                 # guia técnico para quem continua o projeto
├── specs/                    # estas especificações (fonte da verdade)
├── docker-compose.yml        # nginx + backend + redis
├── setup-ec2.sh              # swap + Docker + .env + deploy
├── .env.example              # modelo; o .env real NUNCA vai para o Git
├── .gitignore  .dockerignore
├── nginx/
│   ├── Dockerfile            # nginx -t no build
│   └── nginx.conf
├── frontend/
│   ├── index.html
│   └── app.js
└── backend/
    ├── Dockerfile            # multi-stage, non-root
    ├── requirements.txt      requirements-dev.txt   pytest.ini
    ├── tests/test_backend.py # sem rede, sem chave
    └── app/
        ├── main.py  agent.py  tools.py  onepiece_api.py
        └── cache.py  prompts.py  schemas.py  config.py
```

Regra: mudou um limite, rota ou contrato → atualize a spec correspondente **no mesmo PR**.

## 2. Branches

- `main`: estável e protegida (sem push direto; merge via PR com 1 aprovação; o que está na `main` sobe na EC2).
- Branches curtas a partir da `main`, uma por tarefa, vida ≤ 3 dias:
  `feat/<tema>`, `fix/<tema>`, `test/<tema>`, `docs/<tema>` (ex.: `test/cache-edge-cases`, `feat/chips-por-arco`).
- Antes do PR: `cd backend && pytest -q` e `docker compose build` passando.
- Commits curtos no imperativo: `fix: limita histórico a 6 mensagens`.

## 3. Handoff

### Membro 2 — testes de borda (API e cache) · branch `test/cache-edge-cases`
- [ ] Teste: Redis fora do ar → `/api/chat` continua respondendo (simular com `docker compose stop redis`).
- [ ] Teste: onepieceapi.com com timeout/500 → tool devolve erro amigável (mock `httpx.MockTransport`).
- [ ] Teste: `GET /api/characters/{id}` com UUID inexistente → 404 cacheado, sem nova requisição.
- [ ] Teste: TTL das chaves = 3600 (`redis-cli ttl`).
- [ ] Teste: `message` com 500 caracteres passa; com 501 → 422; `history` com 21 itens → 422.
- [ ] Teste: busca local com acento/hífen (`"Méra-Mera"` encontra Flame-Flame Fruit).
- [ ] Teste: `show_cards` com id inventado é ignorado; recompensa fora do histórico vira `null`.
- [ ] Script de carga leve (10 req em sequência) confirmando 429 do nginx após 6.

### Membro 3 — polimento de UI · branch `feat/chips-por-arco`
- [ ] Chips de perguntas sugeridas **por arco** (mapa `arcId → perguntas` em `frontend/app.js`; trocar ao mudar o `<select>`).
- [ ] Contador de caracteres `x/500` no campo de texto.
- [ ] Revisar mobile em 360 px e 414 px: header, chips roláveis, cards em 1 coluna, teclado não cobrindo o input.
- [ ] Estado vazio/erro para imagem de card (já há fallback com iniciais — validar visual).
- [ ] Mensagem clara quando o nginx retornar 429 (usar `detail` do JSON).
- [ ] Checar contraste (WCAG AA) do dourado `#F4A261` e teal `#2A9D8F` sobre `#1C2541`.
