"""SQLite 접근 헬퍼."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    current_version TEXT,
    llm_mode TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    features TEXT NOT NULL,
    defects TEXT NOT NULL,
    fixed_issue_keys TEXT NOT NULL,
    release_note TEXT,
    released_at TEXT NOT NULL,
    UNIQUE (cycle_id, version)
);
CREATE TABLE IF NOT EXISTS test_cases (
    id TEXT PRIMARY KEY,
    feature TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    total INTEGER NOT NULL,
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    test_case_id TEXT NOT NULL,
    passed INTEGER NOT NULL,
    log TEXT
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    test_case_id TEXT NOT NULL,
    version_found TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    fixed_in_version TEXT,
    fix_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (cycle_id, key)
);
CREATE TABLE IF NOT EXISTS issue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    name TEXT PRIMARY KEY,
    dept TEXT NOT NULL,
    role TEXT,
    features TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    cycle_id INTEGER,
    actor TEXT NOT NULL,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT
);
"""

# 실습 모드에서 추가된 컬럼. 기존 DB 파일도 기동 시 자동으로 보강한다.
MIGRATIONS = {
    "cycles": {"mode": "TEXT NOT NULL DEFAULT 'demo'"},
    "releases": {"released_by": "TEXT"},
    "test_cases": {"assignee": "TEXT"},
    "test_results": {"result": "TEXT", "assignee": "TEXT", "sim_log": "TEXT", "checked_by": "TEXT", "checked_at": "TEXT"},
    "issues": {"reporter": "TEXT", "assignee": "TEXT"},
}

JSON_COLUMNS = {"features", "defects", "fixed_issue_keys", "payload"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            existing = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        # 이전 버전(데모 전용)에서 기록된 결과는 passed 값으로 판정 결과를 채운다
        self.conn.execute("UPDATE test_results SET result = CASE passed WHEN 1 THEN 'PASS' ELSE 'FAIL' END WHERE result IS NULL")

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

    def reset(self) -> None:
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM sqlite_sequence")
