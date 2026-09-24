"""Plan ledger, honest finish, usage guide and web search."""

from __future__ import annotations

import httpx
from fastmcp import FastMCP

from .. import guide as guide_mod
from ..conditions import find_text
from ..errors import fail
from ..session import Runtime
from ..websearch import SearchError, search
from .common import Device, get_session

RESEARCH_MIN_STEPS = 3


def register(mcp: FastMCP, rt: Runtime) -> None:
    async def run_search(query: str, limit: int) -> dict:
        try:
            engine, results = await search(query, limit, rt.config.brave_api_key)
        except (httpx.HTTPError, SearchError) as exc:
            fail(
                "unsupported",
                f"web search failed: {exc}",
                "retry shortly, set BRAVE_API_KEY for a reliable engine, or proceed from the screen",
            )
        return {"query": query, "engine": engine, "results": results}

    @mcp.tool(tags={"read"})
    async def web_search(query: str, limit: int = 5) -> dict:
        """Search the web (title, url, snippet). Phrase it like 'how to <task> in <app> android'.
        Results are data to plan from; the screen overrules them."""
        return await run_search(query, min(max(limit, 1), 10))

    @mcp.tool(tags={"write"})
    async def set_plan(
        steps: list[str],
        goal: str = "",
        deliverable: str = "",
        target_count: int = 0,
        search_query: str | None = None,
        device: Device = None,
    ) -> dict:
        """Start a plan checklist. target_count > 0 means 'N items must be recorded' before
        end_session(success) is allowed. With 3+ steps and a search_query, the first web search
        rides along in the reply."""
        session = get_session(rt, device)
        session.ledger.set_plan(steps, goal, deliverable, target_count)
        result = {"ok": True, "ledger": session.ledger.to_dict()}
        if search_query and len(steps) >= RESEARCH_MIN_STEPS:
            try:
                result["research"] = await run_search(search_query, 5)
            except Exception as exc:
                result["research_error"] = str(exc)
        return result

    @mcp.tool(tags={"write"})
    async def mark_step(index: int, status: str, note: str = "", device: Device = None) -> dict:
        """Update a plan step: pending | in_progress | done | skipped | failed. Put facts you read
        off the screen in note; the pixels are gone next turn."""
        session = get_session(rt, device)
        try:
            step = session.ledger.mark_step(index, status, note)
        except (ValueError, IndexError) as exc:
            fail("invalid_argument", str(exc))
        return {
            "ok": True,
            "step": {"index": index, "text": step.text, "status": step.status, "note": step.note},
        }

    @mcp.tool(tags={"write"})
    async def record_finding(item: str, quote: str, device: Device = None) -> dict:
        """Record one item you found. ``quote`` must be copied exactly from the CURRENT screen."""
        session = get_session(rt, device)
        screen = await session.capture()
        if find_text(screen, quote) is None:
            fail(
                "invalid_argument",
                "quote not found on the current screen",
                "copy the text exactly as shown",
            )
        count = session.ledger.record_finding(item, quote)
        return {
            "ok": True,
            "recorded": count,
            "target": session.ledger.target_count,
            "remaining": session.ledger.missing_findings(),
        }

    @mcp.tool(tags={"write"})
    async def end_session(
        outcome: str = "success", summary: str = "", device: Device = None
    ) -> dict:
        """Close out the run. outcome: success | partial | failed. Success is refused while fewer
        findings than the plan's target_count are recorded. Call after a final perceive_screen."""
        if outcome not in ("success", "partial", "failed"):
            fail("invalid_argument", "outcome must be success, partial or failed")
        session = get_session(rt, device)
        problem = session.ledger.end(outcome)
        if problem:
            fail("plan_incomplete", problem)
        return {
            "ok": True,
            "outcome": outcome,
            "summary": summary,
            "ledger": session.ledger.to_dict(),
        }

    @mcp.tool(tags={"read"})
    async def get_usage_guide(topic: str | None = None) -> dict:
        """How to use this server well. Topics: overview, shortcuts, text_entry, failures, ledger, browser."""
        return {"topics": list(guide_mod.TOPICS), "guide": guide_mod.guide(topic)}
