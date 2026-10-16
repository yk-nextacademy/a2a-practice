"""Hub 웹 (:8300) = MCP Host. 관제 + AI 에이전트.

업무 데이터는 MCP resources로 읽고, 에이전트 기록은 host.db에서 읽는다.
데모는 AI가 모든 일을 도구로 직접 하고, 실습은 AI가 두 부서 시스템 사이의 결과 전달(조정)만 맡는다.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from common import config
from common.db import Database
from common.events import EventBus
from common.web.setup import setup_web

from .agent import HOST_ONLY_TOOLS, POLL_SECONDS, SCHEMA, AgentRunner, CycleConflict
from .mcp_hub import McpHub
from .relay import EventRelay, department_events

HOST_TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "H", "name": "Hub 웹", "sub": "Stage 3 · MCP Host (관제 + AI)", "crumb": "Hub", "system": f"MCP Host :{config.HOST_PORT}"}
STAGES = ["RELEASING", "VERIFYING", "TRANSFERRING", "FIXING", "APPROVED"]


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s host %(name)s: %(message)s")
    db = Database(config.db_path("host"), SCHEMA)
    bus = EventBus(db)
    hub = McpHub(config.MCP_SERVERS)
    runner = AgentRunner(db, bus, hub, config.llm_mode(), config.MODEL)
    relay = EventRelay()
    http = httpx.AsyncClient(timeout=10)
    # Host 자신의 이벤트도 관제 스트림으로 보낸다
    _publish = bus.publish
    bus.publish = lambda event: (_publish(event), relay.publish({**event, "source": "Host"}))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.start()
        relay.start()
        runner.resume()
        print(f"[Stage 3] Hub (MCP Host) ready (agent: {runner.label})", flush=True)
        yield
        await runner.stop()
        await relay.stop()
        await hub.aclose()
        await http.aclose()

    app = FastAPI(title="Stage 3 - Hub (MCP Host)", lifespan=lifespan)
    templates = setup_web(app, HOST_TEMPLATES)

    def errors() -> list[str]:
        return [f"{c.name.upper()} MCP 서버 연결 안 됨" for c in hub.connections.values() if not c.connected]

    def resolve_cycle(request: Request) -> tuple[dict | None, str]:
        value = request.query_params.get("cycle")
        if value and value.isdigit():
            return db.one("SELECT * FROM cycles WHERE id = ?", int(value)), f"?cycle={value}"
        return db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1"), ""

    def turn_of(cycle: dict | None) -> str | None:
        if not cycle or cycle.get("mode") != "manual" or cycle["status"] not in ("RELEASING", "VERIFYING", "TRANSFERRING", "FIXING"):
            return None
        return "QA" if cycle["status"] == "VERIFYING" else "Dev"

    def render(request: Request, template: str, active: str, cycle, cycle_query: str, **extra):
        active_cycle = runner.active_cycle()
        turn = turn_of(active_cycle)
        nav = [
            ("관제", [("overview", "/", "Overview", ""), ("releases", "/releases", "Releases", ""), ("test_runs", "/test-runs", "Test Runs", ""),
                     ("issues", "/issues", "Issues", ""), ("timeline", "/timeline", "Timeline", "")]),
            ("Agent", [("trace", "/trace", "Agent Trace", ""), ("tools", "/tools", "MCP Tools", "")]),
            ("부서 웹", [("dev", f"http://localhost:{config.DEV_PORT}", "Dev 웹 · 발행·수정", "차례" if turn == "Dev" else str(config.DEV_PORT)),
                       ("qa", f"http://localhost:{config.QA_PORT}", "QA 웹 · 검증·이관·확정", "차례" if turn == "QA" else str(config.QA_PORT))]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "stages": STAGES, "members": None, "llm_label": runner.label,
            "llm": {"label": runner.label}, "errors": errors(), "running": runner.running or bool(active_cycle), "turn": turn,
            "dev_web": f"http://localhost:{config.DEV_PORT}", "qa_web": f"http://localhost:{config.QA_PORT}",
            "dev_port": config.DEV_PORT, "qa_port": config.QA_PORT, "poll_seconds": f"{POLL_SECONDS:g}",
            "cycle": cycle, "cycle_query": cycle_query, **extra,
        })

    # ---------- 조합 (MCP resources) ----------
    async def releases_with_runs(cycle_id: int) -> list[dict]:
        releases, runs = await asyncio.gather(hub.read_json("dev", f"dev://cycles/{cycle_id}/releases", []),
                                              hub.read_json("qa", f"qa://cycles/{cycle_id}/test-runs", []))
        by_version = {r["version"]: r for r in runs}
        for r in releases:
            run = by_version.get(r["version"], {})
            r.update(run_id=run.get("id"), passed=run.get("passed"), failed=run.get("failed"), total=run.get("total"))
        return releases

    async def issues_with_tickets(cycle_id: int) -> list[dict]:
        issues, tickets = await asyncio.gather(hub.read_json("qa", f"qa://cycles/{cycle_id}/issues", []),
                                               hub.read_json("dev", f"dev://cycles/{cycle_id}/tickets", []))
        by_key = {t["key"]: t for t in tickets}
        for i in issues:
            t = by_key.get(i["key"], {})
            i.update(dev_status=t.get("status"), fix_note=t.get("fix_note"), ticket=t or None)
        return issues

    async def events(cycle_id: int | None, limit: int = 500) -> list[dict]:
        """Host 이벤트 + Dev/QA 시스템 이벤트(부서원 작업)를 시간순으로 병합한다."""
        if cycle_id is None:
            return []
        own = [{**e, "source": "Host"} for e in db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit)]
        merged = own + await department_events(http, cycle_id, limit)
        return sorted(merged, key=lambda e: (e["ts"], e["id"]), reverse=True)[:limit]

    # ---------- pages ----------
    @app.get("/")
    async def overview(request: Request):
        cycle, q = resolve_cycle(request)
        extra = {"cycles": db.all("SELECT * FROM cycles ORDER BY id DESC LIMIT 8")}
        if cycle:
            releases, issues, runs, qa_status, evs = await asyncio.gather(
                releases_with_runs(cycle["id"]), issues_with_tickets(cycle["id"]),
                hub.read_json("qa", f"qa://cycles/{cycle['id']}/test-runs", []), hub.read_json("qa", "qa://status", {}), events(cycle["id"], 12))
            extra.update(releases=releases, issues=issues, events=evs, qa_status=qa_status or {}, stats={
                "releases": len(releases), "last_run": runs[-1] if runs else None, "total_issues": len(issues),
                "open_issues": sum(1 for i in issues if i["status"] != "VERIFIED"),
            })
        return render(request, "overview.html", "overview", cycle, q, **extra)

    @app.get("/releases")
    async def releases(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "releases.html", "releases", cycle, q, releases=await releases_with_runs(cycle["id"]) if cycle else [])

    @app.get("/releases/{version}")
    async def release_detail(request: Request, version: str):
        cycle, q = resolve_cycle(request)
        data = await releases_with_runs(cycle["id"]) if cycle else []
        release = next((r for r in data if r["version"] == version), None)
        if not release:
            raise HTTPException(404, "release not found")
        runs = [r for r in await hub.read_json("qa", f"qa://cycles/{cycle['id']}/test-runs", []) if r["version"] == version]
        issues = await issues_with_tickets(cycle["id"])
        return render(request, "release_detail.html", "releases", cycle, q, release=release, runs=runs,
                      fixed_issues=[i for i in issues if i["key"] in release["fixed_issue_keys"]],
                      found_issues=[i for i in issues if i["version_found"] == version])

    @app.get("/test-runs")
    async def test_runs(request: Request):
        cycle, q = resolve_cycle(request)
        runs = await hub.read_json("qa", f"qa://cycles/{cycle['id']}/test-runs", []) if cycle else []
        return render(request, "test_runs.html", "test_runs", cycle, q, runs=runs)

    @app.get("/test-runs/{run_id}")
    async def test_run_detail(request: Request, run_id: int):
        run = await hub.read_json("qa", f"qa://test-runs/{run_id}")
        if not run:
            raise HTTPException(404, "test run not found")
        cycle, q = resolve_cycle(request)
        return render(request, "test_run_detail.html", "test_runs", cycle, q, run=run, results=run["results"])

    @app.get("/issues")
    async def issues(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "issues.html", "issues", cycle, q, issues=await issues_with_tickets(cycle["id"]) if cycle else [])

    @app.get("/issues/{key}")
    async def issue_detail(request: Request, key: str):
        cycle, q = resolve_cycle(request)
        issue = next((i for i in await issues_with_tickets(cycle["id"]) if i["key"] == key), None) if cycle else None
        if not issue:
            raise HTTPException(404, "issue not found")
        ticket = issue["ticket"]
        history = [{**h, "system": "QA MCP"} for h in issue["history"]]
        history += [{**h, "system": "Dev MCP"} for h in (ticket or {}).get("history", [])]
        history.sort(key=lambda h: (h["ts"], h["system"] == "Dev MCP"))
        return render(request, "issue_detail.html", "issues", cycle, q, issue=issue, ticket=ticket, history=history)

    @app.get("/timeline")
    async def timeline(request: Request):
        cycle, q = resolve_cycle(request)
        return render(request, "timeline.html", "timeline", cycle, q, events=await events(cycle["id"] if cycle else None),
                      desc="Host(AI)의 도구 호출과 Dev·QA 시스템에서 부서원이 한 작업을 시간순으로 합쳐 보여 줍니다.")

    @app.get("/trace")
    async def trace(request: Request):
        cycle, q = resolve_cycle(request)
        steps = db.all("SELECT * FROM agent_steps WHERE cycle_id = ? ORDER BY id", cycle["id"]) if cycle else []
        return render(request, "trace.html", "trace", cycle, q, steps=steps)

    @app.get("/tools")
    async def tools(request: Request):
        cycle, q = resolve_cycle(request)
        servers = [{"name": c.name, "url": c.url, "connected": c.connected, "error": c.last_error,
                    "info": c.server_info, "tools": c.tools} for c in hub.connections.values()]
        return render(request, "tools.html", "tools", cycle, q, servers=servers, anthropic_tools=hub.anthropic_tools(), host_only=HOST_ONLY_TOOLS)

    # ---------- API ----------
    @app.post("/api/cycles")
    async def start_cycle():
        try:
            return {"cycle_id": runner.start("demo")}
        except CycleConflict:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다."}, status_code=409)

    @app.post("/api/work/start")
    async def start_manual():
        try:
            return {"cycle_id": runner.start("manual")}
        except CycleConflict:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다."}, status_code=409)

    @app.post("/api/work/abort")
    async def abort_cycle():
        if not runner.running:
            return JSONResponse({"error": "진행 중인 사이클이 아닙니다."}, status_code=409)
        await runner.abort()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset():
        if runner.running:
            return JSONResponse({"error": "사이클 실행 중에는 초기화할 수 없습니다."}, status_code=409)
        async with httpx.AsyncClient(timeout=5) as client:
            for base in (config.DEV_URL, config.QA_URL):
                try:
                    (await client.post(f"{base}/admin/reset")).raise_for_status()
                except httpx.HTTPError as exc:
                    return JSONResponse({"error": f"{base} 초기화 실패: {type(exc).__name__}"}, status_code=503)
        db.reset()
        bus.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        cycle = db.one("SELECT * FROM cycles ORDER BY id DESC LIMIT 1")
        return {"running": runner.running, "cycle": cycle, "turn": turn_of(cycle),
                "agent": runner.label, "mcp": {c.name: c.connected for c in hub.connections.values()}}

    @app.get("/api/cycles/{cycle_id}/summary")
    async def cycle_summary(cycle_id: int):
        cycle = db.one("SELECT * FROM cycles WHERE id = ?", cycle_id)
        if not cycle:
            raise HTTPException(404)
        releases = await releases_with_runs(cycle_id)
        issues = await issues_with_tickets(cycle_id)
        return {"cycle": cycle,
                "releases": [{"version": r["version"], "passed": r["passed"], "failed": r["failed"], "fixed_issue_keys": r["fixed_issue_keys"]} for r in releases],
                "issues": [{k: i.get(k) for k in ("key", "test_case_id", "severity", "status", "dev_status", "version_found", "fixed_in_version")} for i in issues]}

    @app.get("/api/events/stream")
    async def event_stream():
        return StreamingResponse(relay.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app
