"""Dev Agent의 업무 수행부 (A2A AgentExecutor).

Hub는 "최초 버전을 발행해 달라", "이 이슈들을 고쳐서 차기 버전을 내 달라" 같은 업무 단위로 요청한다.
- 데모: 이 에이전트가 내부(LLM, DB)에서 스스로 수정·발행하고 Task를 완료한다.
- 실습: Task를 working 상태로 유지한 채 개발부서원이 Dev 웹에서 일하기를 기다린다.
        착수·수정 같은 진행 상황은 Task 상태 메시지로 Hub에 스트리밍하고, 부서원이 발행하면 release Artifact로 완료한다.
Hub는 이 에이전트 내부(사람이 하는지, LLM이 하는지)를 모른다.
"""

import asyncio
import logging

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater

from common import config
from common.a2a_parts import first_data
from common.db import Database, now
from common.events import EventBus
from common.llm import LLMClient
from common.work import Members, WorkError

from . import catalog

log = logging.getLogger("dev_agent")
TEAM = [{"name": "최개발", "role": "백엔드 개발"}, {"name": "정개발", "role": "프론트엔드 개발"}]

SYSTEM = "당신은 커머스 웹 서비스 개발팀의 시니어 개발자입니다. 간결하고 사실적인 한국어로 작성합니다."
FIX_SCHEMA = {"type": "object", "properties": {"fix_note": {"type": "string", "description": "원인과 수정 내용을 1~2문장으로"}},
              "required": ["fix_note"], "additionalProperties": False}
NOTE_SCHEMA = {"type": "object", "properties": {"release_note": {"type": "string", "description": "릴리즈 노트 본문(목록 형태, 5줄 이내)"}},
               "required": ["release_note"], "additionalProperties": False}

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, version TEXT NOT NULL,
    features TEXT NOT NULL, build TEXT NOT NULL, fixed_issue_keys TEXT NOT NULL, release_note TEXT,
    released_at TEXT NOT NULL, UNIQUE (cycle_id, version)
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, key TEXT NOT NULL, test_case_id TEXT NOT NULL,
    title TEXT NOT NULL, description TEXT, severity TEXT NOT NULL, version_found TEXT NOT NULL, status TEXT NOT NULL,
    fix_note TEXT, fixed_in_version TEXT, received_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (cycle_id, key)
);
CREATE TABLE IF NOT EXISTS ticket_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    actor TEXT, note TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, task_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL,
    outcome TEXT, requested_at TEXT NOT NULL, done_at TEXT
);
"""
MIGRATIONS = {"releases": {"released_by": "TEXT"}, "tickets": {"assignee": "TEXT"}}


class DevWork:
    """개발부서 업무: 데모 자동 처리, 실습 부서원 작업, 진행 중인 Task와의 연결."""

    def __init__(self, db: Database, bus: EventBus, llm: LLMClient):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.members = Members(db, "Dev", TEAM)
        self.waiting: dict[int, tuple[asyncio.Future, TaskUpdater]] = {}  # work_id -> (부서원 완료 신호, Task 갱신기)
        db.migrate(MIGRATIONS)
        self.members.seed()
        # 이전 프로세스에서 기다리던 작업은 Task 연결이 끊겼으므로 취소
        db.execute("UPDATE work_orders SET status = 'CANCELLED' WHERE status = 'OPEN'")

    # ---------- 공통 ----------
    def build_url(self, cycle_id: int, version: str) -> str:
        return f"{config.DEV_URL}/builds/{cycle_id}/{version}"

    def _history(self, ticket_id: int, from_status, to_status: str, actor: str, note: str) -> None:
        self.db.execute("INSERT INTO ticket_history (ticket_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
                        ticket_id, from_status, to_status, actor, note, now())

    def _ticket_status(self, ticket: dict, to_status: str, actor: str, note: str, **fields) -> None:
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), ticket["id"])
        self._history(ticket["id"], ticket["status"], to_status, actor, note)
        ticket["status"] = to_status
        ticket.update(fields)

    def upsert_tickets(self, cycle_id: int, issues: list[dict]) -> None:
        for issue in issues:
            ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, issue["key"])
            if ticket and ticket["status"] in ("RECEIVED", "IN_PROGRESS"):  # 지난 발행에 넣지 못한 미수정 티켓: 진행 상태 유지
                self._history(ticket["id"], ticket["status"], ticket["status"], "Hub", "재위임 수신 (미수정 이월)")
            elif ticket:
                self._ticket_status(ticket, "RECEIVED", "Hub", "재위임 수신 (재현)", fixed_in_version=None)
            else:
                tid = self.db.execute(
                    "INSERT INTO tickets (cycle_id, key, test_case_id, title, description, severity, version_found, status, received_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)",
                    cycle_id, issue["key"], issue["test_case_id"], issue["title"], issue.get("description", ""), issue["severity"],
                    issue["version_found"], now(), now())
                self._history(tid, None, "RECEIVED", "Hub", f"Hub로부터 수정 업무 위임 (v{issue['version_found']})")

    def current_release(self, cycle_id: int) -> dict | None:
        return self.db.one("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)

    def next_version(self, cycle_id: int) -> str:
        current = self.current_release(cycle_id)
        return catalog.next_version(current["version"]) if current else catalog.INITIAL_VERSION

    def shippable(self, cycle_id: int) -> list[dict]:
        return self.db.all("SELECT * FROM tickets WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version IS NULL ORDER BY id", cycle_id)

    def save_release(self, cycle_id: int, version: str, defects: list[str], keys: list[str], note: str, by: str) -> dict:
        self.db.execute("INSERT INTO releases (cycle_id, version, features, build, fixed_issue_keys, release_note, released_by, released_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        cycle_id, version, catalog.FEATURES, catalog.build_manifest(version, defects), keys, note, by, now())
        for key in keys:
            self.db.execute("UPDATE tickets SET fixed_in_version = ? WHERE cycle_id = ? AND key = ?", version, cycle_id, key)
        return {"cycle_id": cycle_id, "version": version, "build_url": self.build_url(cycle_id, version), "features": catalog.FEATURES,
                "fixed_issue_keys": keys, "release_note": note}

    def next_defects(self, cycle_id: int, tickets: list[dict]) -> list[str]:
        current = self.current_release(cycle_id)
        defects, introduced = catalog.apply_fixes([b["id"] for b in current["build"]["behaviors"]], [t["test_case_id"] for t in tickets])
        if introduced:
            log.info("simulator: regression introduced %s", introduced)
            self.bus.emit("Simulator", "regression.introduced", f"수정 과정에서 숨은 회귀 결함 발생: {', '.join(introduced)}", {}, cycle_id)
        return defects

    async def write_fix_note(self, ticket: dict) -> str:
        prompt = f"다음 결함을 수정했습니다. 수정 내역을 작성하세요.\n이슈: {ticket['key']} {ticket['title']}\n설명: {ticket.get('description', '')}"
        result, _ = await self.llm.json(SYSTEM, prompt, FIX_SCHEMA, lambda: {"fix_note": f"{ticket['title']} 원인 코드 수정 및 단위 테스트 추가"})
        return result["fix_note"]

    async def write_release_note(self, version: str, tickets: list[dict]) -> str:
        lines = "\n".join(f"- {t['key']} [{t['severity']}] {t['title']}: {t.get('fix_note', '')}" for t in tickets)
        result, _ = await self.llm.json(
            SYSTEM, f"{catalog.PRODUCT} v{version} 릴리즈 노트를 작성하세요. 반영된 수정 사항:\n{lines}", NOTE_SCHEMA,
            lambda: {"release_note": f"{catalog.PRODUCT} {version} 유지보수 릴리즈\n" + "\n".join(f"- {t['key']} {t['title']} 수정" for t in tickets)})
        return result["release_note"]

    # ---------- 실습: 부서원 작업 대기 ----------
    def open_order(self) -> dict | None:
        return self.db.one("SELECT * FROM work_orders WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")

    async def wait_for_staff(self, cycle_id: int, task_id: str, kind: str, updater: TaskUpdater) -> dict:
        for work_id, (future, _) in list(self.waiting.items()):  # 남아 있던 대기 정리
            if not future.done():
                future.cancel()
        work_id = self.db.execute("INSERT INTO work_orders (cycle_id, task_id, kind, status, requested_at) VALUES (?, ?, ?, 'OPEN', ?)",
                                  cycle_id, task_id, kind, now())
        future = asyncio.get_running_loop().create_future()
        self.waiting[work_id] = (future, updater)
        self.bus.emit("Hub", "work.requested", f"Hub가 {'최초 버전 발행' if kind == 'initial' else '수정 후 차기 버전 발행'} 업무를 위임 (Task {task_id[:8]}) - 개발부서원 작업 대기",
                      {"work_id": work_id}, cycle_id)
        try:
            return await future
        finally:
            self.waiting.pop(work_id, None)
            self.db.execute("UPDATE work_orders SET status = CASE status WHEN 'OPEN' THEN 'CANCELLED' ELSE status END WHERE id = ?", work_id)

    async def _progress(self, text: str) -> None:
        """진행 중인 Task에 상태 메시지를 보낸다 (Hub로 스트리밍된다)."""
        order = self.open_order()
        entry = self.waiting.get(order["id"]) if order else None
        if entry:
            await entry[1].start_work(entry[1].new_agent_message([new_text_part(text)]))

    def _order(self, kinds=("initial", "fix")) -> dict:
        order = self.open_order()
        if not order or order["kind"] not in kinds or order["id"] not in self.waiting:
            raise WorkError("지금은 개발부서 차례가 아닙니다. (Hub가 위임한 업무가 없습니다)", 409)
        return order

    async def release(self, actor_name: str, note: str) -> dict:
        member = self.members.require(actor_name)
        order = self._order()
        cycle_id = order["cycle_id"]
        version = self.next_version(cycle_id)
        if order["kind"] == "initial":
            result = self.save_release(cycle_id, version, catalog.INITIAL_DEFECTS, [],
                                       note.strip() or f"{catalog.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(catalog.FEATURES)}", member["name"])
        else:
            tickets = self.shippable(cycle_id)
            if not tickets:
                raise WorkError("반영할 수정 완료 티켓이 없습니다. 티켓을 수정한 뒤 발행하세요.")
            if not note.strip():
                raise WorkError("릴리즈 노트를 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
            result = self.save_release(cycle_id, version, self.next_defects(cycle_id, tickets), [t["key"] for t in tickets], note.strip(), member["name"])
            result["fixes"] = [{"key": t["key"], "fix_note": t["fix_note"]} for t in tickets]
        self.db.execute("UPDATE work_orders SET status = 'DONE', outcome = ?, done_at = ? WHERE id = ?", result, now(), order["id"])
        self.bus.emit(Members.actor(member), "release.published", f"v{version} 발행 - Task 완료로 Hub에 반환", {"version": version}, cycle_id)
        self.waiting[order["id"]][0].set_result(result)
        return result

    def _ticket(self, key: str, order: dict) -> dict:
        ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", order["cycle_id"], key)
        if not ticket or ticket["status"] not in ("RECEIVED", "IN_PROGRESS"):
            raise WorkError(f"{key}은(는) 수정할 수 있는 상태가 아닙니다.")
        return ticket

    async def start_fix(self, key: str, actor_name: str) -> None:
        member = self.members.require(actor_name)
        order = self._order(("fix",))
        ticket = self._ticket(key, order)
        self._ticket_status(ticket, "IN_PROGRESS", member["name"], f"{member['name']} 착수", assignee=member["name"])
        self.bus.emit(Members.actor(member), "ticket.started", f"{key} 착수", {}, order["cycle_id"])
        await self._progress(f"{key} 착수 ({member['name']})")

    async def fix(self, key: str, actor_name: str, fix_note: str) -> None:
        member = self.members.require(actor_name)
        order = self._order(("fix",))
        ticket = self._ticket(key, order)
        if not fix_note.strip():
            raise WorkError("수정 내역을 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
        self._ticket_status(ticket, "FIXED", member["name"], fix_note.strip(), fix_note=fix_note.strip(), fixed_in_version=None,
                            assignee=ticket["assignee"] or member["name"])
        self.bus.emit(Members.actor(member), "ticket.fixed", f"{key} 수정 완료 (다음 버전 발행 시 반영)", {}, order["cycle_id"])
        await self._progress(f"{key} 수정 완료 ({member['name']})")

    async def draft_fix(self, key: str) -> dict:
        return {"fix_note": await self.write_fix_note(self._ticket(key, self._order(("fix",))))}

    async def draft_release(self) -> dict:
        order = self._order()
        tickets = self.shippable(order["cycle_id"])
        return {"release_note": await self.write_release_note(self.next_version(order["cycle_id"]), tickets) if tickets else ""}


class DevAgentExecutor(AgentExecutor):
    def __init__(self, work: DevWork):
        self.work = work

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        data = first_data(context.message.parts)
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        skill, manual = data.get("skill"), data.get("mode") == "manual"
        try:
            if skill == "release-build":
                cycle_id = int(data["cycle_id"])
                if manual:
                    await self._say(updater, "최초 버전 발행 업무를 접수했습니다. 개발부서원의 발행을 기다립니다.")
                    result = await self.work.wait_for_staff(cycle_id, context.task_id, "initial", updater)
                else:
                    result = await self._demo_initial(updater, cycle_id)
                await self._finish(updater, result)
            elif skill == "fix-and-release":
                cycle_id, issues = int(data["cycle_id"]), data.get("issues", [])
                if not issues:
                    await updater.reject(updater.new_agent_message([new_text_part("수정할 이슈가 없습니다.")]))
                    return
                self.work.upsert_tickets(cycle_id, issues)
                if manual:
                    self.work.bus.emit("Hub", "tickets.received", f"티켓 {len(issues)}건 접수 (Hub 위임): {', '.join(i['key'] for i in issues)}", {}, cycle_id)
                    await self._say(updater, f"이슈 {len(issues)}건을 티켓으로 접수했습니다. 개발부서원의 수정과 발행을 기다립니다.")
                    result = await self.work.wait_for_staff(cycle_id, context.task_id, "fix", updater)
                else:
                    result = await self._demo_fix(updater, cycle_id, issues)
                await self._finish(updater, result)
            else:
                await updater.reject(updater.new_agent_message([new_text_part(f"지원하지 않는 요청입니다: skill={skill!r}")]))
        except asyncio.CancelledError:
            await updater.cancel(updater.new_agent_message([new_text_part("작업이 취소되었습니다.")]))
        except Exception as exc:
            log.exception("task failed")
            await updater.failed(updater.new_agent_message([new_text_part(f"처리 중 오류: {exc}")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        order = self.work.db.one("SELECT id FROM work_orders WHERE task_id = ? AND status = 'OPEN'", context.task_id)
        entry = self.work.waiting.get(order["id"]) if order else None
        if entry and not entry[0].done():
            entry[0].cancel()  # execute()가 취소 상태로 Task를 마무리한다
            return
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    async def _say(self, updater: TaskUpdater, text: str) -> None:
        await updater.start_work(updater.new_agent_message([new_text_part(text)]))

    async def _finish(self, updater: TaskUpdater, data: dict) -> None:
        await updater.add_artifact([new_text_part(data["release_note"]), new_data_part(data, media_type="application/json")], name="release")
        await updater.complete(updater.new_agent_message([new_text_part(f"v{data['version']} 발행 완료")]))

    # ---------- 데모: 에이전트가 스스로 처리 ----------
    async def _demo_initial(self, updater: TaskUpdater, cycle_id: int) -> dict:
        await self._say(updater, "최초 빌드를 준비하고 있습니다.")
        await asyncio.sleep(config.STEP_DELAY)
        version = catalog.INITIAL_VERSION
        return self.work.save_release(cycle_id, version, catalog.INITIAL_DEFECTS, [],
                                      f"{catalog.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(catalog.FEATURES)}", "Dev Agent")

    async def _demo_fix(self, updater: TaskUpdater, cycle_id: int, issues: list[dict]) -> dict:
        version = self.work.next_version(cycle_id)
        await self._say(updater, f"이슈 {len(issues)}건을 접수했습니다. v{version} 작업을 시작합니다.")
        tickets = []
        for issue in issues:
            ticket = self.work.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, issue["key"])
            await self._say(updater, f"{issue['key']} 수정 중: {issue['title']}")
            await asyncio.sleep(config.STEP_DELAY)
            fix_note = await self.work.write_fix_note(ticket)
            self.work._ticket_status(ticket, "FIXED", "Dev Agent", fix_note, fix_note=fix_note, fixed_in_version=None)
            tickets.append(ticket)
        await self._say(updater, "릴리즈 노트를 작성하고 빌드를 생성합니다.")
        note = await self.work.write_release_note(version, tickets)
        result = self.work.save_release(cycle_id, version, self.work.next_defects(cycle_id, tickets), [t["key"] for t in tickets], note, "Dev Agent")
        result["fixes"] = [{"key": t["key"], "fix_note": t["fix_note"]} for t in tickets]
        return result
