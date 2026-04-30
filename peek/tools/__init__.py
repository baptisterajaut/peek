"""Tool registry: schemas for the LLM + async dispatch.

Each tool is a (name, schema, fn) triple. fn is async, takes (ctx, **args),
returns a string that will be sent back as the tool message content.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from peek.config import Config
from peek.memory.store import MemoryStore


@dataclass
class ToolContext:
    config: Config
    store: MemoryStore
    scratch: list[str] = field(default_factory=list)


ToolFn = Callable[..., Awaitable[str]]


@dataclass
class Tool:
    name: str
    schema: dict
    fn: ToolFn


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


def all_schemas() -> list[dict]:
    """Return tool list in OpenAI tool-calling format."""
    return [
        {"type": "function", "function": t.schema}
        for t in _REGISTRY.values()
    ]


async def dispatch(ctx: ToolContext, name: str, arguments_json: str) -> str:
    tool = _REGISTRY.get(name)
    if tool is None:
        return f"error: unknown tool {name!r}"
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as e:
        return f"error: invalid JSON arguments ({e})"
    if not isinstance(args, dict):
        return "error: tool arguments must be a JSON object"
    try:
        return await tool.fn(ctx, **args)
    except TypeError as e:
        return f"error: bad arguments for {name}: {e}"
    except Exception as e:  # noqa: BLE001 — tools must be robust, surface as text
        return f"error: {type(e).__name__}: {e}"


# Side-effect imports register tools into _REGISTRY.
from peek.tools import websearch, fetch, note_for_later, read_memory, forget_memory  # noqa: E402,F401
