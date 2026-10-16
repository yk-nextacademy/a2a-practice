"""개발부서 서비스: 버전 발행, 이슈 수신, 수정 후 차기 버전 발행."""

import asyncio

from ..domain import scenario, simulator
from ..infra.db import Database, now
from ..infra.events import EventBus
from ..infra.llm import LLMClient
from .issue_store import change_status

ACTOR = "Dev"

SYSTEM = "당신은 커머스 웹 서비스 개발팀의 시니어 개발자입니다. 간결하고 사실적인 한국어로 작성합니다."

FIX_SCHEMA = {
    "type": "object",
    "properties": {"fix_note": {"type": "string", "description": "원인과 수정 내용을 1~2문장으로"}},
    "required": ["fix_note"],
    "additionalProperties": False,
}

NOTE_SCHEMA = {
    "type": "object",
    "properties": {"release_note": {"type": "string", "description": "릴리즈 노트 본문(목록 형태, 5줄 이내)"}},
    "required": ["release_note"],
    "additionalProperties": False,
}


class DevService:
    def __init__(self, db: Database, bus: EventBus, llm: LLMClient, step_delay: float):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.step_delay = step_delay

    async def release_initial(self, cycle_id: int) -> str:
        version = scenario.INITIAL_VERSION
        note = f"{scenario.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(scenario.FEATURES)}"
        self._save_release(cycle_id, version, simulator.initial_defects(), [], note)
        self.bus.emit(ACTOR, "release.published", f"v{version} 발행 (기능: {', '.join(scenario.FEATURES)})",
                      {"version": version}, cycle_id)
        return version

    async def receive_issues(self, cycle_id: int, issues: list[dict]) -> None:
        for issue in issues:
            change_status(self.db, issue, "IN_PROGRESS", ACTOR, "개발 착수")
        keys = ", ".join(i["key"] for i in issues)
        self.bus.emit(ACTOR, "issues.received", f"이슈 {len(issues)}건 수신 및 착수: {keys}", {"keys": [i["key"] for i in issues]}, cycle_id)

    async def fix_and_release(self, cycle_id: int, issues: list[dict]) -> str:
        current = self.db.one("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)
        version = simulator.next_version(current["version"])

        for issue in issues:
            await asyncio.sleep(self.step_delay)
            fix_note = await self._write_fix_note(issue)
            change_status(self.db, issue, "FIXED", ACTOR, fix_note, fixed_in_version=version, fix_note=fix_note)
            self.bus.emit(ACTOR, "issue.fixed", f"{issue['key']} 수정 완료 (v{version} 반영 예정)",
                          {"key": issue["key"], "fix_note": fix_note}, cycle_id)

        defects, introduced = simulator.apply_fixes(current["defects"], [i["test_case_id"] for i in issues])
        if introduced:
            # 개발자는 모르는 정보. 학습용으로 시뮬레이터 이벤트로만 남긴다.
            self.bus.emit("Simulator", "regression.introduced", f"수정 과정에서 숨은 회귀 결함 발생: {', '.join(introduced)}",
                          {"defects": introduced}, cycle_id)

        note = await self._write_release_note(version, issues)
        self._save_release(cycle_id, version, defects, [i["key"] for i in issues], note)
        self.bus.emit(ACTOR, "release.published", f"v{version} 발행 (반영 이슈: {', '.join(i['key'] for i in issues)})",
                      {"version": version}, cycle_id)
        return version

    def _save_release(self, cycle_id: int, version: str, defects: list[str], fixed_keys: list[str], note: str) -> None:
        self.db.execute(
            "INSERT INTO releases (cycle_id, version, features, defects, fixed_issue_keys, release_note, released_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            cycle_id, version, scenario.FEATURES, defects, fixed_keys, note, now(),
        )

    async def _write_fix_note(self, issue: dict) -> str:
        prompt = (
            f"다음 결함을 수정했습니다. 수정 내역을 작성하세요.\n"
            f"이슈: {issue['key']} {issue['title']}\n설명: {issue['description']}"
        )
        result, _ = await self.llm.json(
            SYSTEM, prompt, FIX_SCHEMA,
            lambda: {"fix_note": f"{issue['title']} 원인 코드 수정 및 단위 테스트 추가"},
        )
        return result["fix_note"]

    async def _write_release_note(self, version: str, issues: list[dict]) -> str:
        lines = "\n".join(f"- {i['key']} [{i['severity']}] {i['title']}: {i.get('fix_note', '')}" for i in issues)
        prompt = f"{scenario.PRODUCT} v{version} 릴리즈 노트를 작성하세요. 반영된 수정 사항:\n{lines}"
        result, _ = await self.llm.json(
            SYSTEM, prompt, NOTE_SCHEMA,
            lambda: {"release_note": f"{scenario.PRODUCT} {version} 유지보수 릴리즈\n" + "\n".join(f"- {i['key']} {i['title']} 수정" for i in issues)},
        )
        return result["release_note"]
