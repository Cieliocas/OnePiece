#!/usr/bin/env bash
# =============================================================================
# Log Pose AI — preparação de uma EC2 Ubuntu 24.04 LTS (t3.micro, 1 GB RAM)
#
#   1. Cria swap de 2 GB (o build das imagens não cabe em 1 GB de RAM)
#   2. Instala Docker Engine + plugin Docker Compose (repositório oficial da Docker)
#   3. Configura o .env (GEMINI_API_KEY) e sobe a stack com `docker compose up -d --build`
#
# Uso (dentro da pasta do projeto, já clonada na instância):
#   sudo ./setup-ec2.sh
#   sudo GEMINI_API_KEY=xxxx ./setup-ec2.sh     # modo não interativo
#
# Idempotente: pode rodar de novo para atualizar/rebuildar.
# =============================================================================
set -euo pipefail

SWAP_FILE=/swapfile
SWAP_SIZE_GB=2
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-ubuntu}"

log()  { printf '\n\033[1;33m⚓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;31m!  %s\033[0m\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
  warn "Rode com sudo:  sudo ./setup-ec2.sh"
  exit 1
fi
if [[ ! -f "$PROJECT_DIR/docker-compose.yml" ]]; then
  warn "docker-compose.yml não encontrado em $PROJECT_DIR — rode o script de dentro do repositório."
  exit 1
fi

# --------------------------------------------------------------------------- 1. Swap
log "1/4 Swap de ${SWAP_SIZE_GB} GB"
if swapon --show=NAME --noheadings | grep -qx "$SWAP_FILE"; then
  echo "Swap já ativo em $SWAP_FILE"
else
  if [[ ! -f $SWAP_FILE ]]; then
    fallocate -l "${SWAP_SIZE_GB}G" "$SWAP_FILE" 2>/dev/null \
      || dd if=/dev/zero of="$SWAP_FILE" bs=1M count=$((SWAP_SIZE_GB * 1024)) status=progress
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE"
  fi
  swapon "$SWAP_FILE"
fi
grep -q "^$SWAP_FILE " /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
# Usa swap só quando necessário (menos I/O no EBS)
cat > /etc/sysctl.d/99-logpose.conf <<'EOF'
vm.swappiness=10
vm.vfs_cache_pressure=50
EOF
sysctl --quiet --system
free -h

# --------------------------------------------------------------------------- 2. Docker
log "2/4 Docker Engine + Compose plugin"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "Docker já instalado: $(docker --version)"
else
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl git
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  CODENAME="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# Rotação de logs dos containers (o disco padrão da EC2 é pequeno)
if [[ ! -f /etc/docker/daemon.json ]]; then
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
  systemctl restart docker 2>/dev/null || true
elif ! grep -q '"max-size"' /etc/docker/daemon.json; then
  warn "/etc/docker/daemon.json já existe sem rotação de logs; o docker-compose.yml ainda limita por serviço (SPEC 04)."
fi
systemctl enable --now docker
usermod -aG docker "$TARGET_USER" || true
docker compose version

# --------------------------------------------------------------------------- 3. .env
log "3/4 Configuração (.env)"
cd "$PROJECT_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
current_key="$(grep -E '^GEMINI_API_KEY=' .env | cut -d= -f2- || true)"
if [[ -z "$current_key" || "$current_key" == "sua_chave_aqui" ]]; then
  key="${GEMINI_API_KEY:-}"
  if [[ -z "$key" && -t 0 ]]; then
    read -rsp "Cole sua GEMINI_API_KEY (Enter para pular): " key; echo
  fi
  if [[ -n "$key" ]]; then
    tmp="$(mktemp)"
    grep -v '^GEMINI_API_KEY=' .env > "$tmp" || true
    printf 'GEMINI_API_KEY=%s\n' "$key" | cat - "$tmp" > .env
    rm -f "$tmp"
    echo "GEMINI_API_KEY gravada em .env"
  else
    warn "GEMINI_API_KEY não definida: a stack sobe, mas /api/chat responde 503 até você editar .env e rodar 'docker compose up -d'."
  fi
fi
chmod 600 .env
chown "$TARGET_USER":"$TARGET_USER" .env 2>/dev/null || true

# --------------------------------------------------------------------------- 4. Sobe a stack
log "4/4 Build e start (pode levar alguns minutos numa t3.micro)"
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null

printf 'Aguardando /api/health'
for _ in $(seq 1 45); do
  if curl -fsS http://localhost/api/health >/dev/null 2>&1; then ok=1; break; fi
  printf '.'; sleep 2
done
echo
docker compose ps

if [[ "${ok:-0}" != 1 ]]; then
  warn "A API não respondeu a tempo. Veja os logs:  docker compose logs --tail=100 backend nginx"
  exit 1
fi
curl -fsS http://localhost/api/health; echo

# IP público via IMDSv2
TOKEN="$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
PUBLIC_IP="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"

log "Pronto! Log Pose AI no ar: http://${PUBLIC_IP:-<IP-PUBLICO-DA-EC2>}/"
cat <<'EOF'
Checklist (specs/02-seguranca.md e specs/04-economia-nuvem.md):
  - Security Group: 80 aberta; 22 só para o IP do grupo (ou use EC2 Instance Connect); nada de 8000/6379.
  - t3.micro: troque os créditos de CPU para "standard" (evita cobrança do modo Unlimited).
  - Após a aula: pare a instância (aws ec2 stop-instances --instance-ids <id>) — o app volta sozinho no próximo start.
  - Para usar docker sem sudo, saia e entre de novo na sessão SSH (grupo 'docker').
EOF
