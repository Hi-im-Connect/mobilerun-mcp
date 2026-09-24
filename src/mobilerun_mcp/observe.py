"""Settle-then-observe: every mutating action returns what the screen looks like afterwards."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .marks import has_loading_indicator, top_labels
from .models import Mark, Screen

Capture = Callable[[], Awaitable[tuple[Screen, list[Mark], str]]]
Ready = Callable[[Screen, str], bool]

INITIAL_DELAY = 0.3
POLL_INTERVAL = 0.25
SETTLE_TIMEOUT = 3.0


async def settle(
    capture: Capture,
    *,
    timeout: float = SETTLE_TIMEOUT,
    interval: float = POLL_INTERVAL,
    delay: float = INITIAL_DELAY,
    ready: Ready | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Screen, list[Mark], str, bool]:
    """Poll until two consecutive captures agree and ``ready`` (default: a foreground app exists)
    accepts them. Returns (screen, marks, signature, settled)."""
    ready = ready or (lambda screen, _sig: bool(screen.phone.package))
    await sleep(delay)
    deadline = clock() + timeout
    screen, marks, sig = await capture()
    while clock() < deadline:
        await sleep(interval)
        screen2, marks2, sig2 = await capture()
        if sig2 == sig and ready(screen2, sig2):
            return screen2, marks2, sig2, True
        screen, marks, sig = screen2, marks2, sig2
    return screen, marks, sig, False


def build_observation(
    before_signature: str, screen: Screen, marks: list[Mark], signature: str, settled: bool
) -> dict[str, Any]:
    return {
        "foreground_app": screen.phone.app or screen.phone.package,
        "package": screen.phone.package,
        "activity": screen.phone.activity.rsplit(".", 1)[-1],
        "element_count": len(marks),
        "keyboard_visible": screen.phone.keyboard_visible,
        "top_labels": top_labels(marks),
        "screen_changed": bool(before_signature) and before_signature != signature,
        "loading_indicator_present": has_loading_indicator(screen),
        "settled": settled,
    }


async def mutate(
    session: Any,
    action: Callable[[], Awaitable[None]],
    result: dict[str, Any] | None = None,
    *,
    settle_timeout: float = SETTLE_TIMEOUT,
    expect_change: bool = False,
    expect_package: str | None = None,
) -> dict[str, Any]:
    """Run ``action`` under the device lock, wait for the screen to settle, report the change.

    ``expect_package`` (launches, deep links) finishes once that app, or any other app that
    replaced the previous one, is in the foreground: cold starts can take ~20s on a slow box, but
    an already-foreground target returns at once. ``expect_change`` waits for any screen change."""
    async with session.lock:
        before_screen, _, before = await session.peek()
        before_package = before_screen.phone.package
        await action()
        session.invalidate_marks()

        def ready(screen: Screen, sig: str) -> bool:
            package = screen.phone.package
            if not package:
                return False
            if expect_package:
                return package == expect_package or package != before_package
            return not expect_change or sig != before

        screen, marks, sig, settled = await settle(
            session.peek, timeout=settle_timeout, ready=ready
        )
        observation = build_observation(before, screen, marks, sig, settled)
        session.signature = sig
        session.last_observation = observation
    return {"ok": True, **(result or {}), "post_action_observation": observation}
