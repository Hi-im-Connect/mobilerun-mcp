"""Port of droidrun's official mobilerun-mcp tools: cloud calls go to the right endpoint with the
right body/query (httpx.MockTransport), local device ids run on the local session."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from mobilerun_mcp.config import Config
from mobilerun_mcp.session import Runtime
from mobilerun_mcp.tools import cloud

CLOUD_ID = "11111111-2222-3333-4444-555555555555"
DEV = f"cloud:{CLOUD_ID}"


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: object = {}
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.reply
        if self.status != 200:
            return httpx.Response(self.status, json=reply)
        if isinstance(reply, str):
            return httpx.Response(200, text=reply, headers={"content-type": "text/plain"})
        return httpx.Response(200, json=reply)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self) -> dict:
        return json.loads(self.last.content or b"{}")


@pytest.fixture
def rec(monkeypatch):
    from mobilerun_sdk import AsyncMobilerun

    recorder = Recorder()

    def factory(key: str):
        http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
        return AsyncMobilerun(api_key=key, http_client=http, max_retries=0)

    monkeypatch.setattr(cloud, "CLIENT_FACTORY", factory)
    monkeypatch.setattr(cloud, "_clients", {})
    return recorder


def make_server(key: str | None = "dr_sk_test") -> tuple[FastMCP, Runtime]:
    rt = Runtime(Config(cloud_api_key=key))
    mcp = FastMCP("t")
    cloud.register(mcp, rt)
    return mcp, rt


async def run(tool: str, args: dict, key: str | None = "dr_sk_test"):
    mcp, _ = make_server(key)
    async with Client(mcp) as c:
        result = await c.call_tool(tool, args)
    return result.structured_content or json.loads(result.content[0].text)


def path(req: httpx.Request) -> str:
    return req.url.path.removeprefix("/v1")


# ---- surface ---------------------------------------------------------------------------------


async def test_registers_every_official_tool_except_task_tools():
    from importlib import resources

    spec = json.loads(
        (resources.files("mobilerun_mcp.upstream") / "official_mcp_tools.json").read_text()
    )
    mine = {e["name"] for e in spec} - {
        "list_devices",
        "run_task",
        "get_task",
        "list_tasks",
        "stop_task",
        "get_task_media",
        "send_task_message",
    }
    mcp, _ = make_server()
    async with Client(mcp) as c:
        tools = {t.name: t.input_schema for t in await c.list_tools()}
    assert set(tools) == mine
    for entry in spec:
        if entry["name"] not in tools:
            continue
        ours = tools[entry["name"]]
        assert set(entry["inputSchema"].get("properties", {})) <= set(ours["properties"]), entry[
            "name"
        ]
        assert set(ours.get("required", [])) <= set(entry["inputSchema"].get("required", [])), (
            entry["name"]
        )


async def test_missing_api_key_is_unsupported(rec):
    with pytest.raises(ToolError, match="MOBILERUN_CLOUD_API_KEY"):
        await run("get_device", {"deviceId": DEV}, key=None)


async def test_api_errors_map_to_codes(rec):
    rec.status, rec.reply = 404, {"message": "nope"}
    with pytest.raises(ToolError, match=r"\[not_found\]"):
        await run("get_device", {"deviceId": DEV})


# ---- device tools, cloud -----------------------------------------------------------------------


async def test_get_device_and_ui_state(rec):
    rec.reply = {"id": CLOUD_ID, "state": "ready"}
    out = await run("get_device", {"deviceId": DEV})
    assert rec.last.method == "GET" and path(rec.last) == f"/devices/{CLOUD_ID}"
    assert out["id"] == CLOUD_ID

    rec.reply = {
        "phone_state": {"currentApp": "Settings", "keyboardVisible": False},
        "a11y_tree": {
            "boundsInScreen": {"left": 0, "top": 0, "right": 100, "bottom": 100},
            "children": [
                {
                    "text": "Wi-Fi",
                    "isClickable": True,
                    "resourceId": "android:id/title",
                    "boundsInScreen": {"left": 0, "top": 0, "right": 10, "bottom": 20},
                },
                {
                    "className": "android.widget.FrameLayout",
                    "boundsInScreen": {"left": 0, "top": 0, "right": 5, "bottom": 5},
                },
            ],
        },
    }
    out = await run("get_device_ui_state", {"deviceId": DEV, "contains": "wi"})
    assert path(rec.last) == f"/devices/{CLOUD_ID}/ui-state"
    assert rec.last.url.params.get("filter") == "true"
    assert out["app"] == "Settings" and out["total"] == 1
    assert out["nodes"] == [{"t": "Wi-Fi", "id": "title", "xy": [5, 10], "f": "clickable"}]
    assert out["filter"] == {"contains": "wi"}


async def test_device_action_tap_swipe_keyboard(rec):
    out = await run(
        "device_action", {"operation": "tap", "deviceId": DEV, "x": 10, "y": 20, "displayId": 2}
    )
    assert out == {"status": "ok", "operation": "tap", "deviceId": DEV}
    assert rec.last.method == "POST" and path(rec.last).endswith("/tap")
    assert rec.body() == {"x": 10, "y": 20}
    assert rec.last.headers["X-Device-Display-ID"] == "2"

    await run(
        "device_action",
        {
            "operation": "swipe",
            "deviceId": DEV,
            "startX": 1,
            "startY": 2,
            "endX": 3,
            "endY": 4,
            "duration": 300,
            "stealth": True,
        },
    )
    assert rec.body() == {
        "startX": 1,
        "startY": 2,
        "endX": 3,
        "endY": 4,
        "duration": 300,
        "stealth": True,
    }

    await run(
        "device_action",
        {"operation": "keyboard_write", "deviceId": DEV, "text": "hi", "clear": True, "wpm": 40},
    )
    assert path(rec.last) == f"/devices/{CLOUD_ID}/keyboard"
    assert rec.body()["text"] == "hi" and rec.body()["wpm"] == 40

    await run("device_action", {"operation": "global_action", "deviceId": DEV, "action": 2})
    assert rec.body() == {"action": 2}


async def test_device_action_rejects_fields_of_other_operations(rec):
    with pytest.raises(ToolError, match="not valid for operation=tap"):
        await run(
            "device_action", {"operation": "tap", "deviceId": DEV, "x": 1, "y": 1, "text": "x"}
        )
    with pytest.raises(ToolError, match="requires y"):
        await run("device_action", {"operation": "tap", "deviceId": DEV, "x": 1})
    assert not rec.requests


async def test_manage_device(rec):
    rec.reply = {"id": CLOUD_ID, "name": "new"}
    out = await run("manage_device", {"operation": "rename", "deviceId": DEV, "name": "new"})
    assert [(r.method, path(r)) for r in rec.requests] == [
        ("PUT", f"/devices/{CLOUD_ID}/name"),
        ("GET", f"/devices/{CLOUD_ID}"),
    ]
    assert json.loads(rec.requests[0].content) == {"name": "new"}
    assert out["name"] == "new"

    out = await run("manage_device", {"operation": "reboot", "deviceId": DEV})
    assert out == {"status": "reboot_requested", "deviceId": DEV}
    assert path(rec.last).endswith("/reboot")

    rec.reply = {"ready": 1}
    await run("manage_device", {"operation": "count"})
    assert path(rec.last).endswith("/count")

    with pytest.raises(ToolError, match="not valid for operation=reboot"):
        await run("manage_device", {"operation": "reboot", "deviceId": DEV, "name": "x"})


async def test_manage_device_apps_and_files(rec):
    await run(
        "manage_device_apps",
        {"operation": "start", "deviceId": DEV, "packageName": "com.x", "activity": ".Main"},
    )
    assert "/apps/com.x" in path(rec.last) and rec.body().get("activity") == ".Main"

    with pytest.raises(ToolError, match="packageName and/or bundleId"):
        await run("manage_device_apps", {"operation": "install", "deviceId": DEV})

    rec.reply = ["com.a"]
    await run(
        "manage_device_apps",
        {"operation": "list_packages", "deviceId": DEV, "includeSystemPackages": True},
    )
    assert rec.last.url.params.get("includeSystemPackages") == "true"

    await run(
        "manage_device_files",
        {
            "operation": "upload",
            "deviceId": DEV,
            "path": "/sdcard/a.txt",
            "contentBase64": base64.b64encode(b"hello").decode(),
        },
    )
    assert rec.last.method == "POST" and b"hello" in rec.last.content

    rec.reply = "aGVsbG8="
    out = await run(
        "manage_device_files", {"operation": "download", "deviceId": DEV, "path": "/sdcard/a.txt"}
    )
    assert out == {"content": "aGVsbG8="}
    assert rec.last.url.params.get("path") == "/sdcard/a.txt"


async def test_configure_device_proxy_defaults_smart_ip(rec):
    await run(
        "configure_device",
        {"operation": "proxy_connect", "deviceId": DEV, "socks5Host": "h", "socks5Port": 1080},
    )
    body = rec.body()
    assert body["smartIp"] is True and body["socks5"] == {"host": "h", "port": 1080}

    rec.reply = {"locale": "en-US"}
    out = await run("configure_device", {"operation": "get_language", "deviceId": DEV})
    assert out == {"locale": "en-US"}


async def test_manage_esim_activate(rec):
    rec.reply = {"ok": True}
    await run(
        "manage_esim",
        {"operation": "activate", "deviceId": DEV, "enable": True, "smDpAddr": "sm.example"},
    )
    assert rec.body() == {"enable": True, "smDpAddr": "sm.example"}


async def test_create_and_terminate_device(rec):
    rec.reply = {"id": CLOUD_ID}
    await run("create_device", {"country": "de", "name": "n"})
    assert rec.last.url.params.get("country") == "DE" and rec.body()["name"] == "n"
    out = await run("terminate_device", {"deviceId": DEV})
    assert out == {"status": "termination_requested", "deviceId": DEV}
    assert rec.last.method == "DELETE"


# ---- platform tools ---------------------------------------------------------------------------


async def test_credentials_never_echo_values(rec):
    rec.reply = {
        "data": {
            "credentialName": "main",
            "packageName": "com.x",
            "userId": "u",
            "secretPath": "p",
            "fields": [{"fieldType": "password", "value": "hunter2"}],
        },
        "message": "ok",
        "success": True,
    }
    out = await run(
        "manage_credentials",
        {
            "operation": "create_credential",
            "packageName": "com.x",
            "credentialName": "main",
            "fields": [{"fieldType": "password", "value": "hunter2"}],
        },
    )
    assert "hunter2" not in json.dumps(out)
    assert out["data"]["fields"] == [{"fieldType": "password"}]
    assert rec.body() == {
        "credentialName": "main",
        "fields": [{"fieldType": "password", "value": "hunter2"}],
    }

    with pytest.raises(ToolError, match="requires credentialName"):
        await run("manage_credentials", {"operation": "delete_credential", "packageName": "a"})


async def test_list_credential_packages_is_derived(rec):
    rec.reply = {
        "items": [{"packageName": "a"}, {"packageName": "a"}, {"packageName": "b"}],
        "pagination": {"pages": 1},
    }
    out = await run("list_credential_packages", {})
    assert out == {"items": [{"packageName": "a"}, {"packageName": "b"}], "derived": True}


async def test_webhooks_create_adds_secret_handling(rec):
    rec.reply = {"data": {"id": "e", "secret": "s"}}
    out = await run(
        "webhooks",
        {"operation": "create", "url": "https://x.test/hook", "eventTypes": ["task.completed"]},
    )
    assert "secretHandling" in out
    assert rec.body() == {"url": "https://x.test/hook", "eventTypes": ["task.completed"]}
    with pytest.raises(ToolError, match="requires eventTypes, state, or description"):
        await run(
            "webhooks",
            {"operation": "update", "endpointId": "3fa85f64-5717-4562-b3fc-2c963f66afa6"},
        )


async def test_proxies_and_connect(rec):
    await run(
        "proxies",
        {"operation": "create", "protocol": "wireguard", "name": "w", "config": "[Interface]"},
    )
    assert rec.body() == {"protocol": "wireguard", "name": "w", "config": "[Interface]"}
    with pytest.raises(ToolError, match="requires host"):
        await run("proxies", {"operation": "create", "protocol": "socks5", "name": "s"})

    out = await run("connect", {"operation": "cancel_proxy", "proxyId": "p1"})
    assert out == {"success": True}
    rec.reply = {"items": []}
    await run(
        "connect",
        {"operation": "list_connections", "proxyId": "p1", "dstPort": 443, "orderBy": "bytesIn"},
    )
    assert rec.last.url.params.get("dstPort") == "443"
    assert rec.last.url.params.get("orderBy") == "bytesIn"


async def test_workflow_tools(rec):
    with pytest.raises(ToolError, match="not valid for resource=service"):
        await run("list_workflow_resources", {"resource": "service", "search": "x"})
    with pytest.raises(ToolError, match="requires filter: service"):
        await run("list_workflow_resources", {"resource": "service_methods"})

    rec.reply = {"items": []}
    await run("list_workflow_resources", {"resource": "execution", "flowId": "f", "limit": 5})
    assert rec.last.url.params.get("pageSize") == "5"
    assert rec.last.url.params.get("flowId") == "f"

    rec.reply = {"events": []}
    await run("workflow_events", {"operation": "list_event_types", "source": "device"})
    assert path(rec.last) == "/events/catalog" and rec.last.url.params.get("source") == "device"

    await run(
        "workflow_events",
        {"operation": "register_events", "events": [{"eventType": "custom.x", "label": "X"}]},
    )
    assert path(rec.last) == "/events/catalog/register"
    assert rec.body() == {"events": [{"eventType": "custom.x", "label": "X"}]}

    rec.reply = {"id": "t"}
    await run(
        "create_trigger",
        {
            "name": "n",
            "activation": "schedule",
            "scheduleRule": {"type": "cron", "expression": "0 9 * * *"},
            "timezone": "UTC",
        },
    )
    assert rec.body()["scheduleRule"] == {"type": "cron", "expression": "0 9 * * *"}

    await run(
        "create_flow",
        {
            "name": "n",
            "triggerId": "t",
            "deviceIds": [CLOUD_ID],
            "actions": [{"actionId": "a", "position": 1}],
        },
    )
    assert rec.body()["actions"] == [{"actionId": "a", "position": 1}]
    assert rec.body()["deviceIds"] == [CLOUD_ID]

    await run(
        "manage_flow", {"operation": "execution_metrics", "from": "2026-01-01", "triggerId": "t"}
    )
    assert rec.last.url.params.get("from") == "2026-01-01"
    assert rec.last.url.params.get("triggerId") == "t"

    rec.reply = {"data": [{"id": 1}]}
    out = await run(
        "manage_flow",
        {
            "operation": "replace_actions",
            "flowId": "f",
            "actions": [{"actionId": "a", "position": 1}],
        },
    )
    assert out == {"items": [{"id": 1}]}


async def test_platform_catalog_and_apps(rec):
    rec.reply = {"data": []}
    await run("platform_catalog", {"catalog": "timezones"})
    assert "timezones" in path(rec.last)
    rec.reply = {"items": []}
    await run("apps", {"operation": "list", "platform": "android", "pageSize": 10})
    assert rec.last.url.params.get("platform") == "android"
    with pytest.raises(ToolError, match="requires id"):
        await run("apps", {"operation": "get"})


# ---- task helpers -----------------------------------------------------------------------------


async def test_task_helpers(rec):
    rt = Runtime(Config(cloud_api_key="dr_sk_test"))
    rec.reply = {"id": "t1", "status": "queued"}
    await cloud.cloud_run_task(
        rt,
        deviceId=DEV,
        task="open settings",
        maxSteps=5,
        credentials=[{"credentialNames": ["a"], "packageName": "p"}],
    )
    body = rec.body()
    assert body["deviceId"] == CLOUD_ID and body["maxSteps"] == 5
    assert body["credentials"] == [{"credentialNames": ["a"], "packageName": "p"}]

    rec.reply = {"task": {"id": "t1", "trajectory": [1], "temperature": 0.1, "status": "done"}}
    assert await cloud.cloud_get_task(rt, "t1") == {"id": "t1", "status": "done"}

    rec.reply = {"trajectory": list(range(60))}
    page = await cloud.cloud_get_task(rt, "t1", view="trajectory", offset=10)
    assert page["eventCount"] == 50 and page["total"] == 60
    assert not page["hasMore"] and "nextOffset" not in page
    assert page["events"][0] == 10

    rec.reply = {
        "items": [{"taskId": "a", "createdAt": "c", "updatedAt": "u", "x": 1}],
        "pagination": {},
    }
    out = await cloud.cloud_list_tasks(rt, deviceId=DEV)
    assert out["deviceScoped"] and out["items"] == [
        {"taskId": "a", "createdAt": "c", "updatedAt": "u"}
    ]
    with pytest.raises(ToolError, match="only valid without `deviceId`"):
        await cloud.cloud_list_tasks(rt, deviceId=DEV, status="running")
    with pytest.raises(ToolError, match="only valid with `deviceId`"):
        await cloud.cloud_list_tasks(rt, orderBy="assignedAt")

    rec.reply = {"items": []}
    await cloud.cloud_list_devices(rt)
    states = rec.last.url.params.get_list("state") or rec.last.url.params.get("state")
    assert "terminated" not in str(states) and "ready" in str(states)

    await cloud.cloud_get_task_media(rt, "t1", kind="ui_state", index=3)
    assert path(rec.last) == "/tasks/t1/ui_states/3"

    await cloud.cloud_send_task_message(rt, "t1", "go on")
    assert rec.body() == {"message": "go on"}


# ---- local devices ----------------------------------------------------------------------------


class FakeCore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def call(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        return "2026-09-25T10:00:00" if method == "time" else None

    async def capabilities(self):
        return {"backend": "local-android-adb", "actions": ["tap"]}


class FakeSession:
    has_adb = True

    def __init__(self) -> None:
        self.core = FakeCore()
        self.log: list[tuple] = []
        self.shells: list[str] = []

    async def ensure_connected(self):
        return None

    async def tap_xy(self, x, y):
        self.log.append(("tap", x, y))

    async def swipe_xy(self, *a):
        self.log.append(("swipe", *a))

    async def input_text(self, text, clear=False):
        self.log.append(("type", text, clear))

    async def press(self, name):
        self.log.append(("press", name))

    async def start_app(self, pkg, activity=None):
        self.log.append(("start", pkg, activity))

    async def shell(self, cmd, timeout=30.0, check=True):
        self.shells.append(cmd)
        if cmd.startswith("stat"):
            return "5\n"
        if cmd.startswith("base64"):
            return "aGVs\nbG8=\n"
        return ""

    def require_adb(self, feature):
        return None

    async def raw_state(self):
        return {
            "phone_state": {"currentApp": "Home"},
            "a11y_tree": {
                "boundsInScreen": {"left": 0, "top": 0, "right": 10, "bottom": 10},
                "children": [
                    {
                        "text": "Chrome",
                        "isClickable": True,
                        "boundsInScreen": {"left": 0, "top": 0, "right": 10, "bottom": 10},
                    }
                ],
            },
        }

    async def screenshot(self):
        return b"PNG"

    portal = None


@pytest.fixture
def local(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(Runtime, "session", lambda self, device=None: fake)
    return fake


async def test_local_device_action_and_ui(local, rec):
    await run("device_action", {"operation": "tap", "deviceId": "emulator-5554", "x": 3, "y": 4})
    await run(
        "device_action",
        {"operation": "keyboard_write", "deviceId": "emulator-5554", "text": "hey", "clear": True},
    )
    await run(
        "device_action", {"operation": "global_action", "deviceId": "emulator-5554", "action": 1}
    )
    await run(
        "device_action", {"operation": "keyboard_key", "deviceId": "emulator-5554", "key": 66}
    )
    assert local.log == [("tap", 3, 4), ("type", "hey", True), ("press", "back")]
    assert local.shells == ["input keyevent 66"]
    out = await run("get_device_ui_state", {"deviceId": "emulator-5554"})
    assert out["nodes"][0]["t"] == "Chrome"
    shot = await run("get_device_screenshot", {"deviceId": "emulator-5554"})
    assert base64.b64decode(shot["screenshot"]) == b"PNG"
    assert not rec.requests


async def test_local_apps_files_config(local, rec):
    await run(
        "manage_device_apps",
        {"operation": "start", "deviceId": "x:5555", "packageName": "com.a", "activity": ".M"},
    )
    await run(
        "manage_device_apps", {"operation": "stop", "deviceId": "x:5555", "packageName": "com.a"}
    )
    assert ("start", "com.a", ".M") in local.log
    assert local.core.calls[-1][0] == "stop_app"
    with pytest.raises(ToolError, match="need a Mobilerun Cloud device"):
        await run(
            "manage_device_apps",
            {"operation": "install", "deviceId": "x:5555", "packageName": "com.a"},
        )

    out = await run(
        "manage_device_files", {"operation": "download", "deviceId": "x:5555", "path": "/sdcard/a"}
    )
    assert out == {"content": "aGVsbG8="}

    out = await run("configure_device", {"operation": "get_time", "deviceId": "x:5555"})
    assert out == {"time": "2026-09-25T10:00:00"}
    for op, args in (("reset", {}), ("rename", {"name": "n"})):
        with pytest.raises(ToolError, match="needs a Mobilerun Cloud device"):
            await run("manage_device", {"operation": op, "deviceId": "x:5555", **args})
    with pytest.raises(ToolError, match="needs a Mobilerun Cloud device"):
        await run("manage_esim", {"operation": "list", "deviceId": "x:5555"})
    with pytest.raises(ToolError, match="needs a Mobilerun Cloud device"):
        await run("terminate_device", {"deviceId": "x:5555"})
    caps = await run("manage_device", {"operation": "get_capabilities", "deviceId": "x:5555"})
    assert caps["backend"] == "local-android-adb"
    assert not rec.requests
