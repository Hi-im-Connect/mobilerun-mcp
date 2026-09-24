import asyncio
import json
import shutil
import subprocess

import pytest
from websockets.asyncio.server import serve

from mobilerun_mcp.browser import js
from mobilerun_mcp.browser.cdp import CdpClient, CdpError, parse_targets
from mobilerun_mcp.errors import McpToolError
from mobilerun_mcp.tools.browser import host_file, normalize_url

# ---- CDP client against a fake browser ------------------------------------------------


async def fake_browser(ws):
    async for raw in ws:
        msg = json.loads(raw)
        method, mid = msg["method"], msg["id"]
        if method == "Runtime.evaluate":
            expr = msg["params"]["expression"]
            if expr == "boom":
                await ws.send(
                    json.dumps(
                        {
                            "id": mid,
                            "result": {
                                "exceptionDetails": {
                                    "text": "Uncaught",
                                    "exception": {
                                        "description": "ReferenceError: boom is not defined"
                                    },
                                }
                            },
                        }
                    )
                )
            else:
                await ws.send(
                    json.dumps({"id": mid, "result": {"result": {"type": "number", "value": 42}}})
                )
        elif method == "Page.enable":
            await ws.send(json.dumps({"id": mid, "result": {}}))
            await ws.send(json.dumps({"method": "Page.loadEventFired", "params": {"timestamp": 1}}))
        elif method == "Silent.method":
            pass  # never answers
        elif method == "Drop.connection":
            await ws.close()
        else:
            await ws.send(
                json.dumps(
                    {"id": mid, "error": {"code": -32601, "message": f"{method} wasn't found"}}
                )
            )


@pytest.fixture
async def client():
    async with serve(fake_browser, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        c = await CdpClient.connect(f"ws://127.0.0.1:{port}")
        yield c
        await c.close()


async def test_evaluate_returns_value(client):
    assert await client.evaluate("1+1") == 42


async def test_page_script_errors_become_cdp_errors(client):
    with pytest.raises(CdpError, match="ReferenceError"):
        await client.evaluate("boom")


async def test_protocol_errors_carry_the_message(client):
    with pytest.raises(CdpError, match="Nope.method wasn't found"):
        await client.send("Nope.method")


async def test_events_can_be_awaited(client):
    waiter = asyncio.create_task(client.wait_event("Page.loadEventFired", timeout=2))
    await asyncio.sleep(0)  # let the waiter register before the event can fire
    await client.send("Page.enable")
    assert (await waiter)["timestamp"] == 1


async def test_send_and_event_timeouts(client):
    with pytest.raises(CdpError, match="timed out"):
        await client.send("Silent.method", timeout=0.2)
    with pytest.raises(CdpError, match="not seen"):
        await client.wait_event("Never.happens", timeout=0.2)


async def test_dropped_connection_fails_pending_and_later_calls(client):
    with pytest.raises(CdpError):
        await client.send("Drop.connection", timeout=2)
    with pytest.raises(CdpError, match="closed"):
        await client.send("Page.enable")


async def test_connect_failure_is_a_cdp_error():
    with pytest.raises(CdpError, match="cannot connect"):
        await CdpClient.connect("ws://127.0.0.1:1", timeout=1)


# ---- target parsing -------------------------------------------------------------------


def test_parse_targets_survives_raw_control_characters():
    raw = '[{"id": "A", "type": "page", "title": "bad\ttab\nnewline", "url": "https://x"}]'
    (target,) = parse_targets(raw)
    assert target["id"] == "A" and "bad" in target["title"]
    assert parse_targets("not json") == [] and parse_targets('{"a": 1}') == []


# ---- js builders ----------------------------------------------------------------------


def test_js_values_are_json_escaped():
    snippet = js.find('it\'s "quoted"', 3)
    assert json.dumps('it\'s "quoted"'.lower()) in snippet
    assert json.dumps('a"b') in js.page_text('a"b', 10)
    assert js.REF_ATTR in js.act("click", "e1", None)
    assert '"#s"' in js.act("select", None, "#s", "b")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "snippet",
    [
        js.SNAPSHOT,
        js.page_text(None, 10),
        js.page_text("main > p", 5),
        js.interactives(10),
        js.find("x", 2),
        js.tables(None, 2),
        js.links("nav", 3),
        js.wait_condition("a", "#b", "c"),
        js.act("click", "e1", None),
        js.act("select", None, "select[name=q]", "b"),
        js.act("prepare_type", None, "#f", None, True),
        js.act("check", "e2", None, "true"),
    ],
)
def test_snippets_are_valid_javascript(snippet):
    result = subprocess.run(
        ["node", "--check", "-"], input=f"const x = {snippet};", capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# ---- tool helpers ---------------------------------------------------------------------


def test_normalize_url():
    assert normalize_url("example.com/a") == "https://example.com/a"
    for kept in ("http://a.b", "https://a.b", "about:blank", "data:text/html,x", "file:///x"):
        assert normalize_url(kept) == kept


def test_host_file_rules(tmp_path):
    good = tmp_path / "doc.txt"
    good.write_text("x")
    assert host_file(str(good)) == good
    with pytest.raises(McpToolError):
        host_file(str(tmp_path / "missing.txt"))
    hidden = tmp_path / ".secret" / "id"
    hidden.parent.mkdir()
    hidden.write_text("k")
    with pytest.raises(McpToolError, match="not_permitted"):
        host_file(str(hidden))


# ---- navigation waits for the new document --------------------------------------------


class FakePage:
    """Scripted page: each evaluate() pops the next state; send() records commands."""

    def __init__(self, states, navigate_reply=None):
        self.states = list(states)
        self.sent = []
        self.navigate_reply = navigate_reply or {}

    async def evaluate(self, expression, timeout=0):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return state

    async def send(self, method, params=None, timeout=0):
        self.sent.append((method, params))
        return self.navigate_reply


def state(url, origin, ready="complete", title="t"):
    return {"title": title, "url": url, "ready": ready, "origin": origin}


async def test_navigate_ignores_the_old_document_that_is_still_complete():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage(
        [
            state("https://old/", 1),  # before
            state("https://old/", 1),  # right after navigate: still the old, complete document
            state("https://new/", 2, ready="loading"),
            state("https://new/", 2, title="New"),
        ]
    )
    info = await navigate(page, "https://new/", timeout=5, interval=0)
    assert info == {"title": "New", "url": "https://new/", "ready": "complete"}
    assert page.sent == [("Page.navigate", {"url": "https://new/"})]


async def test_navigate_same_document_fragment_change_does_not_wait_for_new_origin():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage([state("https://a/#one", 1), state("https://a/#two", 1)])
    info = await navigate(page, "https://a/#two", timeout=2, interval=0)
    assert info["url"] == "https://a/#two"


async def test_navigate_reports_load_failures():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage(
        [state("about:blank", 1)], navigate_reply={"errorText": "net::ERR_INTERNET_DISCONNECTED"}
    )
    with pytest.raises(CdpError, match="ERR_INTERNET_DISCONNECTED"):
        await navigate(page, "https://unreachable.example/", timeout=1, interval=0)


async def test_navigate_survives_evaluate_errors_while_the_page_swaps():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage([state("about:blank", 1), CdpError("context destroyed"), state("data:x", 2)])
    info = await navigate(page, "data:x", timeout=2, interval=0)
    assert info["url"] == "data:x"


async def test_navigate_without_wait_returns_current_state():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage([state("https://old/", 1)])
    info = await navigate(page, "https://new/", timeout=1, wait=False)
    assert info["url"] == "https://old/"


async def test_navigate_skips_the_interim_blank_document_of_a_cold_browser():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage(
        [
            state("https://old/", 1),  # before
            state("about:blank", 2, title=""),  # interim document: new origin, complete, not ours
            state("about:blank", 2, title=""),
            state("data:text/html,x", 3, title="MCP Test Page"),
        ]
    )
    info = await navigate(page, "data:text/html,x", timeout=5, interval=0)
    assert info["title"] == "MCP Test Page"


async def test_navigating_to_about_blank_is_accepted():
    from mobilerun_mcp.browser.navigation import navigate

    page = FakePage([state("https://old/", 1), state("about:blank", 2, title="")])
    info = await navigate(page, "about:blank", timeout=2, interval=0)
    assert info["url"] == "about:blank"
