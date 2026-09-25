"""Parity with the upstream projects: any call valid for AURA's MCP, droidrun's official
mobilerun-mcp, the mobilerun-core Device API or the mobilerun agent must be valid here.

Specs live in src/mobilerun_mcp/upstream/ (extracted from the upstream sources).
"""

from __future__ import annotations

import asyncio
import json
from importlib import resources

import pytest
from fastmcp import Client

from mobilerun_mcp.config import Config
from mobilerun_mcp.server import build_server

TYPES = {
    "number": {"number", "integer"},
    "integer": {"integer", "number"},
    "string": {"string"},
    "boolean": {"boolean"},
    "array": {"array"},
    "object": {"object"},
}


def spec(name: str) -> dict:
    return json.loads((resources.files("mobilerun_mcp.upstream") / name).read_text())


async def _surface() -> tuple[dict[str, dict], set[str], set[str]]:
    cfg = Config(enable_adb=True, device="x:1", cloud_api_key="dr_sk_test")
    async with Client(build_server(cfg)) as client:
        tools = {
            t.name: (getattr(t, "input_schema", None) or t.inputSchema)
            for t in await client.list_tools()
        }
        uris = {str(r.uri) for r in await client.list_resources()}
        prompts = {p.name for p in await client.list_prompts()}
    return tools, uris, prompts


@pytest.fixture(scope="module")
def surface():
    return asyncio.run(_surface())


def _types(prop: dict) -> set[str]:
    if "type" in prop:
        t = prop["type"]
        return set(t) if isinstance(t, list) else {t}
    out: set[str] = set()
    for alt in prop.get("anyOf", []) + prop.get("oneOf", []):
        out |= _types(alt)
    return out or {"any"}


def check_compatible(tool: str, upstream: dict, ours: dict) -> list[str]:
    problems = []
    props = ours.get("properties", {})
    for name, uprop in upstream.get("properties", {}).items():
        if name not in props:
            problems.append(f"{tool}: missing parameter {name!r}")
            continue
        wants = _types(uprop) - {"any", "null"}
        have = _types(props[name])
        accepted = set().union(*(TYPES.get(w, {w}) for w in wants)) if wants else set()
        if wants and "any" not in have and not (accepted & have):
            problems.append(f"{tool}.{name}: type {sorted(have)} does not accept {sorted(wants)}")
    for req in ours.get("required", []):
        if req not in upstream.get("required", []):
            problems.append(f"{tool}: requires {req!r}, which the upstream call may omit")
    return problems


def test_every_aura_tool_accepts_aura_calls(surface):
    tools, _, _ = surface
    aura = spec("aura_tools.json")["tools"]
    problems = []
    for name, upstream in aura.items():
        if name not in tools:
            problems.append(f"missing AURA tool {name}")
            continue
        problems += check_compatible(name, upstream, tools[name])
    assert not problems, "\n".join(problems)


def test_aura_adb_alias_and_resources_and_prompts(surface):
    tools, uris, prompts = surface
    meta = spec("aura_tools.json")
    assert "aura-adb" in tools
    assert set(meta["resources"]) <= uris, set(meta["resources"]) - uris
    assert set(meta["prompts"]) <= prompts, set(meta["prompts"]) - prompts


def test_every_official_mobilerun_mcp_tool_accepts_its_calls(surface):
    tools, _, _ = surface
    problems = []
    for entry in spec("official_mcp_tools.json"):
        name = entry["name"]
        if name not in tools:
            problems.append(f"missing official tool {name}")
            continue
        problems += check_compatible(name, entry["inputSchema"], tools[name])
    assert not problems, "\n".join(problems)


def test_every_mobilerun_core_device_method_is_a_tool(surface):
    tools, _, _ = surface
    api = spec("mobilerun_api.json")
    problems = []
    for method, params in api["device_api"].items():
        if method == "center_of":  # static geometry helper, not a device operation
            continue
        if method not in tools:
            problems.append(f"missing core method tool {method}")
            continue
        props = tools[method].get("properties", {})
        problems += [f"{method}: missing parameter {p!r}" for p in params if p not in props]
    assert not problems, "\n".join(problems)


def test_every_mobilerun_agent_action_is_a_tool(surface):
    tools, _, _ = surface
    problems = []
    for action, params in spec("mobilerun_api.json")["agent_actions"].items():
        if action not in tools:
            problems.append(f"missing agent action {action}")
            continue
        props = tools[action].get("properties", {})
        problems += [f"{action}: missing parameter {p!r}" for p in params if p not in props]
    assert not problems, "\n".join(problems)
