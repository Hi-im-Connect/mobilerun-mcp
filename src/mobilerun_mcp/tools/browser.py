"""Browser tools: CDP into the on-device browser (WebView Browser Tester) or an in-app WebView."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from ..browser import js
from ..browser.cdp import CdpError
from ..browser.manager import SHELL_ACTIVITY, SHELL_PACKAGE, Attached, BrowserManager
from ..browser.navigation import navigate
from ..errors import fail
from ..policy import check_text
from ..session import DeviceSession, Runtime
from .common import Device, enforce, get_session

KEYS = {
    "Enter": (13, "Enter", "\r"),
    "Tab": (9, "Tab", ""),
    "Escape": (27, "Escape", ""),
    "Backspace": (8, "Backspace", ""),
    "ArrowDown": (40, "ArrowDown", ""),
    "ArrowUp": (38, "ArrowUp", ""),
    "ArrowLeft": (37, "ArrowLeft", ""),
    "ArrowRight": (39, "ArrowRight", ""),
    "Space": (32, "Space", " "),
}
ACTIONS = (
    "click",
    "type",
    "press",
    "focus",
    "hover",
    "select",
    "check",
    "uncheck",
    "scroll",
    "scroll_into_view",
)
DEVICE_DOWNLOADS = "/sdcard/Download"
PAGE_TIMEOUT = 15.0


def manager(session: DeviceSession) -> BrowserManager:
    if session.browser is None:
        session.browser = BrowserManager(session)
    return session.browser


def normalize_url(url: str) -> str:
    if url.startswith(("http://", "https://", "about:", "data:", "file:", "javascript:")):
        return url
    return "https://" + url


def host_file(path: str) -> Path:
    p = Path(path).expanduser()
    if any(part.startswith(".") for part in p.parts[1:]) or not p.is_file():
        fail("not_permitted", f"{path} is not a readable regular file outside hidden directories")
    return p


async def bring_to_front(dev: DeviceSession, attached: Attached) -> None:
    """A backgrounded WebView renders no frames (screenshots hang), so foreground its app."""
    package = attached.target.package
    screen = await dev.capture()
    if screen.phone.package == package:
        return
    if package == SHELL_PACKAGE:
        await dev.shell(f"am start -n {SHELL_ACTIVITY}")
    else:
        await dev.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", check=False)
    for _ in range(20):
        await asyncio.sleep(0.4)
        if (await dev.capture()).phone.package == package:
            return


async def page_info(client) -> dict:
    return await client.evaluate(js.SNAPSHOT)


async def run_js(attached: Attached, expression: str, **kwargs):
    try:
        return await attached.client.evaluate(expression, **kwargs)
    except CdpError as exc:
        fail("unsupported", str(exc), "is the page still open? call browser_tabs")


def register(mcp: FastMCP, rt: Runtime) -> None:
    async def attach(device: Device, session_name: str, **kw) -> tuple[DeviceSession, Attached]:
        session = get_session(rt, device)
        await session.ensure_connected()
        try:
            return session, await manager(session).attach(session_name, **kw)
        except CdpError as exc:
            fail("device_unreachable", str(exc))

    @mcp.tool(tags={"write"})
    async def browser_open(
        url: str | None = None,
        session: str = "default",
        target_id: str | None = None,
        app: str | None = None,
        wait: bool = True,
        timeout: float = PAGE_TIMEOUT,
        device: Device = None,
    ) -> dict:
        """Open a URL in the on-device browser, or attach to an existing page (target_id from
        browser_tabs, or app=<package> for an in-app WebView). ``session`` names the attachment."""
        _, attached = await attach(device, session, target_id=target_id, app=app)
        if url:
            try:
                info = await navigate(attached.client, normalize_url(url), timeout, wait)
            except CdpError as exc:
                fail("unsupported", str(exc), "check the URL and the device's network")
        else:
            info = await page_info(attached.client)
        return {
            "ok": True,
            "session": session,
            "app": attached.target.package,
            "title": info.get("title"),
            "url": info.get("url"),
            "ready": info.get("ready"),
        }

    @mcp.tool(tags={"read"})
    async def browser_tabs(device: Device = None) -> dict:
        """Every open page across the on-device browser and in-app WebViews, plus attached sessions."""
        session = get_session(rt, device)
        await session.ensure_connected()
        mgr = manager(session)
        targets = await mgr.targets()
        return {
            "count": len(targets),
            "pages": [t.to_dict() for t in targets],
            "sessions": mgr.sessions(),
        }

    @mcp.tool(tags={"write"})
    async def browser_close(
        session: str = "default", close_app: bool = False, device: Device = None
    ) -> dict:
        """Detach a browser session (blanking the page); close_app also stops the browser app."""
        dev = get_session(rt, device)
        mgr = manager(dev)
        cached = mgr._attached.get(session)
        package = cached.target.package if cached else None
        if cached and not cached.client.closed:
            try:
                await cached.client.send("Page.navigate", {"url": "about:blank"}, timeout=5)
            except CdpError:
                pass
        detached = await mgr.detach(session)
        if close_app and package:
            await dev.shell(f"am force-stop {package}")
        return {"ok": True, "detached": detached, "closed_app": bool(close_app and package)}

    @mcp.tool(tags={"read"})
    async def browser_screenshot(
        session: str = "default", full_page: bool = False, device: Device = None
    ) -> Image:
        """Screenshot of the page content. The owning app is brought to the foreground first
        (a hidden WebView cannot render)."""
        dev, attached = await attach(device, session)
        await bring_to_front(dev, attached)
        params = {"format": "png", "captureBeyondViewport": full_page}
        try:
            result = await attached.client.send("Page.captureScreenshot", params, timeout=20)
        except CdpError as exc:
            fail("unsupported", str(exc))
        import base64

        return Image(data=base64.b64decode(result["data"]), format="png")

    @mcp.tool(tags={"read"})
    async def browser_read(
        session: str = "default",
        selector: str | None = None,
        max_chars: int = 6000,
        structure: bool = False,
        device: Device = None,
    ) -> dict:
        """Read the page: title, url and visible text; structure=true also lists interactive
        elements with refs (e1, e2...) for browser_act."""
        _, attached = await attach(device, session)
        data = await run_js(attached, js.page_text(selector, max_chars))
        if data is None:
            fail("element_not_found", f"no element matches {selector!r}")
        data["truncated"] = data["length"] > len(data["text"])
        if structure:
            data["elements"] = await run_js(attached, js.interactives(80))
        return data

    @mcp.tool(tags={"read"})
    async def browser_find(
        query: str, session: str = "default", limit: int = 10, device: Device = None
    ) -> dict:
        """Find visible elements by text, label, placeholder, alt or name; returns refs for browser_act."""
        _, attached = await attach(device, session)
        matches = await run_js(attached, js.find(query, min(max(limit, 1), 50)))
        return {"query": query, "count": len(matches), "matches": matches}

    @mcp.tool(tags={"read"})
    async def browser_wait(
        text: str | None = None,
        selector: str | None = None,
        url_contains: str | None = None,
        timeout: float = PAGE_TIMEOUT,
        session: str = "default",
        device: Device = None,
    ) -> dict:
        """Wait until the page has text, a selector and/or a URL fragment (or just finished loading)."""
        _, attached = await attach(device, session)
        condition = js.wait_condition(text, selector, url_contains)
        started = time.monotonic()
        while True:
            try:
                if await attached.client.evaluate(condition):
                    break
            except CdpError:
                pass
            if time.monotonic() - started >= min(timeout, 120.0):
                return {"ok": False, "elapsed": round(time.monotonic() - started, 2)}
            await asyncio.sleep(0.4)
        return {
            "ok": True,
            "elapsed": round(time.monotonic() - started, 2),
            **await page_info(attached.client),
        }

    @mcp.tool(tags={"read"})
    async def browser_extract(
        kind: str = "table",
        selector: str | None = None,
        limit: int = 20,
        session: str = "default",
        device: Device = None,
    ) -> dict:
        """Pull structured data: kind = table (headers + row dicts) | links | text."""
        if kind not in ("table", "links", "text"):
            fail("invalid_argument", "kind must be table, links or text")
        _, attached = await attach(device, session)
        if kind == "text":
            data = await run_js(attached, js.page_text(selector, 20000))
        else:
            builder = js.tables if kind == "table" else js.links
            data = await run_js(attached, builder(selector, min(max(limit, 1), 200)))
        if data is None:
            fail("element_not_found", f"no element matches {selector!r}")
        return {"kind": kind, "data": data}

    async def press_key(attached: Attached, key: str) -> None:
        if key not in KEYS:
            fail("invalid_argument", f"key must be one of {', '.join(KEYS)}")
        code, name, text = KEYS[key]
        base = {
            "key": key,
            "code": name,
            "windowsVirtualKeyCode": code,
            "nativeVirtualKeyCode": code,
        }
        await attached.client.send(
            "Input.dispatchKeyEvent",
            {**base, "type": "keyDown", **({"text": text} if text else {})},
        )
        await attached.client.send("Input.dispatchKeyEvent", {**base, "type": "keyUp"})

    @mcp.tool(tags={"write"})
    async def browser_act(
        action: str,
        ref: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        value: str | None = None,
        key: str | None = None,
        clear: bool = False,
        submit: bool = False,
        amount: int = 600,
        session: str = "default",
        device: Device = None,
    ) -> dict:
        """Act on the page. Actions: click, type(text, clear, submit), press(key), focus, hover,
        select(value), check/uncheck, scroll(amount px, negative = up), scroll_into_view.
        Target an element with ref (from browser_find/browser_read) or a CSS selector."""
        if action not in ACTIONS:
            fail("invalid_argument", f"action must be one of {', '.join(ACTIONS)}")
        _, attached = await attach(device, session)
        client = attached.client
        if action == "scroll":
            await run_js(attached, f"window.scrollBy(0, {int(amount)})")
            return {"ok": True, "action": action, **await page_info(client)}
        if action == "press":
            if not key:
                fail("invalid_argument", "press needs key")
            await press_key(attached, key)
            return {"ok": True, "action": action, "key": key, **await page_info(client)}
        if not ref and not selector:
            fail("invalid_argument", "give ref or selector")
        if action == "type":
            if text is None:
                fail("invalid_argument", "type needs text")
            prep = await run_js(attached, js.act("prepare_type", ref, selector, None, clear))
            if prep.get("error"):
                fail("element_not_found", prep["error"])
            enforce(
                check_text(
                    rt.config.policy, text, prep.get("hint", ""), prep.get("type") == "password"
                )
            )
            await client.send("Input.insertText", {"text": text})
            if submit:
                await press_key(attached, "Enter")
            return {"ok": True, "action": action, "chars": len(text), **await page_info(client)}
        js_action = "check" if action in ("check", "uncheck") else action
        val = "false" if action == "uncheck" else value
        result = await run_js(attached, js.act(js_action, ref, selector, val))
        if result.get("error"):
            fail(
                "element_not_found",
                result["error"],
                "refs go stale after navigation; call browser_find again",
            )
        await asyncio.sleep(0.2)
        return {"ok": True, "action": action, **result, **await page_info(client)}

    @mcp.tool(tags={"write"})
    async def browser_handoff(
        message: str = "", session: str = "default", device: Device = None
    ) -> dict:
        """Bring the browser to the foreground so a person can finish a login or captcha by hand;
        call browser_read afterwards to continue."""
        dev = get_session(rt, device)
        await dev.ensure_connected()
        cached = manager(dev)._attached.get(session)
        if cached is None:
            _, cached = await attach(device, session)
        await bring_to_front(dev, cached)
        await asyncio.sleep(0.5)
        screen = await dev.capture()
        return {
            "ok": True,
            "message": message or "control handed to a person",
            "foreground": screen.phone.package,
            "next": "the person can now act on the device (e.g. via scrcpy); call browser_read when done",
        }

    @mcp.tool(tags={"write"})
    async def browser_upload(
        path: str,
        ref: str | None = None,
        selector: str | None = None,
        session: str = "default",
        device: Device = None,
    ) -> dict:
        """Attach a file to an <input type=file>. ``path`` is a device path under /sdcard, or a host
        file (not inside a hidden directory) that is pushed to /sdcard/Download first."""
        if not ref and not selector:
            fail("invalid_argument", "give ref or selector of the file input")
        dev, attached = await attach(device, session)
        if path.startswith("/sdcard/"):
            from .files import safe_path

            device_path = safe_path(path)
        else:
            local = host_file(path)
            device_path = f"{DEVICE_DOWNLOADS}/{local.name}"
            await dev.adb.push(str(local), device_path)
        client = attached.client
        try:
            evaluated = await client.send(
                "Runtime.evaluate",
                {"expression": js.file_input(ref, selector), "returnByValue": False},
            )
            object_id = (evaluated.get("result") or {}).get("objectId")
            if not object_id:
                fail("element_not_found", "file input not found")
            node = await client.send("DOM.describeNode", {"objectId": object_id})
            await client.send(
                "DOM.setFileInputFiles",
                {"files": [device_path], "backendNodeId": node["node"]["backendNodeId"]},
            )
        except CdpError as exc:
            fail("unsupported", str(exc))
        return {"ok": True, "device_path": device_path}
