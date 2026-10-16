"""개발부서 업무 로직: 버전 발행, 접수된 문제(티켓) 수정. QA와는 HTTP API로만 통신한다.

- 데모: 티켓을 접수하면 자동으로 수정하고 차기 버전을 발행한다 (Webhook 체인).
- 실습: 개발부서원이 Dev 웹에서 티켓을 착수·수정하고 버전을 발행한다.
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

log = logging.getLogger("dev")
ACTOR = "Dev"
ACTIVE = ("RELEASING", "VERIFYING", "TRANSFERRING", "FIXING")

TEAM = [
    {"name": "최개발", "role": "백엔드 개발"},
    {"name": "정개발", "role": "프론트엔드 개발"},
]

SYSTEM = "당신은 커머스 웹 서비스 개발팀의 시니어 개발자입니다. 간결하고 사실적인 한국어로 작성합니다."
FIX_SCHEMA = {
    "type": "object",
    "properties": {"fix_note": {"type": "string", "description": "원인과 수정 내용을 1~2문장으로"}},
    "required": ["fix_note"], "additionalProperties": False,
}
NOTE_SCHEMA = {
    "type": "object",
    "properties": {"release_note": {"type": "string", "description": "릴리즈 노트 본문(목록 형태, 5줄 이내)"}},
    "required": ["release_note"], "additionalProperties": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, iteration INTEGER NOT NULL DEFAULT 1,
    current_version TEXT, llm_mode TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, version TEXT NOT NULL,
    features TEXT NOT NULL, build TEXT NOT NULL, fixed_issue_keys TEXT NOT NULL, release_note TEXT,
    released_at TEXT NOT NULL, approved_at TEXT, UNIQUE (cycle_id, version)
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, key TEXT NOT NULL, test_case_id TEXT NOT NULL,
    title TEXT NOT NULL, description TEXT, severity TEXT NOT NULL, version_found TEXT NOT NULL, status TEXT NOT NULL,
    fix_note TEXT, fixed_in_version TEXT, received_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (cycle_id, key)
);
CREATE TABLE IF NOT EXISTS ticket_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    note TEXT, ts TEXT NOT NULL
);
"""
MIGRATIONS = {
    "cycles": {"mode": "TEXT NOT NULL DEFAULT 'demo'"},
    "releases": {"released_by": "TEXT"},
    "tickets": {"assignee": "TEXT"},
    "ticket_history": {"actor": "TEXT"},
}


class CycleConflict(Exception):
    def __init__(self, message: str = "이미 진행 중인 사이클이 있습니다."):
        super().__init__(message)


class DevService:
    def __init__(self, db: Database, bus: EventBus, llm: LLMClient, client: IntegrationClient):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.client = client
        self.members = Members(db, "Dev", TEAM)
        self.tasks: set[asyncio.Task] = set()
        db.migrate(MIGRATIONS)
        self.members.seed()
        # 이전 프로세스에서 끝나지 못한 데모 사이클 정리 (실습 사이클은 사람이 진행하므로 유지)
        self.db.execute(f"UPDATE cycles SET status = 'CANCELLED', error = '서버 재시작으로 중단', finished_at = ?"
                        f" WHERE mode = 'demo' AND status IN {ACTIVE}", now())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ---------- cycle ----------
    def active_cycle(self) -> dict | None:
        return self.db.one(f"SELECT * FROM cycles WHERE status IN {ACTIVE} ORDER BY id DESC LIMIT 1")

    def cycle(self, cycle_id: int) -> dict | None:
        return self.db.one("SELECT * FROM cycles WHERE id = ?", cycle_id)

    def _set_cycle(self, cycle_id: int, status: str, **fields) -> None:
        sets = ", ".join(["status = ?"] + [f"{k} = ?" for k in fields])
        self.db.execute(f"UPDATE cycles SET {sets} WHERE id = ?", status, *fields.values(), cycle_id)
        self.bus.publish({"type": "cycle.status", "actor": ACTOR, "cycle_id": cycle_id, "payload": {"status": status}})

    def _fail(self, cycle_id: int, reason: str) -> None:
        self._set_cycle(cycle_id, "FAILED", error=reason, finished_at=now())
        self.bus.emit(ACTOR, "cycle.failed", f"사이클 #{cycle_id} 실패: {reason}", {}, cycle_id)

    async def abort(self, cycle_id: int) -> None:
        for task in list(self.tasks):
            task.cancel()
        self._set_cycle(cycle_id, "CANCELLED", finished_at=now())
        self.bus.emit(ACTOR, "cycle.cancelled", f"사이클 #{cycle_id} 중단", {}, cycle_id)
        # QA에도 중단을 알려 진행 중인 검증 실행을 닫게 한다 (실패해도 중단 자체는 유지)
        payload = {"cycle_id": cycle_id, "reason": "사이클 중단"}
        try:
            await self.client.post(f"{config.QA_URL}/api/webhooks/cycle-cancelled", payload, cycle_id)
            self.bus.emit(ACTOR, "webhook.sent", f"QA에 사이클 #{cycle_id} 중단 통지 (cycle-cancelled)", payload, cycle_id)
        except IntegrationError as exc:
            self.bus.emit(ACTOR, "integration.error", f"QA에 중단을 통지하지 못했습니다: {exc}", {}, cycle_id)

    def start_cycle(self, mode: str = "demo") -> int:
        if self.active_cycle():
            raise CycleConflict()
        cycle_id = self.db.execute("INSERT INTO cycles (status, iteration, llm_mode, mode, started_at) VALUES ('RELEASING', ?, ?, ?, ?)",
                                   1 if mode == "demo" else 0, self.llm.label, mode, now())
        if mode == "demo":
            self.bus.emit(ACTOR, "cycle.started", f"데모 사이클 #{cycle_id} 시작 (LLM: {self.llm.label})", {}, cycle_id)
            self._spawn(self._initial_release(cycle_id))
        else:
            self.bus.emit(ACTOR, "cycle.started", f"실습 사이클 #{cycle_id} 시작 - 개발부서의 최초 버전 발행을 기다립니다", {}, cycle_id)
        return cycle_id

    def _build_url(self, cycle_id: int, version: str) -> str:
        return f"{config.DEV_URL}/builds/{cycle_id}/{version}"

    async def _notify_qa(self, cycle: dict, version: str, fixed_keys: list[str]) -> None:
        payload = {"cycle_id": cycle["id"], "version": version, "fixed_issue_keys": fixed_keys,
                   "build_url": self._build_url(cycle["id"], version), "mode": cycle["mode"]}
        await self.client.post(f"{config.QA_URL}/api/webhooks/release-published", payload, cycle["id"])
        self.bus.emit(ACTOR, "webhook.sent", f"QA에 v{version} 발행 통지 (release-published)", payload, cycle["id"])

    # ---------- demo: 자동 발행/수정 ----------
    async def _initial_release(self, cycle_id: int) -> None:
        await asyncio.sleep(config.STEP_DELAY)
        version = catalog.INITIAL_VERSION
        note = f"{catalog.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(catalog.FEATURES)}"
        self._save_release(cycle_id, version, catalog.INITIAL_DEFECTS, [], note, "Demo")
        await self._publish(cycle_id, version, [])

    async def _publish(self, cycle_id: int, version: str, fixed_keys: list[str]) -> None:
        self._set_cycle(cycle_id, "VERIFYING", current_version=version)
        self.bus.emit(ACTOR, "release.published",
                      f"v{version} 발행" + (f" (반영 이슈: {', '.join(fixed_keys)})" if fixed_keys else f" (기능: {', '.join(catalog.FEATURES)})"),
                      {"version": version}, cycle_id)
        try:
            await self._notify_qa(self.cycle(cycle_id), version, fixed_keys)
        except IntegrationError as exc:
            self._fail(cycle_id, f"QA 서버 연동 실패 - {exc}")

    async def _fix_and_release(self, cycle_id: int, keys: list[str]) -> None:
        try:
            cycle = self.cycle(cycle_id)
            if cycle["iteration"] >= catalog.MAX_ITERATIONS:
                self._fail(cycle_id, f"최대 반복 {catalog.MAX_ITERATIONS}회 초과")
                return
            await asyncio.sleep(config.STEP_DELAY)
            self._set_cycle(cycle_id, "FIXING")
            current = self.db.one("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)
            version = catalog.next_version(current["version"])
            tickets = [self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, k) for k in keys]
            for ticket in tickets:
                self._ticket_status(ticket, "IN_PROGRESS", "Demo", "개발 착수")
                await asyncio.sleep(config.STEP_DELAY)
                fix_note = await self.write_fix_note(ticket)
                ticket["fix_note"] = fix_note
                self._ticket_status(ticket, "FIXED", "Demo", fix_note, fix_note=fix_note, fixed_in_version=version)
                self.bus.emit(ACTOR, "ticket.fixed", f"{ticket['key']} 수정 완료 (v{version} 반영 예정)",
                              {"key": ticket["key"], "fix_note": fix_note}, cycle_id)
            defects = self._apply_fixes(cycle_id, current, tickets)
            note = await self.write_release_note(version, tickets)
            self._save_release(cycle_id, version, defects, keys, note, "Demo")
            self.db.execute("UPDATE cycles SET iteration = iteration + 1 WHERE id = ?", cycle_id)
            await self._publish(cycle_id, version, keys)
        except Exception as exc:
            log.exception("fix_and_release failed")
            self._fail(cycle_id, f"내부 오류 - {exc}")

    # ---------- QA로부터 수신 ----------
    def receive_tickets(self, cycle_id: int, version: str, issues: list[dict]) -> None:
        cycle = self.cycle(cycle_id)
        if not cycle or cycle["status"] != "VERIFYING":
            raise CycleConflict("검증 단계의 사이클이 아닙니다.")
        for issue in issues:
            ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, issue["key"])
            if ticket and ticket["status"] in ("RECEIVED", "IN_PROGRESS"):
                # 이월된 티켓(아직 수정 중)의 재이관: 상태·담당자를 그대로 두고 이력만 남긴다
                self._history(ticket["id"], ticket["status"], ticket["status"], "QA 시스템", f"재이관 수신 (v{version}에 미반영, 진행 상태 유지)")
            elif ticket:
                self._ticket_status(ticket, "RECEIVED", "QA 시스템", "재이관 수신 (재현)", fixed_in_version=None)
            else:
                ticket_id = self.db.execute(
                    "INSERT INTO tickets (cycle_id, key, test_case_id, title, description, severity, version_found, status, received_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)",
                    cycle_id, issue["key"], issue["test_case_id"], issue["title"], issue.get("description"), issue["severity"],
                    issue["version_found"], now(), now(),
                )
                self._history(ticket_id, None, "RECEIVED", "QA 시스템", f"QA로부터 이관 수신 (v{version})")
        self._set_cycle(cycle_id, "TRANSFERRING")
        self.bus.emit(ACTOR, "tickets.received", f"이슈 {len(issues)}건 이관 수신: {', '.join(i['key'] for i in issues)}",
                      {"keys": [i["key"] for i in issues]}, cycle_id)
        if cycle["mode"] == "demo":
            self._spawn(self._fix_and_release(cycle_id, [i["key"] for i in issues]))
        else:
            self._set_cycle(cycle_id, "FIXING")

    def verification_completed(self, cycle_id: int, version: str, run_id: int, passed: int, total: int) -> None:
        cycle = self.cycle(cycle_id)
        # 중단·완료된 사이클이나 검증 중이 아닌 버전에 대한 확정 통지는 거절한다
        if not cycle or cycle["status"] != "VERIFYING" or cycle["current_version"] != version:
            raise CycleConflict("검증 중인 사이클·버전이 아닙니다.")
        self.db.execute("UPDATE releases SET approved_at = ? WHERE cycle_id = ? AND version = ?", now(), cycle_id, version)
        self._set_cycle(cycle_id, "APPROVED", finished_at=now())
        cycle = self.cycle(cycle_id)
        self.bus.emit(ACTOR, "cycle.completed", f"사이클 #{cycle_id} 완료: v{version} 확정 통지 수신 ({passed}/{total} PASS, 검증 {cycle['iteration']}회)",
                      {"version": version, "run_id": run_id}, cycle_id)

    # ---------- 실습: 개발부서원 작업 ----------
    def _manual(self, *statuses: str) -> dict:
        cycle = self.active_cycle()
        if not cycle or cycle["mode"] != "manual":
            raise WorkError("진행 중인 실습 사이클이 없습니다. '실습 시작'을 누르세요.", 409)
        if statuses and cycle["status"] not in statuses:
            raise WorkError(f"지금은 이 작업을 할 수 없습니다. (현재 단계: {cycle['status']})", 409)
        return cycle

    def shippable(self, cycle_id: int) -> list[dict]:
        """수정 완료되었지만 아직 어떤 버전에도 반영되지 않은 티켓."""
        return self.db.all("SELECT * FROM tickets WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version IS NULL ORDER BY id", cycle_id)

    def next_version(self, cycle_id: int) -> str:
        current = self.db.one("SELECT version FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)
        return catalog.next_version(current["version"]) if current else catalog.INITIAL_VERSION

    async def release(self, actor_name: str, note: str) -> str:
        member = self.members.require(actor_name)
        cycle = self._manual("RELEASING", "FIXING")
        current = self.db.one("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle["id"])
        version = self.next_version(cycle["id"])
        tickets: list[dict] = []
        if current is None:
            defects = list(catalog.INITIAL_DEFECTS)
            note = note.strip() or f"{catalog.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(catalog.FEATURES)}"
        else:
            tickets = self.shippable(cycle["id"])
            if not tickets:
                raise WorkError("반영할 수정 완료 티켓이 없습니다. 티켓을 수정한 뒤 발행하세요.")
            if not note.strip():
                raise WorkError("릴리즈 노트를 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
            defects = self._apply_fixes(cycle["id"], current, tickets, emit=False)
        keys = [t["key"] for t in tickets]
        self._save_release(cycle["id"], version, defects, keys, note.strip(), member["name"])
        try:
            await self._notify_qa(cycle, version, keys)
        except IntegrationError as exc:
            # QA가 통지를 받지 못하면 발행을 되돌린다 (다시 시도할 수 있게)
            self.db.execute("DELETE FROM releases WHERE cycle_id = ? AND version = ?", cycle["id"], version)
            raise WorkError(f"QA 서버에 발행을 통지하지 못해 발행을 취소했습니다. 잠시 후 다시 시도하세요. ({exc})", 502)
        for t in tickets:
            self.db.execute("UPDATE tickets SET fixed_in_version = ?, updated_at = ? WHERE id = ?", version, now(), t["id"])
        if current is not None:
            self._apply_fixes(cycle["id"], current, tickets, emit=True)
        self._set_cycle(cycle["id"], "VERIFYING", current_version=version, iteration=cycle["iteration"] + 1)
        self.bus.emit(Members.actor(member), "release.published",
                      f"v{version} 발행" + (f" (반영 티켓: {', '.join(keys)})" if keys else "") + " - 검증부서 검증 대기",
                      {"version": version}, cycle["id"])
        return version

    def _ticket(self, key: str, cycle: dict) -> dict:
        ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle["id"], key)
        if not ticket or ticket["status"] not in ("RECEIVED", "IN_PROGRESS"):
            raise WorkError(f"{key}은(는) 수정할 수 있는 상태가 아닙니다.")
        return ticket

    def start_fix(self, key: str, actor_name: str) -> None:
        member = self.members.require(actor_name)
        cycle = self._manual("FIXING")
        ticket = self._ticket(key, cycle)
        self._ticket_status(ticket, "IN_PROGRESS", member["name"], f"{member['name']} 착수", assignee=member["name"])
        self.bus.emit(Members.actor(member), "ticket.started", f"{key} 착수", {"key": key}, cycle["id"])

    def fix(self, key: str, actor_name: str, fix_note: str) -> None:
        member = self.members.require(actor_name)
        cycle = self._manual("FIXING")
        ticket = self._ticket(key, cycle)
        if not fix_note.strip():
            raise WorkError("수정 내역을 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
        self._ticket_status(ticket, "FIXED", member["name"], fix_note.strip(), fix_note=fix_note.strip(), fixed_in_version=None,
                            assignee=ticket["assignee"] or member["name"])
        self.bus.emit(Members.actor(member), "ticket.fixed", f"{key} 수정 완료 (다음 버전 발행 시 반영)", {"key": key}, cycle["id"])

    async def draft_fix(self, key: str) -> dict:
        cycle = self._manual("FIXING")
        return {"fix_note": await self.write_fix_note(self._ticket(key, cycle))}

    async def draft_release(self) -> dict:
        cycle = self._manual("RELEASING", "FIXING")
        tickets = self.shippable(cycle["id"])
        return {"release_note": await self.write_release_note(self.next_version(cycle["id"]), tickets) if tickets else ""}

    # ---------- helpers ----------
    def _history(self, ticket_id: int, from_status: str | None, to_status: str, actor: str, note: str) -> None:
        self.db.execute("INSERT INTO ticket_history (ticket_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
                        ticket_id, from_status, to_status, actor, note, now())

    def _ticket_status(self, ticket: dict, to_status: str, actor: str, note: str, **fields) -> None:
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), ticket["id"])
        self._history(ticket["id"], ticket["status"], to_status, actor, note)
        ticket["status"] = to_status
        ticket.update(fields)

    def _apply_fixes(self, cycle_id: int, current: dict, tickets: list[dict], emit: bool = True) -> list[str]:
        defects, introduced = catalog.apply_fixes([b["id"] for b in current["build"]["behaviors"]], [t["test_case_id"] for t in tickets])
        if introduced and emit:
            self.bus.emit("Simulator", "regression.introduced", f"수정 과정에서 숨은 회귀 결함 발생: {', '.join(introduced)}",
                          {"defects": introduced}, cycle_id)
        return defects

    def _save_release(self, cycle_id: int, version: str, defects: list[str], fixed_keys: list[str], note: str, by: str) -> None:
        self.db.execute(
            "INSERT INTO releases (cycle_id, version, features, build, fixed_issue_keys, release_note, released_by, released_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            cycle_id, version, catalog.FEATURES, catalog.build_manifest(version, defects), fixed_keys, note, by, now(),
        )

    async def write_fix_note(self, ticket: dict) -> str:
        prompt = f"다음 결함을 수정했습니다. 수정 내역을 작성하세요.\n이슈: {ticket['key']} {ticket['title']}\n설명: {ticket['description']}"
        result, _ = await self.llm.json(SYSTEM, prompt, FIX_SCHEMA, lambda: {"fix_note": f"{ticket['title']} 원인 코드 수정 및 단위 테스트 추가"})
        return result["fix_note"]

    async def write_release_note(self, version: str, tickets: list[dict]) -> str:
        lines = "\n".join(f"- {t['key']} [{t['severity']}] {t['title']}: {t.get('fix_note', '')}" for t in tickets)
        prompt = f"{catalog.PRODUCT} v{version} 릴리즈 노트를 작성하세요. 반영된 수정 사항:\n{lines}"
        result, _ = await self.llm.json(
            SYSTEM, prompt, NOTE_SCHEMA,
            lambda: {"release_note": f"{catalog.PRODUCT} {version} 유지보수 릴리즈\n" + "\n".join(f"- {t['key']} {t['title']} 수정" for t in tickets)},
        )
        return result["release_note"]
