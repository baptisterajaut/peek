"""peek daemon — system tray, popup window, IPC, async memory flush.

The daemon owns:
- a single QApplication
- a single Popup + ChatWorker
- a tray icon for show/hide/quit
- a unix-socket IPC for `peek toggle` (used as the global-hotkey hook —
  bind `peek toggle` in your DE's shortcuts)

Flush lifecycle:
- Popup is hidden → flush_requested(messages, scratch) fires
- Daemon enters flushing state, tray icon swaps to a "busy" tooltip
- Reflect runs on the worker's asyncio loop, completion brings us back
- If a toggle arrives while flushing, we queue it and pop the popup once done
"""

from __future__ import annotations

import asyncio
import os
import sys

# Force xwayland for this process: under Wayland-native, the compositor
# ignores programmatic positioning (QWidget.move()), so the popup forgets
# where it was. Running under xwayland gives us X11 semantics — saved
# position is honored, taskbar exclusion via Qt.Tool is honored, and the
# only trade-off is fractional-scaling HiDPI which doesn't matter here.
# Set BEFORE importing PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import QObject, QSettings, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from peek.chat import Chat
from peek.config import Config, update_personality
from peek.ipc import IPCServer
from peek.memory.reflect import ReflectResult, reflect
from peek.memory.store import MemoryStore
from peek.notify import notify
from peek.personality import ensure_personalities, list_available
from peek.popup import ChatWorker, Popup
from peek.portal import HotkeyPortal


def _make_tray_icon() -> QIcon:
    """Tiny generated icon — round dot, peek-blue. No asset file needed."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter

    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 24, 24)
    p.end()
    return QIcon(pix)


class Daemon(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app

        self.config = Config.load()
        self.config.write_default_if_missing()
        self.config.memory_dir.mkdir(parents=True, exist_ok=True)
        ensure_personalities(self.config.personalities_dir)

        self.store = MemoryStore(self.config.memory_dir)
        self.worker = ChatWorker(self.config, self.store)
        self.popup = Popup(self.worker)
        self.popup.flush_requested.connect(self._on_flush_requested)

        self._flushing = False
        self._pending_show = False
        self._pending_personality: str | None = None
        self._reopen_after_switch = False

        # Persist hide-thinking preference across daemon restarts.
        self._settings = QSettings("peek", "daemon")
        hide = self._settings.value("hide_thinking", False, type=bool)
        self.popup.set_hide_thinking(hide)

        self._ipc = IPCServer()
        self._ipc.command.connect(self._on_ipc)

        self._tray = self._build_tray()
        self._tray.show()

        # Try to auto-register via the GlobalShortcuts portal. If the portal
        # isn't available or refuses, the IPC path (`peek toggle`) still works.
        self._portal = HotkeyPortal([
            ("toggle", "Toggle peek popup", self.config.hotkey),
        ])
        self._portal.activated.connect(self._on_portal_activated)
        self._portal.bound.connect(lambda msg: notify("peek ready", msg))
        self._portal.failed.connect(
            lambda msg: notify(
                "peek ready (manual hotkey)",
                f"{msg}. Bind `peek toggle` in your DE's shortcuts.",
            ),
        )

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(_make_tray_icon(), self)
        tray.setToolTip("peek — idle")
        tray.activated.connect(self._on_tray_activated)
        menu = QMenu()

        a_show = QAction("Show", menu)
        a_show.triggered.connect(self._show_popup)
        menu.addAction(a_show)
        menu.addSeparator()

        # Personality submenu — radio-style, exclusive, current one checked.
        self._personality_menu = menu.addMenu("Personality")
        self._personality_group = QActionGroup(self._personality_menu)
        self._personality_group.setExclusive(True)
        self._rebuild_personality_menu()
        self._personality_menu.aboutToShow.connect(self._rebuild_personality_menu)

        # Hide-thinking toggle: only visible if thinking isn't explicitly off.
        if self.config.thinking is not False:
            self._a_hide_thinking = QAction("Hide thinking", menu, checkable=True)
            self._a_hide_thinking.setChecked(
                self._settings.value("hide_thinking", False, type=bool)
                if hasattr(self, "_settings") else False,
            )
            self._a_hide_thinking.toggled.connect(self._on_hide_thinking_toggled)
            menu.addAction(self._a_hide_thinking)

        menu.addSeparator()

        a_quit = QAction("Quit", menu)
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_quit)
        tray.setContextMenu(menu)
        return tray

    def _on_hide_thinking_toggled(self, checked: bool) -> None:
        self.popup.set_hide_thinking(checked)
        self._settings.setValue("hide_thinking", checked)

    def _rebuild_personality_menu(self) -> None:
        self._personality_menu.clear()
        for old in self._personality_group.actions():
            self._personality_group.removeAction(old)
        names = list_available(self.config.personalities_dir)
        if not names:
            placeholder = QAction("(no personalities)", self._personality_menu)
            placeholder.setEnabled(False)
            self._personality_menu.addAction(placeholder)
            return
        for name in names:
            action = QAction(name, self._personality_menu)
            action.setCheckable(True)
            action.setChecked(name == self.config.personality)
            # Default-arg trick to early-bind `name` in the closure.
            action.triggered.connect(
                lambda _checked=False, n=name: self._request_personality_switch(n),
            )
            self._personality_group.addAction(action)
            self._personality_menu.addAction(action)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._toggle()

    def _on_ipc(self, cmd: str) -> None:
        if cmd == "toggle":
            self._toggle()
        elif cmd == "show":
            self._show_popup()
        elif cmd == "hide":
            self.popup.hide()
        elif cmd == "quit":
            self._quit()

    def _on_portal_activated(self, shortcut_id: str) -> None:
        if shortcut_id == "toggle":
            self._toggle()

    # ----- personality switching -----------------------------------------

    def _request_personality_switch(self, name: str) -> None:
        if name == self.config.personality:
            return
        if self._flushing:
            # Already flushing — queue and apply at flush-done.
            self._pending_personality = name
            notify("peek", f"Switch to '{name}' queued (flush in progress)")
            return

        update_personality(name)
        self.config.personality = name

        has_content = (
            len(self.worker.chat.messages) > 1
            or bool(self.worker.chat.scratch)
        )
        if has_content:
            self._pending_personality = name
            self._reopen_after_switch = self.popup.isVisible()
            self.popup.close_and_flush()  # → flush_requested → flush done → applies
        else:
            # No content to flush; rebuild Chat immediately.
            self._apply_personality_switch(name, reopen=False)

    def _apply_personality_switch(self, name: str, *, reopen: bool) -> None:
        # Build a fresh Chat with the new personality, reusing the worker's
        # backend so we don't reopen the HTTP client.
        self.worker.chat = Chat.create(
            self.config, self.store, backend=self.worker.chat.backend,
        )
        notify("peek", f"Personality → {name}")
        # Update menu check state in case the change came from IPC or elsewhere.
        for action in self._personality_group.actions():
            action.setChecked(action.text() == name)
        if reopen:
            self.popup.show()

    def _toggle(self) -> None:
        if self.popup.isVisible():
            # Toggle off === user is done with this session: flush.
            self.popup.close_and_flush()
        else:
            self._show_popup()

    def _show_popup(self) -> None:
        if self._flushing:
            self._pending_show = True
            notify("peek busy", "Memory flush in progress — will pop once done.")
            return
        self.popup.show()

    def _on_flush_requested(self, messages: list, scratch: list) -> None:
        if self._flushing:
            return
        # Reset the chat for next session immediately so the next show is fresh.
        self.worker.chat.reset()
        self._flushing = True
        self._tray.setToolTip("peek — flushing memory…")
        # Run reflect on the worker's asyncio loop.
        loop = self.worker._loop  # noqa: SLF001 — internal handoff
        if loop is None:
            # Worker never started or already shut down — nothing to do, but
            # we MUST clear the flushing flag so the daemon doesn't lock up.
            self._end_flush()
            return
        future = asyncio.run_coroutine_threadsafe(
            reflect(
                config=self.config, store=self.store,
                messages=messages, scratch=scratch,
                backend=self.worker.chat.backend,
            ),
            loop,
        )
        # Poll the future from Qt land — no extra threading needed.
        timer = QTimer(self)
        timer.setInterval(150)

        def _check() -> None:
            if not future.done():
                return
            timer.stop()
            timer.deleteLater()
            try:
                result: ReflectResult = future.result()
            except Exception as e:  # noqa: BLE001
                notify("peek flush failed", str(e), urgency="critical")
                self._end_flush()
                return
            self._on_flush_done(result)

        timer.timeout.connect(_check)
        timer.start()

    def _on_flush_done(self, result: ReflectResult) -> None:
        if result.applied:
            summary = ", ".join(
                f"{op['action']} {op.get('name') or op.get('filename', '?')}"
                for op in result.applied
            )
            notify(f"peek memory updated ({len(result.applied)})", summary[:200])
        elif result.errors:
            notify("peek flush errors",
                   "\n".join(result.errors)[:200], urgency="normal")

        pending = self._pending_personality
        if pending:
            self._pending_personality = None
            reopen = self._reopen_after_switch
            self._reopen_after_switch = False
            self._apply_personality_switch(pending, reopen=reopen)
        else:
            # Reload the system prompt so the next session sees new memory.
            self.worker.chat.refresh_system_prompt()
        self._end_flush()

    def _end_flush(self) -> None:
        self._flushing = False
        self._tray.setToolTip("peek — idle")
        if self._pending_show:
            self._pending_show = False
            self.popup.show()

    def _quit(self) -> None:
        self._portal.stop()
        self._ipc.close()
        self.worker.shutdown()
        self.app.quit()


def main() -> int:
    if len(sys.argv) >= 2:
        cmd = sys.argv[1]
        if cmd == "setup":
            from peek.setup import main as setup_main
            return setup_main()
        if cmd == "help" or cmd in {"-h", "--help"}:
            print("Usage: peek [setup|toggle|show|hide|quit]")
            return 0
        if cmd in {"toggle", "show", "hide", "quit"}:
            from peek.ipc import send_command
            ok = send_command(cmd)
            if not ok:
                print("peek daemon is not running", file=sys.stderr)
                return 1
            return 0
        print(f"unknown command: {cmd!r} (try `peek help`)", file=sys.stderr)
        return 2

    # First-run wizard: config missing AND running interactively.
    from peek.config import CONFIG_PATH
    if not CONFIG_PATH.exists() and sys.stdin.isatty():
        from peek.setup import main as setup_main
        rc = setup_main()
        if rc != 0:
            return rc

    # Basic logging — silent by default, set PEEK_DEBUG=1 to see it.
    import logging
    if os.environ.get("PEEK_DEBUG"):
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray-only lifecycle

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("system tray is not available — peek requires it", file=sys.stderr)
        return 2

    daemon = Daemon(app)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
