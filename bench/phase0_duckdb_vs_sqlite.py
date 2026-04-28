"""
Fase 0 — benchmark de validação: DuckDB+Parquet vs SQLite+gzip.

Eixos medidos contra SQLite legado (schema v2, 7 índices):
  1. Tamanho em disco (DuckDB nativo, Parquet zstd) vs SQLite, vs SQLite gzipado
  2. Tempo de busca forense pontual (nat_ip + ts) — caso de uso primário
  3. Tempo de agregação multi-dia (top IPs do período) — caso onde o registry global brilha

Critério de bloqueio do plano: ganho >=3x em qualquer eixo dispara prosseguimento.
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import statistics
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

LEGACY_DIR = Path("/tmp/legacy")
WORK_DIR = Path("/tmp/megalog_bench")
LEGACY_DBS = sorted(LEGACY_DIR.glob("*.db"))


@contextmanager
def timed(label: str):
    t0 = time.perf_counter()
    yield (lambda: time.perf_counter() - t0)
    elapsed = time.perf_counter() - t0
    print(f"  [{elapsed * 1000:8.1f} ms] {label}")


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def gzip_size(src: Path) -> int:
    """Comprime para medir baseline cold storage atual (gzip nivel 6)."""
    dst = WORK_DIR / (src.name + ".gz")
    with src.open("rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
    return dst.stat().st_size


def ingest_to_duckdb(legacy_db: Path, duck_path: Path) -> tuple[int, float]:
    """
    Ingere um SQLite legado em DuckDB nativo, mantendo estrutura colunar.
    Retorna (linhas_inseridas, segundos).
    """
    if duck_path.exists():
        duck_path.unlink()
    t0 = time.perf_counter()
    con = duckdb.connect(str(duck_path))
    # Tipos enxutos (USMALLINT, UTINYINT, BOOLEAN) = compressao melhor que INTEGER
    con.execute("""
        CREATE TABLE logs (
            ts             UINTEGER NOT NULL,
            in_iface_id    USMALLINT,
            out_iface_id   USMALLINT,
            proto_id       UTINYINT,
            conn_state_id  USMALLINT,
            has_snat       BOOLEAN,
            src_ip_id      UINTEGER NOT NULL,
            src_port       USMALLINT,
            dst_ip_id      UINTEGER NOT NULL,
            dst_port       USMALLINT,
            nat_ip_id      UINTEGER,
            nat_port       USMALLINT,
            pkt_len        USMALLINT,
            tcp_flags      VARCHAR,
            log_type       VARCHAR
        );
        CREATE TABLE ip_addresses (id INTEGER PRIMARY KEY, ip UINTEGER UNIQUE);
        CREATE TABLE interfaces (id INTEGER PRIMARY KEY, name VARCHAR);
        CREATE TABLE protocols (id INTEGER PRIMARY KEY, name VARCHAR);
        CREATE TABLE conn_states (id INTEGER PRIMARY KEY, name VARCHAR);
    """)
    # DuckDB le SQLite via extensao oficial — sem dependencia de SQL bridge externo
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{legacy_db}' AS src (TYPE SQLITE, READ_ONLY);")
    # Copia dicionarios primeiro (FK alvo)
    con.execute("INSERT INTO interfaces SELECT id, name FROM src.interfaces;")
    con.execute("INSERT INTO protocols SELECT id, name FROM src.protocols;")
    con.execute("INSERT INTO conn_states SELECT id, name FROM src.conn_states;")
    con.execute("INSERT INTO ip_addresses SELECT id, ip FROM src.ip_addresses;")
    # logs com ORDER BY (ts, src_ip_id) garante data locality + zone maps eficazes
    con.execute("""
        INSERT INTO logs
        SELECT
            ts, in_iface_id, out_iface_id, proto_id, conn_state_id,
            CAST(COALESCE(has_snat, 0) AS BOOLEAN),
            src_ip_id, src_port, dst_ip_id, dst_port, nat_ip_id, nat_port,
            pkt_len, tcp_flags, log_type
        FROM src.logs
        ORDER BY ts, src_ip_id;
    """)
    con.execute("DETACH src;")
    n = con.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    # CHECKPOINT obriga DuckDB a persistir tudo (sem WAL pendurado inflando o tamanho)
    con.execute("CHECKPOINT;")
    con.close()
    return n, time.perf_counter() - t0


def export_to_parquet(duck_path: Path, parquet_path: Path) -> float:
    if parquet_path.exists():
        parquet_path.unlink()
    t0 = time.perf_counter()
    con = duckdb.connect(str(duck_path), read_only=True)
    con.execute(f"""
        COPY (SELECT * FROM logs)
        TO '{parquet_path}'
        (FORMAT PARQUET, COMPRESSION 'zstd', COMPRESSION_LEVEL 6,
         ROW_GROUP_SIZE 100000);
    """)
    con.close()
    return time.perf_counter() - t0


def bench_search_pointwise(con, label: str, samples: list[tuple[int, int, int]]) -> None:
    """
    Simula busca forense: dado nat_ip + janela de ts, retorna o registro.
    Mede tempo medio sobre N amostras reais sorteadas do dataset.
    """
    times = []
    for nat_ip_id, ts_start, ts_end in samples:
        t0 = time.perf_counter()
        con.execute(
            """
            SELECT ts, src_ip_id, src_port, dst_ip_id, dst_port, nat_port
            FROM logs
            WHERE nat_ip_id = ? AND ts BETWEEN ? AND ?
            LIMIT 100
            """,
            [nat_ip_id, ts_start, ts_end],
        ).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    print(
        f"  [{label}] busca pontual: "
        f"min={min(times):6.1f}ms  median={statistics.median(times):6.1f}ms  "
        f"max={max(times):6.1f}ms  (n={len(times)})"
    )


def bench_topip_aggregate(con, label: str) -> None:
    t0 = time.perf_counter()
    rows = con.execute(
        """
        SELECT src_ip_id, COUNT(*) AS hits
        FROM logs
        GROUP BY src_ip_id
        ORDER BY hits DESC
        LIMIT 10
        """
    ).fetchall()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  [{label}] top 10 IPs (1 dia):  {elapsed:7.1f} ms  -> {rows[0]}")


def bench_topip_sqlite(sqlite_db: Path) -> None:
    con = sqlite3.connect(sqlite_db)
    con.execute("PRAGMA cache_size=-262144")  # 256 MB, igual ao processor atual
    t0 = time.perf_counter()
    rows = con.execute(
        """
        SELECT src_ip_id, COUNT(*) AS hits
        FROM logs
        GROUP BY src_ip_id
        ORDER BY hits DESC
        LIMIT 10
        """
    ).fetchall()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  [SQLite] top 10 IPs (1 dia):  {elapsed:7.1f} ms  -> {rows[0]}")
    con.close()


def bench_search_sqlite(sqlite_db: Path, samples: list[tuple[int, int, int]]) -> None:
    con = sqlite3.connect(sqlite_db)
    con.execute("PRAGMA cache_size=-262144")
    times = []
    for nat_ip_id, ts_start, ts_end in samples:
        t0 = time.perf_counter()
        con.execute(
            """
            SELECT ts, src_ip_id, src_port, dst_ip_id, dst_port, nat_port
            FROM logs
            WHERE nat_ip_id = ? AND ts BETWEEN ? AND ?
            LIMIT 100
            """,
            [nat_ip_id, ts_start, ts_end],
        ).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    con.close()
    print(
        f"  [SQLite] busca pontual: "
        f"min={min(times):6.1f}ms  median={statistics.median(times):6.1f}ms  "
        f"max={max(times):6.1f}ms  (n={len(times)})"
    )


def sample_search_keys(legacy_db: Path, n: int = 30) -> list[tuple[int, int, int]]:
    """
    Sorteia n triplas (nat_ip_id, ts_start, ts_end) reais do banco.
    Cada janela e' de 5 minutos centrada num ts random.
    """
    con = sqlite3.connect(legacy_db)
    rows = con.execute(
        """
        SELECT nat_ip_id, ts FROM logs
        WHERE nat_ip_id IS NOT NULL
        ORDER BY RANDOM() LIMIT ?
        """,
        [n],
    ).fetchall()
    con.close()
    return [(nat_ip, ts - 150, ts + 150) for nat_ip, ts in rows]


def main() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Legacy DBs encontrados: {len(LEGACY_DBS)}")
    for p in LEGACY_DBS:
        print(f"  - {p.name}: {fmt_bytes(p.stat().st_size)}")
    print()

    results = []
    for legacy_db in LEGACY_DBS:
        date = legacy_db.stem
        print(f"=== {date} ===")

        sqlite_size = legacy_db.stat().st_size
        print(f"  SQLite (com indices) : {fmt_bytes(sqlite_size)}")

        with timed(f"gzip {legacy_db.name}"):
            gz_size = gzip_size(legacy_db)
        print(f"  SQLite + gzip-6      : {fmt_bytes(gz_size)}  (ratio {sqlite_size / gz_size:.2f}x)")

        duck_path = WORK_DIR / f"{date}.duckdb"
        n, ingest_secs = ingest_to_duckdb(legacy_db, duck_path)
        duck_size = duck_path.stat().st_size
        print(
            f"  DuckDB nativo        : {fmt_bytes(duck_size)}  "
            f"(ratio {sqlite_size / duck_size:.2f}x)  ingest {ingest_secs:.1f}s  ({n:,} linhas)"
        )

        parquet_path = WORK_DIR / f"{date}.parquet"
        export_secs = export_to_parquet(duck_path, parquet_path)
        pq_size = parquet_path.stat().st_size
        print(
            f"  Parquet zstd-6       : {fmt_bytes(pq_size)}  "
            f"(ratio {sqlite_size / pq_size:.2f}x vs SQLite, "
            f"{gz_size / pq_size:.2f}x vs SQLite+gzip)  export {export_secs:.1f}s"
        )

        # benchmark de busca: amostras reais (consistentes entre SQLite e DuckDB)
        samples = sample_search_keys(legacy_db, n=30)

        bench_search_sqlite(legacy_db, samples)

        con = duckdb.connect(str(duck_path), read_only=True)
        bench_search_pointwise(con, "DuckDB nativo", samples)
        con.close()

        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW logs AS SELECT * FROM read_parquet('{parquet_path}');")
        bench_search_pointwise(con, "Parquet (cold)", samples)
        con.close()

        bench_topip_sqlite(legacy_db)
        con = duckdb.connect(str(duck_path), read_only=True)
        bench_topip_aggregate(con, "DuckDB nativo")
        con.close()
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW logs AS SELECT * FROM read_parquet('{parquet_path}');")
        bench_topip_aggregate(con, "Parquet (cold)")
        con.close()

        results.append({
            "date": date,
            "rows": n,
            "sqlite_size": sqlite_size,
            "sqlite_gz_size": gz_size,
            "duckdb_size": duck_size,
            "parquet_size": pq_size,
        })
        print()

    # multi-dia (ganho-chave do registry global de IPs)
    print("=== AGREGADO MULTI-DIA (4 dias) ===")
    parquets = [str(WORK_DIR / f"{r['date']}.parquet") for r in results]
    con = duckdb.connect(":memory:")
    con.execute(
        f"CREATE VIEW logs_all AS SELECT * FROM read_parquet({parquets!r});"
    )
    t0 = time.perf_counter()
    rows = con.execute(
        """
        SELECT src_ip_id, COUNT(*) AS hits, COUNT(DISTINCT nat_ip_id) AS distinct_nat
        FROM logs_all
        GROUP BY src_ip_id
        ORDER BY hits DESC
        LIMIT 10
        """
    ).fetchall()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  [Parquet 4 arquivos] top 10 IPs do periodo: {elapsed:7.1f} ms")
    print(f"    -> top: src_ip_id={rows[0][0]}  hits={rows[0][1]:,}  distinct_nat={rows[0][2]}")

    # totais
    total_sqlite = sum(r["sqlite_size"] for r in results)
    total_gz = sum(r["sqlite_gz_size"] for r in results)
    total_duck = sum(r["duckdb_size"] for r in results)
    total_pq = sum(r["parquet_size"] for r in results)
    total_rows = sum(r["rows"] for r in results)
    print()
    print("=== TOTAIS ===")
    print(f"  Linhas processadas      : {total_rows:>14,}")
    print(f"  SQLite (com indices)    : {fmt_bytes(total_sqlite):>14}")
    print(
        f"  SQLite + gzip           : {fmt_bytes(total_gz):>14}  "
        f"(ratio {total_sqlite / total_gz:.2f}x vs SQLite cru)"
    )
    print(
        f"  DuckDB nativo           : {fmt_bytes(total_duck):>14}  "
        f"(ratio {total_sqlite / total_duck:.2f}x vs SQLite cru)"
    )
    print(
        f"  Parquet zstd            : {fmt_bytes(total_pq):>14}  "
        f"(ratio {total_sqlite / total_pq:.2f}x vs SQLite cru, "
        f"{total_gz / total_pq:.2f}x vs SQLite+gzip)"
    )
    print(f"  Bytes/linha SQLite      : {total_sqlite / total_rows:>14.2f}")
    print(f"  Bytes/linha Parquet     : {total_pq / total_rows:>14.2f}")


if __name__ == "__main__":
    main()
