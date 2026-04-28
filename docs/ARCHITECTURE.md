# Arquitetura do MegaLog v5

> Documento técnico para devs e arquitetos. Pressupõe familiaridade com syslog,
> SQL, Python async e SPA.

## Índice

1. [Visão geral do pipeline](#1-visão-geral-do-pipeline)
2. [Decisões arquiteturais](#2-decisões-arquiteturais)
3. [Storage layer](#3-storage-layer)
4. [Pipeline de ingestão](#4-pipeline-de-ingestão)
5. [Analytics e classificador](#5-analytics-e-classificador)
6. [Backend API](#6-backend-api)
7. [Frontend SPA](#7-frontend-spa)
8. [Jobs (systemd timers)](#8-jobs-systemd-timers)
9. [Tradeoffs explícitos](#9-tradeoffs-explícitos)
10. [Limites conhecidos](#10-limites-conhecidos)

---

## 1. Visão geral do pipeline

```
Mikrotik → UDP:514 → receiver(asyncio) → /dados1/stream/HH.raw
                                              │ tail
                                              ▼
                                       processor(parser+batch)
                                              │
                          ┌───────────────────┼─────────────────────┐
                          ▼                   ▼                     ▼
                  IpRegistry (SQLite WAL)  DuckDB hot          DictCache
                  /dados1/state/           /dados1/hot/          (in-mem)
                  ip_registry.db           YYYY-MM-DD.duckdb

                          │ daily 02:00 (timer)
                          ▼
                   archive_day → COPY TO PARQUET zstd
                          │
                          ▼
                  Parquet cold
                  /dados2/cold/YYYY-MM-DD.parquet

                          │ daily 02:30 (timer)
                          ▼
                   analyze_day → 10 SQL queries → classifier
                          │
                          ▼
                  Alerts / daily_stats (SQLite ops)
                  /dados1/state/megalog.db

                  ┌───────────────────────┐
                  │  nginx :80            │
                  │  ├ SPA (frontend/dist)│
                  │  └ proxy /api → 5000  │
                  └───────────────────────┘
                          │
                          ▼
                  uvicorn FastAPI :5000
```

---

## 2. Decisões arquiteturais

### Storage colunar (DuckDB + Parquet) em vez de SQLite

**Por quê:** logs CGNAT são write-heavy e search-mostly-sequential. Em DBs >50M
linhas, SQLite com 7 índices B-tree consome 1-2 GB de índices, faz INSERT lento
e tem joins caros para analytics multi-dia.

**Tradeoff:** DuckDB tem **lock exclusivo por arquivo** — não permite leitor
concorrente com escritor. Resolvido com:
- **IP registry** trocado para SQLite WAL (permite N readers + 1 writer)
- **DuckDB hot do dia** o processor fecha a conexão a cada `duckdb_close_interval_seconds=3`,
  abrindo janela para o web. Web faz retry linear (8 × 0.5s).

**Resultado medido (Fase 0):** 5× compressão, 5-7× busca pontual, 15-20× agregação top-IPs.

### Dicionário global persistente de IPs

**Problema do v4:** cada DB diário tinha sua própria tabela `ip_addresses`. O IP
`192.168.1.100` recebia ID `5` num DB e ID `47` em outro. Analytics multi-dia
exigia abrir N DBs e construir mapeamento manual.

**Solução v5:** **um único** `ip_registry.db` (SQLite WAL) com `(ip_id, ip,
first_seen, last_seen, total_hits, is_hot)`. ID estável entre dias para sempre.

**Implementação:** [megalog/storage/ip_registry.py](../megalog/storage/ip_registry.py).
- Pré-carrega top-N IPs (`is_hot=true`) em RAM no startup
- Cache LRU para os demais (200k entradas máximo)
- `get_or_create` no caminho rápido = lookup em dict; lento = SELECT + INSERT
- `flush_hits` periódico (60s) atualiza `total_hits` em batch

### Frontend desacoplado (SPA)

**Por quê:** UI rica para análise forense (gráficos, tabelas grandes, calendar,
filtros dinâmicos) ficou ruim em Jinja+JS-vanilla do v4. SPA permite reuso
total dos componentes Vue + ECharts e build estático servido pelo nginx.

**Tradeoff:** mais complexidade no build (vite + npm). Mitigado com `install.sh`
que faz `npm install && npm run build` automático.

### Auth Argon2id + JWT em cookie HttpOnly

**Por quê:**
- Argon2id é o padrão moderno (vencedor PHC), resistente a GPU/ASIC
- JWT em cookie HttpOnly + SameSite=Strict mitiga XSS (não acessível via JS)
  e CSRF (cookie não enviado em cross-origin) numa única decisão

**Migração transparente:** se hash do banco está em formato v4
(`hex(salt)$hex(sha256(salt+pass))`), aceita no login e re-hasha para Argon2.
Implementado em [megalog/api/auth.py:verify_password](../megalog/api/auth.py#L33).

### systemd timers em vez de cron

**Por quê:**
- Logs unificados via `journalctl`
- `Persistent=true` reexecuta após downtime do servidor
- Hardening sandbox (mesmo dos services daemon)
- Status visível com `systemctl list-timers`

---

## 3. Storage layer

### Hot — `/dados1/hot/YYYY-MM-DD.duckdb`

DuckDB nativo com schema enxuto:

```sql
CREATE TABLE logs (
    ts             UINTEGER NOT NULL,         -- UNIX epoch
    in_iface_id    USMALLINT,
    out_iface_id   USMALLINT,
    proto_id       UTINYINT,                  -- 1=TCP 2=UDP (convencional)
    conn_state_id  USMALLINT,
    has_snat       BOOLEAN,
    src_ip_id      UINTEGER NOT NULL,         -- FK ao registry global
    src_port       USMALLINT,
    dst_ip_id      UINTEGER NOT NULL,
    dst_port       USMALLINT,
    nat_ip_id      UINTEGER,
    nat_port       USMALLINT,
    pkt_len        USMALLINT,
    tcp_flags      VARCHAR,
    log_type       VARCHAR DEFAULT 'nat'
);
```

**Sem índices B-tree explícitos.** DuckDB usa **zone maps** automáticos por
coluna (min/max por bloco) + ordem natural de inserção `(ts, src_ip_id)` que
garante boa data locality para queries forenses.

Tabelas auxiliares (`interfaces`, `protocols`, `conn_states`) ficam **dentro**
do mesmo DB diário — recriadas a cada dia. IDs são locais e perdidos quando o
DB é deletado pós-archive; os nomes são preservados em sidecar JSON
(`cold/YYYY-MM-DD.dicts.json`) gerado pelo `archive_day` para que queries em
cold continuem resolvendo `in_iface`/`proto`/`conn_state`.

### Cold — `/dados2/cold/YYYY-MM-DD.parquet`

Snapshot Parquet:
- `COMPRESSION 'zstd', COMPRESSION_LEVEL 6`
- `ROW_GROUP_SIZE 100000`
- `ORDER BY (ts, src_ip_id)`

Queries lêem direto via `read_parquet()` em DuckDB — **sem descompressão
integral**, com filter pushdown e projection pushdown.

### Registry global — `/dados1/state/ip_registry.db` (SQLite WAL)

```sql
CREATE TABLE ip_registry (
    ip_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         INTEGER NOT NULL UNIQUE,    -- IPv4 packed (signed 32-bit no SQLite)
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    total_hits INTEGER NOT NULL DEFAULT 0,
    is_hot     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_ip_registry_hits ON ip_registry(total_hits DESC);
```

**Por que SQLite WAL** (e não DuckDB):
- DuckDB tem lock exclusivo absoluto por arquivo
- SQLite WAL permite **N readers + 1 writer** simultaneamente
- Para tabela única e pequena (~100k IPs), performance é equivalente
- DuckDB pode ler SQLite via extensão (`INSTALL sqlite`) — então JOINs cross-format (Parquet × registry) seguem possíveis

**Cuidado com signed int:** SQLite armazena INTEGER como signed 64-bit. IPs > 2³¹
ficam negativos no read. Sempre fazer `ip_int & 0xFFFFFFFF` ao recuperar.

### Operacional — `/dados1/state/megalog.db` (SQLite)

Tabelas:
- `users` (id, username, password_hash, role, created_at, last_login, active)
- `audit_log` (id, user_id, username, action, details, ip_address, ts) — append-only
- `daily_stats` (date, log_count, db_size_bytes, updated_at) — cache atualizado pelo archive job
- `anomaly_alerts` (id, date, log_count, expected_count, ratio, analysis, details_json, classification, acknowledged*)

Volume baixo (< 1 MB típico), padrão OLTP. SQLite é a ferramenta certa.

---

## 4. Pipeline de ingestão

### Receiver — [megalog/ingest/receiver.py](../megalog/ingest/receiver.py)

`asyncio.DatagramProtocol` com `uvloop` (event loop em C, ~30% mais rápido).
`SO_REUSEPORT` permite N processos no mesmo (host, porta) para escalar
horizontalmente — kernel distribui em round-robin.

`SO_RCVBUF = 16 MB` absorve picos de syslog sem perda.

`AmbientCapabilities=CAP_NET_BIND_SERVICE` no systemd permite bind em porta 514
sem rodar como root.

### Stream buffer — [megalog/ingest/stream.py](../megalog/ingest/stream.py)

Arquivo `.raw` rotacionado por hora: `/dados1/stream/YYYY-MM-DD-HH.raw`.

Substitui o `stream_logs.raw` monolítico do v4. Vantagens:
- Não cresce indefinidamente (rotação automática)
- Offsets persistentes em `state_dir/stream_offsets.json` (não `/tmp`)
- Após processado, arquivo é movido para `.processed/` e excluído após 24h

### Parser — [megalog/ingest/parser.py](../megalog/ingest/parser.py)

Suporta 4 formatos de timestamp do Mikrotik:
- **A**: `YYYY-MM-DD HH:MM:SS firewall,info forward: ...`
- **B**: `Mon DD HH:MM:SS HOSTNAME PREFIX: ...` (BSD syslog)
- **C**: `HH:MM:SS PREFIX: ...` (apenas hora — herda data de hoje)
- **D**: `<PRI>Mon DD HH:MM:SS HOSTNAME ...` (RFC3164 com prioridade)

Suporta **line continuation**: Mikrotik quebra linhas longas dentro de `IP:PORT`,
deixando segunda linha começando com `:port,...`. Regex `RE_CONTINUATION` detecta
e une.

Funções **puras** (sem side-effect) — testadas isoladamente em
[tests/unit/test_parser.py](../tests/unit/test_parser.py) (24 testes).

### Processor — [megalog/ingest/processor.py](../megalog/ingest/processor.py)

Loop principal:
1. Lista arquivos `.raw` em ordem cronológica
2. Para cada arquivo, lê do offset salvo até EOF
3. Junta linhas de continuation (`join_continuations`)
4. Parser puro → `ParsedLine` dataclass
5. Resolve IDs:
   - Iface/Proto/State via `DictCache` local (cache do dia)
   - IPs via `IpRegistry` global (cache LRU + SQLite)
6. Acumula em batch (`batch_size=5000`, `batch_flush_seconds=2`)
7. INSERT batch no DuckDB do dia (`day = datetime.fromtimestamp(ts).date()`)
8. A cada `duckdb_close_interval_seconds=3`: `CHECKPOINT` + close (libera lock pro web)
9. A cada 60s: `flush_hits()` no registry (UPDATE em batch dos contadores)

`day` é determinado pelo **timestamp do log**, não pela data do arquivo `.raw`.
Logs que chegam após meia-noite mas têm timestamp de antes ainda vão para o
DB correto.

---

## 5. Analytics e classificador

### Detector — [megalog/analytics/anomaly_detector.py](../megalog/analytics/anomaly_detector.py)

Função pura `compute_anomalies(daily_stats, baseline_days=7, threshold_ratio=3.0,
min_count=100_000)`. Para cada data com volume ≥ `min_count`, compara com média
móvel dos `baseline_days` anteriores. Se `ratio >= threshold_ratio`, emite
`DateAnomaly` candidato a análise profunda.

### Analyzer — [megalog/analytics/anomaly_analyzer.py](../megalog/analytics/anomaly_analyzer.py)

`collect_stats(con)` roda **10 queries SQL** sequenciais sobre uma partição
(hot ou cold via view), montando dict com:
- Globais (total, src/dst únicos, dist de portas)
- Top 10 IPs origem com perfil (dst_ports, dst_ips, well_known_pct, ephemeral_pct)
- Top 10 portas destino com nome de serviço
- Top 10 IPs destino
- IPs com >50 portas distintas (suspeitos P2P)
- Média de portas por IP
- Detalhe NTP / DNS (se volume relevante)
- Top 5 fluxos repetidos (4-tupla src_ip+dst_ip+dst_port+proto)
- UDP em portas TCP-only

`analyze_day(con)` chama `collect_stats` + `classify_traffic` + monta narrativa textual.

### Classificador — [megalog/analytics/classifier.py](../megalog/analytics/classifier.py)

Função `classify_traffic(stats: dict) -> Classification`. Pura. Avalia 9
categorias com sinais ponderados; cada categoria recebe score 0.0-1.0; a maior
acima de `0.30` vence; senão `"Alto Volume (Indeterminado)"`.

Categorias:
- **P2P/Torrent** — top talker com muitas portas × muitos IPs destino
- **Abuso de Protocolo (NTP)** — ≥100k queries NTP + poucos clientes
- **Abuso de Protocolo (DNS)** — ≥500k queries DNS + poucos clientes ou muitos servers
- **Ataque/DoS** — top IP ≥80% para ≤10 destinos
- **Varredura de Portas** — ≤10 origens varrendo ≥1000 portas
- **Botnet/C2** — ≥30 clientes → ≤10 destinos em ≤5 portas
- **Abuso de Banda** — top IP ≥50% com perfil de serviços conhecidos
- **Flood/Loop** — mesmo fluxo (4-tupla) repetido ≥1000×
- **Malware/Anômalo** — UDP em portas TCP-only ≥10k eventos

**Anti-sinais** reduzem score (evitam falso positivo). Ex: HTTP ≥50% → −0.20 em P2P.

Portado verbatim de [v4 compress_old_dbs.py:341-740](../../megalog/compress_old_dbs.py#L341-L740).
Heurísticas e thresholds inalterados.

**Por que tão complexo?** Complexidade ciclomática F (52). Justificada porque
são regras de negócio explícitas que precisam ser auditáveis linha-a-linha.
Tentar decompor em sub-funções espalharia a lógica.

---

## 6. Backend API

[megalog/api/](../megalog/api/) — FastAPI 0.115+.

### Estrutura

```
api/
├── app.py              # factory create_app() + lifespan (cria admin default)
├── deps.py             # DI: get_settings, get_ops_store, get_ip_registry, get_current_user, require_admin
├── auth.py             # Argon2 + JWT issue/decode + verify_password (com migração legado v4)
├── search.py           # SearchService sobre partições hot/cold
├── system.py           # collect_status (psutil + systemctl + métricas ingestão)
└── routes/
    ├── auth.py         # login, logout, me, change-password
    ├── search.py       # search (paginado), export CSV (streaming)
    ├── analytics.py    # daily, top-ips (registry global), calendar
    ├── system.py       # system-status, healthz
    └── admin.py        # users CRUD, audit, alerts list/get/ack (require_admin)
```

### Lifespan

[megalog/api/app.py](../megalog/api/app.py): no startup do uvicorn:
1. Cria `OperationalStore` (SQLite ops) — gera schema se não existe
2. Cria admin default `admin/megalog123` se `users` está vazio (warning visível no journal)
3. Abre `IpRegistry` em **read-only** (web só lê; processor é o único writer)

No shutdown: fecha o registry.

### Auth flow

```
POST /api/auth/login {username, password}
  → ops.get_user_by_username
  → verify_password (Argon2 ou hash legado v4)
  → se hash legado: re-hasha para Argon2 e UPDATE
  → ops.touch_last_login + log_audit("login")
  → issue_token(JWT HS256)
  → set_cookie megalog_session HttpOnly SameSite=Strict
  → 200 LoginOut

GET /api/auth/me  →  requer cookie  →  decode_token  →  CurrentUser
POST /api/auth/logout  →  delete_cookie + log_audit("logout")
```

### Audit automático

Estas ações geram entry em `audit_log` automaticamente:
`login, login_failed, logout, change_password, change_password_failed,
search, export, create_user, deactivate_user, update_user_role, reset_password, ack_alert`.

Inclui `ip_address` extraído de `request.client.host`.

---

## 7. Frontend SPA

[frontend/](../frontend/) — Vue 3.5 + Vite 6 + Pinia 2 + vue-router 4 + Tailwind 3 + ECharts.

### Estrutura

```
frontend/src/
├── main.ts              # bootstrap (createApp + Pinia + router)
├── App.vue              # <RouterView>
├── router.ts            # 9 rotas + auth guard (requiresAuth, requiresAdmin)
├── style.css            # Tailwind + componentes utilitários
├── lib/
│   ├── api.ts           # axios client + interceptor 401→/login + types
│   └── format.ts        # fmtNumber, fmtBytes, fmtDateTime, fmtPct
├── stores/
│   ├── auth.ts          # Pinia: me, bootstrap, login, logout, isAdmin
│   ├── alerts.ts        # contagem de alertas pendentes (badge na sidebar)
│   └── toast.ts         # notificações success/error/warn/info com auto-dismiss
├── components/
│   ├── Layout.vue       # sidebar + main + Toast
│   ├── PageHeader.vue   # header sticky com título/subtítulo + slot actions
│   ├── StatCard.vue     # label-micro + valor grande tabular + sub-info
│   ├── ProgressBar.vue  # cor adaptativa (verde/amber/red)
│   ├── CpuBars.vue      # mini-bars per-core
│   ├── StatusDot.vue    # dot pulsante com glow + label
│   ├── EmptyState.vue   # ícone + título + subtitle (com slot action)
│   ├── Skeleton.vue     # shimmer animado
│   └── Toast.vue        # render dos toasts (Teleport to body)
└── pages/
    ├── Login.vue              # gradient radial + logo glow
    ├── Dashboard.vue          # 4 cards hardware + 4 cards ingestão + alerts + 14 dias + serviços
    ├── Search.vue             # form 6 colunas + tabela 7 colunas (IP:porta combinado)
    ├── DailyLogs.vue          # 4 cards sumário + lista dias + sidebar (distribuição + timers)
    ├── AnomalyDetail.vue      # 4 cards + razões/causas + ECharts portas + 3 tabelas + narrativa
    ├── AdminUsers.vue         # tabela + modal de criação + ações no hover
    ├── AdminAudit.vue         # filtro por ação + badges coloridos
    ├── AdminAlerts.vue        # checkbox "só pendentes" + reconhecer hover
    └── ChangePassword.vue     # form simples
```

### Tema

Paleta dark inspirada em Vercel/Supabase, herdando tokens de cor do dashboard
v4 (`#0a0c12` body, `#161925` card, `#22d3ee` primary). Tipografia: Inter
(sans) + JetBrains Mono (números/IPs/portas). Tabular nums com `font-feature-settings`.

Componentes utilitários CSS em [style.css](../frontend/src/style.css):
`btn-primary/secondary/ghost/danger`, `input`, `card`, `card-header`,
`badge-ok/warn/error/info/muted`, `dot-ok/warn/error/muted`, `tbl`, `tbl-compact`,
`label-micro`, `num-tabular`, `skeleton`.

### Build

```
vite v6 + vue-tsc → 660 módulos transformados → 7s
- index.html       0.5 KB
- index.css       30 KB / 6 KB gzip (Tailwind purged)
- index.js (vendor) 150 KB / 59 KB gzip
- páginas comuns    1-7 KB cada (lazy)
- AnomalyDetail   470 KB / 158 KB gzip (ECharts inteiro, lazy)
```

Code splitting funciona: cada `pages/*.vue` em chunk separado, carregado sob demanda.

---

## 8. Jobs (systemd timers)

[megalog/jobs/](../megalog/jobs/):

| Job | Timer | Frequência | Função |
|---|---|---|---|
| `archive` | `megalog-archive.timer` | diário 02:00 | Move DuckDB hot > N dias para Parquet zstd em cold; atualiza `daily_stats` |
| `analyze` | `megalog-analyze.timer` | diário 02:30 | Detecta anomalias (baseline + threshold) + classifica + cria alerta |
| `retention` | `megalog-retention.timer` | semanal seg 03:00 | Apaga Parquets > `delete_after_days` (0 = nunca) |

Todos `Type=oneshot`. Jitter random (`RandomizedDelaySec`). `Persistent=true`
reexecuta após downtime.

CLI direta:
```bash
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -m megalog.jobs.archive
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -m megalog.jobs.analyze --date 2026-04-23 --force
sudo -u megalog /usr/local/src/megalog-v5/.venv/bin/python -m megalog.jobs.retention
```

---

## 9. Tradeoffs explícitos

### Lock exclusivo do DuckDB

**Limitação:** DuckDB não permite reader concorrente com writer.

**Mitigação:** processor fecha conexão a cada 3s. Web faz retry com backoff
linear (8 × 0.5s = 4s max). Funciona até dezenas de queries simultâneas no DB
do dia atual. Cold (Parquet) não tem o problema.

### Nomes de interface perdidos no cold

**Limitação:** Parquet só salva `logs`. Tabelas auxiliares
(`interfaces`, `protocols`, `conn_states`) ficam no DuckDB hot que é deletado.

**Impacto:** Em busca de logs cold, a coluna `in_iface` aparece como null.
`proto` ainda funciona via mapping convencional (1=TCP, 2=UDP).

**Mitigação implementada:** o `archive_day` salva sidecar JSON
`cold/YYYY-MM-DD.dicts.json` com os 3 dicts ao gerar o Parquet. A camada de
busca carrega o sidecar quando abre Parquet (Parquets antigos sem sidecar
degradam graciosamente para `null` nesses campos).

### `total_hits` aproximado

**Limitação:** `flush_hits` roda a cada 60s. Se o processor crash por kill -9,
perde até 60s de contadores.

**Impacto:** Stats levemente abaixo do real. Não perde **logs** (esses já estão
no DuckDB pós-flush do batch). Aceitável.

---

## 10. Limites conhecidos

| Limite | Valor atual | Origem |
|---|---|---|
| Pacote UDP máximo | 65535 bytes | `RECEIVER_BUFFER_SIZE` (config) |
| Buffer SO_RCVBUF | 16 MB | Hardcoded (config: `receiver_socket_buffer_bytes`) |
| Batch INSERT | 5000 linhas / 2s | `batch_size`, `batch_flush_seconds` (config) |
| Cache LRU IPs | 200k entradas | `ip_cache_max` (config) |
| Top-N IPs hot pré-carregados | 50k | `ip_cache_hot_top_n` (config) |
| Janela close DuckDB | 3s | `duckdb_close_interval_seconds` (config) |
| Retry de query no hot | 8 × 0.5s = 4s | `partitions.open_for_query` |
| Hot retention | 30 dias | `hot_retention_days` (config) |
| Delete after | 365 dias | `delete_after_days` (config; 0 = nunca) |
| Sessão TTL | 60 min | `session_timeout_minutes` (config) |
| Search per_page max | 1000 | Pydantic validator em `SearchQuery` |
| Export CSV max rows | 100k | hardcoded em `export_csv` |
| Upload via API | 8 MB | `client_max_body_size` no nginx |

Todos configuráveis em `/etc/megalog/megalog.env` (production) ou via env vars
`MEGALOG_*` (dev).
