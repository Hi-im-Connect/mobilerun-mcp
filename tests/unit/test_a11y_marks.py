from mobilerun_mcp.marks import (
    build_marks,
    find_marks,
    format_marks,
    has_loading_indicator,
    signature,
    top_labels,
)
from mobilerun_mcp.models import Bounds
from mobilerun_mcp.parsers.a11y import parse_screen


def test_parse_screen_phone_state_and_size(state_fixture):
    screen = parse_screen(state_fixture("contacts"))
    assert screen.phone.package == "com.android.contacts"
    assert screen.phone.keyboard_visible is False
    assert (screen.width, screen.height) == (720, 1280)
    assert len(screen.elements) > 5
    assert screen.elements[0].depth == 0 and screen.elements[0].parent == -1


def test_parse_screen_keyboard_and_editable_focus(state_fixture):
    screen = parse_screen(state_fixture("contacts_search"))
    assert screen.phone.keyboard_visible is True
    assert screen.phone.focused_editable is True


def test_parse_screen_tolerates_missing_tree():
    screen = parse_screen({"phone_state": {"packageName": "x"}})
    assert screen.elements == () and screen.phone.package == "x"


def test_marks_are_numbered_from_one_in_reading_order(state_fixture):
    marks = build_marks(parse_screen(state_fixture("contacts")))
    assert [m.id for m in marks] == list(range(1, len(marks) + 1))
    tops = [(m.bounds.top, m.bounds.left) for m in marks]
    assert tops == sorted(tops)


def test_contacts_marks_expose_search_and_fab(state_fixture):
    marks = build_marks(parse_screen(state_fixture("contacts")))
    labels = {m.label for m in marks}
    assert "Search contacts" in labels
    assert "Create new contact" in labels
    assert find_marks(marks, "Ali Omar")


def test_full_screen_list_container_is_not_a_mark(state_fixture):
    screen = parse_screen(state_fixture("contacts"))
    marks = build_marks(screen)
    assert all(m.bounds.area < 0.6 * screen.bounds.area for m in marks)
    rows = [m for m in marks if "Ali Omar" in m.label]
    assert len(rows) == 1  # one per visible row, not one more for the list itself


def test_home_icons_become_buttons(state_fixture):
    marks = build_marks(parse_screen(state_fixture("home")))
    by_label = {m.label: m for m in marks}
    assert by_label["Contacts"].kind == "button"
    assert by_label["Eyecon"].kind == "button"


def test_search_field_is_input_kind(state_fixture):
    marks = build_marks(parse_screen(state_fixture("contacts_search")))
    inputs = [m for m in marks if m.kind == "input"]
    assert inputs and inputs[0].label == "ali"


def test_text_inside_clickable_container_is_not_duplicated(state_fixture):
    marks = build_marks(parse_screen(state_fixture("settings")))
    interactive = [m for m in marks if m.kind != "text"]
    for text_mark in (m for m in marks if m.kind == "text"):
        assert not any(
            text_mark.label in owner.label and owner.bounds.contains(text_mark.bounds)
            for owner in interactive
        )
    assert interactive, "settings rows should be targetable"


def test_shade_marks_include_notifications_when_system_ui_is_foreground(state_fixture):
    marks = build_marks(parse_screen(state_fixture("shade")))
    labels = " ".join(m.label for m in marks)
    assert "Sign in required" in labels
    assert "Keep Screen Awake" in labels


def test_system_ui_nodes_hidden_when_another_app_is_foreground(state_fixture):
    state = state_fixture("shade")
    state["phone_state"]["packageName"] = "com.android.contacts"
    labels = " ".join(m.label for m in build_marks(parse_screen(state)))
    assert "Sign in required" not in labels


def test_signature_stable_and_sensitive(state_fixture):
    screen_a = parse_screen(state_fixture("contacts"))
    screen_b = parse_screen(state_fixture("contacts_search"))
    marks_a, marks_b = build_marks(screen_a), build_marks(screen_b)
    assert signature(screen_a, marks_a) == signature(screen_a, marks_a)
    assert signature(screen_a, marks_a) != signature(screen_b, marks_b)


def test_find_marks_prefers_exact_match(state_fixture):
    marks = build_marks(parse_screen(state_fixture("contacts")))
    hits = find_marks(marks, "ali omar")
    assert hits and hits[0].label.endswith("Ali Omar")
    assert find_marks(marks, "omar")[0].id == hits[0].id
    assert find_marks(marks, "") == []


def test_top_labels_limit_and_dedupe(state_fixture):
    marks = build_marks(parse_screen(state_fixture("settings")))
    labels = top_labels(marks, 5)
    assert len(labels) <= 5 and len(labels) == len(set(labels))


def test_format_marks_line_shape(state_fixture):
    marks = build_marks(parse_screen(state_fixture("contacts")))
    first_line = format_marks(marks).splitlines()[0]
    assert first_line.lstrip().startswith("1 [")
    assert "@(" in first_line


def test_no_loading_indicator_on_static_screen(state_fixture):
    assert has_loading_indicator(parse_screen(state_fixture("contacts"))) is False


def test_bounds_geometry():
    outer, inner = Bounds(0, 0, 100, 100), Bounds(10, 10, 20, 20)
    assert outer.contains(inner) and not inner.contains(outer)
    assert outer.intersects(inner) and inner.center == (15, 15)
    assert not Bounds(0, 0, 10, 10).intersects(Bounds(10, 0, 20, 10))
