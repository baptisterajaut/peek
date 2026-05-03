"""read_file / write_file / run_shell — coding-helper tools confined to /tmp/peek.

All file paths are resolved against WORKSPACE and rejected if they escape
it (so symlinks pointing out, `..`, and absolute paths all fail). run_shell
goes through bwrap unless the user has explicitly opted out via config.
"""

from __future__ import annotations

from pathlib import Path

from peek.tools import Tool, ToolContext, register
from peek.tools.sandbox import (
    WORKSPACE,
    bwrap_path,
    run_sandboxed,
    run_unsandboxed,
)

_MAX_READ_BYTES = 64 * 1024
_MAX_WRITE_BYTES = 256 * 1024
_MAX_SHELL_OUTPUT = 8 * 1024
_SHELL_TIMEOUT = 30.0


def _resolve(path: str) -> Path:
    """Resolve `path` inside the workspace, raising if it escapes."""
    if not path or path.startswith("/"):
        raise ValueError("path must be relative to the workspace")
    target = (WORKSPACE / path).resolve()
    workspace_root = WORKSPACE.resolve()
    if target != workspace_root and workspace_root not in target.parents:
        raise ValueError("path escapes the workspace")
    return target


READ_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read a file from the agent workspace at /tmp/peek. Path is relative "
        "to the workspace. Returns the file contents (truncated to 64 KB)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path."},
        },
        "required": ["path"],
    },
}


WRITE_SCHEMA = {
    "name": "write_file",
    "description": (
        "Write text to a file in the agent workspace at /tmp/peek. Overwrites "
        "if it exists. Path is relative to the workspace; parent directories "
        "are created. Max 256 KB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path."},
            "content": {"type": "string", "description": "File contents."},
        },
        "required": ["path", "content"],
    },
}


SHELL_SCHEMA = {
    "name": "run_shell",
    "description": (
        "Run a bash command inside the agent workspace (/tmp/peek). Sandboxed "
        "via bubblewrap: read-only view of the host, no network, only "
        "/tmp/peek is writable. Use for one-off scripts, computations, dice "
        "rolls — anything deterministic that beats guessing. Returns combined "
        "stdout+stderr (truncated to 8 KB) and the exit code. 30s timeout."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command. Multi-line OK; use $'...' for literal newlines.",
            },
        },
        "required": ["command"],
    },
}


async def read_file(ctx: ToolContext, path: str) -> str:  # noqa: ARG001
    target = _resolve(path)
    if not target.exists():
        return f"error: {path} not found"
    if not target.is_file():
        return f"error: {path} is not a regular file"
    data = target.read_bytes()
    truncated = len(data) > _MAX_READ_BYTES
    text = data[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[truncated, {len(data) - _MAX_READ_BYTES} more bytes]"
    return text or "(empty file)"


async def write_file(ctx: ToolContext, path: str, content: str) -> str:  # noqa: ARG001
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_WRITE_BYTES:
        return f"error: content too large ({len(encoded)} > {_MAX_WRITE_BYTES})"
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded)
    return f"wrote {len(encoded)} bytes to {path}"


async def run_shell(ctx: ToolContext, command: str) -> str:
    if ctx.config.unsafe_no_sandbox and bwrap_path() is None:
        code, out = await run_unsandboxed(command, _SHELL_TIMEOUT)
    else:
        code, out = await run_sandboxed(command, _SHELL_TIMEOUT)
    if len(out) > _MAX_SHELL_OUTPUT:
        out = out[:_MAX_SHELL_OUTPUT] + f"\n[truncated, {len(out) - _MAX_SHELL_OUTPUT} more chars]"
    return f"exit {code}\n{out}" if out else f"exit {code} (no output)"


def register_workspace_tools() -> None:
    register(Tool(name=READ_SCHEMA["name"], schema=READ_SCHEMA, fn=read_file))
    register(Tool(name=WRITE_SCHEMA["name"], schema=WRITE_SCHEMA, fn=write_file))
    register(Tool(name=SHELL_SCHEMA["name"], schema=SHELL_SCHEMA, fn=run_shell))
