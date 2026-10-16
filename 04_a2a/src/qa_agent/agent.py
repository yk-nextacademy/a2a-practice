"""QA Agent의 업무 수행부 (A2A AgentExecutor).

Hub는 "v1.1.0 검증해 줘"라고만 요청한다.
- 데모: 이 에이전트가 빌드 다운로드, 테스트, 결함 분석, 이슈 관리를 스스로 하고 진행률을 스트리밍한다.
- 실습: 검증 항목을 '대기'로 만들고 Task를 working 상태로 유지한다. 검증부서원이 QA 웹에서 판정할 때마다
        진행률(metadata.progress)과 함께 상태 메시지를 Hub로 스트리밍한다.
        부서원이 '이관'을 결정하면 issues Artifact로 완료하고, '확정'을 결정하면 요청자(Hub)에게 승인 여부를 되묻는다(input-required).
"""

import asyncio
import logging

import httpx
from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from common import config
from common.a2a_parts import first_data
from common.db import Database, now
from common.events import EventBus
from common.llm import LLMClient
from common.work import Members, WorkError

from . import catalog

log = logging.getLogger("qa_agent")
ACTIVE = ("OPEN", "TRANSFERRED", "REOPENED")
UNRESOLVED = ("OPEN", "TRANSFERRED", "REOPENED", "FIXED")
# 이관 대상: 신규·재현 이슈 + 이관했지만 이번 버전에도 수정이 반영되지 않은 이슈(개발부서가 일부만 수정해 발행한 경우)
TO_TRANSFER = ("OPEN", "REOPENED", "TRANSFERRED")
TEAM = [
    {"name": "김검증", "role": "로그인·검색 담당", "features": ["login", "search"]},
    {"name": "이검증", "role": "결제 담당", "features": ["payment"]},
    {"name": "박검증", "role": "리포트 담당", "features": ["report"]},
]

SYSTEM = "당신은 커머스 웹 서비스 검증팀의 QA 엔지니어입니다. 테스트 실패 로그를 분석해 결함 리포트를 작성합니다. 한국어로 작성합니다."
ISSUE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "description": "결함 제목 (40자 이내)"},
                   "description": {"type": "string", "description": "재현 조건, 기대 결과, 실제 결과를 포함한 2~3문장"},
                   "severity": {"type": "string", "enum": catalog.SEVERITIES}},
    "required": ["title", "description", "severity"], "additionalProperties": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_cases (id TEXT PRIMARY KEY, feature TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, task_id TEXT, version TEXT NOT NULL, build_url TEXT,
    total INTEGER NOT NULL, passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, approval TEXT, approval_comment TEXT, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, test_case_id TEXT NOT NULL, passed INTEGER NOT NULL, log TEXT
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, key TEXT NOT NULL, test_case_id TEXT NOT NULL,
    version_found TEXT NOT NULL, title TEXT NOT NULL, description TEXT, severity TEXT NOT NULL, status TEXT NOT NULL,
    fixed_in_version TEXT, analyzed_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (cycle_id, key)
);
CREATE TABLE IF NOT EXISTS issue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    actor TEXT, note TEXT, ts TEXT NOT NULL
);
"""
MIGRATIONS = {
    "test_cases": {"assignee": "TEXT"},
    "test_runs": {"mode": "TEXT NOT NULL DEFAULT 'demo'", "build": "TEXT", "decision": "TEXT", "decided_by": "TEXT"},
    "test_results": {"result": "TEXT", "assignee": "TEXT", "sim_log": "TEXT", "checked_by": "TEXT", "checked_at": "TEXT"},
    "issues": {"reporter": "TEXT"},
}


class QAWork:
    """검증부서 업무: 데모 자동 검증, 실습 부서원 작업, 진행 중인 Task와의 연결."""

    def __init__(self, db: Database, bus: EventBus, llm: LLMClient):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.members = Members(db, "QA", TEAM)
        self.waiting: dict[int, tuple[asyncio.Future, TaskUpdater]] = {}  # run_id -> (부서원 결정 신호, Task 갱신기)
        db.migrate(MIGRATIONS)
        db.execute("UPDATE test_results SET result = CASE passed WHEN 1 THEN 'PASS' ELSE 'FAIL' END WHERE result IS NULL")
        # 이전 프로세스에서 기다리던 검증은 Task 연결이 끊겼으므로 취소
        db.execute("UPDATE test_runs SET status = 'CANCELLED' WHERE mode = 'manual' AND status = 'RUNNING'")

    def seed(self) -> None:
        for tc in catalog.TEST_CASES:
            self.db.execute("INSERT OR IGNORE INTO test_cases (id, feature, name) VALUES (?, ?, ?)", tc["id"], tc["feature"], tc["name"])
        self.members.seed()
        for m in TEAM:
            for feature in m["features"]:
                self.db.execute("UPDATE test_cases SET assignee = ? WHERE feature = ? AND assignee IS NULL", m["name"], feature)

    # ---------- 공통 ----------
    def change(self, issue: dict, to_status: str, actor: str, note: str, **fields) -> None:
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), issue["id"])
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
                        issue["id"], issue["status"], to_status, actor, note, now())
        issue["status"] = to_status

    async def download(self, build_url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(build_url)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as exc:
            raise WorkError(f"빌드 다운로드 실패: {build_url} ({type(exc).__name__})", 502)

    def active_issue(self, cycle_id: int, test_case_id: str) -> dict | None:
        return self.db.one(f"SELECT key FROM issues WHERE cycle_id = ? AND test_case_id = ? AND status IN {ACTIVE}", cycle_id, test_case_id)

    def insert_issue(self, cycle_id: int, version: str, test_case_id: str, data: dict, actor: str, analyzed_by: str) -> str:
        key = f"ISSUE-{(self.db.scalar('SELECT COUNT(*) FROM issues WHERE cycle_id = ?', cycle_id) or 0) + 1}"
        issue_id = self.db.execute(
            "INSERT INTO issues (cycle_id, key, test_case_id, version_found, title, description, severity, status, analyzed_by, reporter, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
            cycle_id, key, test_case_id, version, data["title"], data.get("description", ""), data["severity"], analyzed_by, actor, now(), now())
        self.db.execute("INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, NULL, 'OPEN', ?, ?, ?)",
                        issue_id, actor, f"{test_case_id} 결함 등록", now())
        return key

    def mark_fixed(self, cycle_id: int, version: str, keys: list[str]) -> None:
        for key in keys:
            issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
            if issue:
                self.change(issue, "FIXED", "Hub", f"Dev가 v{version}에 수정 반영 (Hub 위임 내용)", fixed_in_version=version)

    def update_counts(self, run_id: int) -> None:
        self.db.execute(
            "UPDATE test_runs SET passed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PASS'),"
            " failed = (SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'FAIL') WHERE id = ?", run_id, run_id, run_id)

    def report(self, run_id: int, verified: list[str], reopened: list[str], created: list[str]) -> dict:
        run = self.db.one("SELECT * FROM test_runs WHERE id = ?", run_id)
        results = [{"test_case_id": r["test_case_id"], "name": r["name"], "feature": r["feature"], "passed": r["result"] == "PASS", "log": r["log"] or ""}
                   for r in self.db.all("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE run_id = ? ORDER BY r.id",
                                        run_id)]
        return {"cycle_id": run["cycle_id"], "run_id": run_id, "version": run["version"], "total": run["total"], "passed": run["passed"],
                "failed": run["failed"], "results": results, "verified": verified, "reopened": reopened, "created": created}

    def transfer_payload(self, cycle_id: int) -> list[dict]:
        issues = self.db.all(f"SELECT * FROM issues WHERE cycle_id = ? AND status IN {TO_TRANSFER} ORDER BY id", cycle_id)
        for issue in issues:
            self.change(issue, "TRANSFERRED", "QA Agent",
                        "미수정 이슈 재이관 (이전 버전에 반영되지 않음)" if issue["status"] == "TRANSFERRED" else "Hub로 반환 (개발부서 이관 대상)")
        return [{k: i[k] for k in ("key", "test_case_id", "title", "description", "severity", "version_found")} | {"analyzed_by": i["analyzed_by"]}
                for i in issues]

    async def analyze(self, test_case: dict, version: str, log_text: str) -> tuple[dict, str]:
        prompt = (f"제품: {catalog.PRODUCT} v{version}\n기능: {test_case['feature']}\n테스트케이스: {test_case['id']} {test_case['name']}\n"
                  f"실패 로그:\n{log_text}\n\n결함 리포트를 작성하세요. 심각도는 매출/결제 영향은 Critical, 주요 기능 장애는 Major, 불편 수준은 Minor.")
        return await self.llm.json(SYSTEM, prompt, ISSUE_SCHEMA, lambda: {
            "title": f"[{test_case['feature']}] {test_case['name']} 실패",
            "description": f"{test_case['id']} 실행 시 실패. {log_text.splitlines()[0]}",
            "severity": catalog.FEATURE_SEVERITY[test_case["feature"]]})

    # ---------- 실습: 검증 대기와 부서원 작업 ----------
    def open_manual_run(self, cycle_id: int, task_id: str, version: str, build_url: str) -> int:
        run_id = self.db.execute("INSERT INTO test_runs (cycle_id, task_id, version, build_url, total, status, mode, started_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', 'manual', ?)",
                                 cycle_id, task_id, version, build_url, len(catalog.TEST_CASES), now())
        for tc in self.db.all("SELECT * FROM test_cases ORDER BY id"):
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, assignee) VALUES (?, ?, 0, 'PENDING', ?)", run_id, tc["id"], tc["assignee"])
        self.bus.emit("Hub", "work.requested", f"Hub가 v{version} 검증을 위임 (Task {task_id[:8]}) - 검증 항목 12건 판정 대기", {"run_id": run_id}, cycle_id)
        return run_id

    async def wait_for_staff(self, run_id: int, updater: TaskUpdater) -> dict:
        for rid, (future, _) in list(self.waiting.items()):
            if not future.done():
                future.cancel()
        future = asyncio.get_running_loop().create_future()
        self.waiting[run_id] = (future, updater)
        try:
            return await future
        finally:
            self.waiting.pop(run_id, None)
            self.db.execute("UPDATE test_runs SET status = 'CANCELLED' WHERE id = ? AND status = 'RUNNING'", run_id)

    def current_run(self) -> dict | None:
        run = self.db.one("SELECT * FROM test_runs WHERE mode = 'manual' AND status = 'RUNNING' ORDER BY id DESC LIMIT 1")
        return run if run and run["id"] in self.waiting else None

    def _run(self) -> dict:
        run = self.current_run()
        if not run:
            raise WorkError("지금은 검증부서 차례가 아닙니다. (Hub가 위임한 검증이 없습니다)", 409)
        return run

    def _result(self, result_id: int, run: dict) -> dict:
        row = self.db.one("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.id = ?", result_id)
        if not row or row["run_id"] != run["id"]:
            raise WorkError("현재 검증 중인 버전의 항목이 아닙니다.", 404)
        return row

    async def _progress(self, run: dict, text: str, progress: bool = False) -> None:
        entry = self.waiting.get(run["id"])
        if not entry:
            return
        fresh = self.db.one("SELECT * FROM test_runs WHERE id = ?", run["id"])
        meta = None
        if progress:
            meta = {"progress": {"done": fresh["passed"] + fresh["failed"], "total": fresh["total"], "test_case_id": text.split()[0],
                                 "passed": "PASS" in text}}
        await entry[1].update_status(TaskState.TASK_STATE_WORKING, entry[1].new_agent_message([new_text_part(text)]), metadata=meta)

    async def execute(self, result_id: int, actor_name: str) -> dict:
        self.members.require(actor_name)
        run = self._run()
        row = self._result(result_id, run)
        build = run["build"]
        if not build:
            build = await self.download(run["build_url"])
            self.db.execute("UPDATE test_runs SET build = ? WHERE id = ?", build, run["id"])
        ok, log_text = catalog.run_test(build, row["test_case_id"])
        self.db.execute("UPDATE test_results SET sim_log = ?, log = ? WHERE id = ?", f"{'PASS' if ok else 'FAIL'}\n{log_text}", log_text, result_id)
        return {"passed": ok, "log": log_text}

    async def judge(self, result_id: int, actor_name: str, result: str) -> None:
        member = self.members.require(actor_name)
        run = self._run()
        row = self._result(result_id, run)
        if result not in ("PASS", "FAIL"):
            raise WorkError("판정은 PASS 또는 FAIL입니다.")
        self.db.execute("UPDATE test_results SET result = ?, passed = ?, checked_by = ?, checked_at = ? WHERE id = ?",
                        result, int(result == "PASS"), member["name"], now(), result_id)
        self.update_counts(run["id"])
        self.bus.emit(Members.actor(member), "test.judged", f"{row['test_case_id']} {row['name']} → {result}", {}, run["cycle_id"])
        await self._progress(run, f"{row['test_case_id']} {result} 판정 ({member['name']})", progress=True)

    async def staff_issue(self, actor_name: str, data: dict) -> dict:
        member = self.members.require(actor_name)
        run = self._run()
        title, severity = (data.get("title") or "").strip(), data.get("severity")
        if not title:
            raise WorkError("이슈 제목을 입력하세요.")
        if severity not in catalog.SEVERITIES:
            raise WorkError("심각도를 선택하세요.")
        row = self._result(int(data.get("result_id") or 0), run)
        self.db.execute("UPDATE test_results SET result = 'FAIL', passed = 0, checked_by = ?, checked_at = ? WHERE id = ?", member["name"], now(), row["id"])
        self.update_counts(run["id"])
        existing = self.active_issue(run["cycle_id"], row["test_case_id"])
        if existing:
            await self._progress(run, f"{row['test_case_id']} FAIL 판정 ({member['name']}, 미해결 {existing['key']}에 연결)", progress=True)
            return {"key": existing["key"], "created": False}
        key = self.insert_issue(run["cycle_id"], run["version"], row["test_case_id"],
                                {"title": title, "severity": severity, "description": (data.get("description") or "").strip()}, member["name"], "검증부서원")
        self.bus.emit(Members.actor(member), "issue.created", f"{key} 등록 [{severity}] {title}", {}, run["cycle_id"])
        await self._progress(run, f"{row['test_case_id']} FAIL 판정 ({member['name']}) · {key} 등록 [{severity}] {title}", progress=True)
        return {"key": key, "created": True}

    async def verify_issue(self, key: str, actor_name: str, ok: bool) -> None:
        member = self.members.require(actor_name)
        run = self._run()
        issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", run["cycle_id"], key)
        if not issue or issue["status"] != "FIXED" or issue["fixed_in_version"] != run["version"]:
            raise WorkError(f"{key}은(는) 이 버전에서 확인할 수정 이슈가 아닙니다.")
        self.change(issue, "VERIFIED" if ok else "REOPENED", member["name"], f"v{run['version']}에서 {'수정 확인' if ok else '재현'}")
        self.bus.emit(Members.actor(member), "issue.verified" if ok else "issue.reopened", f"{key} {'수정 확인' if ok else '재현 (재오픈)'}", {}, run["cycle_id"])
        await self._progress(run, f"{key} {'수정 확인 (VERIFIED)' if ok else '재현 (REOPENED)'} - {member['name']}")

    def readiness(self, run: dict | None) -> dict:
        if not run:
            return {"pending": 0, "unverified": 0, "open": 0, "unresolved": 0, "can_transfer": False, "can_approve": False}
        pending = self.db.scalar("SELECT COUNT(*) FROM test_results WHERE run_id = ? AND result = 'PENDING'", run["id"])
        unverified = self.db.scalar("SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ?", run["cycle_id"], run["version"])
        open_ = self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {TO_TRANSFER}", run["cycle_id"])
        unresolved = self.db.scalar(f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND status IN {UNRESOLVED}", run["cycle_id"])
        done = pending == 0 and unverified == 0
        return {"pending": pending, "unverified": unverified, "open": open_, "unresolved": unresolved,
                "can_transfer": done and open_ > 0, "can_approve": done and unresolved == 0}

    def decide(self, actor_name: str, decision: str) -> dict:
        member = self.members.require(actor_name)
        run = self._run()
        ready = self.readiness(run)
        if decision == "TRANSFERRED" and not ready["can_transfer"]:
            raise WorkError(self._blocker(ready, "이관"))
        if decision == "APPROVED" and not ready["can_approve"]:
            raise WorkError(self._blocker(ready, "확정"))
        self.db.execute("UPDATE test_runs SET status = 'DONE', decision = ?, decided_by = ?, finished_at = ? WHERE id = ?", decision, member["name"], now(), run["id"])
        self.bus.emit(Members.actor(member), "verification.decided",
                      f"v{run['version']} 검증 완료 → {'Dev 이관' if decision == 'TRANSFERRED' else '버전 확정'} 결정 (Task로 Hub에 반환)", {}, run["cycle_id"])
        outcome = {"decision": decision, "decided_by": member["name"], "run_id": run["id"]}
        self.waiting[run["id"]][0].set_result(outcome)
        return outcome

    @staticmethod
    def _blocker(ready: dict, action: str) -> str:
        if ready["pending"]:
            return f"판정하지 않은 항목이 {ready['pending']}건 있습니다."
        if ready["unverified"]:
            return f"확인하지 않은 수정 이슈가 {ready['unverified']}건 있습니다."
        if action == "이관":
            return "이관할 이슈(OPEN/REOPENED/미수정 TRANSFERRED)가 없습니다. 문제가 없다면 버전을 확정하세요."
        return f"미해결 이슈가 {ready['unresolved']}건 있어 확정할 수 없습니다. 개발부서로 이관하세요."

    async def draft_issue(self, result_id: int) -> dict:
        run = self._run()
        row = self._result(result_id, run)
        log_text = row["log"] or "(실행 결과 없음: '실행'을 먼저 누르면 로그를 근거로 작성합니다)"
        analysis, source = await self.analyze({"id": row["test_case_id"], "feature": row["feature"], "name": row["name"]}, run["version"], log_text)
        return {**analysis, "source": source}


class QAAgentExecutor(AgentExecutor):
    def __init__(self, work: QAWork):
        self.work = work

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        data = first_data(context.message.parts)
        try:
            if task is not None and task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
                await self._resume_approval(updater, context.task_id, data, context.get_user_input())
            elif data.get("skill") == "verify-release":
                cycle_id, version, build_url = int(data["cycle_id"]), data["version"], data["build_url"]
                fixed = data.get("fixed_issue_keys", [])
                if data.get("mode") == "manual":
                    await self._manual(updater, context.task_id, cycle_id, version, build_url, fixed)
                else:
                    await self._demo(updater, context.task_id, cycle_id, version, build_url, fixed)
            else:
                await updater.reject(updater.new_agent_message([new_text_part(f"지원하지 않는 요청입니다: skill={data.get('skill')!r}")]))
        except asyncio.CancelledError:
            await updater.cancel(updater.new_agent_message([new_text_part("작업이 취소되었습니다.")]))
        except Exception as exc:
            log.exception("task failed")
            await updater.failed(updater.new_agent_message([new_text_part(f"처리 중 오류: {exc}")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        run = self.work.db.one("SELECT id FROM test_runs WHERE task_id = ? AND status = 'RUNNING'", context.task_id)
        entry = self.work.waiting.get(run["id"]) if run else None
        if entry and not entry[0].done():
            entry[0].cancel()
            return
        # 승인 회신을 기다리던(input-required) Task 취소: 승인 대기도 함께 정리
        self.work.db.execute("UPDATE test_runs SET approval = 'CANCELLED' WHERE task_id = ? AND approval = 'PENDING'", context.task_id)
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    async def _say(self, updater: TaskUpdater, text: str, metadata: dict | None = None) -> None:
        await updater.update_status(TaskState.TASK_STATE_WORKING, updater.new_agent_message([new_text_part(text)]), metadata=metadata)

    async def _conclude(self, updater: TaskUpdater, cycle_id: int, run_id: int, verified: list[str], reopened: list[str], created: list[str],
                        decision: str, decided_by: str) -> None:
        """결과 Artifact를 반환하고, 이관이면 완료 / 확정이면 승인 여부를 되묻는다."""
        report = self.work.report(run_id, verified, reopened, created)
        summary = f"v{report['version']} 검증 결과: {report['passed']} PASS / {report['failed']} FAIL"
        if verified or reopened:
            summary += f"\n수정 확인 - VERIFIED: {', '.join(verified) or '-'} / REOPENED: {', '.join(reopened) or '-'}"
        await updater.add_artifact([new_text_part(summary), new_data_part(report, media_type="application/json")], name="test-report")
        if decision == "TRANSFERRED":
            payload = self.work.transfer_payload(cycle_id)
            await updater.add_artifact([new_text_part(f"개발부서 이관 대상 이슈 {len(payload)}건 ({decided_by} 결정)"), new_data_part({"issues": payload})], name="issues")
            await updater.complete(updater.new_agent_message([new_text_part(f"{summary}. 이슈 {len(payload)}건을 이관 대상으로 넘깁니다.")]))
        else:
            self.work.db.execute("UPDATE test_runs SET approval = 'PENDING' WHERE id = ?", run_id)
            await updater.requires_input(updater.new_agent_message([
                new_text_part(f"v{report['version']} 테스트 {report['total']}건이 모두 통과했습니다 ({decided_by} 확인). 릴리즈 승인 여부를 회신해 주세요."),
                new_data_part({"question": "release-approval", "version": report["version"], "run_id": run_id, "options": ["approve", "reject"]}),
            ]))

    # ---------- 실습 ----------
    async def _manual(self, updater: TaskUpdater, task_id: str, cycle_id: int, version: str, build_url: str, fixed: list[str]) -> None:
        self.work.mark_fixed(cycle_id, version, fixed)
        run_id = self.work.open_manual_run(cycle_id, task_id, version, build_url)
        await self._say(updater, f"v{version} 검증 항목 12건을 만들었습니다. 검증부서원의 판정을 기다립니다.")
        outcome = await self.work.wait_for_staff(run_id, updater)
        rows = self.work.db.all("SELECT key, status FROM issues WHERE cycle_id = ? AND fixed_in_version = ? AND status IN ('VERIFIED', 'REOPENED')", cycle_id, version)
        created = [r["key"] for r in self.work.db.all("SELECT key FROM issues WHERE cycle_id = ? AND version_found = ?", cycle_id, version)]
        await self._conclude(updater, cycle_id, run_id, [r["key"] for r in rows if r["status"] == "VERIFIED"],
                             [r["key"] for r in rows if r["status"] == "REOPENED"], created, outcome["decision"], outcome["decided_by"])

    # ---------- 데모 ----------
    async def _demo(self, updater: TaskUpdater, task_id: str, cycle_id: int, version: str, build_url: str, fixed: list[str]) -> None:
        w = self.work
        await self._say(updater, f"v{version} 빌드를 내려받고 있습니다.")
        build = await w.download(build_url)
        total = len(catalog.TEST_CASES)
        run_id = w.db.execute("INSERT INTO test_runs (cycle_id, task_id, version, build_url, total, status, mode, started_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', 'demo', ?)",
                              cycle_id, task_id, version, build_url, total, now())
        failures = []
        for index, tc in enumerate(catalog.TEST_CASES, start=1):
            await asyncio.sleep(config.TEST_DELAY)
            ok, log_text = catalog.run_test(build, tc["id"])
            w.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, log, checked_by) VALUES (?, ?, ?, ?, ?, 'QA Agent')",
                         run_id, tc["id"], int(ok), "PASS" if ok else "FAIL", log_text)
            w.update_counts(run_id)
            if not ok:
                failures.append((tc, log_text))
            await self._say(updater, f"{tc['id']} {'PASS' if ok else 'FAIL'} ({index}/{total})",
                            {"progress": {"done": index, "total": total, "test_case_id": tc["id"], "passed": ok}})
        w.db.execute("UPDATE test_runs SET status = 'DONE', finished_at = ? WHERE id = ?", now(), run_id)
        verified, reopened = [], []
        for key in fixed:
            issue = w.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
            if not issue:
                continue
            ok = w.db.scalar("SELECT passed FROM test_results WHERE run_id = ? AND test_case_id = ?", run_id, issue["test_case_id"])
            w.change(issue, "VERIFIED" if ok else "REOPENED", "QA Agent", f"v{version}에서 {'수정 확인' if ok else '재현'}", fixed_in_version=version)
            (verified if ok else reopened).append(key)
        created = []
        for tc, log_text in failures:
            if w.active_issue(cycle_id, tc["id"]):
                continue
            await self._say(updater, f"{tc['id']} 실패 로그를 분석하고 있습니다.")
            analysis, source = await w.analyze(tc, version, log_text)
            created.append(w.insert_issue(cycle_id, version, tc["id"], analysis, "QA Agent", source))
        failed = w.db.one("SELECT failed FROM test_runs WHERE id = ?", run_id)["failed"]
        await self._conclude(updater, cycle_id, run_id, verified, reopened, created, "TRANSFERRED" if failed else "APPROVED", "QA Agent")

    # ---------- input-required 재개 ----------
    async def _resume_approval(self, updater: TaskUpdater, task_id: str, data: dict, text: str) -> None:
        run = self.work.db.one("SELECT * FROM test_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1", task_id)
        decision = str(data.get("decision") or text).strip().lower()
        approved = decision.startswith("approve") or decision in ("승인", "yes", "y")
        comment = data.get("comment", "")
        self.work.db.execute("UPDATE test_runs SET approval = ?, approval_comment = ? WHERE id = ?", "APPROVED" if approved else "REJECTED", comment, run["id"])
        result = {"cycle_id": run["cycle_id"], "version": run["version"], "run_id": run["id"], "approved": approved, "comment": comment}
        label = "승인" if approved else "반려"
        self.work.bus.emit("Hub", "approval.received", f"v{run['version']} 릴리즈 {label} 회신 수신" + (f": {comment}" if comment else ""), {}, run["cycle_id"])
        await updater.add_artifact([new_text_part(f"v{run['version']} 릴리즈 {label}" + (f": {comment}" if comment else "")), new_data_part(result)],
                                   name="approval")
        await updater.complete(updater.new_agent_message([new_text_part(f"v{run['version']} 릴리즈 {label} 처리 완료")]))
