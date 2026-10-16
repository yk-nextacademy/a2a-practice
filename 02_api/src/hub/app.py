"""Hub (:8200) - 전체 관제 웹 서비스.

Hub는 업무 흐름에 끼어들지 않는다. Dev와 QA는 REST로 서로 직접 통신하고,
Hub는 두 시스템의 API를 조회해 전체 그림(진행 단계, 차례, 데이터, 연동 기록)을 보여 준다.
사이클 시작·중단·초기화 버튼은 편의를 위해 Dev/QA API를 대신 호출할 뿐이다.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from common import config
from common.web.setup import setup_web

from .upstream import EventRelay, Upstream

TEMPLATES = Path(__file__).resolve().parent / "templates"
APP = {"mark": "H", "name": "Hub 웹", "sub": "Stage 2 · 전체 관제", "crumb": "Hub", "system": "Hub :8200"}
STAGES = ["RELEASING", "VERIFYING", "TRANSFERRING", "FIXING", "APPROVED"]
DEV, QA = config.DEV_URL, config.QA_URL


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    client = httpx.AsyncClient(timeout=10)
    relay = EventRelay()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        relay.start()
        print("[Stage 2] Hub ready", flush=True)
        yield
        await relay.stop()
        await client.aclose()

    app = FastAPI(title="Stage 2 - Hub (관제)", lifespan=lifespan)
    templates = setup_web(app, TEMPLATES)

    async def base_ctx(request: Request, up: Upstream) -> dict:
        state, qa_state = await asyncio.gather(up.get(DEV, "/api/state", default={}), up.get(QA, "/api/state", default={}))
        value = request.query_params.get("cycle")
        cycle = await up.get(DEV, f"/api/cycles/{value}") if value and value.isdigit() else (state or {}).get("cycle")
        running = bool((state or {}).get("running"))
        turn = None
        if running and cycle and cycle.get("mode") == "manual":
            turn = "QA" if cycle["status"] in ("VERIFYING", "TRANSFERRING") else "Dev"
        llm = (state or {}).get("llm") or {}
        return {"cycle": cycle, "running": running, "turn": turn, "qa_state": qa_state or {},
                "llm_label": llm.get("model") if llm.get("mode") == "llm" else "mock",
                "cycle_query": f"?cycle={cycle['id']}" if cycle and value else ""}

    def render(request: Request, template: str, active: str, up: Upstream, ctx: dict, **extra):
        nav = [
            ("관제", [("overview", "/", "Overview", ""), ("releases", "/releases", "Releases", ""), ("test_runs", "/test-runs", "Test Runs", ""),
                     ("issues", "/issues", "Issues", ""), ("timeline", "/timeline", "Timeline", "")]),
            ("시스템", [("integration", "/integration", "Integration", "")]),
            ("부서 웹", [("dev", f"http://localhost:{config.DEV_PORT}", "Dev 웹 · 발행·수정", "차례" if ctx.get("turn") == "Dev" else "8201"),
                       ("qa", f"http://localhost:{config.QA_PORT}", "QA 웹 · 검증·이관·확정", "차례" if ctx.get("turn") == "QA" else "8202")]),
        ]
        return templates.TemplateResponse(request, template, {
            "app": APP, "nav_groups": nav, "active": active, "stages": STAGES, "members": None, "errors": up.errors,
            "dev_url": DEV, "qa_url": QA, "dev_web": f"http://localhost:{config.DEV_PORT}", "qa_web": f"http://localhost:{config.QA_PORT}",
            **ctx, **extra,
        })

    # ---------- 조합 헬퍼 ----------
    async def releases_with_runs(up: Upstream, cycle_id: int) -> list[dict]:
        releases, runs = await asyncio.gather(up.get(DEV, "/api/releases", [], cycle_id=cycle_id),
                                              up.get(QA, "/api/test-runs", [], cycle_id=cycle_id))
        by_version = {r["version"]: r for r in runs}
        for r in releases:
            run = by_version.get(r["version"], {})
            r.update(run_id=run.get("id"), passed=run.get("passed"), failed=run.get("failed"), total=run.get("total"),
                     run_status=run.get("status"), decision=run.get("decision"))
        return releases

    async def issues_with_tickets(up: Upstream, cycle_id: int) -> list[dict]:
        issues, tickets = await asyncio.gather(up.get(QA, "/api/issues", [], cycle_id=cycle_id),
                                               up.get(DEV, "/api/tickets", [], cycle_id=cycle_id))
        by_key = {t["key"]: t for t in tickets}
        for i in issues:
            t = by_key.get(i["key"], {})
            i.update(dev_status=t.get("status"), fix_note=t.get("fix_note"), assignee=t.get("assignee"))
        return issues

    async def merged_events(up: Upstream, cycle_id: int, limit: int = 500) -> list[dict]:
        dev_events, qa_events = await asyncio.gather(up.get(DEV, "/api/events", [], cycle_id=cycle_id, limit=limit),
                                                     up.get(QA, "/api/events", [], cycle_id=cycle_id, limit=limit))
        events = [{**e, "source": "Dev"} for e in dev_events] + [{**e, "source": "QA"} for e in qa_events]
        return sorted(events, key=lambda e: (e["ts"], e["source"] == "QA", e["id"]), reverse=True)[:limit]

    # ---------- 화면 ----------
    @app.get("/")
    async def overview(request: Request):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        cycle = ctx["cycle"]
        extra = {"cycles": (await up.get(DEV, "/api/cycles", []))[:8]}
        if cycle:
            releases, issues, events, runs = await asyncio.gather(
                releases_with_runs(up, cycle["id"]), issues_with_tickets(up, cycle["id"]),
                merged_events(up, cycle["id"], 12), up.get(QA, "/api/test-runs", [], cycle_id=cycle["id"]))
            extra.update(releases=releases, issues=issues, events=events, stats={
                "releases": len(releases), "last_run": runs[-1] if runs else None, "total_issues": len(issues),
                "open_issues": sum(1 for i in issues if i["status"] != "VERIFIED"),
            })
        return render(request, "overview.html", "overview", up, ctx, **extra)

    @app.get("/releases")
    async def releases(request: Request):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        data = await releases_with_runs(up, ctx["cycle"]["id"]) if ctx["cycle"] else []
        return render(request, "releases.html", "releases", up, ctx, releases=data)

    @app.get("/releases/{version}")
    async def release_detail(request: Request, version: str):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        cycle = ctx["cycle"]
        release = await up.get(DEV, f"/api/releases/{version}", cycle_id=cycle["id"]) if cycle else None
        if not release:
            raise HTTPException(404, "release not found")
        runs = [r for r in await up.get(QA, "/api/test-runs", [], cycle_id=cycle["id"]) if r["version"] == version]
        issues = await issues_with_tickets(up, cycle["id"])
        return render(request, "release_detail.html", "releases", up, ctx, release=release, runs=runs,
                      fixed_issues=[i for i in issues if i["key"] in release["fixed_issue_keys"]],
                      found_issues=[i for i in issues if i["version_found"] == version])

    @app.get("/test-runs")
    async def test_runs(request: Request):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        runs = await up.get(QA, "/api/test-runs", [], cycle_id=ctx["cycle"]["id"]) if ctx["cycle"] else []
        return render(request, "test_runs.html", "test_runs", up, ctx, runs=runs)

    @app.get("/test-runs/{run_id}")
    async def test_run_detail(request: Request, run_id: int):
        up = Upstream(client)
        run = await up.get(QA, f"/api/test-runs/{run_id}")
        if not run:
            raise HTTPException(404, "test run not found")
        ctx = await base_ctx(request, up)
        return render(request, "test_run_detail.html", "test_runs", up, ctx, run=run, results=run["results"])

    @app.get("/issues")
    async def issues(request: Request):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        data = await issues_with_tickets(up, ctx["cycle"]["id"]) if ctx["cycle"] else []
        return render(request, "issues.html", "issues", up, ctx, issues=data)

    @app.get("/issues/{key}")
    async def issue_detail(request: Request, key: str):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        cycle = ctx["cycle"]
        issue = await up.get(QA, f"/api/issues/{key}", cycle_id=cycle["id"]) if cycle else None
        if not issue:
            raise HTTPException(404, "issue not found")
        ticket = await up.get(DEV, f"/api/tickets/{key}", cycle_id=cycle["id"])
        history = [{**h, "system": "QA Server"} for h in issue["history"]]
        history += [{**h, "system": "Dev Server"} for h in (ticket or {}).get("history", [])]
        history.sort(key=lambda h: (h["ts"], h["system"] == "Dev Server"))
        issue["fix_note"] = (ticket or {}).get("fix_note")
        return render(request, "issue_detail.html", "issues", up, ctx, issue=issue, ticket=ticket, history=history)

    @app.get("/timeline")
    async def timeline(request: Request):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        events = await merged_events(up, ctx["cycle"]["id"]) if ctx["cycle"] else []
        return render(request, "timeline.html", "timeline", up, ctx, events=events)

    @app.get("/integration")
    async def integration(request: Request):
        up = Upstream(client)
        ctx = await base_ctx(request, up)
        cycle_id = ctx["cycle"]["id"] if ctx["cycle"] else None
        dev_health, qa_health, dev_logs, qa_logs = await asyncio.gather(
            up.health(DEV), up.health(QA),
            up.get(DEV, "/api/integration-logs", [], cycle_id=cycle_id), up.get(QA, "/api/integration-logs", [], cycle_id=cycle_id))
        logs = [{**l, "service": "Dev"} for l in dev_logs] + [{**l, "service": "QA"} for l in qa_logs]
        logs.sort(key=lambda l: l["ts"], reverse=True)
        outbound = [l for l in logs if l["direction"] == "out"]
        return render(request, "integration.html", "integration", up, ctx, logs=logs, health={"Dev": dev_health, "QA": qa_health},
                      summary={"calls": len(outbound), "errors": sum(1 for l in outbound if l["error"]),
                               "avg_ms": round(sum(l["duration_ms"] or 0 for l in outbound) / len(outbound)) if outbound else 0})

    # ---------- 편의 조작 (Dev/QA API 대리 호출) ----------
    @app.post("/api/cycles")
    async def start_demo():
        status, body = await Upstream(client).post(DEV, "/api/cycles?mode=demo")
        return JSONResponse(body, status_code=status)

    @app.post("/api/work/start")
    async def start_manual():
        status, body = await Upstream(client).post(DEV, "/api/cycles?mode=manual")
        return JSONResponse(body, status_code=status)

    @app.post("/api/work/abort")
    async def abort():
        up = Upstream(client)
        state = await up.get(DEV, "/api/state", default={})
        cycle = (state or {}).get("cycle")
        if not cycle or not state.get("running"):
            return JSONResponse({"error": "진행 중인 사이클이 없습니다."}, status_code=409)
        status, body = await up.post(DEV, f"/api/cycles/{cycle['id']}/abort")
        return JSONResponse(body, status_code=status)

    @app.post("/api/reset")
    async def reset():
        up = Upstream(client)
        # 1단계: 양쪽이 모두 초기화할 수 있는지 먼저 확인한다 (한쪽만 초기화되는 불일치 방지)
        for base in (DEV, QA):
            status, body = await up.post(base, "/api/admin/reset?dry_run=true")
            if status >= 400:
                return JSONResponse(body, status_code=status)
        # 2단계: 실제 초기화
        for base in (DEV, QA):
            status, body = await up.post(base, "/api/admin/reset")
            if status >= 400:
                return JSONResponse(body, status_code=status)
        relay.publish({"type": "system.reset", "payload": {}})
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        up = Upstream(client)
        dev_state, qa_state = await asyncio.gather(up.get(DEV, "/api/state", default={}), up.get(QA, "/api/state", default={}))
        return {**(dev_state or {}), "qa": qa_state}

    @app.get("/api/cycles/{cycle_id}/summary")
    async def cycle_summary(cycle_id: int):
        up = Upstream(client)
        cycle = await up.get(DEV, f"/api/cycles/{cycle_id}")
        if not cycle:
            raise HTTPException(404)
        releases = await releases_with_runs(up, cycle_id)
        issues = await issues_with_tickets(up, cycle_id)
        return {"cycle": cycle,
                "releases": [{"version": r["version"], "passed": r["passed"], "failed": r["failed"], "fixed_issue_keys": r["fixed_issue_keys"],
                              "decision": r["decision"]} for r in releases],
                "issues": [{k: i.get(k) for k in ("key", "test_case_id", "severity", "status", "dev_status", "version_found", "fixed_in_version")}
                           for i in issues]}

    @app.get("/api/events/stream")
    async def event_stream():
        return StreamingResponse(relay.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app
