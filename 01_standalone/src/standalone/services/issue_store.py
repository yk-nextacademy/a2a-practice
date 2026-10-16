"""이슈 상태 변경 + 이력 기록 (Stage 1에서는 Dev/QA가 같은 테이블을 공유한다)."""

from ..infra.db import Database, now


def change_status(db: Database, issue: dict, to_status: str, actor: str, note: str = "", **fields) -> None:
    sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
    db.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), issue["id"])
    db.execute(
        "INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
        issue["id"], issue["status"], to_status, actor, note, now(),
    )
    issue["status"] = to_status
    issue.update(fields)
