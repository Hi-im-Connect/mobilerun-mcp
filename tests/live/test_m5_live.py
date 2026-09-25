"""Live checks for the upstream-parity surface: AURA formats, mobilerun-core Device API,
mobilerun agent actions, launcher shortcuts, the AURA browser model and local paths of the
official cloud tools."""

import asyncio
from urllib.parse import quote

import pytest

pytestmark = pytest.mark.live

LIST_PAGE = "data:text/html;charset=utf-8," + quote(
    "<title>List</title><h1>Shop</h1><ul>"
    + "".join(
        f'<li class="item"><a href="#p{i}">Product {i}</a> price {i}0</li>' for i in range(1, 7)
    )
    + '</ul><button id="b" onclick="document.title=\'clicked\'">Buy</button>'
)


async def test_read_screen_is_an_aura_grid(phone):
    grid = await phone.call("read_screen")
    assert grid.startswith("SCREEN ") and ("IDLE" in grid or "BUSY" in grid)
    assert "som  in   flg" in grid and "+--" in grid.replace("+-", "+--")


async def test_perceive_screen_e_array_and_full_tier(phone):
    view = await phone.perceive(description="apps")
    assert view["perception_tier"] == "tree_only"
    assert len(view["e"]) == view["mark_count"] and all(len(e) >= 2 for e in view["e"])
    full = await phone.perceive(detail="full")
    assert full["perception_tier"] == "full" and full["mark_count"] >= view["mark_count"]


async def test_core_device_api_on_the_fast_path(phone):
    ui = await phone.call("ui")
    assert "a11y_tree" in ui and "phone_state" in ui
    assert (await phone.call("current_app_id")) == "com.android.launcher3"
    width, height = await phone.call("screen_size")
    assert width > 0 and height > 0
    caps = await phone.call("capabilities")
    assert {"tap", "execute_script", "get_clipboard", "grant_permission"} <= set(caps["actions"])
    nodes = await phone.call("find_nodes_on_screen", any_contains="Contacts")
    assert nodes
    tapped = await phone.call("tap_text", text="Contacts")
    assert tapped["ok"]
    assert await phone.call("wait_for_app", app_id="com.android.contacts", timeout=20)
    assert "T" in await phone.call("time") or await phone.call("time")


async def test_agent_indices_click_like_mobilerun(phone):
    state = (await phone.call("get_state"))["state"]
    assert "Current Clickable UI elements" in state
    line = next(ln for ln in state.splitlines() if '"Contacts"' in ln)
    result = await phone.call("click", index=int(line.split(".")[0]))
    assert result["post_action_observation"]["package"] == "com.android.contacts"
    assert (await phone.call("system_button", button="back"))["ok"]


async def test_launch_app_reports_already_foreground(phone):
    await phone.call("launch_app", app_name="contacts")
    again = await phone.call("launch_app", package_name="com.android.contacts")
    assert again["already_foreground"] is True


async def test_launcher_shortcut_deeplinks(phone):
    links = await phone.call("list_app_deeplinks", package_name="com.android.settings")
    first = links["deeplinks"][0]
    assert first["source"] == "shortcut" and first["uri"].startswith("app-shortcut://")
    wifi = next(d for d in links["deeplinks"] if d["uri"].endswith("manifest-shortcut-wifi"))
    resolved = await phone.call("resolve_deeplink", uri=wifi["uri"])
    assert resolved["handler_kind"] == "app"
    opened = await phone.call("open_deeplink", uri=wifi["uri"])
    assert opened["post_action_observation"]["package"] == "com.android.settings"
    assert "not_permitted" in await phone.call_error("open_deeplink", uri="javascript:alert(1)")
    await phone.shell("am force-stop com.android.settings")


async def test_aura_argument_forms(phone):
    alarms = await phone.call("system_intent", action="show_alarms")
    assert alarms["handled_by"].startswith("com.android.deskclock/")
    await phone.home()
    waited = await phone.call(
        "wait_for", condition="home idle", timeout_ms=3000, poll_interval_ms=200
    )
    assert waited["condition"] == "home idle"
    events = await phone.call("watch_device_events", timeout_seconds=1, max_events=5)
    assert events["count"] <= 5
    check = await phone.call("validate_action", gesture_type="launch_app", target="Contacts")
    assert check["allowed"]
    assert (await phone.call("echo", text="ping"))["echo"] == "ping"
    before = await phone.call("mute")
    after = await phone.call("mute")
    assert before["muted"] != after["muted"]


async def test_end_session_enforces_a_look_for_messages(phone):
    await phone.call("press_home")
    error = await phone.call_error("end_session", outcome="success", goal_type="send_message")
    assert "plan_incomplete" in error
    await phone.call("read_screen")
    done = await phone.call(
        "end_session", reason="sent", outcome="success", goal_type="send_message"
    )
    assert done["outcome"] == "success"
    failed = await phone.call("end_session", reason="gave up", outcome="failure")
    assert failed["outcome"] == "failure"


async def test_official_device_tools_work_on_a_local_device(phone):
    from .conftest import DEVICE

    device = await phone.call("get_device", deviceId=DEVICE)
    assert device["id"] == DEVICE
    listed = await phone.call("manage_device_apps", deviceId=DEVICE, operation="list_packages")
    assert "com.mobilerun.portal" in str(listed)
    tapped = await phone.call("device_action", deviceId=DEVICE, operation="tap", x=10, y=600)
    assert tapped
    unsupported = await phone.call_error("manage_esim", deviceId=DEVICE, operation="list")
    assert "unsupported" in unsupported


async def test_browser_el_id_generation_items_and_tabs(phone):
    opened = await phone.call("browser_open", url=LIST_PAGE, background=True)
    assert opened["title"] == "List" and opened["generation"] >= 1
    button = next(e for e in opened["elements"] if e["text"] == "Buy")
    clicked = await phone.call(
        "browser_act", action="click", el_id=button["el_id"], generation=opened["generation"]
    )
    assert clicked["title"] == "clicked"
    stale = await phone.call(
        "browser_act", action="click", el_id=button["el_id"], generation=opened["generation"] - 1
    )
    assert stale["stale_handles"]
    items = await phone.call("browser_extract")
    assert items["total"] == 6 and items["items"][0]["link"].endswith("#p1")
    found = await phone.call("browser_find", text="Product 3")
    assert found["found"] and found["el_id"] is not None
    tab = await phone.call("browser_tabs", action="open", url="data:text/html,<title>Two</title>")
    assert tab["title"] == "Two"
    tabs = await phone.call("browser_tabs")
    assert tabs["count"] == 2
    back = await phone.call("browser_tabs", action="switch", index=1)
    assert back["reloaded"] and back["title"] in ("List", "clicked")
    assert await phone.call("execute_script", js="1 + 2") == 3
    await phone.call("browser_close")


async def test_list_devices_scopes(phone):
    local = await phone.call("list_devices")
    assert local["count"] >= 1
    await asyncio.sleep(0)


async def test_portal_http_target_runs_through_mobilerun_core(phone, monkeypatch):
    """The non-adb path (used for iOS / cloud too): same device, Portal HTTP only."""
    import re
    import subprocess

    from fastmcp import Client

    from mobilerun_mcp.config import Config
    from mobilerun_mcp.server import build_server

    from .conftest import DEVICE, Phone

    subprocess.run(["adb", "-s", DEVICE, "forward", "tcp:18080", "tcp:8080"], check=True)
    raw = await phone.shell("content query --uri content://com.mobilerun.portal/auth_token")
    token = re.search(r'"result"\s*:\s*"([^"]+)"', raw) or re.search(r"result=(\S+)", raw)
    monkeypatch.setenv("MOBILERUN_ANDROID_PORTAL_TOKEN", token.group(1))
    async with Client(build_server(Config(device="http://127.0.0.1:18080"))) as client:
        http = Phone(client)
        assert (await http.call("capabilities"))["backend"] == "local-android-http"
        opened = await http.call("launch_app", app_name="contacts")
        assert opened["post_action_observation"]["package"] == "com.android.contacts"
        grid = await http.call("read_screen")
        assert "com.android.contacts" in grid.splitlines()[0]
        await http.call("tap_text", text="Search contacts")
        typed = await http.call("type", text="ali")
        assert typed["post_action_observation"]["keyboard_visible"]
        assert await http.call("find_nodes_on_screen", text_contains="Ali Omar")
        await http.call("press_back")
        await http.call("press_back")
        assert "unsupported" in await http.call_error("list_app_deeplinks", package_name="x")
