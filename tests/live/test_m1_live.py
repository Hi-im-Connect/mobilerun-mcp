import re

import pytest

pytestmark = pytest.mark.live


def mark_id(elements: str, label: str) -> int:
    for line in elements.splitlines():
        if label.lower() in line.lower():
            return int(line.split("[")[0])
    raise AssertionError(f"{label!r} not on screen:\n{elements}")


async def test_ping_and_status(phone):
    ping = await phone.call("ping_device")
    assert ping["ok"] and ping["transport"] in ("http", "content_provider")
    status = await phone.call("get_device_status")
    assert status["abi"] == "x86_64" and status["android"] == "12"
    assert status["screen"]["size"] == [720, 1280]
    assert status["foreground"]["package"]


async def test_list_devices_and_echo(phone):
    devices = await phone.call("list_devices")
    assert any(d["state"] == "device" for d in devices["devices"])
    assert (await phone.call("echo", message="hi"))["echo"] == "hi"


async def test_launch_by_name_reports_observation(phone):
    result = await phone.call("launch_app", app_name="contacts")
    obs = result["post_action_observation"]
    assert obs["package"] == "com.android.contacts"
    assert obs["screen_changed"] and obs["settled"]
    assert any("Contacts" in label or "Search" in label for label in obs["top_labels"])


async def test_launch_unknown_app_is_an_error(phone):
    assert "app_not_found" in await phone.call_error("launch_app", app_name="nonexistentapp123")


async def test_perceive_tap_by_id_then_ids_go_stale(phone):
    await phone.call("launch_app", app_name="contacts")
    elements = await phone.elements()
    search = mark_id(elements, "Search contacts")
    tapped = await phone.call("tap", som_id=search)
    assert tapped["post_action_observation"]["keyboard_visible"] is True
    assert "stale_som_id" in await phone.call_error("tap", som_id=search)


async def test_type_text_lands_in_field(phone):
    await phone.call("launch_app", app_name="contacts")
    await phone.call("tap", som_id=mark_id(await phone.elements(), "Search contacts"))
    typed = await phone.call("type_text", text="ali")
    assert typed["chars"] == 3
    elements = await phone.elements()
    assert '"ali"' in elements and "Ali Omar" in elements
    back = await phone.call("press_back")
    assert back["ok"]


async def test_swipe_opens_app_drawer(phone):
    result = await phone.call("swipe", x1=360, y1=1180, x2=360, y2=400)
    assert result["post_action_observation"]["screen_changed"]
    assert "Search apps" in await phone.elements()


async def test_long_press_shows_launcher_menu(phone):
    await phone.call("long_press", x=360, y=650)
    assert "Wallpapers" in await phone.elements()


async def test_scroll_to_finds_offscreen_item(phone):
    await phone.shell("am force-stop com.android.settings")  # start from the top level
    await phone.call("launch_app", app_name="settings")
    found = await phone.call("scroll_to", text="About phone", max_scrolls=10)
    assert found["found"] and "About phone" in found["mark"]["label"]
    tapped = await phone.call("tap", som_id=found["mark"]["id"])
    assert tapped["post_action_observation"]["screen_changed"]


async def test_scroll_down_and_up_move_content(phone):
    await phone.call("launch_app", app_name="settings")
    for _ in range(4):  # start from the top whatever state a previous test left behind
        await phone.call("scroll_up", amount=0.9)
    top = await phone.elements()
    await phone.call("scroll_down", amount=0.7)
    scrolled = await phone.elements()
    assert scrolled != top
    await phone.call("scroll_up", amount=0.9)
    assert await phone.elements() != scrolled


async def test_lookup_and_deeplinks(phone):
    hit = (await phone.call("lookup_app", query="eyecon"))["candidates"][0]
    assert hit["package"] == "com.eyecon.global" and hit["score"] == 1.0
    links = await phone.call("list_app_deeplinks", package="com.eyecon.global")
    assert any(link["uri"] == "eyecon://" for link in links["deeplinks"])
    resolved = await phone.call("resolve_deeplink", uri="android.settings.WIFI_SETTINGS")
    assert resolved["resolved"] and resolved["package"] == "com.android.settings"
    opened = await phone.call("open_deeplink", uri="android.settings.WIFI_SETTINGS")
    assert opened["post_action_observation"]["package"] == "com.android.settings"


async def test_screenshots_and_tree(phone):
    await phone.call("launch_app", app_name="contacts")
    content = await phone.call("get_screenshot")
    assert content[0].type == "image"
    shot = await phone.call("screenshot_path")
    data = open(shot["path"], "rb").read()
    assert data[:4] == b"\x89PNG" and shot["bytes"] == len(data)
    tree = await phone.call("get_ui_tree")
    assert "Contacts" in tree["tree"] and tree["package"] == "com.android.contacts"


async def test_perceive_screen_returns_annotated_image(phone):
    parts = await phone.call("perceive_screen")
    assert [c.type for c in parts] == ["text", "image"]
    assert re.search(r'"mark_count": \d+', parts[0].text)


async def test_out_of_bounds_tap_rejected(phone):
    assert "invalid_argument" in await phone.call_error("tap", x=5000, y=5000)


async def test_cold_start_waits_for_the_app(phone):
    await phone.shell("am force-stop com.android.settings")
    result = await phone.call("launch_app", app_name="settings")
    obs = result["post_action_observation"]
    assert obs["package"] == "com.android.settings" and obs["settled"]
