"""실습 모드: 부서원이 직접 업무를 진행한다.

    [Dev] 버전 발행 → [QA] 담당 항목 검증·이슈 등록·수정 확인 → [QA] Dev 이관 또는 버전 확정
    → [Dev] 이슈 착수·수정 완료 → [Dev] 버전 발행 → ...

데모(CycleRunner)와 같은 도메인·시뮬레이터·이슈 상태를 쓰고, 단계 전환만 사람이 버튼으로 한다.
빌드 동작(숨은 결함)은 시뮬레이터가 정하므로, 검증부서원은 '실행' 결과를 참고해 판정한다.
"""

from ..domain import scenario, simulator
from ..infra.db import Database, now
from ..infra.events import EventBus
from .dev_service import DevService
from .issue_store import change_status
from .qa_service import ACTIVE_STATUSES, QAService

ACTIVE_CYCLE = ("RELEASING", "VERIFYING", "TRANSFERRING", "FIXING")
UNRESOLVED = ("OPEN", "TRANSFERRED", "IN_PROGRESS", "REOPENED", "FIXED")


class WorkError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Workbench:
    def __init__(self, db: Database, bus: EventBus, dev: DevService, qa: QAService):
        self.db = db
        self.bus = bus
        self.dev = dev
        self.qa = qa

    # ---------- 기준 데이터 ----------
    def seed(self) -> None:
        for m in scenario.TEAM:
            self.db.execute("INSERT OR IGNORE INTO members (name, dept, role, features) VALUES (?, ?, ?, ?)",
                            m["name"], m["dept"], m["role"], m["features"])
            for feature in m["features"]:
                self.db.execute("UPDATE test_cases SET assignee = ? WHERE feature = ? AND assignee IS NULL", m["name"], feature)

    def members(self) -> list[dict]:
        return self.db.all("SELECT * FROM members ORDER BY dept DESC, name")

    def _member(self, name: str | None, dept: str) -> dict:
        member = self.db.one("SELECT * FROM members WHERE name = ?", name or "")
        if not member:
            raise WorkError("상단에서 작업자를 선택하세요.", 403)
        if member["dept"] != dept:
            label = "검증부서" if dept == "QA" else "개발부서"
            raise WorkError(f"{label} 작업자만 할 수 있는 작업입니다. (현재: {member['dept']} {member['name']})", 403)
        return member

    def _actor(self, member: dict) -> str:
        return f"{member['dept']} {member['name']}"

    # ---------- 사이클 ----------
    def active_cycle(self) -> dict | None:
        return self.db.one(f"SELECT * FROM cycles WHERE mode = 'manual' AND status IN {ACTIVE_CYCLE} ORDER BY id DESC LIMIT 1")

    def _cycle(self, *statuses: str) -> dict:
        cycle = self.active_cycle()
        if not cycle:
            raise WorkError("진행 중인 실습 사이클이 없습니다. 상단의 '실습 시작'을 누르세요.", 409)
        if statuses and cycle["status"] not in statuses:
            raise WorkError(f"지금은 이 작업을 할 수 없습니다. (현재 단계: {cycle['status']})", 409)
        return cycle

    def _set(self, cycle_id: int, status: str, **fields) -> None:
        sets = ", ".join(["status = ?"] + [f"{k} = ?" for k in fields])
        self.db.execute(f"UPDATE cycles SET {sets} WHERE id = ?", status, *fields.values(), cycle_id)
        self.bus.publish({"type": "cycle.status", "actor": "Workbench", "cycle_id": cycle_id, "payload": {"status": status}})

    def start(self, llm_label: str, demo_running: bool) -> int:
        if demo_running or self.db.scalar(f"SELECT COUNT(*) FROM cycles WHERE status IN {ACTIVE_CYCLE}"):
            raise WorkError("이미 진행 중인 사이클이 있습니다.", 409)
        cycle_id = self.db.execute(
            "INSERT INTO cycles (status, iteration, llm_mode, mode, started_at) VALUES ('RELEASING', 0, ?, 'manual', ?)", llm_label, now())
        self.bus.emit("Workbench", "cycle.started", f"실습 사이클 #{cycle_id} 시작 - 개발부서의 최초 버전 발행을 기다립니다", {}, cycle_id)
        return cycle_id

    def abort(self) -> None:
        cycle = self._cycle()
        self._set(cycle["id"], "CANCELLED", finished_at=now())
        self.bus.emit("Workbench", "cycle.cancelled", f"실습 사이클 #{cycle['id']} 중단", {}, cycle["id"])

    # ---------- Dev: 버전 발행 ----------
    def shippable(self, cycle_id: int) -> list[dict]:
        """수정 완료되었지만 아직 어떤 버전에도 반영되지 않은 이슈."""
        return self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version IS NULL ORDER BY id", cycle_id)

    def next_version(self, cycle_id: int) -> str:
        current = self.db.one("SELECT version FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)
        return simulator.next_version(current["version"]) if current else scenario.INITIAL_VERSION

    async def release(self, actor_name: str, note: str) -> str:
        member = self._member(actor_name, "Dev")
        cycle = self._cycle("RELEASING", "FIXING")
        current = self.db.one("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle["id"])
        version = self.next_version(cycle["id"])
        if current is None:
            defects, keys = simulator.initial_defects(), []
            note = note.strip() or f"{scenario.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(scenario.FEATURES)}"
        else:
            issues = self.shippable(cycle["id"])
            if not issues:
                raise WorkError("반영할 수정 완료 이슈가 없습니다. 이슈를 수정한 뒤 발행하세요.")
            defects, introduced = simulator.apply_fixes(current["defects"], [i["test_case_id"] for i in issues if i["test_case_id"]])
            keys = [i["key"] for i in issues]
            if not note.strip():
                raise WorkError("릴리즈 노트를 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
            self.db.execute(f"UPDATE issues SET fixed_in_version = ?, updated_at = ? WHERE id IN ({','.join(str(i['id']) for i in issues)})",
                            version, now())
            if introduced:
                self.bus.emit("Simulator", "regression.introduced", f"수정 과정에서 숨은 회귀 결함 발생: {', '.join(introduced)}",
                              {"defects": introduced}, cycle["id"])

        self.db.execute(
            "INSERT INTO releases (cycle_id, version, features, defects, fixed_issue_keys, release_note, released_by, released_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            cycle["id"], version, scenario.FEATURES, defects, keys, note.strip(), member["name"], now())
        run_id = self.db.execute("INSERT INTO test_runs (cycle_id, version, total, status, started_at) VALUES (?, ?, ?, 'RUNNING', ?)",
                                 cycle["id"], version, len(scenario.TEST_CASES), now())
        for tc in self.db.all("SELECT * FROM test_cases ORDER BY id"):
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, assignee) VALUES (?, ?, 0, 'PENDING', ?)",
                            run_id, tc["id"], tc["assignee"])
        self._set(cycle["id"], "VERIFYING", iteration=cycle["iteration"] + 1, current_version=version)
        self.bus.emit(self._actor(member), "release.published",
                      f"v{version} 발행" + (f" (반영 이슈: {', '.join(keys)})" if keys else "") + " - 검증부서 검증 시작",
                      {"version": version, "run_id": run_id}, cycle["id"])
        return version

    # ---------- QA: 검증 ----------
    def current_run(self, cycle: dict) -> dict | None:
        if not cycle or not cycle["current_version"]:
            return None
        return self.db.one("SELECT * FROM test_runs WHERE cycle_id = ? AND version = ? ORDER BY id DESC LIMIT 1",
                           cycle["id"], cycle["current_version"])

    def _result(self, result_id: int, cycle: dict) -> dict:
        row = self.db.one("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.id = ?", result_id)
        run = self.current_run(cycle)
        if not row or not run or row["run_id"] != run["id"]:
            raise WorkError("현재 검증 중인 버전의 항목이 아닙니다.", 404)
        return row

    def execute(self, result_id: int, actor_name: str) -> dict:
        """빌드에서 테스트를 실행한 결과(참고용). 판정은 검증부서원이 한다."""
        self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        row = self._result(result_id, cycle)
        release = self.db.one("SELECT defects FROM releases WHERE cycle_id = ? AND version = ?", cycle["id"], cycle["current_version"])
        ok, log = simulator.run_test(release["defects"], row["test_case_id"])
        self.db.execute("UPDATE test_results SET sim_log = ?, log = ? WHERE id = ?", f"{'PASS' if ok else 'FAIL'}\n{log}", log, result_id)
        return {"passed": ok, "log": log}

    def judge(self, result_id: int, actor_name: str, result: str) -> None:
        member = self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        row = self._result(result_id, cycle)
        if result not in ("PASS", "FAIL"):
            raise WorkError("판정은 PASS 또는 FAIL입니다.")
        self.db.execute("UPDATE test_results SET result = ?, passed = ?, checked_by = ?, checked_at = ? WHERE id = ?",
                        result, int(result == "PASS"), member["name"], now(), result_id)
        self._update_counts(row["run_id"])
        self.bus.emit(self._actor(member), "test.judged", f"{row['test_case_id']} {row['name']} → {result}",
                      {"test_case_id": row["test_case_id"], "result": result}, cycle["id"])

    def _update_counts(self, run_id: int) -> None:
        self.db.execute(
            "UPDATE test_runs SET passed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PASS'),"
            " failed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'FAIL') WHERE id = ?",
            run_id, run_id, run_id)

    def create_issue(self, actor_name: str, data: dict) -> dict:
        member = self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        title, severity = (data.get("title") or "").strip(), data.get("severity")
        test_case_id = data.get("test_case_id") or ""
        if not title:
            raise WorkError("이슈 제목을 입력하세요.")
        if severity not in scenario.SEVERITIES:
            raise WorkError("심각도를 선택하세요.")
        if data.get("result_id"):
            row = self._result(int(data["result_id"]), cycle)
            test_case_id = row["test_case_id"]
            self.db.execute("UPDATE test_results SET result = 'FAIL', passed = 0, checked_by = ?, checked_at = ? WHERE id = ?",
                            member["name"], now(), row["id"])
            self._update_counts(row["run_id"])
        if test_case_id:
            existing = self.db.one(f"SELECT key FROM issues WHERE cycle_id = ? AND test_case_id = ? AND status IN {ACTIVE_STATUSES}",
                                   cycle["id"], test_case_id)
            if existing:
                self.bus.emit(self._actor(member), "test.judged", f"{test_case_id} → FAIL (미해결 {existing['key']}에 연결)", {}, cycle["id"])
                return {"key": existing["key"], "created": False}
        key = f"ISSUE-{(self.db.scalar('SELECT COUNT(*) FROM issues WHERE cycle_id = ?', cycle['id']) or 0) + 1}"
        issue_id = self.db.execute(
            "INSERT INTO issues (cycle_id, key, test_case_id, version_found, title, description, severity, status, reporter, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)",
            cycle["id"], key, test_case_id, cycle["current_version"], title, (data.get("description") or "").strip(), severity,
            member["name"], now(), now())
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, NULL, 'OPEN', ?, ?, ?)",
                        issue_id, member["name"], f"{test_case_id or '테스트 외'} 결함 등록", now())
        self.bus.emit(self._actor(member), "issue.created", f"{key} 등록 [{severity}] {title}", {"key": key}, cycle["id"])
        return {"key": key, "created": True}

    def verify_issue(self, key: str, actor_name: str, ok: bool) -> None:
        member = self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle["id"], key)
        if not issue or issue["status"] != "FIXED" or issue["fixed_in_version"] != cycle["current_version"]:
            raise WorkError(f"{key}은(는) 이 버전에서 확인할 수정 이슈가 아닙니다.")
        status = "VERIFIED" if ok else "REOPENED"
        change_status(self.db, issue, status, member["name"], f"v{cycle['current_version']}에서 {'수정 확인' if ok else '재현'}")
        self.bus.emit(self._actor(member), "issue.verified" if ok else "issue.reopened",
                      f"{key} {'수정 확인 (VERIFIED)' if ok else '재현되어 재오픈 (REOPENED)'}", {"key": key}, cycle["id"])

    def readiness(self, cycle: dict) -> dict:
        """검증 완료 조건: 모든 항목 판정 + 수정 이슈 확인.
        이관은 새로 넘길 OPEN/REOPENED 이슈 또는 개발부서에 남은 미완료 이슈(carried)가 있을 때, 확정은 미해결 이슈가 없을 때.
        carried: 일부만 수정하고 발행한 경우 TRANSFERRED/IN_PROGRESS로 남은 이슈. 다시 개발부서 차례로 넘겨야 수정할 수 있다."""
        run = self.current_run(cycle)
        if not run:
            return {"pending": 0, "unverified": 0, "open": 0, "carried": 0, "unresolved": 0, "can_transfer": False, "can_approve": False}
        pending = self.db.scalar("SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PENDING'", run["id"])
        unverified = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ?",
                                    cycle["id"], cycle["current_version"])
        open_ = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN ('OPEN', 'REOPENED')", cycle["id"])
        carried = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN ('TRANSFERRED', 'IN_PROGRESS')", cycle["id"])
        unresolved = self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {UNRESOLVED}", cycle["id"])
        done = pending == 0 and unverified == 0
        return {"pending": pending, "unverified": unverified, "open": open_, "carried": carried, "unresolved": unresolved,
                "can_transfer": done and (open_ + carried) > 0, "can_approve": done and unresolved == 0}

    def transfer(self, actor_name: str) -> list[str]:
        member = self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        ready = self.readiness(cycle)
        if not ready["can_transfer"]:
            raise WorkError(self._blocker(ready, "이관"))
        run = self.current_run(cycle)
        self.db.execute("UPDATE test_runs SET status = 'DONE', finished_at = ? WHERE id = ?", now(), run["id"])
        self.bus.emit(self._actor(member), "test_run.finished", f"v{cycle['current_version']} 검증 완료: {run['passed']} PASS / {run['failed']} FAIL",
                      {"run_id": run["id"]}, cycle["id"])
        self._set(cycle["id"], "TRANSFERRING")
        # 개발부서 미완료 이슈(TRANSFERRED/IN_PROGRESS)는 상태·담당자를 그대로 두고 차례만 넘긴다
        carried = [i["key"] for i in self.db.all(
            "SELECT key FROM issues WHERE cycle_id = ? AND status IN ('TRANSFERRED', 'IN_PROGRESS') ORDER BY id", cycle["id"])]
        issues = self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status IN ('OPEN', 'REOPENED') ORDER BY id", cycle["id"])
        for issue in issues:
            change_status(self.db, issue, "TRANSFERRED", member["name"], "개발부서로 이관")
        parts = []
        if issues:
            parts.append(f"이슈 {len(issues)}건 개발부서 이관: {', '.join(i['key'] for i in issues)}")
        if carried:
            parts.append(f"개발부서 미완료 {len(carried)}건 계속 수정: {', '.join(carried)}")
        self.bus.emit(self._actor(member), "issues.transferred", " / ".join(parts), {"carried": carried}, cycle["id"])
        self._set(cycle["id"], "FIXING")
        return [i["key"] for i in issues]

    def approve(self, actor_name: str) -> None:
        member = self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        ready = self.readiness(cycle)
        if not ready["can_approve"]:
            raise WorkError(self._blocker(ready, "확정"))
        run = self.current_run(cycle)
        self.db.execute("UPDATE test_runs SET status = 'DONE', finished_at = ? WHERE id = ?", now(), run["id"])
        self._set(cycle["id"], "APPROVED", finished_at=now())
        self.bus.emit(self._actor(member), "release.approved", f"v{cycle['current_version']} 버전 확정 ({run['passed']}/{run['total']} PASS)",
                      {}, cycle["id"])
        self.bus.emit("Workbench", "cycle.completed", f"실습 사이클 #{cycle['id']} 완료: v{cycle['current_version']} 확정 (검증 {cycle['iteration']}회)",
                      {}, cycle["id"])

    @staticmethod
    def _blocker(ready: dict, action: str) -> str:
        if ready["pending"]:
            return f"판정하지 않은 항목이 {ready['pending']}건 있습니다."
        if ready["unverified"]:
            return f"확인하지 않은 수정 이슈가 {ready['unverified']}건 있습니다."
        if action == "이관":
            return "이관할 이슈(OPEN/REOPENED 또는 개발부서 미완료 이슈)가 없습니다. 문제가 없다면 버전을 확정하세요."
        return f"미해결 이슈가 {ready['unresolved']}건 있어 확정할 수 없습니다. 개발부서로 이관하세요."

    # ---------- Dev: 이슈 수정 ----------
    def _dev_issue(self, key: str, cycle: dict) -> dict:
        issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle["id"], key)
        if not issue or issue["status"] not in ("TRANSFERRED", "IN_PROGRESS"):
            raise WorkError(f"{key}은(는) 수정할 수 있는 상태가 아닙니다.")
        return issue

    def start_fix(self, key: str, actor_name: str) -> None:
        member = self._member(actor_name, "Dev")
        cycle = self._cycle("FIXING")
        issue = self._dev_issue(key, cycle)
        change_status(self.db, issue, "IN_PROGRESS", member["name"], f"{member['name']} 착수", assignee=member["name"])
        self.bus.emit(self._actor(member), "issue.started", f"{key} 착수", {"key": key}, cycle["id"])

    def fix(self, key: str, actor_name: str, fix_note: str) -> None:
        member = self._member(actor_name, "Dev")
        cycle = self._cycle("FIXING")
        issue = self._dev_issue(key, cycle)
        if not fix_note.strip():
            raise WorkError("수정 내역을 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
        change_status(self.db, issue, "FIXED", member["name"], fix_note.strip(), fix_note=fix_note.strip(), fixed_in_version=None,
                      assignee=issue["assignee"] or member["name"])
        self.bus.emit(self._actor(member), "issue.fixed", f"{key} 수정 완료 (다음 버전 발행 시 반영)", {"key": key}, cycle["id"])

    # ---------- AI 초안 (LLM / mock) ----------
    async def draft_issue(self, result_id: int, actor_name: str) -> dict:
        self._member(actor_name, "QA")
        cycle = self._cycle("VERIFYING")
        row = self._result(result_id, cycle)
        log = row["log"] or "(실행 결과 없음: '실행'을 먼저 누르면 로그를 근거로 작성합니다)"
        analysis, source = await self.qa._analyze({"id": row["test_case_id"], "feature": row["feature"], "name": row["name"], "log": log},
                                                  cycle["current_version"])
        return {**analysis, "source": source}

    async def draft_fix(self, key: str, actor_name: str) -> dict:
        self._member(actor_name, "Dev")
        cycle = self._cycle("FIXING")
        issue = self._dev_issue(key, cycle)
        return {"fix_note": await self.dev._write_fix_note(issue)}

    async def draft_release(self, actor_name: str) -> dict:
        self._member(actor_name, "Dev")
        cycle = self._cycle("RELEASING", "FIXING")
        issues = self.shippable(cycle["id"])
        if not issues:
            return {"release_note": ""}
        return {"release_note": await self.dev._write_release_note(self.next_version(cycle["id"]), issues)}
