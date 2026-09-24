"""The README's tool reference must describe exactly the tools the server registers."""

import asyncio
import json
import re
from pathlib import Path

import jsonschema
import pytest
from fastmcp import Client

from mobilerun_mcp.config import Config
from mobilerun_mcp.server import build_server

README = Path(__file__).resolve().parents[2] / "README.md"


def reference_section() -> str:
    text = README.read_text()
    start = text.index("## Tool reference")
    return text[start : text.index("\n## ", start + 1)]


async def tool_schemas(**config) -> dict[str, dict]:
    async with Client(build_server(Config(**config))) as client:
        tools = await client.list_tools()
    return {t.name: (getattr(t, "input_schema", None) or t.inputSchema) for t in tools}


@pytest.fixture(scope="module")
def schemas():
    return asyncio.run(tool_schemas(enable_adb=True))


@pytest.fixture(scope="module")
def section():
    return reference_section()


def test_every_tool_is_in_the_reference_table_and_nothing_else(schemas, section):
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))
    assert documented == set(schemas), {
        "undocumented": sorted(set(schemas) - documented),
        "unknown": sorted(documented - set(schemas)),
    }


def test_every_tool_has_an_example_call(schemas, section):
    with_examples = {name for name, _ in re.findall(r"^([a-z_]+) (\{.*\})$", section, re.M)}
    assert set(schemas) - with_examples == set()


def test_example_calls_are_valid_for_their_tool(schemas, section):
    examples = re.findall(r"^([a-z_]+) (\{.*\})$", section, re.M)
    assert len(examples) >= len(schemas)
    for tool, arguments in examples:
        jsonschema.validate(json.loads(arguments), schemas[tool])


def test_readme_states_the_correct_tool_count():
    default_tools = asyncio.run(tool_schemas())
    assert f"{len(default_tools)} tools" in README.read_text()
