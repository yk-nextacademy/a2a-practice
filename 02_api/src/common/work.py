"""실습 모드 공통: 업무 오류, 부서원(작업자) 관리.

부서원 명부는 각 부서 시스템이 자기 DB에 따로 가진다 (Dev 서버는 개발부서원, QA 서버는 검증부서원).
"""

from .db import Database


class WorkError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Members:
    def __init__(self, db: Database, dept: str, team: list[dict]):
        self.db = db
        self.dept = dept
        self.team = team

    def seed(self) -> None:
        for m in self.team:
            self.db.execute("INSERT OR IGNORE INTO members (name, dept, role, features) VALUES (?, ?, ?, ?)",
                            m["name"], self.dept, m["role"], m.get("features", []))

    def all(self) -> list[dict]:
        return self.db.all("SELECT * FROM members ORDER BY name")

    def require(self, name: str | None) -> dict:
        member = self.db.one("SELECT * FROM members WHERE name = ?", name or "")
        if not member:
            label = "개발부서" if self.dept == "Dev" else "검증부서"
            raise WorkError(f"상단에서 {label} 작업자를 선택하세요. (이 시스템의 부서원만 작업할 수 있습니다)", 403)
        return member

    @staticmethod
    def actor(member: dict) -> str:
        return f"{member['dept']} {member['name']}"
