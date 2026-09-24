"""Per-device state and the process-wide runtime that owns the sessions."""

from __future__ import annotations

import asyncio
import time

from .adb import Adb, AdbError, connect_tcp, detect_single_device
from .config import Config
from .errors import fail
from .ledger import Ledger
from .marks import build_marks, signature
from .models import Mark, Screen
from .parsers.a11y import parse_screen
from .parsers.packages import App, parse_apps
from .parsers.system import parse_wm_size
from .portal import PortalClient, PortalError

APPS_TTL = 60.0
UNREACHABLE_HINT = "is the device listed by `adb devices` and the Mobilerun Portal enabled?"
DOUBLE_TAP_GAP = 0.12


class DeviceSession:
    def __init__(self, serial: str, config: Config) -> None:
        self.serial = serial
        self.config = config
        self.adb = Adb(serial, config.adb_bin)
        self.portal = PortalClient(self.adb)
        self.lock = asyncio.Lock()
        self.ledger = Ledger()
        self.screen: Screen | None = None
        self.marks: list[Mark] = []
        self.marks_stale = True
        self.signature = ""
        self.last_observation: dict[str, object] | None = None
        self._apps: list[App] = []
        self._apps_at = 0.0
        self._connected = False
        self.browser = None  # BrowserManager, created on first browser tool call

    # ---- connectivity ------------------------------------------------------------------
    async def ensure_connected(self) -> None:
        if self._connected:
            return
        try:
            await connect_tcp(self.serial, self.config.adb_bin)
            await self.portal.connect()
        except (AdbError, PortalError) as exc:
            fail(
                "device_unreachable",
                f"{self.serial}: {exc}",
                UNREACHABLE_HINT,
            )
        self._connected = True

    def _lost(self, exc: Exception):
        self._connected = False
        fail("device_unreachable", f"{self.serial}: {exc}", UNREACHABLE_HINT)

    async def shell(self, command: str, timeout: float = 30.0, check: bool = True) -> str:
        await self.ensure_connected()
        try:
            return await self.adb.shell(command, timeout=timeout, check=check)
        except AdbError as exc:
            self._lost(exc)

    # ---- gestures ----------------------------------------------------------------------
    # Accessibility gestures via the Portal are preferred: they work on surfaces (the notification
    # shade) that ignore injected `input` events, and skip a process launch per gesture.
    async def _portal_gesture(self, call) -> bool:
        await self.ensure_connected()
        if self.portal.transport != "http":
            return False
        try:
            await call()
            return True
        except PortalError:
            return False

    async def tap_xy(self, x: int, y: int) -> None:
        if not await self._portal_gesture(lambda: self.portal.tap(x, y)):
            await self.shell(f"input tap {x} {y}")

    async def swipe_xy(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        if not await self._portal_gesture(lambda: self.portal.swipe(x1, y1, x2, y2, duration_ms)):
            await self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    async def long_press_xy(self, x: int, y: int, duration_ms: int = 800) -> None:
        await self.swipe_xy(x, y, x, y, duration_ms)

    async def double_tap_xy(self, x: int, y: int) -> None:
        # Back-to-back accessibility gestures cancel each other (the page sees one click); a short
        # gap keeps the pair inside the double-tap window (verified: 2 clicks + a dblclick).
        if await self._portal_gesture(lambda: self.portal.tap(x, y)):
            await asyncio.sleep(DOUBLE_TAP_GAP)
            if await self._portal_gesture(lambda: self.portal.tap(x, y)):
                return
        await self.shell(f"input tap {x} {y} & input tap {x} {y} & wait")

    # ---- perception --------------------------------------------------------------------
    async def capture(self) -> Screen:
        await self.ensure_connected()
        try:
            state = await self.portal.state()
        except (AdbError, PortalError) as exc:
            self._lost(exc)
        self.screen = parse_screen(state)
        return self.screen

    async def perceive(self, max_marks: int = 150) -> tuple[Screen, list[Mark]]:
        """Fresh screen plus numbered marks; the marks become the current (valid) ids."""
        screen = await self.capture()
        marks = build_marks(screen, max_marks)
        self.marks, self.marks_stale = marks, False
        self.signature = signature(screen, marks)
        return screen, marks

    async def peek(self) -> tuple[Screen, list[Mark], str]:
        """Capture without touching the id table (used for settle polling)."""
        screen = await self.capture()
        marks = build_marks(screen)
        return screen, marks, signature(screen, marks)

    def invalidate_marks(self) -> None:
        self.marks_stale = True

    def get_mark(self, som_id: int) -> Mark:
        if not self.marks:
            fail("unknown_som_id", "no screen has been perceived yet", "call perceive_screen first")
        if self.marks_stale:
            fail(
                "stale_som_id",
                f"som_id {som_id} belongs to a screen that has since changed",
                "call perceive_screen again and use the new ids",
            )
        for mark in self.marks:
            if mark.id == som_id:
                return mark
        fail("unknown_som_id", f"no mark with id {som_id}", f"valid ids are 1..{len(self.marks)}")

    async def screenshot(self) -> bytes:
        await self.ensure_connected()
        try:
            return await self.portal.screenshot()
        except (AdbError, PortalError) as exc:
            self._lost(exc)

    async def screen_size(self) -> tuple[int, int]:
        if self.screen is not None:
            return self.screen.width, self.screen.height
        size = parse_wm_size(await self.shell("wm size"))
        return size or (720, 1280)

    # ---- apps --------------------------------------------------------------------------
    async def apps(self, refresh: bool = False) -> list[App]:
        if refresh or not self._apps or time.monotonic() - self._apps_at > APPS_TTL:
            await self.ensure_connected()
            try:
                self._apps = parse_apps(await self.portal.apps())
            except (AdbError, PortalError) as exc:
                self._lost(exc)
            self._apps_at = time.monotonic()
        return self._apps


class Runtime:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._sessions: dict[str, DeviceSession] = {}
        self._default = ""

    def default_device(self) -> str:
        """MOBILERUN_DEVICE, else the only attached adb device; anything else is an error."""
        if not self._default:
            self._default = self.config.device or detect_single_device(self.config.adb_bin) or ""
        if not self._default:
            fail(
                "device_unreachable",
                "no device selected",
                "set MOBILERUN_DEVICE=<adb serial> or attach exactly one device",
            )
        return self._default

    def session(self, device: str | None = None) -> DeviceSession:
        serial = device or self.default_device()
        if serial not in self._sessions:
            self._sessions[serial] = DeviceSession(serial, self.config)
        return self._sessions[serial]

    async def aclose(self) -> None:
        for session in self._sessions.values():
            if session.browser is not None:
                await session.browser.close()
            await session.portal.close()
