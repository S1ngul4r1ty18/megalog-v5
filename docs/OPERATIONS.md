# Operações do MegaLog v5

> Para SRE/DevOps administrando o sistema em produção.

## Índice

1. [Comandos do dia-a-dia](#1-comandos-do-dia-a-dia)
2. [Jobs noturnos (timers)](#2-jobs-noturnos-timers)
3. [Configuração runtime](#3-configuração-runtime)
4. [Backup](#4-backup)
5. [Rotação e retenção](#5-rotação-e-retenção)
6. [Monitoramento](#6-monitoramento)
7. [Importação de DBs legados](#7-importação-de-dbs-legados)
8. [Tarefas raras](#8-tarefas-raras)

---

## 1. Comandos do dia-a-dia

### Status

```bash
# Visão rápida
systemctl is-active megalog-receiver megalog-processor megalog-web nginx

# Status detalhado
systemctl status megalog-receiver
systemctl status megalog-processor
systemctl status megalog-web

# Próximas execuções dos timers
systemctl list-timers megalog-*
```

### Logs em tempo real

```bash
# Receiver: pacotes UDP recebidos, taxa, erros
journalctl -u megalog-receiver -f

# Processor: linhas parseadas, batch inserts, erros
journalctl -u megalog-processor -f

# Web: requests HTTP, erros FastAPI
journalctl -u megalog-web -f

# Tudo junto
journalctl -u 'megalog-*' -f
```

### Logs históricos

```bash
# Últimos N erros
journalctl -u megalog-processor -p err --since '24 hours ago' --no-pager

# Por intervalo
journalctl -u megalog-receiver --since '2026-04-27 14:00' --until '2026-04-27 16:00' --no-pager

# Última inicialização
journalctl -u megalog-web --boot --no-pager
```

### Reiniciar

```bash
# Reiniciar todos (serviços, não timers)
sudo systemctl restart megalog-receiver megalog-processor megalog-web

# Reiniciar só um (ex: depois de mudar config)
sudo systemctl restart megalog-web
```

> ⚠️ Reiniciar `megalog-processor` é seguro — offset persistido em
> `/dados1/state/stream_offsets.json`. Pode perder até `~60s` de contadores
> (`total_hits` no registry), mas zero logs.

### Acesso ao banco de dados

```bash
# DuckDB do dia atual (use depois de parar o processor para evitar lock conflict)
sudo systemctl stop megalog-processor
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
import duckdb
con = duckdb.connect('/dados1/hot/$(date +%F).duckdb', read_only=True)
print(con.execute('SELECT COUNT(*) FROM logs').fetchone())
"
sudo systemctl start megalog-processor

# Parquet cold (sempre seguro, leitura concorrente OK)
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
import duckdb
con = duckdb.connect(':memory:')
print(con.execute(\"SELECT COUNT(*) FROM read_parquet('/dados2/cold/2026-04-23.parquet')\").fetchone())
"

# IP registry (SQLite WAL — sempre seguro)
sudo -u megalog sqlite3 /dados1/state/ip_registry.db \
  "SELECT COUNT(*), SUM(total_hits) FROM ip_registry"

# Operacional (users, audit, alerts)
sudo -u megalog sqlite3 /dados1/state/megalog.db \
  "SELECT id, username, role, last_login FROM users"
```

---

## 2. Jobs noturnos (timers)

| Timer | Quando | O que faz |
|---|---|---|
| `megalog-archive.timer` | diário 02:00 + jitter 5min | Move DuckDB hot > 30 dias para Parquet zstd em cold; atualiza `daily_stats` |
| `megalog-analyze.timer` | diário 02:30 + jitter 5min | Compara volume com baseline; gera alerta + classifica se ratio ≥ 3× |
| `megalog-retention.timer` | seg 03:00 + jitter 15min | Apaga Parquets > 365 dias (config: `delete_after_days`) |

### Disparar manualmente

```bash
# Forçar archive agora (move + atualiza daily_stats)
sudo systemctl start megalog-archive.service
sudo journalctl -u megalog-archive --since '5 minutes ago' --no-pager

# Forçar análise para uma data específica (mesmo se já tem alerta)
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python \
    -m megalog.jobs.analyze --date 2026-04-23 --force

# Forçar retenção (atenção: apaga Parquets antigos!)
sudo systemctl start megalog-retention.service
```

### Pausar timers (manutenção)

```bash
# Parar até manualmente reiniciar
sudo systemctl stop megalog-archive.timer megalog-analyze.timer megalog-retention.timer

# Voltar
sudo systemctl start megalog-archive.timer megalog-analyze.timer megalog-retention.timer
```

### Resultado dos jobs

Logs de execução completa (com sucesso ou falha):
```bash
journalctl -u megalog-archive --since '7 days ago' --no-pager | grep "Started\|Failed\|archive:"
journalctl -u megalog-analyze --since '7 days ago' --no-pager | grep "alerta\|candidatos"
```

---

## 3. Configuração runtime

Toda configuração fica em `/etc/megalog/megalog.env`. Após editar, reiniciar
os serviços afetados.

```bash
sudo nano /etc/megalog/megalog.env
sudo systemctl restart megalog-{receiver,processor,web}
```

### Variáveis principais

```env
# ── Receiver UDP ─────────────────────────────────────────────
MEGALOG_RECEIVER_HOST=0.0.0.0       # bind interface (0.0.0.0 = todas)
MEGALOG_RECEIVER_PORT=514           # porta syslog

# ── Web ──────────────────────────────────────────────────────
MEGALOG_WEB_HOST=0.0.0.0            # ⚠ mude para 127.0.0.1 (nginx é proxy)
MEGALOG_WEB_PORT=5000

# ── Storage ──────────────────────────────────────────────────
MEGALOG_HOT_STORAGE_DIR=/dados1/hot
MEGALOG_COLD_STORAGE_DIR=/dados2/cold
MEGALOG_STREAM_DIR=/dados1/stream
MEGALOG_STATE_DIR=/dados1/state
MEGALOG_IP_REGISTRY_PATH=/dados1/state/ip_registry.db

# ── Retenção ─────────────────────────────────────────────────
MEGALOG_HOT_RETENTION_DAYS=30       # dias antes de mover hot→cold
MEGALOG_DELETE_AFTER_DAYS=365       # 0 = nunca apagar

# ── Processor (tuning) ───────────────────────────────────────
MEGALOG_BATCH_SIZE=5000                          # logs por batch
MEGALOG_BATCH_FLUSH_SECONDS=2.0                  # ou flush por tempo
MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=3.0        # janela de leitura para o web
MEGALOG_IP_CACHE_HOT_TOP_N=50000                 # IPs quentes pré-carregados
MEGALOG_IP_CACHE_MAX=200000                      # LRU em RAM

# ── Sessão ───────────────────────────────────────────────────
MEGALOG_SESSION_TIMEOUT_MINUTES=60
MEGALOG_SECRET_KEY=...              # ⚠ 48+ bytes random — TROCAR após install
```

Lista completa de variáveis em [megalog/config.py](../megalog/config.py).

### Reiniciar só o serviço afetado

| Mudança em | Reiniciar |
|---|---|
| `MEGALOG_RECEIVER_*` | `megalog-receiver` |
| `MEGALOG_BATCH_*`, `MEGALOG_DUCKDB_CLOSE_*` | `megalog-processor` |
| `MEGALOG_IP_CACHE_*` | `megalog-processor` + `megalog-web` |
| `MEGALOG_SECRET_KEY`, `MEGALOG_SESSION_*` | `megalog-web` (invalida sessões) |
| `MEGALOG_*_DIR` | todos (`receiver, processor, web`) |
| `MEGALOG_HOT_RETENTION_DAYS`, `MEGALOG_DELETE_*` | nada — lido em cada execução do timer |

---

## 4. Backup

> ⚠️ **OPS-01 da auditoria**: backup automático ainda não implementado. Configure via cron como abaixo.

### O que precisa ser backupeado

| Arquivo | Por quê |
|---|---|
| `/dados1/state/ip_registry.db` | Mapeamento global ip↔id_id; perdê-lo quebra TODOS os Parquets cold |
| `/dados1/state/megalog.db` | Usuários + audit_log + alertas |
| `/etc/megalog/megalog.env` | SECRET_KEY (perdê-la = invalida todas as sessões) |
| `/dados2/cold/*.parquet` | Histórico imutável (depende da retenção exigida) |

`/dados1/hot/*.duckdb` **não** precisa de backup — vira Parquet em ≤30 dias.

### Backup manual

```bash
sudo mkdir -p /dados2/backups/$(date +%F)
cd /dados2/backups/$(date +%F)

# SQLite online backup (não bloqueia o serviço)
sudo -u megalog sqlite3 /dados1/state/ip_registry.db ".backup ip_registry.db"
sudo -u megalog sqlite3 /dados1/state/megalog.db     ".backup megalog.db"

# Config
sudo cp /etc/megalog/megalog.env .
sudo chmod 640 megalog.env

# Parquets (se backup full)
sudo cp -r /dados2/cold .

# Comprimir tudo num tar
cd /dados2/backups
sudo tar czf megalog-$(date +%F).tar.gz $(date +%F)/
```

### Backup via cron

`/etc/cron.d/megalog-backup`:

```cron
# Backup do estado crítico, todo dia às 04:00
0 4 * * * root /usr/local/src/megalog-v5/scripts/backup.sh
```

Crie o script `scripts/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail
BACKUP_DIR=/dados2/backups/$(date +%F)
mkdir -p "$BACKUP_DIR"
sqlite3 /dados1/state/ip_registry.db ".backup $BACKUP_DIR/ip_registry.db"
sqlite3 /dados1/state/megalog.db     ".backup $BACKUP_DIR/megalog.db"
cp /etc/megalog/megalog.env "$BACKUP_DIR/"
chmod 640 "$BACKUP_DIR/megalog.env"

# Mantém 30 dias de backups
find /dados2/backups -maxdepth 1 -type d -name '20*' -mtime +30 -exec rm -rf {} +
```

### Restore

```bash
# Parar serviços
sudo systemctl stop megalog-{receiver,processor,web}

# Restaurar SQLite
sudo cp /dados2/backups/2026-04-27/ip_registry.db /dados1/state/
sudo cp /dados2/backups/2026-04-27/megalog.db     /dados1/state/
sudo chown megalog:megalog /dados1/state/{ip_registry,megalog}.db

# Restaurar config (cuidado: substitui SECRET_KEY → invalida sessões)
sudo cp /dados2/backups/2026-04-27/megalog.env /etc/megalog/
sudo chmod 640 /etc/megalog/megalog.env

# Reiniciar
sudo systemctl start megalog-{receiver,processor,web}
```

---

## 5. Rotação e retenção

### Hot → Cold (automático)

`megalog-archive.timer` move DuckDBs com idade > `MEGALOG_HOT_RETENTION_DAYS`
(default 30) para Parquet zstd em `/dados2/cold/`. Idempotente — se Parquet já
existe, pula.

### Cold → DELETE (automático)

`megalog-retention.timer` (seg 03:00) apaga Parquets com idade >
`MEGALOG_DELETE_AFTER_DAYS` (default 365). Para nunca apagar: `0`.

> ⚠️ **Compliance:** Marco Civil + Anatel exigem **mínimo 365 dias** de retenção
> de logs CGNAT. Não diminua sem consultar o jurídico.

### Forçar retenção manual

```bash
# Move tudo > 30 dias para cold AGORA (não só os com idade exata)
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
from megalog.config import get_settings
from megalog.storage.partitions import move_old_to_cold
s = get_settings()
moved = move_old_to_cold(s.hot_storage_dir, s.cold_storage_dir, retention_days=30)
print(f'Movidos: {moved}')
"

# Apagar Parquets > 365 dias
sudo systemctl start megalog-retention.service
```

### Diretório `.processed` em stream

Receiver+processor mantêm os `.raw` da última hora em `/dados1/stream/.processed/`
por 24h (config: `stream_processed_keep_hours`). Após isso, são apagados
automaticamente. Útil para replay manual em caso de bug do parser.

---

## 6. Monitoramento

### Endpoint healthz

```bash
curl -s http://127.0.0.1/healthz
# {"status":"ok"}  ← API web está respondendo

# Use em monitoria externa: blackbox_exporter, Uptime Kuma, etc.
```

> ⚠️ **OPS-02 da auditoria**: o `/healthz` atual retorna sempre 200 quando o
> processo está vivo, mas não checa: disco cheio, processor parado, lock
> persistente. Considere adicionar essas verificações.

### Métricas via UI

Dashboard mostra em tempo real:
- CPU/RAM/disco hot/disco cold
- Buffer .raw (deve crescer + ter "último pacote há Xs")
- Logs hoje (cresce conforme ingestão)
- Status dos 3 serviços (verde = active)

### Métricas via API

```bash
curl -s -b /tmp/cookies.txt http://127.0.0.1/api/system-status | jq
```

Retorna JSON com `cpu`, `ram`, `swap`, `disks`, `services`, `ingest`, `now`.

### Logs grandes

```bash
# Tamanho do journal
sudo journalctl --disk-usage

# Limpar journal antigo
sudo journalctl --vacuum-time=30d
sudo journalctl --vacuum-size=500M
```

### Alertas externos (sugestão)

`/etc/cron.d/megalog-watchdog`:

```cron
# A cada 5 min: se /healthz falha ou disk cheio, alerta
*/5 * * * * root /usr/local/src/megalog-v5/scripts/watchdog.sh
```

Script `scripts/watchdog.sh`:

```bash
#!/bin/bash
HEALTHZ=$(curl -fs http://127.0.0.1/healthz || echo FAIL)
DISK_HOT=$(df --output=pcent /dados1 | tail -1 | tr -d ' %')

if [ "$HEALTHZ" = "FAIL" ] || [ "$DISK_HOT" -gt 90 ]; then
  echo "MegaLog ALERT: healthz=$HEALTHZ disk_hot=$DISK_HOT%" \
    | mail -s "MegaLog $(hostname)" admin@example.com
fi
```

---

## 7. Importação de DBs legados

Para trazer dados históricos do servidor v4:

```bash
# 1. Copiar os .db ou .db.gz para um diretório acessível
sudo mkdir -p /tmp/legacy
sudo scp old-server:/dados2/cold-storage/*.db.gz /tmp/legacy/

# 2. Importar (idempotente — pula se Parquet já existe)
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python \
    -m megalog.tools.import_legacy /tmp/legacy/

# 3. Verificar no Dashboard ou via API
curl -s -b /tmp/c.txt http://127.0.0.1/api/analytics/daily | jq
```

A tool detecta automaticamente 3 schemas (v2, B, A). Output: Parquet zstd em
`/dados2/cold/`. Throughput medido: **~700k linhas/s**.

Detalhes em [docs/CHANGELOG-v4-to-v5.md](CHANGELOG-v4-to-v5.md#migração-de-dados).

---

## 8. Tarefas raras

### Trocar a SECRET_KEY (rotação anual recomendada)

```bash
# 1. Gera nova chave
NEW=$(head -c 48 /dev/urandom | base64 | tr -d '\n')

# 2. Substitui no env
sudo sed -i "s/^MEGALOG_SECRET_KEY=.*/MEGALOG_SECRET_KEY=$NEW/" /etc/megalog/megalog.env

# 3. Reinicia o web (invalida TODAS as sessões)
sudo systemctl restart megalog-web
```

Todos os usuários precisarão fazer login de novo. Não afeta dados.

### Resetar senha de um usuário (esqueceu, sem admin disponível)

```bash
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -c "
from megalog.config import get_settings
from megalog.storage.operational import OperationalStore
from megalog.api.auth import hash_password
s = get_settings()
ops = OperationalStore(s.state_dir / 'megalog.db')
u = ops.get_user_by_username('admin')
ops.set_user_password(u['id'], hash_password('nova-senha-123'))
print('Senha redefinida.')
"
```

### Resetar dashboard de stats (recalcular daily_stats)

```bash
# Apaga todos os daily_stats (mantém alerts)
sudo -u megalog sqlite3 /dados1/state/megalog.db "DELETE FROM daily_stats"

# Recalcula via job archive (que reescaneia todas as partições)
sudo systemctl start megalog-archive.service
```

### Compactar journal do systemd

```bash
sudo journalctl --vacuum-size=200M
```

### Inspecionar registry (top 100 IPs)

```bash
sudo -u megalog sqlite3 /dados1/state/ip_registry.db <<EOF
.headers on
.mode column
SELECT printf('%d.%d.%d.%d', (ip>>24)&0xFF, (ip>>16)&0xFF, (ip>>8)&0xFF, ip&0xFF) AS ip,
       total_hits, datetime(first_seen,'unixepoch') AS first, datetime(last_seen,'unixepoch') AS last
FROM ip_registry ORDER BY total_hits DESC LIMIT 100;
EOF
```

---

## Próximo

- Problemas? → [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- API completa? → [docs/API.md](API.md)
- Decisões de design? → [docs/ARCHITECTURE.md](ARCHITECTURE.md)
