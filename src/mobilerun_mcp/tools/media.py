"""Playback and volume control through ``cmd media_session``."""

from __future__ import annotations

from fastmcp import FastMCP

from ..errors import fail
from ..parsers.media import parse_media_sessions, parse_stream_block, parse_volume_get
from ..session import DeviceSession, Runtime
from .common import Device, get_session

MUSIC = 3
DISPATCH_KEYS = {
    "play": "play",
    "pause": "pause",
    "play_pause": "play-pause",
    "stop": "stop",
    "next": "next",
    "previous": "previous",
    "rewind": "rewind",
    "fast_forward": "fast-forword",  # sic: that is the spelling the platform accepts
}
DEFAULT_UNMUTE = 5


async def read_volume(session: DeviceSession) -> dict:
    out = await session.shell(f"cmd media_session volume --get --stream {MUSIC}", check=False)
    got = parse_volume_get(out)
    audio = parse_stream_block(
        await session.shell("dumpsys audio | grep -A8 -E '^- STREAM_MUSIC'", check=False)
    )
    return {
        "volume": got[0] if got else audio.get("volume"),
        "min": got[1] if got else audio.get("min"),
        "max": got[2] if got else audio.get("max"),
        "muted": audio.get("muted", False),
    }


def register(mcp: FastMCP, rt: Runtime) -> None:
    premute: dict[str, int] = {}

    @mcp.tool(tags={"read"})
    async def get_media_sessions(include_system: bool = False, device: Device = None) -> dict:
        """Active media sessions (app, playback state, title/artist) and the music volume."""
        session = get_session(rt, device)
        dump = await session.shell("dumpsys media_session")
        sessions = parse_media_sessions(dump, include_system)
        return {
            "count": len(sessions),
            "sessions": [s.to_dict() for s in sessions],
            "volume": await read_volume(session),
        }

    @mcp.tool(tags={"write"})
    async def media_control(action: str, device: Device = None) -> dict:
        """Send a media key: play, pause, play_pause, stop, next, previous, rewind, fast_forward."""
        if action not in DISPATCH_KEYS:
            fail("invalid_argument", f"action must be one of {', '.join(DISPATCH_KEYS)}")
        session = get_session(rt, device)
        await session.shell(f"cmd media_session dispatch {DISPATCH_KEYS[action]}")
        sessions = parse_media_sessions(await session.shell("dumpsys media_session"))
        return {"ok": True, "action": action, "sessions": [s.to_dict() for s in sessions]}

    async def adjust(session: DeviceSession, direction: str, steps: int) -> dict:
        for _ in range(max(1, min(steps, 15))):
            await session.shell(f"cmd media_session volume --stream {MUSIC} --adj {direction}")
        return {"ok": True, **await read_volume(session)}

    @mcp.tool(tags={"write"})
    async def volume_up(steps: int = 1, device: Device = None) -> dict:
        """Raise the music volume by ``steps``."""
        return await adjust(get_session(rt, device), "raise", steps)

    @mcp.tool(tags={"write"})
    async def volume_down(steps: int = 1, device: Device = None) -> dict:
        """Lower the music volume by ``steps``."""
        return await adjust(get_session(rt, device), "lower", steps)

    @mcp.tool(tags={"write"})
    async def mute(muted: bool = True, device: Device = None) -> dict:
        """Mute (volume 0, previous level remembered) or unmute the music stream."""
        session = get_session(rt, device)
        current = await read_volume(session)
        if muted:
            if current["volume"]:
                premute[session.serial] = int(current["volume"])
            level = 0
        else:
            level = (
                premute.pop(session.serial, DEFAULT_UNMUTE)
                if not current["volume"]
                else int(current["volume"])
            )
        await session.shell(f"cmd media_session volume --stream {MUSIC} --set {level}")
        return {"ok": True, "muted": muted, **await read_volume(session)}
