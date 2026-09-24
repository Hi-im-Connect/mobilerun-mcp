"""Tools kept from the first version of this server, plus the natural-language agent."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from fastmcp import FastMCP

from ..errors import fail
from ..observe import mutate
from ..session import Runtime
from .common import Device, get_session
from .input import KEY_BACK, KEY_ENTER, KEY_HOME

KEYS = {"home": KEY_HOME, "back": KEY_BACK, "enter": KEY_ENTER}
RUN_TIMEOUT = 1800.0
GOAL = re.compile(r"(Goal (?:achieved|failed): .*)")


def _mobilerun_bin(configured: str | None) -> str:
    return configured or shutil.which("mobilerun") or str(Path.home() / ".local/bin/mobilerun")


def register(mcp: FastMCP, rt: Runtime) -> None:
    @mcp.tool(tags={"write"})
    async def press(button: str, device: Device = None) -> dict:
        """Press home, back or enter (kept for old clients; prefer press_home/back/enter)."""
        if button not in KEYS:
            fail("invalid_argument", "button must be home, back or enter")
        session = get_session(rt, device)
        return await mutate(
            session,
            lambda: session.shell(f"input keyevent {KEYS[button]}"),
            {"action": f"press_{button}"},
        )

    @mcp.tool(tags={"write"})
    async def run_task(
        task: str,
        vision: bool = False,
        reasoning: bool = False,
        steps: int = 15,
        device: Device = None,
    ) -> dict:
        """Hand a goal to the Mobilerun LLM agent (best-effort: its self-reported result can be
        wrong, so verify with perceive_screen). Disabled when the safety policy is on."""
        if rt.config.policy != "off":
            fail(
                "policy_blocked",
                "run_task acts on its own and cannot be policed",
                "use step-by-step tools",
            )
        session = get_session(rt, device)
        args = ["run", "-d", session.serial, "--steps", str(steps)]
        if vision:
            args.append("--vision")
        if reasoning:
            args.append("--reasoning")
        proc = await asyncio.create_subprocess_exec(
            _mobilerun_bin(rt.config.mobilerun_bin),
            *args,
            task,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), RUN_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            fail("timeout", f"agent exceeded {RUN_TIMEOUT:.0f}s")
        text = out.decode("utf-8", "replace")
        match = GOAL.findall(text)
        return {
            "ok": proc.returncode == 0,
            "result": match[-1] if match else "",
            "output_tail": text[-1500:],
            "note": "agent self-report; verify with perceive_screen",
        }
