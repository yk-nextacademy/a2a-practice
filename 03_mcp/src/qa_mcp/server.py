"""QA MCP Server (:8302) - MCP 도구(/mcp) + QA 웹(/).

MCP 도구는 Host(AI)를 위한 연결 지점이고, QA 웹은 검증부서원이 일하는 화면이다.
이 시스템은 Dev 시스템과 업무 통신을 하지 않는다 (빌드 산출물만 build_url로 내려받는다).
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from common import config
from common.db import Database
from common.events import EventBus
from common.llm import LLMClient
from common.web.setup import setup_web
from common.work import WorkError

from .service import SCHEMA, QASystem

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "Q", "name": "QA 웹", "sub": "Stage 3 · 검증부서 시스템 (MCP)", "crumb": "QA", "system": f"QA MCP :{config.QA_PORT}"}


def create_mcp(qa: QASystem, db: Database) -> MCPServer:
    mcp = MCPServer(name="qa-department",
                    instructions="검증부서 시스템. 빌드 테스트 실행, 이슈 등록·이관·승인, 검증부서원에게 검증 요청(실습)을 제공한다.")

    def run(fn, *args):
        try:
            return fn(*args)
        except WorkError as exc:
            raise ToolError(str(exc))

    # ----- 데모: Host의 AI가 직접 수행 -----
    @mcp.tool()
    async def run_tests(cycle_id: int, version: str, build_url: str) -> dict[str, Any]:
        """(데모) build_url에서 빌드를 내려받아 테스트케이스 12건을 실행한다. 실패한 테스트의 로그를 반환한다."""
        try:
            return await qa.run_tests(cycle_id, version, build_url)
        except WorkError as exc:
            raise ToolError(str(exc))

    @mcp.tool()
    def verify_fixed_issues(cycle_id: int, version: str, run_id: int, fixed_issue_keys: list[str]) -> dict[str, Any]:
        """(데모) 개발부서가 이 버전에서 수정했다고 한 이슈를 테스트 결과로 확인한다. 통과하면 VERIFIED, 재현되면 REOPENED."""
        return run(qa.verify_fixed_issues, cycle_id, version, run_id, fixed_issue_keys)

    @mcp.tool()
    def create_issue(cycle_id: int, version: str, test_case_id: str, title: str, description: str,
                     severity: Literal["Critical", "Major", "Minor"]) -> dict[str, Any]:
        """(데모) 실패한 테스트에 대한 이슈를 등록한다. 같은 테스트케이스의 미해결 이슈가 있으면 기존 키를 반환한다."""
        return run(qa.create_issue, cycle_id, version, test_case_id, title, description, severity)

    @mcp.tool()
    def transfer_issues(cycle_id: int) -> dict[str, Any]:
        """OPEN/REOPENED 이슈를 이관 상태(TRANSFERRED)로 바꾸고, 개발부서 register_tickets에 넘길 데이터를 반환한다.
        이미 이관됐지만 직전 버전에 반영되지 않은 이슈(이월)도 함께 반환한다."""
        return run(qa.transfer_issues, cycle_id)

    @mcp.tool()
    def approve_release(cycle_id: int, version: str, run_id: int, comment: str) -> dict[str, Any]:
        """전체 테스트가 통과한 버전의 릴리즈 확정을 기록한다. 실패가 있는 실행은 승인할 수 없다."""
        return run(qa.approve_release, cycle_id, version, run_id, comment)

    @mcp.tool()
    def list_issues(cycle_id: int) -> dict[str, Any]:
        """사이클의 이슈 목록과 상태를 조회한다."""
        return {"issues": db.all("SELECT key, test_case_id, title, severity, status, version_found, fixed_in_version FROM issues WHERE cycle_id = ? ORDER BY id",
                                 cycle_id)}

    # ----- 실습: 검증부서원에게 작업을 맡기고 결정을 회수 -----
    @mcp.tool()
    def request_verification(cycle_id: int, version: str, build_url: str, fixed_issue_keys: list[str]) -> dict[str, Any]:
        """(실습) 검증부서에 검증을 요청한다. 검증부서원이 QA 웹에서 판정하고 이관 또는 확정을 결정하면 완료 작업으로 남는다."""
        return run(qa.request_verification, cycle_id, version, build_url, fixed_issue_keys)

    @mcp.tool()
    def list_completed_work(cycle_id: int) -> dict[str, Any]:
        """(실습) 검증부서원이 끝냈지만 아직 확인(ack)하지 않은 작업. outcome.decision이 transfer(이관) 또는 approve(확정)이다."""
        return {"completed": qa.completed_work(cycle_id)}

    @mcp.tool()
    def ack_work(work_id: int) -> dict[str, Any]:
        """(실습) 완료 작업의 결정을 상대 부서에 전달했음을 확인 처리한다."""
        return qa.ack_work(work_id)

    @mcp.tool()
    def cancel_work(cycle_id: int) -> dict[str, Any]:
        """(Host 전용) 사이클이 중단되었을 때 그 사이클의 진행 중인 검증 요청과 검증 실행을 취소한다. Host 코드가 중단 처리에서만 호출한다."""
        return qa.cancel_work(cycle_id)

    # ----- resources (Hub 조회용) -----
    @mcp.resource("qa://test-cases", mime_type="application/json", description="테스트케이스 목록")
    def test_cases_resource() -> str:
        return json.dumps(db.all("SELECT * FROM test_cases ORDER BY id"), ensure_ascii=False)

    @mcp.resource("qa://cycles/{cycle_id}/test-runs", mime_type="application/json", description="사이클의 테스트 실행 목록")
    def runs_resource(cycle_id: str) -> str:
        return json.dumps(db.all("SELECT id, cycle_id, version, build_url, total, passed, failed, status, mode, decision, decided_by, approved,"
                                 " started_at, finished_at FROM test_runs WHERE cycle_id = ? ORDER BY id", int(cycle_id)), ensure_ascii=False)

    @mcp.resource("qa://test-runs/{run_id}", mime_type="application/json", description="테스트 실행 상세 (케이스별 결과)")
    def run_resource(run_id: str) -> str:
        run = db.one("SELECT id, cycle_id, version, build_url, total, passed, failed, status, mode, decision, decided_by, started_at, finished_at"
                     " FROM test_runs WHERE id = ?", int(run_id))
        if run:
            run["results"] = db.all("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id"
                                    " WHERE r.run_id = ? ORDER BY r.id", int(run_id))
        return json.dumps(run, ensure_ascii=False)

    @mcp.resource("qa://cycles/{cycle_id}/issues", mime_type="application/json", description="사이클의 이슈 목록 (이력 포함)")
    def issues_resource(cycle_id: str) -> str:
        rows = db.all("SELECT * FROM issues WHERE cycle_id = ? ORDER BY id", int(cycle_id))
        for row in rows:
            row["history"] = db.all("SELECT * FROM issue_history WHERE issue_id = ? ORDER BY id", row["id"])
        return json.dumps(rows, ensure_ascii=False)

    @mcp.resource("qa://status", mime_type="application/json", description="현재 검증 요청과 진행 상황")
    def status_resource() -> str:
        run = qa.current_run()
        return json.dumps({"run": {k: run[k] for k in ("id", "cycle_id", "version", "passed", "failed", "total")} if run else None,
                           "readiness": qa.readiness(run)}, ensure_ascii=False)

    return mcp


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(config.db_path("qa"), SCHEMA)
    bus = EventBus(db)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    qa = QASystem(db, bus, llm, config.TEST_DELAY)
    qa.seed()
    mcp = create_mcp(qa, db)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            print(f"[Stage 3] QA MCP Server ready (/mcp, web /, LLM: {llm.label})", flush=True)
            yield

    app = FastAPI(title="Stage 3 - QA MCP Server", lifespan=lifespan)
    templates = setup_web(app, TEMPLATES)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def view_run() -> dict | None:
        return qa.current_run() or db.one("SELECT * FROM test_runs ORDER BY id DESC LIMIT 1")

    def render(request: Request, template: str, active: str, run: dict | None = None, **ctx):
        current = qa.current_run()
        cycle = {"id": run["cycle_id"], "mode": run["mode"], "status": "VERIFYING" if current and current["id"] == run["id"] else None} if run else None
        nav = [
            ("업무", [("desk", "/", "검증 작업대", "차례" if current else ""), ("runs", "/runs", "검증 이력", ""), ("issues", "/issues", "이슈", "")]),
            ("기록", [("timeline", "/timeline", "타임라인", "")]),
            ("연결", [("hub", f"http://localhost:{config.HOST_PORT}", "Hub 웹 (MCP Host)", str(config.HOST_PORT)),
                     ("dev", f"http://localhost:{config.DEV_PORT}", "Dev 웹", str(config.DEV_PORT))]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "llm_label": llm.label, "members": qa.members.all(),
            "cycle": cycle, "run": run, "order": qa.open_order(), "errors": [], **ctx,
        })

    # ---------- 웹 ----------
    @app.get("/", include_in_schema=False)
    async def desk(request: Request):
        run = view_run()
        cid, version = (run["cycle_id"], run["version"]) if run else (0, None)
        results = db.all(
            "SELECT r.*, c.name, c.feature, (SELECT key FROM issues i WHERE i.cycle_id = ? AND i.test_case_id = r.test_case_id"
            " AND i.status IN ('OPEN', 'TRANSFERRED', 'REOPENED', 'FIXED') ORDER BY i.id DESC LIMIT 1) AS issue_key"
            " FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.run_id = ? ORDER BY r.id", cid, run["id"] if run else 0)
        current = qa.current_run()
        return render(request, "desk.html", "desk", run=run, results=results, live=bool(current and run and current["id"] == run["id"]),
                      to_verify=db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ? ORDER BY id", cid, version),
                      found=db.all("SELECT * FROM issues WHERE cycle_id = ? AND version_found = ? ORDER BY id", cid, version),
                      ready=qa.readiness(current), severities=["Critical", "Major", "Minor"])

    @app.get("/runs", include_in_schema=False)
    async def runs_page(request: Request):
        return render(request, "runs.html", "runs", run=view_run(), runs=db.all("SELECT * FROM test_runs ORDER BY id DESC LIMIT 100"))

    @app.get("/runs/{run_id}", include_in_schema=False)
    async def run_page(request: Request, run_id: int):
        run = db.one("SELECT * FROM test_runs WHERE id = ?", run_id)
        if not run:
            raise HTTPException(404, "test run not found")
        results = db.all("SELECT r.*, c.name, c.feature FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.run_id = ? ORDER BY r.id", run_id)
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
        return render(request, "issue_detail.html", "issues", run=run, issue=issue,
                      history=db.all("SELECT * FROM issue_history WHERE issue_id = ? ORDER BY id", issue["id"]))

    @app.get("/timeline", include_in_schema=False)
    async def timeline_page(request: Request):
        run = view_run()
        return render(request, "timeline.html", "timeline", run=run,
                      events=db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT 500", run["cycle_id"]) if run else [],
                      desc="QA 시스템이 기록한 이벤트입니다. 'Host' 행은 Host(AI)가 MCP 도구로 요청한 작업입니다.")

    # ---------- HTTP (MCP 외부) ----------
    @app.get("/health")
    async def health():
        return {"service": "qa-mcp", "status": "ok", "mcp": "/mcp"}

    @app.get("/api/events")
    async def events(cycle_id: int, limit: int = 500):
        return db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit)

    @app.get("/api/events/stream")
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/admin/reset")
    async def reset():
        db.reset(keep=("test_cases",))
        qa.seed()
        bus.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    # ---------- 실습 API (QA 웹, body.actor = 검증부서원) ----------
    @app.post("/api/work/results/{result_id}/execute")
    async def work_execute(result_id: int, body: dict):
        return await qa.execute(result_id, body.get("actor"))

    @app.post("/api/work/results/{result_id}/judge")
    async def work_judge(result_id: int, body: dict):
        qa.judge(result_id, body.get("actor"), body.get("result"))
        return {"ok": True}

    @app.post("/api/work/issues")
    async def work_issue(body: dict):
        return qa.staff_issue(body.get("actor"), body)

    @app.post("/api/work/issues/{key}/verify")
    async def work_verify(key: str, body: dict):
        qa.verify_issue(key, body.get("actor"), body.get("ok") in (True, "true", "1"))
        return {"ok": True}

    @app.post("/api/work/transfer")
    async def work_transfer(body: dict):
        return qa.transfer(body.get("actor"))

    @app.post("/api/work/approve")
    async def work_approve(body: dict):
        return qa.approve(body.get("actor"))

    @app.post("/api/work/draft/issue/{result_id}")
    async def draft_issue(result_id: int):
        return await qa.draft_issue(result_id)

    app.mount("/", mcp_app)  # 마지막에 마운트: /mcp 엔드포인트
    return app
