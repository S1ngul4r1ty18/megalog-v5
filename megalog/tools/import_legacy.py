"""Importa SQLite legados (.db ou .db.gz) do MegaLog v4 para Parquet v5.

Detecta 4 schemas conhecidos automaticamente:
  - **v2** (atual no v4): IPs em FK `ip_addresses`, dicts `interfaces`/`protocols`/`conn_states`,
    timestamp INTEGER. É o schema dos `/tmp/legacy/*.db`.
  - **C** (unificado v4): dicts `interfaces`/`protocols`/`conn_states` sem prefixo,
    IPs INTEGER inline em `logs.{src,dst,nat}_ip`, tabela `db_meta` com metadados de
    migração. Resultado de uma normalização A/B → C feita no servidor v4 antigo.
  - **B** (intermediário): IPs como INTEGER, dicts em `d_interfaces`/`d_protocols`/`d_states`,
    timestamp INTEGER.
  - **A** (mais antigo): IPs como TEXT, dicts inline (nomes nas próprias linhas),
    timestamp TEXT. Adaptado de [v4 migrate_import_db.py](../../../megalog/migrate_import_db.py).

Pipeline:
  1. Descomprime `.db.gz` para tmpdir, se necessário.
  2. Detecta schema, extrai data do nome (`YYYY-MM-DD.db`).
  3. Pula se Parquet em `cold/{date}.parquet` já existe (idempotente).
  4. Pré-carrega dicionário local de IPs e mapeia para `ip_id` global (registra
     no `ip_registry` SQLite WAL — mesmo registry usado pelo processor).
  5. Escreve Parquet zstd via DuckDB com schema v5.
  6. Atualiza `daily_stats` no `OperationalStore`.

Uso:
  python -m megalog.tools.import_legacy /backup/old/2026-*.db
  python -m megalog.tools.import_legacy /backup/old/        # diretório
  python -m megalog.tools.import_legacy --src /tmp/legacy/  # equivalente
"""
from __future__ import annotations

import argparse
import gzip
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from megalog.config import Settings, get_settings
from megalog.storage.ip_registry import IpRegistry, ip_to_int
from megalog.storage.operational import OperationalStore

log = logging.getLogger("megalog.tools.import_legacy")

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ── Detecção e descompressão ────────────────────────────────────────────────


def detect_schema(con: sqlite3.Connection) -> str:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ip_addresses" in tables:
        return "v2"
    if "d_interfaces" in tables or "d_protocols" in tables:
        return "B"
    if "db_meta" in tables and {"interfaces", "protocols", "conn_states"}.issubset(tables):
        return "C"
    return "A"


def detect_date(filename: str) -> str:
    """Extrai 'YYYY-MM-DD' do nome do arquivo."""
    m = _DATE_RE.search(Path(filename).name)
    if not m:
        raise ValueError(f"Não consegui detectar data (YYYY-MM-DD) em: {filename}")
    return m.group(1)


@contextmanager
def open_legacy_sqlite(path: Path) -> Iterator[tuple[sqlite3.Connection, Path]]:
    """
    Abre `.db` ou `.db.gz` para leitura.

    SEMPRE copia para tmpdir gravável pelo processo, porque:
      - `.db.gz` precisa ser descomprimido
      - `.db` original pode estar em diretório read-only para o usuário do
        serviço (ex: /tmp/legacy montado como root); o DuckDB sqlite scanner
        ignora `READ_ONLY` para fins de criação de `-shm`/`-wal` em alguns
        cenários e exige permissão de escrita no diretório do arquivo
      - garante consistência de comportamento entre `.db` e `.db.gz`

    Yield (conexão_sqlite_readonly, path_efetivo_no_tmpdir). Limpa tmp ao sair.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="megalog_import_"))
    try:
        target = tmpdir / path.name.removesuffix(".gz")
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as fi, target.open("wb") as fo:
                shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
        else:
            # cópia direta (binária) — preserva mtime
            shutil.copy2(path, target)

        # `immutable=1` impede o SQLite (Python) de tentar inicializar -shm/-wal.
        con = sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        try:
            yield con, target
        finally:
            con.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _new_duckdb(home_dir: Path) -> duckdb.DuckDBPyConnection:
    """Cria conexão DuckDB :memory: com home_directory configurado.

    DuckDB usa ~/ para cache de extensões instaladas (ex: extension `sqlite`).
    Como o serviço roda como usuário 'megalog' (sistema, sem /home), precisa
    de um diretório gravável explícito — usamos o state_dir.
    """
    home_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    safe_home = str(home_dir).replace("'", "''")
    con.execute(f"SET home_directory='{safe_home}'")
    return con


# ── Construção do Parquet (schema v5) ───────────────────────────────────────


_PARQUET_OPTS = "FORMAT PARQUET, COMPRESSION 'zstd', COMPRESSION_LEVEL 6, ROW_GROUP_SIZE 100000"


def _resolve_ip_dict(
    con: sqlite3.Connection, table: str, registry: IpRegistry,
) -> dict[int, int]:
    """Para schema v2: pré-resolve `local_id → global_id` lendo `ip_addresses` local."""
    rows = con.execute(f"SELECT id, ip FROM {table}").fetchall()
    out: dict[int, int] = {}
    for local_id, ip_int in rows:
        # SQLite trata uint32 como signed; normaliza
        ip_uint = ip_int & 0xFFFFFFFF
        out[local_id] = registry.get_or_create(ip_uint)
    registry.flush_hits()
    return out


def _write_ip_map_to_duckdb(con: duckdb.DuckDBPyConnection, ip_map: dict[int, int]) -> None:
    """Cria tabela temp `ip_map(local_id, global_id)` no DuckDB e popula."""
    con.execute("CREATE TEMP TABLE ip_map (local_id BIGINT, global_id BIGINT)")
    if not ip_map:
        return
    # Bulk insert via VALUES (mais rápido que executemany para 30k+ linhas)
    rows = list(ip_map.items())
    chunk = 5000
    for i in range(0, len(rows), chunk):
        slice_ = rows[i:i + chunk]
        values = ",".join(f"({k},{v})" for k, v in slice_)
        con.execute(f"INSERT INTO ip_map VALUES {values}")
    con.execute("CREATE INDEX idx_ip_map_local ON ip_map(local_id)")


def _import_v2(
    src_db: Path, src_con: sqlite3.Connection, cold_path: Path,
    registry: IpRegistry, home_dir: Path,
) -> int:
    """Schema v2: tem ip_addresses e dicts próprios."""
    # Pre-resolve IP dict local → global
    ip_map = _resolve_ip_dict(src_con, "ip_addresses", registry)
    log.info("  %d IPs locais mapeados → global registry", len(ip_map))

    safe_out = str(cold_path).replace("'", "''")
    safe_src = str(src_db).replace("'", "''")

    con = _new_duckdb(home_dir)
    try:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{safe_src}' AS src (TYPE SQLITE, READ_ONLY)")
        _write_ip_map_to_duckdb(con, ip_map)

        n = con.execute("SELECT COUNT(*) FROM src.logs").fetchone()[0]

        con.execute(f"""
            COPY (
                SELECT
                    CAST(l.ts AS UINTEGER)                         AS ts,
                    CAST(l.in_iface_id   AS USMALLINT)             AS in_iface_id,
                    CAST(l.out_iface_id  AS USMALLINT)             AS out_iface_id,
                    CAST(l.proto_id      AS UTINYINT)              AS proto_id,
                    CAST(l.conn_state_id AS USMALLINT)             AS conn_state_id,
                    CAST(COALESCE(l.has_snat, 0) AS BOOLEAN)       AS has_snat,
                    CAST(COALESCE(src_m.global_id, 0) AS UINTEGER) AS src_ip_id,
                    CAST(l.src_port AS USMALLINT)                  AS src_port,
                    CAST(COALESCE(dst_m.global_id, 0) AS UINTEGER) AS dst_ip_id,
                    CAST(l.dst_port AS USMALLINT)                  AS dst_port,
                    CAST(nat_m.global_id AS UINTEGER)              AS nat_ip_id,
                    CAST(l.nat_port AS USMALLINT)                  AS nat_port,
                    CAST(l.pkt_len  AS USMALLINT)                  AS pkt_len,
                    l.tcp_flags                                    AS tcp_flags,
                    COALESCE(l.log_type, 'nat')                    AS log_type
                FROM src.logs l
                LEFT JOIN ip_map src_m ON src_m.local_id = l.src_ip_id
                LEFT JOIN ip_map dst_m ON dst_m.local_id = l.dst_ip_id
                LEFT JOIN ip_map nat_m ON nat_m.local_id = l.nat_ip_id
                ORDER BY l.ts, src_m.global_id
            )
            TO '{safe_out}' ({_PARQUET_OPTS})
        """)
    finally:
        con.close()
    return n


def _import_b(
    src_db: Path, src_con: sqlite3.Connection, cold_path: Path,
    registry: IpRegistry, home_dir: Path,
) -> int:
    """Schema B: IPs INTEGER inline, dicts d_interfaces/d_protocols/d_states."""
    # IPs no schema B já são INTEGER nas próprias colunas — sem ip_addresses,
    # então registramos cada IP único encontrado.
    log.info("  pré-resolvendo IPs únicos do schema B…")
    ip_int_set: set[int] = set()
    for col in ("src_ip_priv", "dst_ip", "nat_ip_pub"):
        for r in src_con.execute(
            f"SELECT DISTINCT {col} FROM logs WHERE {col} IS NOT NULL"
        ):
            ip_int_set.add(r[0] & 0xFFFFFFFF)
    ip_map = {ip_int: registry.get_or_create(ip_int) for ip_int in ip_int_set}
    registry.flush_hits()
    log.info("  %d IPs únicos mapeados", len(ip_map))

    safe_out = str(cold_path).replace("'", "''")
    safe_src = str(src_db).replace("'", "''")

    con = _new_duckdb(home_dir)
    try:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{safe_src}' AS src (TYPE SQLITE, READ_ONLY)")
        _write_ip_map_to_duckdb(con, ip_map)

        n = con.execute("SELECT COUNT(*) FROM src.logs").fetchone()[0]

        con.execute(f"""
            COPY (
                SELECT
                    CAST(l.timestamp AS UINTEGER)                   AS ts,
                    CAST(l.interface_in_id  AS USMALLINT)           AS in_iface_id,
                    CAST(l.interface_out_id AS USMALLINT)           AS out_iface_id,
                    CAST(l.protocol_id      AS UTINYINT)            AS proto_id,
                    CAST(l.state_id         AS USMALLINT)           AS conn_state_id,
                    FALSE                                           AS has_snat,
                    CAST(COALESCE(src_m.global_id, 0) AS UINTEGER)  AS src_ip_id,
                    CAST(l.src_port_priv AS USMALLINT)              AS src_port,
                    CAST(COALESCE(dst_m.global_id, 0) AS UINTEGER)  AS dst_ip_id,
                    CAST(l.dst_port AS USMALLINT)                   AS dst_port,
                    CAST(nat_m.global_id AS UINTEGER)               AS nat_ip_id,
                    CAST(l.nat_port_pub AS USMALLINT)               AS nat_port,
                    CAST(NULL AS USMALLINT)                         AS pkt_len,
                    CAST(NULL AS VARCHAR)                           AS tcp_flags,
                    'nat'                                           AS log_type
                FROM src.logs l
                LEFT JOIN ip_map src_m ON src_m.local_id = l.src_ip_priv
                LEFT JOIN ip_map dst_m ON dst_m.local_id = l.dst_ip
                LEFT JOIN ip_map nat_m ON nat_m.local_id = l.nat_ip_pub
                ORDER BY l.timestamp, src_m.global_id
            )
            TO '{safe_out}' ({_PARQUET_OPTS})
        """)
    finally:
        con.close()
    return n


def _import_c(
    src_db: Path, src_con: sqlite3.Connection, cold_path: Path,
    registry: IpRegistry, home_dir: Path,
) -> int:
    """Schema C: unificado v4. IPs INTEGER inline (sem `ip_addresses`),
    dicts `interfaces`/`protocols`/`conn_states` (sem prefixo `d_`),
    tabela `db_meta`. Layout de coluna do `logs` já alinhado ao v5.
    """
    log.info("  pré-resolvendo IPs únicos do schema C…")
    ip_int_set: set[int] = set()
    for col in ("src_ip", "dst_ip", "nat_ip"):
        for r in src_con.execute(
            f"SELECT DISTINCT {col} FROM logs WHERE {col} IS NOT NULL AND {col} != 0"
        ):
            ip_int_set.add(r[0] & 0xFFFFFFFF)
    ip_map = {ip_int: registry.get_or_create(ip_int) for ip_int in ip_int_set}
    registry.flush_hits()
    log.info("  %d IPs únicos mapeados", len(ip_map))

    safe_out = str(cold_path).replace("'", "''")
    safe_src = str(src_db).replace("'", "''")

    con = _new_duckdb(home_dir)
    try:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{safe_src}' AS src (TYPE SQLITE, READ_ONLY)")
        _write_ip_map_to_duckdb(con, ip_map)

        n = con.execute("SELECT COUNT(*) FROM src.logs").fetchone()[0]

        con.execute(f"""
            COPY (
                SELECT
                    CAST(l.ts AS UINTEGER)                          AS ts,
                    CAST(l.in_iface_id   AS USMALLINT)              AS in_iface_id,
                    CAST(l.out_iface_id  AS USMALLINT)              AS out_iface_id,
                    CAST(l.proto_id      AS UTINYINT)               AS proto_id,
                    CAST(l.conn_state_id AS USMALLINT)              AS conn_state_id,
                    CAST(COALESCE(l.has_snat, 0) AS BOOLEAN)        AS has_snat,
                    CAST(COALESCE(src_m.global_id, 0) AS UINTEGER)  AS src_ip_id,
                    CAST(l.src_port AS USMALLINT)                   AS src_port,
                    CAST(COALESCE(dst_m.global_id, 0) AS UINTEGER)  AS dst_ip_id,
                    CAST(l.dst_port AS USMALLINT)                   AS dst_port,
                    CAST(nat_m.global_id AS UINTEGER)               AS nat_ip_id,
                    CAST(l.nat_port AS USMALLINT)                   AS nat_port,
                    CAST(l.pkt_len AS USMALLINT)                    AS pkt_len,
                    l.tcp_flags                                     AS tcp_flags,
                    COALESCE(l.log_type, 'nat')                     AS log_type
                FROM src.logs l
                LEFT JOIN ip_map src_m ON src_m.local_id = l.src_ip
                LEFT JOIN ip_map dst_m ON dst_m.local_id = l.dst_ip
                LEFT JOIN ip_map nat_m ON nat_m.local_id = l.nat_ip
                ORDER BY l.ts, src_m.global_id
            )
            TO '{safe_out}' ({_PARQUET_OPTS})
        """)
    finally:
        con.close()
    return n


def _import_a(
    src_db: Path, src_con: sqlite3.Connection, cold_path: Path,
    registry: IpRegistry, home_dir: Path,
) -> int:
    """Schema A: IPs TEXT, timestamp TEXT, dicts inline (nomes em cada linha).

    Strategy: Python loop com batch de 50k linhas, registra IPs e dicts ad-hoc,
    insere em DuckDB :memory:, no fim COPY TO PARQUET. Mais lento que A/v2 mas
    formato mais raro (esperado em DBs antigos).
    """
    from datetime import datetime, timezone

    iface_seq = {"_next": 1}
    proto_seq = {"_next": 1}

    def get_iface_id(name: str | None) -> int | None:
        if not name: return None
        if name in iface_seq: return iface_seq[name]
        i = iface_seq["_next"]; iface_seq["_next"] = i + 1
        iface_seq[name] = i
        return i

    def get_proto_id(name: str | None) -> int | None:
        if not name: return None
        if name in proto_seq: return proto_seq[name]
        i = proto_seq["_next"]; proto_seq["_next"] = i + 1
        proto_seq[name] = i
        return i

    def parse_ts(s: str | None) -> int:
        if not s: return 0
        try:
            return int(datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
                       .replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            try:
                return int(datetime.fromisoformat(s.strip()).timestamp())
            except Exception:
                return 0

    duck = _new_duckdb(home_dir)
    duck.execute("""
        CREATE TABLE logs (
            ts UINTEGER, in_iface_id USMALLINT, out_iface_id USMALLINT,
            proto_id UTINYINT, conn_state_id USMALLINT, has_snat BOOLEAN,
            src_ip_id UINTEGER, src_port USMALLINT,
            dst_ip_id UINTEGER, dst_port USMALLINT,
            nat_ip_id UINTEGER, nat_port USMALLINT,
            pkt_len USMALLINT, tcp_flags VARCHAR, log_type VARCHAR
        )
    """)
    insert_sql = "INSERT INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"

    BATCH = 50_000
    offset, total = 0, 0
    while True:
        rows = src_con.execute(
            f"SELECT * FROM logs LIMIT {BATCH} OFFSET {offset}"
        ).fetchall()
        if not rows:
            break
        batch_data = []
        for r in rows:
            src_ip = ip_to_int(r["src_ip_priv"]) if r["src_ip_priv"] else 0
            dst_ip = ip_to_int(r["dst_ip"])      if r["dst_ip"] else 0
            nat_ip = ip_to_int(r["nat_ip_pub"])  if r["nat_ip_pub"] else None
            batch_data.append((
                parse_ts(r["timestamp"]),
                get_iface_id(r["interface_in"]),
                get_iface_id(r["interface_out"]),
                get_proto_id(r["proto"]),
                None,           # conn_state_id
                False,          # has_snat
                registry.get_or_create(src_ip),
                r["src_port_priv"],
                registry.get_or_create(dst_ip),
                r["dst_port"],
                registry.get_or_create(nat_ip) if nat_ip else None,
                r["nat_port_pub"],
                None, None, "nat",
            ))
        duck.executemany(insert_sql, batch_data)
        total += len(rows)
        offset += BATCH
        log.info("  %d/? linhas processadas (schema A)", total)

    registry.flush_hits()

    safe_out = str(cold_path).replace("'", "''")
    duck.execute(f"""
        COPY (SELECT * FROM logs ORDER BY ts, src_ip_id)
        TO '{safe_out}' ({_PARQUET_OPTS})
    """)
    n = duck.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    duck.close()
    return n


# ── Orquestração ────────────────────────────────────────────────────────────


def import_file(
    src: Path,
    *,
    settings: Settings,
    registry: IpRegistry,
    ops: OperationalStore | None = None,
    overwrite: bool = False,
) -> dict:
    """Importa um único arquivo. Retorna dict com resultado/erro."""
    date_str = detect_date(src.name)
    cold_path = settings.cold_storage_dir / f"{date_str}.parquet"
    settings.cold_storage_dir.mkdir(parents=True, exist_ok=True)

    if cold_path.exists() and not overwrite:
        return {"src": str(src), "date": date_str, "skipped": True, "reason": "parquet já existe (use --overwrite)"}

    with open_legacy_sqlite(src) as (con, eff):
        schema = detect_schema(con)
        log.info("Importando %s (schema %s) → %s", src.name, schema, cold_path.name)
        t0 = time.time()
        # Source path efetivo (descomprimido se .gz). DuckDB ATTACH precisa do
        # arquivo real, não do pseudo-uri readonly do sqlite3.
        try:
            if schema == "v2":
                n = _import_v2(eff, con, cold_path, registry, settings.state_dir)
            elif schema == "B":
                n = _import_b(eff, con, cold_path, registry, settings.state_dir)
            elif schema == "C":
                n = _import_c(eff, con, cold_path, registry, settings.state_dir)
            else:
                n = _import_a(eff, con, cold_path, registry, settings.state_dir)
        except Exception as e:
            if cold_path.exists():
                cold_path.unlink()  # rollback parcial
            return {"src": str(src), "date": date_str, "error": str(e)}
        elapsed = time.time() - t0

    size = cold_path.stat().st_size
    if ops is not None:
        ops.upsert_daily_stats(date_str, n, size)

    return {
        "src": str(src), "date": date_str, "schema": schema,
        "rows": n, "output": str(cold_path),
        "size_bytes": size, "seconds": round(elapsed, 1),
        "rows_per_sec": int(n / elapsed) if elapsed > 0 else 0,
    }


def discover_files(targets: list[Path]) -> list[Path]:
    """Expande diretórios; aceita .db e .db.gz."""
    out: list[Path] = []
    for t in targets:
        if t.is_dir():
            out.extend(sorted([
                *t.glob("*.db"), *t.glob("*.db.gz"),
            ]))
        elif t.is_file():
            out.append(t)
    # Dedupe preservando ordem
    seen: set[Path] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def run(targets: list[Path], *, overwrite: bool = False) -> int:
    """Importa todos os arquivos. Retorna nº de arquivos importados com sucesso."""
    s = get_settings()
    s.state_dir.mkdir(parents=True, exist_ok=True)
    s.cold_storage_dir.mkdir(parents=True, exist_ok=True)

    files = discover_files(targets)
    if not files:
        log.warning("Nenhum arquivo .db/.db.gz encontrado em: %s", targets)
        return 0

    log.info("Encontrados %d arquivo(s) para importar", len(files))
    registry = IpRegistry(
        s.ip_registry_path,
        hot_top_n=s.ip_cache_hot_top_n,
        max_cache=s.ip_cache_max,
    )
    ops = OperationalStore(s.state_dir / "megalog.db")

    ok, skipped, errors = 0, 0, 0
    total_rows = 0
    for src in files:
        try:
            result = import_file(src, settings=s, registry=registry, ops=ops, overwrite=overwrite)
        except Exception:
            log.exception("Erro inesperado importando %s", src)
            errors += 1
            continue
        if result.get("error"):
            log.error("  FALHOU: %s — %s", src.name, result["error"])
            errors += 1
        elif result.get("skipped"):
            log.info("  PULADO: %s — %s", src.name, result["reason"])
            skipped += 1
        else:
            ok += 1
            total_rows += result["rows"]
            log.info(
                "  OK: %s — %d linhas em %ss (%d/s) → %s (%.1f MB)",
                src.name, result["rows"], result["seconds"],
                result["rows_per_sec"], Path(result["output"]).name,
                result["size_bytes"] / 1024 / 1024,
            )

    registry.close()
    log.info(
        "Resumo: %d ok / %d pulado / %d erro · %d linhas total importadas",
        ok, skipped, errors, total_rows,
    )
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa SQLite legados (v2, B, A) para Parquet v5.",
    )
    parser.add_argument(
        "targets", nargs="+", type=Path,
        help="Arquivos .db/.db.gz ou diretórios contendo eles",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Sobrescreve Parquet existente em cold (padrão: skip).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    n = run(args.targets, overwrite=args.overwrite)
    sys.exit(0 if n > 0 else 1)


if __name__ == "__main__":
    main()
