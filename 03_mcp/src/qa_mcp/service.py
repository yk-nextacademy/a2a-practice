"""검증부서 시스템 업무 로직 (Stage 3).

이 시스템은 Dev 시스템을 모른다(빌드 산출물만 build_url로 내려받는다). 바깥과의 연결은 MCP 도구뿐이다.
- 데모: Host의 AI가 도구로 테스트 실행·이슈 등록·이관·승인을 모두 지시한다.
- 실습: Host가 '검증 요청'을 넣으면 검증부서원이 QA 웹에서 판정하고 이관 또는 확정을 결정한다.
        결정은 '완료 작업'으로 남고, Host가 이를 발견해 Dev 시스템에 전달한 뒤 확인(ack)한다.
"""

import asyncio

import httpx

from common.db import Database, now
from common.events import EventBus
from common.llm import LLMClient
from common.work import Members, WorkError

from . import catalog

ACTIVE = ("OPEN", "TRANSFERRED", "REOPENED")
UNRESOLVED = ("OPEN", "TRANSFERRED", "REOPENED", "FIXED")
FEATURE_SEVERITY = {"payment": "Critical", "login": "Major", "report": "Major", "search": "Minor"}
TEAM = [
    {"name": "김검증", "role": "로그인·검색 담당", "features": ["login", "search"]},
    {"name": "이검증", "role": "결제 담당", "features": ["payment"]},
    {"name": "박검증", "role": "리포트 담당", "features": ["report"]},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_cases (id TEXT PRIMARY KEY, feature TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, version TEXT NOT NULL, build_url TEXT,
    total INTEGER NOT NULL, passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, approval_comment TEXT, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, test_case_id TEXT NOT NULL, passed INTEGER NOT NULL, log TEXT
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, key TEXT NOT NULL, test_case_id TEXT NOT NULL,
    version_found TEXT NOT NULL, title TEXT NOT NULL, description TEXT, severity TEXT NOT NULL, status TEXT NOT NULL,
    fixed_in_version TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (cycle_id, key)
);
CREATE TABLE IF NOT EXISTS issue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    note TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, run_id INTEGER, status TEXT NOT NULL,
    outcome TEXT, requested_at TEXT NOT NULL, done_at TEXT, acked_at TEXT
);
"""
MIGRATIONS = {
    "test_cases": {"assignee": "TEXT"},
    "test_runs": {"mode": "TEXT NOT NULL DEFAULT 'demo'", "build": "TEXT", "decision": "TEXT", "decided_by": "TEXT"},
    "test_results": {"result": "TEXT", "assignee": "TEXT", "sim_log": "TEXT", "checked_by": "TEXT", "checked_at": "TEXT"},
    "issues": {"reporter": "TEXT"},
    "issue_history": {"actor": "TEXT"},
}

SYSTEM = "당신은 커머스 웹 서비스 검증팀의 QA 엔지니어입니다. 테스트 실패 로그를 분석해 결함 리포트를 작성합니다. 한국어로 작성합니다."
ISSUE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "description": "결함 제목 (40자 이내)"},
                   "description": {"type": "string", "description": "재현 조건, 기대 결과, 실제 결과를 포함한 2~3문장"},
                   "severity": {"type": "string", "enum": catalog.SEVERITIES}},
    "required": ["title", "description", "severity"], "additionalProperties": False,
}


class QASystem:
    def __init__(self, db: Database, bus: EventBus, llm: LLMClient, test_delay: float):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.test_delay = test_delay
        self.members = Members(db, "QA", TEAM)
        db.migrate(MIGRATIONS)
        db.execute("UPDATE test_results SET result = CASE passed WHEN 1 THEN 'PASS' ELSE 'FAIL' END WHERE result IS NULL")

    def seed(self) -> None:
        for tc in catalog.TEST_CASES:
            self.db.execute("INSERT OR IGNORE INTO test_cases (id, feature, name) VALUES (?, ?, ?)", tc["id"], tc["feature"], tc["name"])
        self.members.seed()
        for m in TEAM:
            for feature in m["features"]:
                self.db.execute("UPDATE test_cases SET assignee = ? WHERE feature = ? AND assignee IS NULL", m["name"], feature)

    def _change(self, issue: dict, to_status: str, actor: str, note: str, **fields) -> None:
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), issue["id"])
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
                        issue["id"], issue["status"], to_status, actor, note, now())
        issue["status"] = to_status

    async def _download(self, build_url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(build_url)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as exc:
            raise WorkError(f"빌드 다운로드 실패: {build_url} ({type(exc).__name__})", 502)

    def _update_counts(self, run_id: int) -> None:
        self.db.execute(
            "UPDATE test_runs SET passed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PASS'),"
            " failed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'FAIL') WHERE id = ?", run_id, run_id, run_id)

    def _active_issue(self, cycle_id: int, test_case_id: str) -> dict | None:
        return self.db.one(f"SELECT key, status FROM issues WHERE cycle_id = ? AND test_case_id = ? AND status IN {ACTIVE}", cycle_id, test_case_id)

    def _insert_issue(self, cycle_id: int, version: str, test_case_id: str, title: str, description: str, severity: str, actor: str) -> str:
        key = f"ISSUE-{(self.db.scalar('SELECT COUNT(*) FROM issues WHERE cycle_id = ?', cycle_id) or 0) + 1}"
        issue_id = self.db.execute(
            "INSERT INTO issues (cycle_id, key, test_case_id, version_found, title, description, severity, status, reporter, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)", cycle_id, key, test_case_id, version, title, description, severity, actor, now(), now())
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, NULL, 'OPEN', ?, ?, ?)",
                        issue_id, actor, f"{test_case_id} 결함 등록", now())
        self.bus.emit("QA" if actor == "Host AI" else f"QA {actor}", "issue.created", f"{key} 등록 [{severity}] {title}", {"key": key}, cycle_id)
        return key

    # ---------- 데모용 도구 (Host의 AI가 직접 수행) ----------
    async def run_tests(self, cycle_id: int, version: str, build_url: str) -> dict:
        build = await self._download(build_url)
        if build.get("version") != version:
            raise WorkError(f"빌드 버전 불일치: 요청 {version}, 빌드 {build.get('version')}")
        total = len(catalog.TEST_CASES)
        run_id = self.db.execute("INSERT INTO test_runs (cycle_id, version, build_url, total, status, mode, started_at) VALUES (?, ?, ?, ?, 'RUNNING', 'demo', ?)",
                                 cycle_id, version, build_url, total, now())
        passed, failures = 0, []
        for index, tc in enumerate(catalog.TEST_CASES, start=1):
            await asyncio.sleep(self.test_delay)
            ok, log_text = catalog.run_test(build, tc["id"])
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, log, checked_by) VALUES (?, ?, ?, ?, ?, 'Host AI')",
                            run_id, tc["id"], int(ok), "PASS" if ok else "FAIL", log_text)
            passed += ok
            if not ok:
                failures.append({"test_case_id": tc["id"], "feature": tc["feature"], "name": tc["name"], "log": log_text})
            self.db.execute("UPDATE test_runs SET passed = ?, failed = ? WHERE id = ?", passed, index - passed, run_id)
            # 진행률 (DB에 남기지 않는 일시 이벤트, Hub 상단 진행 막대가 사용)
            self.bus.publish({"type": "test.progress", "actor": "QA", "cycle_id": cycle_id,
                              "payload": {"run_id": run_id, "done": index, "total": total, "test_case_id": tc["id"], "passed": ok}})
        self.db.execute("UPDATE test_runs SET status = 'DONE', finished_at = ? WHERE id = ?", now(), run_id)
        self.bus.emit("QA", "test_run.finished", f"v{version} 검증 완료 (Host 지시): {passed} PASS / {total - passed} FAIL", {}, cycle_id)
        return {"run_id": run_id, "version": version, "total": total, "passed": passed, "failed": total - passed, "failures": failures}

    def verify_fixed_issues(self, cycle_id: int, version: str, run_id: int, keys: list[str]) -> dict:
        verified, reopened = [], []
        for key in keys:
            issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
            if not issue:
                continue
            self._change(issue, "FIXED", "Host AI", f"Dev가 v{version}에 수정 반영", fixed_in_version=version)
            ok = self.db.scalar("SELECT passed FROM test_results WHERE run_id = ? AND test_case_id = ?", run_id, issue["test_case_id"])
            self._change(issue, "VERIFIED" if ok else "REOPENED", "Host AI", f"v{version}에서 {'통과 확인' if ok else '재현'}")
            (verified if ok else reopened).append(key)
        return {"verified": verified, "reopened": reopened}

    def create_issue(self, cycle_id: int, version: str, test_case_id: str, title: str, description: str, severity: str) -> dict:
        existing = self._active_issue(cycle_id, test_case_id)
        if existing:
            return {"key": existing["key"], "created": False, "status": existing["status"], "note": "미해결 이슈가 이미 있습니다."}
        return {"key": self._insert_issue(cycle_id, version, test_case_id, title, description, severity, "Host AI"), "created": True, "status": "OPEN"}

    def transfer_issues(self, cycle_id: int) -> dict:
        moved = self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status IN ('OPEN', 'REOPENED') ORDER BY id", cycle_id)
        for issue in moved:
            self._change(issue, "TRANSFERRED", "Host", "개발부서 이관 (Host가 전달)")
        # 이관 데이터 = 이번에 이관한 이슈 + 이미 이관됐지만 직전 발행에 반영되지 않은(이월) 이슈.
        # (반영된 이슈는 request_verification이 FIXED로 바꾸므로 TRANSFERRED로 남은 것은 모두 미반영분이다.
        #  Host가 전달을 다시 시도할 때도 같은 목록이 나온다.)
        issues = self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'TRANSFERRED' ORDER BY id", cycle_id)
        carried = [i["key"] for i in issues if i["key"] not in {m["key"] for m in moved}]
        if issues:
            self.bus.emit("Host", "issues.transferred", f"이슈 {len(issues)}건 이관 데이터 반환: {', '.join(i['key'] for i in issues)}"
                          + (f" (이월 {', '.join(carried)})" if carried and moved else ""), {"carried": carried}, cycle_id)
        return {"issues": [{k: i[k] for k in ("key", "test_case_id", "title", "description", "severity", "version_found")} for i in issues]}

    def approve_release(self, cycle_id: int, version: str, run_id: int, comment: str) -> dict:
        run = self.db.one("SELECT * FROM test_runs WHERE id = ? AND cycle_id = ?", run_id, cycle_id)
        if not run or run["version"] != version:
            raise WorkError(f"run {run_id}은(는) cycle {cycle_id} / v{version}의 실행이 아닙니다.")
        if run["failed"]:
            raise WorkError(f"run {run_id}에 실패한 테스트가 {run['failed']}건 있어 승인할 수 없습니다.")
        self.db.execute("UPDATE test_runs SET approved = 1, approval_comment = ? WHERE id = ?", comment, run_id)
        self.bus.emit("Host", "release.approved", f"v{version} 확정 기록 (Host 처리)", {}, cycle_id)
        return {"approved": True, "version": version, "run_id": run_id}

    # ---------- 실습: Host와 주고받는 작업 지시 ----------
    def request_verification(self, cycle_id: int, version: str, build_url: str, fixed_keys: list[str]) -> dict:
        """Host가 검증부서에 검증을 요청한다. 검증 항목을 '대기'로 만들고 검증부서원이 QA 웹에서 처리한다."""
        # 이전 사이클의 요청이 남아 있으면 정리한다 (Host가 cancel_work를 부르지 못하고 종료된 경우 대비)
        self._cancel_orders(self.db.all("SELECT * FROM work_orders WHERE status = 'OPEN' AND cycle_id != ?", cycle_id))
        existing = self.db.one("SELECT * FROM work_orders WHERE cycle_id = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1", cycle_id)
        if existing:
            return {"work_id": existing["id"], "note": "이미 진행 중인 검증 요청이 있습니다."}
        for key in fixed_keys:
            issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
            if issue:
                self._change(issue, "FIXED", "Host", f"Dev가 v{version}에 수정 반영 (Host 전달)", fixed_in_version=version)
        run_id = self.db.execute("INSERT INTO test_runs (cycle_id, version, build_url, total, status, mode, started_at) VALUES (?, ?, ?, ?, 'RUNNING', 'manual', ?)",
                                 cycle_id, version, build_url, len(catalog.TEST_CASES), now())
        for tc in self.db.all("SELECT * FROM test_cases ORDER BY id"):
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, assignee) VALUES (?, ?, 0, 'PENDING', ?)",
                            run_id, tc["id"], tc["assignee"])
        work_id = self.db.execute("INSERT INTO work_orders (cycle_id, run_id, status, requested_at) VALUES (?, ?, 'OPEN', ?)", cycle_id, run_id, now())
        self.bus.emit("Host", "work.requested", f"v{version} 검증 요청 접수 (#{work_id}) - 검증 항목 {len(catalog.TEST_CASES)}건 판정 대기",
                      {"work_id": work_id}, cycle_id)
        return {"work_id": work_id, "run_id": run_id}

    def _cancel_orders(self, orders: list[dict]) -> list[int]:
        for order in orders:
            self.db.execute("UPDATE work_orders SET status = 'CANCELLED' WHERE id = ?", order["id"])
            self.db.execute("UPDATE test_runs SET status = 'CANCELLED', finished_at = ? WHERE id = ? AND status = 'RUNNING'", now(), order["run_id"])
        return [o["id"] for o in orders]

    def cancel_work(self, cycle_id: int) -> dict:
        """Host가 사이클을 중단했을 때 그 사이클의 진행 중(OPEN) 검증 요청과 검증 실행을 취소한다."""
        ids = self._cancel_orders(self.db.all("SELECT * FROM work_orders WHERE cycle_id = ? AND status = 'OPEN'", cycle_id))
        if ids:
            self.bus.emit("Host", "work.cancelled", f"사이클 중단으로 검증 요청 취소 (#{', #'.join(map(str, ids))})", {"work_ids": ids}, cycle_id)
        return {"cycle_id": cycle_id, "cancelled": ids}

    def open_order(self) -> dict | None:
        return self.db.one("SELECT * FROM work_orders WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")

    def completed_work(self, cycle_id: int) -> list[dict]:
        return self.db.all("SELECT id AS work_id, run_id, outcome, done_at FROM work_orders WHERE cycle_id = ? AND status = 'DONE' ORDER BY id", cycle_id)

    def ack_work(self, work_id: int) -> dict:
        self.db.execute("UPDATE work_orders SET status = 'ACKED', acked_at = ? WHERE id = ? AND status = 'DONE'", now(), work_id)
        return {"work_id": work_id, "acked": True}

    # ---------- 실습: 검증부서원 작업 (QA 웹) ----------
    def current_run(self) -> dict | None:
        order = self.open_order()
        return self.db.one("SELECT * FROM test_runs WHERE id = ?", order["run_id"]) if order else None

    def _run(self) -> dict:
        run = self.current_run()
        if not run:
            raise WorkError("지금은 검증부서 차례가 아닙니다. (Host의 검증 요청이 없습니다)", 409)
        return run

    def _result(self, result_id: int, run: dict) -> dict:
        row = self.db.one("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.id = ?", result_id)
        if not row or row["run_id"] != run["id"]:
            raise WorkError("현재 검증 중인 버전의 항목이 아닙니다.", 404)
        return row

    async def execute(self, result_id: int, actor_name: str) -> dict:
        self.members.require(actor_name)
        run = self._run()
        row = self._result(result_id, run)
        build = run["build"]
        if not build:
            build = await self._download(run["build_url"])
            self.db.execute("UPDATE test_runs SET build = ? WHERE id = ?", build, run["id"])
        ok, log_text = catalog.run_test(build, row["test_case_id"])
        self.db.execute("UPDATE test_results SET sim_log = ?, log = ? WHERE id = ?", f"{'PASS' if ok else 'FAIL'}\n{log_text}", log_text, result_id)
        return {"passed": ok, "log": log_text}

    def judge(self, result_id: int, actor_name: str, result: str) -> None:
        member = self.members.require(actor_name)
        run = self._run()
        row = self._result(result_id, run)
        if result not in ("PASS", "FAIL"):
            raise WorkError("판정은 PASS 또는 FAIL입니다.")
        self.db.execute("UPDATE test_results SET result = ?, passed = ?, checked_by = ?, checked_at = ? WHERE id = ?",
                        result, int(result == "PASS"), member["name"], now(), result_id)
        self._update_counts(run["id"])
        self.bus.emit(Members.actor(member), "test.judged", f"{row['test_case_id']} {row['name']} → {result}", {}, run["cycle_id"])

    def staff_issue(self, actor_name: str, data: dict) -> dict:
        member = self.members.require(actor_name)
        run = self._run()
        title, severity = (data.get("title") or "").strip(), data.get("severity")
        if not title:
            raise WorkError("이슈 제목을 입력하세요.")
        if severity not in catalog.SEVERITIES:
            raise WorkError("심각도를 선택하세요.")
        row = self._result(int(data.get("result_id") or 0), run)
        self.db.execute("UPDATE test_results SET result = 'FAIL', passed = 0, checked_by = ?, checked_at = ? WHERE id = ?", member["name"], now(), row["id"])
        self._update_counts(run["id"])
        existing = self._active_issue(run["cycle_id"], row["test_case_id"])
        if existing:
            return {"key": existing["key"], "created": False}
        return {"key": self._insert_issue(run["cycle_id"], run["version"], row["test_case_id"], title, (data.get("description") or "").strip(),
                                          severity, member["name"]), "created": True}

    def verify_issue(self, key: str, actor_name: str, ok: bool) -> None:
        member = self.members.require(actor_name)
        run = self._run()
        issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", run["cycle_id"], key)
        if not issue or issue["status"] != "FIXED" or issue["fixed_in_version"] != run["version"]:
            raise WorkError(f"{key}은(는) 이 버전에서 확인할 수정 이슈가 아닙니다.")
        self._change(issue, "VERIFIED" if ok else "REOPENED", member["name"], f"v{run['version']}에서 {'수정 확인' if ok else '재현'}")
        self.bus.emit(Members.actor(member), "issue.verified" if ok else "issue.reopened",
                      f"{key} {'수정 확인 (VERIFIED)' if ok else '재현되어 재오픈 (REOPENED)'}", {}, run["cycle_id"])

    def readiness(self, run: dict | None) -> dict:
        if not run:
            return {"pending": 0, "unverified": 0, "open": 0, "carried": 0, "unresolved": 0, "can_transfer": False, "can_approve": False}
        pending = self.db.scalar("SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PENDING'", run["id"])
        unverified = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ?", run["cycle_id"], run["version"])
        new_ = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN ('OPEN', 'REOPENED')", run["cycle_id"])
        # 이월: 이전에 이관했지만 이번 버전에 반영되지 않아 TRANSFERRED로 남은 이슈 (다시 이관 대상)
        carried = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status = 'TRANSFERRED'", run["cycle_id"])
        unresolved = self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {UNRESOLVED}", run["cycle_id"])
        done = pending == 0 and unverified == 0
        return {"pending": pending, "unverified": unverified, "open": new_ + carried, "carried": carried, "unresolved": unresolved,
                "can_transfer": done and new_ + carried > 0, "can_approve": done and unresolved == 0}

    def _decide(self, actor_name: str, decision: str) -> dict:
        member = self.members.require(actor_name)
        run = self._run()
        ready = self.readiness(run)
        if decision == "TRANSFERRED" and not ready["can_transfer"]:
            raise WorkError(self._blocker(ready, "이관"))
        if decision == "APPROVED" and not ready["can_approve"]:
            raise WorkError(self._blocker(ready, "확정"))
        order = self.open_order()
        outcome = {"decision": "transfer" if decision == "TRANSFERRED" else "approve", "cycle_id": run["cycle_id"], "version": run["version"],
                   "run_id": run["id"], "passed": run["passed"], "failed": run["failed"], "decided_by": member["name"]}
        self.db.execute("UPDATE test_runs SET status = 'DONE', decision = ?, decided_by = ?, finished_at = ? WHERE id = ?",
                        decision, member["name"], now(), run["id"])
        self.db.execute("UPDATE work_orders SET status = 'DONE', outcome = ?, done_at = ? WHERE id = ?", outcome, now(), order["id"])
        self.bus.emit(Members.actor(member), "verification.decided",
                      f"v{run['version']} 검증 완료 → {'Dev 이관' if decision == 'TRANSFERRED' else '버전 확정'} 결정 (Host 전달 대기)", {}, run["cycle_id"])
        return outcome

    def transfer(self, actor_name: str) -> dict:
        return self._decide(actor_name, "TRANSFERRED")

    def approve(self, actor_name: str) -> dict:
        return self._decide(actor_name, "APPROVED")

    @staticmethod
    def _blocker(ready: dict, action: str) -> str:
        if ready["pending"]:
            return f"판정하지 않은 항목이 {ready['pending']}건 있습니다."
        if ready["unverified"]:
            return f"확인하지 않은 수정 이슈가 {ready['unverified']}건 있습니다."
        if action == "이관":
            return "이관할 이슈(OPEN/REOPENED 또는 이전 버전에 반영되지 않은 이슈)가 없습니다. 문제가 없다면 버전을 확정하세요."
        return f"미해결 이슈가 {ready['unresolved']}건 있어 확정할 수 없습니다. 개발부서로 이관하세요."

    async def draft_issue(self, result_id: int) -> dict:
        run = self._run()
        row = self._result(result_id, run)
        log_text = row["log"] or "(실행 결과 없음: '실행'을 먼저 누르면 로그를 근거로 작성합니다)"
        prompt = (f"제품: Commerce Web v{run['version']}\n기능: {row['feature']}\n"
                  f"테스트케이스: {row['test_case_id']} {row['name']}\n실패 로그:\n{log_text}\n\n"
                  "결함 리포트를 작성하세요. 심각도는 매출/결제 영향은 Critical, 주요 기능 장애는 Major, 불편 수준은 Minor.")
        analysis, source = await self.llm.json(SYSTEM, prompt, ISSUE_SCHEMA, lambda: {
            "title": f"[{row['feature']}] {row['name']} 실패", "description": f"{row['test_case_id']} 실행 시 실패. {log_text.splitlines()[0]}",
            "severity": FEATURE_SEVERITY[row["feature"]]})
        return {**analysis, "source": source}
