"""Capture real device output as test fixtures (sanitized). Run with the device reachable.

.venv/bin/python scripts/capture_fixtures.py [serial]
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from mobilerun_mcp.adb import Adb
from mobilerun_mcp.portal import PortalClient

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text: str) -> str:
    return EMAIL.sub("user@example.com", text)


def save_text(name: str, text: str) -> None:
    (OUT / name).write_text(scrub(text))


async def main(serial: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    adb = Adb(serial)
    portal = PortalClient(adb)
    await portal.connect()

    async def snap(name: str, shot: bool = False) -> None:
        await asyncio.sleep(1.8)
        state = await portal.state()
        save_text(f"state_{name}.json", json.dumps(state, indent=1))
        if shot:
            (OUT / f"screenshot_{name}.png").write_bytes(await portal.screenshot())
        print("captured state", name)

    async def sh(cmd: str) -> str:
        return await adb.shell(cmd, timeout=60, check=False)

    # ---- UI states -------------------------------------------------------
    await sh("input keyevent KEYCODE_HOME")
    await snap("home", shot=True)
    await sh("am start -n com.android.contacts/.activities.PeopleActivity")
    await snap("contacts")
    await sh("input tap 664 104")
    await asyncio.sleep(1)
    await portal.input_text("ali")
    await snap("contacts_search")
    await sh("input keyevent KEYCODE_BACK; input keyevent KEYCODE_HOME")
    await sh("am start -n com.android.settings/.Settings")
    await snap("settings")
    await sh("input keyevent KEYCODE_HOME; input swipe 360 1200 360 400 400")
    await snap("drawer")
    await sh("input keyevent KEYCODE_HOME; cmd statusbar expand-notifications")
    await snap("shade")
    await sh("cmd statusbar collapse; input keyevent KEYCODE_HOME")
    await sh("am start -n com.android.deskclock/.DeskClock")
    await snap("clock")
    await sh("am start -n org.chromium.webview_shell/.WebViewBrowserActivity")
    await snap("webview")
    await sh("input keyevent KEYCODE_HOME")

    # ---- shell dumps -----------------------------------------------------
    (OUT / "packages.json").write_text(json.dumps(await portal.apps(), indent=1))
    dumps = {
        "dumpsys_notification.txt": "dumpsys notification --noredact",
        "media_sessions_list.txt": "cmd media_session list-sessions",
        "dumpsys_media_session.txt": "dumpsys media_session",
        "dumpsys_battery.txt": "dumpsys battery",
        "dumpsys_power_wake.txt": "dumpsys power | grep -E 'mWakefulness|mScreenOn|Display Power'",
        "dumpsys_window_focus.txt": "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'",
        "dumpsys_audio_streams.txt": "dumpsys audio | grep -A14 -m1 'Stream volumes'",
        "wm_size.txt": "wm size",
        "wm_density.txt": "wm density",
        "getprop_subset.txt": "getprop | grep -E 'ro.product.(model|name)|ro.build.version.(release|sdk)|"
        "ro.hardware|ro.product.cpu.abi'",
        "contacts_phones.txt": "content query --uri content://com.android.contacts/data/phones "
        "--projection display_name:data1",
        "resolve_launcher_clock.txt": "cmd package resolve-activity --brief "
        "-c android.intent.category.LAUNCHER com.android.deskclock",
        "resolve_view_https.txt": "cmd package resolve-activity --brief "
        "-a android.intent.action.VIEW -d https://www.facebook.com/",
        "devtools_sockets.txt": "cat /proc/net/unix | grep -o '@[a-z_]*devtools_remote[_0-9a-z]*'",
        "find_sdcard.txt": "find /sdcard -maxdepth 2 2>/dev/null | head -40",
        "df_data.txt": "df /data",
        "ip_addr.txt": "ip -4 addr show",
        "pkg_eyecon.txt": "dumpsys package com.eyecon.global",
        "pkg_settings.txt": "dumpsys package com.android.settings",
    }
    for name, cmd in dumps.items():
        save_text(name, await sh(cmd))
        print("captured", name)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else os.environ["MOBILERUN_DEVICE"]))
