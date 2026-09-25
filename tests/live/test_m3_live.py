import asyncio

import pytest
from fastmcp import Client

from mobilerun_mcp.config import Config
from mobilerun_mcp.server import build_server

from .conftest import DEVICE, Phone

pytestmark = pytest.mark.live


async def test_wait_for_text_package_gone_and_timeout(phone):
    await phone.call("launch_app", app_name="contacts")
    hit = await phone.call("wait_for", text="Search contacts", timeout=5)
    assert hit["ok"] and "Search contacts" in hit["evidence"]
    assert (await phone.call("wait_for", package="com.android.contacts", timeout=3))["ok"]
    assert not (await phone.call("wait_for", text="zzz-not-there", timeout=1))["ok"]
    assert (await phone.call("wait_for", text="zzz-not-there", gone=True, timeout=2))["ok"]
    idle = await phone.call("wait_for", condition="contacts loaded", timeout_ms=3000)
    assert idle["ok"] and idle["condition"] == "contacts loaded"


async def test_verify_action_kinds(phone):
    await phone.call("launch_app", app_name="contacts")
    assert (await phone.call("verify_action", expected="Create new contact"))["verified"]
    assert (await phone.call("verify_action", expected="contacts", kind="app"))["verified"]
    assert (await phone.call("verify_action", expected="PeopleActivity", kind="activity"))[
        "verified"
    ]
    assert (await phone.call("verify_action", expected="zzz", kind="gone", timeout=1))["verified"]
    missing = await phone.call("verify_action", expected="zzz", timeout=0.5)
    assert not missing["verified"] and missing["visible_text"]
    await phone.call("press_home")
    assert (await phone.call("verify_action", expected="", kind="changed"))["verified"]


async def test_verify_action_can_fall_back_to_ocr(phone):
    await phone.call("launch_app", app_name="contacts")
    found = await phone.call("verify_action", expected="Contacts", use_ocr=True, timeout=0.5)
    assert found["verified"]


async def test_validate_action_reports_without_acting(phone):
    await phone.call("launch_app", app_name="contacts")
    ok = await phone.call("validate_action", action="tap", x=100, y=100)
    assert ok["valid"] and ok["policy"]["mode"] == "off"
    assert not (await phone.call("validate_action", action="tap", x=9999, y=1))["valid"]
    stale = await phone.call("validate_action", action="tap", som_id=1)
    assert not stale["valid"] and "som_id" in stale["problems"][0]
    assert not (
        await phone.call("validate_action", action="launch_app", app_name="nonexistentapp123")
    )["valid"]
    assert (await phone.call("validate_action", action="launch_app", app_name="contacts"))["valid"]
    assert (
        await phone.call(
            "validate_action", action="open_deeplink", uri="android.settings.WIFI_SETTINGS"
        )
    )["valid"]
    no_focus = await phone.call("validate_action", action="type_text", text="hi")
    assert not no_focus["valid"] and "focused" in no_focus["problems"][0]
    assert (await phone.call("verify_action", expected="Contacts", kind="app"))[
        "verified"
    ]  # nothing was executed


async def test_watch_device_events_sees_a_launch(phone):
    async def launch_later():
        await asyncio.sleep(1.5)
        await phone.call("launch_app", app_name="contacts")

    watched, _ = await asyncio.gather(
        phone.call(
            "watch_device_events", duration=12, interval=0.5, kinds=["foreground", "screen"]
        ),
        launch_later(),
    )
    changes = [e for e in watched["events"] if e["type"] == "foreground_changed"]
    assert any("com.android.contacts" in e["to"] for e in changes), watched


async def test_watch_reports_notifications(phone):
    async def post_later():
        await asyncio.sleep(1)
        await phone.shell("cmd notification post -t 'Watch Me' watchtag 'x'")

    watched, _ = await asyncio.gather(
        phone.call("watch_device_events", duration=7, kinds=["notifications"]), post_later()
    )
    assert any(
        e["type"] == "notification_posted" and "watchtag" in e["key"] for e in watched["events"]
    )
    await phone.shell("am force-stop com.android.shell")


async def test_ledger_flow_and_honest_finish(phone):
    await phone.call("launch_app", app_name="contacts")
    plan = await phone.call("set_plan", steps=["read", "read again"], goal="g", target_count=2)
    assert plan["ledger"]["target_count"] == 2
    step = await phone.call("mark_step", index=0, status="done", note="opened")
    assert step["step"]["status"] == "done"
    assert "invalid_argument" in await phone.call_error("mark_step", index=9, status="done")
    assert "invalid_argument" in await phone.call_error(
        "record_finding", item="x", quote="not on screen at all"
    )
    first = await phone.call("record_finding", item="search control", quote="Search contacts")
    assert first["remaining"] == 1
    assert "plan_incomplete" in await phone.call_error("end_session", outcome="success")
    await phone.call("record_finding", item="fab", quote="Create new contact")
    done = await phone.call("end_session", outcome="success", summary="ok")
    assert done["ledger"]["ended"] == "success" and len(done["ledger"]["findings"]) == 2


async def test_web_search_returns_results_or_skips(phone):
    try:
        data = await phone.call("web_search", query="how to set an alarm on android", limit=3)
    except Exception as exc:
        pytest.skip(f"no web search available here: {exc}")
    assert data["results"] and data["results"][0]["url"].startswith("http")


async def test_usage_guide_resources_and_prompts(phone):
    guide = await phone.call("get_usage_guide", topic="text_entry")
    assert "FOCUSED" in guide["guide"]
    uris = {str(r.uri) for r in await phone.client.list_resources()}
    assert {
        "mobilerun://guide",
        "mobilerun://policy",
        "mobilerun://ledger",
        "mobilerun://device/snapshot",
    } <= uris
    policy = (await phone.client.read_resource("mobilerun://policy"))[0].text
    assert "active mode: off" in policy
    snapshot = (await phone.client.read_resource("mobilerun://device/snapshot"))[0].text
    assert "keyboard=" in snapshot
    prompts = {p.name for p in await phone.client.list_prompts()}
    assert {"perceive_act_verify", "research_then_act"} <= prompts


async def test_device_connection_tools(phone):
    assert (await phone.call("connect_device"))["ok"]
    assert (await phone.call("request_screen_capture_permission"))["needed"] is False


# ---- policy modes and scopes ----------------------------------------------------------


async def with_config(**kwargs):
    client = Client(build_server(Config(device=DEVICE, **kwargs)))
    await client.__aenter__()
    return client, Phone(client)


async def test_standard_policy_blocks_card_numbers_but_not_normal_text(phone):
    client, guarded = await with_config(policy="standard")
    try:
        await guarded.call("launch_app", app_name="contacts")
        await guarded.call("tap", som_id=await _search_id(guarded))
        assert "policy_blocked" in await guarded.call_error("type_text", text="4111 1111 1111 1111")
        check = await guarded.call("validate_action", action="type_text", text="4111111111111111")
        assert not check["valid"] and check["policy"] == {
            "mode": "standard",
            "blocked": True,
            "category": "card_number",
        }
        assert (await guarded.call("validate_action", action="type_text", text="hello"))["policy"][
            "blocked"
        ] is False
        assert (await guarded.call("type_text", text="ali"))["chars"] == 3
        assert "policy_blocked" in await guarded.call_error("run_task", task="anything")
    finally:
        await client.__aexit__(None, None, None)


async def _search_id(p):
    for line in (await p.elements()).splitlines():
        if "Search contacts" in line:
            return int(line.split("[")[0])
    raise AssertionError("no search control")


async def test_strict_policy_blocks_password_style_input(phone):
    client, strict = await with_config(policy="strict")
    try:
        await strict.call("launch_app", app_name="contacts")
        await strict.call("tap", som_id=await _search_id(strict))
        assert "policy_blocked" in await strict.call_error("type_text", text="123-45-6789")
    finally:
        await client.__aexit__(None, None, None)


async def test_adb_tool_only_when_armed_and_not_under_policy(phone):
    client, armed = await with_config(enable_adb=True)
    try:
        out = await armed.call("adb", command="shell echo mcp-adb-ok")
        assert out["ok"] and "mcp-adb-ok" in out["output"]
        piped = await armed.call("adb", command="shell dumpsys window | grep -c mCurrentFocus")
        assert piped["ok"]
        assert "not_permitted" in await armed.call_error("adb", command="-s other:5555 shell id")
    finally:
        await client.__aexit__(None, None, None)
    client, both = await with_config(enable_adb=True, policy="standard")
    try:
        assert "policy_blocked" in await both.call_error("adb", command="shell id")
    finally:
        await client.__aexit__(None, None, None)


async def test_read_scope_hides_actions_but_keeps_perception(phone):
    client, read_only = await with_config(scopes=frozenset({"read"}))
    try:
        names = {t.name for t in await client.list_tools()}
        assert "perceive_screen" in names and "tap" not in names and "launch_app" not in names
        assert (await read_only.call("read_screen")).startswith("SCREEN ")
    finally:
        await client.__aexit__(None, None, None)
