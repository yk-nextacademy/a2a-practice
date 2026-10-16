"""사이클 실행기 (Host).

- 데모: Claude(또는 규칙 기반 플래너)가 MCP 도구로 발행·검증·이관·수정·승인을 모두 직접 수행한다.
- 실습: 사람(부서원)이 Dev 웹·QA 웹에서 일한다. Dev와 QA 시스템은 서로를 모르므로,
        Host의 조정자(coordinator)가 각 시스템의 '완료 작업'을 주기적으로 조회(폴링)하고,
        발견하면 Claude(또는 규칙)가 MCP 도구로 상대 시스템에 결과를 전달한다.
        MCP 도구 호출은 요청-응답 단위라, 사람이 오래 걸리는 일은 Host가 상태를 계속 확인해야 한다.

모든 LLM 응답과 도구 호출을 agent_steps에 기록하고, 도구 호출을 관찰해 사이클 상태를 갱신한다.
"""

import asyncio
import json
import logging
import time

import anthropic

from common import config
from common.db import Database, now, now_ms
from common.events import EventBus

from .mcp_hub import McpHub
from .planner import MockCoordinator, MockPlanner

log = logging.getLogger("agent")
MAX_VERIFICATIONS = 5
POLL_SECONDS = 2.0
ACTIVE = ("RELEASING", "VERIFYING", "TRANSFERRING", "FIXING")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, iteration INTEGER NOT NULL DEFAULT 0,
    current_version TEXT, llm_mode TEXT, summary TEXT, error TEXT, turns INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, turn INTEGER NOT NULL, actor TEXT NOT NULL,
    kind TEXT NOT NULL, tool TEXT, input TEXT, output TEXT, is_error INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER, ts TEXT NOT NULL
);
"""
MIGRATIONS = {
    "cycles": {"mode": "TEXT NOT NULL DEFAULT 'demo'", "polls": "INTEGER NOT NULL DEFAULT 0", "handoffs": "INTEGER NOT NULL DEFAULT 0"},
    "agent_steps": {"run_label": "TEXT"},
}

SYSTEM_PROMPT = """당신은 커머스 웹 서비스의 릴리즈 관리 AI 에이전트입니다.
개발부서 시스템(dev__ 도구)과 검증부서 시스템(qa__ 도구)을 사용해 하나의 릴리즈 사이클을 끝까지 진행합니다.

업무 절차:
1. dev__publish_initial_release로 최초 버전을 발행한다.
2. qa__run_tests로 발행된 버전을 검증한다 (version과 build_url은 발행 결과의 값을 그대로 사용).
3. 이 버전에 반영된 수정 이슈(fixed_issue_keys)가 있으면 qa__verify_fixed_issues로 확인한다.
4. 실패한 테스트마다 qa__create_issue로 이슈를 등록한다. 실패 로그를 분석해 직접 작성한다.
   - title: 증상 중심 40자 이내, description: 재현 조건·기대 결과·실제 결과 2~3문장
   - severity: 결제/매출 영향 Critical, 주요 기능 장애 Major, 불편 수준 Minor
5. 실패가 없으면 qa__approve_release로 승인하고 사이클을 마친다.
6. 실패가 있으면 qa__transfer_issues로 이관 데이터를 만들고, 결과의 issues를 그대로 dev__register_tickets에 전달한다.
7. 등록한 티켓마다 dev__fix_ticket을 호출한다. fix_note에는 로그에서 추정한 원인과 수정 내용을 1~2문장으로 쓴다.
8. dev__publish_release로 차기 버전을 발행한다. release_note에는 반영 내용을 목록으로 쓴다. 그 다음 2번으로 돌아간다.

규칙:
- 모든 도구 호출에 주어진 cycle_id를 넣는다.
- 검증(qa__run_tests)은 최대 5회까지만 한다.
- 문서(이슈, 수정 내역, 릴리즈 노트)는 한국어로 간결하게 작성한다.
- 서로 독립적인 호출(예: 여러 이슈 등록, 여러 티켓 수정)은 한 번에 함께 요청해도 된다.
- 사이클을 마치면 결과를 2~3문장으로 요약한다."""

COORDINATOR_PROMPT = """당신은 개발부서 시스템과 검증부서 시스템을 잇는 조정 AI 에이전트입니다.
두 시스템은 서로를 모르며, 실제 업무는 각 부서의 사람이 합니다. 당신은 한 부서가 끝낸 작업의 결과를 다른 부서에 전달합니다.

상황별 처리:
- 사이클 시작: dev__request_release(cycle_id)로 개발부서에 최초 버전 발행을 요청한다.
- 개발부서 발행 완료(outcome에 version, build_url, fixed_issue_keys): qa__request_verification에 그 값을 그대로 넣어 검증을 요청하고, dev__ack_work(work_id)로 확인 처리한다.
- 검증부서 '이관' 결정(outcome.decision = transfer): qa__transfer_issues로 이관 데이터를 받고, 결과의 issues를 그대로 dev__register_tickets에 전달한 뒤, dev__request_release로 다음 버전 발행을 요청하고, qa__ack_work(work_id)로 확인 처리한다.
- 검증부서 '확정' 결정(outcome.decision = approve): qa__approve_release(cycle_id, version, run_id, comment)로 확정을 기록하고 qa__ack_work(work_id)로 확인 처리한다. comment에는 결정한 부서원과 결과를 한 문장으로 쓴다.

규칙: 주어진 값을 바꾸지 않는다. 끝나면 무엇을 어디에 전달했는지 한 문장으로 보고한다."""

MANUAL_TOOLS = {"dev__request_release", "dev__register_tickets", "dev__ack_work", "dev__list_tickets",
                "qa__request_verification", "qa__transfer_issues", "qa__approve_release", "qa__ack_work", "qa__list_issues"}
# Host 코드만 호출하는 도구 (어떤 LLM에도 노출하지 않는다): 사이클 중단 시 부서 시스템의 진행 중 작업 지시 취소
HOST_ONLY_TOOLS = {"dev__cancel_work", "qa__cancel_work"}
# 전달 패턴별로 반드시 성공해야 하는 도구. 모두 성공해야 완료 작업을 확인(ack) 처리한다
REQUIRED_CALLS = {
    "cycle_started": ("dev__request_release",),
    "dev_work_completed": ("qa__request_verification",),
    "transfer": ("qa__transfer_issues", "dev__register_tickets", "dev__request_release"),
    "approve": ("qa__approve_release",),
}
MAX_HANDOFF_RETRIES = 3

# 도구 호출 -> 사이클 상태 (대시보드 파이프라인용)
TOOL_STATUS = {
    "dev__publish_initial_release": "RELEASING", "dev__publish_release": "RELEASING",
    "qa__run_tests": "VERIFYING", "qa__verify_fixed_issues": "VERIFYING", "qa__create_issue": "VERIFYING",
    "qa__transfer_issues": "TRANSFERRING", "dev__register_tickets": "FIXING", "dev__fix_ticket": "FIXING",
}


class CycleConflict(Exception):
    pass


class AgentRunner:
    def __init__(self, db: Database, bus: EventBus, hub: McpHub, mode: str, model: str):
        self.db = db
        self.bus = bus
        self.hub = hub
        self.mode = mode
        self.model = model
        self.label = model if mode == "llm" else "mock"
        self.run_label: str | None = None
        self._required: tuple[str, ...] = ()  # 진행 중인 전달에서 성공해야 하는 도구
        self._succeeded: set[str] = set()     # 진행 중인 전달에서 성공한 도구
        self._task: asyncio.Task | None = None
        self._client = anthropic.AsyncAnthropic() if mode == "llm" else None
        db.migrate(MIGRATIONS)
        # 이전 프로세스에서 끝나지 못한 데모 사이클 정리 (실습 사이클은 resume()으로 조정을 재개)
        self.db.execute(f"UPDATE cycles SET status = 'CANCELLED', error = '서버 재시작으로 중단', finished_at = ? WHERE mode = 'demo' AND status IN {ACTIVE}",
                        now())

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def active_cycle(self) -> dict | None:
        return self.db.one(f"SELECT * FROM cycles WHERE status IN {ACTIVE} ORDER BY id DESC LIMIT 1")

    def resume(self) -> None:
        """Host 재시작 후 진행 중이던 실습 사이클의 조정을 이어 간다."""
        cycle = self.active_cycle()
        if cycle and cycle["mode"] == "manual" and not self.running:
            self._task = asyncio.create_task(self._coordinate(cycle["id"], resumed=True))

    def start(self, mode: str = "demo") -> int:
        if self.running or self.active_cycle():
            raise CycleConflict()
        cycle_id = self.db.execute("INSERT INTO cycles (status, llm_mode, mode, started_at) VALUES ('RELEASING', ?, ?, ?)", self.label, mode, now())
        self._task = asyncio.create_task(self._run(cycle_id) if mode == "demo" else self._coordinate(cycle_id))
        return cycle_id

    async def abort(self) -> None:
        """사용자 중단. 실습 사이클이면 부서 시스템에 알려 진행 중인 작업 지시(OPEN)를 바로 취소한다.
        (Host 종료 시의 stop()은 취소하지 않는다: 재시작 후 resume()으로 이어 가야 하기 때문)"""
        if not self.running:
            return
        cycle = self.active_cycle()
        await self.stop()
        if cycle and cycle["mode"] == "manual":
            for server in ("dev", "qa"):
                is_error, text, _ = await self.hub.call_tool(f"{server}__cancel_work", {"cycle_id": cycle["id"]})
                if is_error:
                    log.warning("cancel_work failed (%s): %s", server, text)

    async def stop(self) -> None:
        if self.running:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ---------- 상태/기록 ----------
    def _cycle(self, cycle_id: int) -> dict:
        return self.db.one("SELECT * FROM cycles WHERE id = ?", cycle_id)

    def _set(self, cycle_id: int, status: str | None = None, **fields) -> None:
        if status:
            fields["status"] = status
        if not fields:
            return
        self.db.execute(f"UPDATE cycles SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), cycle_id)
        if status:
            self.bus.publish({"type": "cycle.status", "actor": "Host", "cycle_id": cycle_id, "payload": {"status": status}})

    def _next_turn(self, cycle_id: int) -> int:
        return (self.db.scalar("SELECT MAX(turn) FROM agent_steps WHERE cycle_id = ?", cycle_id) or 0) + 1

    def _step(self, cycle_id: int, turn: int, actor: str, kind: str, tool: str | None = None, input_=None, output=None,
              is_error: bool = False, duration_ms: int | None = None) -> None:
        step_id = self.db.execute(
            "INSERT INTO agent_steps (cycle_id, turn, actor, kind, tool, input, output, is_error, duration_ms, run_label, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            cycle_id, turn, actor, kind, tool,
            json.dumps(input_, ensure_ascii=False, indent=1) if isinstance(input_, (dict, list)) else input_,
            output, int(is_error), duration_ms, self.run_label, now_ms(),
        )
        self.bus.publish({"type": "agent.step", "actor": actor, "cycle_id": cycle_id,
                          "payload": {"id": step_id, "kind": kind, "tool": tool, "is_error": is_error}})

    # ---------- 도구 실행 (LLM/플래너 공통) ----------
    async def execute_tool(self, cycle_id: int, turn: int, actor: str, name: str, args: dict) -> tuple[bool, str, dict | None]:
        cycle = self._cycle(cycle_id)
        if name in TOOL_STATUS:
            self._set(cycle_id, TOOL_STATUS[name])
        if name == "qa__run_tests":
            if cycle["iteration"] >= MAX_VERIFICATIONS:
                text = f"검증 횟수 제한({MAX_VERIFICATIONS}회)을 초과했습니다. 사이클을 종료하세요."
                self._step(cycle_id, turn, "Host", "tool_result", name, args, text, True, 0)
                return True, text, None
            self._set(cycle_id, iteration=cycle["iteration"] + 1, current_version=args.get("version"))
        if name.endswith("__ack_work") and self._required:
            missing = [t for t in self._required if t not in self._succeeded]
            if missing:  # 전달이 끝나지 않았으면 확인 처리하지 않는다 (다음 폴링에서 다시 전달)
                text = f"전달이 끝나지 않아 확인 처리하지 않습니다. 성공하지 않은 도구: {', '.join(missing)}"
                self._step(cycle_id, turn, "Host", "tool_result", name, args, text, True, 0)
                return True, text, None

        started = time.perf_counter()
        is_error, text, data = await self.hub.call_tool(name, args)
        duration = int((time.perf_counter() - started) * 1000)
        self.db.execute("UPDATE cycles SET tool_calls = tool_calls + 1 WHERE id = ?", cycle_id)
        self._step(cycle_id, turn, f"{name.split('__')[0].upper()} MCP", "tool_result", name, args, text, is_error, duration)
        self._emit_tool_event(cycle_id, name, args, is_error, text, data or {})
        if not is_error:
            self._succeeded.add(name)
            if name == "qa__approve_release":
                self._set(cycle_id, "APPROVED", finished_at=now())
            elif name == "qa__request_verification" and "note" not in (data or {}):  # 기존 요청 반환(재전달)은 검증 횟수에 넣지 않는다
                self._set(cycle_id, "VERIFYING", iteration=cycle["iteration"] + 1, current_version=args.get("version"))
            elif name == "dev__request_release":
                self._set(cycle_id, "RELEASING" if (data or {}).get("kind") == "initial" else "FIXING")
        await asyncio.sleep(config.STEP_DELAY if cycle["mode"] == "demo" else 0)
        return is_error, text, data

    def _emit_tool_event(self, cycle_id: int, name: str, args: dict, is_error: bool, text: str, d: dict) -> None:
        server, _, tool = name.partition("__")
        actor = "Dev MCP" if server == "dev" else "QA MCP"
        if is_error:
            self.bus.emit(actor, "tool.error", f"{tool} 오류: {text[:160]}", {"tool": name}, cycle_id)
            return
        summaries = {
            "publish_initial_release": ("release.published", lambda: f"v{d.get('version')} 발행 (기능: {', '.join(d.get('features', []))})"),
            "publish_release": ("release.published", lambda: f"v{d.get('version')} 발행 (반영 이슈: {', '.join(d.get('fixed_issue_keys', []))})"),
            "run_tests": ("test_run.finished", lambda: f"v{d.get('version')} 검증 완료: {d.get('passed')} PASS / {d.get('failed')} FAIL"),
            "verify_fixed_issues": ("issue.verified", lambda: f"수정 확인 - VERIFIED: {', '.join(d.get('verified', [])) or '-'} / REOPENED: {', '.join(d.get('reopened', [])) or '-'}"),
            "create_issue": ("issue.created", lambda: f"{d.get('key')} {'등록' if d.get('created') else '기존 이슈 사용'} [{args.get('severity')}] {args.get('title')}"),
            "transfer_issues": ("issues.transferred", lambda: f"이슈 {len(d.get('issues', []))}건 이관: {', '.join(i['key'] for i in d.get('issues', []))}"),
            "register_tickets": ("tickets.received", lambda: f"티켓 등록: {', '.join(d.get('registered', []))}"),
            "fix_ticket": ("ticket.fixed", lambda: f"{d.get('key')} 수정 완료"),
            "approve_release": ("release.approved", lambda: f"v{d.get('version')} 릴리즈 확정"),
            "request_release": ("work.requested", lambda: f"개발부서에 {'최초 버전' if d.get('kind') == 'initial' else '다음 버전'} 발행 요청 (#{d.get('work_id')})"),
            "request_verification": ("work.requested", lambda: f"검증부서에 v{args.get('version')} 검증 요청 (#{d.get('work_id')})"),
            "ack_work": ("work.acked", lambda: f"작업 #{d.get('work_id')} 전달 완료 확인"),
        }
        type_, message = summaries.get(tool, ("tool.called", lambda: f"{tool} 호출"))
        self.bus.emit(actor, type_, message(), {"tool": name}, cycle_id)

    # ---------- 데모 ----------
    async def _run(self, cycle_id: int) -> None:
        self.bus.emit("Host", "cycle.started", f"데모 사이클 #{cycle_id} 시작 (에이전트: {self.label})", {}, cycle_id)
        self.run_label = "데모 사이클"
        try:
            await self.hub.wait_ready(timeout=10)
            if self.mode == "llm":
                tools = [t for t in self.hub.anthropic_tools() if t["name"] not in HOST_ONLY_TOOLS]
                summary = await self._agent_loop(cycle_id, SYSTEM_PROMPT, f"릴리즈 사이클을 진행하세요. cycle_id={cycle_id}", tools, config.MAX_AGENT_TURNS)
            else:
                summary = await MockPlanner(self, cycle_id).run()
            cycle = self._cycle(cycle_id)
            if cycle["status"] == "APPROVED":
                self._set(cycle_id, summary=summary)
                self.bus.emit("Host", "cycle.completed", f"사이클 #{cycle_id} 완료: v{cycle['current_version']} 승인 (검증 {cycle['iteration']}회)", {}, cycle_id)
            else:
                self._set(cycle_id, "FAILED", summary=summary, error="승인 없이 에이전트가 종료됨", finished_at=now())
                self.bus.emit("Host", "cycle.failed", f"사이클 #{cycle_id} 실패: 승인 없이 종료", {}, cycle_id)
        except asyncio.CancelledError:
            self._set(cycle_id, "CANCELLED", finished_at=now())
            self.bus.emit("Host", "cycle.cancelled", f"사이클 #{cycle_id} 중단", {}, cycle_id)
            raise
        except asyncio.TimeoutError:
            self._set(cycle_id, "FAILED", error="MCP 서버에 연결할 수 없습니다.", finished_at=now())
            self.bus.emit("Host", "cycle.error", f"사이클 #{cycle_id} 오류: MCP 서버 연결 실패", {}, cycle_id)
        except Exception as exc:
            log.exception("cycle failed")
            self._set(cycle_id, "FAILED", error=f"{type(exc).__name__}: {exc}"[:300], finished_at=now())
            self.bus.emit("Host", "cycle.error", f"사이클 #{cycle_id} 오류: {exc}"[:200], {}, cycle_id)

    # ---------- 실습: 조정자 ----------
    async def _coordinate(self, cycle_id: int, resumed: bool = False) -> None:
        try:
            await self.hub.wait_ready(timeout=30)
            if not resumed:
                self.bus.emit("Host", "cycle.started", f"실습 사이클 #{cycle_id} 시작 - Host가 두 부서 사이를 조정합니다 (에이전트: {self.label})", {}, cycle_id)
                missing = await self._handoff(cycle_id, "사이클 시작", {"event": "cycle_started", "cycle_id": cycle_id})
                if missing:
                    raise RuntimeError(f"개발부서에 발행을 요청하지 못했습니다 ({', '.join(missing)} 실패)")
            retries: dict[int, int] = {}
            while self._cycle(cycle_id)["status"] in ACTIVE:
                for server in ("dev", "qa"):
                    is_error, _, data = await self.hub.call_tool(f"{server}__list_completed_work", {"cycle_id": cycle_id})
                    self.db.execute("UPDATE cycles SET polls = polls + 1 WHERE id = ?", cycle_id)
                    for work in [] if is_error else (data or {}).get("completed", []):
                        outcome = work.get("outcome") or {}
                        if server == "dev":
                            label = f"Dev 발행 완료 전달 (v{outcome.get('version')})"
                        else:
                            label = f"QA {'이관' if outcome.get('decision') == 'transfer' else '확정'} 결정 전달 (v{outcome.get('version')})"
                        self.bus.emit("Host", "work.found", f"완료 작업 발견: {label}", {}, cycle_id)
                        missing = await self._handoff(cycle_id, label, {"event": f"{server}_work_completed", "cycle_id": cycle_id, "work": work})
                        if not missing:
                            await self.hub.call_tool(f"{server}__ack_work", {"work_id": work["work_id"]})  # LLM의 ack 누락 대비(멱등)
                            continue
                        # 전달이 일부만 수행됐으면 ack하지 않는다 -> 다음 폴링에서 같은 완료 작업을 다시 전달
                        retries[work["work_id"]] = retries.get(work["work_id"], 0) + 1
                        if retries[work["work_id"]] >= MAX_HANDOFF_RETRIES:
                            raise RuntimeError(f"{label} {MAX_HANDOFF_RETRIES}회 실패 ({', '.join(missing)})")
                        self.bus.emit("Host", "work.retry", f"전달 미완료: {label} - {', '.join(missing)} 실패, 다음 확인 때 다시 전달합니다", {}, cycle_id)
                if self._cycle(cycle_id)["status"] not in ACTIVE:
                    break
                await asyncio.sleep(POLL_SECONDS)
            cycle = self._cycle(cycle_id)
            if cycle["status"] == "APPROVED":
                self.bus.emit("Host", "cycle.completed", f"실습 사이클 #{cycle_id} 완료: v{cycle['current_version']} 확정 (검증 {cycle['iteration']}회)",
                              {}, cycle_id)
        except asyncio.CancelledError:
            if self._cycle(cycle_id)["status"] in ACTIVE:
                self._set(cycle_id, "CANCELLED", finished_at=now())
                self.bus.emit("Host", "cycle.cancelled", f"실습 사이클 #{cycle_id} 중단", {}, cycle_id)
            raise
        except Exception as exc:
            log.exception("coordination failed")
            self._set(cycle_id, "FAILED", error=f"{type(exc).__name__}: {exc}"[:300], finished_at=now())
            self.bus.emit("Host", "cycle.error", f"실습 사이클 #{cycle_id} 조정 오류: {exc}"[:200], {}, cycle_id)

    async def _handoff(self, cycle_id: int, label: str, event: dict) -> list[str]:
        """완료 작업 1건을 상대 시스템에 전달한다. Returns: 성공하지 못한 필수 도구 목록 (비어 있으면 전달 완료)"""
        self.run_label = label
        self.db.execute("UPDATE cycles SET handoffs = handoffs + 1 WHERE id = ?", cycle_id)
        kind = event["event"]
        if kind == "qa_work_completed":
            kind = (event["work"].get("outcome") or {}).get("decision")
        self._required, self._succeeded = REQUIRED_CALLS.get(kind, ()), set()
        try:
            if self.mode == "llm":
                tools = [t for t in self.hub.anthropic_tools() if t["name"] in MANUAL_TOOLS]
                text = await self._agent_loop(cycle_id, COORDINATOR_PROMPT, f"{label}\n\n상황 데이터:\n{json.dumps(event, ensure_ascii=False, indent=1)}",
                                              tools, 12)
            else:
                text = await MockCoordinator(self, cycle_id).handle(event)
            self._set(cycle_id, summary=text)
            return [t for t in self._required if t not in self._succeeded]
        finally:
            self._required, self._succeeded = (), set()

    async def _agent_loop(self, cycle_id: int, system: str, user_text: str, tools: list[dict], max_turns: int) -> str:
        messages: list[dict] = [{"role": "user", "content": user_text}]
        summary = ""
        for _ in range(max_turns):
            turn = self._next_turn(cycle_id)
            started = time.perf_counter()
            response = await self._client.beta.messages.create(
                model=self.model, max_tokens=16000, system=system, tools=tools, messages=messages,
                output_config={"effort": "low"}, cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            duration = int((time.perf_counter() - started) * 1000)
            self.db.execute("UPDATE cycles SET turns = turns + 1, input_tokens = input_tokens + ?, output_tokens = output_tokens + ? WHERE id = ?",
                            response.usage.input_tokens + (response.usage.cache_read_input_tokens or 0), response.usage.output_tokens, cycle_id)
            texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            for text in texts:
                self._step(cycle_id, turn, "Claude", "text", output=text, duration_ms=duration)
                summary = text
            for block in tool_uses:
                self._step(cycle_id, turn, "Claude", "tool_use", block.name, block.input, duration_ms=duration)
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "end_turn":
                break
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                self._step(cycle_id, turn, "Host", "error", output=f"stop_reason={response.stop_reason}", is_error=True)
                break
            results = []
            for block in tool_uses:
                is_error, text, _ = await self.execute_tool(cycle_id, turn, "Claude", block.name, dict(block.input))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": text or "(empty)", "is_error": is_error})
            messages.append({"role": "user", "content": results})
        return summary
