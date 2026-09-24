"""Device status and connection management."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from .. import adb as adb_mod
from ..errors import fail
from ..parsers import system as sysparse
from ..parsers.media import parse_stream_block
from ..session import Runtime
from .common import Device, get_session


def register(mcp: FastMCP, rt: Runtime) -> None:
    @mcp.tool(tags={"read"})
    async def get_device_status(device: Device = None) -> dict:
        """Battery, screen power, foreground app, size, storage, network addresses, volume."""
        session = get_session(rt, device)
        (battery, size, density, wake, focus, storage, ip, props, audio) = await asyncio.gather(
            session.shell("dumpsys battery"),
            session.shell("wm size"),
            session.shell("wm density"),
            session.shell("dumpsys power | grep -E 'mWakefulness='"),
            session.shell("dumpsys window | grep -E 'mCurrentFocus'"),
            session.shell("df /data"),
            session.shell("ip -4 addr show"),
            session.shell(
                "getprop | grep -E 'ro.product.model|ro.build.version.release|ro.build.version.sdk|ro.product.cpu.abi\\]'"
            ),
            session.shell("dumpsys audio | grep -A8 -E '^- STREAM_MUSIC'"),
        )
        prop = sysparse.parse_getprop(props)
        foreground = sysparse.parse_focus(focus)
        return {
            "serial": session.serial,
            "model": prop.get("ro.product.model"),
            "android": prop.get("ro.build.version.release"),
            "sdk": prop.get("ro.build.version.sdk"),
            "abi": prop.get("ro.product.cpu.abi"),
            "screen": {
                "size": sysparse.parse_wm_size(size),
                "density": sysparse.parse_wm_density(density),
                "power": sysparse.parse_wakefulness(wake),
            },
            "foreground": {"package": foreground[0], "activity": foreground[1]}
            if foreground
            else None,
            "battery": sysparse.parse_battery(battery),
            "storage": sysparse.parse_df(storage),
            "addresses": sysparse.parse_ip_addresses(ip),
            "music_volume": parse_stream_block(audio),
            "portal_transport": session.portal.transport,
        }

    @mcp.tool(tags={"read"})
    async def list_devices() -> dict:
        """Devices adb can see (serial and state)."""
        try:
            devices = await adb_mod.list_devices(rt.config.adb_bin)
        except adb_mod.AdbError as exc:
            fail("device_unreachable", str(exc))
        return {"count": len(devices), "devices": devices, "default": rt.config.device or None}

    @mcp.tool(tags={"read"})
    async def ping_device(device: Device = None) -> dict:
        """Is the Mobilerun Portal reachable? Returns its transport (http or content_provider)."""
        session = get_session(rt, device)
        await session.ensure_connected()
        try:
            return {"ok": True, "serial": session.serial, **await session.portal.ping()}
        except Exception as exc:  # portal errors surface as a clear failure, not a stack trace
            fail("device_unreachable", f"{session.serial}: {exc}")

    @mcp.tool(tags={"write"})
    async def connect_device(device: Device = None) -> dict:
        """(Re)connect adb and the Portal for a device; use after the network path came back."""
        session = get_session(rt, device)
        session._connected = False
        await session.ensure_connected()
        return {"ok": True, "serial": session.serial, "portal_transport": session.portal.transport}

    @mcp.tool(tags={"read"})
    async def request_screen_capture_permission(device: Device = None) -> dict:
        """Compatibility no-op: screenshots use the Portal / adb screencap, no prompt is needed."""
        return {"needed": False, "message": "screen capture works over adb; nothing to approve"}

    @mcp.tool(tags={"read"})
    async def echo(message: str = "") -> dict:
        """Connectivity check for the MCP server itself (does not touch the device)."""
        return {"echo": message, "policy": rt.config.policy, "scopes": sorted(rt.config.scopes)}
