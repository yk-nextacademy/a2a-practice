"""업무 이벤트 버스: DB 기록 + SSE 구독자에게 전달."""

import asyncio
import json

from .db import Database, now


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._subscribers: set[asyncio.Queue] = set()

    def emit(self, actor: str, type_: str, message: str, payload: dict | None = None, cycle_id: int | None = None) -> dict:
        event = {"ts": now(), "cycle_id": cycle_id, "actor": actor, "type": type_, "message": message, "payload": payload or {}}
        event["id"] = self.db.execute(
            "INSERT INTO events (ts, cycle_id, actor, type, message, payload) VALUES (?, ?, ?, ?, ?, ?)",
            event["ts"], cycle_id, actor, type_, message, event["payload"],
        )
        self.publish(event)
        return event

    def publish(self, event: dict) -> None:
        """DB에 남기지 않는 일시 이벤트(진행률 등)도 전달한다."""
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def stream(self):
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers.discard(queue)
