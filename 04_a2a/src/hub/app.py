"""Hub 웹 (:8400) = A2A Orchestrator. 관제 + 업무 위임·라우팅 + 승인함.

데모는 에이전트가 스스로 일하고, 실습은 에이전트 뒤에서 부서원이 일한다. Hub는 그 차이를 모른다 -
똑같이 Task를 위임하고, 스트리밍되는 진행 상황과 Artifact를 받을 뿐이다.
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from common import config
from common.db import Database
from common.events import EventBus
from common.llm import LLMClient
from common.web.setup import setup_web

from .decider import Decider
from .registry import Registry
from .store import SCHEMA
from .workflow import CycleConflict, Workflow

HUB_TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "H", "name": "Hub 웹", "sub": "Stage 4 · A2A Orchestrator", "crumb": "Hub", "system": "Hub :8400"}
STAGES = ["RELEASING", "VERIFYING", "TRANSFERRING", "FIXING", "AWAITING_APPROVAL", "APPROVED"]
TURN = {"RELEASING": "Dev", "FIXING": "Dev", "VERIFYING": "QA", "TRANSFERRING": "Hub", "AWAITING_APPROVAL": "Hub"}


class ApprovalDecision(BaseModel):
    decision: str
    comment: str = ""


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s hub %(name)s: %(message)s")
    db = Database(config.db_path("hub"), SCHEMA)
    bus = EventBus(db)
    llm = LLMClient(config.llm_mode(), config.MODEL)
    registry = Registry(db, config.AGENT_URLS)
    workflow = Workflow(db, bus, registry, Decider(llm), llm.label)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry.start()
        workflow.recover()  # 재시작 전에 진행 중이던 사이클이 있으면 에이전트의 Task에 다시 연결해 이어 간다
        print(f"[Stage 4] Hub ready (LLM: {llm.label})", flush=True)
        yield
        await workflow.detach()  # 종료 시 에이전트의 Task는 취소하지 않는다 (재시작 후 이어 가기)
        await registry.stop()

    app = FastAPI(title="Stage 4 - Hub (A2A Orchestrator)", lifespan=lifespan)
    templates = setup_web(app, HUB_TEMPLATES)

    def resolve_cycle(request: Request) -> tuple[dict | None, str]:
        value = request.query_params.get("cycle")
        if value and value.isdigit():
            return db.one("SELECT * FROM cycles WHERE id = ?", int(value)), f"?cycle={value}"
        return db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1"), ""

    def render(request: Request, template: str, active: str, cycle, cycle_query: str, **extra):
        agents = db.all("SELECT name, url, status FROM agents ORDER BY url")
        errors = [f"{a['name'] or a['url']} 응답 없음" for a in agents if a["status"] != "UP"]
        pending = db.scalar("SELECT COUNT(*) FROM approvals WHERE status = 'PENDING'")
        latest = db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1")
        turn = TURN.get(latest["status"]) if workflow.running and latest and latest["mode"] == "manual" else None
        working = db.one("SELECT * FROM tasks WHERE cycle_id = ? AND state = 'WORKING' ORDER BY id DESC LIMIT 1", latest["id"]) if latest else None
        nav = [
            ("관제", [("overview", "/", "Overview", ""), ("releases", "/releases", "Releases", ""), ("test_runs", "/test-runs", "Test Runs", ""),
                     ("issues", "/issues", "Issues", ""), ("timeline", "/timeline", "Timeline", "")]),
            ("Hub", [("approvals", "/approvals", "Approvals", str(pending) if pending else ""), ("agents", "/agents", "Agents", ""),
                    ("messages", "/messages", "Message Trace", "")]),
            ("부서 웹", [("dev", f"http://localhost:{config.DEV_PORT}", "Dev 웹 · 발행·수정", "차례" if turn == "Dev" else "8401"),
                       ("qa", f"http://localhost:{config.QA_PORT}", "QA 웹 · 검증·이관·확정", "차례" if turn == "QA" else "8402")]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "stages": STAGES, "members": None, "llm_label": llm.label,
            "llm": {"label": llm.label}, "errors": errors, "running": workflow.running, "turn": turn, "working_task": working,
            "dev_web": f"http://localhost:{config.DEV_PORT}", "qa_web": f"http://localhost:{config.QA_PORT}",
            "pending_approvals": pending, "cycle": cycle, "cycle_query": cycle_query, **extra,
        })

    def releases_with_runs(cycle_id: int) -> list[dict]:
        releases = db.all("SELECT * FROM releases WHERE cycle_id = ? ORDER BY id", cycle_id)
        runs = {r["version"]: r for r in db.all("SELECT * FROM test_runs WHERE cycle_id = ? ORDER BY id", cycle_id)}
        for r in releases:
            run = runs.get(r["version"], {})
            r.update(run_id=run.get("id"), passed=run.get("passed"), failed=run.get("failed"), total=run.get("total"))
        return releases

    def issues(cycle_id: int) -> list[dict]:
        return db.all("SELECT * FROM issues WHERE cycle_id = ? ORDER BY id", cycle_id)

    def events(cycle_id: int | None, limit: int = 500) -> list[dict]:
        return db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit) if cycle_id else []

    # ---------- pages ----------
    @app.get("/")
    async def overview(request: Request):
        cycle, q = resolve_cycle(request)
        extra = {"cycles": db.all("SELECT c.*, (SELECT COUNT(*) FROM tasks t WHERE t.cycle_id = c.id) AS tasks,"
                                  " (SELECT COUNT(*) FROM a2a_messages m WHERE m.cycle_id = c.id) AS messages FROM cycles c ORDER BY id DESC LIMIT 8")}
        if cycle:
            data = issues(cycle["id"])
            extra.update(
                releases=releases_with_runs(cycle["id"]), issues=data, events=events(cycle["id"], 12),
                decisions=db.all("SELECT * FROM decisions WHERE cycle_id = ? ORDER BY id", cycle["id"]),
                tasks=db.all("SELECT * FROM tasks WHERE cycle_id = ? ORDER BY id DESC LIMIT 6", cycle["id"]),
                stats={"releases": db.scalar("SELECT COUNT(*) FROM releases WHERE cycle_id = ?", cycle["id"]),
                       "last_run": db.one("SELECT * FROM test_runs WHERE cycle_id = ? ORDER BY id DESC LIMIT 1", cycle["id"]),
                       "total_issues": len(data), "open_issues": sum(1 for i in data if i["status"] != "VERIFIED")},
            )
        return render(request, "overview.html", "overview", cycle, q, **extra)

    @app.get("/releases")
    async def releases(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "releases.html", "releases", cycle, q, releases=releases_with_runs(cycle["id"]) if cycle else [])

    @app.get("/releases/{version}")
    async def release_detail(request: Request, version: str):
        cycle, q = resolve_cycle(request)
        release = next((r for r in releases_with_runs(cycle["id"]) if r["version"] == version), None) if cycle else None
        if not release:
            raise HTTPException(404, "release not found")
        runs = db.all("SELECT * FROM test_runs WHERE cycle_id = ? AND version = ?", cycle["id"], version)
        data = issues(cycle["id"])
        return render(request, "release_detail.html", "releases", cycle, q, release=release, runs=runs,
                      fixed_issues=[i for i in data if i["key"] in release["fixed_issue_keys"]],
                      found_issues=[i for i in data if i["version_found"] == version])

    @app.get("/test-runs")
    async def test_runs(request: Request):
        cycle, q = resolve_cycle(request)
        runs = db.all("SELECT * FROM test_runs WHERE cycle_id = ? ORDER BY id", cycle["id"]) if cycle else []
        return render(request, "test_runs.html", "test_runs", cycle, q, runs=runs)

    @app.get("/test-runs/{run_id}")
    async def test_run_detail(request: Request, run_id: int):
        run = db.one("SELECT * FROM test_runs WHERE id = ?", run_id)
        if not run:
            raise HTTPException(404, "test run not found")
        cycle, q = resolve_cycle(request)
        return render(request, "test_run_detail.html", "test_runs", cycle, q, run=run, results=json.loads(run["results"] or "[]"))

    @app.get("/issues")
    async def issues_page(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "issues.html", "issues", cycle, q, issues=issues(cycle["id"]) if cycle else [])

    @app.get("/issues/{key}")
    async def issue_detail(request: Request, key: str):
        cycle, q = resolve_cycle(request)
        issue = db.one("SELECT * FROM issues WHERE cycle_id = ? AND key = ?", cycle["id"], key) if cycle else None
        if not issue:
            raise HTTPException(404, "issue not found")
        history = db.all("SELECT *, source AS system FROM issue_history WHERE cycle_id = ? AND key = ? ORDER BY id", cycle["id"], key)
        return render(request, "issue_detail.html", "issues", cycle, q, issue=issue, ticket=None, history=history)

    @app.get("/timeline")
    async def timeline(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "timeline.html", "timeline", cycle, q, events=events(cycle["id"] if cycle else None))

    @app.get("/approvals")
    async def approvals(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "approvals.html", "approvals", cycle, q,
                      pending=db.all("SELECT * FROM approvals WHERE status = 'PENDING' ORDER BY id"),
                      done=db.all("SELECT * FROM approvals WHERE status != 'PENDING' ORDER BY id DESC LIMIT 20"),
                      runs={r["version"]: r for r in db.all("SELECT * FROM test_runs WHERE cycle_id = ?", cycle["id"])} if cycle else {})

    @app.get("/agents")
    async def agents(request: Request):
        cycle, q = resolve_cycle(request)
        rows = db.all("SELECT * FROM agents ORDER BY url")
        for row in rows:
            row["card_obj"] = json.loads(row["card"]) if row["card"] else None
        return render(request, "agents.html", "agents", cycle, q, agents=rows)

    @app.get("/messages")
    async def messages(request: Request):
        cycle, q = resolve_cycle(request)
        cid = cycle["id"] if cycle else None
        return render(request, "messages.html", "messages", cycle, q,
                      tasks=db.all("SELECT * FROM tasks WHERE cycle_id = ? ORDER BY id", cid),
                      messages=db.all("SELECT * FROM a2a_messages WHERE cycle_id = ? ORDER BY id DESC", cid))

    # ---------- API ----------
    @app.post("/api/cycles")
    async def start_cycle():
        try:
            return {"cycle_id": workflow.start("demo")}
        except CycleConflict:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다."}, status_code=409)

    @app.post("/api/work/start")
    async def start_manual():
        try:
            return {"cycle_id": workflow.start("manual")}
        except CycleConflict:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다."}, status_code=409)

    @app.post("/api/work/abort")
    async def abort_cycle():
        if not workflow.running:
            return JSONResponse({"error": "진행 중인 사이클이 아닙니다."}, status_code=409)
        await workflow.abort()
        return {"ok": True}

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: int, body: ApprovalDecision):
        if body.decision not in ("approve", "reject"):
            return JSONResponse({"error": "decision은 approve 또는 reject여야 합니다."}, status_code=400)
        if not workflow.decide_approval(approval_id, body.decision == "approve", body.comment.strip()):
            return JSONResponse({"error": "처리할 수 없는 승인 요청입니다 (이미 처리되었거나 만료됨)."}, status_code=409)
        return {"ok": True}

    @app.post("/api/agents/refresh")
    async def refresh_agents():
        await registry.refresh()
        return {"agents": db.all("SELECT name, url, status FROM agents")}

    @app.post("/api/reset")
    async def reset():
        if workflow.running:
            return JSONResponse({"error": "사이클 실행 중에는 초기화할 수 없습니다."}, status_code=409)
        async with httpx.AsyncClient(timeout=5) as client:
            for url in config.AGENT_URLS:
                try:
                    (await client.post(f"{url}/admin/reset")).raise_for_status()
                except httpx.HTTPError as exc:
                    return JSONResponse({"error": f"{url} 초기화 실패: {type(exc).__name__}"}, status_code=503)
        db.reset(keep=("agents",))
        bus.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        return {"running": workflow.running, "cycle": db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1"),
                "pending_approvals": db.all("SELECT id, cycle_id, version, status FROM approvals WHERE status = 'PENDING'"),
                "agents": db.all("SELECT name, url, status FROM agents"),
                "llm": {"mode": llm.mode, "model": llm.model, "calls": llm.calls, "failures": llm.failures}}

    @app.get("/api/cycles/{cycle_id}/summary")
    async def cycle_summary(cycle_id: int):
        cycle = db.one("SELECT * FROM cycles WHERE id = ?", cycle_id)
        if not cycle:
            raise HTTPException(404)
        return {"cycle": cycle,
                "releases": [{"version": r["version"], "passed": r["passed"], "failed": r["failed"], "fixed_issue_keys": r["fixed_issue_keys"]}
                             for r in releases_with_runs(cycle_id)],
                "issues": [{k: i[k] for k in ("key", "test_case_id", "severity", "status", "version_found", "fixed_in_version")} for i in issues(cycle_id)],
                "decisions": db.all("SELECT iteration, action, decided_by, reason FROM decisions WHERE cycle_id = ? ORDER BY id", cycle_id),
                "tasks": db.all("SELECT agent, skill, state FROM tasks WHERE cycle_id = ? ORDER BY id", cycle_id),
                "a2a_messages": db.scalar("SELECT COUNT(*) FROM a2a_messages WHERE cycle_id = ?", cycle_id)}

    @app.get("/api/events/stream")
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app
