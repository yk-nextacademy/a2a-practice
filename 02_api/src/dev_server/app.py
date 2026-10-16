"""Dev Server (:8201) - 개발부서 웹 서비스 + REST API. Swagger: /docs"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from common import config
from common.db import Database
from common.events import EventBus
from common.http import InboundLogMiddleware, IntegrationClient
from common.llm import LLMClient
from common.service_api import add_common_routes
from common.web.setup import setup_web
from common.work import WorkError

from .service import ACTIVE, SCHEMA, CycleConflict, DevService

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "D", "name": "Dev 웹", "sub": "Stage 2 · 개발부서 시스템", "crumb": "Dev", "system": "Dev Server :8201"}


class IssuePayload(BaseModel):
    key: str
    test_case_id: str
    title: str
    description: str | None = None
    severity: str
    version_found: str


class TicketsPayload(BaseModel):
    cycle_id: int
    version: str
    issues: list[IssuePayload]


class VerificationPayload(BaseModel):
    cycle_id: int
    version: str
    run_id: int
    passed: int
    total: int


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s dev %(name)s: %(message)s")
    db = Database(config.db_path("dev"), SCHEMA)
    bus = EventBus(db)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    client = IntegrationClient(db, "dev-server")
    service = DevService(db, bus, llm, client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[Stage 2] Dev Server ready (LLM: {llm.label})", flush=True)
        yield
        for task in list(service.tasks):
            task.cancel()
        await client.close()

    app = FastAPI(title="Stage 2 - Dev Server", description="개발부서 시스템: 버전 발행, 접수된 문제 수정", lifespan=lifespan)
    app.add_middleware(InboundLogMiddleware, db=db)
    add_common_routes(app, "dev-server", db, bus, llm.label)
    templates = setup_web(app, TEMPLATES)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def view_cycle() -> dict | None:
        return service.active_cycle() or db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1")

    def render(request: Request, template: str, active: str, **ctx):
        active_cycle = service.active_cycle()
        turn = active_cycle and active_cycle["mode"] == "manual" and active_cycle["status"] in ("RELEASING", "FIXING")
        nav = [
            ("업무", [("desk", "/", "개발 작업대", "차례" if turn else ""), ("releases", "/releases", "릴리즈", ""),
                     ("tickets", "/tickets", "티켓", "")]),
            ("기록", [("timeline", "/timeline", "타임라인", ""), ("integration", "/integration", "연동 로그", "")]),
            ("연결", [("hub", f"http://localhost:{config.PORTAL_PORT}", "Hub 웹 (관제)", "8200"),
                     ("qa", f"http://localhost:{config.QA_PORT}", "QA 웹", "8202"), ("docs", "/docs", "API 문서", "")]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "llm_label": llm.label, "members": service.members.all(),
            "active_cycle": active_cycle, "errors": [], **ctx,
        })

    # ---------- 웹 화면 ----------
    @app.get("/", include_in_schema=False)
    async def desk(request: Request):
        cycle = view_cycle()
        cid = cycle["id"] if cycle else 0
        work = db.all("SELECT * FROM tickets WHERE cycle_id = ? AND (status IN ('RECEIVED', 'IN_PROGRESS')"
                      " OR (status = 'FIXED' AND fixed_in_version IS NULL)) ORDER BY id", cid)
        return render(request, "desk.html", "desk", cycle=cycle, work=work, shippable=service.shippable(cid),
                      next_version=service.next_version(cid) if cycle else None,
                      has_release=bool(db.scalar("SELECT COUNT(*) FROM releases WHERE cycle_id = ?", cid)),
                      releases=db.all("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id", cid))

    @app.get("/releases", include_in_schema=False)
    async def releases_page(request: Request):
        cycle = view_cycle()
        return render(request, "releases.html", "releases", cycle=cycle,
                      releases=db.all("SELECT r.*, c.mode FROM releases r JOIN cycles c ON c.id = r.cycle_id ORDER BY r.id DESC LIMIT 100"))

    @app.get("/tickets", include_in_schema=False)
    async def tickets_page(request: Request):
        cycle = view_cycle()
        return render(request, "tickets.html", "tickets", cycle=cycle,
                      tickets=db.all("SELECT * FROM tickets WHERE cycle_id = ? ORDER BY id", cycle["id"]) if cycle else [])

    @app.get("/tickets/{key}", include_in_schema=False)
    async def ticket_page(request: Request, key: str, cycle_id: int | None = None):
        cycle = service.cycle(cycle_id) if cycle_id else view_cycle()
        ticket = db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle["id"], key) if cycle else None
        if not ticket:
            raise HTTPException(404, "ticket not found")
        history = db.all("SELECT * FROM ticket_history WHERE ticket_id = ? ORDER BY id", ticket["id"])
        return render(request, "ticket_detail.html", "tickets", cycle=cycle, ticket=ticket, history=history)

    @app.get("/timeline", include_in_schema=False)
    async def timeline_page(request: Request):
        cycle = view_cycle()
        events = db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT 500", cycle["id"]) if cycle else []
        return render(request, "timeline.html", "timeline", cycle=cycle, events=events,
                      desc="Dev 시스템이 기록한 업무 이벤트입니다. QA 시스템의 기록은 QA 웹, 전체는 Hub 웹에서 봅니다.")

    @app.get("/integration", include_in_schema=False)
    async def integration_page(request: Request):
        cycle = view_cycle()
        logs = db.all("SELECT * FROM integration_logs WHERE cycle_id = ? ORDER BY id DESC", cycle["id"]) if cycle else []
        return render(request, "integration_log.html", "integration", cycle=cycle, logs=logs,
                      desc="Dev 시스템이 QA 시스템을 호출한 기록(out)과 QA 시스템으로부터 받은 호출(in)입니다.")

    # ---------- 운영/연동 API ----------
    @app.get("/api/state", tags=["cycle"])
    async def state():
        return {"running": service.active_cycle() is not None,
                "cycle": db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1"),
                "llm": {"mode": llm.mode, "model": llm.model, "calls": llm.calls, "failures": llm.failures, "last_error": llm.last_error}}

    @app.post("/api/cycles", tags=["cycle"], status_code=201)
    async def start_cycle(mode: str = "demo"):
        """사이클 시작. mode=demo(자동 진행) / manual(실습: 부서원이 직접 진행)"""
        if mode not in ("demo", "manual"):
            return JSONResponse({"error": "mode는 demo 또는 manual입니다."}, status_code=400)
        try:
            return {"cycle_id": service.start_cycle(mode)}
        except CycleConflict:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다."}, status_code=409)

    @app.post("/api/cycles/{cycle_id}/abort", tags=["cycle"])
    async def abort_cycle(cycle_id: int):
        """멈춘 사이클이나 실습 사이클을 중단한다."""
        row = service.cycle(cycle_id)
        if not row or row["status"] not in ACTIVE:
            return JSONResponse({"error": "진행 중인 사이클이 아닙니다."}, status_code=409)
        await service.abort(cycle_id)
        return {"ok": True}

    @app.get("/api/cycles", tags=["cycle"])
    async def cycles():
        return db.all("SELECT c.*, (SELECT COUNT(*) FROM releases r WHERE r.cycle_id = c.id) AS releases,"
                      " (SELECT COUNT(*) FROM tickets t WHERE t.cycle_id = c.id) AS tickets FROM cycles c ORDER BY id DESC")

    @app.get("/api/cycles/{cycle_id}", tags=["cycle"])
    async def cycle(cycle_id: int):
        row = service.cycle(cycle_id)
        if not row:
            raise HTTPException(404, "cycle not found")
        return row

    @app.get("/api/releases", tags=["release"])
    async def releases(cycle_id: int):
        return db.all("SELECT id, cycle_id, version, features, fixed_issue_keys, release_note, released_by, released_at, approved_at"
                      " FROM releases WHERE cycle_id = ? ORDER BY id", cycle_id)

    @app.get("/api/releases/{version}", tags=["release"])
    async def release(version: str, cycle_id: int):
        row = db.one("SELECT id, cycle_id, version, features, fixed_issue_keys, release_note, released_by, released_at, approved_at"
                     " FROM releases WHERE cycle_id = ? AND version = ?", cycle_id, version)
        if not row:
            raise HTTPException(404, "release not found")
        return row

    @app.get("/builds/{cycle_id}/{version}", tags=["release"])
    async def build(cycle_id: int, version: str):
        """빌드 산출물 다운로드 (QA가 build_url로 호출)."""
        row = db.one("SELECT build FROM releases WHERE cycle_id = ? AND version = ?", cycle_id, version)
        if not row:
            raise HTTPException(404, "build not found")
        return row["build"]

    @app.post("/api/tickets", tags=["ticket"], status_code=202)
    async def receive_tickets(payload: TicketsPayload):
        """QA로부터 이슈 이관 수신. 데모는 자동 수정·발행, 실습은 개발부서원이 처리한다."""
        try:
            service.receive_tickets(payload.cycle_id, payload.version, [i.model_dump() for i in payload.issues])
        except CycleConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"accepted": len(payload.issues)}

    @app.get("/api/tickets", tags=["ticket"])
    async def tickets(cycle_id: int):
        return db.all("SELECT * FROM tickets WHERE cycle_id = ? ORDER BY id", cycle_id)

    @app.get("/api/tickets/{key}", tags=["ticket"])
    async def ticket(key: str, cycle_id: int):
        row = db.one("SELECT * FROM tickets WHERE cycle_id = ? AND key = ?", cycle_id, key)
        if not row:
            raise HTTPException(404, "ticket not found")
        row["history"] = db.all("SELECT * FROM ticket_history WHERE ticket_id = ? ORDER BY id", row["id"])
        return row

    @app.post("/api/webhooks/verification-completed", tags=["webhook"])
    async def verification_completed(payload: VerificationPayload):
        """QA의 검증 완료(버전 확정) 통지. 검증 중인 사이클·버전이 아니면 409."""
        try:
            service.verification_completed(payload.cycle_id, payload.version, payload.run_id, payload.passed, payload.total)
        except CycleConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"ok": True}

    @app.post("/api/admin/reset", tags=["ops"])
    async def reset(dry_run: bool = False):
        """데이터 초기화. dry_run=true면 초기화할 수 있는지만 확인한다 (Hub가 양쪽을 먼저 확인할 때 사용)."""
        if service.active_cycle() and service.active_cycle()["mode"] == "demo":
            return JSONResponse({"error": "데모 실행 중에는 초기화할 수 없습니다."}, status_code=409)
        if dry_run:
            return {"ok": True, "dry_run": True}
        db.reset()
        service.members.seed()
        bus.publish({"type": "system.reset", "actor": "Dev", "payload": {}})
        return {"ok": True}

    # ---------- 실습 API (body.actor = 개발부서원) ----------
    @app.post("/api/work/start", tags=["work"])
    async def work_start():
        try:
            return {"cycle_id": service.start_cycle("manual")}
        except CycleConflict:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다."}, status_code=409)

    @app.post("/api/work/abort", tags=["work"])
    async def work_abort():
        cycle = service.active_cycle()
        if not cycle:
            return JSONResponse({"error": "진행 중인 사이클이 없습니다."}, status_code=409)
        await service.abort(cycle["id"])
        return {"ok": True}

    @app.post("/api/work/release", tags=["work"])
    async def work_release(body: dict):
        return {"version": await service.release(body.get("actor"), body.get("release_note", ""))}

    @app.post("/api/work/issues/{key}/start", tags=["work"])
    async def work_start_fix(key: str, body: dict):
        service.start_fix(key, body.get("actor"))
        return {"ok": True}

    @app.post("/api/work/issues/{key}/fix", tags=["work"])
    async def work_fix(key: str, body: dict):
        service.fix(key, body.get("actor"), body.get("fix_note", ""))
        return {"ok": True}

    @app.post("/api/work/draft/fix/{key}", tags=["work"])
    async def draft_fix(key: str):
        return await service.draft_fix(key)

    @app.post("/api/work/draft/release", tags=["work"])
    async def draft_release():
        return await service.draft_release()

    return app
