"""Backup online de ip_registry.db e megalog.db (SQLite WAL, sem bloquear writers).

Usa sqlite3.Connection.backup() — lê a fonte enquanto o processor/web continuam
escrevendo. Mantém apenas as últimas N cópias.

Uso:
    python3 backup_state.py --src /dados1/state --dest /dados2/backups --keep 8
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DBS = ("ip_registry.db", "megalog.db")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_one(src: Path, dst: Path) -> int:
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as src_con:
        with sqlite3.connect(dst) as dst_con:
            src_con.backup(dst_con)
    return dst.stat().st_size


def rotate(dest_root: Path, keep: int) -> list[str]:
    snaps = sorted(p for p in dest_root.iterdir() if p.is_dir())
    removed: list[str] = []
    for old in snaps[:-keep]:
        shutil.rmtree(old)
        removed.append(old.name)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--keep", type=int, default=8)
    args = ap.parse_args()

    if not args.src.is_dir():
        print(f"src não existe: {args.src}", file=sys.stderr)
        return 2

    args.dest.mkdir(parents=True, exist_ok=True)
    snap = args.dest / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snap.mkdir()

    total = 0
    for name in DBS:
        src_db = args.src / name
        if not src_db.exists():
            print(f"skip (ausente): {src_db}")
            continue
        dst_db = snap / name
        size = backup_one(src_db, dst_db)
        digest = sha256_of(dst_db)
        (snap / f"{name}.sha256").write_text(f"{digest}  {name}\n")
        total += size
        print(f"ok: {name} → {dst_db} ({size/1024/1024:.1f} MB, sha256={digest[:12]}…)")

    removed = rotate(args.dest, args.keep)
    if removed:
        print(f"rotacionados (removidos): {removed}")

    print(f"backup completo: {snap.name} ({total/1024/1024:.1f} MB total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
