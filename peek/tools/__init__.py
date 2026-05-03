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


_workspace_registered = False


def register_optional_tools(config: Config) -> str:
    """Register read_file/write_file/run_shell once if the environment allows.

    Gated on bwrap availability OR the explicit unsafe-flag in config — see
    peek/tools/workspace.py. Returns a one-line status suitable for logging.
    Idempotent: subsequent calls report status without re-registering.
    """
    global _workspace_registered
    from peek.tools.sandbox import bwrap_path, ensure_workspace
    from peek.tools.workspace import WORKSPACE, register_workspace_tools

    bwrap = bwrap_path()
    if bwrap:
        mode = "sandboxed (bwrap)"
    elif config.unsafe_no_sandbox:
        mode = "UNSANDBOXED (unsafe flag set)"
    else:
        return (
            "workspace tools disabled: bwrap not on PATH and "
            "[sandbox] i_dont_care_if_an_agent_wipes_my_files is false"
        )

    if not _workspace_registered:
        ensure_workspace()
        register_workspace_tools()
        _workspace_registered = True
    return f"workspace tools enabled — {mode} — workspace={WORKSPACE}"
