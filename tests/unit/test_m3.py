import pytest

from mobilerun_mcp import guide
from mobilerun_mcp.conditions import (
    Snapshot,
    diff_events,
    diff_notifications,
    find_text,
    matches,
    normalize,
    screen_text,
    snapshot_of,
)
from mobilerun_mcp.marks import build_marks, signature
from mobilerun_mcp.parsers.a11y import parse_screen
from mobilerun_mcp.websearch import parse_brave_json, parse_ddg_html, search

# ---- conditions -----------------------------------------------------------------------


def test_normalize_collapses_whitespace_and_case():
    assert normalize("  Hello\n  WORLD ") == "hello world"


def test_screen_text_and_find_text(state_fixture):
    screen = parse_screen(state_fixture("contacts"))
    assert "Search contacts" in screen_text(screen)
    assert find_text(screen, "search  CONTACTS") == "Search contacts"
    assert find_text(screen, "definitely not here") is None
    assert find_text(screen, "  ") is None


def test_matches_requires_every_condition(state_fixture):
    screen = parse_screen(state_fixture("contacts"))
    marks = build_marks(screen)
    ok, evidence = matches(screen, marks, package="com.android.contacts", text="Create new contact")
    assert ok and "package=com.android.contacts" in evidence
    assert not matches(screen, marks, package="com.other")[0]
    assert not matches(screen, marks, text="Nope")[0]
    assert matches(screen, marks, activity="PeopleActivity")[0]
    assert not matches(screen, marks)[0]  # no condition given is never "true"


def test_matches_accepts_app_label_as_package(state_fixture):
    screen = parse_screen(state_fixture("contacts"))
    assert matches(screen, [], package=screen.phone.app)[0]


def test_diff_events_reports_each_change_kind(state_fixture):
    a = parse_screen(state_fixture("contacts"))
    b = parse_screen(state_fixture("contacts_search"))
    ma, mb = build_marks(a), build_marks(b)
    snap_a, snap_b = snapshot_of(a, ma, signature(a, ma)), snapshot_of(b, mb, signature(b, mb))
    kinds = {e["type"] for e in diff_events(snap_a, snap_b)}
    assert kinds == {"keyboard", "screen_changed"}
    assert diff_events(snap_a, snap_a) == []
    moved = Snapshot("other.pkg", "Main", False, "x", 1)
    assert diff_events(snap_a, moved)[0]["type"] == "foreground_changed"


def test_diff_notifications():
    events = diff_notifications({"a", "b"}, {"b", "c"})
    assert {"type": "notification_posted", "key": "c"} in events
    assert {"type": "notification_removed", "key": "a"} in events
    assert diff_notifications({"a"}, {"a"}) == []


# ---- web search parsing ---------------------------------------------------------------

DDG_HTML = """
<div class="result"><h2><a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fhow-to&amp;rut=abc">How to <b>do</b> it</a></h2>
<a class="result__snippet" href="x">Step one &amp; two</a></div>
<div class="result"><h2><a rel="nofollow" class="result__a" href="https://direct.example.org/">Direct</a></h2>
<a class="result__snippet" href="y">Second snippet</a></div>
"""


def test_parse_ddg_unwraps_redirects_and_cleans_text():
    results = parse_ddg_html(DDG_HTML)
    assert results[0] == {
        "title": "How to do it",
        "url": "https://example.com/how-to",
        "snippet": "Step one & two",
    }
    assert results[1]["url"] == "https://direct.example.org/"
    assert len(parse_ddg_html(DDG_HTML, limit=1)) == 1
    assert parse_ddg_html("<html>no results</html>") == []


def test_parse_brave_json():
    data = {"web": {"results": [{"title": "T <b>x</b>", "url": "https://a.b", "description": "D"}]}}
    assert parse_brave_json(data) == [{"title": "T x", "url": "https://a.b", "snippet": "D"}]
    assert parse_brave_json({}) == []


async def test_search_picks_engine_by_key(monkeypatch):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if "brave" in request.url.host:
            assert request.headers["X-Subscription-Token"] == "k"
            return httpx.Response(
                200, json={"web": {"results": [{"title": "B", "url": "u", "description": "d"}]}}
            )
        return httpx.Response(200, text=DDG_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        engine, results = await search("q", 3, brave_key="k", client=client)
        assert engine == "brave" and results[0]["title"] == "B"
        engine, results = await search("q", 3, client=client)
        assert engine == "duckduckgo" and len(results) == 2


# ---- guide ----------------------------------------------------------------------------


def test_guide_topics():
    assert "som_id" in guide.guide("overview")
    assert "Unknown topic" in guide.guide("nope")
    everything = guide.guide()
    assert all(f"## {name}" in everything for name in guide.TOPICS)


# ---- adb tool command splitting -------------------------------------------------------


def test_adb_split_keeps_shell_remainder_as_one_argument():
    from mobilerun_mcp.tools.adbtool import split_command

    assert split_command("shell dumpsys window | grep mCurrentFocus") == [
        "shell",
        "dumpsys window | grep mCurrentFocus",
    ]
    assert split_command("logcat -d -t 100") == ["logcat", "-d", "-t", "100"]
    assert split_command('push "a b.txt" /sdcard/') == ["push", "a b.txt", "/sdcard/"]


def test_adb_split_rejects_retargeting_and_empty():
    from mobilerun_mcp.errors import McpToolError
    from mobilerun_mcp.tools.adbtool import split_command

    for bad in ("-s other:5555 shell id", "-H host devices", "", "   ", "shell"):
        with pytest.raises(McpToolError):
            split_command(bad)


def test_server_gates_adb_tool_and_scopes():
    import asyncio

    from fastmcp import Client

    from mobilerun_mcp.config import Config
    from mobilerun_mcp.server import build_server

    async def names(cfg):
        async with Client(build_server(cfg)) as client:
            return {t.name for t in await client.list_tools()}

    default = asyncio.run(names(Config()))
    assert "adb" not in default and "tap" in default and "perceive_screen" in default
    assert "adb" in asyncio.run(names(Config(enable_adb=True)))
    read_only = asyncio.run(names(Config(scopes=frozenset({"read"}))))
    assert "perceive_screen" in read_only and not ({"tap", "type_text", "launch_app"} & read_only)
    write_only = asyncio.run(names(Config(scopes=frozenset({"write"}))))
    assert "tap" in write_only and "perceive_screen" not in write_only


def test_server_advertises_full_tool_surface():
    import asyncio

    from fastmcp import Client

    from mobilerun_mcp.config import Config
    from mobilerun_mcp.server import build_server

    expected = {
        "perceive_screen",
        "get_ui_tree",
        "read_screen",
        "get_screenshot",
        "get_device_status",
        "tap",
        "double_tap",
        "long_press",
        "swipe",
        "scroll_up",
        "scroll_down",
        "scroll_left",
        "scroll_right",
        "scroll_to",
        "type_text",
        "press_home",
        "press_back",
        "press_enter",
        "open_recent_apps",
        "launch_app",
        "lookup_app",
        "list_app_deeplinks",
        "resolve_deeplink",
        "open_deeplink",
        "system_intent",
        "resolve_contact",
        "read_notifications",
        "notification_action",
        "dismiss_notification",
        "media_control",
        "get_media_sessions",
        "volume_up",
        "volume_down",
        "mute",
        "find_files",
        "wait_for",
        "watch_device_events",
        "validate_action",
        "verify_action",
        "connect_device",
        "end_session",
        "echo",
        "get_usage_guide",
        "set_plan",
        "mark_step",
        "record_finding",
        "request_screen_capture_permission",
        "web_search",
        # kept from the first version of this server
        "start_app",
        "press",
        "screenshot",
        "screenshot_path",
        "list_apps",
        "list_devices",
        "ping_device",
        "run_task",
        # browser over CDP
        "browser_open",
        "browser_close",
        "browser_tabs",
        "browser_screenshot",
        "browser_read",
        "browser_find",
        "browser_wait",
        "browser_extract",
        "browser_act",
        "browser_handoff",
        "browser_upload",
    }

    async def names():
        async with Client(build_server(Config())) as client:
            return {t.name for t in await client.list_tools()}

    missing = expected - asyncio.run(names())
    assert not missing, f"missing tools: {sorted(missing)}"


async def test_search_raises_when_the_engine_returns_nothing():
    import httpx

    from mobilerun_mcp.websearch import SearchError

    def handler(request: httpx.Request) -> httpx.Response:
        if "brave" in request.url.host:
            return httpx.Response(200, json={"web": {"results": []}})
        return httpx.Response(200, text="<html>blocked for automated traffic</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SearchError, match="duckduckgo returned no results"):
            await search("q", 3, client=client)
        with pytest.raises(SearchError, match="brave returned no results"):
            await search("q", 3, brave_key="k", client=client)
