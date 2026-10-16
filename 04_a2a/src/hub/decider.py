"""Hub의 판단부: 에이전트 결과를 보고 다음 행동을 결정한다 (LLM 구조화 출력 / mock 규칙).

선택지는 현재 상태에서 가능한 행동으로 제한해 준다 (예: QA가 승인을 묻는 경우에만 request_approval).
"""

import json

from common.llm import LLMClient

SYSTEM = (
    "당신은 소프트웨어 릴리즈 허브(오케스트레이터)입니다. 개발부서·검증부서 에이전트의 작업 결과를 보고 다음 행동을 결정합니다. "
    "판단 근거를 한국어로 간결하게 남깁니다."
)
ACTIONS = {
    "transfer_to_dev": "실패 이슈를 개발부서 에이전트에 넘겨 수정·재발행을 요청",
    "request_approval": "검증부서의 승인 요청을 사람(릴리즈 매니저)에게 올림",
    "abort": "사이클 중단",
}


class Decider:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def decide(self, state: dict, allowed: list[str]) -> dict:
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": allowed},
                "reason": {"type": "string", "description": "판단 근거 1~2문장"},
                "brief": {"type": "string", "description": "request_approval일 때 승인자에게 보여 줄 요약(이력·위험 요소 포함 3문장 이내). 그 외에는 빈 문자열"},
            },
            "required": ["action", "reason", "brief"], "additionalProperties": False,
        }
        prompt = (
            "현재 사이클 상태:\n" + json.dumps(state, ensure_ascii=False, indent=1) +
            "\n\n선택 가능한 행동:\n" + "\n".join(f"- {a}: {ACTIONS[a]}" for a in allowed) +
            f"\n\n검증은 최대 {state['max_iterations']}회까지 가능합니다. 다음 행동을 결정하세요."
        )
        result, source = await self.llm.json(SYSTEM, prompt, schema, lambda: self._rule(state, allowed))
        if result.get("action") not in allowed:
            result, source = self._rule(state, allowed), "mock"
        return {**result, "decided_by": self.llm.model if source == "llm" else "rule"}

    @staticmethod
    def _rule(state: dict, allowed: list[str]) -> dict:
        report = state["last_test_report"]
        if "request_approval" in allowed:
            history = ", ".join(f"v{r['version']} {r['passed']}/{r['total']}" for r in state["history"])
            return {"action": "request_approval", "reason": "검증부서가 전체 통과를 보고하고 승인을 요청했습니다.",
                    "brief": f"v{report['version']} 테스트 {report['total']}건 전체 통과. 검증 이력: {history}. 누적 이슈 {len(state['issues'])}건 모두 해결."}
        if "transfer_to_dev" in allowed and state["iteration"] < state["max_iterations"]:
            return {"action": "transfer_to_dev", "reason": f"실패 {report['failed']}건이 있어 개발부서에 수정을 요청합니다.", "brief": ""}
        return {"action": "abort", "reason": "더 진행할 수 없는 상태입니다.", "brief": ""}
