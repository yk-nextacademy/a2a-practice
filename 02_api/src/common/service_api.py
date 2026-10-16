"""Dev/QA 서버가 공통으로 제공하는 운영 API (health, events, integration-logs)."""

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from .db import Database
from .events import EventBus


def add_common_routes(app: FastAPI, service: str, db: Database, bus: EventBus, llm_label: str) -> None:
    @app.get("/health", tags=["ops"])
    async def health():
        return {"service": service, "status": "ok", "llm": llm_label}

    @app.get("/api/events", tags=["ops"])
    async def events(cycle_id: int | None = None, limit: int = 500):
        if cycle_id is None:
            return db.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", limit)
        return db.all("SELECT * FROM events WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit)

    @app.get("/api/events/stream", tags=["ops"])
    async def event_stream():
        return StreamingResponse(bus.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/integration-logs", tags=["ops"])
    async def integration_logs(cycle_id: int | None = None, limit: int = 300):
        if cycle_id is None:
            return db.all("SELECT * FROM integration_logs ORDER BY id DESC LIMIT ?", limit)
        return db.all("SELECT * FROM integration_logs WHERE cycle_id = ? ORDER BY id DESC LIMIT ?", cycle_id, limit)
