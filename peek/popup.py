"""Frameless SMS-style popup window. Always-on-top, position remembered.

Talks to a worker thread that owns the asyncio loop and the Chat object,
so the UI stays responsive while the model streams.

Lifecycle:
- show(): focus input, ready
- hide(): emits flush_requested(messages, scratch) — daemon decides what to do
- Ctrl+C single = clear input or cancel current generation
- Ctrl+C double (within 1.5s) = hide() → flush
- Esc = hide() → flush
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from peek.chat import Chat
from peek.config import Config
from peek.markdown import to_html
from peek.memory.store import MemoryStore

# ---------------------------------------------------------------------------
# Worker — runs Chat on its own asyncio loop, emits Qt signals.
# ---------------------------------------------------------------------------


class ChatWorker(QObject):
    delta = Signal(str)
    reasoning = Signal(str)
    tool_call = Signal(str, str)  # name, args-json
    tool_result = Signal(str, str)  # name, result
    done = Signal(str)  # full assistant text
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, config: Config, store: MemoryStore) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.chat = Chat.create(config, store)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._loop_ready.wait()
        self._cancel_event: asyncio.Event | None = None

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def shutdown(self) -> None:
        # Run close() inside the loop, then stop the loop from inside it —
        # this lets the coroutine actually complete (vs. fire-and-forget,
        # which leaves a "Task pending" warning at interpreter shutdown).
        # The wait_for caps the worst case if the network teardown stalls.
        loop = self._loop
        if loop is not None and loop.is_running():
            async def _shutdown() -> None:
                try:
                    await asyncio.wait_for(self.chat.backend.close(), timeout=0.5)
                except Exception:  # noqa: BLE001
                    pass
                loop.stop()

            asyncio.run_coroutine_threadsafe(_shutdown(), loop)
        self._thread.join(timeout=1.0)

    def send(self, text: str) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._do_send(text), self._loop)

    def cancel(self) -> None:
        if self._cancel_event is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._cancel_event.set)

    async def _do_send(self, text: str) -> None:
        self._cancel_event = asyncio.Event()
        self.busy_changed.emit(True)
        try:
            stream = self.chat.send(text)
            await self._consume(stream)
        except Exception as e:  # noqa: BLE001 — surface anything the chat layer didn't
            self.error.emit(f"{type(e).__name__}: {e}")
        finally:
            self.busy_changed.emit(False)
            self._cancel_event = None

    async def _consume(self, stream: AsyncIterator[tuple]) -> None:
        async for event in stream:
            if self._cancel_event and self._cancel_event.is_set():
                self.error.emit("cancelled")
                return
            kind = event[0]
            if kind == "delta":
                self.delta.emit(event[1])
            elif kind == "reasoning":
                self.reasoning.emit(event[1])
            elif kind == "tool_call":
                self.tool_call.emit(event[1], str(event[2]))
            elif kind == "tool_result":
                self.tool_result.emit(event[1], event[2])
            elif kind == "done":
                self.done.emit(event[1])
            elif kind == "error":
                self.error.emit(event[1])


# ---------------------------------------------------------------------------
# Bubbles
# ---------------------------------------------------------------------------


_STYLE = """
QWidget#root {
    background-color: #1a1a1a;
    border: 1px solid #333;
    border-radius: 10px;
}
QFrame#dragHandle {
    background-color: #444;
    border-radius: 2px;
}
QFrame#bubbleUser {
    background-color: #2563eb;
    color: white;
    border-radius: 10px;
}
QFrame#bubbleAssistant {
    background-color: #2a2a2a;
    color: #e6e6e6;
    border-radius: 10px;
}
QFrame#bubbleSystem {
    background-color: transparent;
    color: #888;
}
QLabel#bubbleLabel {
    background-color: transparent;
    padding: 6px 10px;
    font-size: 12px;
    selection-background-color: #ffd54f;
    selection-color: #1a1a1a;
}
QLabel#reasoningLabel {
    background-color: #161616;
    color: #999;
    font-style: italic;
    font-size: 11px;
    padding: 8px 12px;
    border-radius: 6px;
    selection-background-color: #ffd54f;
    selection-color: #1a1a1a;
}
QLineEdit {
    background-color: #2a2a2a;
    color: #e6e6e6;
    border: 1px solid #444;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
QLineEdit:focus {
    border-color: #2563eb;
}
QPushButton#cancelBtn {
    background-color: #4a1a1a;
    color: #ff8a8a;
    border: 1px solid #7a2a2a;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
}
QPushButton#cancelBtn:hover {
    background-color: #5a2020;
}
QPushButton#cancelBtn:pressed {
    background-color: #6a2828;
}
QScrollBar:vertical {
    background: transparent;
    width: 6px;
}
QScrollBar::handle:vertical {
    background: #444;
    border-radius: 3px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class Bubble(QFrame):
    def __init__(self, role: str, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if role == "user":
            self.setObjectName("bubbleUser")
        elif role == "assistant":
            self.setObjectName("bubbleAssistant")
        else:
            self.setObjectName("bubbleSystem")
        layout = QVBoxLayout(self)
        # Inner padding so a nested reasoning block doesn't touch the
        # bubble's rounded edge; also a small gap between reasoning and
        # content when both are present.
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.label = QLabel()
        self.label.setObjectName("bubbleLabel")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        self.label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard,
        )
        self.label.setCursor(Qt.IBeamCursor)
        layout.addWidget(self.label)
        self._role = role
        self._reasoning_label: QLabel | None = None
        self._reasoning_text = ""
        self._content_text = text
        self.label.setText(to_html(text))

    def append_content(self, chunk: str) -> None:
        self._content_text += chunk
        self.label.setText(to_html(self._content_text))

    def set_content(self, text: str) -> None:
        self._content_text = text
        self.label.setText(to_html(text))

    def _ensure_reasoning_label(self) -> None:
        if self._reasoning_label is not None:
            return
        self._reasoning_label = QLabel()
        self._reasoning_label.setObjectName("reasoningLabel")
        self._reasoning_label.setWordWrap(True)
        self._reasoning_label.setTextFormat(Qt.RichText)
        self._reasoning_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard,
        )
        self._reasoning_label.setCursor(Qt.IBeamCursor)
        # Insert at top so reasoning shows above content
        self.layout().insertWidget(0, self._reasoning_label)

    def append_reasoning(self, chunk: str) -> None:
        self._ensure_reasoning_label()
        self._reasoning_text += chunk
        self._reasoning_label.setText(to_html(self._reasoning_text))

    def set_reasoning_text(self, text: str) -> None:
        """Overwrite the reasoning area with literal text (used for spinners)."""
        self._ensure_reasoning_label()
        self._reasoning_text = ""  # not real reasoning content
        self._reasoning_label.setText(text)

    def remove_reasoning_label(self) -> None:
        if self._reasoning_label is None:
            return
        self.layout().removeWidget(self._reasoning_label)
        self._reasoning_label.deleteLater()
        self._reasoning_label = None
        self._reasoning_text = ""


def _aligned(bubble: Bubble, role: str) -> QWidget:
    """Wrap a bubble in a horizontal layout with stretch on the right side
    for user (push right) or on the left for assistant (push left)."""
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(0)
    if role == "user":
        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
    elif role == "assistant":
        layout.addWidget(bubble, 0, Qt.AlignLeft)
        layout.addStretch(1)
    else:
        layout.addWidget(bubble, 1, Qt.AlignCenter)
    return wrapper


# ---------------------------------------------------------------------------
# Popup window
# ---------------------------------------------------------------------------


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Popup(QWidget):
    """Frameless always-on-top SMS-style chat popup."""

    flush_requested = Signal(object, object)  # (messages, scratch)

    def __init__(self, worker: ChatWorker) -> None:
        super().__init__()
        self.worker = worker
        self._last_ctrlc = 0.0
        self._busy = False
        self._current_assistant: Bubble | None = None
        self._hide_thinking = False
        self._spinner_state: str | None = None  # None | "waiting" | "thinking"
        self._spinner_frame = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(100)
        self._spinner_timer.timeout.connect(self._tick_spinner)

        self._setup_window()
        self._build_ui()
        self._wire_worker()
        self._restore_geometry()

    # ----- spinner -------------------------------------------------------

    def set_hide_thinking(self, on: bool) -> None:
        self._hide_thinking = on
        # If we're currently showing reasoning text and the user just turned
        # hiding ON, swap to spinner. The reverse direction (hide → show) we
        # don't bother with since the reasoning text wasn't kept.
        if on and self._current_assistant is not None and self._busy:
            if self._spinner_state != "thinking":
                self._set_spinner("thinking")

    def _set_spinner(self, state: str | None) -> None:
        self._spinner_state = state
        if state is None:
            self._spinner_timer.stop()
            return
        self._spinner_frame = 0
        self._render_spinner()
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def _tick_spinner(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        self._render_spinner()

    def _render_spinner(self) -> None:
        if self._current_assistant is None or self._spinner_state is None:
            return
        glyph = _SPINNER_FRAMES[self._spinner_frame]
        if self._spinner_state == "waiting":
            self._current_assistant.set_content(f"{glyph} waiting for first token…")
        elif self._spinner_state == "thinking":
            self._current_assistant.set_reasoning_text(f"{glyph} thinking…")

    def _stop_spinner_for_real_content(self) -> None:
        """First real content delta arrived — clear all placeholders."""
        if self._spinner_state is None:
            return
        if self._current_assistant is not None:
            if self._spinner_state == "waiting":
                self._current_assistant.set_content("")
            elif self._spinner_state == "thinking":
                self._current_assistant.remove_reasoning_label()
        self._set_spinner(None)

    def _setup_window(self) -> None:
        # The daemon forces QT_QPA_PLATFORM=xcb (see daemon.py). Under xcb,
        # Qt.Tool + stays-on-top + frameless is the classic combo: no taskbar
        # entry, kept above other windows, no chrome.
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint,
        )
        self.setWindowTitle("peek")
        self.setStyleSheet(_STYLE)

    def _build_ui(self) -> None:
        self.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Drag handle — small accent strip at the top, signals the area is draggable.
        self.handle = QFrame()
        self.handle.setObjectName("dragHandle")
        self.handle.setFixedHeight(4)
        self.handle.setMinimumWidth(48)
        self.handle.setMaximumWidth(72)
        handle_row = QHBoxLayout()
        handle_row.setContentsMargins(0, 0, 0, 0)
        handle_row.addStretch(1)
        handle_row.addWidget(self.handle)
        handle_row.addStretch(1)
        outer.addLayout(handle_row, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Sticky-bottom scroll. _add_bubble's singleShot(0) fires before the
        # layout settles, so it scrolls to the *previous* max. rangeChanged
        # fires after layout — follow the new bottom, but only when the user
        # was already there (so scrolling up to read history mid-stream
        # doesn't yank them back down on every token).
        bar = self.scroll.verticalScrollBar()
        self._stick_to_bottom = True
        bar.valueChanged.connect(self._on_scroll_value)
        bar.rangeChanged.connect(self._on_scroll_range)

        self.transcript = QWidget()
        self.transcript_layout = QVBoxLayout(self.transcript)
        self.transcript_layout.setContentsMargins(0, 0, 0, 0)
        self.transcript_layout.setSpacing(2)
        # Stretch at the TOP so bubbles cluster at the bottom — same UX as
        # SMS apps and ochat: empty space above, newest message just over
        # the input.
        self.transcript_layout.addStretch(1)
        self.scroll.setWidget(self.transcript)
        outer.addWidget(self.scroll, 1)

        # Input row: text field + cancel button (button hidden until busy).
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("ask peek…")
        self.input.returnPressed.connect(self._on_submit)
        input_row.addWidget(self.input, 1)
        self.cancel_btn = QPushButton("✕ stop")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setFocusPolicy(Qt.NoFocus)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        input_row.addWidget(self.cancel_btn, 0)
        outer.addLayout(input_row, 0)

    def _wire_worker(self) -> None:
        self.worker.delta.connect(self._on_delta)
        self.worker.reasoning.connect(self._on_reasoning)
        self.worker.tool_call.connect(self._on_tool_call)
        self.worker.tool_result.connect(self._on_tool_result)
        self.worker.done.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker.busy_changed.connect(self._on_busy_changed)

    # ----- geometry -------------------------------------------------------

    def _settings(self) -> QSettings:
        return QSettings("peek", "popup")

    def _default_size(self) -> QSize:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QSize(360, 480)
        rect = screen.availableGeometry()
        # 1/5 wide, 1/3 tall — minimum sane floor.
        return QSize(max(280, rect.width() // 5), max(360, rect.height() // 3))

    def _restore_geometry(self) -> None:
        s = self._settings()
        size = s.value("size", None)
        if isinstance(size, QSize):
            self.resize(size)
        else:
            self.resize(self._default_size())
        pos = s.value("pos", None)
        if isinstance(pos, QPoint):
            self.move(pos)
        # NOTE: under Wayland, the compositor may ignore programmatic moves;
        # on KDE specifically, set a Custom Window Rule on class "peek" with
        # "Position = Apply Initially" if you want the saved pos honored.

    def _save_geometry(self) -> None:
        s = self._settings()
        s.setValue("size", self.size())
        s.setValue("pos", self.pos())

    # ----- transcript management -----------------------------------------

    def _add_bubble(self, role: str, text: str = "") -> Bubble:
        bubble = Bubble(role, text)
        wrapper = _aligned(bubble, role)
        # Append after existing bubbles. Layout is [stretch, b1, b2, ...]
        # so addWidget tacks the new one at the bottom.
        self.transcript_layout.addWidget(wrapper)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self) -> None:
        # Force-stick on explicit calls (e.g. send pressed) — even if the user
        # had scrolled up earlier, sending a new message snaps back to the
        # newest content.
        self._stick_to_bottom = True
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_scroll_value(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        # 8px slop — Qt occasionally reports a few px off the exact max.
        self._stick_to_bottom = value >= bar.maximum() - 8

    def _on_scroll_range(self, _min: int, _max: int) -> None:
        if self._stick_to_bottom:
            self.scroll.verticalScrollBar().setValue(_max)

    # ----- events --------------------------------------------------------

    def _on_submit(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy:
            return
        self.input.clear()
        self._add_bubble("user", text)
        self._current_assistant = self._add_bubble("assistant", "")
        self._set_spinner("waiting")
        self.worker.send(text)

    def _on_delta(self, chunk: str) -> None:
        if self._current_assistant is None:
            self._current_assistant = self._add_bubble("assistant", "")
        self._stop_spinner_for_real_content()
        self._current_assistant.append_content(chunk)
        self._scroll_to_bottom()

    def _on_reasoning(self, chunk: str) -> None:
        if self._current_assistant is None:
            self._current_assistant = self._add_bubble("assistant", "")
        if self._hide_thinking:
            # Switch the visible state from "waiting" to "thinking" spinner;
            # actual reasoning text is not displayed.
            if self._spinner_state == "waiting":
                self._current_assistant.set_content("")  # clear waiting placeholder
            if self._spinner_state != "thinking":
                self._set_spinner("thinking")
            return
        # Reasoning shown normally: clear waiting spinner if it was running.
        if self._spinner_state == "waiting":
            self._current_assistant.set_content("")
            self._set_spinner(None)
        self._current_assistant.append_reasoning(chunk)
        self._scroll_to_bottom()

    def _on_tool_call(self, name: str, args: str) -> None:
        self._add_bubble("system", f"→ {name}")
        # The current assistant turn is over (the model emitted tool calls
        # instead of content). Reset spinner state so the NEXT submit re-arms
        # cleanly; the next post-tool turn will create its own bubble.
        self._set_spinner(None)
        self._current_assistant = None

    def _on_tool_result(self, name: str, _result: str) -> None:
        # Already shown the call; we don't dump full results in compact UI.
        pass

    def _on_done(self, _final_text: str) -> None:
        # Clear any lingering spinner (e.g. empty response).
        self._stop_spinner_for_real_content()
        self._current_assistant = None

    def _on_error(self, msg: str) -> None:
        self._stop_spinner_for_real_content()
        self._add_bubble("system", f"⚠ {msg}")
        self._current_assistant = None

    def _on_busy_changed(self, busy: bool) -> None:
        self._busy = busy
        self.input.setEnabled(not busy)
        self.input.setPlaceholderText("…" if busy else "ask peek…")
        self.cancel_btn.setVisible(busy)
        if not busy:
            self.input.setFocus()

    def _on_cancel_clicked(self) -> None:
        if self._busy:
            self.worker.cancel()

    # ----- key handling --------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            # Esc while generating = cancel (don't close+flush). Esc while
            # idle = close+flush as before.
            if self._busy:
                self.worker.cancel()
                return
            self._handle_close()
            return
        if event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier:
            now = time.monotonic()
            if self._busy:
                # First press while generating: cancel.
                self.worker.cancel()
                self._last_ctrlc = now
                return
            if self.input.text():
                self.input.clear()
                self._last_ctrlc = now
                return
            if now - self._last_ctrlc < 1.5:
                self._handle_close()
                return
            self._last_ctrlc = now
            return
        super().keyPressEvent(event)

    def close_and_flush(self) -> None:
        """Hide the window, clear the on-screen transcript, signal flush."""
        # Cancel any in-flight generation FIRST. Otherwise the worker keeps
        # streaming chunks into the about-to-be-reset chat, polluting the
        # next session's context.
        if self._busy:
            self.worker.cancel()
        self._save_geometry()
        self.hide()
        messages = list(self.worker.chat.messages)
        scratch = list(self.worker.chat.scratch)
        self._clear_transcript()
        self._stop_spinner_for_real_content()
        self.flush_requested.emit(messages, scratch)

    def _clear_transcript(self) -> None:
        """Remove all bubbles from the transcript, keeping the leading stretch.

        takeAt() removes the item from the layout but the widget keeps its
        parent and stays visible until deleteLater is processed. We force
        setParent(None) so it disappears immediately.
        """
        layout = self.transcript_layout
        while layout.count() > 1:
            item = layout.takeAt(layout.count() - 1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._current_assistant = None
        self.input.clear()

    # Internal alias kept for the key handler.
    _handle_close = close_and_flush

    # ----- drag-to-move on frameless window ------------------------------
    #
    # Uses windowHandle().startSystemMove() so Wayland's xdg_toplevel.move
    # protocol is honored (manual self.move(...) is a no-op on Wayland).
    # Drag activates on the popup background — anywhere that isn't an
    # interactive child (input, scroll content, etc.).

    def _is_drag_target(self, pos) -> bool:
        widget = self.childAt(pos)
        if widget is None:
            return True
        # Walk up; refuse if we're inside the input or a bubble label
        # (so text selection still works).
        w = widget
        while w is not None and w is not self:
            name = w.objectName()
            if name in {"bubbleLabel", "reasoningLabel"}:
                return False
            if isinstance(w, QLineEdit):
                return False
            w = w.parentWidget()
        return True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._is_drag_target(event.position().toPoint()):
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def closeEvent(self, event: QEvent) -> None:
        self._save_geometry()
        super().closeEvent(event)

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        # Re-assert stays-on-top after the surface is mapped — some
        # compositors only honor the flag on a fresh setWindowFlags() call.
        if not (self.windowFlags() & Qt.WindowStaysOnTopHint):
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.input.setFocus()
        self.activateWindow()
        self.raise_()


def run_standalone() -> int:
    """Standalone popup launcher (no daemon, no global hotkey).

    Useful for testing the UI in isolation. Real entry point is the daemon.
    """
    from peek.config import Config
    from peek.memory.store import MemoryStore
    from peek.personality import ensure_personalities

    app = QApplication.instance() or QApplication([])
    config = Config.load()
    config.write_default_if_missing()
    config.memory_dir.mkdir(parents=True, exist_ok=True)
    ensure_personalities(config.personalities_dir)

    store = MemoryStore(config.memory_dir)
    worker = ChatWorker(config, store)
    popup = Popup(worker)
    popup.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_standalone())
