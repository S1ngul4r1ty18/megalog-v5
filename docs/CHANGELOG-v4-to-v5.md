# MegaLog v4 → v5 — Changelog e migração

> Para quem opera o **v4** hoje e vai migrar para **v5**.

## TL;DR

A v5 é uma **reescrita completa**, mantendo paridade funcional total com a v4
(mesmo escopo CGNAT, mesmo classificador 9-categorias, mesmo fluxo do operador).
Por trás, mudou tudo: storage colunar, dicionário global de IPs, FastAPI + SPA,
systemd timers.

**Ganhos medidos contra os dados reais do servidor v4:**
- **Compactação 5×** (450 MB SQLite+gzip → 90 MB Parquet zstd, em 4 dias × 10.4M linhas)
- **Busca pontual 5-7×** mais rápida (60-96 ms → 11-15 ms mediana)
- **Agregação top-IPs do mês 15-20×** mais rápida
- **Bytes/log no disco caíram 16.7×** (151 → 9 bytes)

A migração de dados antigos é **idempotente** e roda a ~700k linhas/s.

---

## Índice

1. [Resumo das diferenças](#1-resumo-das-diferenças)
2. [Breaking changes](#2-breaking-changes)
3. [Migração de dados](#3-migração-de-dados)
4. [Migração de configuração](#4-migração-de-configuração)
5. [Migração de usuários](#5-migração-de-usuários)
6. [Convivência v4 + v5 durante transição](#6-convivência-v4--v5-durante-transição)
7. [Rollback (se algo der errado)](#7-rollback)

---

## 1. Resumo das diferenças

### Stack

| Aspecto | v4 (Flask + SQLite) | v5 (FastAPI + DuckDB + Vue) |
|---|---|---|
| Linguagem backend | Python 3.9+ | Python **3.11+** |
| Web framework | Flask 2 + Jinja2 SSR | **FastAPI** + OpenAPI |
| Frontend | Templates Jinja + JS vanilla | **Vue 3 SPA** + Pinia + Tailwind + ECharts |
| WSGI/ASGI | Gunicorn (sync workers) | **Uvicorn** (async) |
| Hot storage | SQLite + 7 índices B-tree | **DuckDB** (zone maps automáticos) |
| Cold storage | SQLite + gzip-6 | **Parquet zstd** |
| Cold query | Descomprime tudo para `/dev/shm` | Lê direto via `read_parquet()` |
| Dicionário IPs | `ip_addresses` por DB diário | **Registry global** SQLite WAL persistente |
| Auth | SHA-256 + salt | **Argon2id** + JWT HttpOnly |
| Scheduler | cron | **systemd timers** |
| Receiver | `socket.recvfrom` (sync, single-thread) | `asyncio.DatagramProtocol` + **uvloop** + `SO_REUSEPORT` |
| LOC total | ~6.200 | ~7.730 (+ frontend SPA + deploy completo) |

### UX

| v4 | v5 |
|---|---|
| Páginas SSR | SPA com transições suaves (animations, skeletons) |
| Tema dark fixo | Tema dark moderno (Vercel/Supabase-like) com tipografia tabular |
| Cliques: form → submit → reload página inteira | XHR (axios) → atualização do component |
| Auto-refresh do dashboard via `meta http-equiv="refresh"` (página inteira) | `setInterval(refresh, 3s)` (cards atualizam isoladamente) |
| Sem feedback visual de loading | Skeletons + spinners + toasts para ações |
| Tabelas com headers fixos (CSS) | Mesma coisa + sticky positioning |
| Modal de criação de usuário em janela popup | Modal animado com backdrop blur |

### Operacional

| v4 | v5 |
|---|---|
| `setup.sh` 432 linhas | `install.sh` 258 linhas (mais enxuto, com `--dry-run` e `--non-interactive`) |
| `cron` para jobs | `systemd timers` (logs unificados, `Persistent=true` reexecuta após downtime) |
| Logs em `/var/log/megalog/*.log` | Tudo via `journalctl -u megalog-*` |
| Sem health endpoint | `GET /api/healthz` |
| Status via `systemctl status` apenas | Dashboard com cards de hardware + ingestão tempo real |

---

## 2. Breaking changes

### Schema do banco

| v4 | v5 |
|---|---|
| Hot: `YYYY-MM-DD.db` (SQLite) | Hot: `YYYY-MM-DD.duckdb` (DuckDB nativo) |
| Cold: `YYYY-MM-DD.db.gz` | Cold: `YYYY-MM-DD.parquet` (zstd) |
| Tabela `ip_addresses` em cada DB diário | Tabela `ip_registry` global em `state/ip_registry.db` |
| Coluna `id INTEGER PK AUTOINCREMENT` em logs | **Removida** (DuckDB tem rowid implícito) |
| Coluna `raw_msg TEXT` em logs | **Removida** (não é mais armazenada — economiza ~50% do tamanho) |
| Coluna `src_mac TEXT` em logs | **Removida** (não é usada em auditoria) |
| Tipos: `INTEGER` | Tipos: `UTINYINT, USMALLINT, UINTEGER, BOOLEAN` (compactos) |

### Endpoints da API

V4 era SSR (sem API formal). Templates Jinja faziam tudo. V5 tem **19 endpoints
REST sob `/api/`**. Veja [docs/API.md](API.md).

Se você tinha scripts batendo direto nas URLs HTML do v4 (raro), eles **quebram**.
Reescrever para `/api/search`, `/api/auth/login`, etc.

### Estrutura de diretórios

| v4 | v5 |
|---|---|
| `/dados1/hot-storage/` | `/dados1/hot/` |
| `/dados2/cold-storage/` | `/dados2/cold/` |
| `/dados1/stream/stream_logs.raw` (monolítico) | `/dados1/stream/YYYY-MM-DD-HH.raw` (rotação horária) |
| `/tmp/megalog_stream_position` | `/dados1/state/stream_offsets.json` (mais robusto) |
| `app/users.db` (SQLite no diretório do app) | `/dados1/state/megalog.db` (separado do código) |
| `/var/log/megalog/*.log` | journald (`journalctl -u megalog-*`) |

### Configuração

| v4: `config_app.py` (módulo Python) | v5: `/etc/megalog/megalog.env` (env file) |
|---|---|
| `APP_NAME = "MegaLog"` | `MEGALOG_APP_NAME=MegaLog` |
| `RECEIVER_PORT = 514` | `MEGALOG_RECEIVER_PORT=514` |
| `HOT_STORAGE_DIR = "/dados1/hot-storage"` | `MEGALOG_HOT_STORAGE_DIR=/dados1/hot` |
| `BATCH_INSERT_SIZE = 300` | `MEGALOG_BATCH_SIZE=5000` (10× maior, DuckDB amortiza) |
| `SECRET_KEY = "..."` (no código) | `MEGALOG_SECRET_KEY=...` (no env, perms `640 root:megalog`) |

Lista completa em [megalog/config.py](../megalog/config.py).

### Auth

- Hash: `sha256(salt+password)` em formato `hex(salt)$hex(hash)` → **Argon2id**
- Sessão: cookie de Flask Session → **JWT em cookie HttpOnly**
- **Migração transparente:** usuários do v4 conseguem logar normalmente; o hash
  é re-hashado para Argon2 no primeiro login bem-sucedido. Não precisa resetar
  senhas.

### Classificador

**Sem breaking change.** O classificador 9-categorias foi **portado verbatim**
do v4 para v5 ([megalog/analytics/classifier.py](../megalog/analytics/classifier.py)).
Mesmas heurísticas, mesmos thresholds, mesmas categorias, mesmos anti-sinais.

A diferença é que agora o input vem de queries DuckDB sobre Parquet (mais rápido)
em vez de SQLite com índices. Resultado da classificação é idêntico.

---

## 3. Migração de dados

### Importar tudo de uma vez

```bash
# 1. Copie os arquivos do servidor v4 para um diretório acessível no v5
sudo mkdir -p /backup/v4-import
sudo scp old-server:/dados2/cold-storage/*.db.gz /backup/v4-import/
sudo scp old-server:/dados1/hot-storage/*.db    /backup/v4-import/

# 2. Rode a tool de importação (idempotente)
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python \
    -m megalog.tools.import_legacy /backup/v4-import/

# Output esperado:
# Encontrados N arquivo(s) para importar
# Importando 2026-04-20.db (schema v2) → 2026-04-20.parquet
#   32263 IPs locais mapeados → global registry
#   OK: 2026-04-20.db — 2741707 linhas em 4.5s (604597/s) → 2026-04-20.parquet (23.4 MB)
# ...
# Resumo: N ok / 0 pulado / 0 erro · X linhas total importadas
```

### O que a tool faz

1. **Auto-detecta** o schema (3 schemas suportados: v2 atual, B intermediário, A antigo)
2. **Descomprime** `.db.gz` para tmpdir (não toca no original)
3. **Mapeia** IPs locais (`ip_addresses` do DB legado) → IDs globais no `ip_registry`
4. **Escreve** Parquet zstd em `/dados2/cold/`
5. **Atualiza** `daily_stats` para aparecer no Dashboard
6. Pula se Parquet já existe (`--overwrite` força)

### Em batches noturnos (se tem MUITO volume)

```bash
#!/bin/bash
# /usr/local/bin/megalog-import-nightly.sh
DATE=$(date -d 'yesterday' +%F)
SRC=/backup/v4/$DATE.db.gz
[ -f "$SRC" ] && sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python \
    -m megalog.tools.import_legacy "$SRC"
```

```cron
# /etc/cron.d/megalog-import
0 4 * * * root /usr/local/bin/megalog-import-nightly.sh >> /var/log/megalog-import.log 2>&1
```

### Verificar integridade pós-import

```bash
# 1. Conferir no Dashboard
firefox http://<seu-server>/daily

# 2. Comparar contagem por dia (v4 vs v5)
# No v4:
sqlite3 /v4/dados1/hot-storage/2026-04-20.db "SELECT COUNT(*) FROM logs"
# No v5:
duckdb -c "SELECT COUNT(*) FROM read_parquet('/dados2/cold/2026-04-20.parquet')"
# Devem bater.

# 3. Spot-check de uma busca conhecida
# Pegue um nat_ip+ts que você sabe que existia no v4, faça a busca no v5
# e confira que o src_ip retornado é o mesmo.
```

### Detalhes técnicos da migração

| v4 schema | Detectado por | Como mapeado |
|---|---|---|
| **v2** (atual no v4) | tem `ip_addresses` | IPs já em INTEGER, dicts em `interfaces`/`protocols`/`conn_states`. Mapeamento 1:1 com tradução de IDs locais → globais via `IpRegistry`. |
| **B** (intermediário) | tem `d_interfaces` ou `d_protocols` | IPs como INTEGER inline (sem tabela `ip_addresses`). Itera todos os logs para extrair IPs únicos → registra no global → mapeia. |
| **A** (mais antigo) | nenhum dos acima | IPs como TEXT, timestamp TEXT. Loop Python: para cada linha, parseia ts, converte IP TEXT→INT, registra. Mais lento (~50-100k linhas/s). |

Throughput medido em produção contra schema v2 real: **~700k linhas/s**.

---

## 4. Migração de configuração

### Copia da `config_app.py` v4 → `/etc/megalog/megalog.env` v5

```bash
# Veja seu config_app.py v4
cat /usr/local/src/mega-log/config_app.py | grep -E '^[A-Z_]+ ='

# Edite o env do v5 com os mesmos valores
sudo nano /etc/megalog/megalog.env
```

Mapeamento direto:

| Variável v4 (Python) | Variável v5 (env) |
|---|---|
| `APP_NAME` | `MEGALOG_APP_NAME` |
| `RECEIVER_HOST` | `MEGALOG_RECEIVER_HOST` |
| `RECEIVER_PORT` | `MEGALOG_RECEIVER_PORT` |
| `WEB_HOST` | `MEGALOG_WEB_HOST` |
| `WEB_PORT` | `MEGALOG_WEB_PORT` |
| `STREAM_DIR` | `MEGALOG_STREAM_DIR` |
| `HOT_STORAGE_DIR` | `MEGALOG_HOT_STORAGE_DIR` |
| `COLD_STORAGE_DIR` | `MEGALOG_COLD_STORAGE_DIR` |
| `HOT_RETENTION_DAYS` | `MEGALOG_HOT_RETENTION_DAYS` |
| `DELETE_AFTER_DAYS` | `MEGALOG_DELETE_AFTER_DAYS` |
| `SESSION_TIMEOUT_MINUTES` | `MEGALOG_SESSION_TIMEOUT_MINUTES` |
| `SECRET_KEY` | `MEGALOG_SECRET_KEY` |
| `BATCH_INSERT_SIZE` | `MEGALOG_BATCH_SIZE` (sugestão: aumentar 10× — DuckDB amortiza) |

Sem equivalente direto (não existem no v5):
- `RAM_QUERY_DIR` — v5 não descomprime para RAM
- `RAM_CACHE_TTL_SECONDS` — idem
- `SQLITE_CACHE_MB` — DuckDB tem cache automático
- `COMPRESS_AFTER_DAYS` — v5 só tem hot e cold, sem fase intermediária

Novas no v5 (sem equivalente v4):
- `MEGALOG_IP_REGISTRY_PATH=/dados1/state/ip_registry.db`
- `MEGALOG_STATE_DIR=/dados1/state`
- `MEGALOG_BATCH_FLUSH_SECONDS=2.0`
- `MEGALOG_DUCKDB_CLOSE_INTERVAL_SECONDS=3.0`
- `MEGALOG_IP_CACHE_HOT_TOP_N=50000`
- `MEGALOG_IP_CACHE_MAX=200000`
- `MEGALOG_DUCKDB_THREADS=2`
- `MEGALOG_STREAM_PROCESSED_KEEP_HOURS=24`

---

## 5. Migração de usuários

A v5 cria um admin default (`admin` / `megalog123`) no primeiro startup
**se a tabela `users` está vazia**.

### Importar usuários do v4

A v5 espera `users` em SQLite no formato:

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    created_at INTEGER,
    last_login INTEGER,
    active INTEGER DEFAULT 1
);
```

Que é **idêntico** ao do v4 (`app/users.db`). Cópia direta funciona:

```bash
sudo systemctl stop megalog-web
sudo cp /v4/app/users.db /tmp/v4-users.db

# Copia tabela users do v4 para o v5
sudo -u megalog sqlite3 /dados1/state/megalog.db <<EOF
ATTACH '/tmp/v4-users.db' AS v4;
DELETE FROM users;
INSERT INTO users SELECT * FROM v4.users;
DETACH v4;
EOF

sudo systemctl start megalog-web
```

Os usuários conseguem logar com **mesma senha do v4**. Os hashes legados
(`hex(salt)$hex(sha256(salt+pass))`) são aceitos pelo `verify_password` do v5
e re-hashados para Argon2id no primeiro login bem-sucedido.

### Importar audit_log do v4 (opcional)

Se quer preservar o histórico de auditoria:

```bash
sudo -u megalog sqlite3 /dados1/state/megalog.db <<EOF
ATTACH '/tmp/v4-users.db' AS v4;
INSERT INTO audit_log (user_id, username, action, details, ip_address, ts)
SELECT user_id, username, action, details, ip_address, ts FROM v4.audit_log;
DETACH v4;
EOF
```

---

## 6. Convivência v4 + v5 durante transição

Recomendado por 1-2 semanas para validar que v5 está estável antes de
desativar v4.

### Estratégia: split horizon

- Ambos os sistemas recebem syslog **simultaneamente** (Mikrotik envia para 2 destinos)
- v4 continua em produção (porta 514)
- v5 escuta na porta 5140 (ou outro)

```routeros
# Adicione SEGUNDA action de log apontando pro v5
/system logging action
add name=megalog-v5 \
    target=remote \
    remote=10.100.100.10 \      # IP do servidor v5
    remote-port=5140 \
    bsd-syslog=yes

# Direcione o mesmo topic para ambos
/system logging
add action=megalog-v5 topics=firewall
```

E no v5:

```bash
sudo sed -i 's/MEGALOG_RECEIVER_PORT=514/MEGALOG_RECEIVER_PORT=5140/' \
    /etc/megalog/megalog.env
sudo systemctl restart megalog-receiver
```

Após uma semana de validação:

1. Importe os DBs históricos do v4 → v5 (seção 3)
2. Confirme paridade de buscas (mesmo nat_ip+ts retorna mesmo src_ip)
3. Cutover: troque o Mikrotik para enviar **só** para v5 (porta 514)
4. Desative o v4

---

## 7. Rollback

Se algo der errado durante a migração e você precisar voltar para v4:

### Cenário 1: v5 não funciona, v4 ainda intacto

Não há nada a fazer — o v4 continua rodando como antes. Apenas pare o v5
(que pode estar consumindo a mesma porta UDP 514 do Mikrotik se você não
usou a estratégia split horizon).

```bash
sudo systemctl stop megalog-receiver megalog-processor megalog-web
sudo systemctl disable megalog-receiver megalog-processor megalog-web
```

### Cenário 2: v5 estava em produção sozinho, deu problema

Se você cutoveou para v5 e quer voltar: o v5 importou os dados antigos do v4,
mas os **novos logs** (recebidos só pelo v5) não estão no v4. Você perde esse
intervalo se voltar.

Procedimento:
1. Reative o v4 (instalação antiga continua em `/usr/local/src/mega-log/`)
2. `sudo systemctl start megalog-receiver` (v4) — assume que ainda existe
3. Reapontar Mikrotik para v4 (porta 514 do servidor v4)
4. Aceitar a perda do intervalo em v5 (ou tentar exportar do Parquet de v5
   para SQLite v4 — não há tool pronta, código manual em SQL)

### Mitigação: backup antes de cutover

```bash
# No v4, antes de qualquer mudança:
sudo systemctl stop megalog-receiver megalog-processor megalog-web
sudo tar czf /backup/v4-pre-migration.tar.gz \
    /dados1/hot-storage \
    /dados1/stream \
    /dados2/cold-storage \
    /usr/local/src/mega-log/app/users.db \
    /usr/local/src/mega-log/config_app.py
sudo systemctl start megalog-receiver megalog-processor megalog-web
```

Backup completo de v4 que permite rollback total.

---

## Histórico de versões

| Versão | Data | Notas |
|---|---|---|
| 4.5.0 | 2025-Q4 | Última versão v4 (referência) |
| 5.0.0a1 | 2026-04-27 | Reescrita completa (esta) — em produção interna |

---

## Suporte à migração

Em caso de dúvidas durante migração:

1. Consulte [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) — receitas para
   problemas comuns durante import.
2. Veja [megalog/tools/import_legacy.py](../megalog/tools/import_legacy.py) — código está bem comentado.
3. Os 4 DBs reais de teste em `/tmp/legacy/` foram usados para validar a
   migração de schema v2. Use esses como benchmark se quiser comparar
   throughput (esperado: ~700k linhas/s).
