"""Testes do stream — focam no comportamento de archive_processed,
em particular a janela de retenção baseada em momento de arquivamento
(não no mtime herdado do arquivo)."""
from __future__ import annotations

import os
import time
from pathlib import Path

from megalog.ingest.stream import archive_processed


def _make_raw(stream_dir: Path, name: str, mtime: float | None = None) -> Path:
    p = stream_dir / name
    p.write_text("dummy\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_archive_processed_moves_file(tmp_path: Path) -> None:
    stream = tmp_path / "stream"
    stream.mkdir()
    src = _make_raw(stream, "2026-04-28-15.raw")
    processed = stream / ".processed"

    archive_processed(src, processed, keep_hours=24)

    assert not src.exists()
    assert (processed / src.name).exists()


def test_archive_processed_resets_mtime_on_archive(tmp_path: Path) -> None:
    """Regressão crítica: arquivos .raw importados de fontes antigas
    têm mtime histórico (ex: nov/2025). Sem reset de mtime, o cleanup
    da janela `keep_hours` apaga o arquivo na hora seguinte ao mover.
    """
    stream = tmp_path / "stream"
    stream.mkdir()
    old = time.time() - 180 * 24 * 3600  # 180 dias atrás
    src = _make_raw(stream, "2025-11-15-00.raw", mtime=old)
    processed = stream / ".processed"

    archive_processed(src, processed, keep_hours=24)

    dst = processed / src.name
    assert dst.exists(), "fix: recém-arquivado nunca deve ser apagado pelo cleanup"
    # mtime resetado: dentro de poucos segundos do agora.
    assert (time.time() - dst.stat().st_mtime) < 5


def test_archive_processed_cleans_old_archived_files(tmp_path: Path) -> None:
    """O cleanup ainda funciona — arquivos antigos no .processed são apagados."""
    stream = tmp_path / "stream"
    stream.mkdir()
    processed = stream / ".processed"
    processed.mkdir()

    # Arquivo "antigo" já no processed (foi arquivado há 48h).
    old_archive = processed / "2026-04-26-00.raw"
    old_archive.write_text("old\n")
    cutoff_past = time.time() - 48 * 3600
    os.utime(old_archive, (cutoff_past, cutoff_past))

    # Arquivo novo recém-arquivado.
    src = _make_raw(stream, "2026-04-28-15.raw")
    archive_processed(src, processed, keep_hours=24)

    assert not old_archive.exists(), "antigos além de keep_hours devem ser removidos"
    assert (processed / "2026-04-28-15.raw").exists()
