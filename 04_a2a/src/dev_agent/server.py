"""Dev Agent 서버 (:8401): Agent Card + A2A JSON-RPC(/a2a) + Dev 웹(/) + 빌드 산출물(/builds)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from common import config
from common.db import Database
from common.events import EventBus
from common.llm import LLMClient
from common.web.setup import setup_web
from common.work import WorkError

from .agent import SCHEMA, DevAgentExecutor, DevWork

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "D", "name": "Dev 웹", "sub": "Stage 4 · 개발부서 에이전트 (A2A)", "crumb": "Dev", "system": "Dev Agent :8401"}


def agent_card() -> AgentCard:
    return AgentCard(
        name="Dev Department Agent",
        description="개발부서 에이전트. 릴리즈 빌드를 발행하고, 위임받은 결함을 수정하여 차기 버전을 발행합니다. 실습 모드에서는 개발부서원이 작업하며 진행 상황을 스트리밍합니다.",
        version="1.0.0",
        provider=AgentProvider(organization="개발부서", url=config.DEV_URL),
        supported_interfaces=[AgentInterface(url=f"{config.DEV_URL}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id="release-build", name="최초 릴리즈 발행",
                description="사이클의 최초 버전을 빌드하여 발행한다. 입력 DataPart: {skill, cycle_id, mode}. 출력 Artifact 'release': {version, build_url, ...}",
                tags=["release", "build"], examples=["cycle 1의 최초 버전을 발행해 주세요."],
            ),
            AgentSkill(
                id="fix-and-release", name="결함 수정 후 차기 버전 발행",
                description="이관된 이슈를 수정하고 차기 버전을 발행한다. 입력 DataPart: {skill, cycle_id, mode, issues[]}. 출력 Artifact 'release' {fixes[]}",
                tags=["fix", "release"], examples=["이 이슈들을 수정해서 다음 버전을 발행해 주세요."],
            ),
        ],
    )


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(config.db_path("dev"), SCHEMA)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    card = agent_card()
    bus = EventBus(db)
    work = DevWork(db, bus, llm)
    handler = DefaultRequestHandler(agent_executor=DevAgentExecutor(work), task_store=InMemoryTaskStore(), agent_card=card)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[Stage 4] Dev Agent ready (A2A /a2a, web /, LLM: {llm.label})", flush=True)
        yield

    app = FastAPI(title="Stage 4 - Dev Agent", lifespan=lifespan)
    templates = setup_web(app, TEMPLATES)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def view_cycle_id() -> int | None:
        ids = [r["cycle_id"] for r in (db.one("SELECT cycle_id FROM work_orders ORDER BY id DESC LIMIT 1"),
                                        db.one("SELECT cycle_id FROM releases ORDER BY id DESC LIMIT 1")) if r]
        return max(ids) if ids else None

    def render(request: Request, template: str, active: str, **ctx):
        order = work.open_order()
        if order and order["id"] not in work.waiting:
            order = None
        cid = ctx.get("cycle_id")
        manual = bool(db.scalar("SELECT COUNT(*) FROM work_orders WHERE cycle_id = ?", cid)) if cid else False
        nav = [
            ("업무", [("desk", "/", "개발 작업대", "차례" if order else ""), ("tickets", "/tickets", "티켓", "")]),
            ("기록", [("timeline", "/timeline", "타임라인", ""), ("card", "/.well-known/agent-card.json", "Agent Card", "")]),
            ("연결", [("hub", f"http://localhost:{config.HUB_PORT}", "Hub 웹 (Orchestrator)", "8400"),
                     ("qa", f"http://localhost:{config.QA_PORT}", "QA 웹", "8402")]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "llm_label": llm.label, "members": work.members.all(),
            "cycle": {"id": cid, "mode": "manual" if manual else "demo",
                      "status": ("RELEASING" if order["kind"] == "initial" else "FIXING") if order and order["cycle_id"] == cid else None} if cid else None,
            "order": order, "errors": [], **ctx,
        })

    # ---------- 웹 ----------
    @app.get("/", include_in_schema=False)
    async def desk(request: Request):
        cid = view_cycle_id()
        rows = db.all("SELECT * FROM tickets WHERE cycle_id = ? AND (status IN ('RECEIVED', 'IN_PROGRESS')"
                      " OR (status = 'FIXED' AND fixed_in_version IS NULL)) ORDER BY id", cid or 0)
        return render(request, "desk.html", "desk", cycle_id=cid, work=rows, shippable=work.shippable(cid or 0),
                      next_version=work.next_version(cid) if cid else None,
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
                      desc="Dev Agent가 기록한 이벤트입니다. 'Hub' 행은 Hub가 A2A Task로 위임한 업무입니다.")

    # ---------- HTTP ----------
    @app.get("/health")
    async def health():
        return {"service": "dev-agent", "status": "ok", "llm": llm.label, "llm_calls": llm.calls}

    @app.get("/builds/{cycle_id}/{version}")
    async def build(cycle_id: int, version: str):
        row = db.one("SELECT build FROM releases WHERE cycle_id = ? AND version = ?", cycle_id, version)
        if not row:
            raise HTTPException(404, "build not found")
        return row["build"]

    @app.get("/api/events/stream", include_in_schema=False)
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/admin/reset")
    async def reset():
        db.reset()
        work.members.seed()
        bus.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    # ---------- 실습 API (Dev 웹, body.actor = 개발부서원) ----------
    @app.post("/api/work/release")
    async def work_release(body: dict):
        return await work.release(body.get("actor"), body.get("release_note", ""))

    @app.post("/api/work/issues/{key}/start")
    async def work_start_fix(key: str, body: dict):
        await work.start_fix(key, body.get("actor"))
        return {"ok": True}

    @app.post("/api/work/issues/{key}/fix")
    async def work_fix(key: str, body: dict):
        await work.fix(key, body.get("actor"), body.get("fix_note", ""))
        return {"ok": True}

    @app.post("/api/work/draft/fix/{key}")
    async def draft_fix(key: str):
        return await work.draft_fix(key)

    @app.post("/api/work/draft/release")
    async def draft_release():
        return await work.draft_release()

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )
    return app
