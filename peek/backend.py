"""LLM backend — llama.cpp server via OpenAI-compatible API.

Ported from ochat/backend/llama_cpp.py, simplified (no n_ctx tracking, no
reasoning split) and extended with tool-call support.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

import httpx
import openai


@dataclass
class StreamDelta:
    """One chunk worth of incremental output from a chat stream."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None


class Backend:
    """Thin async wrapper around llama.cpp's OpenAI-compatible endpoint."""

    def __init__(
        self,
        host: str = "http://localhost:8080",
        verify_ssl: bool = True,
        api_key: str = "llama.cpp",
    ) -> None:
        self.host = host.rstrip("/")
        self.verify_ssl = verify_ssl
        self.api_key = api_key
        self._client: openai.AsyncOpenAI | None = None
        # Track injected http_client so we can aclose() it at shutdown — the
        # openai client doesn't take ownership of clients passed via http_client.
        self._owned_httpx: httpx.AsyncClient | None = None

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            base_url = f"{self.host}/v1"
            kwargs: dict[str, Any] = {"base_url": base_url, "api_key": self.api_key}
            if not self.verify_ssl:
                self._owned_httpx = httpx.AsyncClient(verify=False)
                kwargs["http_client"] = self._owned_httpx
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    async def close(self) -> None:
        """Release the openai client and our injected httpx client, if any."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        if self._owned_httpx is not None:
            try:
                await self._owned_httpx.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._owned_httpx = None

    async def list_models(self) -> list[str]:
        response = await self.client.models.list()
        return [m.id for m in response.data]

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        thinking: bool | None = None,
        extra_body: dict | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Streamed chat. Yields StreamDelta per chunk.

        thinking: None = server default; True/False forces enable_thinking
        in chat_template_kwargs (Qwen3, DeepSeek-R1). Reasoning tokens come
        back on `delta.reasoning_content` and are exposed as StreamDelta.reasoning.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature

        body = dict(extra_body or {})
        if thinking is not None:
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = thinking
        if body:
            kwargs["extra_body"] = body

        stream = await self.client.chat.completions.create(**kwargs)
        tool_acc: dict[int, dict] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            content = delta.content or ""
            reasoning = getattr(delta, "reasoning_content", "") or ""

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_acc.setdefault(tc.index, {
                        "id": "", "name": "", "arguments": "",
                    })
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

            finish = choice.finish_reason
            if content or reasoning or finish:
                yield StreamDelta(
                    content=content, reasoning=reasoning, finish_reason=finish,
                )

            if finish == "tool_calls":
                yield StreamDelta(
                    tool_calls=[
                        {"id": v["id"], "type": "function",
                         "function": {"name": v["name"], "arguments": v["arguments"]}}
                        for v in sorted(tool_acc.values(), key=lambda x: x.get("id", ""))
                    ],
                    finish_reason="tool_calls",
                )

    async def chat_once(
        self,
        model: str,
        messages: list[dict],
        temperature: float | None = None,
        thinking: bool | None = None,
    ) -> str:
        """Non-streamed single completion. Used by the reflect step.

        Default thinking=False — reflect is a structured task, reasoning
        tokens are pure latency cost.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if thinking is not None:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": thinking},
            }
        result = await self.client.chat.completions.create(**kwargs)
        return result.choices[0].message.content or ""
