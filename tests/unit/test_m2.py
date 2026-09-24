from datetime import datetime

import pytest

from mobilerun_mcp.errors import McpToolError
from mobilerun_mcp.intents import VERBS, build_intent, epoch_ms
from mobilerun_mcp.parsers.a11y import parse_screen
from mobilerun_mcp.parsers.shade import find_clear_all, match_row, notification_rows
from mobilerun_mcp.shell import q, view_args
from mobilerun_mcp.tools.files import safe_path
from mobilerun_mcp.tools.input import scroll_vector

# ---- shell quoting --------------------------------------------------------------------


def test_q_survives_single_quotes():
    assert q("it's") == "'it'\\''s'"
    assert q("plain") == "'plain'"


def test_view_args_distinguishes_action_from_uri():
    assert view_args("android.settings.WIFI_SETTINGS") == "-a 'android.settings.WIFI_SETTINGS'"
    assert view_args("https://a.com/x", "com.app") == (
        "-a android.intent.action.VIEW -d 'https://a.com/x' -p 'com.app'"
    )


# ---- intents --------------------------------------------------------------------------


def test_set_alarm_args_and_validation():
    spec = build_intent("set_alarm", {"hour": 7, "minute": 30, "label": "wake"})
    assert (
        "SET_ALARM" in spec.args and "alarm.HOUR 7" in spec.args and "alarm.MINUTES 30" in spec.args
    )
    assert "'wake'" in spec.args and "SKIP_UI true" in spec.args
    assert build_intent("set_alarm", {"hour": 7, "minute": 0, "skip_ui": False}).args.endswith(
        "false"
    )
    with pytest.raises(ValueError):
        build_intent("set_alarm", {"hour": 25, "minute": 0})
    with pytest.raises(ValueError, match="missing"):
        build_intent("set_alarm", {"hour": 7})


def test_timer_dial_sms_share_navigate():
    assert "alarm.LENGTH 90" in build_intent("set_timer", {"seconds": 90}).args
    with pytest.raises(ValueError):
        build_intent("set_timer", {"seconds": 0})
    assert "tel:+20128" in build_intent("dial", {"phone_number": "+20 (128)"}).args
    sms = build_intent("compose_sms", {"phone_number": "0100", "body": "hi 'you'"}).args
    assert "smsto:0100" in sms and "sms_body 'hi '\\''you'\\'''" in sms
    share = build_intent("share_text", {"text": "look", "subject": "s"}).args
    assert "android.intent.action.SEND" in share and "text/plain" in share
    nav = build_intent("navigate", {"destination": "Cairo Tower", "mode": "walk"}).args
    assert "Cairo%20Tower" in nav and "travelmode=walking" in nav
    with pytest.raises(ValueError):
        build_intent("navigate", {"destination": "x", "mode": "teleport"})


def test_calendar_event_defaults_to_one_hour():
    spec = build_intent("add_calendar_event", {"title": "Demo", "start": "2026-07-10T15:00"})
    begin = epoch_ms("2026-07-10T15:00")
    assert f"beginTime {begin}" in spec.args and f"endTime {begin + 3_600_000}" in spec.args
    assert datetime.fromtimestamp(begin / 1000).hour == 15
    assert "-t vnd.android.cursor.dir/event" in spec.args  # needed for resolve-activity


def test_unknown_verb_lists_choices():
    with pytest.raises(ValueError, match="set_alarm"):
        build_intent("teleport", {})
    assert len(VERBS) == 7


# ---- shade parsing --------------------------------------------------------------------


def _shade(state_fixture):
    return parse_screen(state_fixture("shade"))


def test_shade_rows_from_real_tree(state_fixture):
    rows = notification_rows(_shade(state_fixture))
    assert [r.title for r in rows] == ["Sign in required", "Keep Screen Awake"]
    assert rows[0].expanded and not rows[1].expanded
    assert rows[1].expand_button is not None and rows[1].actions == ()


def test_match_row_by_title_and_text(state_fixture):
    rows = notification_rows(_shade(state_fixture))
    assert match_row(rows, "keep screen awake").title == "Keep Screen Awake"
    assert match_row(rows, "Sign in required", "continue using").title == "Sign in required"
    assert match_row(rows, "missing") is None and match_row(rows, "") is None


def test_no_clear_all_when_nothing_clearable(state_fixture):
    assert find_clear_all(_shade(state_fixture)) is None


# ---- files & scroll geometry ----------------------------------------------------------


def test_safe_path_allows_shared_storage_only():
    assert safe_path("/sdcard/Download/a.txt") == "/sdcard/Download/a.txt"
    assert safe_path("/storage/emulated/0/DCIM") == "/storage/emulated/0/DCIM"
    # any ".." is refused outright, even when the result would stay inside an allowed root
    for bad in (
        "/data/data/com.x",
        "/system/bin",
        "/sdcard/../data",
        "/sdcard/Download/../DCIM",
        "/sdcardevil",
    ):
        with pytest.raises(McpToolError, match="not_permitted"):
            safe_path(bad)


def test_scroll_vector_directions():
    region = (0, 100, 720, 1100)
    x1, y1, x2, y2 = scroll_vector("down", region, 0.5)
    assert x1 == x2 and y1 > y2  # finger moves up to scroll content down
    x1, y1, x2, y2 = scroll_vector("up", region, 0.5)
    assert y1 < y2
    x1, y1, x2, y2 = scroll_vector("right", region, 0.5)
    assert y1 == y2 and x1 > x2
    assert scroll_vector("down", region, 5.0) == scroll_vector("down", region, 0.9)  # clamped


# ---- grouped shade (real capture with clearable notifications) ------------------------


def test_grouped_shade_yields_leaf_rows_only(state_fixture):
    rows = notification_rows(parse_screen(state_fixture("shade_clearable")))
    titles = [r.title for r in rows]
    assert "Beta Title" in titles and "Alpha Title" in titles and "Sign in required" in titles
    assert all(r.title for r in rows)  # the group header (no title of its own) is not a row
    beta = next(r for r in rows if r.title == "Beta Title")
    assert beta.text == "beta body" and beta.bounds.height < 100
    assert match_row(rows, "Alpha Title", "alpha body") is not None
