"""화면 조회용 쿼리 모음."""

from ..infra.db import Database

OPEN_STATUSES = ("OPEN", "TRANSFERRED", "IN_PROGRESS", "REOPENED", "FIXED")


class Queries:
    def __init__(self, db: Database):
        self.db = db

    def cycle(self, cycle_id: int | None) -> dict | None:
        if cycle_id:
            return self.db.one("SELECT * FROM cycles WHERE id = ?", cycle_id)
        return self.db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1")

    def cycles(self) -> list[dict]:
        return self.db.all(
            "SELECT c.*, (SELECT COUNT(*) FROM releases r WHERE r.cycle_id = c.id) AS releases,"
            " (SELECT COUNT(*) FROM issues i WHERE i.cycle_id = c.id) AS issues FROM cycles c ORDER BY id DESC"
        )

    def releases(self, cycle_id: int) -> list[dict]:
        return self.db.all(
            "SELECT r.*, t.id AS run_id, t.passed, t.failed, t.total, t.status AS run_status FROM releases r"
            " LEFT JOIN test_runs t ON t.cycle_id = r.cycle_id AND t.version = r.version"
            " WHERE r.cycle_id = ? ORDER BY r.id", cycle_id,
        )

    def release(self, cycle_id: int, version: str) -> dict | None:
        return self.db.one("SELECT * FROM releases WHERE cycle_id = ? AND version = ?", cycle_id, version)

    def test_runs(self, cycle_id: int) -> list[dict]:
        return self.db.all("SELECT * FROM test_runs WHERE cycle_id = ? ORDER BY id", cycle_id)

    def test_run(self, run_id: int) -> dict | None:
        return self.db.one("SELECT * FROM test_runs WHERE id = ?", run_id)

    def test_results(self, run_id: int) -> list[dict]:
        return self.db.all(
            "SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.run_id = ? ORDER BY r.id",
            run_id,
        )

    def issues(self, cycle_id: int, keys: list[str] | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM issues WHERE cycle_id = ? ORDER BY id", cycle_id)
        return [r for r in rows if keys is None or r["key"] in keys]

    def issue(self, cycle_id: int, key: str) -> dict | None:
        return self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)

    def issue_history(self, issue_id: int) -> list[dict]:
        return self.db.all("SELECT * FROM issue_history WHERE issue_id = ? ORDER BY id", issue_id)

    def events(self, cycle_id: int | None, limit: int = 500) -> list[dict]:
        if cycle_id is None:
            return []
        return self.db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit)

    def stats(self, cycle_id: int) -> dict:
        last_run = self.db.one("SELECT * FROM test_runs WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)
        return {
            "releases": self.db.scalar("SELECT COUNT(*) FROM releases WHERE cycle_id = ?", cycle_id),
            "open_issues": self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {OPEN_STATUSES}", cycle_id),
            "total_issues": self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ?", cycle_id),
            "last_run": last_run,
        }
