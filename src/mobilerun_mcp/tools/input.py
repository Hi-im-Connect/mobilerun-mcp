"""Gestures, typing and hardware keys."""

from __future__ import annotations

from fastmcp import FastMCP

from ..errors import fail
from ..marks import find_marks
from ..observe import mutate, settle
from ..policy import check_text
from ..session import DeviceSession, Runtime
from .common import Device, check_point, enforce, get_session, guard_foreground

KEY_HOME, KEY_BACK, KEY_ENTER, KEY_RECENTS = 3, 4, 66, 187
DEFAULT_SWIPE_MS = 300
DEFAULT_LONG_PRESS_MS = 800
SCROLL_TOP_MARGIN = 120
SCROLL_BOTTOM_MARGIN = 140


def scroll_vector(direction: str, region: tuple[int, int, int, int], amount: float):
    """(x1, y1, x2, y2) finger path that scrolls the content ``direction`` by ``amount`` of the region."""
    left, top, right, bottom = region
    cx, cy = (left + right) // 2, (top + bottom) // 2
    dx = int((right - left) * min(max(amount, 0.1), 0.9) / 2)
    dy = int((bottom - top) * min(max(amount, 0.1), 0.9) / 2)
    return {
        "down": (cx, cy + dy, cx, cy - dy),
        "up": (cx, cy - dy, cx, cy + dy),
        "right": (cx + dx, cy, cx - dx, cy),
        "left": (cx - dx, cy, cx + dx, cy),
    }[direction]


async def tap_point(session: DeviceSession, x: int, y: int) -> None:
    await session.tap_xy(x, y)


def register(mcp: FastMCP, rt: Runtime) -> None:
    async def resolve_point(session, x, y, som_id) -> tuple[int, int, dict]:
        if som_id is not None:
            mark = session.get_mark(som_id)
            cx, cy = mark.center
            return cx, cy, {"target": {"som_id": som_id, "label": mark.label}}
        if x is None or y is None:
            fail("invalid_argument", "give x and y, or a som_id")
        check_point(await session.screen_size(), x, y)
        return x, y, {"target": {"x": x, "y": y}}

    @mcp.tool(tags={"write"})
    async def tap(
        x: int | None = None, y: int | None = None, som_id: int | None = None, device: Device = None
    ) -> dict:
        """Tap at (x, y) or at the center of a numbered mark (som_id from perceive_screen)."""
        session = get_session(rt, device)
        await guard_foreground(rt, session)
        cx, cy, info = await resolve_point(session, x, y, som_id)
        return await mutate(session, lambda: tap_point(session, cx, cy), {"action": "tap", **info})

    @mcp.tool(tags={"write"})
    async def double_tap(
        x: int | None = None, y: int | None = None, som_id: int | None = None, device: Device = None
    ) -> dict:
        """Double-tap at (x, y) or a mark; both taps are issued concurrently so they land together."""
        session = get_session(rt, device)
        await guard_foreground(rt, session)
        cx, cy, info = await resolve_point(session, x, y, som_id)
        return await mutate(
            session, lambda: session.double_tap_xy(cx, cy), {"action": "double_tap", **info}
        )

    @mcp.tool(tags={"write"})
    async def long_press(
        x: int | None = None,
        y: int | None = None,
        som_id: int | None = None,
        duration_ms: int = DEFAULT_LONG_PRESS_MS,
        device: Device = None,
    ) -> dict:
        """Press and hold at (x, y) or a mark."""
        session = get_session(rt, device)
        await guard_foreground(rt, session)
        cx, cy, info = await resolve_point(session, x, y, som_id)
        return await mutate(
            session,
            lambda: session.long_press_xy(cx, cy, duration_ms),
            {"action": "long_press", **info},
        )

    @mcp.tool(tags={"write"})
    async def swipe(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = DEFAULT_SWIPE_MS,
        duration: float | None = None,
        device: Device = None,
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2). ``duration`` (seconds) is accepted for old clients."""
        session = get_session(rt, device)
        await guard_foreground(rt, session)
        size = await session.screen_size()
        for px, py in ((x1, y1), (x2, y2)):
            check_point(size, px, py)
        ms = int(duration * 1000) if duration is not None else duration_ms
        return await mutate(
            session,
            lambda: session.swipe_xy(x1, y1, x2, y2, ms),
            {"action": "swipe", "from": [x1, y1], "to": [x2, y2]},
        )

    async def do_scroll(direction: str, amount: float, som_id: int | None, device: Device) -> dict:
        session = get_session(rt, device)
        await guard_foreground(rt, session)
        width, height = await session.screen_size()
        if som_id is not None:
            b = session.get_mark(som_id).bounds
            region = (b.left, b.top, b.right, b.bottom)
        else:
            region = (0, SCROLL_TOP_MARGIN, width, height - SCROLL_BOTTOM_MARGIN)
        x1, y1, x2, y2 = scroll_vector(direction, region, amount)
        return await mutate(
            session,
            lambda: session.swipe_xy(x1, y1, x2, y2, 600),
            {"action": f"scroll_{direction}"},
        )

    @mcp.tool(tags={"write"})
    async def scroll_down(
        amount: float = 0.5, som_id: int | None = None, device: Device = None
    ) -> dict:
        """Scroll the content down (reveal what is below). ``amount`` is a fraction of the region."""
        return await do_scroll("down", amount, som_id, device)

    @mcp.tool(tags={"write"})
    async def scroll_up(
        amount: float = 0.5, som_id: int | None = None, device: Device = None
    ) -> dict:
        """Scroll the content up (reveal what is above)."""
        return await do_scroll("up", amount, som_id, device)

    @mcp.tool(tags={"write"})
    async def scroll_left(
        amount: float = 0.5, som_id: int | None = None, device: Device = None
    ) -> dict:
        """Scroll the content left (reveal what is to the left)."""
        return await do_scroll("left", amount, som_id, device)

    @mcp.tool(tags={"write"})
    async def scroll_right(
        amount: float = 0.5, som_id: int | None = None, device: Device = None
    ) -> dict:
        """Scroll the content right (reveal what is to the right)."""
        return await do_scroll("right", amount, som_id, device)

    @mcp.tool(tags={"write"})
    async def scroll_to(
        text: str, direction: str = "down", max_scrolls: int = 8, device: Device = None
    ) -> dict:
        """Scroll until an element whose label contains ``text`` is visible; returns its mark."""
        if direction not in ("down", "up", "left", "right"):
            fail("invalid_argument", "direction must be down, up, left or right")
        session = get_session(rt, device)
        await guard_foreground(rt, session)
        width, height = await session.screen_size()
        region = (0, SCROLL_TOP_MARGIN, width, height - SCROLL_BOTTOM_MARGIN)
        vector = scroll_vector(direction, region, 0.6)
        async with session.lock:
            last_sig = ""
            for scrolls in range(max_scrolls + 1):
                _, marks = await session.perceive()
                hits = find_marks(marks, text)
                if hits:
                    return {
                        "ok": True,
                        "found": True,
                        "scrolls": scrolls,
                        "mark": hits[0].to_dict(),
                    }
                if session.signature == last_sig:
                    break  # the screen stopped changing: end of the list
                last_sig = session.signature
                if scrolls < max_scrolls:
                    await session.swipe_xy(*vector, 600)
                    await settle(session.peek)  # let the fling finish so the next tap is a tap
        return {
            "ok": True,
            "found": False,
            "message": f'"{text}" not found while scrolling {direction}',
        }

    @mcp.tool(tags={"write"})
    async def type_text(
        text: str,
        clear: bool = False,
        submit: bool = False,
        som_id: int | None = None,
        device: Device = None,
    ) -> dict:
        """Type into the focused field (tap it first, or pass som_id). submit presses Enter after."""
        session = get_session(rt, device)
        if som_id is not None:
            cx, cy, _ = await resolve_point(session, None, None, som_id)
            await mutate(session, lambda: tap_point(session, cx, cy), {"action": "focus"})
        screen = await session.capture()
        focused = next((e for e in screen.elements if e.focused and e.editable), None)
        hint = " ".join(
            filter(None, (focused.hint, focused.description, focused.short_id) if focused else ())
        )
        enforce(check_text(rt.config.policy, text, hint, bool(focused and focused.password)))
        note = (
            {}
            if focused
            else {"warning": "no editable field appears focused; typing may go nowhere"}
        )

        async def action() -> None:
            await session.portal.input_text(text, clear)
            if submit:
                await session.shell(f"input keyevent {KEY_ENTER}")

        return await mutate(session, action, {"action": "type_text", "chars": len(text), **note})

    async def press_key(keycode: int, name: str, device: Device) -> dict:
        session = get_session(rt, device)
        return await mutate(
            session, lambda: session.shell(f"input keyevent {keycode}"), {"action": name}
        )

    @mcp.tool(tags={"write"})
    async def press_home(device: Device = None) -> dict:
        """Press the Home button."""
        return await press_key(KEY_HOME, "press_home", device)

    @mcp.tool(tags={"write"})
    async def press_back(device: Device = None) -> dict:
        """Press Back (also closes the keyboard without leaving the screen)."""
        return await press_key(KEY_BACK, "press_back", device)

    @mcp.tool(tags={"write"})
    async def press_enter(device: Device = None) -> dict:
        """Press Enter (submits search bars and forms)."""
        return await press_key(KEY_ENTER, "press_enter", device)

    @mcp.tool(tags={"write"})
    async def open_recent_apps(device: Device = None) -> dict:
        """Open the recent-apps overview."""
        return await press_key(KEY_RECENTS, "open_recent_apps", device)
