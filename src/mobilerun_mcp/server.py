"""FastMCP server assembly."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from . import guide as guide_mod
from .config import Config
from .marks import format_marks
from .session import Runtime
from .tools import (
    adbtool,
    apps,
    device,
    files,
    legacy,
    media,
    notifications,
    perception,
    plan,
    waiting,
)
from .tools import (
    browser as browser_tools,
)
from .tools import input as input_tools
from .tools import intents as intent_tools

INSTRUCTIONS = """\
You control an Android device (an x86_64 redroid) through numbered marks and typed actions.

Loop: perceive_screen -> act -> read the post_action_observation -> repeat.
- perceive_screen returns numbered marks (som_id) and an annotated screenshot. ids describe ONE
  captured screen; every gesture, launch, scroll or key press makes them stale (stale_som_id).
  Perceive again before tapping something new.
- Every state-changing tool settles first, then returns post_action_observation (foreground app,
  element count, keyboard, top labels, screen_changed). Read it instead of re-perceiving.
- The screen is ground truth. Never tap coordinates from memory.

Prefer typed tools over tapping through apps: system_intent (alarm, timer, dial, sms, calendar,
share, navigate), read_notifications / notification_action, media_control, open_deeplink,
launch_app by name.

Text entry: focus the field first (tap or som_id), type_text, and confirm the value read back.
Search bars submit with press_enter.

When something fails, change strategy: re-perceive, scroll, press_back and try another path,
then ask the user. A policy_blocked error is a decision, never an obstacle to route around.
Use set_plan / mark_step / record_finding for multi-step goals and end_session honestly.
"""


POLICY_TEXT = """\
Safety policy modes (MOBILERUN_MCP_POLICY): off (default) | standard | strict.
standard blocks banking/payment/wallet, authenticator and password-manager apps, payment card
numbers and card security codes. strict also refuses password/PIN fields and national-id numbers.
Blocked actions fail with [policy_blocked]; that is a decision, not an obstacle. run_task and the
adb tool are disabled while a policy is on because they cannot be policed.
"""


def register_resources(mcp: FastMCP, runtime: Runtime) -> None:
    @mcp.resource("mobilerun://guide")
    def guide_resource() -> str:
        """Usage guide for this server."""
        return guide_mod.guide()

    @mcp.resource("mobilerun://policy")
    def policy_resource() -> str:
        """Safety policy boundaries and the active mode."""
        return f"active mode: {runtime.config.policy}\n\n{POLICY_TEXT}"

    @mcp.resource("mobilerun://ledger")
    async def ledger_resource() -> dict:
        """The current plan and recorded findings for the default device."""
        return runtime.session().ledger.to_dict()

    @mcp.resource("mobilerun://device/snapshot")
    async def snapshot_resource() -> str:
        """A live text snapshot of the default device's screen."""
        screen, marks = await runtime.session().perceive()
        return (
            f"{screen.phone.app} ({screen.phone.package}) keyboard={screen.phone.keyboard_visible}\n"
            + format_marks(marks)
        )

    @mcp.prompt
    def perceive_act_verify(goal: str) -> str:
        """Work toward a goal on the device one verified step at a time."""
        return (
            f"Goal: {goal}\n\nStart with perceive_screen. Act with one tool, read its "
            "post_action_observation, and confirm the outcome before the next step. Re-perceive before "
            "tapping anything new (ids go stale). If an approach fails, change strategy."
        )

    @mcp.prompt
    def research_then_act(goal: str, app: str) -> str:
        """Look up the flow first, then execute it against the live screen."""
        return (
            f"Goal: {goal} (in {app}).\n\nFirst web_search 'how to <task> in {app} android', turn the "
            "answer into set_plan steps, then execute them. The screen overrules the article."
        )


def build_server(config: Config | None = None) -> FastMCP:
    config = config or Config.from_env()
    runtime = Runtime(config)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await runtime.aclose()

    mcp = FastMCP("mobilerun", instructions=INSTRUCTIONS, lifespan=lifespan)
    for module in (
        perception,
        input_tools,
        apps,
        device,
        legacy,
        notifications,
        media,
        files,
        intent_tools,
        waiting,
        plan,
        browser_tools,
    ):
        module.register(mcp, runtime)
    if config.enable_adb:
        adbtool.register(mcp, runtime)
    register_resources(mcp, runtime)
    if "write" not in config.scopes:
        mcp.disable(tags={"write"})
    if "read" not in config.scopes:
        mcp.disable(tags={"read"})
    return mcp


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    build_server().run()


if __name__ == "__main__":
    main()
