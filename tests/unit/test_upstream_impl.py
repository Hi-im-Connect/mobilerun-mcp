"""Unit tests for the pieces added for upstream parity."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from mobilerun_mcp import agentui, detect, grid
from mobilerun_mcp.marks import build_marks
from mobilerun_mcp.parsers.a11y import generic_bounds, parse_any_state, parse_screen
from mobilerun_mcp.parsers.shortcuts import parse_shortcuts, split_uri
from mobilerun_mcp.parsers.system import media_uri, parse_content_rows
from mobilerun_mcp.targets import resolve_target
from mobilerun_mcp.websearch import tavily_search

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def node(cls, text="", bounds=(0, 0, 100, 100), children=(), **flags):
    left, top, right, bottom = bounds
    return {
        "className": f"android.widget.{cls}",
        "text": text,
        "resourceId": flags.pop("rid", ""),
        "boundsInScreen": {"left": left, "top": top, "right": right, "bottom": bottom},
        "children": list(children),
        **flags,
    }


STATE = {
    "a11y_tree": node(
        "FrameLayout",
        bounds=(0, 0, 720, 1280),
        children=[
            node("Button", "OK", (10, 10, 200, 80), isClickable=True, drawingOrder=1, windowId=1),
            node("View", "", (100, 20, 300, 70), isClickable=True, drawingOrder=2, windowId=1),
            node("EditText", "", (10, 100, 700, 160), isEditable=True, isFocused=True),
            node(
                "Switch",
                "Wi-Fi",
                (10, 200, 700, 260),
                isCheckable=True,
                isChecked=True,
                isClickable=True,
            ),
            node("TextView", "gone", (0, 2000, 100, 2100)),
            node("TextView", "tiny", (0, 300, 3, 303)),
        ],
    ),
    "phone_state": {"packageName": "com.x", "currentApp": "X", "isEditable": True},
    "device_context": {"screen_bounds": {"width": 720, "height": 1280}},
}


# ---- mobilerun agent indexing ---------------------------------------------------------------
def test_index_state_numbers_like_mobilerun_and_filters_offscreen_and_tiny():
    text, elements = agentui.index_state(STATE)
    labels = [e["text"] for e in elements]
    assert [e["index"] for e in elements] == list(range(1, len(elements) + 1))
    assert "gone" not in labels and "tiny" not in labels
    assert "Current Clickable UI elements" in text and "**App:** X (com.x)" in text
    switch = next(e for e in elements if e["text"] == "Wi-Fi")
    assert switch["checkedState"] == "isChecked=True"


def test_tap_point_avoids_sibling_drawn_on_top():
    _, elements = agentui.index_state(STATE)
    ok = next(e for e in elements if e["text"] == "OK")
    assert ok["tapBlockers"] == ["100,20,300,70"]
    x, y = agentui.element_coords(elements, ok["index"], (720, 1280))
    assert not (100 <= x < 300 and 20 <= y < 70) and 10 <= x < 200
    with pytest.raises(ValueError, match="No element found with index 99"):
        agentui.element_coords(elements, 99)


def test_find_uncovered_point_none_when_fully_covered():
    assert agentui.find_uncovered_point((0, 0, 10, 10), [(0, 0, 10, 10)]) is None


# ---- AURA grid and entries ------------------------------------------------------------------
def test_render_grid_header_table_flags_and_escalate():
    screen = parse_screen(STATE)
    marks = build_marks(screen)
    text = grid.render_grid(screen, marks, idle=True, settled=True)
    assert text.startswith("SCREEN 720x1280  com.x  IDLE")
    assert "som  in   flg" in text
    assert "ESCALATE" not in text
    sparse = grid.render_grid(screen, marks[:2], idle=True, settled=True)
    assert "ESCALATE to perceive_screen" in sparse  # fewer than 3 elements
    flags = {line.split()[2] for line in text.splitlines()[5:] if line[:1].isdigit()}
    assert {"*", "e", "c"} & flags
    busy = grid.render_grid(screen, marks, idle=False, settled=False)
    assert "BUSY (still moving" in busy


def test_element_entries_match_marks_and_flag_input_and_toggle():
    screen = parse_screen(STATE)
    marks = build_marks(screen)
    entries = grid.element_entries(screen, marks)
    assert len(entries) == len(marks)
    by_label = {e[2]: e for e in entries if len(e) > 2}
    assert "c" in by_label["Wi-Fi"][3]
    assert any(len(e) == 4 and "e" in e[3] and "f" in e[3] for e in entries)


def test_offscreen_text_lists_labels_outside_the_screen():
    assert grid.offscreen_text(parse_screen(STATE)) == ["gone"]


# ---- launcher shortcuts ---------------------------------------------------------------------
def test_parse_shortcuts_from_dumpsys_fixture():
    items = parse_shortcuts((FIXTURES / "dumpsys_shortcut.txt").read_text())
    wifi = next(s for s in items if s.id == "manifest-shortcut-wifi")
    assert wifi.uri == "app-shortcut://com.android.settings/manifest-shortcut-wifi"
    assert wifi.am_args() == "-a 'android.settings.WIFI_SETTINGS' -p 'com.android.settings'"
    contact = next(s for s in items if s.id == "shortcut-add-contact")
    assert contact.component.endswith("ContactEditorActivity") and "-d " in contact.am_args()
    elided = next(s for s in items if s.data.endswith("/..."))
    assert elided.truncated and "-d " not in elided.am_args()
    assert parse_shortcuts((FIXTURES / "dumpsys_shortcut.txt").read_text(), "com.twitter.android")
    assert split_uri("app-shortcut://pkg/a/b") == ("pkg", "a/b") and split_uri("https://x") is None


# ---- content rows / media URIs --------------------------------------------------------------
def test_parse_content_rows_keeps_commas_inside_values():
    raw = "Row: 0 _id=5, _display_name=a, b.jpg, media_type=1, _data=/sdcard/a, b.jpg\n"
    (row,) = parse_content_rows(raw, ("_id", "_display_name", "media_type", "_data"))
    assert row["_display_name"] == "a, b.jpg" and row["_data"] == "/sdcard/a, b.jpg"
    assert media_uri(row) == "content://media/external/images/media/5"
    assert media_uri({"_id": "9", "media_type": "0"}) == "content://media/external/file/9"


# ---- detector post-processing ---------------------------------------------------------------
def test_detector_decode_scales_back_and_suppresses_overlaps():
    pred = np.zeros((1, 5, 3), dtype=np.float32)
    pred[0, :, 0] = [320, 320, 100, 100, 0.9]
    pred[0, :, 1] = [322, 321, 100, 100, 0.8]  # overlaps the first: suppressed
    pred[0, :, 2] = [100, 100, 40, 40, 0.1]  # below confidence
    boxes = detect.decode(pred, scale=0.5, px=0, py=0, size=(1280, 1280))
    assert len(boxes) == 1
    b = boxes[0]
    assert (b.left, b.top, b.right, b.bottom) == (540, 540, 740, 740) and b.score == pytest.approx(
        0.9
    )


# ---- generic trees / targets ---------------------------------------------------------------
def test_generic_bounds_and_ios_tree():
    assert generic_bounds({"x": 1, "y": 2, "width": 3, "height": 4}).as_list() == [1, 2, 4, 6]
    assert generic_bounds("[0,0][10,20]").as_list() == [0, 0, 10, 20]
    tree = {
        "type": "XCUIElementTypeApplication",
        "frame": {"x": 0, "y": 0, "width": 390, "height": 844},
        "children": [
            {
                "type": "XCUIElementTypeButton",
                "label": "Go",
                "frame": {"x": 10, "y": 10, "width": 80, "height": 40},
            }
        ],
    }
    screen = parse_any_state({"a11y_tree": tree, "phone_state": {"bundleId": "com.apple.x"}})
    button = screen.elements[1]
    assert button.clickable and button.label == "Go" and screen.phone.package == "com.apple.x"


def test_resolve_target_grammar():
    assert resolve_target("emulator-5554").kind == "android-adb"
    assert resolve_target("cloud:abc").kind == "cloud"
    assert resolve_target("ios", {}).url == "http://127.0.0.1:6643"
    assert resolve_target("http://10.0.0.2:8080", {}).kind == "android-http"


# ---- Tavily ---------------------------------------------------------------------------------
def test_tavily_search_answer_and_results():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["include_answer"] and body["topic"] == "news"
        assert request.headers["Authorization"] == "Bearer tv-key"
        return httpx.Response(
            200,
            json={
                "answer": "Tap Settings.",
                "results": [{"title": "T", "url": "https://u", "content": "snippet"}],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tavily_search("q", 3, "news", "tv-key", client)

    out = asyncio.run(run())
    assert out["answer"] == "Tap Settings." and out["results"][0]["snippet"] == "snippet"


# ---- fast connection ------------------------------------------------------------------------
class FakeSession:
    serial = "fake:1"

    def __init__(self):
        self.calls = []

    async def raw_state(self):
        return {"a11y_tree": STATE["a11y_tree"], "phone_state": {"packageName": "com.x"}}

    async def screenshot(self):
        return b"PNG"

    async def tap_xy(self, x, y):
        self.calls.append(("tap", x, y))

    async def swipe_xy(self, *args):
        self.calls.append(("swipe", *args))

    async def input_text(self, text, clear=False):
        self.calls.append(("type", text, clear))

    async def shell(self, command, check=True):
        self.calls.append(("shell", command))
        return ""


def test_fast_connection_routes_hot_paths_through_the_session():
    from mobilerun_core import Device

    from mobilerun_mcp.fastconn import FastAndroidConnection, allow_all

    async def run():
        loop = asyncio.get_running_loop()
        session = FakeSession()
        device = Device(FastAndroidConnection(session, loop), allow_all)

        def work():
            device.tap(5, 6, stealth=False)
            device.swipe(1, 2, 3, 4, ms=100)
            device.type("hi", stealth=False)
            device.key("back")
            device.grant_permission("com.x", "android.permission.CAMERA")
            device.open_deep_link("https://x.y", package_name="com.x")
            return device.ui(), device.screenshot(), device.capabilities

        ui, shot, caps = await asyncio.to_thread(work)
        return session.calls, ui, shot, caps

    calls, ui, shot, caps = asyncio.run(run())
    assert ("tap", 5, 6) in calls and ("swipe", 1, 2, 3, 4, 100) in calls
    assert ("type", "hi", False) in calls and ("shell", "input keyevent 4") in calls
    assert ("shell", "pm grant 'com.x' 'android.permission.CAMERA'") in calls
    assert any(c[0] == "shell" and "am start" in c[1] and "-p 'com.x'" in c[1] for c in calls)
    assert "a11y_tree" in ui and base64.b64decode(shot) == b"PNG"
    assert {"execute_script", "get_clipboard"} <= set(caps["actions"])


# ---- credentials and local tasks -----------------------------------------------------------
def test_load_secrets_supports_both_mobilerun_formats(tmp_path):
    from mobilerun_mcp.tools.agent import load_secrets

    file = tmp_path / "credentials.yaml"
    file.write_text(
        "secrets:\n  A: {value: one, enabled: true}\n  B: two\n  C: {value: three, enabled: false}\n"
    )
    assert load_secrets(str(file)) == {"A": "one", "B": "two"}


def test_local_run_task_lifecycle(tmp_path):
    from fastmcp import Client

    from mobilerun_mcp.config import Config
    from mobilerun_mcp.server import build_server

    fake = tmp_path / "mobilerun"
    fake.write_text("#!/bin/sh\necho step one\necho 'Goal achieved: opened it'\n")
    fake.chmod(0o755)

    async def run():
        cfg = Config(device="x:1", mobilerun_bin=str(fake))
        async with Client(build_server(cfg)) as client:
            done = (await client.call_tool("run_task", {"task": "open it"})).structured_content
            task_id = done["taskId"]
            summary = (await client.call_tool("get_task", {"taskId": task_id})).structured_content
            listed = (await client.call_tool("list_tasks", {"scope": "local"})).structured_content
            status = (
                await client.call_tool("get_task", {"taskId": task_id, "view": "status"})
            ).structured_content
            return done, summary, listed, status

    done, summary, listed, status = asyncio.run(run())
    assert done["ok"] and done["result"] == "Goal achieved: opened it"
    assert summary["succeeded"] and "step one" in summary["output_tail"]
    assert listed["local"][0]["id"] == done["taskId"] and status["status"] == "completed"
