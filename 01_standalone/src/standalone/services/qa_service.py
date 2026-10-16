"""검증부서 서비스: 빌드 검증, 이슈 생성/재오픈/검증 완료, 개발부서 이관."""

import asyncio

from ..domain import scenario, simulator
from ..infra.db import Database, now
from ..infra.events import EventBus
from ..infra.llm import LLMClient
from .issue_store import change_status

ACTOR = "QA"

SYSTEM = "당신은 커머스 웹 서비스 검증팀의 QA 엔지니어입니다. 테스트 실패 로그를 분석해 결함 리포트를 작성합니다. 한국어로 작성합니다."

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "결함 제목 (40자 이내)"},
        "description": {"type": "string", "description": "재현 조건, 기대 결과, 실제 결과를 포함한 2~3문장"},
        "severity": {"type": "string", "enum": scenario.SEVERITIES},
    },
    "required": ["title", "description", "severity"],
    "additionalProperties": False,
}

ACTIVE_STATUSES = ("OPEN", "TRANSFERRED", "IN_PROGRESS", "REOPENED")


class QAService:
    def __init__(self, db: Database, bus: EventBus, llm: LLMClient, step_delay: float, test_delay: float):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.step_delay = step_delay
        self.test_delay = test_delay

    def seed_test_cases(self) -> None:
        for tc in scenario.TEST_CASES:
            self.db.execute("INSERT OR IGNORE INTO test_cases (id, feature, name) VALUES (?, ?, ?)", tc["id"], tc["feature"], tc["name"])

    async def verify(self, cycle_id: int, version: str) -> dict:
        release = self.db.one("SELECT * FROM releases WHERE cycle_id = ? AND version = ?", cycle_id, version)
        total = len(scenario.TEST_CASES)
        run_id = self.db.execute(
            "INSERT INTO test_runs (cycle_id, version, total, status, started_at) VALUES (?, ?, ?, 'RUNNING', ?)",
            cycle_id, version, total, now(),
        )
        self.bus.emit(ACTOR, "test_run.started", f"v{version} 검증 시작 (테스트 {total}건)", {"run_id": run_id, "version": version}, cycle_id)

        passed = 0
        failures: list[dict] = []
        for index, tc in enumerate(scenario.TEST_CASES, start=1):
            await asyncio.sleep(self.test_delay)
            ok, log = simulator.run_test(release["defects"], tc["id"])
            self.db.execute("INSERT INTO test_results (run_id, test_case_id, passed, result, log, checked_by) VALUES (?, ?, ?, ?, ?, 'Demo')",
                            run_id, tc["id"], int(ok), "PASS" if ok else "FAIL", log)
            passed += ok
            if not ok:
                failures.append({**tc, "log": log})
            self.db.execute("UPDATE test_runs SET passed = ?, failed = ? WHERE id = ?", passed, index - passed, run_id)
            self.bus.publish({"type": "test.progress", "actor": ACTOR, "cycle_id": cycle_id,
                              "payload": {"run_id": run_id, "done": index, "total": total, "test_case_id": tc["id"], "passed": ok}})

        failed = total - passed
        self.db.execute("UPDATE test_runs SET status = 'DONE', finished_at = ? WHERE id = ?", now(), run_id)
        self.bus.emit(ACTOR, "test_run.finished", f"v{version} 검증 완료: {passed} PASS / {failed} FAIL",
                      {"run_id": run_id, "passed": passed, "failed": failed}, cycle_id)

        await self._verify_fixed_issues(cycle_id, version, run_id)
        await self._register_failures(cycle_id, version, failures)
        return {"run_id": run_id, "version": version, "total": total, "passed": passed, "failed": failed}

    async def _verify_fixed_issues(self, cycle_id: int, version: str, run_id: int) -> None:
        fixed = self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ?", cycle_id, version)
        for issue in fixed:
            ok = self.db.scalar("SELECT passed FROM test_results WHERE run_id = ? AND test_case_id = ?", run_id, issue["test_case_id"])
            if ok:
                change_status(self.db, issue, "VERIFIED", ACTOR, f"v{version}에서 {issue['test_case_id']} 통과 확인")
                self.bus.emit(ACTOR, "issue.verified", f"{issue['key']} 수정 확인 (VERIFIED)", {"key": issue["key"]}, cycle_id)
            else:
                change_status(self.db, issue, "REOPENED", ACTOR, f"v{version}에서 {issue['test_case_id']} 재현")
                self.bus.emit(ACTOR, "issue.reopened", f"{issue['key']} 재현되어 재오픈 (REOPENED)", {"key": issue["key"]}, cycle_id)

    async def _register_failures(self, cycle_id: int, version: str, failures: list[dict]) -> None:
        for failure in failures:
            exists = self.db.scalar(
                f"SELECT COUNT(*) FROM issues WHERE cycle_id = ? AND test_case_id = ? AND status IN {ACTIVE_STATUSES}",
                cycle_id, failure["id"],
            )
            if exists:
                continue
            await asyncio.sleep(self.step_delay)
            analysis, source = await self._analyze(failure, version)
            key = f"ISSUE-{(self.db.scalar('SELECT COUNT(*) FROM issues WHERE cycle_id = ?', cycle_id) or 0) + 1}"
            issue_id = self.db.execute(
                "INSERT INTO issues (cycle_id, key, test_case_id, version_found, title, description, severity, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)",
                cycle_id, key, failure["id"], version, analysis["title"], analysis["description"], analysis["severity"], now(), now(),
            )
            self.db.execute(
                "INSERT INTO issue_history (issue_id, from_status, to_status, actor, note, ts) VALUES (?, NULL, 'OPEN', ?, ?, ?)",
                issue_id, ACTOR, f"{failure['id']} 실패로 등록 (분석: {source})", now(),
            )
            self.bus.emit(ACTOR, "issue.created", f"{key} 등록 [{analysis['severity']}] {analysis['title']}",
                          {"key": key, "severity": analysis["severity"], "analyzed_by": source}, cycle_id)

    async def _analyze(self, failure: dict, version: str) -> tuple[dict, str]:
        prompt = (
            f"제품: {scenario.PRODUCT} v{version}\n기능: {failure['feature']}\n"
            f"테스트케이스: {failure['id']} {failure['name']}\n실패 로그:\n{failure['log']}\n\n"
            "결함 리포트를 작성하세요. 심각도는 매출/결제 영향은 Critical, 주요 기능 장애는 Major, 불편 수준은 Minor."
        )
        return await self.llm.json(
            SYSTEM, prompt, ISSUE_SCHEMA,
            lambda: {
                "title": f"[{failure['feature']}] {failure['name']} 실패",
                "description": f"{failure['id']} 실행 시 실패. {failure['log'].splitlines()[0]}",
                "severity": scenario.FEATURE_SEVERITY[failure["feature"]],
            },
        )

    async def transfer(self, cycle_id: int) -> list[dict]:
        issues = self.db.all("SELECT * FROM issues WHERE cycle_id = ? AND status IN ('OPEN', 'REOPENED') ORDER BY id", cycle_id)
        for issue in issues:
            change_status(self.db, issue, "TRANSFERRED", ACTOR, "개발부서로 이관")
        if issues:
            self.bus.emit(ACTOR, "issues.transferred", f"이슈 {len(issues)}건 개발부서 이관: {', '.join(i['key'] for i in issues)}",
                          {"keys": [i["key"] for i in issues]}, cycle_id)
        return issues

    async def approve(self, cycle_id: int, version: str, run_id: int) -> None:
        self.bus.emit(ACTOR, "release.approved", f"v{version} 전체 테스트 통과, 릴리즈 승인", {"version": version, "run_id": run_id}, cycle_id)
