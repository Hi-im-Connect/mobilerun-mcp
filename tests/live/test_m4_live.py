import asyncio
from urllib.parse import quote

import pytest
from fastmcp import Client

from mobilerun_mcp.config import Config
from mobilerun_mcp.server import build_server

from .conftest import DEVICE, Phone

pytestmark = pytest.mark.live

PAGE = """<!doctype html><html><head><title>MCP Test Page</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>
<h1 id="h">Hello Browser</h1>
<p id="count">clicks: 0</p>
<button id="btn" onclick="window.n=(window.n||0)+1;document.getElementById('count').innerText='clicks: '+window.n">Increment</button>
<input id="name" placeholder="Your name">
<select id="sel"><option value="a">Alpha</option><option value="b">Beta</option></select>
<label><input type="checkbox" id="chk"> Accept terms</label>
<a href="#next" id="lnk">Go next</a>
<table id="t"><thead><tr><th>Name</th><th>Qty</th></tr></thead>
<tbody><tr><td>Apple</td><td>3</td></tr><tr><td>Pear</td><td>5</td></tr></tbody></table>
<input type="file" id="file">
<div id="dbl" style="padding:40px;background:#ddd" ondblclick="window.d=(window.d||0)+1;document.title='dbl:'+window.d">Double-click me</div>
<input type="password" id="pw" placeholder="Password">
<div style="height:1500px">tall</div>
</body></html>"""
URL = "data:text/html;charset=utf-8," + quote(PAGE)


async def open_page(phone, **kw):
    return await phone.call("browser_open", url=URL, **kw)


async def ref_of(phone, query):
    return (await phone.call("browser_find", query=query))["matches"][0]["el_id"]


async def test_open_reads_title_text_and_structure(phone):
    opened = await open_page(phone)
    assert opened["ok"] and opened["title"] == "MCP Test Page" and opened["ready"] == "complete"
    page = await phone.call("browser_read", structure=True)
    assert "Hello Browser" in page["text"] and not page["truncated"]
    tags = {(e["tag"], e["text"]) for e in page["elements"]}
    assert ("button", "Increment") in tags and ("a", "Go next") in tags
    assert all(isinstance(e["el_id"], int) for e in page["elements"])
    limited = await phone.call("browser_read", max_chars=5)
    assert limited["truncated"] and len(limited["text"]) == 5
    part = await phone.call("browser_read", selector="#count")
    assert part["text"] == "clicks: 0"
    assert "element_not_found" in await phone.call_error("browser_read", selector="#nope")


async def test_tabs_lists_the_page_and_session(phone):
    await open_page(phone)
    tabs = await phone.call("browser_tabs")
    assert any(
        p["title"] == "MCP Test Page" and p["app"] == "org.chromium.webview_shell"
        for p in tabs["all_pages"]
    )
    assert "scratch" in tabs["sessions"]


async def test_find_click_updates_the_page(phone):
    await open_page(phone)
    found = (await phone.call("browser_find", query="increment"))["matches"][0]
    assert found["tag"] == "button" and found["rect"][2] > 0
    clicked = await phone.call("browser_act", action="click", el_id=found["el_id"])
    assert clicked["ok"]
    await phone.call("browser_act", action="click", selector="#btn")
    assert (await phone.call("browser_read", selector="#count"))["text"] == "clicks: 2"


async def test_type_select_check_and_press(phone):
    await open_page(phone)
    typed = await phone.call("browser_act", action="type", selector="#name", text="Alice")
    assert typed["chars"] == 5
    await phone.call("browser_act", action="type", selector="#name", text=" Smith")
    replaced = await phone.call(
        "browser_act", action="type", selector="#name", text="Only", clear=True
    )
    assert replaced["ok"]
    field = (await phone.call("browser_find", query="Your name"))["matches"][0]
    assert field["value"] == "Only"
    assert (await phone.call("browser_act", action="select", selector="#sel", value="Beta"))[
        "value"
    ] == "b"
    assert (await phone.call("browser_act", action="check", selector="#chk"))["checked"] is True
    assert (await phone.call("browser_act", action="uncheck", selector="#chk"))["checked"] is False
    assert (await phone.call("browser_act", action="press", key="Tab"))["ok"]
    assert (await phone.call("browser_act", action="scroll", amount=400))["ok"]
    assert "invalid_argument" in await phone.call_error("browser_act", action="press", key="F13")
    assert "invalid_argument" in await phone.call_error(
        "browser_act", action="explode", selector="#x"
    )
    assert "element_not_found" in await phone.call_error("browser_act", action="click", ref="e999")


async def test_extract_table_links_and_text(phone):
    await open_page(phone)
    (table,) = (await phone.call("browser_extract", kind="table"))["data"]
    assert table["headers"] == ["Name", "Qty"]
    assert table["rows"] == [{"Name": "Apple", "Qty": "3"}, {"Name": "Pear", "Qty": "5"}]
    links = (await phone.call("browser_extract", kind="links"))["data"]
    assert links[0]["text"] == "Go next" and links[0]["href"].endswith("#next")
    text = (await phone.call("browser_extract", kind="text", selector="#h"))["data"]
    assert text["text"] == "Hello Browser"
    assert "invalid_argument" in await phone.call_error("browser_extract", kind="csv")


async def test_wait_conditions(phone):
    await open_page(phone)
    assert (await phone.call("browser_wait", text="Hello Browser"))["ok"]
    assert (await phone.call("browser_wait", selector="#file"))["ok"]
    assert (await phone.call("browser_wait", url_contains="text/html"))["ok"]
    assert not (await phone.call("browser_wait", text="never appears", timeout=1))["ok"]


async def test_screenshot_is_a_png(phone):
    await open_page(phone)
    shot = await phone.call("browser_screenshot")
    assert shot[0].type == "image" and shot[0].mime_type == "image/png"
    full = await phone.call("browser_screenshot", full_page=True)
    assert full[0].type == "image"


async def test_upload_sets_the_file_input(phone, tmp_path):
    await open_page(phone)
    doc = tmp_path / "mcp-upload-test.txt"
    doc.write_text("hello")
    result = await phone.call("browser_upload", path=str(doc), selector="#file")
    assert result["device_path"] == "/sdcard/Download/mcp-upload-test.txt"
    field = (await phone.call("browser_find", query="file"))["matches"]
    assert any((m.get("value") or "").endswith("mcp-upload-test.txt") for m in field)
    hidden = tmp_path / ".hidden" / "x.txt"
    hidden.parent.mkdir()
    hidden.write_text("no")
    assert "not_permitted" in await phone.call_error(
        "browser_upload", path=str(hidden), selector="#file"
    )
    await phone.shell("rm -f /sdcard/Download/mcp-upload-test.txt")


async def test_double_tap_gesture_reaches_the_page(phone):
    await open_page(phone)
    await phone.call("browser_handoff")  # foreground the browser so device gestures land on it
    line = None
    for _ in range(10):  # the WebView fills its accessibility tree a moment after it renders
        await asyncio.sleep(0.5)
        elements = await phone.elements()
        line = next((ln for ln in elements.splitlines() if "Double-click me" in ln), None)
        if line:
            break
    assert line, elements
    await phone.call("double_tap", som_id=int(line.split("[")[0]))
    await asyncio.sleep(0.5)
    tabs = await phone.call("browser_tabs")
    assert any(p["title"] == "dbl:1" for p in tabs["all_pages"]), tabs


async def test_handoff_and_close(phone):
    await open_page(phone)
    handoff = await phone.call("browser_handoff", message="please log in")
    assert handoff["ok"] and handoff["foreground"] == "org.chromium.webview_shell"
    closed = await phone.call("browser_close")
    assert closed["detached"] and not (await phone.call("browser_close"))["detached"]
    reopened = await phone.call("browser_open")
    assert reopened["url"] == "about:blank"


async def test_navigation_validates_urls(phone):
    opened = await phone.call("browser_open", url="about:blank")
    assert opened["url"] == "about:blank"


async def test_strict_policy_blocks_password_fields_in_the_browser(phone):
    client = Client(build_server(Config(device=DEVICE, policy="strict")))
    await client.__aenter__()
    try:
        guarded = Phone(client)
        await guarded.call("browser_open", url=URL)
        assert "policy_blocked" in await guarded.call_error(
            "browser_act", action="type", selector="#pw", text="hunter2"
        )
        assert (await guarded.call("browser_act", action="type", selector="#name", text="fine"))[
            "ok"
        ]
    finally:
        await client.__aexit__(None, None, None)


async def test_open_always_reports_the_new_page_not_the_old_one(phone):
    """Regression: right after navigation the OLD document can still be 'complete'."""

    def page(name: str) -> str:
        return "data:text/html;charset=utf-8," + quote(f"<title>{name}</title><h1>{name}</h1>")

    for name in ("First", "Second", "Third", "Fourth"):
        opened = await phone.call("browser_open", url=page(name))
        assert opened["title"] == name
        assert (await phone.call("browser_read", selector="h1"))["text"] == name
