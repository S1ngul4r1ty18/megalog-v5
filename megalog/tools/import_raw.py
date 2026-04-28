"""Importa arquivos .raw (texto syslog Mikrotik) direto para Parquet v5.

Caminho rápido para "replay" de logs antigos em formato texto: pula
completamente o stage hot/DuckDB do processor (que tem overhead de
batch + WAL + lock + flush periódico). O processor faz ~2k linhas/s
nesse cenário; esta tool faz ~50k linhas/s single-thread, e escala
quase linear com `--workers` (cada arquivo = 1 dia, 100% paralelo).

Detecta data automaticamente do nome do arquivo (regex `YYYY-MM-DD`).

Pipeline por arquivo:
  1. Pula se Parquet em `cold/{date}.parquet` já existe (idempotente,
     mesmo critério do `import_legacy`).
  2. Lê linhas, joina continuação Mikrotik, `parse_line` puro.
  3. Atribui IDs locais para `interfaces`/`protocols`/`conn_states`
     (escopo por dia, igual ao hot DuckDB do processor).
  4. Resolve IPs únicos no `ip_registry` global (cada worker abre sua
     própria conexão SQLite WAL — múltiplos writers OK).
  5. Escreve Parquet zstd via DuckDB COPY.
  6. Escreve sidecar `cold/{date}.dicts.json` para resolver names dos
     dicts (mesmo formato do `archive_day` em partitions.py).
  7. Atualiza `daily_stats` no `OperationalStore` (no processo
     principal, após o worker retornar — evita contention).

Uso:
  python -m megalog.tools.import_raw /dados1/stream/2025-*.raw
  python -m megalog.tools.import_raw /backup/raw/ --workers 4
  python -m megalog.tools.import_raw arquivo.raw --overwrite
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pyarrow as pa

from megalog.config import Settings, get_settings
from megalog.ingest.parser import join_continuations, parse_line
from megalog.storage.ip_registry import IpRegistry, ip_to_int
from megalog.storage.operational import OperationalStore

log = logging.getLogger("megalog.tools.import_raw")

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_PARQUET_OPTS = (
    "FORMAT PARQUET, COMPRESSION 'zstd', COMPRESSION_LEVEL 6, "
    "ROW_GROUP_SIZE 100000"
)


def detect_date(filename: str) -> str:
    """Extrai 'YYYY-MM-DD' do nome do arquivo (qualquer posição)."""
    m = _DATE_RE.search(Path(filename).name)
    if not m:
        raise ValueError(f"Não consegui detectar data (YYYY-MM-DD) em: {filename}")
    return m.group(1)


def discover_files(targets: list[Path]) -> list[Path]:
    """Expande diretórios; aceita .raw."""
    out: list[Path] = []
    for t in targets:
        if t.is_dir():
            out.extend(sorted(t.glob("*.raw")))
        elif t.is_file():
            out.append(t)
    seen: set[Path] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def import_raw_file(
    src: Path,
    *,
    settings: Settings,
    overwrite: bool = False,
) -> dict:
    """Importa um único .raw → Parquet. Retorna dict com resultado/erro.

    Worker self-contained: abre/fecha sua própria conexão IpRegistry,
    para suportar execução em ProcessPoolExecutor.
    """
    date_str = detect_date(src.name)
    cold_path = settings.cold_storage_dir / f"{date_str}.parquet"
    sidecar_path = settings.cold_storage_dir / f"{date_str}.dicts.json"
    settings.cold_storage_dir.mkdir(parents=True, exist_ok=True)

    if cold_path.exists() and not overwrite:
        return {
            "src": str(src), "date": date_str, "skipped": True,
            "reason": "parquet já existe (use --overwrite)",
        }

    t0 = time.time()
    iface_dict: dict[str, int] = {}
    proto_dict: dict[str, int] = {}
    state_dict: dict[str, int] = {}

    def _intern(d: dict[str, int], name: str | None) -> int | None:
        if not name:
            return None
        v = d.get(name)
        if v is None:
            v = len(d) + 1
            d[name] = v
        return v

    # Acumula por coluna (não por tupla) — pyarrow constrói direto da lista
    # tipada, e fica MUITO mais rápido que executemany de tuplas.
    col_ts: list[int] = []
    col_in_iface: list[int | None] = []
    col_out_iface: list[int | None] = []
    col_proto: list[int | None] = []
    col_conn_state: list[int | None] = []
    col_has_snat: list[bool] = []
    col_src_ip: list[int] = []
    col_src_port: list[int | None] = []
    col_dst_ip: list[int] = []
    col_dst_port: list[int | None] = []
    col_nat_ip: list[int | None] = []
    col_nat_port: list[int | None] = []
    col_pkt_len: list[int | None] = []
    col_tcp_flags: list[str | None] = []
    col_log_type: list[str] = []

    parsed_count = 0
    error_count = 0

    with src.open("r", encoding="utf-8", errors="replace") as f:
        for joined in join_continuations(f):
            p = parse_line(joined)
            if p is None:
                error_count += 1
                continue
            parsed_count += 1
            col_ts.append(p.ts)
            col_in_iface.append(_intern(iface_dict, p.in_iface))
            col_out_iface.append(_intern(iface_dict, p.out_iface))
            col_proto.append(_intern(proto_dict, p.proto))
            col_conn_state.append(_intern(state_dict, p.conn_state))
            col_has_snat.append(p.has_snat)
            col_src_ip.append(ip_to_int(p.src_ip))
            col_src_port.append(p.src_port)
            col_dst_ip.append(ip_to_int(p.dst_ip))
            col_dst_port.append(p.dst_port)
            col_nat_ip.append(ip_to_int(p.nat_ip))
            col_nat_port.append(p.nat_port)
            col_pkt_len.append(p.pkt_len)
            col_tcp_flags.append(p.tcp_flags)
            col_log_type.append(p.log_type)

    if parsed_count == 0:
        return {
            "src": str(src), "date": date_str,
            "rows": 0, "errors": error_count,
            "warning": "nenhuma linha parseável",
            "seconds": round(time.time() - t0, 1),
        }

    # Resolve TODOS os IPs únicos do arquivo em UM SÓ batch — evita N
    # chamadas SQLite get_or_create (cada uma com fsync no WAL).
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    registry = IpRegistry(
        settings.ip_registry_path,
        hot_top_n=settings.ip_cache_hot_top_n,
        max_cache=settings.ip_cache_max,
    )
    try:
        ip_int_set: set[int] = set(col_src_ip) | set(col_dst_ip) | set(col_nat_ip)
        ip_int_set.discard(None)  # type: ignore[arg-type]
        ip_map = {ip: registry.get_or_create(ip) for ip in ip_int_set}
        registry.flush_hits()
    finally:
        registry.close()

    # Substitui colunas IP em uma passada (list comprehension em C).
    col_src_ip_id = [ip_map.get(ip, 0) for ip in col_src_ip]
    col_dst_ip_id = [ip_map.get(ip, 0) for ip in col_dst_ip]
    col_nat_ip_id = [ip_map[ip] if ip is not None else None for ip in col_nat_ip]

    # Tabela Arrow tipada — DuckDB lê em velocidade C via from_arrow.
    table = pa.table({
        "ts":            pa.array(col_ts,           type=pa.uint32()),
        "in_iface_id":   pa.array(col_in_iface,     type=pa.uint16()),
        "out_iface_id":  pa.array(col_out_iface,    type=pa.uint16()),
        "proto_id":      pa.array(col_proto,        type=pa.uint8()),
        "conn_state_id": pa.array(col_conn_state,   type=pa.uint16()),
        "has_snat":      pa.array(col_has_snat,     type=pa.bool_()),
        "src_ip_id":     pa.array(col_src_ip_id,    type=pa.uint32()),
        "src_port":      pa.array(col_src_port,     type=pa.uint16()),
        "dst_ip_id":     pa.array(col_dst_ip_id,    type=pa.uint32()),
        "dst_port":      pa.array(col_dst_port,     type=pa.uint16()),
        "nat_ip_id":     pa.array(col_nat_ip_id,    type=pa.uint32()),
        "nat_port":      pa.array(col_nat_port,     type=pa.uint16()),
        "pkt_len":       pa.array(col_pkt_len,      type=pa.uint16()),
        "tcp_flags":     pa.array(col_tcp_flags,    type=pa.string()),
        "log_type":      pa.array(col_log_type,     type=pa.string()),
    })

    safe_out = str(cold_path).replace("'", "''")
    safe_home = str(settings.state_dir).replace("'", "''")
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET home_directory='{safe_home}'")
        con.register("logs_arrow", table)
        con.execute(
            f"COPY (SELECT * FROM logs_arrow ORDER BY ts, src_ip_id) "
            f"TO '{safe_out}' ({_PARQUET_OPTS})"
        )
    except Exception as e:
        if cold_path.exists():
            cold_path.unlink()
        return {"src": str(src), "date": date_str, "error": str(e)}
    finally:
        con.close()

    # Sidecar de dicts: mesmo formato escrito por archive_day em partitions.py.
    sidecar_path.write_text(json.dumps(
        {
            "interfaces":  {v: k for k, v in iface_dict.items()},
            "protocols":   {v: k for k, v in proto_dict.items()},
            "conn_states": {v: k for k, v in state_dict.items()},
        },
        ensure_ascii=False,
    ))

    elapsed = time.time() - t0
    size = cold_path.stat().st_size
    return {
        "src": str(src), "date": date_str,
        "rows": parsed_count, "errors": error_count,
        "output": str(cold_path), "size_bytes": size,
        "seconds": round(elapsed, 1),
        "rows_per_sec": int(parsed_count / elapsed) if elapsed > 0 else 0,
    }


def run(
    targets: list[Path],
    *,
    overwrite: bool = False,
    workers: int = 1,
) -> int:
    """Importa todos os arquivos. Retorna nº de arquivos importados com sucesso."""
    s = get_settings()
    s.state_dir.mkdir(parents=True, exist_ok=True)
    s.cold_storage_dir.mkdir(parents=True, exist_ok=True)

    files = discover_files(targets)
    if not files:
        log.warning("Nenhum arquivo .raw encontrado em: %s", targets)
        return 0

    log.info(
        "Encontrados %d arquivo(s) .raw para importar (workers=%d)",
        len(files), workers,
    )
    ops = OperationalStore(s.state_dir / "megalog.db")

    results: list[dict] = []
    if workers <= 1:
        for src in files:
            r = import_raw_file(src, settings=s, overwrite=overwrite)
            _log_result(r)
            results.append(r)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(import_raw_file, f, settings=s, overwrite=overwrite): f
                for f in files
            }
            for fut in as_completed(futures):
                r = fut.result()
                _log_result(r)
                results.append(r)

    ok, skipped, errors = 0, 0, 0
    total_rows = 0
    for r in results:
        if r.get("error"):
            errors += 1
        elif r.get("skipped"):
            skipped += 1
        elif r.get("rows", 0) > 0:
            ok += 1
            total_rows += r["rows"]
            ops.upsert_daily_stats(r["date"], r["rows"], r["size_bytes"])
        else:
            errors += 1  # rows=0 conta como falha (parser não pegou nada)

    log.info(
        "Resumo: %d ok / %d pulado / %d erro · %d linhas total importadas",
        ok, skipped, errors, total_rows,
    )
    return ok


def _log_result(r: dict) -> None:
    name = Path(r["src"]).name
    if r.get("error"):
        log.error("  FALHOU: %s — %s", name, r["error"])
    elif r.get("skipped"):
        log.info("  PULADO: %s — %s", name, r["reason"])
    elif r.get("warning"):
        log.warning("  VAZIO:  %s — %s (errors=%d)",
                    name, r["warning"], r.get("errors", 0))
    else:
        log.info(
            "  OK: %s — %d linhas em %ss (%d/s, errors=%d) → %s (%.1f MB)",
            name, r["rows"], r["seconds"], r["rows_per_sec"],
            r.get("errors", 0), Path(r["output"]).name,
            r["size_bytes"] / 1024 / 1024,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa .raw (syslog texto) para Parquet v5 — caminho rápido.",
    )
    parser.add_argument(
        "targets", nargs="+", type=Path,
        help="Arquivos .raw ou diretórios contendo eles",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Sobrescreve Parquet existente em cold (padrão: skip).",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Número de workers paralelos (1 = single-process).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    n = run(args.targets, overwrite=args.overwrite, workers=args.workers)
    sys.exit(0 if n > 0 else 1)


if __name__ == "__main__":
    main()
