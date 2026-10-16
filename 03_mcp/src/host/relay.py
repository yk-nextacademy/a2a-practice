"""관제용 이벤트 중계: Host 자신의 이벤트와 Dev/QA 시스템의 이벤트(SSE)를 하나의 스트림으로 합친다.

업무 연결은 MCP로만 하고, 이 중계는 사람의 작업(판정, 수정 등)을 Hub 화면에 실시간으로 보여 주기 위한 관찰 채널이다.
"""

import asyncio
import json

import httpx

from common import config


class EventRelay:
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._follow("Dev", config.DEV_URL)), asyncio.create_task(self._follow("QA", config.QA_URL))]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()

    def publish(self, event: dict) -> None:
        for q in list(self.subscribers):
            q.put_nowait(event)

    async def _follow(self, source: str, base: str) -> None:
        async with httpx.AsyncClient(timeout=None) as client:
            while True:
                try:
                    async with client.stream("GET", base + "/api/events/stream") as r:
                        async for line in r.aiter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
                                event["source"] = source
                                self.publish(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(2)

    async def stream(self):
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self.subscribers.discard(q)


async def department_events(client: httpx.AsyncClient, cycle_id: int, limit: int = 500) -> list[dict]:
    """Dev/QA 시스템이 기록한 이벤트 (관찰용 HTTP 조회)."""
    out: list[dict] = []
    for source, base in (("Dev", config.DEV_URL), ("QA", config.QA_URL)):
        try:
            r = await client.get(f"{base}/api/events", params={"cycle_id": cycle_id, "limit": limit}, timeout=5)
            out += [{**e, "source": source} for e in r.json()]
        except (httpx.HTTPError, ValueError):
            pass
    return out
