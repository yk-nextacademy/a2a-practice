"""QA Agent 서버 (:8402): Agent Card + A2A JSON-RPC(/a2a) + QA 웹(/)."""

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

from .agent import SCHEMA, QAAgentExecutor, QAWork

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "Q", "name": "QA 웹", "sub": "Stage 4 · 검증부서 에이전트 (A2A)", "crumb": "QA", "system": "QA Agent :8402"}


def agent_card() -> AgentCard:
    return AgentCard(
        name="QA Department Agent",
        description=("검증부서 에이전트. 발행된 빌드를 검증하고, 실패를 이슈로 정리하며, 수정 여부를 확인합니다. "
                     "실습 모드에서는 검증부서원이 판정하며 진행률을 스트리밍합니다. 확정 시 릴리즈 승인 여부를 되묻습니다."),
        version="1.0.0",
        provider=AgentProvider(organization="검증부서", url=config.QA_URL),
        supported_interfaces=[AgentInterface(url=f"{config.QA_URL}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id="verify-release", name="릴리즈 검증",
                description=("빌드를 내려받아 테스트케이스 12건을 검증하고, 수정 이슈를 확인하고, 실패를 이슈로 등록한다. "
                             "입력 DataPart: {skill, cycle_id, mode, version, build_url, fixed_issue_keys}. "
                             "출력 Artifact 'test-report', 이관 결정 시 'issues'. 확정 결정 시 input-required로 승인 여부를 묻고 'approval'을 반환한다."),
                tags=["qa", "test", "approval"], examples=["v1.1.0을 검증해 주세요."],
            ),
        ],
    )


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(config.db_path("qa"), SCHEMA)
    bus = EventBus(db)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    work = QAWork(db, bus, llm)
    work.seed()
    card = agent_card()
    handler = DefaultRequestHandler(agent_executor=QAAgentExecutor(work), task_store=InMemoryTaskStore(), agent_card=card)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[Stage 4] QA Agent ready (A2A /a2a, web /, LLM: {llm.label})", flush=True)
        yield

    app = FastAPI(title="Stage 4 - QA Agent", lifespan=lifespan)
    templates = setup_web(app, TEMPLATES)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def view_run() -> dict | None:
        return work.current_run() or db.one("SELECT * FROM test_runs ORDER BY id DESC LIMIT 1")

    def render(request: Request, template: str, active: str, run: dict | None = None, **ctx):
        current = work.current_run()
        cycle = {"id": run["cycle_id"], "mode": run["mode"], "status": "VERIFYING" if current and current["id"] == run["id"] else None} if run else None
        nav = [
            ("업무", [("desk", "/", "검증 작업대", "차례" if current else ""), ("runs", "/runs", "검증 이력", ""), ("issues", "/issues", "이슈", "")]),
            ("기록", [("timeline", "/timeline", "타임라인", ""), ("card", "/.well-known/agent-card.json", "Agent Card", "")]),
            ("연결", [("hub", f"http://localhost:{config.HUB_PORT}", "Hub 웹 (Orchestrator)", "8400"),
                     ("dev", f"http://localhost:{config.DEV_PORT}", "Dev 웹", "8401")]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "llm_label": llm.label, "members": work.members.all(),
            "cycle": cycle, "run": run, "order": current, "errors": [], **ctx,
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
        current = work.current_run()
        return render(request, "desk.html", "desk", run=run, results=results, live=bool(current and run and current["id"] == run["id"]),
                      to_verify=db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ? ORDER BY id", cid, version),
                      found=db.all("SELECT * FROM issues WHERE cycle_id = ? AND version_found = ? ORDER BY id", cid, version),
                      ready=work.readiness(current), severities=["Critical", "Major", "Minor"])

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
                      desc="QA Agent가 기록한 이벤트입니다. 'Hub' 행은 Hub가 A2A Task로 위임하거나 회신한 내용입니다.")

    # ---------- HTTP ----------
    @app.get("/health")
    async def health():
        return {"service": "qa-agent", "status": "ok", "llm": llm.label, "llm_calls": llm.calls}

    @app.get("/api/events/stream", include_in_schema=False)
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/admin/reset")
    async def reset():
        db.reset(keep=("test_cases",))
        work.seed()
        bus.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    # ---------- 실습 API (QA 웹, body.actor = 검증부서원) ----------
    @app.post("/api/work/results/{result_id}/execute")
    async def work_execute(result_id: int, body: dict):
        return await work.execute(result_id, body.get("actor"))

    @app.post("/api/work/results/{result_id}/judge")
    async def work_judge(result_id: int, body: dict):
        await work.judge(result_id, body.get("actor"), body.get("result"))
        return {"ok": True}

    @app.post("/api/work/issues")
    async def work_issue(body: dict):
        return await work.staff_issue(body.get("actor"), body)

    @app.post("/api/work/issues/{key}/verify")
    async def work_verify(key: str, body: dict):
        await work.verify_issue(key, body.get("actor"), body.get("ok") in (True, "true", "1"))
        return {"ok": True}

    @app.post("/api/work/transfer")
    async def work_transfer(body: dict):
        return work.decide(body.get("actor"), "TRANSFERRED")

    @app.post("/api/work/approve")
    async def work_approve(body: dict):
        return work.decide(body.get("actor"), "APPROVED")

    @app.post("/api/work/draft/issue/{result_id}")
    async def draft_issue(result_id: int):
        return await work.draft_issue(result_id)

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )
    return app
