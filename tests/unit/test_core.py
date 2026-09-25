import asyncio
import io

import pytest
from PIL import Image

from mobilerun_mcp import ocr, policy
from mobilerun_mcp.annotate import annotate
from mobilerun_mcp.config import Config
from mobilerun_mcp.errors import McpToolError
from mobilerun_mcp.ledger import Ledger
from mobilerun_mcp.marks import build_marks
from mobilerun_mcp.models import Bounds, Mark
from mobilerun_mcp.observe import build_observation, mutate, settle
from mobilerun_mcp.parsers.a11y import parse_screen
from mobilerun_mcp.session import DeviceSession

# ---- config ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = Config.from_env({})
    assert cfg.policy == "off" and not cfg.enable_adb and cfg.scopes == {"read", "write"}


def test_config_parses_env_and_rejects_bad_values():
    cfg = Config.from_env(
        {
            "MOBILERUN_DEVICE": "1.2.3.4:5555",
            "MOBILERUN_MCP_POLICY": "STRICT",
            "MOBILERUN_MCP_ENABLE_ADB": "1",
            "MOBILERUN_MCP_SCOPES": "read",
        }
    )
    assert cfg.device == "1.2.3.4:5555" and cfg.policy == "strict"
    assert cfg.enable_adb and cfg.scopes == {"read"}
    assert Config.from_env({"MOBILERUN_MCP_POLICY": "bogus"}).policy == "off"
    assert Config.from_env({"MOBILERUN_MCP_SCOPES": "nonsense"}).scopes == {"read", "write"}


# ---- ledger ---------------------------------------------------------------------------


def test_ledger_plan_and_steps():
    ledger = Ledger()
    ledger.set_plan(["open app", "read post"], goal="g")
    ledger.mark_step(0, "done", "ok")
    assert ledger.steps[0].status == "done" and ledger.steps[0].note == "ok"
    with pytest.raises(ValueError):
        ledger.mark_step(0, "weird")
    with pytest.raises(IndexError):
        ledger.mark_step(5, "done")


def test_ledger_refuses_success_until_target_met():
    ledger = Ledger()
    ledger.set_plan(["read"], target_count=2)
    ledger.record_finding("post 1", "hello")
    assert "1 of 2" in ledger.end("success")
    assert ledger.ended is None
    assert ledger.end("partial") is None and ledger.ended == "partial"
    ledger.record_finding("post 2", "world")
    assert ledger.missing_findings() == 0
    assert ledger.end("success") is None


def test_ledger_set_plan_resets_findings():
    ledger = Ledger()
    ledger.set_plan(["a"], target_count=1)
    ledger.record_finding("x", "y")
    ledger.set_plan(["b"])
    assert ledger.findings == [] and ledger.to_dict()["steps"][0]["text"] == "b"


# ---- policy ---------------------------------------------------------------------------


def test_luhn_and_card_detection():
    assert policy.luhn_valid("4111111111111111")
    assert not policy.luhn_valid("4111111111111112")
    assert policy.looks_like_card("4111 1111 1111 1111")
    assert not policy.looks_like_card("1234")


def test_policy_off_allows_everything():
    assert policy.check_app("off", "com.paypal.android", "PayPal").allowed
    assert policy.check_text("off", "4111111111111111", "cvv", True).allowed


def test_policy_standard_blocks_sensitive_apps_but_not_normal_ones():
    assert not policy.check_app("standard", "com.paypal.android.p2pmobile", "PayPal").allowed
    assert policy.check_app("standard", "com.x", "Authenticator").category == "authenticator"
    assert policy.check_app("standard", "com.bitwarden.mobile", "Bitwarden").category == (
        "password_manager"
    )
    for pkg, label in (("com.eyecon.global", "Eyecon"), ("com.facebook.katana", "Facebook")):
        assert policy.check_app("standard", pkg, label).allowed


def test_policy_standard_text_rules():
    assert not policy.check_text("standard", "4111111111111111").allowed
    assert policy.check_text("standard", "123", "Enter CVV").category == "cvv"
    assert policy.check_text("standard", "hunter2", "Password", password_field=True).allowed


def test_policy_strict_blocks_passwords_and_ids():
    assert policy.check_text("strict", "hunter2", password_field=True).category == "password"
    assert policy.check_text("strict", "x", "Enter your PIN").category == "password"
    assert policy.check_text("strict", "123-45-6789").category == "national_id"
    assert policy.check_text("strict", "29801011234567").category == "national_id"
    assert policy.check_text("strict", "hello world").allowed


# ---- settle / observe -----------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def _capture_from(sequence):
    """Capture fn replaying signatures; the screen/marks payload is irrelevant to settle."""
    screen = parse_screen({"phone_state": {"packageName": "p"}})
    it = iter(sequence)
    last = {"sig": sequence[-1]}

    async def capture():
        sig = next(it, last["sig"])
        return screen, [], sig

    return capture


async def test_settle_returns_when_two_captures_agree():
    clock = FakeClock()
    capture = _capture_from(["a", "b", "b"])
    _, _, sig, settled = await settle(capture, sleep=clock.sleep, clock=clock)
    assert sig == "b" and settled


async def test_settle_gives_up_after_timeout_when_screen_keeps_changing():
    clock = FakeClock()
    capture = _capture_from([str(i) for i in range(100)])
    _, _, _, settled = await settle(capture, timeout=1.0, sleep=clock.sleep, clock=clock)
    assert not settled


def test_build_observation_reports_change_and_labels():
    screen = parse_screen(
        {"phone_state": {"packageName": "com.x", "currentApp": "X", "activityName": "a.b.Main"}}
    )
    marks = [Mark(1, "button", "Go", Bounds(0, 0, 10, 10), 0)]
    obs = build_observation("old", screen, marks, "new", True)
    assert obs["screen_changed"] is True and obs["top_labels"] == ["Go"]
    assert obs["activity"] == "Main" and obs["foreground_app"] == "X"
    assert build_observation("", screen, marks, "new", True)["screen_changed"] is False


class FakeSession:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.stale = False
        self.signature = ""
        self.last_observation = None
        self.action_count = 0
        self.last_action_at = 0.0
        self.seen_at = {}
        self._sigs = iter(["before", "after", "after"])
        self.screen = parse_screen({"phone_state": {"packageName": "com.x"}})

    async def peek(self):
        return self.screen, [], next(self._sigs, "after")

    def invalidate_marks(self):
        self.stale = True


async def test_mutate_runs_action_invalidates_marks_and_observes(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr("mobilerun_mcp.observe.asyncio.sleep", instant)
    session, calls = FakeSession(), []

    async def action():
        calls.append("acted")

    result = await mutate(session, action, {"action": "tap"})
    assert calls == ["acted"] and session.stale
    assert result["ok"] and result["action"] == "tap"
    assert result["post_action_observation"]["screen_changed"] is True


# ---- session mark handling ------------------------------------------------------------


def test_get_mark_states():
    session = DeviceSession("x:1", Config())
    with pytest.raises(McpToolError, match="unknown_som_id"):
        session.get_mark(1)
    session.marks = [Mark(1, "button", "A", Bounds(0, 0, 10, 10), 0)]
    session.marks_stale = False
    assert session.get_mark(1).label == "A"
    with pytest.raises(McpToolError, match="unknown_som_id"):
        session.get_mark(9)
    session.invalidate_marks()
    with pytest.raises(McpToolError, match="stale_som_id"):
        session.get_mark(1)


# ---- OCR and annotation ---------------------------------------------------------------

TSV = (
    "\t".join(
        [
            "level",
            "page_num",
            "block_num",
            "par_num",
            "line_num",
            "word_num",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
        ]
    )
    + "\n"
    + "\n".join(
        [
            "5\t1\t1\t1\t1\t1\t100\t200\t50\t20\t96.5\tHello",
            "5\t1\t1\t1\t1\t2\t160\t200\t60\t20\t92.0\tworld",
            "5\t1\t1\t1\t2\t1\t100\t240\t80\t20\t30.0\tnoise",
            "5\t1\t2\t1\t1\t1\t300\t400\t70\t22\t88.0\tSecond",
            "4\t1\t2\t1\t1\t0\t0\t0\t0\t0\t-1\t",
        ]
    )
)


def test_tesseract_tsv_groups_words_and_drops_noise():
    lines = ocr.parse_tesseract_tsv(TSV)
    assert [ln.text for ln in lines] == ["Hello world", "Second"]
    assert lines[0].bounds == Bounds(100, 200, 220, 220)


def test_ocr_merge_only_adds_uncovered_text():
    lines = ocr.parse_tesseract_tsv(TSV)
    covering = Mark(1, "button", "Hello", Bounds(90, 190, 230, 230), 3)
    merged = ocr.merge_ocr_marks([covering], lines)
    assert [m.label for m in merged] == ["Hello", "Second"]
    assert merged[1].source == "ocr" and [m.id for m in merged] == [1, 2]


def test_annotate_produces_same_size_png(state_fixture):
    png = (
        __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "screenshot_home.png"
    ).read_bytes()
    marks = build_marks(parse_screen(state_fixture("home")))
    out = annotate(png, marks)
    src, dst = Image.open(io.BytesIO(png)), Image.open(io.BytesIO(out))
    assert src.size == dst.size and out != png


async def test_settle_does_not_accept_a_screen_without_foreground_app():
    clock = FakeClock()
    empty = parse_screen({"phone_state": {}})
    real = parse_screen({"phone_state": {"packageName": "com.x"}})
    frames = iter(
        [(empty, [], "e"), (empty, [], "e"), (empty, [], "e"), (real, [], "r"), (real, [], "r")]
    )

    async def capture():
        return next(frames)

    screen, _, sig, settled = await settle(capture, sleep=clock.sleep, clock=clock)
    assert settled and sig == "r" and screen.phone.package == "com.x"


async def test_settle_ready_predicate_can_require_a_change():
    clock = FakeClock()
    screen = parse_screen({"phone_state": {"packageName": "com.x"}})
    seq = iter(["old", "old", "old", "new", "new"])

    async def capture():
        return screen, [], next(seq)

    _, _, sig, settled = await settle(
        capture, ready=lambda _s, sig: sig != "old", sleep=clock.sleep, clock=clock
    )
    assert settled and sig == "new"


async def test_mutate_expect_change_waits_for_new_screen(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr("mobilerun_mcp.observe.asyncio.sleep", instant)
    session = FakeSession()
    session._sigs = iter(["before", "before", "before", "after", "after"])

    async def action():
        return None

    result = await mutate(session, action, expect_change=True, settle_timeout=30.0)
    assert result["post_action_observation"]["screen_changed"] is True


async def test_mutate_expect_package_returns_immediately_when_already_there(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr("mobilerun_mcp.observe.asyncio.sleep", instant)
    session = FakeSession()
    session.screen = parse_screen({"phone_state": {"packageName": "com.x"}})
    session._sigs = iter(["s"] * 20)

    async def action():
        return None

    result = await mutate(session, action, expect_package="com.x", settle_timeout=5.0)
    assert result["post_action_observation"]["settled"] is True


async def test_mutate_expect_package_waits_out_a_cold_start(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr("mobilerun_mcp.observe.asyncio.sleep", instant)
    empty = parse_screen({"phone_state": {}})
    old = parse_screen({"phone_state": {"packageName": "launcher"}})
    new = parse_screen({"phone_state": {"packageName": "com.target"}})
    frames = iter([(old, [], "o")] + [(empty, [], "e")] * 6 + [(new, [], "n")] * 3)

    class Session(FakeSession):
        async def peek(self):
            return next(frames)

    session = Session()

    async def action():
        return None

    result = await mutate(session, action, expect_package="com.target", settle_timeout=60.0)
    obs = result["post_action_observation"]
    assert obs["package"] == "com.target" and obs["settled"] and obs["screen_changed"]


# ---- device selection -----------------------------------------------------------------


def test_parse_devices_skips_daemon_noise():
    from mobilerun_mcp.adb import parse_devices

    out = (
        "* daemon not running; starting now *\nList of devices attached\n"
        "emulator-5554\tdevice product:sdk\n10.0.0.5:5555\toffline\n"
    )
    assert parse_devices("List of devices attached\n" + out.split("attached\n", 1)[1]) == [
        {"serial": "emulator-5554", "state": "device"},
        {"serial": "10.0.0.5:5555", "state": "offline"},
    ]
    assert parse_devices("") == []


def test_default_device_prefers_the_environment(monkeypatch):
    from mobilerun_mcp.session import Runtime

    monkeypatch.setattr(
        "mobilerun_mcp.session.detect_single_device", lambda *_: "should-not-be-used"
    )
    assert Runtime(Config(device="1.2.3.4:5555")).default_device() == "1.2.3.4:5555"


def test_default_device_falls_back_to_the_only_attached_device(monkeypatch):
    from mobilerun_mcp.session import Runtime

    monkeypatch.setattr("mobilerun_mcp.session.detect_single_device", lambda *_: "emulator-5554")
    runtime = Runtime(Config())
    assert runtime.default_device() == "emulator-5554"
    assert runtime.session().serial == "emulator-5554"


def test_no_device_is_a_clear_error(monkeypatch):
    from mobilerun_mcp.session import Runtime

    monkeypatch.setattr("mobilerun_mcp.session.detect_single_device", lambda *_: None)
    with pytest.raises(McpToolError, match="MOBILERUN_DEVICE"):
        Runtime(Config()).session()
    assert (
        Runtime(Config()).session("explicit:5555").serial == "explicit:5555"
    )  # explicit still works
