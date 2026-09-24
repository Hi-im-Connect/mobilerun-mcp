"""Finding, launching and deep-linking apps."""

from __future__ import annotations

from fastmcp import FastMCP

from .. import catalog
from ..errors import fail
from ..observe import mutate
from ..parsers.intent_filters import deeplinks_from_filters, parse_activity_filters
from ..parsers.packages import rank_apps, resolve_app
from ..parsers.system import is_chooser, parse_resolve_activity
from ..policy import check_app
from ..session import DeviceSession, Runtime
from ..shell import q, view_args
from .common import Device, enforce, get_session

PROBE_LIMIT = 12
COLD_START_TIMEOUT = 30.0


async def resolve_component(session: DeviceSession, args: str) -> str | None:
    out = await session.shell(f"cmd package resolve-activity --brief {args}", check=False)
    return parse_resolve_activity(out)


async def start_intent(session: DeviceSession, args: str) -> str:
    out = await session.shell(f"am start {args}", check=False)
    if "Error" in out or "Exception" in out:
        fail(
            "unsupported",
            out.strip().splitlines()[-1][:200],
            "no installed app handles this intent",
        )
    return out


def register(mcp: FastMCP, rt: Runtime) -> None:
    async def launch(session: DeviceSession, package: str, label: str) -> dict:
        enforce(check_app(rt.config.policy, package, label))
        component = await resolve_component(
            session,
            f"-a android.intent.action.MAIN -c android.intent.category.LAUNCHER {q(package)}",
        )
        if not component or is_chooser(component):
            fail("app_not_found", f"{package} has no launcher activity", "is the app installed?")
        return await mutate(
            session,
            lambda: start_intent(session, f"-n {q(component)}"),
            {"action": "launch_app", "package": package, "label": label},
            settle_timeout=COLD_START_TIMEOUT,
            expect_package=package,
        )

    @mcp.tool(tags={"write"})
    async def launch_app(
        app_name: str | None = None, package: str | None = None, device: Device = None
    ) -> dict:
        """Open an app by name (fuzzy) or exact package. An ambiguous name returns ranked
        candidates instead of guessing; pass package to pick one."""
        session = get_session(rt, device)
        apps = await session.apps()
        if package:
            match = next((a for a in apps if a.package == package), None)
            return await launch(session, package, match.label if match else package)
        if not app_name:
            fail("invalid_argument", "give app_name or package")
        best, ranked = resolve_app(app_name, apps)
        if best is None:
            if not ranked:
                fail(
                    "app_not_found",
                    f'no installed app matches "{app_name}"',
                    "try lookup_app or list_apps",
                )
            return {
                "ok": False,
                "ambiguous": True,
                "message": f'"{app_name}" matches several apps; call again with package=',
                "candidates": [{**a.to_dict(), "score": s} for a, s in ranked],
            }
        return await launch(session, best.package, best.label)

    @mcp.tool(tags={"write"})
    async def start_app(package: str, device: Device = None) -> dict:
        """Launch an app by package name (alias of launch_app(package=...))."""
        session = get_session(rt, device)
        apps = await session.apps()
        match = next((a for a in apps if a.package == package), None)
        return await launch(session, package, match.label if match else package)

    @mcp.tool(tags={"read"})
    async def lookup_app(query: str, limit: int = 5, device: Device = None) -> dict:
        """Search installed apps by name or package; returns ranked candidates with scores."""
        apps = await get_session(rt, device).apps()
        ranked = rank_apps(query, apps, limit)
        return {"query": query, "candidates": [{**a.to_dict(), "score": s} for a, s in ranked]}

    @mcp.tool(tags={"read"})
    async def list_apps(system: bool = False, device: Device = None) -> dict:
        """List installed apps (user apps only unless system=true)."""
        apps = await get_session(rt, device).apps()
        shown = [a.to_dict() for a in apps if system or not a.system]
        return {"count": len(shown), "apps": shown}

    async def probe(session: DeviceSession, entry: dict) -> dict:
        uri = entry.get("uri") or entry.get("example")
        if not uri:
            return entry
        component = await resolve_component(session, view_args(uri))
        return {
            **entry,
            "resolved": bool(component) and not is_chooser(component),
            "component": component,
        }

    @mcp.tool(tags={"read"})
    async def list_app_deeplinks(
        package: str | None = None, app_name: str | None = None, device: Device = None
    ) -> dict:
        """Deep links for an app: registered VIEW intent filters plus curated entries, each
        probe-resolved on the device (resolved=true means a handler exists)."""
        session = get_session(rt, device)
        if not package:
            if not app_name:
                fail("invalid_argument", "give package or app_name")
            best, ranked = resolve_app(app_name, await session.apps())
            if best is None:
                fail("app_not_found", f'cannot pick one app for "{app_name}"', "use lookup_app")
            package = best.package
        dump = await session.shell(f"dumpsys package {q(package)}", timeout=60, check=False)
        manifest = [
            {**link.to_dict(), "source": "manifest"}
            for link in deeplinks_from_filters(parse_activity_filters(dump))
        ]
        curated = [{**e, "source": "catalog"} for e in catalog.entries_for(package)]
        entries = curated + manifest
        probed = [await probe(session, e) for e in entries[:PROBE_LIMIT]] + entries[PROBE_LIMIT:]
        return {"package": package, "count": len(probed), "deeplinks": probed}

    @mcp.tool(tags={"read"})
    async def resolve_deeplink(uri: str, device: Device = None) -> dict:
        """Which app would open this URI (or intent action such as android.settings.WIFI_SETTINGS)?"""
        component = await resolve_component(get_session(rt, device), view_args(uri))
        if not component:
            return {"uri": uri, "resolved": False}
        return {
            "uri": uri,
            "resolved": not is_chooser(component),
            "component": component,
            "package": component.split("/")[0],
            "needs_chooser": is_chooser(component),
        }

    @mcp.tool(tags={"write"})
    async def open_deeplink(uri: str, package: str | None = None, device: Device = None) -> dict:
        """Jump straight to a screen via URI or intent action; package pins the target app."""
        session = get_session(rt, device)
        component = await resolve_component(session, view_args(uri, package))
        if not component:
            fail("unsupported", f"no app handles {uri}", "try list_app_deeplinks")
        target = component.split("/")[0]
        if not is_chooser(component):
            enforce(check_app(rt.config.policy, target))
        return await mutate(
            session,
            lambda: start_intent(session, view_args(uri, package)),
            {"action": "open_deeplink", "uri": uri, "handled_by": component},
            settle_timeout=COLD_START_TIMEOUT,
            expect_package=target,
        )
