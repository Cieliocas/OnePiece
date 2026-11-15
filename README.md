# 🧭 Log Pose AI — Chatbot Guia de One Piece

Chatbot multiusuário que responde perguntas sobre o universo de **One Piece** usando o LLM **Google Gemini 2.5 Flash**,
com dados reais da [onepieceapi.com](https://onepieceapi.com) via *Function Calling*, cache em **Redis** e um
**filtro anti-spoiler** baseado no arco do anime em que o usuário está.

> 📚 Trabalho da **2ª Avaliação (1º Protótipo)** da disciplina **Tópicos em Computação Aplicada** (2026.2),
> ministrada pelo **Prof. Armando Soares Sousa**. Apresentação presencial em 16/11/2026.
> Equipe: ver [Equipe](#equipe).

**🌐 Endpoint público:** `http://<IP-PUBLICO-DA-EC2>/` · **API:** `http://<IP-PUBLICO-DA-EC2>/api/chat` ·
**Swagger:** `http://<IP-PUBLICO-DA-EC2>/api/docs`

---

## Sumário

- [Funcionalidades](#funcionalidades)
- [Requisitos da avaliação × implementação](#requisitos-da-avaliação--implementação)
- [Arquitetura](#arquitetura)
- [Justificativa técnica](#justificativa-técnica)
- [Como usar o endpoint](#como-usar-o-endpoint)
- [Rodando localmente](#rodando-localmente)
- [Deploy na AWS EC2](#deploy-na-aws-ec2)
- [Testes](#testes)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Equipe](#equipe)

---

## Funcionalidades

- **Chat multiusuário** via navegador ou HTTP: várias pessoas conversam ao mesmo tempo, cada uma com seu histórico.
- **Respostas com LLM** (Gemini 2.5 Flash) em português, com Markdown.
- **Dados reais** de personagens, recompensas, Akuma no Mi, navios, ilhas e tripulações (onepieceapi.com), consultados
  pelo próprio modelo via *Function Calling*.
- **Cards visuais**: retrato, nome, recompensa em Berries (฿), fruta e afiliação.
- **Filtro anti-spoiler**: o usuário escolhe onde está no anime (East Blue → Egghead) e o bot nunca revela eventos,
  mortes ou recompensas posteriores.
- **Escopo fechado**: recusa educadamente assuntos fora de One Piece e tentativas de *prompt injection*.
- **Cache Redis (TTL 1h)**: a segunda consulta à mesma entidade não sai para a internet (~1 s → ~2 ms).
- **Proteções**: rate limit por IP, validação de entrada, containers isolados, usuário non-root.

## Requisitos da avaliação × implementação

| Requisito | Como foi atendido |
|---|---|
| **Conexão multiusuário / conexões concorrentes** | Backend **assíncrono** (FastAPI + Uvicorn/asyncio). Chamadas ao Gemini (`client.aio`), à API externa (`httpx.AsyncClient`) e ao Redis (`redis.asyncio`) não bloqueiam: enquanto um usuário espera o LLM, outros são atendidos. |
| **Histórico isolado por usuário** | Cada cliente (aba do navegador) mantém o próprio histórico e o envia a cada pergunta (últimas 6 mensagens). O servidor não guarda estado compartilhado entre requisições, então não há como o histórico de um usuário vazar para outro. |
| **Resposta direcionada ao usuário correto** | Cada pergunta é uma requisição HTTP independente e a resposta volta na mesma conexão. O estado de cada pergunta (ex.: entidades consultadas para os cards) vive em um objeto criado por requisição (`ToolRunner`), nunca global. |
| **Responder perguntas com um LLM** | Google Gemini 2.5 Flash via SDK oficial `google-genai`, com *system instruction* e *Function Calling*. |
| **Qualidade e coerência das respostas** | Dados factuais vêm da API (não "alucinados"); recompensas exibidas nos cards são validadas contra o histórico real da API; *prompt* com persona, escopo e regras anti-spoiler. |
| **Implantação na AWS EC2 (opcional)** | `setup-ec2.sh` prepara uma `t3.micro` Ubuntu 24.04 (swap, Docker, deploy) em ~5 min. |
| **Acesso público** | Nginx publica a porta 80; interface web em `/` e API em `/api/*`. |
| **Documentação do endpoint** | Seção [Como usar o endpoint](#como-usar-o-endpoint) + Swagger em `/api/docs`. |
| **Código organizado no GitHub** | Backend em módulos pequenos, testes automatizados e especificações em [`specs/`](specs/). |

## Arquitetura

```
                          Internet
                             │ HTTP :80 (única porta pública)
                 ┌───────────▼────────────┐
  Usuários ────▶ │ Nginx                  │  /       → frontend (HTML + Tailwind + JS)
 (navegadores)   │ rate limit · headers   │  /api/*  → backend:8000
                 └───────────┬────────────┘
         rede Docker interna │ (bridge)
                 ┌───────────▼────────────┐         HTTPS
                 │ Backend FastAPI        │ ──────────────▶ Google Gemini API
                 │ async · non-root       │ ──────────────▶ onepieceapi.com (só em cache miss)
                 └───────────┬────────────┘
                 ┌───────────▼────────────┐
                 │ Redis (cache, TTL 1h)  │
                 └────────────────────────┘
```

**Fluxo de uma pergunta**

1. O navegador envia `POST /api/chat` com a mensagem, o arco atual e o histórico daquela aba.
2. O backend monta um *system instruction* específico para o arco (cronologia + regras anti-spoiler).
3. O Gemini decide se precisa de dados e pede ferramentas (ex.: `search_characters("Zoro")`).
4. O backend executa a ferramenta consultando o Redis e, se não houver cache, a onepieceapi.com.
5. O Gemini recebe os dados, escolhe o que mostrar em cards (`show_cards`) e escreve a resposta.
6. O backend devolve `{ "reply": "...", "cards": [...] }` para o mesmo cliente.

## Justificativa técnica

| Escolha | Por quê |
|---|---|
| **Gemini 2.5 Flash** | Baixa latência e custo por token, cota gratuita no Google AI Studio (ideal para projeto acadêmico), *Function Calling* nativo para buscar dados reais e controle do orçamento de raciocínio (`thinking_budget`) para equilibrar qualidade e custo. |
| **SDK oficial `google-genai`** | Biblioteca atual e mantida pelo Google, com cliente **assíncrono** (`client.aio`), tipagem completa e suporte direto a *tools*. Dispensa camadas extras como LangChain, deixando o fluxo explícito e fácil de explicar. |
| **Python + FastAPI + Uvicorn** | `async/await` nativo para lidar com muitas conexões concorrentes num único processo, validação automática com Pydantic e documentação Swagger gerada sozinha. |
| **Function Calling + onepieceapi.com** | Em vez de confiar só na memória do modelo, ele consulta dados reais (nomes, imagens, recompensas), o que reduz alucinações e permite os cards visuais. |
| **Redis** | Cache das respostas da API externa (TTL 1h): menos latência, menos chamadas externas e resiliência se a API oscilar. Se o Redis cair, o app continua funcionando sem cache. |
| **Nginx** | Ponto único de entrada: serve o frontend, faz *proxy* da API, aplica rate limit (10 req/min/IP) e headers de segurança. |
| **Docker Compose** | Os 3 serviços sobem com um comando, iguais no notebook e na EC2, com limites de memória para caber em 1 GB de RAM. |
| **Frontend sem build** | HTML + Tailwind (CDN) + JavaScript puro: leve, sem Node.js no servidor. |

## Como usar o endpoint

### Pela interface web

1. Acesse `http://<IP-PUBLICO-DA-EC2>/`.
2. Escolha no topo **"Onde você está no anime?"** (obrigatório).
3. Digite uma pergunta ou clique em uma das sugestões. Cada aba do navegador é um usuário independente.

### Pela API

`POST /api/chat`

```bash
curl -s http://<IP-PUBLICO-DA-EC2>/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
        "message": "Quem é o Zoro e qual a recompensa dele?",
        "current_arc": "water-7",
        "history": []
      }'
```

| Campo | Tipo | Regra |
|---|---|---|
| `message` | string | obrigatório, 1 a 500 caracteres |
| `current_arc` | string | id do arco (lista em `GET /api/arcs`): `east-blue`, `alabasta`, `skypiea`, `water-7`, `thriller-bark`, `marineford`, `fishman-island`, `dressrosa`, `whole-cake`, `wano`, `egghead` |
| `history` | array | opcional; `[{ "role": "user" \| "assistant", "content": "..." }]` — só as 6 últimas mensagens são usadas |

Resposta `200`:

```json
{
  "reply": "Depois de Enies Lobby, **Zoro** vale ฿ 120.000.000! ...",
  "cards": [
    {
      "type": "character",
      "id": "faf3b05c-f6e6-402f-85bc-f1a707be8e8d",
      "name": "Roronoa Zoro",
      "subtitle": "ロロノア・ゾロ",
      "image_url": "https://.../roronoa-zoro.webp",
      "bounty": 120000000,
      "devil_fruit": null,
      "affiliation": "Straw Hat Pirates",
      "note": null,
      "details": []
    }
  ]
}
```

Para continuar a conversa, envie as mensagens anteriores em `history`:

```bash
curl -s http://<IP-PUBLICO-DA-EC2>/api/chat -H 'Content-Type: application/json' -d '{
  "message": "E qual era a recompensa dele antes disso?",
  "current_arc": "water-7",
  "history": [
    {"role": "user", "content": "Quem é o Zoro e qual a recompensa dele?"},
    {"role": "assistant", "content": "Depois de Enies Lobby, Zoro vale ฿ 120.000.000!"}
  ]
}'
```

### Outras rotas

| Rota | Descrição |
|---|---|
| `GET /api/health` | status do serviço: `{"status":"ok","redis":true,"gemini_configured":true,...}` |
| `GET /api/arcs` | arcos aceitos em `current_arc` |
| `GET /api/docs` | documentação interativa (Swagger) |

### Erros e limites

| HTTP | Quando |
|---|---|
| `422` | mensagem vazia ou com mais de 500 caracteres, arco inválido, histórico malformado |
| `413` | corpo da requisição maior que 16 KB |
| `429` | mais de 10 requisições/min do mesmo IP (rajada de 5) ou cota do Gemini esgotada |
| `502` / `503` | Gemini indisponível ou chave não configurada |

Todos os erros têm o formato `{"detail": "mensagem"}`.

## Rodando localmente

Pré-requisitos: [Docker](https://docs.docker.com/get-docker/) com Compose e uma chave do Gemini
([Google AI Studio](https://aistudio.google.com/apikey), gratuita).

```bash
git clone https://github.com/Cieliocas/OnePiece.git
cd OnePiece
cp .env.example .env        # edite e coloque sua GEMINI_API_KEY
docker compose up -d --build
```

Acesse **http://localhost**. Comandos úteis:

```bash
docker compose ps                  # status dos containers
docker compose logs -f backend     # logs (ferramentas chamadas, cache HIT/MISS)
docker compose down                # desliga tudo
```

## Deploy na AWS EC2

1. Crie uma instância **Ubuntu Server 24.04 LTS**, tipo `t3.micro` (Free Tier), disco de 8–20 GB.
2. No *Security Group*, libere a porta **80** para todos e a **22** só para o seu IP (ou use EC2 Instance Connect).
3. Conecte na instância e rode:

```bash
git clone https://github.com/Cieliocas/OnePiece.git
cd OnePiece
sudo ./setup-ec2.sh
```

O script cria 2 GB de swap, instala o Docker, pede a `GEMINI_API_KEY`, sobe os containers e imprime a URL pública.
Os containers voltam sozinhos quando a instância é religada. Detalhes de custo e segurança em
[`specs/04-economia-nuvem.md`](specs/04-economia-nuvem.md) e [`specs/02-seguranca.md`](specs/02-seguranca.md).

Verificação rápida:

```bash
curl -s http://<IP-PUBLICO-DA-EC2>/api/health
```

## Testes

Testes automatizados sem rede e sem chave de API (Gemini e onepieceapi.com simulados):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Cobrem: validação de entrada, limite de histórico, loop de *Function Calling*, montagem e validação dos cards
(anti-spoiler), busca local e respostas de erro.

## Estrutura do repositório

```
.
├── README.md                 # este arquivo
├── CLAUDE.md                 # guia técnico detalhado para desenvolvedores
├── specs/                    # especificações (arquitetura, segurança, limites, custos, equipe, apresentação)
├── docker-compose.yml        # nginx + backend + redis
├── setup-ec2.sh              # deploy automatizado na EC2
├── .env.example              # modelo de configuração (GEMINI_API_KEY)
├── nginx/                    # proxy reverso (Dockerfile + nginx.conf)
├── frontend/                 # interface web (index.html + app.js)
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── tests/                # testes automatizados
    └── app/
        ├── main.py           # rotas HTTP
        ├── agent.py          # loop de Function Calling com o Gemini
        ├── tools.py          # ferramentas e cards
        ├── onepiece_api.py   # cliente da onepieceapi.com
        ├── cache.py          # cache Redis
        ├── prompts.py        # arcos + system instruction
        ├── schemas.py        # contratos de entrada/saída
        └── config.py         # variáveis de ambiente
```

## Equipe

| | Integrante |
|---|---|
| 1 | João Pedro Monteiro da Silva Barros |
| 2 | Lucas Herlon dos Santos Moreira Cunha |
| 3 | Otávio da Conceição França |
| 4 | Francielio Evangelista dos Santos Castro |

| Disciplina | Professor | Avaliação |
|---|---|---|
| Tópicos em Computação Aplicada (2026.2) | Armando Soares Sousa | 2ª Avaliação — 1º Protótipo |

## Créditos

- *One Piece* é obra de **Eiichiro Oda** (Shueisha / Toei Animation). Projeto educacional, sem fins lucrativos.
- Dados e imagens: [One Piece API](https://onepieceapi.com) (projeto open source, licença MIT).
