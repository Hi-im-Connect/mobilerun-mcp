"""Finding and opening files on the device's shared storage."""

from __future__ import annotations

import mimetypes
import posixpath

from fastmcp import FastMCP

from ..errors import fail
from ..observe import mutate
from ..parsers.system import parse_find
from ..session import Runtime
from ..shell import q
from .apps import start_intent
from .common import Device, get_session

ALLOWED_ROOTS = ("/sdcard", "/storage/emulated/0", "/data/local/tmp", "/mnt/sdcard")


def safe_path(path: str) -> str:
    clean = posixpath.normpath(path)
    if ".." in path.split("/") or not any(
        clean == r or clean.startswith(r + "/") for r in ALLOWED_ROOTS
    ):
        fail(
            "not_permitted",
            f"{path} is outside shared storage",
            f"allowed roots: {', '.join(ALLOWED_ROOTS)}",
        )
    return clean


def register(mcp: FastMCP, rt: Runtime) -> None:
    @mcp.tool(tags={"read"})
    async def find_files(
        query: str = "",
        path: str = "/sdcard",
        limit: int = 50,
        max_depth: int = 6,
        device: Device = None,
    ) -> dict:
        """Find files under shared storage whose name contains ``query`` (empty lists everything)."""
        root = safe_path(path)
        session = get_session(rt, device)
        cmd = (
            f"find -L {q(root)} -maxdepth {int(max_depth)} -iname {q('*' + query + '*')} "
            f"2>/dev/null | head -n {int(limit)}"
        )
        found = parse_find(await session.shell(cmd, timeout=60))
        return {"root": root, "count": len(found), "files": found}

    @mcp.tool(tags={"write"})
    async def open_file(path: str, device: Device = None) -> dict:
        """Open a file in whichever app handles its type."""
        clean = safe_path(path)
        session = get_session(rt, device)
        mime = mimetypes.guess_type(clean)[0] or "*/*"
        args = f"-a android.intent.action.VIEW -d {q('file://' + clean)} -t {q(mime)}"
        return await mutate(
            session,
            lambda: start_intent(session, args),
            {"action": "open_file", "path": clean, "mime": mime},
            settle_timeout=15.0,
            expect_change=True,
        )
