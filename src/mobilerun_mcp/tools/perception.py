"""Seeing the screen: numbered marks, annotated screenshots, the raw tree, plain screenshots."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from .. import ocr as ocr_mod
from ..annotate import annotate
from ..errors import fail
from ..marks import format_marks
from ..models import Screen
from ..session import DeviceSession, Runtime
from .common import Device, get_session

SPARSE_INTERACTIVE = 3
TREE_LIMIT = 300
SHOT_DIR = Path(tempfile.gettempdir()) / "mobilerun-mcp"


def render_tree(screen: Screen, max_depth: int = 8, limit: int = TREE_LIMIT) -> str:
    """Indented text tree: only visible nodes that carry a label, are interactive, or scroll."""
    keep = [
        e
        for e in screen.elements
        if e.visible and e.depth <= max_depth and (e.label or e.interactive or e.scrollable)
    ]
    lines = []
    for e in keep[:limit]:
        flags = "".join(
            f
            for f, on in (
                ("C", e.clickable),
                ("L", e.long_clickable),
                ("E", e.editable),
                ("S", e.scrollable),
                ("K", e.checkable),
                ("P", e.password),
            )
            if on
        )
        label = f' "{e.label[:60]}"' if e.label else ""
        ident = f" #{e.short_id}" if e.short_id else ""
        b = e.bounds
        lines.append(
            f"{'  ' * e.depth}{e.class_name}{ident}{label} [{flags}] ({b.left},{b.top},{b.right},{b.bottom})"
        )
    if len(keep) > limit:
        lines.append(
            f"... {len(keep) - limit} more nodes (raise max_depth filter or use perceive_screen)"
        )
    return "\n".join(lines)


def screen_header(screen: Screen) -> dict:
    return {
        "foreground_app": screen.phone.app or screen.phone.package,
        "package": screen.phone.package,
        "activity": screen.phone.activity.rsplit(".", 1)[-1],
        "keyboard_visible": screen.phone.keyboard_visible,
        "screen_size": [screen.width, screen.height],
    }


async def perceive(
    session: DeviceSession, include_image: bool, ocr_mode: str, max_marks: int, lang: str
) -> tuple[dict, bytes | None]:
    screen, marks = await session.perceive(max_marks)
    interactive = sum(1 for m in marks if m.kind != "text")
    use_ocr = ocr_mod.available() and (
        ocr_mode == "always" or (ocr_mode == "auto" and interactive < SPARSE_INTERACTIVE)
    )
    png = await session.screenshot() if (include_image or use_ocr) else None
    if use_ocr and png is not None:
        lines = await ocr_mod.run_tesseract(png, lang)
        marks = ocr_mod.merge_ocr_marks(marks, lines)
        session.marks = marks
    result = {
        **screen_header(screen),
        "mark_count": len(marks),
        "ocr_used": bool(use_ocr),
        "elements": format_marks(marks),
    }
    if include_image and png is not None:
        return result, annotate(png, marks)
    return result, None


def register(mcp: FastMCP, rt: Runtime) -> None:
    @mcp.tool(tags={"read"})
    async def perceive_screen(
        include_image: bool = True,
        ocr: str = "auto",
        max_marks: int = 150,
        lang: str = "eng",
        device: Device = None,
    ) -> list:
        """Capture the screen: numbered marks (som_id) for every tappable/readable item plus an
        annotated screenshot. ids are single-use and go stale after any action. ocr = auto
        (only when the accessibility tree is sparse) | always | never."""
        if ocr not in ("auto", "always", "never"):
            fail("invalid_argument", "ocr must be auto, always or never")
        result, image_png = await perceive(
            get_session(rt, device), include_image, ocr, max_marks, lang
        )
        import json

        parts: list = [json.dumps(result, ensure_ascii=False)]
        if image_png is not None:
            parts.append(Image(data=image_png, format="png"))
        return parts

    @mcp.tool(tags={"read"})
    async def read_screen(device: Device = None) -> dict:
        """Text-only view of the screen (no image): foreground app plus numbered elements."""
        result, _ = await perceive(get_session(rt, device), False, "never", 150, "eng")
        return result

    @mcp.tool(tags={"read"})
    async def get_ui_tree(max_depth: int = 8, device: Device = None) -> dict:
        """Compact accessibility tree (class, id, label, flags C/L/E/S/K/P, bounds)."""
        screen = await get_session(rt, device).capture()
        return {**screen_header(screen), "tree": render_tree(screen, max_depth)}

    @mcp.tool(tags={"read"})
    async def get_screenshot(device: Device = None) -> Image:
        """Plain screenshot as an image."""
        return Image(data=await get_session(rt, device).screenshot(), format="png")

    @mcp.tool(tags={"read"})
    async def screenshot(device: Device = None) -> Image:
        """Alias of get_screenshot (kept for existing clients)."""
        return Image(data=await get_session(rt, device).screenshot(), format="png")

    @mcp.tool(tags={"read"})
    async def screenshot_path(device: Device = None) -> dict:
        """Take a screenshot, save it as a PNG file and return the path."""
        png = await get_session(rt, device).screenshot()
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"screen-{int(time.time() * 1000)}.png"
        path.write_bytes(png)
        return {"path": str(path), "bytes": len(png)}
