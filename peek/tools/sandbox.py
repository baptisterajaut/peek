"""Bubblewrap detection + sandboxed command runner for run_shell.

Strategy: everything host-side is read-only-bind-mounted, /tmp/peek is
the single writable surface, all namespaces are unshared (no network, no
host PID visibility), and the child dies with the parent. A `rm -rf /`
inside the sandbox can only nuke /tmp/peek.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

WORKSPACE = Path("/tmp/peek")


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def ensure_workspace() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)


async def run_sandboxed(command: str, timeout: float) -> tuple[int, str]:
    """Run `bash -c command` inside bwrap. Returns (exit_code, combined_output)."""
    bwrap = bwrap_path()
    if bwrap is None:
        return 127, "bwrap not on PATH"
    args = [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--bind", str(WORKSPACE), str(WORKSPACE),
        "--chdir", str(WORKSPACE),
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--",
        "bash", "-c", command,
    ]
    return await _exec(args, timeout)


async def run_unsandboxed(command: str, timeout: float) -> tuple[int, str]:
    """Raw subprocess in the workspace. Used only when the user has set the
    explicit unsafe-flag in config."""
    args = ["bash", "-c", command]
    return await _exec(args, timeout, cwd=str(WORKSPACE))


async def _exec(args: list[str], timeout: float, cwd: str | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        return 124, f"(timeout after {timeout:.0f}s)"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")
