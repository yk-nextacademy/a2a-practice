"""릴리즈 사이클 실행기. 업무 흐름이 이 코드에 하드코딩되어 있다 (Stage 1의 특징)."""

import asyncio
import logging

from ..domain import scenario
from ..infra.db import Database, now
from ..infra.events import EventBus
from .dev_service import DevService
from .qa_service import QAService

log = logging.getLogger("cycle")

ACTOR = "Cycle"

# 상태 -> 파이프라인 단계 (대시보드 표시용)
STAGES = ["RELEASING", "VERIFYING", "TRANSFERRING", "FIXING", "APPROVED"]
ACTIVE = ("RELEASING", "VERIFYING", "TRANSFERRING", "FIXING")


class CycleAlreadyRunning(Exception):
    pass


class CycleRunner:
    def __init__(self, db: Database, bus: EventBus, dev: DevService, qa: QAService, llm_label: str, step_delay: float):
        self.db = db
        self.bus = bus
        self.dev = dev
        self.qa = qa
        self.llm_label = llm_label
        self.step_delay = step_delay
        self._task: asyncio.Task | None = None
        # 이전 프로세스에서 끝나지 못한 데모 사이클 정리 (실습 사이클은 사람이 진행하므로 유지)
        self.db.execute(f"UPDATE cycles SET status = 'CANCELLED', finished_at = ? WHERE mode = 'demo' AND status IN {ACTIVE}", now())

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> int:
        if self.running or self.db.scalar(f"SELECT COUNT(*) FROM cycles WHERE status IN {ACTIVE}"):
            raise CycleAlreadyRunning()
        cycle_id = self.db.execute(
            "INSERT INTO cycles (status, iteration, llm_mode, mode, started_at) VALUES ('RELEASING', 1, ?, 'demo', ?)", self.llm_label, now()
        )
        self._task = asyncio.create_task(self._run(cycle_id))
        return cycle_id

    async def stop(self) -> None:
        if self.running:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _set(self, cycle_id: int, status: str, **fields) -> None:
        sets = ", ".join(["status = ?"] + [f"{k} = ?" for k in fields])
        self.db.execute(f"UPDATE cycles SET {sets} WHERE id = ?", status, *fields.values(), cycle_id)
        self.bus.publish({"type": "cycle.status", "actor": ACTOR, "cycle_id": cycle_id, "payload": {"status": status, **fields}})

    async def _run(self, cycle_id: int) -> None:
        self.bus.emit(ACTOR, "cycle.started", f"릴리즈 사이클 #{cycle_id} 시작 (LLM: {self.llm_label})", {}, cycle_id)
        try:
            version = await self.dev.release_initial(cycle_id)
            for iteration in range(1, scenario.MAX_ITERATIONS + 1):
                self._set(cycle_id, "VERIFYING", iteration=iteration, current_version=version)
                await asyncio.sleep(self.step_delay)
                run = await self.qa.verify(cycle_id, version)

                if run["failed"] == 0:
                    await self.qa.approve(cycle_id, version, run["run_id"])
                    self._set(cycle_id, "APPROVED", finished_at=now())
                    self.bus.emit(ACTOR, "cycle.completed", f"사이클 #{cycle_id} 완료: v{version} 승인 (반복 {iteration}회)",
                                  {"version": version, "iterations": iteration}, cycle_id)
                    return

                self._set(cycle_id, "TRANSFERRING")
                await asyncio.sleep(self.step_delay)
                issues = await self.qa.transfer(cycle_id)
                await self.dev.receive_issues(cycle_id, issues)

                if iteration == scenario.MAX_ITERATIONS:
                    break
                self._set(cycle_id, "FIXING")
                await asyncio.sleep(self.step_delay)
                version = await self.dev.fix_and_release(cycle_id, issues)

            self._set(cycle_id, "FAILED", finished_at=now())
            self.bus.emit(ACTOR, "cycle.failed", f"사이클 #{cycle_id} 실패: 최대 반복 {scenario.MAX_ITERATIONS}회 초과", {}, cycle_id)
        except asyncio.CancelledError:
            self._set(cycle_id, "CANCELLED", finished_at=now())
            raise
        except Exception as exc:  # 예상하지 못한 오류도 사이클 상태로 남긴다
            log.exception("cycle %s failed", cycle_id)
            self._set(cycle_id, "FAILED", finished_at=now())
            self.bus.emit(ACTOR, "cycle.error", f"사이클 #{cycle_id} 오류: {exc}", {}, cycle_id)
