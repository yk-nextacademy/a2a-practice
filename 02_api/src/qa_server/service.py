"""검증부서 업무 로직: 문제 탐지(검증·이슈 등록), Dev 이관 또는 버전 확정. Dev와는 HTTP API로만 통신한다.

- 데모: 발행 통지를 받으면 자동으로 빌드를 내려받아 검증하고, 결과에 따라 이관/확정 통지를 보낸다.
- 실습: 발행 통지를 받으면 검증 항목을 '대기'로 만들고, 검증부서원이 QA 웹에서 판정·등록·이관·확정한다.
"""

import asyncio
import logging

from common import config
from common.db import Database, now
from common.events import EventBus
from common.http import IntegrationClient, IntegrationError
from common.llm import LLMClient
from common.work import Members, WorkError

from . import catalog

log = logging.getLogger("qa")
ACTOR = "QA"
ACTIVE_STATUSES = ("OPEN", "TRANSFERRED", "REOPENED")
UNRESOLVED = ("OPEN", "TRANSFERRED", "REOPENED", "FIXED")
# 이관 대상: 새 이슈·재오픈 + 이전에 이관했지만 직전 발행에 반영되지 않은(이월된) 이슈
TRANSFERABLE = ("OPEN", "REOPENED", "TRANSFERRED")

TEAM = [
    {"name": "김검증", "role": "로그인·검색 담당", "features": ["login", "search"]},
    {"name": "이검증", "role": "결제 담당", "features": ["payment"]},
    {"name": "박검증", "role": "리포트 담당", "features": ["report"]},
]

SYSTEM = "당신은 커머스 웹 서비스 검증팀의 QA 엔지니어입니다. 테스트 실패 로그를 분석해 결함 리포트를 작성합니다. 한국어로 작성합니다."
ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "결함 제목 (40자 이내)"},
        "description": {"type": "string", "description": "재현 조건, 기대 결과, 실제 결과를 포함한 2~3문장"},
        "severity": {"type": "string", "enum": catalog.SEVERITIES},
    },
    "required": ["title", "description", "severity"], "additionalProperties": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_cases (id TEXT PRIMARY KEY, feature TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, version TEXT NOT NULL, build_url TEXT,
    total INTEGER NOT NULL, passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT
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
"""
MIGRATIONS = {
    "test_cases": {"assignee": "TEXT"},
    "test_runs": {"mode": "TEXT NOT NULL DEFAULT 'demo'", "build": "TEXT", "decision": "TEXT", "decided_by": "TEXT"},
    "test_results": {"result": "TEXT", "assignee": "TEXT", "sim_log": "TEXT", "checked_by": "TEXT", "checked_at": "TEXT"},
    "issues": {"reporter": "TEXT"},
    "issue_history": {"actor": "TEXT"},
}


class QAService:
    def __init__(self, db: Database, bus: EventBus, llm: LLMClient, client: IntegrationClient):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.client = client
        self.members = Members(db, "QA", TEAM)
        self.tasks: set[asyncio.Task] = set()
        db.migrate(MIGRATIONS)
        db.execute("UPDATE test_results SET result = CASE passed WHEN 1 THEN 'PASS' ELSE 'FAIL' END WHERE result IS NULL")
        # 이전 프로세스에서 끝나지 못한 데모 검증 실행 정리 (실습 실행은 사람이 이어서 진행하므로 유지)
        db.execute("UPDATE test_runs SET status = 'CANCELLED', finished_at = ? WHERE mode = 'demo' AND status = 'RUNNING'", now())

    def seed(self) -> None:
        for tc in catalog.TEST_CASES:
            self.db.execute("INSERT OR IGNORE INTO test_cases (id, feature, name) VALUES (?, ?, ?)", tc["id"], tc["feature"], tc["name"])
        self.members.seed()
        for m in TEAM:
            for feature in m["features"]:
                self.db.execute("UPDATE test_cases SET assignee = ? WHERE feature = ? AND assignee IS NULL", m["name"], feature)

    def busy(self) -> bool:
        return bool(self.tasks)

    def _change(self, issue: dict, to_status: str, actor: str, note: str, **fields) -> None:
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), issue["id"])
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
                        issue["id"], issue["status"], to_status, actor, note, now())
        issue["status"] = to_status

    # ---------- Dev로부터 발행 통지 ----------
    def on_release_published(self, cycle_id: int, version: str, build_url: str, fixed_keys: list[str], mode: str = "demo") -> None:
        self.bus.emit(ACTOR, "webhook.received", f"v{version} 발행 통지 수신" + (f", 수정 주장 이슈: {', '.join(fixed_keys)}" if fixed_keys else ""),
                      {"version": version, "build_url": build_url}, cycle_id)
        for key in fixed_keys:
            issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
            if issue:
                self._change(issue, "FIXED", "Dev 시스템", f"Dev가 v{version}에 수정 반영을 통지", fixed_in_version=version)
        if mode == "manual":
            self._open_manual_run(cycle_id, version, build_url)
            return
        task = asyncio.create_task(self._verify(cycle_id, version, build_url), name=f"verify-{cycle_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ---------- Dev로부터 사이클 중단 통지 ----------
    def on_cycle_cancelled(self, cycle_id: int, reason: str) -> int:
        """해당 사이클의 자동 검증 작업을 취소하고, 진행 중인 검증 실행을 CANCELLED로 닫는다."""
        for task in list(self.tasks):
            if task.get_name() == f"verify-{cycle_id}":
                task.cancel()
        runs = self.db.all("SELECT id, version FROM test_runs WHERE cycle_id = ? AND status = 'RUNNING'", cycle_id)
        self.db.execute("UPDATE test_runs SET status = 'CANCELLED', finished_at = ? WHERE cycle_id = ? AND status = 'RUNNING'", now(), cycle_id)
        self.bus.emit(ACTOR, "webhook.received", f"사이클 #{cycle_id} 중단 통지 수신 ({reason})"
                      + (f" - v{', v'.join(r['version'] for r in runs)} 검증 실행 취소" if runs else ""),
                      {"runs": [r["id"] for r in runs]}, cycle_id)
        return len(runs)

    # ---------- demo: 자동 검증 ----------
    async def _verify(self, cycle_id: int, version: str, build_url: str) -> None:
        try:
            await asyncio.sleep(config.STEP_DELAY)
            build = await self.client.get(build_url, cycle_id)
            run = await self._run_tests(cycle_id, version, build_url, build)
            self._verify_fixed(cycle_id, version, run["run_id"])
            await self._register_failures(cycle_id, version, run["failures"])
            if run["failed"]:
                await self._transfer(cycle_id, version, "Demo")
                decision = "TRANSFERRED"
            else:
                await self._notify_approved(cycle_id, version, run["run_id"], "Demo")
                decision = "APPROVED"
            # 데모에서도 실행별 QA 결정을 남긴다 (Hub Releases의 'QA 결정' 열)
            self.db.execute("UPDATE test_runs SET decision = ?, decided_by = 'Demo' WHERE id = ?", decision, run["run_id"])
        except IntegrationError as exc:
            self.bus.emit(ACTOR, "integration.error", f"Dev 서버 연동 실패: {exc}", {}, cycle_id)
        except Exception as exc:
            log.exception("verification failed")
            self.bus.emit(ACTOR, "verification.error", f"검증 중 오류: {exc}", {}, cycle_id)

    async def _run_tests(self, cycle_id: int, version: str, build_url: str, build: dict) -> dict:
        total = len(catalog.TEST_CASES)
        run_id = self.db.execute(
            "INSERT INTO test_runs (cycle_id, version, build_url, total, status, mode, started_at) VALUES (?, ?, ?, ?, 'RUNNING', 'demo', ?)",
            cycle_id, version, build_url, total, now(),
        )
        self.bus.emit(ACTOR, "test_run.started", f"v{version} 검증 시작 (빌드 다운로드 완료, 테스트 {total}건)",
                      {"run_id": run_id, "version": version}, cycle_id)
        passed, failures = 0, []
        for index, tc in enumerate(catalog.TEST_CASES, start=1):
            await asyncio.sleep(config.TEST_DELAY)
            ok, log_text = catalog.run_test(build, tc["id"])
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, log, checked_by) VALUES (?, ?, ?, ?, ?, 'Demo')",
                            run_id, tc["id"], int(ok), "PASS" if ok else "FAIL", log_text)
            passed += ok
            if not ok:
                failures.append({**tc, "log": log_text})
            self.db.execute("UPDATE test_runs SET passed = ?, failed = ? WHERE id = ?", passed, index - passed, run_id)
            self.bus.publish({"type": "test.progress", "actor": ACTOR, "cycle_id": cycle_id,
                              "payload": {"run_id": run_id, "done": index, "total": total, "test_case_id": tc["id"], "passed": ok}})
        failed = total - passed
        self.db.execute("UPDATE test_runs SET status = 'DONE', finished_at = ? WHERE id = ?", now(), run_id)
        self.bus.emit(ACTOR, "test_run.finished", f"v{version} 검증 완료: {passed} PASS / {failed} FAIL",
                      {"run_id": run_id, "passed": passed, "failed": failed}, cycle_id)
        return {"run_id": run_id, "passed": passed, "failed": failed, "total": total, "failures": failures}

    def _verify_fixed(self, cycle_id: int, version: str, run_id: int) -> None:
        for issue in self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ?", cycle_id, version):
            ok = self.db.scalar("SELECT passed FROM test_results WHERE run_id = ? AND test_case_id = ?", run_id, issue["test_case_id"])
            self._change(issue, "VERIFIED" if ok else "REOPENED", "Demo", f"v{version}에서 {issue['test_case_id']} {'통과 확인' if ok else '재현'}")
            self.bus.emit(ACTOR, "issue.verified" if ok else "issue.reopened",
                          f"{issue['key']} {'수정 확인 (VERIFIED)' if ok else '재현되어 재오픈 (REOPENED)'}", {"key": issue["key"]}, cycle_id)

    async def _register_failures(self, cycle_id: int, version: str, failures: list[dict]) -> None:
        for failure in failures:
            if self._active_issue(cycle_id, failure["id"]):
                continue
            await asyncio.sleep(config.STEP_DELAY)
            analysis, source = await self.analyze(failure, version)
            self._insert_issue(cycle_id, version, failure["id"], analysis, "Demo", f"{failure['id']} 실패로 등록 (분석: {source})")

    # ---------- 공통: 이관 / 확정 ----------
    async def _transfer(self, cycle_id: int, version: str, actor: str) -> list[str]:
        await asyncio.sleep(config.STEP_DELAY)
        issues = self.db.all(f"SELECT * FROM issues WHERE cycle_id = ? AND status IN {TRANSFERABLE} ORDER BY id", cycle_id)
        payload = {"cycle_id": cycle_id, "version": version, "issues": [
            {k: i[k] for k in ("key", "test_case_id", "title", "description", "severity", "version_found")} for i in issues
        ]}
        await self.client.post(f"{config.DEV_URL}/api/tickets", payload, cycle_id)
        for issue in issues:
            carried = issue["status"] == "TRANSFERRED"
            self._change(issue, "TRANSFERRED", actor,
                         f"v{version}에 반영되지 않아 다시 이관 (이월, POST /api/tickets)" if carried else "개발부서 시스템으로 이관 (POST /api/tickets)")
        self.bus.emit(actor if actor != "Demo" else ACTOR, "issues.transferred",
                      f"이슈 {len(issues)}건 개발부서 이관: {', '.join(i['key'] for i in issues)}", {"keys": [i["key"] for i in issues]}, cycle_id)
        return [i["key"] for i in issues]

    async def _notify_approved(self, cycle_id: int, version: str, run_id: int, actor: str) -> None:
        run = self.db.one("SELECT * FROM test_runs WHERE id = ?", run_id)
        await self.client.post(f"{config.DEV_URL}/api/webhooks/verification-completed",
                               {"cycle_id": cycle_id, "version": version, "run_id": run_id, "passed": run["passed"], "total": run["total"]},
                               cycle_id)
        self.bus.emit(actor if actor != "Demo" else ACTOR, "release.approved", f"v{version} 버전 확정 ({run['passed']}/{run['total']} PASS)",
                      {"version": version, "run_id": run_id}, cycle_id)

    # ---------- 실습: 검증부서원 작업 ----------
    def _open_manual_run(self, cycle_id: int, version: str, build_url: str) -> None:
        self.db.execute("UPDATE test_runs SET status = 'CANCELLED' WHERE cycle_id = ? AND status = 'RUNNING'", cycle_id)
        run_id = self.db.execute(
            "INSERT INTO test_runs (cycle_id, version, build_url, total, status, mode, started_at) VALUES (?, ?, ?, ?, 'RUNNING', 'manual', ?)",
            cycle_id, version, build_url, len(catalog.TEST_CASES), now())
        for tc in self.db.all("SELECT * FROM test_cases ORDER BY id"):
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, assignee) VALUES (?, ?, 0, 'PENDING', ?)",
                            run_id, tc["id"], tc["assignee"])
        self.bus.emit(ACTOR, "test_run.opened", f"v{version} 검증 항목 {len(catalog.TEST_CASES)}건 생성 - 검증부서원 판정 대기",
                      {"run_id": run_id}, cycle_id)

    def current_run(self) -> dict | None:
        return self.db.one("SELECT * FROM test_runs WHERE mode = 'manual' AND status = 'RUNNING' ORDER BY id DESC LIMIT 1")

    def _run(self) -> dict:
        run = self.current_run()
        if not run:
            raise WorkError("검증 중인 버전이 없습니다. 개발부서가 버전을 발행하면 검증 항목이 생성됩니다.", 409)
        return run

    def _result(self, result_id: int, run: dict) -> dict:
        row = self.db.one("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.id = ?", result_id)
        if not row or row["run_id"] != run["id"]:
            raise WorkError("현재 검증 중인 버전의 항목이 아닙니다.", 404)
        return row

    async def execute(self, result_id: int, actor_name: str) -> dict:
        """빌드에서 테스트를 실행한 결과(참고용). 빌드는 처음 한 번 Dev 시스템에서 내려받아 보관한다."""
        self.members.require(actor_name)
        run = self._run()
        row = self._result(result_id, run)
        build = run["build"]
        if not build:
            try:
                build = await self.client.get(run["build_url"], run["cycle_id"])
            except IntegrationError as exc:
                raise WorkError(f"Dev 시스템에서 빌드를 내려받지 못했습니다. ({exc})", 502)
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
        self.bus.emit(Members.actor(member), "test.judged", f"{row['test_case_id']} {row['name']} → {result}",
                      {"test_case_id": row["test_case_id"], "result": result}, run["cycle_id"])

    def _update_counts(self, run_id: int) -> None:
        self.db.execute(
            "UPDATE test_runs SET passed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PASS'),"
            " failed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'FAIL') WHERE id = ?",
            run_id, run_id, run_id)

    def _active_issue(self, cycle_id: int, test_case_id: str) -> dict | None:
        return self.db.one(f"SELECT key FROM issues WHERE cycle_id = ? AND test_case_id = ? AND status IN {ACTIVE_STATUSES}", cycle_id, test_case_id)

    def _insert_issue(self, cycle_id: int, version: str, test_case_id: str, data: dict, actor: str, note: str) -> str:
        key = f"ISSUE-{(self.db.scalar('SELECT COUNT(*) FROM issues WHERE cycle_id = ?', cycle_id) or 0) + 1}"
        issue_id = self.db.execute(
            "INSERT INTO issues (cycle_id, key, test_case_id, version_found, title, description, severity, status, reporter, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)",
            cycle_id, key, test_case_id, version, data["title"], data.get("description", ""), data["severity"], actor, now(), now())
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, NULL, 'OPEN', ?, ?, ?)",
                        issue_id, actor, note, now())
        self.bus.emit(ACTOR if actor == "Demo" else f"QA {actor}", "issue.created", f"{key} 등록 [{data['severity']}] {data['title']}",
                      {"key": key, "severity": data["severity"]}, cycle_id)
        return key

    def create_issue(self, actor_name: str, data: dict) -> dict:
        member = self.members.require(actor_name)
        run = self._run()
        title, severity = (data.get("title") or "").strip(), data.get("severity")
        if not title:
            raise WorkError("이슈 제목을 입력하세요.")
        if severity not in catalog.SEVERITIES:
            raise WorkError("심각도를 선택하세요.")
        row = self._result(int(data.get("result_id") or 0), run)
        self.db.execute("UPDATE test_results SET result = 'FAIL', passed = 0, checked_by = ?, checked_at = ? WHERE id = ?",
                        member["name"], now(), row["id"])
        self._update_counts(run["id"])
        existing = self._active_issue(run["cycle_id"], row["test_case_id"])
        if existing:
            self.bus.emit(Members.actor(member), "test.judged", f"{row['test_case_id']} → FAIL (미해결 {existing['key']}에 연결)", {}, run["cycle_id"])
            return {"key": existing["key"], "created": False}
        key = self._insert_issue(run["cycle_id"], run["version"], row["test_case_id"],
                                 {"title": title, "severity": severity, "description": (data.get("description") or "").strip()},
                                 member["name"], f"{row['test_case_id']} 결함 등록")
        return {"key": key, "created": True}

    def verify_issue(self, key: str, actor_name: str, ok: bool) -> None:
        member = self.members.require(actor_name)
        run = self._run()
        issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", run["cycle_id"], key)
        if not issue or issue["status"] != "FIXED" or issue["fixed_in_version"] != run["version"]:
            raise WorkError(f"{key}은(는) 이 버전에서 확인할 수정 이슈가 아닙니다.")
        self._change(issue, "VERIFIED" if ok else "REOPENED", member["name"], f"v{run['version']}에서 {'수정 확인' if ok else '재현'}")
        self.bus.emit(Members.actor(member), "issue.verified" if ok else "issue.reopened",
                      f"{key} {'수정 확인 (VERIFIED)' if ok else '재현되어 재오픈 (REOPENED)'}", {"key": key}, run["cycle_id"])

    def readiness(self, run: dict | None) -> dict:
        if not run:
            return {"pending": 0, "unverified": 0, "open": 0, "unresolved": 0, "can_transfer": False, "can_approve": False}
        pending = self.db.scalar("SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PENDING'", run["id"])
        unverified = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ?",
                                    run["cycle_id"], run["version"])
        open_ = self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {TRANSFERABLE}", run["cycle_id"])
        unresolved = self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {UNRESOLVED}", run["cycle_id"])
        done = pending == 0 and unverified == 0
        return {"pending": pending, "unverified": unverified, "open": open_, "unresolved": unresolved,
                "can_transfer": done and open_ > 0, "can_approve": done and unresolved == 0}

    @staticmethod
    def _blocker(ready: dict, action: str) -> str:
        if ready["pending"]:
            return f"판정하지 않은 항목이 {ready['pending']}건 있습니다."
        if ready["unverified"]:
            return f"확인하지 않은 수정 이슈가 {ready['unverified']}건 있습니다."
        if action == "이관":
            return "이관할 이슈(OPEN/REOPENED 또는 이월된 TRANSFERRED)가 없습니다. 문제가 없다면 버전을 확정하세요."
        return f"미해결 이슈가 {ready['unresolved']}건 있어 확정할 수 없습니다. 개발부서로 이관하세요."

    async def transfer(self, actor_name: str) -> list[str]:
        member = self.members.require(actor_name)
        run = self._run()
        ready = self.readiness(run)
        if not ready["can_transfer"]:
            raise WorkError(self._blocker(ready, "이관"))
        try:
            keys = await self._transfer(run["cycle_id"], run["version"], Members.actor(member))
        except IntegrationError as exc:
            raise WorkError(f"Dev 시스템에 이관하지 못했습니다. 잠시 후 다시 시도하세요. ({exc})", 502)
        self.db.execute("UPDATE test_runs SET status = 'DONE', decision = 'TRANSFERRED', decided_by = ?, finished_at = ? WHERE id = ?",
                        member["name"], now(), run["id"])
        self.bus.emit(Members.actor(member), "test_run.finished", f"v{run['version']} 검증 완료 → Dev 이관", {"run_id": run["id"]}, run["cycle_id"])
        return keys

    async def approve(self, actor_name: str) -> None:
        member = self.members.require(actor_name)
        run = self._run()
        ready = self.readiness(run)
        if not ready["can_approve"]:
            raise WorkError(self._blocker(ready, "확정"))
        try:
            await self._notify_approved(run["cycle_id"], run["version"], run["id"], Members.actor(member))
        except IntegrationError as exc:
            raise WorkError(f"Dev 시스템에 확정을 통지하지 못했습니다. 잠시 후 다시 시도하세요. ({exc})", 502)
        self.db.execute("UPDATE test_runs SET status = 'DONE', decision = 'APPROVED', decided_by = ?, finished_at = ? WHERE id = ?",
                        member["name"], now(), run["id"])

    async def analyze(self, failure: dict, version: str) -> tuple[dict, str]:
        prompt = (
            f"제품: {catalog.PRODUCT} v{version}\n기능: {failure['feature']}\n테스트케이스: {failure['id']} {failure['name']}\n"
            f"실패 로그:\n{failure['log']}\n\n결함 리포트를 작성하세요. 심각도는 매출/결제 영향은 Critical, 주요 기능 장애는 Major, 불편 수준은 Minor."
        )
        return await self.llm.json(SYSTEM, prompt, ISSUE_SCHEMA, lambda: {
            "title": f"[{failure['feature']}] {failure['name']} 실패",
            "description": f"{failure['id']} 실행 시 실패. {failure['log'].splitlines()[0]}",
            "severity": catalog.FEATURE_SEVERITY[failure["feature"]],
        })

    async def draft_issue(self, result_id: int) -> dict:
        run = self._run()
        row = self._result(result_id, run)
        log_text = row["log"] or "(실행 결과 없음: '실행'을 먼저 누르면 로그를 근거로 작성합니다)"
        analysis, source = await self.analyze({"id": row["test_case_id"], "feature": row["feature"], "name": row["name"], "log": log_text},
                                              run["version"])
        return {**analysis, "source": source}
