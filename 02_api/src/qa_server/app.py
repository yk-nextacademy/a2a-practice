"""QA Server (:8202) - 검증부서 웹 서비스 + REST API. Swagger: /docs"""

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

from .service import SCHEMA, QAService

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "Q", "name": "QA 웹", "sub": "Stage 2 · 검증부서 시스템", "crumb": "QA", "system": "QA Server :8202"}


class ReleasePublished(BaseModel):
    cycle_id: int
    version: str
    build_url: str
    fixed_issue_keys: list[str] = []
    mode: str = "demo"


class CycleCancelled(BaseModel):
    cycle_id: int
    reason: str = "사이클 중단"


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s qa %(name)s: %(message)s")
    db = Database(config.db_path("qa"), SCHEMA)
    bus = EventBus(db)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    client = IntegrationClient(db, "qa-server")
    service = QAService(db, bus, llm, client)
    service.seed()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[Stage 2] QA Server ready (LLM: {llm.label})", flush=True)
        yield
        for task in list(service.tasks):
            task.cancel()
        await client.close()

    app = FastAPI(title="Stage 2 - QA Server", description="검증부서 시스템: 문제 탐지, 이관 또는 버전 확정", lifespan=lifespan)
    app.add_middleware(InboundLogMiddleware, db=db)
    add_common_routes(app, "qa-server", db, bus, llm.label)
    templates = setup_web(app, TEMPLATES)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def view_run() -> dict | None:
        return service.current_run() or db.one("SELECT * FROM test_runs ORDER BY id DESC LIMIT 1")

    def render(request: Request, template: str, active: str, run: dict | None = None, **ctx):
        current = service.current_run()
        cycle = {"id": run["cycle_id"], "mode": run["mode"], "status": "VERIFYING" if run["status"] == "RUNNING" else None} if run else None
        nav = [
            ("업무", [("desk", "/", "검증 작업대", "차례" if current else ""), ("runs", "/runs", "검증 이력", ""), ("issues", "/issues", "이슈", "")]),
            ("기록", [("timeline", "/timeline", "타임라인", ""), ("integration", "/integration", "연동 로그", "")]),
            ("연결", [("hub", f"http://localhost:{config.PORTAL_PORT}", "Hub 웹 (관제)", "8200"),
                     ("dev", f"http://localhost:{config.DEV_PORT}", "Dev 웹", "8201"), ("docs", "/docs", "API 문서", "")]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "llm_label": llm.label, "members": service.members.all(),
            "cycle": cycle, "run": run, "errors": [], **ctx,
        })

    # ---------- 웹 화면 ----------
    @app.get("/", include_in_schema=False)
    async def desk(request: Request):
        run = view_run()
        cid, version = (run["cycle_id"], run["version"]) if run else (0, None)
        results = db.all(
            "SELECT r.*, c.name, c.feature, (SELECT key FROM issues i WHERE i.cycle_id = ? AND i.test_case_id = r.test_case_id"
            " AND i.status IN ('OPEN', 'TRANSFERRED', 'REOPENED', 'FIXED') ORDER BY i.id DESC LIMIT 1) AS issue_key"
            " FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.run_id = ? ORDER BY r.id",
            cid, run["id"] if run else 0)
        return render(request, "desk.html", "desk", run=run, results=results,
                      to_verify=db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ? ORDER BY id", cid, version),
                      found=db.all("SELECT * FROM issues WHERE cycle_id = ? AND version_found = ? ORDER BY id", cid, version),
                      ready=service.readiness(service.current_run()), severities=["Critical", "Major", "Minor"])

    @app.get("/runs", include_in_schema=False)
    async def runs_page(request: Request):
        return render(request, "runs.html", "runs", run=view_run(), runs=db.all("SELECT * FROM test_runs ORDER BY id DESC LIMIT 100"))

    @app.get("/runs/{run_id}", include_in_schema=False)
    async def run_page(request: Request, run_id: int):
        run = db.one("SELECT * FROM test_runs WHERE id = ?", run_id)
        if not run:
            raise HTTPException(404, "test run not found")
        results = db.all("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.run_id = ? ORDER BY r.id",
                         run_id)
        return render(request, "test_run_detail.html", "runs", run=run, results=results)

    @app.get("/issues", include_in_schema=False)
    async def issues_page(request: Request):
        run = view_run()
        return render(request, "issues.html", "issues", run=run,
                      issues=db.all("SELECT * FROM issues WHERE cycle_id = ? ORDER BY id", run["cycle_id"]) if run else [])

    @app.get("/issues/{key}", include_in_schema=False)
    async def issue_page(request: Request, key: str):
        run = view_run()
        issue = db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", run["cycle_id"], key) if run else None
        if not issue:
            raise HTTPException(404, "issue not found")
        history = db.all("SELECT * FROM issue_history WHERE issue_id = ? ORDER BY id", issue["id"])
        return render(request, "issue_detail.html", "issues", run=run, issue=issue, history=history)

    @app.get("/timeline", include_in_schema=False)
    async def timeline_page(request: Request):
        run = view_run()
        events = db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT 500", run["cycle_id"]) if run else []
        return render(request, "timeline.html", "timeline", run=run, events=events,
                      desc="QA 시스템이 기록한 업무 이벤트입니다. Dev 시스템의 기록은 Dev 웹, 전체는 Hub 웹에서 봅니다.")

    @app.get("/integration", include_in_schema=False)
    async def integration_page(request: Request):
        run = view_run()
        logs = db.all("SELECT * FROM integration_logs WHERE cycle_id = ? ORDER BY id DESC", run["cycle_id"]) if run else []
        return render(request, "integration_log.html", "integration", run=run, logs=logs,
                      desc="QA 시스템이 Dev 시스템을 호출한 기록(out)과 Dev 시스템으로부터 받은 호출(in)입니다.")

    # ---------- 연동 API ----------
    @app.post("/api/webhooks/release-published", tags=["webhook"], status_code=202)
    async def release_published(payload: ReleasePublished):
        """Dev의 버전 발행 통지. 데모는 자동 검증, 실습은 검증 항목을 대기 상태로 생성한다."""
        service.on_release_published(payload.cycle_id, payload.version, payload.build_url, payload.fixed_issue_keys, payload.mode)
        return {"accepted": True}

    @app.post("/api/webhooks/cycle-cancelled", tags=["webhook"])
    async def cycle_cancelled(payload: CycleCancelled):
        """Dev의 사이클 중단 통지. 해당 사이클의 자동 검증을 멈추고 진행 중인 검증 실행을 취소한다."""
        return {"cancelled_runs": service.on_cycle_cancelled(payload.cycle_id, payload.reason)}

    @app.get("/api/state", tags=["test"])
    async def state():
        run = service.current_run()
        return {"verifying": run is not None, "run": {k: run[k] for k in ("id", "cycle_id", "version", "passed", "failed", "total")} if run else None,
                "readiness": service.readiness(run)}

    @app.get("/api/test-cases", tags=["test"])
    async def test_cases():
        return db.all("SELECT * FROM test_cases ORDER BY id")

    @app.get("/api/test-runs", tags=["test"])
    async def test_runs(cycle_id: int):
        return db.all("SELECT id, cycle_id, version, build_url, total, passed, failed, status, mode, decision, decided_by, started_at, finished_at"
                      " FROM test_runs WHERE cycle_id = ? ORDER BY id", cycle_id)

    @app.get("/api/test-runs/{run_id}", tags=["test"])
    async def test_run(run_id: int):
        run = db.one("SELECT id, cycle_id, version, build_url, total, passed, failed, status, mode, decision, decided_by, started_at, finished_at"
                     " FROM test_runs WHERE id = ?", run_id)
        if not run:
            raise HTTPException(404, "test run not found")
        run["results"] = db.all("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id"
                                " WHERE r.run_id = ? ORDER BY r.id", run_id)
        return run

    @app.get("/api/issues", tags=["issue"])
    async def issues(cycle_id: int):
        return db.all("SELECT * FROM issues WHERE cycle_id = ? ORDER BY id", cycle_id)

    @app.get("/api/issues/{key}", tags=["issue"])
    async def issue(key: str, cycle_id: int):
        row = db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle_id, key)
        if not row:
            raise HTTPException(404, "issue not found")
        row["history"] = db.all("SELECT * FROM issue_history WHERE issue_id = ? ORDER BY id", row["id"])
        return row

    @app.post("/api/admin/reset", tags=["ops"])
    async def reset(dry_run: bool = False):
        """데이터 초기화(테스트케이스 유지). dry_run=true면 초기화할 수 있는지만 확인한다."""
        if service.busy():
            return JSONResponse({"error": "검증 실행 중에는 초기화할 수 없습니다."}, status_code=409)
        if dry_run:
            return {"ok": True, "dry_run": True}
        db.reset(keep=("test_cases",))
        service.seed()
        bus.publish({"type": "system.reset", "actor": "QA", "payload": {}})
        return {"ok": True}

    # ---------- 실습 API (body.actor = 검증부서원) ----------
    @app.post("/api/work/results/{result_id}/execute", tags=["work"])
    async def work_execute(result_id: int, body: dict):
        return await service.execute(result_id, body.get("actor"))

    @app.post("/api/work/results/{result_id}/judge", tags=["work"])
    async def work_judge(result_id: int, body: dict):
        service.judge(result_id, body.get("actor"), body.get("result"))
        return {"ok": True}

    @app.post("/api/work/issues", tags=["work"])
    async def work_issue(body: dict):
        return service.create_issue(body.get("actor"), body)

    @app.post("/api/work/issues/{key}/verify", tags=["work"])
    async def work_verify(key: str, body: dict):
        service.verify_issue(key, body.get("actor"), body.get("ok") in (True, "true", "1"))
        return {"ok": True}

    @app.post("/api/work/transfer", tags=["work"])
    async def work_transfer(body: dict):
        return {"transferred": await service.transfer(body.get("actor"))}

    @app.post("/api/work/approve", tags=["work"])
    async def work_approve(body: dict):
        await service.approve(body.get("actor"))
        return {"ok": True}

    @app.post("/api/work/draft/issue/{result_id}", tags=["work"])
    async def draft_issue(result_id: int):
        return await service.draft_issue(result_id)

    return app
