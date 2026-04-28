"""Storage operacional em SQLite (volume baixo, OLTP).

Hospeda 4 tabelas que NÃO entram no DuckDB de logs:
  - users          — autenticação web
  - audit_log      — auditoria de ações
  - daily_stats    — contagem por dia (cache para evitar COUNT(*) em DBs gigantes)
  - anomaly_alerts — alertas de detecção de anomalias

Manter em SQLite porque:
  - Volume baixo (centenas a milhares de linhas)
  - Padrão OLTP (inserts/updates pontuais com transação)
  - Não há vantagem colunar
  - Backup é trivial (1 arquivo)
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'user',
    created_at    INTEGER,
    last_login    INTEGER,
    active        INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT,
    action     TEXT,
    details    TEXT,
    ip_address TEXT,
    ts         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);

CREATE TABLE IF NOT EXISTS daily_stats (
    date          TEXT PRIMARY KEY,
    log_count     INTEGER,
    db_size_bytes INTEGER,
    updated_at    INTEGER
);

CREATE TABLE IF NOT EXISTS anomaly_alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT UNIQUE,
    log_count        INTEGER,
    db_size_bytes    INTEGER,
    expected_count   INTEGER,
    ratio            REAL,
    analysis         TEXT,
    details_json     TEXT,
    classification   TEXT,
    created_at       INTEGER,
    acknowledged     INTEGER DEFAULT 0,
    acknowledged_by  TEXT,
    acknowledged_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON anomaly_alerts(created_at);

CREATE TABLE IF NOT EXISTS revoked_jti (
    jti     TEXT PRIMARY KEY,
    exp_ts  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revoked_jti_exp ON revoked_jti(exp_ts);
"""


class OperationalStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(str(self.path), isolation_level=None, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    # ── daily_stats ──────────────────────────────────────────────────────────

    def upsert_daily_stats(self, date_str: str, log_count: int, db_size: int) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO daily_stats (date, log_count, db_size_bytes, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "log_count=excluded.log_count, "
                "db_size_bytes=excluded.db_size_bytes, "
                "updated_at=excluded.updated_at",
                (date_str, log_count, db_size, int(time.time())),
            )

    def all_daily_stats(self) -> dict[str, tuple[int, int]]:
        """date_str → (log_count, db_size_bytes), ordenado por date."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT date, log_count, db_size_bytes FROM daily_stats ORDER BY date"
            ).fetchall()
            return {r["date"]: (r["log_count"], r["db_size_bytes"]) for r in rows}

    # ── anomaly_alerts ───────────────────────────────────────────────────────

    def alert_exists(self, date_str: str) -> bool:
        with self._connect() as con:
            return con.execute(
                "SELECT 1 FROM anomaly_alerts WHERE date=?", (date_str,)
            ).fetchone() is not None

    def insert_alert(
        self,
        *,
        date_str: str,
        log_count: int,
        db_size_bytes: int,
        expected_count: int,
        ratio: float,
        analysis: str,
        details_json: str,
        classification: str | None,
    ) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT OR REPLACE INTO anomaly_alerts "
                "(date, log_count, db_size_bytes, expected_count, ratio, "
                " analysis, details_json, classification, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (date_str, log_count, db_size_bytes, expected_count, ratio,
                 analysis, details_json, classification, int(time.time())),
            )
            return cur.lastrowid

    def list_alerts(self, *, only_unacknowledged: bool = False) -> list[sqlite3.Row]:
        with self._connect() as con:
            if only_unacknowledged:
                return con.execute(
                    "SELECT * FROM anomaly_alerts WHERE acknowledged=0 "
                    "ORDER BY created_at DESC"
                ).fetchall()
            return con.execute(
                "SELECT * FROM anomaly_alerts ORDER BY created_at DESC"
            ).fetchall()

    def get_alert(self, alert_id: int) -> sqlite3.Row | None:
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM anomaly_alerts WHERE id=?", (alert_id,)
            ).fetchone()

    def get_alert_by_date(self, date_str: str) -> sqlite3.Row | None:
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM anomaly_alerts WHERE date=?", (date_str,)
            ).fetchone()

    def acknowledge_alert(self, alert_id: int, by_username: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE anomaly_alerts SET acknowledged=1, "
                "acknowledged_by=?, acknowledged_at=? "
                "WHERE id=? AND acknowledged=0",
                (by_username, int(time.time()), alert_id),
            )
            return cur.rowcount > 0

    def count_unacknowledged_alerts(self) -> int:
        with self._connect() as con:
            return con.execute(
                "SELECT COUNT(*) FROM anomaly_alerts WHERE acknowledged=0"
            ).fetchone()[0]

    # ── users ────────────────────────────────────────────────────────────────

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
    ) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT INTO users (username, password_hash, role, created_at, active) "
                "VALUES (?, ?, ?, ?, 1)",
                (username, password_hash, role, int(time.time())),
            )
            return cur.lastrowid

    def get_user_by_username(self, username: str) -> sqlite3.Row | None:
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM users WHERE username=? AND active=1", (username,)
            ).fetchone()

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        with self._connect() as con:
            return con.execute(
                "SELECT id, username, role, created_at, last_login, active "
                "FROM users ORDER BY username"
            ).fetchall()

    def set_user_password(self, user_id: int, password_hash: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (password_hash, user_id),
            )
            return cur.rowcount > 0

    def deactivate_user(self, user_id: int) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
            return cur.rowcount > 0

    def update_user_role(self, user_id: int, role: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE users SET role=? WHERE id=?", (role, user_id)
            )
            return cur.rowcount > 0

    def touch_last_login(self, user_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE users SET last_login=? WHERE id=?",
                (int(time.time()), user_id),
            )

    def user_count(self) -> int:
        with self._connect() as con:
            return con.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]

    # ── JWT revogados ────────────────────────────────────────────────────────

    def revoke_jti(self, jti: str, exp_ts: int) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO revoked_jti (jti, exp_ts) VALUES (?, ?)",
                (jti, exp_ts),
            )

    def is_jti_revoked(self, jti: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM revoked_jti WHERE jti=?", (jti,)
            ).fetchone()
            return row is not None

    def purge_expired_jti(self, now_ts: int | None = None) -> int:
        now_ts = now_ts if now_ts is not None else int(time.time())
        with self._lock, self._connect() as con:
            cur = con.execute("DELETE FROM revoked_jti WHERE exp_ts < ?", (now_ts,))
            return cur.rowcount

    # ── audit ────────────────────────────────────────────────────────────────

    def log_audit(
        self,
        *,
        user_id: int | None,
        username: str | None,
        action: str,
        details: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO audit_log (user_id, username, action, details, ip_address, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, action, details, ip_address, int(time.time())),
            )

    def list_audit(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        user_id: int | None = None,
        action: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if action is not None:
            sql += " AND action = ?"
            params.append(action)
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as con:
            return con.execute(sql, params).fetchall()
