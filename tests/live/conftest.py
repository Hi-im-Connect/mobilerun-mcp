"""Live tests drive the real device through an in-process MCP client. Run: pytest -m live."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastmcp import Client

from mobilerun_mcp.config import Config
from mobilerun_mcp.server import build_server

DEVICE = os.environ.get("MOBILERUN_DEVICE", "")


class Phone:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def call(self, tool: str, /, **args):
        """Return the structured result (dict) or, for list results, the raw content blocks."""
        result = await self.client.call_tool(tool, args)
        if result.structured_content is not None:
            data = result.structured_content
            return data["result"] if list(data) == ["result"] else data
        texts = [c for c in result.content if c.type == "text"]
        if len(result.content) == 1 and texts:
            try:
                return json.loads(texts[0].text)
            except json.JSONDecodeError:
                return texts[0].text
        return result.content

    async def call_error(self, tool: str, /, **args) -> str:
        with pytest.raises(Exception) as info:
            await self.client.call_tool(tool, args)
        return str(info.value)

    async def perceive(self, **args) -> dict:
        """perceive_screen's JSON part (the tool returns [json, image?])."""
        result = await self.client.call_tool("perceive_screen", {"include_image": False, **args})
        text = next(c.text for c in result.content if c.type == "text")
        return json.loads(text)

    async def elements(self) -> str:
        return (await self.perceive())["elements"]

    async def home(self):
        await self.call("press_home")

    async def shell(self, command: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "adb",
            "-s",
            DEVICE,
            "shell",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return out.decode()


@pytest.fixture
async def phone():
    if not DEVICE:
        pytest.skip("set MOBILERUN_DEVICE to run the live tests")
    async with Client(build_server(Config(device=DEVICE))) as client:
        p = Phone(client)
        await p.home()
        yield p
        await p.home()
