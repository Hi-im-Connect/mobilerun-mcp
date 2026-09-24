"""Waiting, watching and verifying: wait_for, watch_device_events, validate_action, verify_action."""

from __future__ import annotations

import asyncio
import time

from fastmcp import FastMCP

from .. import ocr as ocr_mod
from ..conditions import (
    diff_events,
    diff_notifications,
    matches,
    normalize,
    screen_text,
    snapshot_of,
)
from ..errors import fail
from ..observe import build_observation
from ..parsers.notifications import parse_notifications
from ..parsers.packages import resolve_app
from ..policy import check_app, check_text
from ..session import DeviceSession, Runtime
from ..shell import view_args
from .apps import resolve_component
from .common import Device, get_session

MAX_WATCH_SECONDS = 30.0
NOTIFICATION_POLL = 2.0
ACTIONS = ("tap", "double_tap", "long_press", "swipe", "type_text", "launch_app", "open_deeplink")


async def poll_until(session: DeviceSession, check, timeout: float, interval: float):
    """Poll the screen until ``check(screen, marks)`` returns a truthy value or time runs out."""
    deadline = time.monotonic() + timeout
    while True:
        screen, marks, sig = await session.peek()
        found = check(screen, marks)
        if found or time.monotonic() >= deadline:
            return found, screen, marks, sig
        await asyncio.sleep(interval)


def register(mcp: FastMCP, rt: Runtime) -> None:
    @mcp.tool(tags={"read"})
    async def wait_for(
        text: str | None = None,
        package: str | None = None,
        activity: str | None = None,
        gone: bool = False,
        timeout: float = 15.0,
        interval: float = 0.5,
        device: Device = None,
    ) -> dict:
        """Wait until text is on screen and/or an app/activity is in the foreground (gone=true waits
        for it to disappear). For long waits (downloads, uploads); gestures already settle."""
        if text is None and package is None and activity is None:
            fail("invalid_argument", "give text, package or activity to wait for")
        session = get_session(rt, device)
        started = time.monotonic()

        def check(screen, marks):
            ok, evidence = matches(screen, marks, text=text, package=package, activity=activity)
            return evidence or True if (not ok if gone else ok) else None

        found, screen, marks, sig = await poll_until(session, check, min(timeout, 120.0), interval)
        return {
            "ok": bool(found),
            "gone": gone,
            "elapsed": round(time.monotonic() - started, 2),
            "evidence": found if isinstance(found, str) else "",
            "observation": build_observation("", screen, marks, sig, True),
        }

    @mcp.tool(tags={"read"})
    async def watch_device_events(
        duration: float = 5.0,
        interval: float = 0.5,
        kinds: list[str] | None = None,
        device: Device = None,
    ) -> dict:
        """Collect what changes over ``duration`` seconds (max 30): foreground app, keyboard,
        screen content, notifications posted/removed. kinds filters: foreground, keyboard,
        screen, notifications."""
        session = get_session(rt, device)
        wanted = set(kinds or ["foreground", "keyboard", "screen", "notifications"])
        duration = min(max(duration, 0.5), MAX_WATCH_SECONDS)
        events: list[dict] = []
        screen, marks, sig = await session.peek()
        prev = snapshot_of(screen, marks, sig)
        keys = (
            {
                n.key
                for n in parse_notifications(
                    await session.shell("dumpsys notification --noredact", timeout=45)
                )
            }
            if "notifications" in wanted
            else set()
        )
        start = last_notification_poll = time.monotonic()
        while time.monotonic() - start < duration:
            await asyncio.sleep(interval)
            screen, marks, sig = await session.peek()
            cur = snapshot_of(screen, marks, sig)
            for event in diff_events(prev, cur):
                kind = {
                    "foreground_changed": "foreground",
                    "keyboard": "keyboard",
                    "screen_changed": "screen",
                }[str(event["type"])]
                if kind in wanted:
                    events.append({"t": round(time.monotonic() - start, 2), **event})
            prev = cur
            if (
                "notifications" in wanted
                and time.monotonic() - last_notification_poll >= NOTIFICATION_POLL
            ):
                last_notification_poll = time.monotonic()
                now_keys = {
                    n.key
                    for n in parse_notifications(
                        await session.shell("dumpsys notification --noredact", timeout=45)
                    )
                }
                for event in diff_notifications(keys, now_keys):
                    events.append({"t": round(time.monotonic() - start, 2), **event})
                keys = now_keys
            if len(events) >= 100:
                break
        return {
            "duration": round(time.monotonic() - start, 2),
            "count": len(events),
            "events": events,
        }

    @mcp.tool(tags={"read"})
    async def validate_action(
        action: str,
        x: int | None = None,
        y: int | None = None,
        som_id: int | None = None,
        text: str | None = None,
        package: str | None = None,
        app_name: str | None = None,
        uri: str | None = None,
        device: Device = None,
    ) -> dict:
        """Dry-run an action: would it be allowed and does its target exist? Nothing is executed."""
        if action not in ACTIONS:
            fail("invalid_argument", f"action must be one of {', '.join(ACTIONS)}")
        session = get_session(rt, device)
        mode, problems = rt.config.policy, []
        blocked = None
        if action in ("tap", "double_tap", "long_press", "swipe"):
            if som_id is not None:
                try:
                    mark = session.get_mark(som_id)
                    x, y = mark.center
                except Exception as exc:  # stale/unknown ids are reported, not raised
                    problems.append(str(exc))
            if x is not None and y is not None:
                width, height = await session.screen_size()
                if not (0 <= x < width and 0 <= y < height):
                    problems.append(f"({x},{y}) is outside the {width}x{height} screen")
            elif som_id is None:
                problems.append("give x and y, or a som_id")
            screen = await session.capture()
            blocked = check_app(mode, screen.phone.package, screen.phone.app)
        elif action == "type_text":
            if text is None:
                problems.append("text is required")
            else:
                screen = await session.capture()
                field = next((e for e in screen.elements if e.focused and e.editable), None)
                if field is None:
                    problems.append("no editable field is focused")
                blocked = check_text(
                    mode,
                    text,
                    " ".join(filter(None, (field.hint, field.description, field.short_id)))
                    if field
                    else "",
                    bool(field and field.password),
                )
        elif action == "launch_app":
            apps = await session.apps()
            match = next((a for a in apps if a.package == package), None)
            if match is None and app_name:
                match, _ = resolve_app(app_name, apps)
            if match is None:
                problems.append("no single installed app matches")
            else:
                blocked = check_app(mode, match.package, match.label)
        else:
            if not uri:
                problems.append("uri is required")
            else:
                component = await resolve_component(session, view_args(uri, package))
                if not component:
                    problems.append(f"no app handles {uri}")
                else:
                    blocked = check_app(mode, component.split("/")[0])
        if blocked is not None and not blocked.allowed:
            problems.append(f"policy_blocked: {blocked.category}")
        return {
            "valid": not problems,
            "problems": problems,
            "policy": {
                "mode": mode,
                "blocked": bool(blocked and not blocked.allowed),
                "category": blocked.category if blocked else "",
            },
        }

    @mcp.tool(tags={"read"})
    async def verify_action(
        expected: str,
        kind: str = "text",
        timeout: float = 3.0,
        use_ocr: bool = False,
        device: Device = None,
    ) -> dict:
        """Check an outcome against the live screen. kind: text (visible), gone (not visible),
        app (foreground package or name), activity, changed (the last action changed the screen)."""
        if kind not in ("text", "gone", "app", "activity", "changed"):
            fail("invalid_argument", "kind must be text, gone, app, activity or changed")
        session = get_session(rt, device)
        if kind == "changed":
            last = session.last_observation or {}
            return {
                "verified": bool(last.get("screen_changed")),
                "evidence": "from the last action's observation",
            }

        def check(screen, marks):
            if kind == "app":
                ok, ev = matches(screen, marks, package=expected)
            elif kind == "activity":
                ok, ev = matches(screen, marks, activity=expected)
            else:
                ok, ev = matches(screen, marks, text=expected)
                ok = ok if kind == "text" else not ok
            return ev or True if ok else None

        found, screen, marks, _ = await poll_until(session, check, timeout, 0.4)
        if not found and kind == "text" and use_ocr and ocr_mod.available():
            lines = await ocr_mod.run_tesseract(await session.screenshot())
            haystack = normalize(" ".join(line.text for line in lines))
            if normalize(expected) in haystack:
                return {"verified": True, "evidence": "found by OCR"}
        return {
            "verified": bool(found),
            "evidence": found if isinstance(found, str) else "",
            "foreground": screen.phone.package,
            "visible_text": screen_text(screen)[:300] if not found else "",
        }
