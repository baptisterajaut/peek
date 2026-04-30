"""Desktop notifications via notify-send. Best-effort, never raises."""

from __future__ import annotations

import shutil
import subprocess


def notify(summary: str, body: str = "", *, urgency: str = "low") -> None:
    """Fire a desktop notification. Silent no-op if notify-send is missing."""
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.Popen(  # noqa: S603 — fixed args, no shell
            [
                "notify-send",
                "--app-name=peek",
                f"--urgency={urgency}",
                "--icon=dialog-information",
                summary,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
