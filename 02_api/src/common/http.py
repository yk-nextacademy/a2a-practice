"""서비스 간 연동 HTTP 클라이언트와 수신 로그 미들웨어.

모든 호출을 integration_logs 테이블에 남긴다 (Stage 2의 '시스템 연동 로그').
"""

import asyncio
import json
import time

import httpx
from starlette.middleware.base import BaseHTTPMiddleware

from .db import Database, now_ms

RETRIES = 3
BACKOFF = (0.5, 1.0)  # 시도 사이 대기(초): 1회 후 0.5, 2회 후 1.0


class IntegrationError(Exception):
    pass


def _short(text: str | None, limit: int = 4000) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + " ...(truncated)"


class IntegrationClient:
    """다른 서비스를 호출하는 클라이언트. 실패 시 재시도하고, 모든 시도를 기록한다."""

    def __init__(self, db: Database, service: str):
        self.db = db
        self.service = service
        self._client = httpx.AsyncClient(timeout=30, headers={"X-Caller": service})

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, json_body: dict | None = None, cycle_id: int | None = None) -> dict:
        body_text = json.dumps(json_body, ensure_ascii=False) if json_body is not None else None
        last_error = ""
        for attempt in range(1, RETRIES + 1):
            started = time.perf_counter()
            status, response_text, error = None, None, None
            try:
                response = await self._client.request(method, url, json=json_body,
                                                      headers={"X-Cycle-Id": str(cycle_id or "")})
                status, response_text = response.status_code, response.text
                if response.status_code >= 500:
                    error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
            duration = int((time.perf_counter() - started) * 1000)
            self.db.execute(
                "INSERT INTO integration_logs (ts, cycle_id, direction, method, url, status, duration_ms, attempt, request_body, response_body, error)"
                " VALUES (?, ?, 'out', ?, ?, ?, ?, ?, ?, ?, ?)",
                now_ms(), cycle_id, method, url, status, duration, attempt, _short(body_text), _short(response_text), error,
            )
            if error is None:
                if status >= 400:
                    raise IntegrationError(f"{method} {url} -> HTTP {status}: {_short(response_text, 300)}")
                return json.loads(response_text) if response_text else {}
            last_error = error
            if attempt < RETRIES:
                await asyncio.sleep(BACKOFF[attempt - 1])
        raise IntegrationError(f"{method} {url} 호출 실패 ({RETRIES}회 시도): {last_error}")

    async def get(self, url: str, cycle_id: int | None = None) -> dict:
        return await self.request("GET", url, cycle_id=cycle_id)

    async def post(self, url: str, body: dict, cycle_id: int | None = None) -> dict:
        return await self.request("POST", url, body, cycle_id)


class InboundLogMiddleware(BaseHTTPMiddleware):
    """다른 서비스(X-Caller 헤더)가 보낸 요청만 수신 로그로 남긴다."""

    def __init__(self, app, db: Database):
        super().__init__(app)
        self.db = db

    async def dispatch(self, request, call_next):
        caller = request.headers.get("x-caller")
        if not caller:
            return await call_next(request)
        body = (await request.body()).decode("utf-8", "replace")
        started = time.perf_counter()
        response = await call_next(request)
        cycle = request.headers.get("x-cycle-id") or None
        self.db.execute(
            "INSERT INTO integration_logs (ts, cycle_id, direction, method, url, status, duration_ms, request_body, response_body, error)"
            " VALUES (?, ?, 'in', ?, ?, ?, ?, ?, NULL, ?)",
            now_ms(), int(cycle) if cycle else None, request.method, f"{caller} -> {request.url.path}", response.status_code,
            int((time.perf_counter() - started) * 1000), _short(body) or None, None,
        )
        return response
