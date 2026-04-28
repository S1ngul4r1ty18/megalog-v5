"""Lifecycle das partições diárias: hot → cold + retenção.

Estrutura de diretórios:
  hot_storage_dir/YYYY-MM-DD.duckdb        (DuckDB nativo, append durante o dia)
  cold_storage_dir/YYYY-MM-DD.parquet      (snapshot zstd, read-only)

Operações:
  - `archive_day(date)`: lê o DuckDB do dia, escreve Parquet zstd em cold,
    apaga o DuckDB. Idempotente: se Parquet já existe, não re-faz.
  - `move_old_to_cold(retention_days)`: arquiva todos os DBs com mtime
    mais antigo que N dias.
  - `delete_expired(delete_after_days)`: remove Parquets antigos.
  - `count_logs(path)`: conta linhas em DuckDB ou Parquet (rápido,
    sem carregar o arquivo todo).
  - `open_for_query(date_str)`: abre DuckDB :memory: com view apontando
    para hot ou cold (transparente para a camada de busca).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb  # noqa: F401  re-export para callers

log = logging.getLogger("megalog.partitions")

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(duckdb|parquet)$")


@dataclass(slots=True)
class PartitionLocation:
    date_str: str
    path: Path
    kind: str           # "hot" | "cold"
    format: str         # "duckdb" | "parquet"
    size_bytes: int


def _scan(directory: Path, kind: str) -> dict[str, PartitionLocation]:
    if not directory.exists():
        return {}
    out: dict[str, PartitionLocation] = {}
    for p in directory.iterdir():
        if not p.is_file():
            continue
        m = _DATE_RE.match(p.name)
        if not m:
            continue
        date_str, fmt = m.group(1), m.group(2)
        out[date_str] = PartitionLocation(date_str, p, kind, fmt, p.stat().st_size)
    return out


def list_all(hot_dir: Path, cold_dir: Path) -> dict[str, PartitionLocation]:
    """Cold é prioritário (depois de archive, ele é a verdade); hot sobrescreve."""
    cold = _scan(cold_dir, "cold")
    hot  = _scan(hot_dir, "hot")
    return {**cold, **hot}


def count_logs(loc: PartitionLocation) -> int:
    """Conta linhas usando open_for_query (que já tem retry em lock conflict)."""
    con = open_for_query(loc)
    try:
        return int(con.execute("SELECT COUNT(*) FROM logs").fetchone()[0])
    finally:
        con.close()


def open_for_query(
    loc: PartitionLocation,
    *,
    threads: int = 2,
    retries: int = 8,
    retry_delay: float = 0.5,
) -> duckdb.DuckDBPyConnection:
    """
    Abre uma conexão DuckDB :memory: com `logs` apontando para a partição.
    Transparente para o chamador: hot ou cold resultam na mesma view.

    Para o DuckDB hot do dia atual, o processor segura o write-lock entre
    flushes — esta função tenta com retry linear (delay constante) para
    cobrir a janela de `duckdb_close_interval_seconds` em que o processor
    libera o lock. Total max wait ≈ retries × retry_delay = 4s.
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        con = None
        try:
            con = duckdb.connect(":memory:")
            con.execute(f"PRAGMA threads={threads}")
            if loc.format == "duckdb":
                con.execute(f"ATTACH '{loc.path}' AS src (READ_ONLY)")
                con.execute("CREATE VIEW logs AS SELECT * FROM src.logs")
            else:
                safe_path = str(loc.path).replace("'", "''")
                con.execute(
                    f"CREATE VIEW logs AS SELECT * FROM read_parquet('{safe_path}')"
                )
            return con
        except duckdb.IOException as e:
            last_err = e
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
            if "Conflicting lock" not in str(e) or attempt == retries - 1:
                raise
            time.sleep(retry_delay)
    raise last_err  # type: ignore[misc]


def archive_day(
    date_str: str,
    hot_dir: Path,
    cold_dir: Path,
    *,
    compression: str = "zstd",
    compression_level: int = 6,
    row_group_size: int = 100_000,
    delete_hot_after: bool = True,
) -> Path | None:
    """
    Hot DuckDB → Cold Parquet (zstd). Retorna o Parquet criado, ou None
    se não havia hot a arquivar. Idempotente.
    """
    src = hot_dir / f"{date_str}.duckdb"
    dst = cold_dir / f"{date_str}.parquet"
    if not src.exists():
        if dst.exists():
            log.info("archive: %s já está em cold, nada a fazer", date_str)
            return dst
        log.info("archive: %s não existe nem em hot nem em cold", date_str)
        return None
    if dst.exists():
        log.info("archive: %s já existe em cold (Parquet), removendo hot", date_str)
        if delete_hot_after:
            src.unlink()
        return dst

    cold_dir.mkdir(parents=True, exist_ok=True)
    tmp = cold_dir / f".{date_str}.parquet.tmp"
    if tmp.exists():
        tmp.unlink()

    con = duckdb.connect(":memory:")
    dicts: dict[str, dict[int, str]] = {}
    try:
        con.execute(f"ATTACH '{src}' AS hot (READ_ONLY)")
        con.execute(
            f"COPY (SELECT * FROM hot.logs ORDER BY ts, src_ip_id) "
            f"TO '{tmp}' "
            f"(FORMAT PARQUET, COMPRESSION '{compression}', "
            f" COMPRESSION_LEVEL {compression_level}, "
            f" ROW_GROUP_SIZE {row_group_size})"
        )
        # Snapshot dos dicts auxiliares — Parquet não os preserva, então
        # gravamos um sidecar para a leitura em cold conseguir resolver os IDs.
        for table in ("interfaces", "protocols", "conn_states"):
            try:
                rows = con.execute(f"SELECT id, name FROM hot.{table}").fetchall()
                dicts[table] = {int(i): n for i, n in rows}
            except duckdb.Error:
                dicts[table] = {}
        con.execute("DETACH hot")
    finally:
        con.close()

    tmp.replace(dst)
    sidecar = cold_dir / f"{date_str}.dicts.json"
    sidecar.write_text(json.dumps(dicts, ensure_ascii=False))
    if delete_hot_after:
        src.unlink()
    log.info(
        "archive: %s → %s (%.1f MB) + sidecar %s",
        src.name, dst.name, dst.stat().st_size / 1024 / 1024, sidecar.name,
    )
    return dst


def load_partition_dicts(loc: PartitionLocation) -> dict[str, dict[int, str]]:
    """Carrega interfaces/protocols/conn_states para a partição.

    Hot (DuckDB): consulta as tabelas auxiliares.
    Cold (Parquet): lê o sidecar JSON. Se ausente (parquet antigo, pré-sidecar),
    devolve dicts vazios — search() degrada para None nos campos.
    """
    out = {"interfaces": {}, "protocols": {}, "conn_states": {}}
    if loc.format == "duckdb":
        con = open_for_query(loc)
        try:
            for table in ("interfaces", "protocols", "conn_states"):
                try:
                    for r in con.execute(f"SELECT id, name FROM src.{table}").fetchall():
                        out[table][r[0]] = r[1]
                except duckdb.Error:
                    pass
        finally:
            con.close()
    else:
        sidecar = loc.path.parent / f"{loc.path.stem}.dicts.json"
        if sidecar.exists():
            data = json.loads(sidecar.read_text())
            for k in out:
                out[k] = {int(i): n for i, n in data.get(k, {}).items()}
    return out


def move_old_to_cold(
    hot_dir: Path,
    cold_dir: Path,
    *,
    retention_days: int,
) -> list[str]:
    """
    Arquiva todos os DuckDB de hot cujo mtime é mais antigo que `retention_days`.
    Hoje (today) e ontem (today-1) ficam preservados em hot mesmo se cair na borda.
    """
    if not hot_dir.exists():
        return []
    now = time.time()
    today = date.today()
    archived: list[str] = []
    for p in hot_dir.iterdir():
        m = _DATE_RE.match(p.name)
        if not m or m.group(2) != "duckdb":
            continue
        date_str = m.group(1)
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - d).days <= retention_days:
            continue
        if archive_day(date_str, hot_dir, cold_dir):
            archived.append(date_str)
    return archived


def delete_expired(
    cold_dir: Path,
    *,
    delete_after_days: int,
) -> list[str]:
    """Remove Parquets de cold mais antigos que `delete_after_days`. 0 = nunca."""
    if delete_after_days <= 0 or not cold_dir.exists():
        return []
    today = date.today()
    deleted: list[str] = []
    for p in cold_dir.iterdir():
        m = _DATE_RE.match(p.name)
        if not m or m.group(2) != "parquet":
            continue
        date_str = m.group(1)
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - d).days > delete_after_days:
            p.unlink()
            deleted.append(date_str)
    return deleted
