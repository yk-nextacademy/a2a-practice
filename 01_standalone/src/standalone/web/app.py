"""FastAPI 애플리케이션: 대시보드 화면 + JSON API + SSE."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import load_settings
from ..infra.db import Database
from ..infra.events import EventBus
from ..infra.llm import LLMClient
from ..services.cycle import STAGES, CycleAlreadyRunning, CycleRunner
from ..services.dev_service import DevService
from ..services.qa_service import ACTIVE_STATUSES, QAService
from ..services.workbench import ACTIVE_CYCLE, Workbench, WorkError
from .queries import Queries
from .ui import register_filters

WEB_DIR = Path(__file__).resolve().parent
STAGE = {"no": 1, "name": "Standalone", "desc": "단일 웹 서비스 · 함수 직접 호출"}
NAV = [
    ("overview", "/", "Overview"),
    ("releases", "/releases", "Releases"),
    ("test_runs", "/test-runs", "Test Runs"),
    ("issues", "/issues", "Issues"),
    ("timeline", "/timeline", "Timeline"),
]
NAV_WORK = [("qa", "/qa", "검증 작업대", "QA"), ("dev", "/dev", "개발 작업대", "Dev")]
TURN = {"RELEASING": "Dev", "FIXING": "Dev", "VERIFYING": "QA", "TRANSFERRING": "QA"}


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    db = Database(settings.db_path)
    bus = EventBus(db)
    llm = LLMClient(settings.llm_mode, settings.model)
    dev = DevService(db, bus, llm, settings.step_delay)
    qa = QAService(db, bus, llm, settings.step_delay, settings.test_delay)
    qa.seed_test_cases()
    runner = CycleRunner(db, bus, dev, qa, llm.label, settings.step_delay)
    wb = Workbench(db, bus, dev, qa)
    wb.seed()
    queries = Queries(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[Stage 1] Standalone ready (LLM: {llm.label})", flush=True)
        yield
        await runner.stop()

    app = FastAPI(title="Stage 1 - Standalone", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    register_filters(templates.env)

    @app.exception_handler(WorkError)
    async def work_error(request: Request, exc: WorkError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def render(request: Request, template: str, active: str, **ctx):
        cycle = ctx.get("cycle")
        manual = wb.active_cycle()
        return templates.TemplateResponse(request, template, {
            "stage": STAGE, "nav": NAV, "nav_work": NAV_WORK, "active": active, "llm": llm, "stages": STAGES,
            "running": runner.running, "manual": manual, "turn": TURN.get(manual["status"]) if manual else None,
            "members": wb.members(),
            "cycle_query": f"?cycle={cycle['id']}" if cycle and request.query_params.get("cycle") else "",
            **ctx,
        })

    def work_cycle() -> dict | None:
        """작업대에 표시할 사이클: 진행 중인 실습 사이클, 없으면 가장 최근 실습 사이클."""
        return wb.active_cycle() or db.one("SELECT * FROM cycles WHERE mode = 'manual' ORDER BY id DESC LIMIT 1")

    def resolve_cycle(request: Request) -> dict | None:
        value = request.query_params.get("cycle")
        return queries.cycle(int(value) if value and value.isdigit() else None)

    # ---------- pages ----------
    @app.get("/")
    async def overview(request: Request):
        cycle = resolve_cycle(request)
        ctx = {"cycle": cycle, "cycles": queries.cycles()[:8]}
        if cycle:
            ctx.update(stats=queries.stats(cycle["id"]), releases=queries.releases(cycle["id"]),
                       issues=queries.issues(cycle["id"]), events=queries.events(cycle["id"], 12))
        return render(request, "overview.html", "overview", **ctx)

    @app.get("/releases")
    async def releases(request: Request):
        cycle = resolve_cycle(request)
        return render(request, "releases.html", "releases", cycle=cycle, releases=queries.releases(cycle["id"]) if cycle else [])

    @app.get("/releases/{version}")
    async def release_detail(request: Request, version: str):
        cycle = resolve_cycle(request)
        release = queries.release(cycle["id"], version) if cycle else None
        if not release:
            raise HTTPException(404, "release not found")
        runs = [r for r in queries.test_runs(cycle["id"]) if r["version"] == version]
        return render(request, "release_detail.html", "releases", cycle=cycle, release=release, runs=runs,
                      fixed_issues=queries.issues(cycle["id"], release["fixed_issue_keys"]),
                      found_issues=[i for i in queries.issues(cycle["id"]) if i["version_found"] == version])

    @app.get("/test-runs")
    async def test_runs(request: Request):
        cycle = resolve_cycle(request)
        return render(request, "test_runs.html", "test_runs", cycle=cycle, runs=queries.test_runs(cycle["id"]) if cycle else [])

    @app.get("/test-runs/{run_id}")
    async def test_run_detail(request: Request, run_id: int):
        run = queries.test_run(run_id)
        if not run:
            raise HTTPException(404, "test run not found")
        cycle = queries.cycle(run["cycle_id"])
        return render(request, "test_run_detail.html", "test_runs", cycle=cycle, run=run, results=queries.test_results(run_id))

    @app.get("/issues")
    async def issues(request: Request):
        cycle = resolve_cycle(request)
        return render(request, "issues.html", "issues", cycle=cycle, issues=queries.issues(cycle["id"]) if cycle else [])

    @app.get("/issues/{key}")
    async def issue_detail(request: Request, key: str):
        cycle = resolve_cycle(request)
        issue = queries.issue(cycle["id"], key) if cycle else None
        if not issue:
            raise HTTPException(404, "issue not found")
        return render(request, "issue_detail.html", "issues", cycle=cycle, issue=issue, history=queries.issue_history(issue["id"]))

    @app.get("/timeline")
    async def timeline(request: Request):
        cycle = resolve_cycle(request)
        return render(request, "timeline.html", "timeline", cycle=cycle, events=queries.events(cycle["id"] if cycle else None))

    @app.get("/qa")
    async def qa_desk(request: Request):
        cycle = work_cycle()
        run = wb.current_run(cycle) if cycle else None
        # issue_key: FAIL 등록 시 서버(create_issue)가 연결할 미해결 이슈와 같은 기준(ACTIVE_STATUSES)
        # fixed_key: 이 버전에서 수정 확인을 기다리는 FIXED 이슈 (연결 대상 아님, '재현'으로 처리)
        results = db.all(
            "SELECT r.*, c.name, c.feature, (SELECT key FROM issues i WHERE i.cycle_id = ? AND i.test_case_id = r.test_case_id"
            f" AND i.status IN {ACTIVE_STATUSES} ORDER BY i.id DESC LIMIT 1) AS issue_key,"
            " (SELECT key FROM issues i WHERE i.cycle_id = ? AND i.test_case_id = r.test_case_id"
            " AND i.status = 'FIXED' ORDER BY i.id DESC LIMIT 1) AS fixed_key"
            " FROM test_results r JOIN test_cases c ON c.id = r.test_case_id WHERE r.run_id = ? ORDER BY r.id",
            cycle["id"] if cycle else 0, cycle["id"] if cycle else 0, run["id"] if run else 0)
        version = cycle["current_version"] if cycle else None
        return render(request, "qa.html", "qa", cycle=cycle, run=run, results=results,
                      to_verify=db.all("SELECT * FROM issues WHERE cycle_id = ? AND status = 'FIXED' AND fixed_in_version = ? ORDER BY id",
                                       cycle["id"], version) if cycle else [],
                      found=db.all("SELECT * FROM issues WHERE cycle_id = ? AND version_found = ? ORDER BY id", cycle["id"], version) if cycle else [],
                      ready=wb.readiness(cycle) if cycle else None, severities=["Critical", "Major", "Minor"])

    @app.get("/dev")
    async def dev_desk(request: Request):
        cycle = work_cycle()
        work = db.all("SELECT * FROM issues WHERE cycle_id = ? AND (status IN ('TRANSFERRED', 'IN_PROGRESS')"
                      " OR (status = 'FIXED' AND fixed_in_version IS NULL)) ORDER BY id", cycle["id"]) if cycle else []
        return render(request, "dev.html", "dev", cycle=cycle, work=work,
                      shippable=wb.shippable(cycle["id"]) if cycle else [],
                      next_version=wb.next_version(cycle["id"]) if cycle else None,
                      has_release=bool(db.scalar("SELECT COUNT(*) FROM releases WHERE cycle_id = ?", cycle["id"])) if cycle else False,
                      releases=queries.releases(cycle["id"]) if cycle else [])

    # ---------- API ----------
    @app.post("/api/cycles")
    async def start_cycle():
        try:
            cycle_id = runner.start()
        except CycleAlreadyRunning:
            return JSONResponse({"error": "이미 진행 중인 사이클이 있습니다. (실습 중이면 실습을 끝내거나 중단하세요)"}, status_code=409)
        return {"cycle_id": cycle_id}

    @app.post("/api/reset")
    async def reset():
        # 데모 실행 중이거나 진행 중인 사이클(데모·실습)이 있으면 초기화하지 않는다 (진행 중인 실습 기록 보호)
        if runner.running or db.scalar(f"SELECT COUNT(*) FROM cycles WHERE status IN {ACTIVE_CYCLE}"):
            return JSONResponse({"error": "진행 중인 사이클이 있어 초기화할 수 없습니다. (실습 중이면 실습을 끝내거나 중단하세요)"},
                                status_code=409)
        db.reset()
        qa.seed_test_cases()
        wb.seed()
        bus.publish({"type": "system.reset", "actor": "System", "payload": {}})
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        return {"running": runner.running, "cycle": queries.cycle(None), "manual": wb.active_cycle(),
                "llm": {"mode": llm.mode, "model": llm.model, "calls": llm.calls, "failures": llm.failures, "last_error": llm.last_error}}

    # ---------- 실습 모드 API (body.actor = 작업자 이름) ----------
    # 실습 시작·중단은 데모 실행·초기화와 같은 '릴리즈 매니저(관리자)' 조작이라 작업자 검사를 하지 않는다.
    # (인증이 없는 학습용 범위. 상단바 버튼으로 누구나 누를 수 있고, 중단은 확인 대화상자를 거친다)
    @app.post("/api/work/start")
    async def work_start():
        return {"cycle_id": wb.start(llm.label, runner.running)}

    @app.post("/api/work/abort")
    async def work_abort():
        wb.abort()
        return {"ok": True}

    @app.post("/api/work/release")
    async def work_release(body: dict):
        return {"version": await wb.release(body.get("actor"), body.get("release_note", ""))}

    @app.post("/api/work/results/{result_id}/execute")
    async def work_execute(result_id: int, body: dict):
        return wb.execute(result_id, body.get("actor"))

    @app.post("/api/work/results/{result_id}/judge")
    async def work_judge(result_id: int, body: dict):
        wb.judge(result_id, body.get("actor"), body.get("result"))
        return {"ok": True}

    @app.post("/api/work/issues")
    async def work_issue(body: dict):
        return wb.create_issue(body.get("actor"), body)

    @app.post("/api/work/issues/{key}/verify")
    async def work_verify(key: str, body: dict):
        wb.verify_issue(key, body.get("actor"), body.get("ok") in (True, "true", "1"))
        return {"ok": True}

    @app.post("/api/work/transfer")
    async def work_transfer(body: dict):
        return {"transferred": wb.transfer(body.get("actor"))}

    @app.post("/api/work/approve")
    async def work_approve(body: dict):
        wb.approve(body.get("actor"))
        return {"ok": True}

    @app.post("/api/work/issues/{key}/start")
    async def work_start_fix(key: str, body: dict):
        wb.start_fix(key, body.get("actor"))
        return {"ok": True}

    @app.post("/api/work/issues/{key}/fix")
    async def work_fix(key: str, body: dict):
        wb.fix(key, body.get("actor"), body.get("fix_note", ""))
        return {"ok": True}

    # AI 초안도 작업의 일부이므로 해당 부서 작업자만 요청할 수 있다 (LLM 호출 비용 보호)
    @app.post("/api/work/draft/issue/{result_id}")
    async def draft_issue(result_id: int, body: dict):
        return await wb.draft_issue(result_id, body.get("actor"))

    @app.post("/api/work/draft/fix/{key}")
    async def draft_fix(key: str, body: dict):
        return await wb.draft_fix(key, body.get("actor"))

    @app.post("/api/work/draft/release")
    async def draft_release(body: dict):
        return await wb.draft_release(body.get("actor"))

    @app.get("/api/cycles/{cycle_id}/summary")
    async def cycle_summary(cycle_id: int):
        cycle = queries.cycle(cycle_id)
        if not cycle:
            raise HTTPException(404)
        return {"cycle": cycle, "releases": [
            {"version": r["version"], "passed": r["passed"], "failed": r["failed"], "fixed_issue_keys": r["fixed_issue_keys"]}
            for r in queries.releases(cycle_id)
        ], "issues": [{k: i[k] for k in ("key", "test_case_id", "severity", "status", "version_found", "fixed_in_version")}
                      for i in queries.issues(cycle_id)]}

    @app.get("/api/events/stream")
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app
