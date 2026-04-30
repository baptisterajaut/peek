"""GlobalShortcuts portal client — auto-registers a hotkey via xdg-desktop-portal.

The xdg-desktop-portal exposes `org.freedesktop.portal.GlobalShortcuts` on
the session bus. We:
  1. CreateSession()             → wait for Response   → session_handle
  2. BindShortcuts(session, …)    → wait for Response   → done
  3. Subscribe to Activated(…) and emit a Qt signal each time the user fires it

Lives on its own asyncio loop in a daemon thread; communicates to the Qt
main thread via the `activated` signal.

If the portal isn't available, or any step fails, the worker logs and exits
quietly — the daemon's IPC path (`peek toggle`) keeps working as before.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading

from PySide6.QtCore import QObject, Signal

try:
    from dbus_next import BusType, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.introspection import Node
    DBUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    DBUS_AVAILABLE = False
    BusType = Variant = MessageBus = Node = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

# We define the interfaces we need by hand. Live introspection of
# /org/freedesktop/portal/desktop fails under dbus-next because portal
# implementations expose property names with hyphens, which dbus-next's
# member-name validator (over-strict) rejects.
_GLOBAL_SHORTCUTS_XML = """\
<node>
  <interface name="org.freedesktop.portal.GlobalShortcuts">
    <method name="CreateSession">
      <arg type="a{sv}" direction="in" name="options"/>
      <arg type="o" direction="out" name="handle"/>
    </method>
    <method name="BindShortcuts">
      <arg type="o" direction="in" name="session_handle"/>
      <arg type="a(sa{sv})" direction="in" name="shortcuts"/>
      <arg type="s" direction="in" name="parent_window"/>
      <arg type="a{sv}" direction="in" name="options"/>
      <arg type="o" direction="out" name="handle"/>
    </method>
    <method name="ListShortcuts">
      <arg type="o" direction="in" name="session_handle"/>
      <arg type="a{sv}" direction="in" name="options"/>
      <arg type="o" direction="out" name="handle"/>
    </method>
    <signal name="Activated">
      <arg type="o" name="session_handle"/>
      <arg type="s" name="shortcut_id"/>
      <arg type="t" name="timestamp"/>
      <arg type="a{sv}" name="options"/>
    </signal>
    <signal name="Deactivated">
      <arg type="o" name="session_handle"/>
      <arg type="s" name="shortcut_id"/>
      <arg type="t" name="timestamp"/>
      <arg type="a{sv}" name="options"/>
    </signal>
    <signal name="ShortcutsChanged">
      <arg type="o" name="session_handle"/>
      <arg type="a(sa{sv})" name="shortcuts"/>
    </signal>
  </interface>
</node>
"""

_REQUEST_XML = """\
<node>
  <interface name="org.freedesktop.portal.Request">
    <method name="Close"/>
    <signal name="Response">
      <arg type="u" name="response"/>
      <arg type="a{sv}" name="results"/>
    </signal>
  </interface>
</node>
"""


def _request_path(sender: str, token: str) -> str:
    # Per portal spec, sender is the unique connection name with ':' stripped
    # and '.' replaced by '_'.
    sender_clean = sender.replace(":", "").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender_clean}/{token}"


class HotkeyPortal(QObject):
    """Async portal client. Emits `activated(shortcut_id)` when a binding fires."""

    activated = Signal(str)
    bound = Signal(str)  # short status string suitable for a notification
    failed = Signal(str)

    def __init__(self, shortcuts: list[tuple[str, str, str]]) -> None:
        """shortcuts: list of (id, description, preferred_trigger)."""
        super().__init__()
        self._shortcuts = shortcuts
        self._stop = threading.Event()
        if not DBUS_AVAILABLE:
            self.failed.emit("dbus-next not installed")
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="peek-portal")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception as e:  # noqa: BLE001
            _log.exception("portal worker died")
            self.failed.emit(f"portal: {type(e).__name__}: {e}")
        finally:
            loop.close()

    async def _await_response(self, bus, request_path: str) -> dict:
        """Wait for the Response(uint32, a{sv}) signal on a Request object path."""
        future: asyncio.Future = asyncio.get_event_loop().create_future()

        request_intro = Node.parse(_REQUEST_XML)
        proxy = bus.get_proxy_object(PORTAL_BUS, request_path, request_intro)
        request = proxy.get_interface("org.freedesktop.portal.Request")

        def on_response(code: int, results: dict) -> None:
            if not future.done():
                future.set_result({"code": code, "results": results})

        request.on_response(on_response)
        try:
            return await asyncio.wait_for(future, timeout=60.0)
        finally:
            try:
                request.off_response(on_response)
            except Exception:  # noqa: BLE001
                pass

    async def _main(self) -> None:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        portal_intro = Node.parse(_GLOBAL_SHORTCUTS_XML)
        proxy = bus.get_proxy_object(PORTAL_BUS, PORTAL_PATH, portal_intro)
        gs = proxy.get_interface("org.freedesktop.portal.GlobalShortcuts")

        # ----- 1. CreateSession --------------------------------------------
        handle_token = "peek_" + secrets.token_hex(6)
        session_token = "peek_session_" + secrets.token_hex(6)
        request_path = _request_path(bus.unique_name, handle_token)

        # Subscribe to the Response signal BEFORE making the call (the
        # Request object is created server-side at the deterministic path
        # we computed from the token).
        wait_create = asyncio.create_task(self._await_response(bus, request_path))
        await gs.call_create_session({
            "handle_token": Variant("s", handle_token),
            "session_handle_token": Variant("s", session_token),
        })
        resp = await wait_create
        if resp["code"] != 0:
            self.failed.emit(f"CreateSession refused (code={resp['code']})")
            return
        session_handle = resp["results"]["session_handle"].value
        _log.info("portal session %s", session_handle)

        # ----- 2. BindShortcuts --------------------------------------------
        bind_token = "peek_bind_" + secrets.token_hex(6)
        bind_request_path = _request_path(bus.unique_name, bind_token)
        wait_bind = asyncio.create_task(self._await_response(bus, bind_request_path))

        shortcut_descriptors = []
        for sid, description, preferred in self._shortcuts:
            opts = {"description": Variant("s", description)}
            if preferred:
                opts["preferred_trigger"] = Variant("s", preferred)
            shortcut_descriptors.append([sid, opts])

        await gs.call_bind_shortcuts(
            session_handle,
            shortcut_descriptors,
            "",  # parent_window
            {"handle_token": Variant("s", bind_token)},
        )
        resp = await wait_bind
        if resp["code"] != 0:
            self.failed.emit(f"BindShortcuts refused (code={resp['code']})")
            return

        ids = ", ".join(s[0] for s in self._shortcuts)
        self.bound.emit(f"shortcuts registered: {ids}")
        _log.info("shortcuts bound")

        # ----- 3. Listen for Activated -------------------------------------
        def on_activated(_session, shortcut_id, _timestamp, _options) -> None:
            self.activated.emit(shortcut_id)

        gs.on_activated(on_activated)

        # Keep the loop alive until told to stop.
        while not self._stop.is_set():
            await asyncio.sleep(0.5)
