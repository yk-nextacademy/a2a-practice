"""에이전트 레지스트리.

설정된 주소에서 Agent Card(/.well-known/agent-card.json)를 수집하고, 스킬 ID로 담당 에이전트를 찾는다.
Hub가 에이전트에 대해 아는 것은 Agent Card에 공개된 내용뿐이다.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx
from a2a.client import A2ACardResolver, Client, ClientConfig, ClientFactory
from a2a.types import AgentCard
from google.protobuf.json_format import MessageToDict

from common.db import Database, now

from .audit import AuditTransport

log = logging.getLogger("registry")
REFRESH_SECONDS = 15


@dataclass
class AgentEntry:
    url: str
    card: AgentCard | None = None
    client: Client | None = None
    status: str = "DOWN"
    skills: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.card.name if self.card else self.url


class Registry:
    def __init__(self, db: Database, urls: list[str]):
        self.db = db
        self.entries = {url: AgentEntry(url) for url in urls}
        self.agent_names: dict[str, str] = {}
        # 실습에서는 사람이 일하는 동안 Task 스트림이 오래 열려 있으므로 읽기 타임아웃을 두지 않는다
        self.http = httpx.AsyncClient(transport=AuditTransport(db, self.agent_names), timeout=httpx.Timeout(60, read=None))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.http.aclose()

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(REFRESH_SECONDS)

    async def refresh(self) -> None:
        await asyncio.gather(*(self._refresh_one(e) for e in self.entries.values()))

    async def _refresh_one(self, entry: AgentEntry) -> None:
        started = time.perf_counter()
        try:
            card = await A2ACardResolver(self.http, entry.url).get_agent_card()
        except Exception as exc:
            entry.status = "DOWN"
            self.db.execute("INSERT INTO agents (url, status, error, checked_at) VALUES (?, 'DOWN', ?, ?)"
                            " ON CONFLICT(url) DO UPDATE SET status = 'DOWN', error = excluded.error, checked_at = excluded.checked_at",
                            entry.url, f"{type(exc).__name__}", now())
            return
        latency = int((time.perf_counter() - started) * 1000)
        if entry.card is None or entry.card.SerializeToString() != card.SerializeToString():
            # 카드가 바뀌면 클라이언트를 새로 만든다. 공유 httpx 클라이언트를 닫지 않도록 기존 클라이언트는 close()하지 않는다.
            entry.client = ClientFactory(ClientConfig(streaming=True, httpx_client=self.http)).create(card)
            log.info("agent card loaded: %s (%s)", card.name, entry.url)
        entry.card, entry.status = card, "UP"
        entry.skills = [s.id for s in card.skills]
        self.agent_names[entry.url] = card.name
        self.db.execute(
            "INSERT INTO agents (url, name, card, status, latency_ms, error, checked_at) VALUES (?, ?, ?, 'UP', ?, NULL, ?)"
            " ON CONFLICT(url) DO UPDATE SET name = excluded.name, card = excluded.card, status = 'UP', latency_ms = excluded.latency_ms,"
            " error = NULL, checked_at = excluded.checked_at",
            entry.url, card.name, json.dumps(MessageToDict(card), ensure_ascii=False), latency, now(),
        )

    def find(self, skill: str) -> AgentEntry | None:
        """스킬 ID를 공개한 에이전트 중 살아 있는 것을 고른다."""
        return next((e for e in self.entries.values() if e.status == "UP" and skill in e.skills), None)
