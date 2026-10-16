"""Claude 호출 래퍼. LLM 모드가 아니거나 호출이 실패하면 mock 결과를 돌려준다."""

import json
import logging
from typing import Callable

import anthropic

log = logging.getLogger("llm")


class LLMClient:
    def __init__(self, mode: str, model: str):
        self.mode = mode
        self.model = model
        self.calls = 0
        self.failures = 0
        self.last_error: str | None = None
        self._client = anthropic.AsyncAnthropic() if mode == "llm" else None

    @property
    def label(self) -> str:
        return self.model if self.mode == "llm" else "mock"

    async def json(self, system: str, prompt: str, schema: dict, fallback: Callable[[], dict]) -> tuple[dict, str]:
        """구조화 출력(JSON) 요청. Returns: (결과, 생성 주체 "llm" | "mock")"""
        if self._client is None:
            return fallback(), "mock"
        self.calls += 1
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason != "end_turn":
                raise RuntimeError(f"unexpected stop_reason: {response.stop_reason}")
            text = next(b.text for b in response.content if b.type == "text")
            return json.loads(text), "llm"
        except (anthropic.APIError, RuntimeError, StopIteration, json.JSONDecodeError) as exc:
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("LLM 호출 실패, mock으로 대체: %s", self.last_error)
            return fallback(), "mock"
