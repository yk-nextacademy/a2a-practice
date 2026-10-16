"""SQLite 접근 헬퍼. 서비스마다 자기 스키마로 별도 DB 파일을 쓴다."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

COMMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    cycle_id INTEGER,
    actor TEXT NOT NULL,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS integration_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    cycle_id INTEGER,
    direction TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    duration_ms INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    request_body TEXT,
    response_body TEXT,
    error TEXT
);
"""

MEMBERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (name TEXT PRIMARY KEY, dept TEXT NOT NULL, role TEXT, features TEXT);
"""

JSON_COLUMNS = {"features", "defects", "fixed_issue_keys", "payload", "build"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_ms() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path, schema: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(COMMON_SCHEMA + MEMBERS_SCHEMA + schema)

    def migrate(self, columns: dict[str, dict[str, str]]) -> None:
        """기존 DB 파일에 없는 컬럼을 추가한다 (실습 모드 추가 컬럼)."""
        for table, cols in columns.items():
            existing = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        data = dict(row)
        for key in JSON_COLUMNS & data.keys():
            if isinstance(data[key], str):
                data[key] = json.loads(data[key])
        return data

    def all(self, sql: str, *args: Any) -> list[dict]:
        return [self._decode(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, *args: Any) -> dict | None:
        row = self.conn.execute(sql, args).fetchone()
        return self._decode(row) if row else None

    def scalar(self, sql: str, *args: Any) -> Any:
        row = self.conn.execute(sql, args).fetchone()
        return row[0] if row else None

    def execute(self, sql: str, *args: Any) -> int:
        args = tuple(json.dumps(a, ensure_ascii=False) if isinstance(a, (list, dict)) else a for a in args)
        return self.conn.execute(sql, args).lastrowid

    def reset(self, keep: tuple[str, ...] = ()) -> None:
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table not in keep:
                self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM sqlite_sequence")
