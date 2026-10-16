"""Hub 워크플로 엔진.

Hub는 업무 '목표'를 에이전트에 위임하고(Task), 결과(Artifact)를 받아 다음 행동을 판단한다.
에이전트가 일을 어떻게 하는지는 모른다. Dev와 QA는 서로를 모르며, Hub를 통해서만 협업한다.

Hub가 재시작해도 에이전트의 Task는 에이전트 프로세스에 남아 있다. 재시작 직후 recover()가
진행 중이던 사이클의 마지막 Task를 GetTask로 확인하고, 살아 있으면 SubscribeToTask로 다시 구독해 이어 간다.
"""

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field

from a2a.helpers import get_artifact_text
from a2a.types import CancelTaskRequest, GetTaskRequest, SendMessageRequest, StreamResponse, SubscribeToTaskRequest, TaskState
from google.protobuf.json_format import MessageToDict

from common import config
from common.a2a_parts import first_data, normalize, user_message
from common.db import Database, now
from common.events import EventBus

from .audit import current_cycle
from .decider import Decider
from .registry import AgentEntry, Registry

log = logging.getLogger("workflow")
ACTIVE = ("RELEASING", "VERIFYING", "TRANSFERRING", "FIXING", "AWAITING_APPROVAL")
LIVE = (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)
DONE_OK = (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_INPUT_REQUIRED)
RECOVER_WAIT = 15  # Hub 재시작 직후 에이전트 카드가 수집될 때까지 기다리는 최대 시간(초)


class CycleConflict(Exception):
    pass


class AgentLost(Exception):
    """에이전트 연결이 끊겼거나(중단·재시작) 에이전트에서 Task를 찾을 수 없다."""


@dataclass
class TaskResult:
    agent: str
    task_id: str = ""
    context_id: str = ""
    state: int = TaskState.TASK_STATE_UNSPECIFIED
    message: str = ""
    message_data: dict = field(default_factory=dict)
    artifacts: dict[str, tuple[str, dict]] = field(default_factory=dict)

    @property
    def state_name(self) -> str:
        return TaskState.Name(self.state).replace("TASK_STATE_", "")

    def data(self, name: str) -> dict:
        return self.artifacts.get(name, ("", {}))[1]


class Workflow:
    def __init__(self, db: Database, bus: EventBus, registry: Registry, decider: Decider, label: str):
        self.db = db
        self.bus = bus
        self.registry = registry
        self.decider = decider
        self.label = label
        self._task: asyncio.Task | None = None
        self._approvals: dict[int, asyncio.Future] = {}
        self._aborting = False   # abort() 진행 중: 이 사이에 끝난 Task(canceled)는 실패가 아니라 중단으로 기록
        self._detaching = False  # Hub 종료 중: 에이전트 Task는 그대로 두고 사이클 상태도 바꾸지 않는다
        db.migrate({"cycles": {"mode": "TEXT NOT NULL DEFAULT 'demo'"}})
        # 진행 중인 사이클에 속하지 않는 승인 대기는 회신할 곳이 없으므로 만료
        db.execute(f"UPDATE approvals SET status = 'EXPIRED' WHERE status = 'PENDING' AND cycle_id NOT IN (SELECT id FROM cycles WHERE status IN {ACTIVE})")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, mode: str = "demo") -> int:
        if self.running:
            raise CycleConflict()
        cycle_id = self.db.execute("INSERT INTO cycles (status, llm_mode, mode, started_at) VALUES ('RELEASING', ?, ?, ?)", self.label, mode, now())
        self._launch(self._run(cycle_id, mode))
        return cycle_id

    def _launch(self, coro) -> None:
        self._aborting = self._detaching = False
        self._task = asyncio.create_task(coro)

    def recover(self) -> None:
        """Hub 재시작 직후 호출. 진행 중이던 사이클(가장 최근 하나)을 이어 가도록 워크플로를 다시 띄운다."""
        cycles = self.db.all(f"SELECT * FROM cycles WHERE status IN {ACTIVE} ORDER BY id")
        for stale in cycles[:-1]:
            self._cancelled(stale["id"], "Hub 재시작 시 더 최근 사이클이 있어 중단")
        if cycles:
            self._launch(self._run(cycles[-1]["id"], cycles[-1]["mode"], resume=True))

    async def abort(self) -> None:
        """진행 중인 A2A Task를 에이전트에 취소 요청(CancelTask)한 뒤 워크플로를 멈춘다."""
        if not self.running:
            return
        self._aborting = True
        cycle = self.db.one("SELECT id FROM cycles ORDER BY id DESC LIMIT 1")
        current_cycle.set(cycle["id"] if cycle else None)
        open_tasks = self.db.all("SELECT * FROM tasks WHERE cycle_id = ? AND state IN ('SUBMITTED', 'WORKING', 'INPUT_REQUIRED')",
                                 cycle["id"] if cycle else 0)
        for task in open_tasks:
            entry = next((e for e in self.registry.entries.values() if e.name == task["agent"] and e.client), None)
            if entry:
                try:
                    await entry.client.cancel_task(CancelTaskRequest(id=task["task_id"]))
                    self.db.execute("UPDATE tasks SET state = 'CANCELED', updated_at = ? WHERE id = ?", now(), task["id"])
                except Exception as exc:
                    log.warning("cancel task failed: %s", exc)
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def detach(self) -> None:
        """Hub 종료 시 호출. 에이전트의 Task는 취소하지 않고(재시작 후 recover()로 다시 연결) 워크플로만 멈춘다."""
        if not self.running:
            return
        self._detaching = True
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    def decide_approval(self, approval_id: int, approve: bool, comment: str) -> bool:
        future = self._approvals.get(approval_id)
        if not future or future.done():
            return False
        future.set_result((approve, comment))
        return True

    # ---------- 상태 ----------
    def _set(self, cycle_id: int, status: str | None = None, **fields) -> None:
        if status:
            fields["status"] = status
        self.db.execute(f"UPDATE cycles SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), cycle_id)
        if status:
            self.bus.publish({"type": "cycle.status", "actor": "Hub", "cycle_id": cycle_id, "payload": {"status": status}})

    def _close_approvals(self, cycle_id: int, status: str, reason: str) -> None:
        """사이클이 끝나면 회신할 Task가 없으므로 남은 승인 대기를 닫는다 (승인함에서 사라지고 처리 이력에 남는다)."""
        self.db.execute("UPDATE approvals SET status = ?, comment = ?, decided_at = ? WHERE cycle_id = ? AND status = 'PENDING'",
                        status, reason, now(), cycle_id)

    def _fail(self, cycle_id: int, reason: str) -> None:
        self._set(cycle_id, "FAILED", error=reason, finished_at=now())
        self._close_approvals(cycle_id, "EXPIRED", "사이클 실패")
        self.bus.emit("Hub", "cycle.failed", f"사이클 #{cycle_id} 실패: {reason}", {}, cycle_id)

    def _cancelled(self, cycle_id: int, reason: str) -> None:
        self._set(cycle_id, "CANCELLED", error=reason, finished_at=now())
        self._close_approvals(cycle_id, "CANCELLED", reason)
        self.db.execute("UPDATE test_runs SET approval = 'CANCELLED' WHERE cycle_id = ? AND approval = 'PENDING'", cycle_id)
        self.bus.emit("Hub", "cycle.cancelled", f"사이클 #{cycle_id} 중단: {reason}", {}, cycle_id)

    # ---------- 실행 ----------
    async def _run(self, cycle_id: int, mode: str = "demo", resume: bool = False) -> None:
        current_cycle.set(cycle_id)
        if not resume:
            self.bus.emit("Hub", "cycle.started", f"{'실습' if mode == 'manual' else '데모'} 사이클 #{cycle_id} 시작 (Hub 판단: {self.label})" + (" - 각 에이전트는 부서원의 작업을 기다립니다" if mode == "manual" else ""), {}, cycle_id)
        try:
            agents = await self._route(cycle_id, wait=resume)
            if agents is None:
                return
            if resume:
                step, result = await self._resume(cycle_id, mode, agents)
            else:
                result = await self._delegate(cycle_id, agents[0], "release-build", f"사이클 {cycle_id}의 최초 버전을 발행해 주세요.",
                                              {"skill": "release-build", "cycle_id": cycle_id, "mode": mode})
                step = "release"
            await self._loop(cycle_id, mode, agents, step, result)
        except asyncio.CancelledError:
            if not self._detaching:
                self._cancelled(cycle_id, "사용자 중단 (CancelTask 전송)")
            raise
        except Exception as exc:
            if self._aborting:  # 중단 요청과 경합해 Task가 먼저 canceled로 끝난 경우
                log.info("cycle %s: task ended during abort (%s) - recorded as CANCELLED", cycle_id, exc)
                self._cancelled(cycle_id, "사용자 중단 (CancelTask 전송)")
                return
            log.exception("cycle failed")
            self._fail(cycle_id, (str(exc) if isinstance(exc, AgentLost) else f"{type(exc).__name__}: {exc}")[:300])

    async def _route(self, cycle_id: int, wait: bool = False) -> tuple[AgentEntry, AgentEntry, AgentEntry] | None:
        """Agent Card의 스킬로 담당 에이전트를 찾는다. 재시작 직후(wait)에는 에이전트가 뜰 때까지 잠시 기다린다."""
        skills = ("release-build", "verify-release", "fix-and-release")
        for attempt in range(RECOVER_WAIT if wait else 1):
            await self.registry.refresh()
            found = tuple(self.registry.find(s) for s in skills)
            if all(found):
                break
            await asyncio.sleep(1 if wait else 0)
        missing = [s for s, a in zip(skills, found) if a is None]
        if missing:
            self._fail(cycle_id, f"스킬을 제공하는 에이전트가 없습니다: {', '.join(missing)}")
            return None
        releaser, verifier, _ = found
        self.bus.emit("Hub", "routing", f"스킬 라우팅: release-build·fix-and-release → {releaser.name}, verify-release → {verifier.name}", {}, cycle_id)
        return found

    async def _loop(self, cycle_id: int, mode: str, agents: tuple, step: str, result: "TaskResult") -> None:
        """step="release": 발행 결과를 받은 직후, step="verify": 검증 결과를 받은 직후부터 사이클을 진행한다."""
        _, verifier, fixer = agents
        while True:
            if step == "release":
                release = self._record_release(cycle_id, result)
                # 검증 회차 = 이 사이클의 발행 수 (재시작 후 같은 발행을 다시 받아도 회차가 늘지 않도록 카운트로 계산)
                iteration = self.db.scalar("SELECT COUNT(*) FROM releases WHERE cycle_id = ?", cycle_id)
                self._set(cycle_id, "VERIFYING", iteration=iteration, current_version=release["version"])
                result = await self._delegate(
                    cycle_id, verifier, "verify-release", f"v{release['version']}을(를) 검증해 주세요.",
                    {"skill": "verify-release", "cycle_id": cycle_id, "version": release["version"],
                     "build_url": release["build_url"], "fixed_issue_keys": release["fixed_issue_keys"], "mode": mode})
            iteration = self.db.scalar("SELECT iteration FROM cycles WHERE id = ?", cycle_id)
            report = self._record_test_report(cycle_id, result)

            if result.state == TaskState.TASK_STATE_INPUT_REQUIRED or "approval" in result.artifacts:
                allowed = ["request_approval", "abort"]
            elif result.state == TaskState.TASK_STATE_COMPLETED and result.data("issues") and iteration < config.MAX_ITERATIONS:
                allowed = ["transfer_to_dev", "abort"]
            else:
                allowed = ["abort"]
            decision = await self._decide(cycle_id, iteration, report, allowed)

            if decision["action"] == "request_approval":
                await self._approval(cycle_id, verifier, result, report, decision)
                return
            if decision["action"] == "abort":
                self._fail(cycle_id, decision["reason"])
                return

            issues = result.data("issues")["issues"]
            self._set(cycle_id, "TRANSFERRING")
            self._record_transfer(cycle_id, issues, verifier.name, fixer.name)
            await asyncio.sleep(config.STEP_DELAY)
            self._set(cycle_id, "FIXING")
            result = await self._delegate(cycle_id, fixer, "fix-and-release", f"이슈 {len(issues)}건을 수정하고 차기 버전을 발행해 주세요.",
                                          {"skill": "fix-and-release", "cycle_id": cycle_id, "issues": issues, "mode": mode})
            step = "release"

    async def _resume(self, cycle_id: int, mode: str, agents: tuple) -> tuple[str, "TaskResult"]:
        """Hub 재시작 후: 사이클의 마지막 Task에 다시 연결해 결과를 받고, 그 다음 단계부터 이어 간다."""
        row = self.db.one("SELECT * FROM tasks WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)
        if not row:  # 첫 위임 전에 재시작: 처음부터 위임한다
            self.bus.emit("Hub", "cycle.resumed", f"Hub 재시작: 사이클 #{cycle_id}의 최초 발행을 다시 위임합니다", {}, cycle_id)
            return "release", await self._delegate(cycle_id, agents[0], "release-build", f"사이클 {cycle_id}의 최초 버전을 발행해 주세요.",
                                                   {"skill": "release-build", "cycle_id": cycle_id, "mode": mode})
        agent = next((a for a in agents if a.name == row["agent"]), None)
        if agent is None:
            raise AgentLost(f"Hub 재시작 후 {row['agent']}을(를) 찾을 수 없어 이어 갈 수 없습니다.")
        self.bus.emit("Hub", "cycle.resumed", f"Hub 재시작: {agent.name}의 Task {row['task_id'][:8]} [{row['skill']}]에 다시 연결합니다", {}, cycle_id)
        try:
            task = await agent.client.get_task(GetTaskRequest(id=row["task_id"]))
        except Exception as exc:
            raise AgentLost(f"{agent.name}에서 Task {row['task_id'][:8]}을(를) 찾을 수 없습니다. 에이전트도 재시작되어 Task가 사라졌습니다 ({type(exc).__name__}).")
        if task.status.state in LIVE:
            events = agent.client.subscribe(SubscribeToTaskRequest(id=task.id))  # 첫 이벤트는 현재 Task 스냅샷
        else:
            events = _single(StreamResponse(task=task))
        result = TaskResult(agent=agent.name, task_id=row["task_id"], context_id=row["context_id"] or "")
        await self._consume(cycle_id, agent, row["skill"], json.loads(row["request"] or "{}"), events, result, row["id"])
        return ("verify" if row["skill"] == "verify-release" else "release"), result

    async def _decide(self, cycle_id: int, iteration: int, report: dict, allowed: list[str]) -> dict:
        done = self.db.one("SELECT * FROM decisions WHERE cycle_id = ? AND iteration = ? ORDER BY id DESC LIMIT 1", cycle_id, iteration)
        if done and done["action"] in allowed:  # 재시작 전에 이미 내린 판단
            return done
        state = {
            "cycle_id": cycle_id, "iteration": iteration, "max_iterations": config.MAX_ITERATIONS,
            "last_test_report": {k: report.get(k) for k in ("version", "total", "passed", "failed", "verified", "reopened")},
            "history": self.db.all("SELECT version, passed, failed, total FROM test_runs WHERE cycle_id = ? ORDER BY id", cycle_id),
            "issues": self.db.all("SELECT key, severity, status, title FROM issues WHERE cycle_id = ? ORDER BY id", cycle_id),
        }
        if allowed == ["abort"]:
            decision = {"action": "abort", "reason": f"검증 {iteration}회 후에도 결함이 남아 최대 반복 횟수에 도달했습니다.", "brief": "", "decided_by": "rule"}
        else:
            decision = await self.decider.decide(state, allowed)
        self.db.execute("INSERT INTO decisions (cycle_id, iteration, action, reason, brief, decided_by, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        cycle_id, iteration, decision["action"], decision["reason"], decision.get("brief"), decision["decided_by"], now())
        self.bus.emit("Hub", "decision", f"판단: {decision['action']} - {decision['reason']}", {"decided_by": decision["decided_by"]}, cycle_id)
        return decision

    async def _approval(self, cycle_id: int, verifier: AgentEntry, result: TaskResult, report: dict, decision: dict) -> None:
        row = self.db.one("SELECT * FROM approvals WHERE task_id = ? ORDER BY id DESC LIMIT 1", result.task_id)
        if row and row["status"] in ("APPROVED", "REJECTED"):  # 재시작 전에 이미 결정됨
            approve, comment = row["status"] == "APPROVED", row["comment"] or ""
        else:
            self._set(cycle_id, "AWAITING_APPROVAL")
            if row:
                approval_id = row["id"]
                self.bus.emit("Hub", "approval.requested", f"승인 대기 재개: v{report['version']} (Hub 재시작 전 요청)", {"approval_id": approval_id}, cycle_id)
            else:
                approval_id = self.db.execute(
                    "INSERT INTO approvals (cycle_id, agent, task_id, context_id, version, question, brief, status, requested_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                    cycle_id, verifier.name, result.task_id, result.context_id, report["version"], result.message, decision.get("brief"), now(),
                )
                self.bus.emit("Hub", "approval.requested", f"승인 요청: v{report['version']} ({verifier.name}의 input-required)", {"approval_id": approval_id}, cycle_id)
            future = asyncio.get_running_loop().create_future()
            self._approvals[approval_id] = future
            try:
                approve, comment = await future
            finally:
                self._approvals.pop(approval_id, None)
            self.db.execute("UPDATE approvals SET status = ?, comment = ?, decided_at = ? WHERE id = ?",
                            "APPROVED" if approve else "REJECTED", comment, now(), approval_id)
            self.bus.emit("Hub", "approval.decided", f"릴리즈 매니저 {'승인' if approve else '반려'}" + (f": {comment}" if comment else ""), {}, cycle_id)

        if "approval" in result.artifacts:  # 회신은 이미 전달되어 Task가 완료됨 (재시작 전)
            final = result
        else:
            final = await self._delegate(cycle_id, verifier, "verify-release", "approve" if approve else "reject",
                                         {"decision": "approve" if approve else "reject", "comment": comment},
                                         task_id=result.task_id, context_id=result.context_id)
        outcome = final.data("approval")
        approved = bool(outcome.get("approved"))
        self.db.execute("UPDATE test_runs SET approval = ? WHERE cycle_id = ? AND version = ?", "APPROVED" if approved else "REJECTED",
                        cycle_id, report["version"])
        self._set(cycle_id, "APPROVED" if approved else "REJECTED", finished_at=now())
        self.bus.emit("Hub", "cycle.completed" if approved else "cycle.rejected",
                      f"사이클 #{cycle_id} {'완료: v' + report['version'] + ' 승인' if approved else '종료: 릴리즈 반려'}", {}, cycle_id)

    # ---------- A2A 위임 ----------
    async def _delegate(self, cycle_id: int, agent: AgentEntry, skill: str, text: str, data: dict,
                        task_id: str = "", context_id: str = "") -> TaskResult:
        result = TaskResult(agent=agent.name, task_id=task_id, context_id=context_id)
        message = user_message(text, data, task_id=task_id, context_id=context_id or f"cycle-{cycle_id}")
        row_id = None
        if task_id:
            row_id = self.db.scalar("SELECT id FROM tasks WHERE task_id = ? ORDER BY id DESC LIMIT 1", task_id)
        else:
            self.bus.emit("Hub", "task.delegated", f"{agent.name}에 위임 [{skill}]: {text}", {"skill": skill}, cycle_id)
        await self._consume(cycle_id, agent, skill, data, agent.client.send_message(SendMessageRequest(message=message)), result, row_id)
        return result

    async def _consume(self, cycle_id: int, agent: AgentEntry, skill: str, data: dict, events, result: TaskResult, row_id: int | None) -> None:
        """Task 이벤트 스트림(SendStreamingMessage 또는 SubscribeToTask)을 읽어 Hub 기록에 반영한다."""

        def save(state: int, last: str | None = None) -> None:
            nonlocal row_id
            name = TaskState.Name(state).replace("TASK_STATE_", "")
            if row_id is None and result.task_id:
                row_id = self.db.execute(
                    "INSERT INTO tasks (cycle_id, agent, skill, task_id, context_id, state, request, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    cycle_id, agent.name, skill, result.task_id, result.context_id, name, json.dumps(data, ensure_ascii=False), now(), now())
            elif row_id is not None:
                self.db.execute("UPDATE tasks SET state = ?, last_message = COALESCE(?, last_message), updated_at = ? WHERE id = ?",
                                name, last, now(), row_id)
            self.bus.publish({"type": "task.state", "actor": agent.name, "cycle_id": cycle_id, "payload": {"state": name}})

        def artifact(item) -> None:
            text_part, payload = get_artifact_text(item), first_data(item.parts)
            result.artifacts[item.name] = (text_part, payload)
            if not self.db.scalar("SELECT id FROM artifacts WHERE task_id = ? AND name = ?", result.task_id, item.name):
                self.db.execute("INSERT INTO artifacts (cycle_id, agent, task_id, name, text, payload, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                cycle_id, agent.name, result.task_id, item.name, text_part, payload, now())

        try:
            async for event in events:
                kind = event.WhichOneof("payload")
                if kind == "task":
                    task = event.task
                    result.task_id, result.context_id, result.state = task.id, task.context_id, task.status.state
                    if task.status.HasField("message"):  # 재구독 스냅샷에는 마지막 상태 메시지와 Artifact가 들어 있다
                        parts = task.status.message.parts
                        result.message, result.message_data = " ".join(p.text for p in parts if p.text), first_data(parts)
                    save(result.state, result.message or None)
                    for item in task.artifacts:
                        artifact(item)
                elif kind == "status_update":
                    update = event.status_update
                    result.task_id = result.task_id or update.task_id
                    result.context_id = result.context_id or update.context_id
                    result.state = update.status.state
                    parts = update.status.message.parts
                    text_out = " ".join(p.text for p in parts if p.text)
                    result.message, result.message_data = text_out, first_data(parts)
                    progress = normalize(MessageToDict(update.metadata)).get("progress") if update.HasField("metadata") else None
                    save(result.state, text_out or None)
                    if progress:
                        self.bus.publish({"type": "test.progress", "actor": agent.name, "cycle_id": cycle_id, "payload": progress})
                    elif text_out:
                        self.bus.emit(agent.name.split()[0], "task.status", f"[{result.state_name}] {text_out}", {"task_id": result.task_id}, cycle_id)
                elif kind == "artifact_update":
                    artifact(event.artifact_update.artifact)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._aborting:
                raise
            raise AgentLost(f"{agent.name} 연결이 끊겼습니다 [{skill}]. 에이전트가 중단·재시작되면 진행 중이던 Task는 복구되지 않습니다 ({type(exc).__name__}).") from exc
        if result.state in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED, TaskState.TASK_STATE_CANCELED):
            raise RuntimeError(f"{agent.name} 작업 실패 ({result.state_name}): {result.message}")
        if result.state not in DONE_OK:
            raise AgentLost(f"{agent.name}의 Task 스트림이 {result.state_name} 상태에서 끊겼습니다 [{skill}]. 에이전트가 중단·재시작되었을 수 있습니다.")

    # ---------- Artifact → Hub 업무 현황 ----------
    def _issue_status(self, cycle_id: int, key: str, to_status: str, source: str, note: str, **fields) -> None:
        issue = self.db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
        if not issue:
            return
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), issue["id"])
        self.db.execute("INSERT INTO issue_history (cycle_id, key, from_status, to_status, source, note, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        cycle_id, key, issue["status"], to_status, source, note, now())

    def _record_release(self, cycle_id: int, result: TaskResult) -> dict:
        note, data = result.artifacts["release"]
        if self.db.scalar("SELECT id FROM releases WHERE task_id = ?", result.task_id):  # 재시작 전에 이미 반영
            return data
        self.db.execute("INSERT INTO releases (cycle_id, version, build_url, features, fixed_issue_keys, release_note, agent, task_id, released_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        cycle_id, data["version"], data["build_url"], data.get("features", []), data.get("fixed_issue_keys", []), note,
                        result.agent, result.task_id, now())
        for fix in data.get("fixes", []):
            self._issue_status(cycle_id, fix["key"], "FIXED", result.agent, fix["fix_note"], fix_note=fix["fix_note"], fixed_in_version=data["version"])
        keys = data.get("fixed_issue_keys", [])
        self.bus.emit(result.agent.split()[0], "release.published",
                      f"v{data['version']} 발행" + (f" (반영 이슈: {', '.join(keys)})" if keys else ""), {"version": data["version"]}, cycle_id)
        return data

    def _record_test_report(self, cycle_id: int, result: TaskResult) -> dict:
        _, report = result.artifacts["test-report"]
        if self.db.scalar("SELECT id FROM test_runs WHERE task_id = ?", result.task_id):  # 재시작 전에 이미 반영
            return report
        self.db.execute(
            "INSERT INTO test_runs (cycle_id, version, total, passed, failed, status, results, verified, reopened, agent, task_id, approval, started_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, 'DONE', ?, ?, ?, ?, ?, ?, ?, ?)",
            cycle_id, report["version"], report["total"], report["passed"], report["failed"], report["results"],
            report.get("verified", []), report.get("reopened", []), result.agent, result.task_id,
            "PENDING" if result.state == TaskState.TASK_STATE_INPUT_REQUIRED else None, now(), now(),
        )
        for key in report.get("verified", []):
            self._issue_status(cycle_id, key, "VERIFIED", result.agent, f"v{report['version']}에서 수정 확인")
        for key in report.get("reopened", []):
            self._issue_status(cycle_id, key, "REOPENED", result.agent, f"v{report['version']}에서 재현")
        for issue in result.data("issues").get("issues", []):
            if not self.db.one("SELECT id FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, issue["key"]):
                self.db.execute(
                    "INSERT INTO issues (cycle_id, key, test_case_id, title, description, severity, status, version_found, analyzed_by, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
                    cycle_id, issue["key"], issue["test_case_id"], issue["title"], issue.get("description"), issue["severity"],
                    issue["version_found"], issue.get("analyzed_by"), now(), now(),
                )
                self.db.execute("INSERT INTO issue_history (cycle_id, key, from_status, to_status, source, note, ts) VALUES (?, ?, NULL, 'OPEN', ?, ?, ?)",
                                cycle_id, issue["key"], result.agent, f"{issue['test_case_id']} 실패 (분석: {issue.get('analyzed_by')})", now())
                self.bus.emit(result.agent.split()[0], "issue.created", f"{issue['key']} 등록 [{issue['severity']}] {issue['title']}", {}, cycle_id)
        self.bus.emit(result.agent.split()[0], "test_run.finished", f"v{report['version']} 검증 완료: {report['passed']} PASS / {report['failed']} FAIL",
                      {}, cycle_id)
        return report

    def _record_transfer(self, cycle_id: int, issues: list[dict], source: str, target: str) -> None:
        for issue in issues:
            self._issue_status(cycle_id, issue["key"], "TRANSFERRED", "Hub", f"{source} → {target} 라우팅")
        self.bus.emit("Hub", "issues.transferred", f"이슈 {len(issues)}건 라우팅: {', '.join(i['key'] for i in issues)} → {target}", {}, cycle_id)


async def _single(event):
    """이미 끝난 Task(GetTask 결과)를 스트림처럼 한 번 흘려보낸다."""
    yield event
