"""mock 모드 플래너: LLM 대신 정해진 절차로 같은 MCP 도구를 호출한다.

LLM 모드의 시스템 프롬프트와 같은 업무 절차를 코드로 옮긴 것이다. 문서 내용은 템플릿으로 채운다.
"""

FEATURE_SEVERITY = {"payment": "Critical", "login": "Major", "report": "Major", "search": "Minor"}
ACTOR = "Planner"


class MockPlanner:
    def __init__(self, runner, cycle_id: int):
        self.runner = runner
        self.cycle_id = cycle_id
        self.turn = 0

    async def call(self, name: str, **args) -> dict:
        self.turn += 1
        if not name.endswith("ack_work"):  # ack_work는 work_id만 받는다
            args = {"cycle_id": self.cycle_id, **args}
        self.runner._step(self.cycle_id, self.turn, ACTOR, "tool_use", name, args)
        is_error, text, data = await self.runner.execute_tool(self.cycle_id, self.turn, ACTOR, name, args)
        if is_error:
            raise RuntimeError(f"{name} 실패: {text}")
        return data or {}

    def note(self, text: str) -> None:
        self.runner._step(self.cycle_id, self.turn, ACTOR, "text", output=text)

    async def run(self) -> str:
        release = await self.call("dev__publish_initial_release")
        fixed_keys: list[str] = []
        for _ in range(5):
            version = release["version"]
            run = await self.call("qa__run_tests", version=version, build_url=release["build_url"])
            if fixed_keys:
                await self.call("qa__verify_fixed_issues", version=version, run_id=run["run_id"], fixed_issue_keys=fixed_keys)
            for f in run["failures"]:
                await self.call("qa__create_issue", version=version, test_case_id=f["test_case_id"],
                                title=f"[{f['feature']}] {f['name']} 실패",
                                description=f"{f['test_case_id']} 실행 시 실패. {f['log'].splitlines()[0]}",
                                severity=FEATURE_SEVERITY[f["feature"]])
            if run["failed"] == 0:
                await self.call("qa__approve_release", version=version, run_id=run["run_id"], comment="전체 테스트 통과")
                summary = f"v{version}에서 테스트 {run['total']}건이 모두 통과하여 릴리즈를 승인했습니다."
                self.note(summary)
                return summary
            self.note(f"v{version}: 실패 {run['failed']}건 → 이관 후 수정 진행")
            transfer = await self.call("qa__transfer_issues")
            await self.call("dev__register_tickets", issues=transfer["issues"])
            for issue in transfer["issues"]:
                await self.call("dev__fix_ticket", issue_key=issue["key"], fix_note=f"{issue['title']} 원인 코드 수정 및 단위 테스트 추가")
            note = "\n".join(f"- {i['key']} {i['title']} 수정" for i in transfer["issues"])
            release = await self.call("dev__publish_release", release_note=f"유지보수 릴리즈\n{note}")
            fixed_keys = release["fixed_issue_keys"]
        return "검증 횟수 제한에 도달했습니다."


class MockCoordinator(MockPlanner):
    """mock 모드 조정자: 실습에서 한 부서의 완료 작업을 정해진 규칙으로 상대 부서에 전달한다."""

    def __init__(self, runner, cycle_id: int):
        super().__init__(runner, cycle_id)
        self.turn = runner._next_turn(cycle_id) - 1

    async def handle(self, event: dict) -> str:
        kind = event["event"]
        if kind == "cycle_started":
            await self.call("dev__request_release")
            summary = "개발부서에 최초 버전 발행을 요청했습니다."
        elif kind == "dev_work_completed":
            work, o = event["work"], event["work"]["outcome"]
            await self.call("qa__request_verification", version=o["version"], build_url=o["build_url"], fixed_issue_keys=o["fixed_issue_keys"])
            await self.call("dev__ack_work", work_id=work["work_id"])
            summary = f"v{o['version']} 발행 결과를 검증부서에 전달하고 검증을 요청했습니다."
        else:
            work, o = event["work"], event["work"]["outcome"]
            if o["decision"] == "transfer":
                transfer = await self.call("qa__transfer_issues")
                await self.call("dev__register_tickets", issues=transfer["issues"])
                await self.call("dev__request_release")
                await self.call("qa__ack_work", work_id=work["work_id"])
                summary = f"이슈 {len(transfer['issues'])}건을 개발부서 티켓으로 전달하고 다음 버전 발행을 요청했습니다."
            else:
                await self.call("qa__approve_release", version=o["version"], run_id=o["run_id"],
                                comment=f"검증부서 {o['decided_by']} 확정 ({o['passed']} PASS)")
                await self.call("qa__ack_work", work_id=work["work_id"])
                summary = f"v{o['version']} 확정을 기록했습니다."
        self.note(summary)
        return summary
