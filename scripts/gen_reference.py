"""Regenerate the README's "## Tool reference" section from the live tool schemas.

    .venv/bin/python scripts/gen_reference.py        # rewrites README.md in place

tests/unit/test_readme.py fails when the README and the server disagree.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import jsonschema

from mobilerun_mcp.config import Config
from mobilerun_mcp.server import build_server

README = Path(__file__).resolve().parents[1] / "README.md"

GROUPS = [
    (
        "perception",
        "Perception",
        "See the screen. `read_screen` (text grid) or `perceive_screen` (annotated image) first; act by `som_id`.",
    ),
    (
        "input",
        "Gestures, typing and keys",
        "Every action settles the screen and returns `post_action_observation`. Target with `x`/`y`, a `som_id`, or (mobilerun style) an `index` from `get_state`.",
    ),
    ("apps", "Apps and deep links", ""),
    ("intents", "System intents and contacts", ""),
    ("notifications", "Notifications", ""),
    ("media", "Media and volume", ""),
    ("files", "Files", ""),
    ("waiting", "Waiting and checking", ""),
    ("plan", "Plan, findings and research", ""),
    (
        "browser",
        "Browser",
        "Pages come back with numbered elements (`el_id`) and a `generation`; pass both to `browser_act`. Sessions: `scratch` (default) or `mine` (the user's signed-in browser).",
    ),
    (
        "core",
        "mobilerun-core Device API",
        "Same names and parameters as `mobilerun_core.Device`. Works on Android (adb or Portal HTTP), iOS and Mobilerun Cloud devices.",
    ),
    (
        "agent",
        "mobilerun agent actions",
        "The mobilerun agent's action set. Indices come from `get_state`.",
    ),
    (
        "tasks",
        "Agent tasks and macros",
        "`run_task` runs the Mobilerun agent locally (mobilerun CLI) or on a Mobilerun Cloud device.",
    ),
    (
        "cloud",
        "Mobilerun Cloud platform",
        "Same tools as droidrun's official mobilerun-mcp. Needs `MOBILERUN_CLOUD_API_KEY`; device tools also work on local devices where an equivalent exists.",
    ),
    ("device", "Devices and connection", ""),
    ("legacy", "Compatibility", ""),
    (
        "adbtool",
        "Raw adb",
        "Only when `MOBILERUN_MCP_ENABLE_ADB=1`; refused while a safety policy is on.",
    ),
]

EXAMPLES: dict[str, list[dict]] = {
    "perceive_screen": [{}, {"description": "search bar", "detail": "full"}],
    "tap": [{"som_id": 4}, {"x": 540, "y": 1200}],
    "long_press": [{"som_id": 4}, {"index": 7, "ms": 800}],
    "swipe": [
        {"x1": 360, "y1": 1000, "x2": 360, "y2": 300},
        {"coordinate": [360, 1000], "coordinate2": [360, 300], "duration": 0.5},
    ],
    "scroll_to": [{"text": "Battery"}, {"x1": 360, "y1": 900, "x2": 360, "y2": 400}],
    "type_text": [{"text": "hello", "som_id": 3, "submit": True}],
    "type": [{"text": "hello", "index": 5, "clear": True}],
    "launch_app": [{"app_name": "Clock"}, {"package_name": "com.android.settings", "force": True}],
    "open_deeplink": [
        {"uri": "android.settings.WIFI_SETTINGS"},
        {"uri": "app-shortcut://com.android.settings/manifest-shortcut-wifi"},
    ],
    "system_intent": [
        {"action": "set_alarm", "hour": 7, "minute": 30, "label": "wake"},
        {"action": "navigate", "destination": "Cairo Tower", "mode": "walk"},
    ],
    "browser_open": [{"url": "https://example.com"}],
    "browser_act": [
        {"action": "click", "el_id": 3, "generation": 1},
        {"action": "type", "el_id": 5, "value": "shoes"},
    ],
    "run_task": [{"task": "Open Clock and tell me the first alarm", "maxSteps": 20}],
    "find_nodes": [{"text_contains": "Wi"}],
    "device_action": [{"deviceId": "192.168.1.20:5555", "operation": "tap", "x": 540, "y": 1200}],
    "adb": [{"command": "shell dumpsys battery"}],
    "aura-adb": [{"command": "shell dumpsys window | grep mCurrentFocus"}],
}

NAME_SAMPLES = {
    "text": "Settings",
    "query": "wifi",
    "app_name": "Clock",
    "package": "com.android.settings",
    "package_name": "com.android.settings",
    "app_id": "com.android.settings",
    "uri": "https://example.com",
    "url": "https://example.com",
    "task": "Open Settings",
    "message": "hi",
    "expected": "Settings is open",
    "condition": "page loaded",
    "deviceId": "192.168.1.20:5555",
    "taskId": "local-1",
    "action": "list",
    "direction": "down",
    "button": "back",
    "secret_id": "MY_PASSWORD",
    "key": "0|com.example|1|null|10123",
    "js": "document.title",
    "permission": "android.permission.CAMERA",
    "path": "/sdcard/Download/a.apk",
    "deep_link": "https://example.com",
    "name": "Ali",
    "name_or_code": "back",
    "value": "copied text",
    "gesture_type": "launch_app",
    "command": "play",
    "item": "Result 1",
    "quote": "exact text",
    "file": "content://media/external/images/media/12",
    "id": "abc123",
    "resource": "flows",
    "target": "Settings",
    "kind": "screenshot",
    "view": "summary",
    "operation": "list",
}


def first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return (match.group(1) if match else text)[:220].replace("|", "/")


def arg_summary(schema: dict) -> str:
    props = {k: v for k, v in schema.get("properties", {}).items() if k != "device"}
    required = set(schema.get("required", []))
    parts = []
    for name, prop in props.items():
        if name in required:
            parts.append(name)
        elif "default" in prop and prop["default"] not in (None, "", [], {}):
            parts.append(f"[{name}={json.dumps(prop['default']).strip(chr(34))}]")
        else:
            parts.append(f"[{name}]")
    return " ".join(parts) or "none"


def sample(name: str, prop: dict):
    for alt in prop.get("anyOf", []):
        if alt.get("type") not in (None, "null"):
            prop = {**alt, **{k: v for k, v in prop.items() if k != "anyOf"}}
            break
    types = {prop.get("type")}
    if "enum" in prop:
        return prop["enum"][0]
    for alt in prop.get("anyOf", []):
        if "enum" in alt:
            return alt["enum"][0]
    if "string" in types:
        return NAME_SAMPLES.get(name, "value")
    if "integer" in types or "number" in types:
        return max(1, prop.get("minimum", 1), prop.get("exclusiveMinimum", 0) + 1)
    if "boolean" in types:
        return True
    if "array" in types:
        items = prop.get("items", {})
        return [sample(name.rstrip("s"), items)] if prop.get("minItems") else []
    if "object" in types:
        return {}
    return NAME_SAMPLES.get(name, "value")


def examples(name: str, schema: dict) -> list[dict]:
    chosen = EXAMPLES.get(name)
    if chosen is None:
        props = schema.get("properties", {})
        chosen = [{k: sample(k, props[k]) for k in schema.get("required", [])}]
    for args in chosen:
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as exc:
            raise SystemExit(f"{name}: example {args} is invalid: {exc.message}") from None
    return chosen


async def build() -> tuple[str, int]:
    server = build_server(Config(enable_adb=True))
    tools = await server.list_tools()
    default_count = len(await build_server(Config()).list_tools())
    by_group: dict[str, list] = {}
    for tool in tools:
        module = tool.fn.__module__.rsplit(".", 1)[-1]
        by_group.setdefault(module, []).append(tool)
    lines = [
        "## Tool reference",
        "",
        f"{default_count} tools, plus `adb` / `aura-adb` when `MOBILERUN_MCP_ENABLE_ADB=1`. Every tool "
        "that acts on a device takes an optional `device` (adb serial, `ios`, `cloud:<id>`, or a "
        "Portal URL). `[name=default]` is optional. Generated by `scripts/gen_reference.py`.",
    ]
    seen = set()
    for key, title, intro in GROUPS:
        group = by_group.get(key, [])
        if not group:
            continue
        lines += ["", f"### {title}", ""]
        if intro:
            lines += [intro, ""]
        lines += ["| Tool | What it does | Arguments |", "|---|---|---|"]
        calls = []
        for tool in group:
            schema = tool.parameters
            seen.add(tool.name)
            lines.append(
                f"| `{tool.name}` | {first_sentence(tool.description)} | {arg_summary(schema)} |"
            )
            calls += [f"{tool.name} {json.dumps(a)}" for a in examples(tool.name, schema)]
        lines += ["", "```", *calls, "```"]
    missing = {t.name for t in tools} - seen
    assert not missing, f"tools without a group: {missing}"
    return "\n".join(lines) + "\n", default_count


def main() -> None:
    section, count = asyncio.run(build())
    text = README.read_text()
    start = text.index("## Tool reference")
    end = text.index("\n## ", start + 1) + 1
    text = text[:start] + section + "\n" + text[end:]
    text = re.sub(r"\b\d+ tools\b", f"{count} tools", text)
    README.write_text(text)
    print(f"README reference regenerated: {count} tools")


if __name__ == "__main__":
    main()
