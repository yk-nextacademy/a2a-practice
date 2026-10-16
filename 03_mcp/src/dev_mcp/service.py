"""개발부서 시스템 업무 로직 (Stage 3).

이 시스템은 QA 시스템을 모른다. 바깥과의 연결은 오직 MCP 도구(Host가 호출)뿐이다.
- 데모: Host의 AI가 도구로 발행·티켓 등록·수정까지 모두 지시한다.
- 실습: Host가 '발행 요청'(작업 지시)을 넣으면 개발부서원이 Dev 웹에서 티켓을 수정하고 발행한다.
        끝난 작업은 '완료 대기'로 남고, Host가 이를 발견(폴링)해 QA 시스템에 전달한 뒤 확인(ack)한다.
"""

from common.db import Database, now
from common.events import EventBus
from common.llm import LLMClient
from common.work import Members, WorkError

from . import catalog

TEAM = [{"name": "최개발", "role": "백엔드 개발"}, {"name": "정개발", "role": "프론트엔드 개발"}]

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
    note TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    outcome TEXT, requested_at TEXT NOT NULL, done_at TEXT, acked_at TEXT
);
"""
MIGRATIONS = {"releases": {"released_by": "TEXT"}, "tickets": {"assignee": "TEXT"}, "ticket_history": {"actor": "TEXT"}}

SYSTEM = "당신은 커머스 웹 서비스 개발팀의 시니어 개발자입니다. 간결하고 사실적인 한국어로 작성합니다."
FIX_SCHEMA = {"type": "object", "properties": {"fix_note": {"type": "string", "description": "원인과 수정 내용을 1~2문장으로"}},
              "required": ["fix_note"], "additionalProperties": False}
NOTE_SCHEMA = {"type": "object", "properties": {"release_note": {"type": "string", "description": "릴리즈 노트 본문(목록 형태, 5줄 이내)"}},
               "required": ["release_note"], "additionalProperties": False}


class DevSystem:
    def __init__(self, db: Database, bus: EventBus, llm: LLMClient, base_url: str):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.base_url = base_url
        self.members = Members(db, "Dev", TEAM)
        db.migrate(MIGRATIONS)
        self.members.seed()

    def build_url(self, cycle_id: int, version: str) -> str:
        return f"{self.base_url}/builds/{cycle_id}/{version}"

    # ---------- 공통 ----------
    def _history(self, ticket_id: int, from_status, to_status: str, actor: str, note: str) -> None:
        self.db.execute("INSERT INTO ticket_history (ticket_id, from_status, to_status, actor, note, ts) VALUES (?, ?, ?, ?, ?, ?)",
                        ticket_id, from_status, to_status, actor, note, now())

    def _ticket_status(self, ticket: dict, to_status: str, actor: str, note: str, **fields) -> None:
        sets = ["status = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields]
        self.db.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", to_status, now(), *fields.values(), ticket["id"])
        self._history(ticket["id"], ticket["status"], to_status, actor, note)
        ticket["status"] = to_status
        ticket.update(fields)

    def _save_release(self, cycle_id: int, version: str, defects: list[str], keys: list[str], note: str, by: str) -> None:
        self.db.execute("INSERT INTO releases (cycle_id, version, features, build, fixed_issue_keys, release_note, released_by, released_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        cycle_id, version, catalog.FEATURES, catalog.build_manifest(version, defects), keys, note, by, now())

    def current_release(self, cycle_id: int) -> dict | None:
        return self.db.one("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle_id)

    def next_version(self, cycle_id: int) -> str:
        current = self.current_release(cycle_id)
        return catalog.next_version(current["version"]) if current else catalog.INITIAL_VERSION

    def shippable(self, cycle_id: int) -> list[dict]:
        return self.db.all("SELECT * FROM tickets WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version IS NULL ORDER BY id", cycle_id)

    def _apply(self, cycle_id: int, current: dict, tickets: list[dict]) -> list[str]:
        defects, introduced = catalog.apply_fixes([b["id"] for b in current["build"]["behaviors"]], [t["test_case_id"] for t in tickets])
        if introduced:
            self.bus.emit("Simulator", "regression.introduced", f"수정 과정에서 숨은 회귀 결함 발생: {', '.join(introduced)}", {}, cycle_id)
        return defects

    # ---------- 데모용 도구 (Host의 AI가 직접 수행) ----------
    def publish_initial_release(self, cycle_id: int) -> dict:
        if self.current_release(cycle_id):
            raise WorkError(f"cycle {cycle_id}에는 이미 최초 릴리즈가 있습니다. publish_release를 사용하세요.")
        version = catalog.INITIAL_VERSION
        self._save_release(cycle_id, version, catalog.INITIAL_DEFECTS, [], f"{catalog.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(catalog.FEATURES)}",
                           "Host AI")
        self.bus.emit("Dev", "release.published", f"v{version} 발행 (Host 지시)", {"version": version}, cycle_id)
        return {"cycle_id": cycle_id, "version": version, "build_url": self.build_url(cycle_id, version), "features": catalog.FEATURES}

    def register_tickets(self, cycle_id: int, issues: list[dict]) -> dict:
        registered, kept = [], []
        for issue in issues:
            ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, issue["key"])
            if ticket and (ticket["status"] in ("RECEIVED", "IN_PROGRESS") or (ticket["status"] == "FIXED" and not ticket["fixed_in_version"])):
                # 아직 발행에 반영되지 않은 티켓 (지난 발행에서 빠져 다시 이관됐거나, Host가 전달을 다시 시도한 경우):
                # 상태·담당자를 그대로 두고 이력도 중복으로 남기지 않는다
                kept.append(issue["key"])
            elif ticket:
                self._ticket_status(ticket, "RECEIVED", "Host", "재이관 수신 (재현)", fixed_in_version=None)
            else:
                tid = self.db.execute(
                    "INSERT INTO tickets (cycle_id, key, test_case_id, title, description, severity, version_found, status, received_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)",
                    cycle_id, issue["key"], issue["test_case_id"], issue["title"], issue.get("description", ""), issue["severity"],
                    issue["version_found"], now(), now())
                self._history(tid, None, "RECEIVED", "Host", f"이관 수신 (v{issue['version_found']})")
            registered.append(issue["key"])
        new = [k for k in registered if k not in kept]
        self.bus.emit("Dev", "tickets.received", f"티켓 {len(new)}건 접수 (Host 전달): {', '.join(new) or '-'}"
                      + (f" / 처리 중 유지 {len(kept)}건: {', '.join(kept)}" if kept else ""), {"kept": kept}, cycle_id)
        return {"registered": registered, "kept": kept}

    def fix_ticket(self, cycle_id: int, issue_key: str, fix_note: str) -> dict:
        ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, issue_key)
        if not ticket:
            raise WorkError(f"{issue_key} 티켓이 없습니다. register_tickets로 먼저 등록하세요.")
        if ticket["status"] == "FIXED":
            return {"key": issue_key, "status": "FIXED", "note": "이미 수정 완료된 티켓입니다."}
        self._ticket_status(ticket, "IN_PROGRESS", "Host AI", "개발 착수")
        self._ticket_status(ticket, "FIXED", "Host AI", fix_note, fix_note=fix_note, fixed_in_version=None)
        self.bus.emit("Dev", "ticket.fixed", f"{issue_key} 수정 완료 (Host 지시)", {}, cycle_id)
        return {"key": issue_key, "status": "FIXED"}

    def publish_release(self, cycle_id: int, release_note: str, by: str = "Host AI") -> dict:
        current = self.current_release(cycle_id)
        if not current:
            raise WorkError("최초 릴리즈가 없습니다. publish_initial_release를 먼저 호출하세요.")
        tickets = self.shippable(cycle_id)
        if not tickets:
            raise WorkError("반영할 수정 완료 티켓이 없습니다. 티켓을 먼저 수정하세요.")
        version = catalog.next_version(current["version"])
        defects = self._apply(cycle_id, current, tickets)
        keys = [t["key"] for t in tickets]
        self._save_release(cycle_id, version, defects, keys, release_note, by)
        for t in tickets:
            self.db.execute("UPDATE tickets SET fixed_in_version = ? WHERE id = ?", version, t["id"])
        self.bus.emit("Dev" if by == "Host AI" else f"Dev {by}", "release.published",
                      f"v{version} 발행 (반영 티켓: {', '.join(keys)})", {"version": version}, cycle_id)
        return {"cycle_id": cycle_id, "version": version, "build_url": self.build_url(cycle_id, version), "fixed_issue_keys": keys}

    # ---------- 실습: Host와 주고받는 작업 지시 ----------
    def request_release(self, cycle_id: int) -> dict:
        """Host가 개발부서에 '버전 발행'을 요청한다. 개발부서원이 Dev 웹에서 처리한다."""
        # 이전 사이클의 요청이 남아 있으면 정리한다 (Host가 cancel_work를 부르지 못하고 종료된 경우 대비)
        self.db.execute("UPDATE work_orders SET status = 'CANCELLED' WHERE status = 'OPEN' AND cycle_id != ?", cycle_id)
        existing = self.open_order(cycle_id)
        if existing:
            return {"work_id": existing["id"], "kind": existing["kind"], "note": "이미 진행 중인 발행 요청이 있습니다."}
        kind = "fix" if self.current_release(cycle_id) else "initial"
        work_id = self.db.execute("INSERT INTO work_orders (cycle_id, kind, status, requested_at) VALUES (?, ?, 'OPEN', ?)", cycle_id, kind, now())
        self.bus.emit("Host", "work.requested", f"발행 요청 접수 (#{work_id}, {'최초 버전' if kind == 'initial' else '수정 반영'}) - 개발부서원 작업 대기",
                      {"work_id": work_id}, cycle_id)
        return {"work_id": work_id, "kind": kind}

    def cancel_work(self, cycle_id: int) -> dict:
        """Host가 사이클을 중단했을 때 그 사이클의 진행 중(OPEN) 발행 요청을 취소한다."""
        ids = [r["id"] for r in self.db.all("SELECT id FROM work_orders WHERE cycle_id = ? AND status = 'OPEN'", cycle_id)]
        for work_id in ids:
            self.db.execute("UPDATE work_orders SET status = 'CANCELLED' WHERE id = ?", work_id)
        if ids:
            self.bus.emit("Host", "work.cancelled", f"사이클 중단으로 발행 요청 취소 (#{', #'.join(map(str, ids))})", {"work_ids": ids}, cycle_id)
        return {"cycle_id": cycle_id, "cancelled": ids}

    def open_order(self, cycle_id: int | None = None) -> dict | None:
        if cycle_id is None:
            return self.db.one("SELECT * FROM work_orders WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1")
        return self.db.one("SELECT * FROM work_orders WHERE cycle_id = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1", cycle_id)

    def completed_work(self, cycle_id: int) -> list[dict]:
        return self.db.all("SELECT id AS work_id, kind, outcome, done_at FROM work_orders WHERE cycle_id = ? AND status = 'DONE' ORDER BY id", cycle_id)

    def ack_work(self, work_id: int) -> dict:
        self.db.execute("UPDATE work_orders SET status = 'ACKED', acked_at = ? WHERE id = ? AND status = 'DONE'", now(), work_id)
        return {"work_id": work_id, "acked": True}

    # ---------- 실습: 개발부서원 작업 (Dev 웹) ----------
    def _order(self, kinds: tuple[str, ...] = ("initial", "fix")) -> dict:
        order = self.open_order()
        if not order or order["kind"] not in kinds:
            raise WorkError("지금은 개발부서 차례가 아닙니다. (Host의 발행 요청이 없습니다)", 409)
        return order

    def release(self, actor_name: str, note: str) -> dict:
        member = self.members.require(actor_name)
        order = self._order()
        cycle_id = order["cycle_id"]
        if order["kind"] == "initial":
            version = catalog.INITIAL_VERSION
            self._save_release(cycle_id, version, catalog.INITIAL_DEFECTS, [],
                               note.strip() or f"{catalog.PRODUCT} {version} 최초 릴리즈\n- 기능: {', '.join(catalog.FEATURES)}", member["name"])
            result = {"cycle_id": cycle_id, "version": version, "build_url": self.build_url(cycle_id, version), "fixed_issue_keys": []}
            self.bus.emit(Members.actor(member), "release.published", f"v{version} 발행 - Host 전달 대기", {"version": version}, cycle_id)
        else:
            if not note.strip():
                raise WorkError("릴리즈 노트를 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
            result = self.publish_release(cycle_id, note.strip(), member["name"])
        self.db.execute("UPDATE work_orders SET status = 'DONE', outcome = ?, done_at = ? WHERE id = ?", result, now(), order["id"])
        return result

    def _ticket(self, key: str, order: dict) -> dict:
        ticket = self.db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", order["cycle_id"], key)
        if not ticket or ticket["status"] not in ("RECEIVED", "IN_PROGRESS"):
            raise WorkError(f"{key}은(는) 수정할 수 있는 상태가 아닙니다.")
        return ticket

    def start_fix(self, key: str, actor_name: str) -> None:
        member = self.members.require(actor_name)
        order = self._order(("fix",))
        ticket = self._ticket(key, order)
        self._ticket_status(ticket, "IN_PROGRESS", member["name"], f"{member['name']} 착수", assignee=member["name"])
        self.bus.emit(Members.actor(member), "ticket.started", f"{key} 착수", {}, order["cycle_id"])

    def fix(self, key: str, actor_name: str, fix_note: str) -> None:
        member = self.members.require(actor_name)
        order = self._order(("fix",))
        ticket = self._ticket(key, order)
        if not fix_note.strip():
            raise WorkError("수정 내역을 입력하세요. ('AI 초안'으로 작성할 수 있습니다)")
        self._ticket_status(ticket, "FIXED", member["name"], fix_note.strip(), fix_note=fix_note.strip(), fixed_in_version=None,
                            assignee=ticket["assignee"] or member["name"])
        self.bus.emit(Members.actor(member), "ticket.fixed", f"{key} 수정 완료 (다음 버전 발행 시 반영)", {}, order["cycle_id"])

    async def draft_fix(self, key: str) -> dict:
        ticket = self._ticket(key, self._order(("fix",)))
        return {"fix_note": await self.write_fix_note(ticket)}

    async def draft_release(self) -> dict:
        order = self._order()
        tickets = self.shippable(order["cycle_id"])
        return {"release_note": await self.write_release_note(self.next_version(order["cycle_id"]), tickets) if tickets else ""}

    async def write_fix_note(self, ticket: dict) -> str:
        prompt = f"다음 결함을 수정했습니다. 수정 내역을 작성하세요.\n이슈: {ticket['key']} {ticket['title']}\n설명: {ticket['description']}"
        result, _ = await self.llm.json(SYSTEM, prompt, FIX_SCHEMA, lambda: {"fix_note": f"{ticket['title']} 원인 코드 수정 및 단위 테스트 추가"})
        return result["fix_note"]

    async def write_release_note(self, version: str, tickets: list[dict]) -> str:
        lines = "\n".join(f"- {t['key']} [{t['severity']}] {t['title']}: {t.get('fix_note', '')}" for t in tickets)
        result, _ = await self.llm.json(
            SYSTEM, f"{catalog.PRODUCT} v{version} 릴리즈 노트를 작성하세요. 반영된 수정 사항:\n{lines}", NOTE_SCHEMA,
            lambda: {"release_note": f"{catalog.PRODUCT} {version} 유지보수 릴리즈\n" + "\n".join(f"- {t['key']} {t['title']} 수정" for t in tickets)})
        return result["release_note"]
