# SPEC 04 — Economia Máxima na AWS

**Status:** vigente · **Implementação:** `docker-compose.yml`, `setup-ec2.sh`

## 1. Dimensionamento

| Recurso | Escolha | Motivo |
|---|---|---|
| Instância | `t3.micro` (ou `t2.micro`), 2 vCPU burst, 1 GB RAM | elegível ao Free Tier |
| SO | Ubuntu Server 24.04 LTS (x86_64) | AMI gratuita |
| Disco | EBS gp3 8–20 GB | Free Tier cobre até 30 GB |
| IP | IP público automático (sem Elastic IP) | EIP parado é cobrado |

**Proibido** (custos fora do Free Tier): ALB/NLB, NAT Gateway, RDS/ElastiCache, CloudWatch pago
(métricas detalhadas, alarmes extras, Logs com retenção), Route 53, CloudFront.

Atenção a custos que pegam iniciantes:
- **t3 "Unlimited"**: o padrão das t3 cobra créditos de CPU extras sob carga contínua. Mude para `standard`:
  `aws ec2 modify-instance-credit-specification --instance-credit-specification "InstanceId=<id>,CpuCredits=standard"`
- **IPv4 público** é tarifado por hora desde 2024 (o Free Tier cobre uma cota mensal). Desligar a instância
  libera o IP e para a cobrança. Confira em *Billing → Free Tier* — as regras mudam com o tipo/idade da conta.
- Crie um **AWS Budget** de US$ 1 com alerta por e-mail (gratuito).

## 2. Memória (1 GB RAM)

**Swap de 2 GB obrigatório** (`setup-ec2.sh`): `/swapfile`, persistido no `/etc/fstab`, `vm.swappiness=10`.
Sem ele, o `pip install` do build e o pico do Python podem causar **OOM Kill**.

| Container | `mem_limit` | Uso típico |
|---|---|---|
| `nginx` | 64m | ~5 MB |
| `redis` | 128m | `maxmemory 64mb` + overhead |
| `backend` | 512m | ~100–150 MB (1 worker uvicorn) |
| **Total** | **704m** | sobra RAM para o SO; swap absorve picos |

## 3. Disco

Rotação de logs em **duas camadas** (`max-size: "10m"`, `max-file: "3"` → ≤ 30 MB por container):
1. `logging:` em cada serviço do `docker-compose.yml`;
2. `/etc/docker/daemon.json` criado pelo `setup-ec2.sh` (vale para qualquer container).

Mais: Redis sem persistência (`--save ""`, `--appendonly no`) e `docker image prune -f` após cada build.
Limpeza manual se precisar: `docker system prune -af` (apaga imagens não usadas — o próximo build demora mais).

## 4. Estratégia Ligar/Desligar

Os containers têm `restart: unless-stopped` e o Docker sobe no boot → **ligar a instância já sobe o app**.

```bash
# Antes da aula (do seu computador, com AWS CLI configurado)
aws ec2 start-instances --instance-ids <id>
aws ec2 wait instance-running --instance-ids <id>
aws ec2 describe-instances --instance-ids <id> --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
curl -s http://<ip>/api/health

# Depois da aula: pausar (EBS continua cobrado, dentro do Free Tier)
aws ec2 stop-instances --instance-ids <id>

# Fim do semestre: encerrar de vez (apaga a instância e o disco)
aws ec2 terminate-instances --instance-ids <id>
```

Pelo console: *EC2 → Instâncias → Estado da instância → Iniciar / Interromper / Encerrar*.
O IP público **muda** a cada start — pegue o novo antes de passar o link para a turma.
