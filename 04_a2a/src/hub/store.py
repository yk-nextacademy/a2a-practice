"""Hub DB 스키마. Hub는 부서 DB에 접근하지 않고, 에이전트가 돌려준 Artifact로만 업무 현황을 구성한다."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    url TEXT PRIMARY KEY, name TEXT, card TEXT, status TEXT NOT NULL, latency_ms INTEGER, error TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, iteration INTEGER NOT NULL DEFAULT 0,
    current_version TEXT, llm_mode TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, agent TEXT NOT NULL, skill TEXT NOT NULL,
    task_id TEXT, context_id TEXT, state TEXT NOT NULL, last_message TEXT, request TEXT, started_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS a2a_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, cycle_id INTEGER, agent TEXT, method TEXT NOT NULL, url TEXT NOT NULL,
    task_id TEXT, status INTEGER, duration_ms INTEGER, request_body TEXT, response_body TEXT, streaming INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, agent TEXT NOT NULL, task_id TEXT, name TEXT NOT NULL,
    text TEXT, payload TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, version TEXT NOT NULL, build_url TEXT, features TEXT,
    fixed_issue_keys TEXT NOT NULL, release_note TEXT, agent TEXT, task_id TEXT, released_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, version TEXT NOT NULL, total INTEGER, passed INTEGER, failed INTEGER,
    status TEXT NOT NULL, results TEXT, verified TEXT, reopened TEXT, agent TEXT, task_id TEXT, approval TEXT,
    started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, key TEXT NOT NULL, test_case_id TEXT, title TEXT, description TEXT,
    severity TEXT, status TEXT NOT NULL, version_found TEXT, fixed_in_version TEXT, fix_note TEXT, analyzed_by TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (cycle_id, key)
);
CREATE TABLE IF NOT EXISTS issue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, key TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    source TEXT NOT NULL, note TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, iteration INTEGER, action TEXT NOT NULL, reason TEXT,
    brief TEXT, decided_by TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, agent TEXT NOT NULL, task_id TEXT NOT NULL, context_id TEXT,
    version TEXT, question TEXT, brief TEXT, status TEXT NOT NULL, comment TEXT, requested_at TEXT NOT NULL, decided_at TEXT
);
"""
