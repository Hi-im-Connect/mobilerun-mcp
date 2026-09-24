import json

from mobilerun_mcp.parsers import intent_filters as filters
from mobilerun_mcp.parsers import media, notifications, packages, system
from mobilerun_mcp.parsers.content import extract_token, parse_content_output, unwrap

# ---- content provider -----------------------------------------------------------------


def test_content_output_envelope_and_double_encoding():
    raw = 'Row: 0 result={"status":"success","result":"{\\"a\\": 1}"}'
    assert parse_content_output(raw) == {"a": 1}


def test_content_output_plain_json_line_and_garbage():
    assert parse_content_output('{"result": [1, 2]}') == [1, 2]
    assert parse_content_output("No result found.") is None


def test_extract_token_shapes():
    assert extract_token({"status": "success", "result": "abc"}) == "abc"
    assert extract_token({"result": {"token": "xyz"}}) == "xyz"
    assert extract_token("plain") == "plain"
    assert extract_token(None) is None and extract_token({}) is None


def test_unwrap_leaves_unwrapped_values_alone():
    assert unwrap({"other": 1}) == {"other": 1}


# ---- notifications --------------------------------------------------------------------


def test_notifications_from_real_dump(fixture_text):
    items = notifications.parse_notifications(fixture_text("dumpsys_notification.txt"))
    assert len(items) == 2
    gms = next(n for n in items if n.package == "com.google.android.gms")
    assert gms.title == "Sign in required"
    assert "Sign in to continue" in gms.text
    portal = next(n for n in items if n.package == "com.mobilerun.portal")
    assert portal.title == "Keep Screen Awake"
    assert portal.actions == ("Stop",)
    assert portal.key == "0|com.mobilerun.portal|2004|null|10083"
    assert portal.ongoing and not portal.clearable
    assert portal.importance == "LOW"


def test_notifications_empty_and_missing_section():
    assert notifications.parse_notifications("") == []
    assert notifications.parse_notifications("Current Notification Manager state:\n") == []


def test_notification_clearable_flag_logic():
    plain = notifications.Notification("k", "p", 1, None, flags=0x10)
    assert plain.clearable and not plain.ongoing
    assert not notifications.Notification("k", "p", 1, None, flags=0x20).clearable


# ---- media ----------------------------------------------------------------------------

SYNTHETIC_PLAYING = """MEDIA SESSION SERVICE (dumpsys media_session)

  MusicSession com.example.player/MusicSession (userId=0)
    ownerPid=1, ownerUid=1, userId=0
    package=com.example.player
    active=true
    state=PlaybackState {state=3, position=1000, speed=1.0}
    metadata: size=3, description=Song Title, Artist Name, Album Name
User Records:
"""


def test_media_sessions_hide_system_by_default(fixture_text):
    dump = fixture_text("dumpsys_media_session.txt")
    assert media.parse_media_sessions(dump) == []
    everything = media.parse_media_sessions(dump, include_system=True)
    assert len(everything) == 1 and everything[0].system and everything[0].state == "none"


def test_media_session_playing_synthetic_format():
    (session,) = media.parse_media_sessions(SYNTHETIC_PLAYING)
    assert session.package == "com.example.player"
    assert session.state == "playing" and session.active
    assert session.description.startswith("Song Title")


def test_volume_and_stream_parsing():
    assert media.parse_volume_get("[V] volume is 5 in range [0..15]") == (5, 0, 15)
    assert media.parse_volume_get("nope") is None
    block = (
        "- STREAM_MUSIC:\n   Muted: true\n   Min: 0\n   Max: 15\n   streamVolume:7\n- STREAM_X:\n"
    )
    assert media.parse_stream_block(block) == {"muted": True, "min": 0, "max": 15, "volume": 7}
    assert media.parse_stream_block("nothing") == {}


# ---- system outputs -------------------------------------------------------------------


def test_device_outputs_from_real_fixtures(fixture_text):
    battery = system.parse_battery(fixture_text("dumpsys_battery.txt"))
    assert battery["scale"] == 100 and battery["present"] is False
    assert system.parse_wm_size(fixture_text("wm_size.txt")) == (720, 1280)
    assert system.parse_wm_density(fixture_text("wm_density.txt")) == 320
    props = system.parse_getprop(fixture_text("getprop_subset.txt"))
    assert props["ro.build.version.sdk"] == "31" and props["ro.product.cpu.abi"] == "x86_64"
    assert system.parse_wakefulness(fixture_text("dumpsys_power_wake.txt")) == "awake"
    assert (
        system.parse_focus(fixture_text("dumpsys_window_focus.txt"))[0] == "com.android.launcher3"
    )
    assert system.parse_df(fixture_text("df_data.txt"))["free_kb"] > 0
    assert system.parse_ip_addresses(fixture_text("ip_addr.txt")) == ["172.17.0.2"]


def test_wm_size_prefers_override_line():
    assert system.parse_wm_size("Physical size: 720x1280\nOverride size: 360x640") == (360, 640)
    assert system.parse_wm_size("garbage") is None


def test_contacts_and_resolve_and_sockets(fixture_text):
    (contact,) = system.parse_contacts(fixture_text("contacts_phones.txt"))
    assert contact.name == "Ali Omar" and contact.number.startswith("+20")
    assert system.parse_contacts("No result found.") == []
    assert system.parse_resolve_activity(fixture_text("resolve_launcher_clock.txt")) == (
        "com.android.deskclock/.DeskClock"
    )
    chooser = system.parse_resolve_activity(fixture_text("resolve_view_https.txt"))
    assert system.is_chooser(chooser) and not system.is_chooser("com.x/.Main")
    assert system.parse_resolve_activity("No activity found") is None
    sockets = system.parse_devtools_sockets(fixture_text("devtools_sockets.txt"))
    assert ("webview_devtools_remote_21512", 21512) in sockets and len(sockets) == 4


def test_find_parses_absolute_paths_only():
    assert system.parse_find("/sdcard\n/sdcard/DCIM\nfind: oops\n") == ["/sdcard", "/sdcard/DCIM"]


# ---- intent filters / deep links ------------------------------------------------------

SYNTHETIC_TABLE = """Activity Resolver Table:
  Schemes:
      https:
        aa11 com.demo/.Main filter f1
          Action: "android.intent.action.VIEW"
          Category: "android.intent.category.DEFAULT"
          Category: "android.intent.category.BROWSABLE"
          Scheme: "https"
          Authority: "demo.example.com": -1
          Path: "PatternMatcher{PREFIX: /watch}"
      demo:
        aa11 com.demo/.Main filter f2
          Action: "android.intent.action.VIEW"
          Category: "android.intent.category.DEFAULT"
          Scheme: "demo"
  Non-Data Actions:
      android.intent.action.MAIN:
        bb22 com.demo/.Main filter f3
          Action: "android.intent.action.MAIN"
Receiver Resolver Table:
"""


def test_synthetic_filters_yield_expected_links():
    links = filters.deeplinks_from_filters(filters.parse_activity_filters(SYNTHETIC_TABLE))
    uris = {link.uri: link for link in links}
    assert uris["https://demo.example.com/watch"].kind == "prefix"
    assert uris["https://demo.example.com/watch"].browsable
    assert uris["demo://"].kind == "scheme" and not uris["demo://"].browsable
    assert len(links) == 2  # the MAIN filter has no scheme


def test_filters_deduplicated_across_sections(fixture_text):
    parsed = filters.parse_activity_filters(fixture_text("pkg_eyecon.txt"))
    keys = [(f.component, f.filter_id) for f in parsed]
    assert len(keys) == len(set(keys))
    links = filters.deeplinks_from_filters(parsed)
    assert any(link.uri == "eyecon://" for link in links)
    assert not any("PatternMatcher" in link.uri for link in links)


# ---- app ranking ----------------------------------------------------------------------


def _apps(fixture_text):
    return packages.parse_apps(json.loads(fixture_text("packages.json")))


def test_exact_label_wins(fixture_text):
    best, ranked = packages.resolve_app("contacts", _apps(fixture_text))
    assert best and best.package == "com.android.contacts" and ranked[0][1] == 1.0


def test_prefix_and_package_matches(fixture_text):
    apps = _apps(fixture_text)
    assert packages.resolve_app("face", apps)[0].label == "Facebook"
    assert packages.resolve_app("com.eyecon.global", apps)[0].label == "Eyecon"


def test_unknown_app_returns_no_candidates(fixture_text):
    best, ranked = packages.resolve_app("chrome", _apps(fixture_text))
    assert best is None and ranked == []


def test_ambiguous_query_returns_candidates_without_choosing():
    apps = [packages.App("a.one", "Photo Editor"), packages.App("b.two", "Photo Viewer")]
    best, ranked = packages.resolve_app("photo", apps)
    assert best is None and len(ranked) == 2
