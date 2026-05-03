"""Tiny unix-socket IPC between the running daemon and the `peek` CLI.

Protocol: one line per message, JSON, e.g. {"cmd": "toggle"}.
Used so the user can bind `peek toggle` to a global shortcut in their
desktop environment without implementing the GlobalShortcuts portal.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Signal


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/peek-{os.getuid()}"
    p = Path(runtime) / "peek.sock"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class IPCServer(QObject):
    """Unix-socket listener integrated into the Qt event loop."""

    command = Signal(str)  # cmd name

    def __init__(self) -> None:
        super().__init__()
        self.path = socket_path()
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass  # bind() will fail loudly below if the path really is in use
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.path))
        self._server.listen(4)
        self._server.setblocking(False)
        self._notifier = QSocketNotifier(self._server.fileno(), QSocketNotifier.Read)
        self._notifier.activated.connect(self._on_ready)

    def _on_ready(self) -> None:
        try:
            client, _ = self._server.accept()
        except BlockingIOError:
            return
        try:
            with client:
                client.settimeout(0.5)
                data = client.recv(4096)
                if not data:
                    return
                try:
                    msg = json.loads(data.decode("utf-8").strip())
                except json.JSONDecodeError:
                    return
                cmd = msg.get("cmd")
                if isinstance(cmd, str):
                    self.command.emit(cmd)
        except OSError:
            return

    def close(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass


def send_command(cmd: str) -> bool:
    """Send a command to a running daemon. Returns True on success."""
    p = socket_path()
    if not p.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(str(p))
        s.sendall(json.dumps({"cmd": cmd}).encode("utf-8") + b"\n")
        s.close()
        return True
    except OSError:
        return False


def is_daemon_running() -> bool:
    """Probe the IPC socket — distinguishes a live daemon from a stale path."""
    p = socket_path()
    if not p.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.2)
        s.connect(str(p))
        s.close()
        return True
    except OSError:
        return False
