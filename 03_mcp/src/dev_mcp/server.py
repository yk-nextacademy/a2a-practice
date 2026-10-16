"""Dev MCP Server (:8301) - MCP 도구(/mcp) + Dev 웹(/) + 빌드 산출물(/builds).

MCP 도구는 Host(AI)를 위한 연결 지점이고, Dev 웹은 개발부서원이 일하는 화면이다.
이 시스템은 QA 시스템과 직접 통신하지 않는다.
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from common import config
from common.db import Database
from common.events import EventBus
from common.llm import LLMClient
from common.web.setup import setup_web
from common.work import WorkError

from .service import SCHEMA, DevSystem

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "D", "name": "Dev 웹", "sub": "Stage 3 · 개발부서 시스템 (MCP)", "crumb": "Dev", "system": f"Dev MCP :{config.DEV_PORT}"}


class TicketInput(BaseModel):
    key: str = Field(description="QA 이슈 키 (예: ISSUE-1)")
    test_case_id: str = Field(description="실패한 테스트케이스 ID (예: TC-02)")
    title: str
    description: str = ""
    severity: str = Field(description="Critical | Major | Minor")
    version_found: str


def create_mcp(dev: DevSystem, db: Database) -> MCPServer:
    mcp = MCPServer(name="dev-department", instructions="개발부서 시스템. 버전 발행, 티켓 등록·수정, 개발부서원에게 발행 요청(실습)을 제공한다.")

    def run(fn, *args):
        try:
            return fn(*args)
        except WorkError as exc:
            raise ToolError(str(exc))

    # ----- 데모: Host의 AI가 직접 수행 -----
    @mcp.tool()
    def publish_initial_release(cycle_id: int) -> dict[str, Any]:
        """사이클의 최초 버전(1.0.0)을 발행한다. 사이클당 한 번만 호출한다. 결과의 build_url을 검증부서에 전달해야 한다."""
        return run(dev.publish_initial_release, cycle_id)

    @mcp.tool()
    def register_tickets(cycle_id: int, issues: list[TicketInput]) -> dict[str, Any]:
        """검증부서에서 이관받은 이슈를 개발 티켓으로 등록한다 (이미 있으면 재접수 처리)."""
        return run(dev.register_tickets, cycle_id, [i.model_dump() for i in issues])

    @mcp.tool()
    def fix_ticket(cycle_id: int, issue_key: str, fix_note: str) -> dict[str, Any]:
        """(데모) 티켓을 수정 완료 처리한다. fix_note에는 원인과 수정 내용을 1~2문장으로 적는다. 수정분은 다음 publish_release에 반영된다."""
        return run(dev.fix_ticket, cycle_id, issue_key, fix_note)

    @mcp.tool()
    def publish_release(cycle_id: int, release_note: str) -> dict[str, Any]:
        """(데모) 수정 완료되었지만 아직 배포되지 않은 티켓을 반영해 차기 버전을 발행한다."""
        return run(dev.publish_release, cycle_id, release_note)

    @mcp.tool()
    def list_tickets(cycle_id: int) -> dict[str, Any]:
        """사이클의 개발 티켓 목록과 상태를 조회한다."""
        return {"tickets": db.all("SELECT key, test_case_id, title, severity, status, assignee, fixed_in_version FROM tickets WHERE cycle_id = ? ORDER BY id",
                                  cycle_id)}

    # ----- 실습: 개발부서원에게 작업을 맡기고 결과를 회수 -----
    @mcp.tool()
    def request_release(cycle_id: int) -> dict[str, Any]:
        """(실습) 개발부서에 버전 발행을 요청한다. 개발부서원이 Dev 웹에서 티켓을 수정하고 발행하면 완료 작업으로 남는다."""
        return run(dev.request_release, cycle_id)

    @mcp.tool()
    def list_completed_work(cycle_id: int) -> dict[str, Any]:
        """(실습) 개발부서원이 끝냈지만 아직 확인(ack)하지 않은 작업 목록. outcome에 발행 결과(version, build_url, fixed_issue_keys)가 있다."""
        return {"completed": dev.completed_work(cycle_id)}

    @mcp.tool()
    def ack_work(work_id: int) -> dict[str, Any]:
        """(실습) 완료 작업의 결과를 상대 부서에 전달했음을 확인 처리한다."""
        return dev.ack_work(work_id)

    @mcp.tool()
    def cancel_work(cycle_id: int) -> dict[str, Any]:
        """(Host 전용) 사이클이 중단되었을 때 그 사이클의 진행 중인 발행 요청을 취소한다. Host 코드가 중단 처리에서만 호출한다."""
        return dev.cancel_work(cycle_id)

    # ----- resources (Hub 조회용) -----
    @mcp.resource("dev://cycles/{cycle_id}/releases", mime_type="application/json", description="사이클의 릴리즈 목록")
    def releases_resource(cycle_id: str) -> str:
        return json.dumps(db.all("SELECT id, cycle_id, version, features, fixed_issue_keys, release_note, released_by, released_at FROM releases"
                                 " WHERE cycle_id = ? ORDER BY id", int(cycle_id)), ensure_ascii=False)

    @mcp.resource("dev://cycles/{cycle_id}/tickets", mime_type="application/json", description="사이클의 개발 티켓 목록 (이력 포함)")
    def tickets_resource(cycle_id: str) -> str:
        rows = db.all("SELECT * FROM tickets WHERE cycle_id = ? ORDER BY id", int(cycle_id))
        for row in rows:
            row["history"] = db.all("SELECT * FROM ticket_history WHERE ticket_id = ? ORDER BY id", row["id"])
        return json.dumps(rows, ensure_ascii=False)

    @mcp.resource("dev://cycles/{cycle_id}/work", mime_type="application/json", description="사이클의 작업 지시(발행 요청) 목록")
    def work_resource(cycle_id: str) -> str:
        return json.dumps(db.all("SELECT * FROM work_orders WHERE cycle_id = ? ORDER BY id", int(cycle_id)), ensure_ascii=False)

    return mcp


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(config.db_path("dev"), SCHEMA)
    bus = EventBus(db)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    dev = DevSystem(db, bus, llm, config.DEV_URL)
    mcp = create_mcp(dev, db)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            print(f"[Stage 3] Dev MCP Server ready (/mcp, web /, LLM: {llm.label})", flush=True)
            yield

    app = FastAPI(title="Stage 3 - Dev MCP Server", lifespan=lifespan)
    templates = setup_web(app, TEMPLATES)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def view_cycle_id() -> int | None:
        order = db.one("SELECT cycle_id FROM work_orders ORDER BY id DESC LIMIT 1")
        rel = db.one("SELECT cycle_id FROM releases ORDER BY id DESC LIMIT 1")
        ids = [r["cycle_id"] for r in (order, rel) if r]
        return max(ids) if ids else None

    def render(request: Request, template: str, active: str, **ctx):
        order = dev.open_order()
        cid = ctx.get("cycle_id")
        nav = [
            ("업무", [("desk", "/", "개발 작업대", "차례" if order else ""), ("tickets", "/tickets", "티켓", "")]),
            ("기록", [("timeline", "/timeline", "타임라인", "")]),
            ("연결", [("hub", f"http://localhost:{config.HOST_PORT}", "Hub 웹 (MCP Host)", str(config.HOST_PORT)),
                     ("qa", f"http://localhost:{config.QA_PORT}", "QA 웹", str(config.QA_PORT))]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "llm_label": llm.label, "members": dev.members.all(),
            "cycle": {"id": cid, "mode": "manual" if db.scalar("SELECT COUNT(*) FROM work_orders WHERE cycle_id = ?", cid) else "demo",
                      "status": ("RELEASING" if order["kind"] == "initial" else "FIXING") if order and order["cycle_id"] == cid else None} if cid else None,
            "order": order, "errors": [], "host_port": config.HOST_PORT, **ctx,
        })

    # ---------- 웹 ----------
    @app.get("/", include_in_schema=False)
    async def desk(request: Request):
        cid = view_cycle_id()
        work = db.all("SELECT * FROM tickets WHERE cycle_id = ? AND (status IN ('RECEIVED', 'IN_PROGRESS')"
                      " OR (status = 'FIXED' AND fixed_in_version IS NULL)) ORDER BY id", cid or 0)
        return render(request, "desk.html", "desk", cycle_id=cid, work=work, shippable=dev.shippable(cid or 0),
                      next_version=dev.next_version(cid) if cid else None,
                      releases=db.all("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id", cid or 0),
                      orders=db.all("SELECT * FROM work_orders WHERE cycle_id = ? ORDER BY id DESC", cid or 0))

    @app.get("/tickets", include_in_schema=False)
    async def tickets_page(request: Request):
        cid = view_cycle_id()
        return render(request, "tickets.html", "tickets", cycle_id=cid, tickets=db.all("SELECT * FROM tickets WHERE cycle_id = ? ORDER BY id", cid or 0))

    @app.get("/tickets/{key}", include_in_schema=False)
    async def ticket_page(request: Request, key: str):
        cid = view_cycle_id()
        ticket = db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cid or 0, key)
        if not ticket:
            raise HTTPException(404, "ticket not found")
        return render(request, "ticket_detail.html", "tickets", cycle_id=cid, ticket=ticket,
                      history=db.all("SELECT * FROM ticket_history WHERE ticket_id = ? ORDER BY id", ticket["id"]))

    @app.get("/timeline", include_in_schema=False)
    async def timeline_page(request: Request):
        cid = view_cycle_id()
        return render(request, "timeline.html", "timeline", cycle_id=cid,
                      events=db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT 500", cid or 0),
                      desc="Dev 시스템이 기록한 이벤트입니다. 'Host' 행은 Host(AI)가 MCP 도구로 요청한 작업입니다.")

    # ---------- HTTP (MCP 외부) ----------
    @app.get("/health")
    async def health():
        return {"service": "dev-mcp", "status": "ok", "mcp": "/mcp"}

    @app.get("/builds/{cycle_id}/{version}")
    async def build(cycle_id: int, version: str):
        row = db.one("SELECT build FROM releases WHERE cycle_id = ? AND version = ?", cycle_id, version)
        if not row:
            raise HTTPException(404, "build not found")
        return row["build"]

    @app.get("/api/events")
    async def events(cycle_id: int, limit: int = 500):
        return db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit)

    @app.get("/api/events/stream")
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/admin/reset")
    async def reset():
        db.reset()
        dev.members.seed()
        bus.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    # ---------- 실습 API (Dev 웹, body.actor = 개발부서원) ----------
    @app.post("/api/work/release")
    async def work_release(body: dict):
        return dev.release(body.get("actor"), body.get("release_note", ""))

    @app.post("/api/work/issues/{key}/start")
    async def work_start_fix(key: str, body: dict):
        dev.start_fix(key, body.get("actor"))
        return {"ok": True}

    @app.post("/api/work/issues/{key}/fix")
    async def work_fix(key: str, body: dict):
        dev.fix(key, body.get("actor"), body.get("fix_note", ""))
        return {"ok": True}

    @app.post("/api/work/draft/fix/{key}")
    async def draft_fix(key: str):
        return await dev.draft_fix(key)

    @app.post("/api/work/draft/release")
    async def draft_release():
        return await dev.draft_release()

    app.mount("/", mcp_app)  # 마지막에 마운트: /mcp 엔드포인트
    return app
