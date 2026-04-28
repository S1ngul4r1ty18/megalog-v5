# MegaLog v5

> Sistema de auditoria CGNAT para roteadores Mikrotik. Coleta syslog UDP, armazena
> em colunar comprimido, indexa por IP global persistente, e responde busca
> forense em milissegundos.

[![tests](https://img.shields.io/badge/tests-110%20passing-brightgreen.svg)](#testes)
[![coverage](https://img.shields.io/badge/coverage-80%25-green.svg)](#testes)
[![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](#requisitos)
[![status](https://img.shields.io/badge/status-em%20produção-success.svg)](#estado-atual)

Atende **Marco Civil da Internet (Lei 12.965/2014)** e **Anatel 614/2013**: dado
um IP público + porta + timestamp, identifica o IP privado responsável.

---

## TL;DR

```bash
# 1. Instalar (interativo, ~2 min)
sudo /usr/local/src/megalog/deploy/install.sh

# 2. Apontar Mikrotik (no terminal RouterOS)
/system logging action add name=megalog target=remote remote=<IP-do-servidor> remote-port=514

# 3. Acessar
firefox http://<IP-do-servidor>/    # admin / megalog123 (TROCAR)
```

Detalhes: [docs/INSTALL.md](docs/INSTALL.md) · [docs/MANUAL.md](docs/MANUAL.md)

---

## Documentação

| Documento | Para quem | Conteúdo |
|---|---|---|
| [docs/MANUAL.md](docs/MANUAL.md) | **Operador / técnico** | Manual narrativo: instalação, primeiro acesso, busca forense, leitura de alertas, rotina diária |
| [docs/INSTALL.md](docs/INSTALL.md) | Sysadmin | Instalação detalhada, opções do `install.sh`, requisitos de hardware, integração com Mikrotik |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Dev / arquiteto | Visão geral do pipeline, decisões de design, schema de dados, tradeoffs DuckDB+Parquet |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | SRE / DevOps | Comandos systemctl, jobs noturnos, backup, rotação, monitoramento |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Sysadmin | Receitas para problemas comuns: receiver não recebe, processor parou, lock conflict, disco cheio |
| [docs/API.md](docs/API.md) | Integrador | Referência dos 19 endpoints, exemplos curl, contratos JSON, autenticação |
| [docs/SECURITY.md](docs/SECURITY.md) | Sec officer | Modelo de ameaças, hardening, rotação de SECRET_KEY, security headers, rate limit, ufw |
| [docs/CHANGELOG-v4-to-v5.md](docs/CHANGELOG-v4-to-v5.md) | Quem opera v4 hoje | Diferenças v4→v5, guia de migração de dados, breaking changes |

---

## O que mudou da v4 para a v5

| Aspecto | v4 | v5 |
|---|---|---|
| Hot storage | SQLite + 7 índices B-tree | DuckDB nativo (zone maps automáticos) |
| Cold storage | SQLite + gzip-6 | **Parquet zstd** (5× menor) |
| Cold query | descomprime tudo para `/dev/shm` antes | leitura seletiva direta no Parquet |
| Dicionário de IPs | recriado por dia | **global persistente** (cross-day analytics) |
| Web | Flask + Jinja + JS vanilla | FastAPI + OpenAPI + **Vue 3 SPA** |
| Auth | SHA-256+salt | **Argon2id + JWT HttpOnly** (com migração transparente do hash legado) |
| Scheduler | cron | **systemd timers** (logs unificados, Persistent=true) |
| LOC total | ~6.200 | ~7.730 (+ frontend SPA + deploy completo) |
| Bytes/log no disco (cold) | ~43 (gzip) | **~9 (Parquet zstd)** |
| Busca forense (mediana) | 60-96 ms | **11-15 ms** |

Detalhes em [docs/CHANGELOG-v4-to-v5.md](docs/CHANGELOG-v4-to-v5.md).

---

## Estado atual

```
Servidor: Debian 13 (trixie) · 5 cores · 16 GB RAM · /dados1 SSD · /dados2 HDD
Versão: 5.0.0a1
```

| Métrica | Valor |
|---|---|
| Serviços ativos | 9 (receiver + processor + web + nginx + 5 timers) |
| Dias indexados | Variável (depende da retenção; 30 hot + 365 cold por padrão) |
| Throughput de ingestão | ~700k linhas/s sustentado (medido em import) |
| Throughput de busca | 5-7× SQLite com índices |
| Compactação Parquet | 16.7× vs SQLite cru, 5× vs SQLite+gzip |
| Cobertura de testes | 80% (110 testes verdes) |

---

## Visão arquitetural

```
                     Mikrotik
                        │ syslog UDP:514
                        ▼
              ┌────────────────────┐
              │ megalog-receiver   │  asyncio + uvloop + SO_REUSEPORT
              │ (CAP_NET_BIND)     │  16 MB SO_RCVBUF · sem root
              └─────────┬──────────┘
                        │ append linha-a-linha
                        ▼
            /dados1/stream/YYYY-MM-DD-HH.raw   (rotação horária)
                        │ tail
                        ▼
              ┌────────────────────┐
              │ megalog-processor  │  parser regex (4 fmts + line continuation)
              │                    │  IP → registry global (SQLite WAL)
              │                    │  batch INSERT no DuckDB hot (5000)
              └─────────┬──────────┘
                        │ a cada 3s: CHECKPOINT + close (libera lock pro web)
                        ▼
            /dados1/hot/YYYY-MM-DD.duckdb     (do dia)
                        │ daily 02:00 (systemd timer)
                        ▼
              ┌────────────────────┐
              │ megalog-archive    │  COPY logs TO PARQUET zstd
              │                    │  delete hot.duckdb
              └─────────┬──────────┘
                        ▼
            /dados2/cold/YYYY-MM-DD.parquet
                        │ daily 02:30
                        ▼
              ┌────────────────────┐
              │ megalog-analyze    │  10 SQL queries → stats dict
              │                    │  classifier 9-categorias
              │                    │  alert + analysis no SQLite ops
              └────────────────────┘

  ┌─── nginx :80 ───────── proxy /api ───────► uvicorn :5000 ───┐
  │     SPA estática              FastAPI + Pydantic            │
  │     frontend/dist             Argon2 + JWT cookie HttpOnly  │
  │                                                              │
  │   Vue 3 + Pinia + Tailwind                  /api/auth        │
  │   ECharts (lazy)                            /api/search      │
  │   9 páginas (Login, Dashboard, Search,      /api/analytics   │
  │   DailyLogs, AnomalyDetail, AdminUsers,     /api/system-status
  │   AdminAudit, AdminAlerts, ChangePassword)  /api/admin       │
  └─────────────────────────────────────────────────────────────┘
```

Detalhes técnicos em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Requisitos

| Componente | Mínimo | Recomendado |
|---|---|---|
| OS | Debian 12+, Ubuntu 22.04+ | Debian 13 (trixie) |
| Python | 3.11 | 3.13 |
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Disco hot (SSD) | 5 GB/mês por roteador | NVMe |
| Disco cold (HDD) | 1 GB/mês por roteador | 7200 RPM |
| Software | systemd · nginx · nodejs (build do frontend) | mesma lista |

Hardware do servidor onde está rodando: 5 cores, 16 GB RAM, ~109 GB SSD (`/dados1`), ~57 GB HDD (`/dados2`).

---

## Comandos rápidos

```bash
# Instalar
sudo /usr/local/src/megalog/deploy/install.sh

# Status
systemctl status megalog-receiver megalog-processor megalog-web

# Logs
journalctl -u megalog-receiver -f
journalctl -u megalog-processor -f

# Forçar arquivamento manual
systemctl start megalog-archive.service

# Forçar análise de uma data específica
sudo -u megalog /usr/local/src/megalog/.venv/bin/python \
    -m megalog.jobs.analyze --date 2026-04-23 --force

# Importar DBs legados v4 (.db ou .db.gz)
sudo -u megalog /usr/local/src/megalog/.venv/bin/python \
    -m megalog.tools.import_legacy /backup/old/

# Testes
cd /usr/local/src/megalog
.venv/bin/python -m pytest tests/ -v

# Desinstalar (preserva dados)
sudo /usr/local/src/megalog/deploy/uninstall.sh

# Desinstalar e APAGAR TUDO
sudo /usr/local/src/megalog/deploy/uninstall.sh --purge
```

---

## Layout do repositório

```
megalog/
├── README.md                      # você está aqui
├── pyproject.toml                 # uv/pip deps
├── megalog/                       # pacote Python (~5.300 LOC)
│   ├── config.py                  # Pydantic Settings
│   ├── ingest/                    # receiver + parser + processor + stream
│   ├── storage/                   # DuckDB store + IP registry + partitions + ops
│   ├── analytics/                 # classifier 9-cat + anomaly detector + analyzer
│   ├── api/                       # FastAPI + auth + 5 routers
│   ├── jobs/                      # archive + analyze + retention (systemd timers)
│   └── tools/                     # import_legacy
├── frontend/                      # Vue 3 SPA (~1.350 LOC)
│   ├── src/                       # main, router, lib, stores, components, pages
│   └── dist/                      # build de produção (servido pelo nginx)
├── deploy/                        # ~600 LOC
│   ├── install.sh, uninstall.sh
│   ├── nginx.conf.example
│   └── systemd/                   # 9 unit files (3 service + 3 oneshot + 3 timer)
├── tests/                         # 110 testes pytest
│   ├── unit/
│   └── integration/
├── docs/                          # documentação extensa
│   ├── ARCHITECTURE.md, INSTALL.md, OPERATIONS.md, TROUBLESHOOTING.md
│   ├── API.md, SECURITY.md, MANUAL.md
│   └── CHANGELOG-v4-to-v5.md
└── bench/                         # benchmark da Fase 0
```

---

## Suporte

- Bugs / sugestões: abrir issue no repositório
- Documentação completa: [docs/](docs/)

---

**Licença:** ver [LICENSE](LICENSE).
