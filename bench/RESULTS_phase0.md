# Fase 0 — Resultados do benchmark

Executado em 2026-04-27 contra 4 DBs SQLite v2 reais do servidor antigo (`/tmp/legacy/`), totalizando 10.368.421 linhas em 1.5 GB.

## Tamanho em disco

| Formato | Tamanho | Ratio vs SQLite cru | Ratio vs SQLite+gzip (cold atual) |
|---|---|---|---|
| SQLite + 7 índices (hot atual) | 1.5 GB | 1.0× | — |
| SQLite + gzip-6 (cold atual) | 450.4 MB | 3.32× | 1.0× |
| **DuckDB nativo (hot novo)** | **163.5 MB** | **9.14×** | 2.75× |
| **Parquet zstd-6 (cold novo)** | **89.7 MB** | **16.67×** | **5.02×** |

Bytes/linha: 151 → 9 (16.7× menor). Em CGNAT massivo, IPs/portas repetitivos comprimem dramaticamente bem em colunar.

## Busca forense pontual (`WHERE nat_ip_id=? AND ts BETWEEN ? AND ?`, janela 5 min, 30 amostras reais)

| Camada | Mediana | Máx |
|---|---|---|
| SQLite (com índice `idx_logs_nat_ip_id`) | 59-96 ms | 268-425 ms |
| **DuckDB nativo (hot)** | **11-13 ms** | 21-26 ms |
| **Parquet (cold) lido sem descompressão integral** | **13-15 ms** | 23-38 ms |

DuckDB sem índice B-tree explícito bate o SQLite indexado por **5-7× na mediana**. O caso "cold" é dramático no atual: descompressão de `.db.gz` para `/dev/shm` leva ~15s antes de qualquer query (medido aqui: gzip puro = 13-16s). Com Parquet, a query roda direto no arquivo comprimido em <40 ms.

## Top 10 IPs do dia

| Camada | Tempo |
|---|---|
| SQLite | 200-263 ms |
| **DuckDB nativo** | **35-44 ms** (~5-6×) |
| **Parquet** | **12-16 ms** (~15-20×) |

## Agregação multi-dia (4 dias, ganho-chave do registry global)

| Operação | Tempo |
|---|---|
| Top 10 IPs do período (4 Parquets via DuckDB) | **184 ms** |

Hoje isso exigiria abrir 4 SQLite, cada um com seu próprio dicionário `ip_addresses` (IDs incompatíveis), gerar tabela temporária de mapeamento e agregar manualmente em Python — operação de minutos. Com `ip_id` global + Parquet, vira query única sub-segundo.

## Performance de ingestão

| Métrica | Valor |
|---|---|
| Velocidade de ingestão DuckDB | ~700k-1M linhas/s (single-thread) |
| Tempo de export para Parquet | ~1s/dia (~3M linhas) |

## Decisão (gating do plano aprovado)

**PROSSEGUIR PARA FASE 1.** Os três eixos ultrapassaram o threshold de 3× com folga:

- Tamanho: 5× vs gzip atual
- Busca pontual: 5-7× vs SQLite hot
- Agregação: 15-20× vs SQLite com índice

Sem necessidade de avaliar PostgreSQL+TimescaleDB ou ClickHouse.
