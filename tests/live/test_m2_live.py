import asyncio
import uuid

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
async def _clean_shell_notifications(phone):
    """Start from no test notifications (force-stop cancels everything com.android.shell posted)."""
    await phone.shell("am force-stop com.android.shell")
    await asyncio.sleep(1)
    yield
    await phone.shell("am force-stop com.android.shell")


async def post(phone, title: str) -> str:
    """Post a clearable notification and wait until the system lists it."""
    tag = "mcp" + uuid.uuid4().hex[:8]
    await phone.shell(f"cmd notification post -t '{title}' {tag} 'body for {tag}'")
    for _ in range(20):
        if any(tag in n["key"] for n in await shell_notifications(phone)):
            return tag
        await asyncio.sleep(0.25)
    raise AssertionError(f"notification {tag} never appeared")


async def shell_notifications(phone):
    return (await phone.call("read_notifications", package="com.android.shell"))["notifications"]


async def test_read_notifications_sees_the_portal_service(phone):
    data = await phone.call("read_notifications", package="com.mobilerun.portal")
    (item,) = data["notifications"]
    assert item["title"] == "Keep Screen Awake"
    assert item["actions"] == ["Stop"] and item["ongoing"] and not item["clearable"]


async def test_ongoing_notification_cannot_be_dismissed(phone):
    error = await phone.call_error("dismiss_notification", package="com.mobilerun.portal")
    assert "not_permitted" in error


async def test_unknown_notification_action_lists_available(phone):
    error = await phone.call_error(
        "notification_action", action="Nope", package="com.mobilerun.portal"
    )
    assert "element_not_found" in error and "Stop" in error


async def test_dismiss_single_notification(phone):
    tag = await post(phone, "Dismiss Me")
    (item,) = [n for n in await shell_notifications(phone) if tag in n["key"]]
    assert item["clearable"]
    result = await phone.call("dismiss_notification", key=item["key"])
    assert result["ok"] and result["dismissed"] == [item["key"]]
    assert not [n for n in await shell_notifications(phone) if tag in n["key"]]


async def test_clear_all_removes_every_clearable_notification(phone):
    await post(phone, "First")
    await post(phone, "Second")
    result = await phone.call("dismiss_notification", clear_all=True)
    assert result["ok"] and len(result["dismissed"]) >= 2
    assert await shell_notifications(phone) == []
    assert (await phone.call("read_notifications", package="com.mobilerun.portal"))["count"] == 1


async def test_volume_roundtrip_and_mute(phone):
    start = (await phone.call("get_media_sessions"))["volume"]
    assert start["max"] == 15
    await phone.call("volume_down", steps=3)  # make room whatever the starting level
    base = await phone.call("volume_up", steps=1)
    lowered = await phone.call("volume_down", steps=1)
    assert lowered["volume"] == base["volume"] - 1
    muted = await phone.call("mute", muted=True)
    assert muted["volume"] == 0
    restored = await phone.call("mute", muted=False)
    assert restored["volume"] == lowered["volume"]
    await phone.call("volume_up", steps=3)


async def test_media_control_and_validation(phone):
    assert (await phone.call("media_control", action="play_pause"))["ok"]
    assert "invalid_argument" in await phone.call_error("media_control", action="explode")
    sessions = await phone.call("get_media_sessions", include_system=True)
    assert sessions["count"] >= 0 and "volume" in sessions


async def test_set_alarm_is_actually_created(phone):
    result = await phone.call(
        "system_intent", verb="set_alarm", hour=4, minute=44, label="mcp-live"
    )
    assert result["handled_by"].startswith("com.android.deskclock/")
    await phone.call("launch_app", app_name="clock")
    assert "4:44" in await phone.elements()
    await phone.shell("pm clear com.android.deskclock")  # drop the test alarm


async def test_dial_prefills_the_dialer(phone):
    result = await phone.call("system_intent", verb="dial", phone_number="+1 555 123 4567")
    assert result["handled_by"].startswith("com.eyecon.global/")
    assert result["post_action_observation"]["package"] == "com.eyecon.global"


async def test_share_text_opens_chooser(phone):
    result = await phone.call("system_intent", verb="share_text", text="hello", subject="s")
    assert result["post_action_observation"]["package"] == "android"
    await phone.call("press_back")


async def test_calendar_event_reaches_the_calendar_handler(phone):
    result = await phone.call(
        "system_intent", verb="add_calendar_event", title="MCP Live Event", start="2026-09-30T15:00"
    )
    assert result["handled_by"] == "com.android.calendar/.EditEventActivity"


async def test_system_intent_validation_errors(phone):
    assert "invalid_argument" in await phone.call_error(
        "system_intent", verb="set_alarm", hour=30, minute=0
    )
    assert "invalid_argument" in await phone.call_error("system_intent", verb="teleport")


async def test_resolve_contact(phone):
    hit = (await phone.call("resolve_contact", name="ali"))["matches"][0]
    assert hit["name"] == "Ali Omar" and hit["number"].startswith("+20")
    assert (await phone.call("resolve_contact", name="zzzzqq"))["matches"] == []


async def test_find_files_and_path_guard(phone):
    await phone.shell("echo hi > /sdcard/Download/mcp_live_note.txt")
    found = await phone.call("find_files", query="mcp_live_note")
    assert "/sdcard/Download/mcp_live_note.txt" in found["files"]
    assert "not_permitted" in await phone.call_error("find_files", path="/data/data")
    await phone.shell("rm /sdcard/Download/mcp_live_note.txt")
