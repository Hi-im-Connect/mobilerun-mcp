"""Turn the Portal ``state_full`` payload into a flat :class:`Screen`."""

from __future__ import annotations

from typing import Any

from ..models import Bounds, Element, PhoneState, Screen

DEFAULT_SIZE = (720, 1280)


def _bounds(raw: dict[str, Any] | None) -> Bounds:
    raw = raw or {}
    return Bounds(
        int(raw.get("left", 0)),
        int(raw.get("top", 0)),
        int(raw.get("right", 0)),
        int(raw.get("bottom", 0)),
    )


def _flatten(node: dict[str, Any], depth: int, parent: int, out: list[Element]) -> None:
    index = len(out)
    out.append(
        Element(
            index=index,
            depth=depth,
            parent=parent,
            class_name=str(node.get("className", "")).rsplit(".", 1)[-1],
            resource_id=str(node.get("resourceId", "")),
            package=str(node.get("packageName", "")),
            text=str(node.get("text") or ""),
            description=str(node.get("contentDescription") or ""),
            hint=str(node.get("hint") or ""),
            state_description=str(node.get("stateDescription") or ""),
            bounds=_bounds(node.get("boundsInScreen")),
            clickable=bool(node.get("isClickable")),
            long_clickable=bool(node.get("isLongClickable")),
            focusable=bool(node.get("isFocusable")),
            focused=bool(node.get("isFocused")),
            editable=bool(node.get("isEditable")),
            password=bool(node.get("isPassword")),
            scrollable=bool(node.get("isScrollable")),
            checkable=bool(node.get("isCheckable")),
            checked=bool(node.get("isChecked")),
            selected=bool(node.get("isSelected")),
            enabled=bool(node.get("isEnabled", True)),
            visible=bool(node.get("isVisibleToUser", True)),
            input_type=int(node.get("inputType") or 0),
        )
    )
    for child in node.get("children") or []:
        _flatten(child, depth + 1, index, out)


def parse_screen(state: dict[str, Any]) -> Screen:
    """Build a :class:`Screen` from the ``state_full`` dict (``a11y_tree`` + ``phone_state``)."""
    phone = state.get("phone_state") or {}
    focused = phone.get("focusedElement") or {}
    ctx = (state.get("device_context") or {}).get("screen_bounds") or {}
    width = int(ctx.get("width") or DEFAULT_SIZE[0])
    height = int(ctx.get("height") or DEFAULT_SIZE[1])
    elements: list[Element] = []
    tree = state.get("a11y_tree")
    if isinstance(tree, dict):
        _flatten(tree, 0, -1, elements)
    return Screen(
        phone=PhoneState(
            package=str(phone.get("packageName", "")),
            app=str(phone.get("currentApp", "")),
            activity=str(phone.get("activityName", "")),
            keyboard_visible=bool(phone.get("keyboardVisible")),
            focused_editable=bool(phone.get("isEditable")) or bool(focused.get("text")),
        ),
        width=width,
        height=height,
        elements=tuple(elements),
    )
