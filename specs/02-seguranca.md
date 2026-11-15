# SPEC 02 — Segurança Enxuta & Hardening

**Status:** vigente · **Implementação:** `docker-compose.yml`, `backend/Dockerfile`, `nginx/nginx.conf`, `setup-ec2.sh`

## 1. Isolamento de rede

| Serviço | Porta publicada no host | Acesso |
|---|---|---|
| `nginx` | **80** | público |
| `backend` | nenhuma (`expose: 8000`) | só pela rede `logpose-net` |
| `redis` | nenhuma | só pela rede `logpose-net` |

- Rede única `logpose-net` com `driver: bridge`.
- Verificação: `curl localhost:8000` e `nc -z localhost 6379` **devem falhar** no host.

## 2. Containers

- Backend roda como usuário **non-root** `app` (UID 10001), definido no `Dockerfile` (`USER app`).
- Imagem final multi-stage `python:3.13-slim`, sem pip cache nem ferramentas de build.
- `.dockerignore` impede `.env`, `tests/` e `.git` de entrarem nas imagens.
- Verificação: `docker compose exec backend id -u` → `10001`.

## 3. Nginx

Headers em todas as respostas (`always`):

| Header | Valor |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

Mais: `server_tokens off` (sem versão do nginx), `nginx -t` executado no build da imagem.

## 4. Frontend

- Markdown do bot passa por **DOMPurify**; `<img>`, `<iframe>`, `<form>`, `<input>`, `<style>` são removidos.
- Cards são montados com `textContent`/DOM API (nunca `innerHTML` com dados da API).

## 5. Segredos

- `GEMINI_API_KEY` só no `.env` (no `.gitignore`); o repositório tem apenas `.env.example`.
- Injeção em runtime via `env_file` no serviço `backend` — nunca em `Dockerfile`/`ARG`/imagem.
- `setup-ec2.sh` grava o `.env` com `chmod 600`.
- Chave vazada? Revogue em https://aistudio.google.com/apikey, gere outra e rode `docker compose up -d`.

## 6. Checklist AWS (Security Group)

- [ ] Inbound **80/TCP** de `0.0.0.0/0`.
- [ ] Inbound **22/TCP** apenas do IP do grupo (`<seu-ip>/32`) **ou** sem regra 22 e acesso via
      **EC2 Instance Connect** (libere só o prefix list do serviço na região).
- [ ] Nenhuma outra porta aberta (nada de 8000/6379).
- [ ] Par de chaves `.pem` fora do repositório (`*.pem` no `.gitignore`).
- [ ] IMDSv2 obrigatório na instância (padrão nas AMIs Ubuntu 24.04 recentes).

## Fora do escopo (conscientemente)

HTTPS/TLS, WAF e autenticação de usuários — aceitável para demonstração acadêmica de curta duração.
