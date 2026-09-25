# mobilerun-mcp

**An MCP server that controls phones: Android (physical, emulator, redroid; ARM or x86_64) over
adb, iOS through ios-portal, and Mobilerun Cloud devices.** Everything runs on the host, so nothing
ARM-only has to run on the device.

149 tools, covering the full tool surface of [AURA](https://dinesh210805.github.io/aura-app/)'s MCP
server, droidrun's [mobilerun-core](https://pypi.org/project/mobilerun-core/) Device API, the
[mobilerun](https://github.com/droidrun/mobilerun) agent actions and droidrun's official
[mobilerun-mcp](https://github.com/droidrun/mobilerun-mcp) cloud tools, under the same names and
parameters.

```
  MCP client (Claude Code, Cursor, ...)
        |  stdio, or HTTP (--http)
  mobilerun-mcp ---- adb ------------------> Android device (Mobilerun Portal)
     |   |   '------ CDP over adb forward --> on-device browser / WebViews
     |   '---------- mobilerun-core --------> iOS (ios-portal), Portal-HTTP-only Android,
     |                                        Mobilerun Cloud
     '-- host-side OCR (tesseract) and icon detector (OmniParser v2, onnxruntime)
```

**Jump to:** [Quick start](#quick-start) · [Troubleshooting](#troubleshooting) · [Calling the tools](#calling-the-tools) · [Tool reference](#tool-reference) · [Configuration](#configuration)

## Why this exists

Android MCP servers that run *on the phone* are convenient, but they depend on on-device native
libraries built for ARM. On an x86_64 device (redroid, an emulator, an Android-x86 box) those
libraries run under a translation layer that cannot execute some instructions, and the app
crashes. This server keeps all the heavy lifting on the host: it reads the screen through the
[Mobilerun Portal](https://github.com/droidrun/mobilerun-portal) accessibility service, drives
gestures through the Portal and `adb`, and reaches the browser through Chrome DevTools. Nothing
ARM-only runs on the device.

It also works over any network `adb` works over (LAN, VPN, `adb connect`), not only the same Wi-Fi.

## Quick start

### Requirements

| Package | Needed for | Install |
|---|---|---|
| `adb` (platform-tools) | device access | [download](https://developer.android.com/tools/releases/platform-tools), `apt install adb`, `brew install android-platform-tools` |
| `uv` (or Python 3.11+) | environment and dependencies | [install](https://docs.astral.sh/uv/getting-started/installation/) |
| `mobilerun` CLI | installing the Portal; `run_task` | `uv tool install mobilerun` |
| `tesseract` (optional) | OCR on screens with a sparse accessibility tree | [install](https://tesseract-ocr.github.io/tessdoc/Installation.html), `apt install tesseract-ocr`, `brew install tesseract` |

Python dependencies (`fastmcp`, `mobilerun-core[local]`, `onnxruntime`, `numpy`, `pillow`, ...) are
installed with the project. The icon detector for `perceive_screen(detail="full")` (OmniParser v2,
~80 MB, AGPL-3.0) downloads on first use to `~/.cache/mobilerun-mcp`.

You also need an Android device that `adb` can reach: a USB phone
([enable USB debugging](https://developer.android.com/studio/debug/dev-options)), an x86_64
[emulator](https://developer.android.com/studio/run/managing-avds), or
[redroid](https://github.com/remote-android/redroid-doc):

```bash
docker run -itd --privileged -p 5555:5555 redroid/redroid:12.0.0-latest
adb connect localhost:5555
```

### Install

```bash
adb devices                                   # note the serial

# Mobilerun Portal: the accessibility service the server reads the screen through
mobilerun setup -d <serial>
mobilerun ping -d <serial>                    # Portal is installed and accessible

# Server
git clone https://github.com/Hi-im-Connect/mobilerun-mcp.git && cd mobilerun-mcp
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -e .
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.

<details>
<summary>Manual Portal install</summary>

Download the APK from [Portal releases](https://github.com/droidrun/mobilerun-portal/releases), then:

```bash
adb -s <serial> install -r <portal.apk>
# enable it: Settings > Accessibility > Mobilerun Portal, or headless (replaces other enabled services):
adb -s <serial> shell settings put secure enabled_accessibility_services com.mobilerun.portal/com.mobilerun.portal.service.MobilerunAccessibilityService
adb -s <serial> shell settings put secure accessibility_enabled 1
```

</details>

### Register with your MCP client

**Claude Code**

```bash
claude mcp add --scope user mobilerun -e MOBILERUN_DEVICE=<serial> -- "$PWD/.venv/bin/python" -m mobilerun_mcp
```

**Claude Desktop, Cursor, others:** add to the client's MCP config and restart it.

```json
{
  "mcpServers": {
    "mobilerun": {
      "command": "/path/to/mobilerun-mcp/.venv/bin/python",
      "args": ["-m", "mobilerun_mcp"],
      "env": { "MOBILERUN_DEVICE": "<serial>" }
    }
  }
}
```

| Client | Config file |
|---|---|
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` |

Client docs: [Claude Code](https://docs.claude.com/en/docs/claude-code/mcp),
[Claude Desktop](https://modelcontextprotocol.io/quickstart/user),
[Cursor](https://docs.cursor.com/context/model-context-protocol).

`MOBILERUN_DEVICE` is optional when exactly one device is attached to `adb`. Every device tool also
takes a `device` argument, so one server can drive several devices:

| `device` / `MOBILERUN_DEVICE` | Target |
|---|---|
| `emulator-5554`, `192.168.1.20:5555`, a USB serial | Android over adb (all tools) |
| `http://host:8080` or `android-http:<url>` | Android through the Portal HTTP API only (`MOBILERUN_ANDROID_PORTAL_TOKEN`) |
| `ios` or `ios:<url>` | iOS via [ios-portal](https://github.com/droidrun/ios-portal) (default `http://127.0.0.1:6643`) |
| `cloud:<id>` or a device UUID | Mobilerun Cloud device (`MOBILERUN_CLOUD_API_KEY`) |

adb-only tools (dumpsys-based ones, shortcuts, files by path) return `[unsupported]` on the others.

HTTP instead of stdio (AURA's bridge address): `.venv/bin/python -m mobilerun_mcp --http` serves
`http://127.0.0.1:4816/mcp`.

### Usage

Ask the agent in natural language ("open Settings and read the Android version"), or call tools
directly. See [Calling the tools](#calling-the-tools) and the [Tool reference](#tool-reference).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[device_unreachable] no device selected` | `adb devices`, then set `MOBILERUN_DEVICE=<serial>` |
| `adb devices` shows `unauthorized` | Accept the USB debugging prompt on the device; if it does not appear, `adb kill-server` and reconnect |
| `adb devices` empty or `offline` | Replug USB; for a container or remote device run `adb connect <host>:<port>` |
| `adb: command not found` | Install platform-tools, or set `MOBILERUN_ADB_BIN` |
| `Mobilerun Portal is not enabled as an accessibility service` | Enable it under Settings > Accessibility, or rerun `mobilerun setup` |
| `mobilerun setup` stalls after "Found Portal APK" | Play Protect is scanning the install (redroid, emulators with Google Play): `adb -s <serial> shell settings put global verifier_verify_adb_installs 0` and `... package_verifier_enable 0`, then rerun |
| Client lists no `mobilerun` tools | Restart the client and check the config path. Run `.venv/bin/python -m mobilerun_mcp` by hand: a FastMCP banner followed by waiting is healthy, a traceback is the cause |
| `[stale_som_id]` | An action changed the screen; call `perceive_screen` again |
| Slow first launch | Cold starts take 20 to 30 s on slow devices; `launch_app` waits for the app |
| `web_search` error | DuckDuckGo throttled the request; retry or set `BRAVE_API_KEY` |
| Sparse text on image-heavy screens | Install `tesseract` for OCR, or tap by coordinates from the screenshot |

Issues: [github.com/Hi-im-Connect/mobilerun-mcp/issues](https://github.com/Hi-im-Connect/mobilerun-mcp/issues) (include the error and `adb devices` output).

## How it works

The agent works in a **perceive, act, verify** loop:

1. `perceive_screen` returns a numbered list of everything tappable or readable (`som_id`s) and an
   annotated screenshot with the same numbers drawn on it.
2. An action tool (`tap`, `type_text`, `launch_app`, ...) waits for the screen to settle, then
   returns a `post_action_observation`: foreground app, element count, keyboard state, the top
   labels on screen and whether the screen changed. That block is the verification step.
3. `som_id`s describe one captured screen. After any action they are stale and the server refuses
   them (`stale_som_id`), so the agent can never tap something that has moved.

Details that matter in practice:

- **Elements without text.** Icon-only buttons are numbered too, as long as the app exposes them
  to Android's accessibility service, which is the case for standard apps.
- **Cold starts.** Launching an app waits for that app to reach the foreground. On a slow device a
  cold start can take 20 seconds; an app that is already on top returns immediately.
- **Gestures** go through the Portal's accessibility gestures (fast, and accepted by system UI
  such as the notification shade), with `adb input` as the fallback.
- **Errors** look like `[code] message (hint: ...)`: `device_unreachable`, `policy_blocked`,
  `stale_som_id`, `unknown_som_id`, `element_not_found`, `app_not_found`, `timeout`,
  `unsupported`, `invalid_argument`, `not_permitted`, `plan_incomplete`.

## Calling the tools

Every capability is an MCP tool: a name and a JSON `arguments` object. The client makes the call
when you describe what you want, or you can invoke a tool by name. On the wire:

```json
{"method": "tools/call", "params": {"name": "tap", "arguments": {"som_id": 2}}}
```

For every tool:

- **Optional arguments** can be left out; `null` also means "not given".
- **`device`** is an optional argument on every tool that touches a device (an adb serial). Leave it
  out to use `MOBILERUN_DEVICE`, or the only attached device.
- **State-changing tools** (`tap`, `swipe`, `type_text`, `launch_app`, ...) wait for the screen to
  settle and return `{"ok": true, "action": ..., "post_action_observation": {...}}`.
- **Read tools** return a JSON object; `perceive_screen` and the screenshot tools also return an image.
- **Errors** come back as `[code] message (hint: ...)`, for example `[stale_som_id] ...`.

The `post_action_observation` block tells the agent what the screen looks like after the action:

| Field | Meaning |
|---|---|
| `foreground_app`, `package`, `activity` | What is in front now |
| `element_count` | How many numbered elements are on screen |
| `keyboard_visible` | Whether the on-screen keyboard is up |
| `top_labels` | The first few labels on screen, in reading order |
| `screen_changed` | Whether the screen differs from before the action |
| `loading_indicator_present` | A spinner or progress bar is visible |
| `settled`, `settle_ms` | The screen stopped changing before the wait ended; how long that took |
| `screen_changed_confidence` | `low` when the screen never settled |
| `seen_before` | This screen was already seen N actions ago (going in circles?) |
| `sensitive_foreground` | A banking / payment / authenticator app is in front |
| `hint` | What to do next |

### A typical session

Search Contacts for "ali". Calls are written `tool arguments`; outputs are real.

**1. Look at the screen.**

```
perceive_screen {}
```

The reply is a JSON object with `foreground_app`, `package`, `activity`, `keyboard_visible`,
`screen_size`, `perception_tier`, `e` (AURA's `[x, y, name, flags]` per `som_id`),
`mark_count`, `ocr_used` and `elements`. Here `e` is
`[[56, 104, "Open navigation drawer"], [664, 104, "Search contacts"], [224, 104, "Contacts"], [360, 230, "A / Ali Omar", "l"], [632, 1096, "Create new contact"]]`
and `elements` contains:

```
  1 [button] "Open navigation drawer" @(56,104)
  2 [button] "Search contacts" @(664,104)
  3 [text] "Contacts" @(224,104)
  4 [button] "A / Ali Omar" @(360,230)
  5 [button] "Create new contact" @(632,1096)
```

The leading number is the `som_id`; `@(x,y)` is the tap point. An annotated screenshot with the same
numbers is returned alongside the JSON.

**2. Tap the search icon by its number.**

```
tap {"som_id": 2}
```

The reply has `"ok": true` and `keyboard_visible: true` in `post_action_observation`. Any action
invalidates the numbers: reusing `som_id` 2 now fails with `stale_som_id` until `perceive_screen` is
called again.

**3. Type.**

```
type_text {"text": "ali"}
```

```json
{
  "ok": true,
  "action": "type_text",
  "chars": 3,
  "post_action_observation": {
    "foreground_app": "Contacts",
    "package": "com.android.contacts",
    "activity": "PeopleActivity",
    "element_count": 5,
    "keyboard_visible": true,
    "top_labels": [
      "stop searching",
      "ali",
      "Clear search",
      "Ali Omar"
    ],
    "screen_changed": true,
    "loading_indicator_present": false,
    "settled": true,
    "settle_ms": 922,
    "sensitive_foreground": false,
    "hint": "Settled after the action. Judge the result from this observation ..."
  }
}
```

**4. Verify.**

```
verify_action {"expected": "Ali Omar"}
```

```json
{
  "expected": "Ali Omar",
  "state": {"foreground_app": "Contacts", "element_count": 4, "keyboard_visible": true,
            "top_labels": ["stop searching", "ali", "Clear search", "Ali Omar"], "...": "..."},
  "verified": true,
  "evidence": "text=\"Ali Omar\"",
  "foreground": "com.android.contacts",
  "visible_text": ""
}
```

## Tool reference

149 tools, plus `adb` / `aura-adb` when `MOBILERUN_MCP_ENABLE_ADB=1`. Every tool that acts on a device takes an optional `device` (adb serial, `ios`, `cloud:<id>`, or a Portal URL). `[name=default]` is optional. Generated by `scripts/gen_reference.py`.

### Perception

See the screen. `read_screen` (text grid) or `perceive_screen` (annotated image) first; act by `som_id`.

| Tool | What it does | Arguments |
|---|---|---|
| `perceive_screen` | LOOK at the screen: an annotated screenshot plus every element and its tap point. | [description] [detail] [include_image=true] [ocr=auto] [max_marks=150] [lang=eng] |
| `read_screen` | Read the screen now (waits for it to stop moving first): the screen drawn as a character grid, each element a box with its som_id and label, then a table of what can be acted on: som (tap by this), in (som_id of the smal | none |
| `get_ui_tree` | Compact accessibility tree (class, id, label, flags C/L/E/S/K/P, bounds). | [max_depth=8] |
| `get_screenshot` | Plain screenshot as an image. | none |
| `screenshot` | Plain screenshot. | [hide_overlay=false] |
| `screenshot_path` | Take a screenshot, save it as a PNG file and return the path. | none |

```
perceive_screen {}
perceive_screen {"description": "search bar", "detail": "full"}
read_screen {}
get_ui_tree {}
get_screenshot {}
screenshot {}
screenshot_path {}
```

### Gestures, typing and keys

Every action settles the screen and returns `post_action_observation`. Target with `x`/`y`, a `som_id`, or (mobilerun style) an `index` from `get_state`.

| Tool | What it does | Arguments |
|---|---|---|
| `tap` | Tap at (x, y) or at the center of a numbered mark (som_id from perceive_screen / read_screen). | [x] [y] [som_id] [stealth=false] |
| `double_tap` | Double-tap at (x, y) or a mark. | [x] [y] [som_id] |
| `long_press` | Press and hold at (x, y), a mark (som_id) or a get_state element (index). | [x] [y] [som_id] [index] [duration_ms] [ms] |
| `long_press_at` | Long press at (x, y) (mobilerun agent action). | x y |
| `swipe` | Swipe from (x1, y1) to (x2, y2) over duration_ms / ms (default 300). | [x1] [y1] [x2] [y2] [duration_ms] [ms] [coordinate] [coordinate2] [duration] |
| `scroll_down` | Scroll the content down (reveal what is below): a centered swipe over half the screen (amount), or inside a scrollable mark (som_id). | [amount=0.5] [som_id] |
| `scroll_up` | Scroll the content up (reveal what is above). | [amount=0.5] [som_id] |
| `scroll_left` | Scroll the content left (reveal what is to the left). | [amount=0.5] [som_id] |
| `scroll_right` | Scroll the content right (reveal what is to the right). | [amount=0.5] [som_id] |
| `scroll` | Scroll the content in direction (up / down / left / right) by distance (fraction of the screen). | direction [distance=0.5] [ms=300] [verify=false] |
| `scroll_to` | Two modes. | [x1] [y1] [x2] [y2] [duration_ms=300] [text] [direction=down] [max_scrolls=8] |
| `type_text` | Type into the focused field (tap it first, or pass som_id). | text [clear=false] [submit=false] [som_id] |
| `type` | Type text (mobilerun). | text [index] [clear=false] [wpm] [stealth=false] |
| `press_home` | Press the Home button. | none |
| `press_back` | Press Back (also closes the keyboard without leaving the screen). | none |
| `press_enter` | Press Enter (submits search bars and forms). | none |
| `open_recent_apps` | Open the recent-apps overview. | none |
| `key` | Press a key by mobilerun-core name (back, home, menu, enter, delete, escape, tab, space, search, page_up, page_down, volume_up, volume_down, wakeup, media_play_pause, ...) or by Android keycode number. | name_or_code |

```
tap {"som_id": 4}
tap {"x": 540, "y": 1200}
double_tap {}
long_press {"som_id": 4}
long_press {"index": 7, "ms": 800}
long_press_at {"x": 1, "y": 1}
swipe {"x1": 360, "y1": 1000, "x2": 360, "y2": 300}
swipe {"coordinate": [360, 1000], "coordinate2": [360, 300], "duration": 0.5}
scroll_down {}
scroll_up {}
scroll_left {}
scroll_right {}
scroll {"direction": "down"}
scroll_to {"text": "Battery"}
scroll_to {"x1": 360, "y1": 900, "x2": 360, "y2": 400}
type_text {"text": "hello", "som_id": 3, "submit": true}
type {"text": "hello", "index": 5, "clear": true}
press_home {}
press_back {}
press_enter {}
open_recent_apps {}
key {"name_or_code": "back"}
```

### Apps and deep links

| Tool | What it does | Arguments |
|---|---|---|
| `launch_app` | Open an app by name (fuzzy) or exact package_name. | [app_name] [package_name] [force=false] [package] |
| `start_app` | Start an app by id (Android package / iOS bundle id), optionally a specific activity. | [app_id] [activity] [package] |
| `lookup_app` | Search installed apps by name or package; returns ranked candidates with scores. | [app_name] [query] [limit=5] |
| `list_apps` | List installed apps (user apps only unless include_system_apps=true). | [include_system_apps=false] [include_protected_apps=false] [system=false] |
| `list_app_deeplinks` | Deep links into an app, best first. | [package_name] [app_name] [package] |
| `resolve_deeplink` | Which app would open this URI (or intent action such as android.settings.WIFI_SETTINGS)? | uri |
| `open_deeplink` | Jump straight to a screen via a URI, an app-shortcut://pkg/id from list_app_deeplinks, or an intent action. | uri [package_name] [app_name] [package] |

```
launch_app {"app_name": "Clock"}
launch_app {"package_name": "com.android.settings", "force": true}
start_app {}
lookup_app {}
list_apps {}
list_app_deeplinks {}
resolve_deeplink {"uri": "https://example.com"}
open_deeplink {"uri": "android.settings.WIFI_SETTINGS"}
open_deeplink {"uri": "app-shortcut://com.android.settings/manifest-shortcut-wifi"}
```

### System intents and contacts

| Tool | What it does | Arguments |
|---|---|---|
| `system_intent` | One-call Android actions (action = the verb; verb= is accepted too). | [action] [verb] [hour] [minute] [seconds] [label] [phone_number] [body] [title] [start] [end] [location] [notes] [text] [subject] [destination] [mode=drive] [skip_ui=true] |
| `resolve_contact` | Find contacts by (partial) name and return their phone numbers. | name [limit=5] |

```
system_intent {"action": "set_alarm", "hour": 7, "minute": 30, "label": "wake"}
system_intent {"action": "navigate", "destination": "Cairo Tower", "mode": "walk"}
resolve_contact {"name": "Ali"}
```

### Notifications

| Tool | What it does | Arguments |
|---|---|---|
| `read_notifications` | Current status-bar notifications, newest first, without touching the screen: key, app, title, text, action labels. | [package_name] [include_ongoing=false] [limit=20] [package] |
| `dismiss_notification` | Dismiss one notification (by key, or package/title) or every clearable one. | [key] [package] [title] [clear_all=false] |
| `notification_action` | Tap one of a notification's own buttons (reply, archive, stop...); reply_text fills an inline reply field and sends it. | action [key] [package] [title] [reply_text] |

```
read_notifications {}
dismiss_notification {}
notification_action {"action": "list"}
```

### Media and volume

| Tool | What it does | Arguments |
|---|---|---|
| `get_media_sessions` | Active media sessions (app, playback state, title/artist) and the music volume. | [include_system=false] |
| `media_control` | Control playback in any app without touching the screen: play, pause, play_pause, next, previous, stop, rewind, fast_forward. | [command] [package_name] [action] |
| `volume_up` | Raise the music volume by ``steps``. | [steps=1] |
| `volume_down` | Lower the music volume by ``steps``. | [steps=1] |
| `mute` | Toggle mute on the media stream (muted=true/false forces a state). | [muted] |

```
get_media_sessions {}
media_control {}
volume_up {}
volume_down {}
mute {}
```

### Files

| Tool | What it does | Arguments |
|---|---|---|
| `find_files` | Search the device's media index by name, newest first: images, videos, audio and documents (downloads included). | [query] [kind=any] [limit=10] [path] [max_depth=6] |
| `open_file` | Open a file in its default viewer. | [uri] [path] |

```
find_files {}
open_file {}
```

### Waiting and checking

| Tool | What it does | Arguments |
|---|---|---|
| `wait_for` | LONG waits only (downloads, uploads, processing, status changes); gestures already settle. | [condition] [timeout_ms] [poll_interval_ms] [text] [package] [activity] [gone=false] [timeout] [interval] |
| `watch_device_events` | Collect device events for up to timeout_seconds (default 10, max 30), returning early once max_events (default 50) arrive: foreground app, keyboard, screen content, notifications posted/removed. | [timeout_seconds] [max_events=50] [duration] [interval=0.5] [kinds] |
| `validate_action` | Pre-check a planned action against the safety policy (and, for our action set, that its target exists) without doing it. | [gesture_type] [target] [action] [x] [y] [som_id] [text] [package] [app_name] [uri] |
| `verify_action` | Check an outcome against the live screen. | expected [kind=text] [timeout=3.0] [use_ocr=false] |

```
wait_for {}
watch_device_events {}
validate_action {}
verify_action {"expected": "Settings is open"}
```

### Plan, findings and research

| Tool | What it does | Arguments |
|---|---|---|
| `web_search` | Search the web for how to do something in an app ('how to <task> in <app> android'). | query [max_results] [topic=general] [limit=5] |
| `set_plan` | Start a plan checklist. | steps [goal] [deliverable] [target_count=0] [search_query] |
| `mark_step` | Update a plan step: pending / in_progress / done / skipped / failed. | index status [note] |
| `record_finding` | Record one item you found. | item quote |
| `end_session` | Mark the end of the task (the server keeps listening; the next call starts fresh). | [reason=agent-end] [outcome=success] [goal_type] [summary] |
| `get_usage_guide` | How to use this server well. | [topic] |

```
web_search {"query": "wifi"}
set_plan {"steps": []}
mark_step {"index": 1, "status": "value"}
record_finding {"item": "Result 1", "quote": "exact text"}
end_session {}
get_usage_guide {}
```

### Browser

Pages come back with numbered elements (`el_id`) and a `generation`; pass both to `browser_act`. Sessions: `scratch` (default) or `mine` (the user's signed-in browser).

| Tool | What it does | Arguments |
|---|---|---|
| `browser_open` | Open a web page; returns its text plus numbered elements (el_id) and a generation. | [url] [background=false] [session=scratch] [max_text_chars=4000] [max_elements=60] [target_id] [app] [wait=true] [timeout=15.0] |
| `browser_tabs` | Several pages at once. | [action=list] [url] [index] [session=scratch] |
| `browser_close` | Close a browser session and free it (the page is blanked); close_app also stops the app. | [session=scratch] [close_app=false] |
| `browser_screenshot` | A picture of the open page, for what text cannot tell (charts, maps, images, popups). | [session=scratch] [full_page=false] [max_text_chars=4000] [max_elements=60] |
| `browser_read` | Re-read the open page without navigating: text, numbered elements (el_id) and the generation. | [session=scratch] [max_text_chars=4000] [max_elements=60] [selector] [max_chars] [structure=true] |
| `browser_find` | Find something on the page by its text; returns matches with el_id plus the page. | [text] [session=scratch] [max_text_chars=4000] [max_elements=60] [query] [limit=10] |
| `browser_wait` | Wait for text to appear on the page (or, without text, for it to settle), then return the page. | [text] [timeout_ms] [session=scratch] [max_text_chars=4000] [max_elements=60] [selector] [url_contains] [timeout] |
| `browser_extract` | Pull repeated items off the page (search results, product cards, listings) as rows of text + link in one call, with the total found. | [session=scratch] [max_text_chars=4000] [max_elements=60] [kind=items] [selector] [limit] |
| `browser_act` | Act on the page and get the page back. | action [el_id] [value] [generation] [session=scratch] [max_text_chars=4000] [max_elements=60] [ref] [selector] [text] [key] [clear=false] [submit=false] [amount=600] |
| `browser_handoff` | Let the person do a step you cannot (sign in, one-time code, CAPTCHA, payment confirmation): brings the page to the front and posts prompt as a device notification. | [prompt] [check=false] [session=scratch] [max_text_chars=4000] [max_elements=60] [message] |
| `browser_upload` | Attach a file to an upload control (el_id). | [el_id] [file] [generation] [session=scratch] [max_text_chars=4000] [max_elements=60] [path] [ref] [selector] |

```
browser_open {"url": "https://example.com"}
browser_tabs {}
browser_close {}
browser_screenshot {}
browser_read {}
browser_find {}
browser_wait {}
browser_extract {}
browser_act {"action": "click", "el_id": 3, "generation": 1}
browser_act {"action": "type", "el_id": 5, "value": "shoes"}
browser_handoff {}
browser_upload {}
```

### mobilerun-core Device API

Same names and parameters as `mobilerun_core.Device`. Works on Android (adb or Portal HTTP), iOS and Mobilerun Cloud devices.

| Tool | What it does | Arguments |
|---|---|---|
| `ui` | Raw UI snapshot (a11y_tree, phone_state, device_context, ...), as Device.ui(). | [filter=true] |
| `ui_json` | The UI snapshot serialized as JSON text. | [filter=true] [indent] |
| `ui_with_recovery` | UI snapshot that retries past a dead or empty accessibility tree. | [filter=true] |
| `capabilities` | Backend, platform and the actions this device supports. | none |
| `supports` | Whether this device supports a Device action (e.g. | action |
| `screen_size` | [width, height] in pixels. | none |
| `current_app_id` | Package / bundle id of the foreground app. | none |
| `time` | The device clock. | none |
| `find_nodes` | Nodes matching every given filter (exact text/desc/resource_id/class_name, or *_contains substrings), including off-screen ones. | [text] [desc] [resource_id] [class_name] [text_contains] [desc_contains] [any_contains] [tree] |
| `find_nodes_on_screen` | Like find_nodes, limited to nodes inside the visible screen. | [text] [desc] [resource_id] [class_name] [text_contains] [desc_contains] [any_contains] [tree] |
| `tap_text` | Tap the first on-screen node whose text/description contains text. | text |
| `tap_node` | Tap the center of a node returned by find_nodes / find_nodes_on_screen. | node [stealth=true] |
| `tap_and_wait` | Tap a text (or node) and wait until the UI has been idle for idle seconds. | target [idle=2.0] |
| `scroll_until` | Scroll until a matching node is on screen; result is the node (or null). | [text] [text_contains] [any_contains] [resource_id] [direction=down] [max_swipes=10] [distance=0.35] [settle=0.5] |
| `clear_input` | Clear the focused text field. | none |
| `assert_on` | Fail unless app_id is in the foreground. | app_id |
| `assert_text_visible` | Fail unless text becomes visible on screen within timeout seconds. | text [timeout=5.0] |
| `wait_for_app` | Wait until app_id is in the foreground. | app_id [timeout=10.0] [poll=0.5] |
| `wait_for_idle` | Wait until the UI stops changing. | [timeout=5.0] [poll=0.5] |
| `wait_for_screen_change` | Wait until the UI differs from now. | [timeout=10.0] [poll=0.5] |
| `wait_for_text` | Wait until a node containing text exists (off-screen nodes count). | text [timeout=10.0] [poll=0.5] |
| `wait_for_nodes` | Poll find_nodes until something matches (or timeout, returning []). | [timeout=10.0] [poll=0.5] [text] [desc] [resource_id] [class_name] [text_contains] [desc_contains] [any_contains] [on_screen=false] |
| `open_and_settle` | Start an app and wait until it is in front and idle. | app_id [timeout=15.0] [idle=3.0] |
| `stop_app` | Force-stop an app; clear_data also wipes its data. | app_id [clear_data=false] |
| `install_app` | Install an APK (host path) on the device. | path [replace=false] [grant_permissions=true] |
| `uninstall_app` | Uninstall an app. | app_id |
| `grant_permission` | Grant a runtime permission (android.permission.*) to an app. | package permission |
| `open_deep_link` | Dispatch a deep link / intent (default action VIEW), optionally pinned to a package. | deep_link [package_name] [action] |
| `execute_script` | Run JavaScript in the foreground browser page and return its JSON result. | js |
| `get_clipboard` | The clipboard's text (Android needs the Mobilerun Keyboard as the active IME). | none |
| `set_clipboard` | Put text on the clipboard. | value |

```
ui {}
ui_json {}
ui_with_recovery {}
capabilities {}
supports {"action": "list"}
screen_size {}
current_app_id {}
time {}
find_nodes {"text_contains": "Wi"}
find_nodes_on_screen {}
tap_text {"text": "Settings"}
tap_node {"node": {}}
tap_and_wait {"target": "Settings"}
scroll_until {}
clear_input {}
assert_on {"app_id": "com.android.settings"}
assert_text_visible {"text": "Settings"}
wait_for_app {"app_id": "com.android.settings"}
wait_for_idle {}
wait_for_screen_change {}
wait_for_text {"text": "Settings"}
wait_for_nodes {}
open_and_settle {"app_id": "com.android.settings"}
stop_app {"app_id": "com.android.settings"}
install_app {"path": "/sdcard/Download/a.apk"}
uninstall_app {"app_id": "com.android.settings"}
grant_permission {"package": "com.android.settings", "permission": "android.permission.CAMERA"}
open_deep_link {"deep_link": "https://example.com"}
execute_script {"js": "document.title"}
get_clipboard {}
set_clipboard {"value": "copied text"}
```

### mobilerun agent actions

The mobilerun agent's action set. Indices come from `get_state`.

| Tool | What it does | Arguments |
|---|---|---|
| `get_state` | The screen as the mobilerun agent sees it: phone state plus numbered UI elements ('index. | none |
| `click` | Click the get_state element with this index (its center, avoiding views drawn on top). | index |
| `click_at` | Click at screen position (x, y). | x y |
| `click_area` | Click the center of the area (x1, y1, x2, y2). | x1 y1 x2 y2 |
| `system_button` | Press a system button: back, home or enter. | button |
| `wait` | Wait for duration seconds (max 60). | [duration=1.0] |
| `open_app` | Open an app by name or package (mobilerun agent action). | text |
| `complete` | Finish the task (mobilerun agent action): success flag plus the result or the reason for failure. | success message |
| `type_secret` | Type a secret from the mobilerun credentials file (MOBILERUN_CREDENTIALS, else config/credentials.yaml or ~/.config/mobilerun/credentials.yaml) into the get_state element index (-1 = the focused field). | secret_id index |

```
get_state {}
click {"index": 1}
click_at {"x": 1, "y": 1}
click_area {"x1": 1, "y1": 1, "x2": 1, "y2": 1}
system_button {"button": "back"}
wait {}
open_app {"text": "Settings"}
complete {"success": true, "message": "hi"}
type_secret {"secret_id": "MY_PASSWORD", "index": 1}
```

### Agent tasks and macros

`run_task` runs the Mobilerun agent locally (mobilerun CLI) or on a Mobilerun Cloud device.

| Tool | What it does | Arguments |
|---|---|---|
| `run_task` | Hand a natural-language goal to the Mobilerun agent. | task [deviceId] [llmModel] [maxSteps] [vision] [reasoning=false] [stealth] [outputSchema] [apps] [credentials] [files] [wait=true] [steps] |
| `get_task` | A task's summary, status or trajectory (view). | taskId [view=summary] [offset] [limit] |
| `list_tasks` | Tasks: local agent runs of this server, plus Mobilerun Cloud tasks when MOBILERUN_CLOUD_API_KEY is set (scope: local / cloud / all). | [deviceId] [status] [query] [orderBy] [orderByDirection] [page] [pageSize] [scope=all] |
| `stop_task` | Stop a running task. | taskId |
| `get_task_media` | A screenshot (or ui_state) the task recorded; index picks the step (default latest). | taskId [kind=screenshot] [index] |
| `send_task_message` | Send a message to a running cloud task (e.g. | taskId message |
| `macro_list` | Recorded trajectories (mobilerun macro list); defaults to this server's task folder. | [directory] |
| `macro_replay` | Replay a recorded macro (macro.json or a trajectory folder) on the device (mobilerun macro replay). | path [delay] [start_from] [max_steps] [dry_run=false] [on_mismatch=stop] |

```
run_task {"task": "Open Clock and tell me the first alarm", "maxSteps": 20}
get_task {"taskId": "local-1"}
list_tasks {}
stop_task {"taskId": "local-1"}
get_task_media {"taskId": "local-1"}
send_task_message {"taskId": "local-1", "message": "hi"}
macro_list {}
macro_replay {"path": "/sdcard/Download/a.apk"}
```

### Mobilerun Cloud platform

Same tools as droidrun's official mobilerun-mcp. Needs `MOBILERUN_CLOUD_API_KEY`; device tools also work on local devices where an equivalent exists.

| Tool | What it does | Arguments |
|---|---|---|
| `get_device` | Fetch one device by id (a Mobilerun Cloud id, or a local device: adb serial, ios...). | deviceId |
| `get_device_screenshot` | Capture a device screenshot and return the raw result ({deviceId, screenshot}); for cloud devices the SDK result (base64 payload or signed URL), for local ones base64 PNG. | deviceId |
| `get_device_ui_state` | Read the on-screen UI as structured text: current app/activity, keyboard state, and a compact list of labeled/actionable elements (text, resourceId, className, tap center `xy`, flags). | deviceId [contains] [resourceId] [includeAll] [offset] |
| `list_apps_on_device` | List apps installed on a device (package_name, label, version_name, version_code, is_system_app). | deviceId [includeSystemApps] [includeProtectedApps] |
| `create_device` | Provision a new Mobilerun Cloud device (billing is enforced by the API). | [deviceType] [name] [country] |
| `terminate_device` | Terminate a Mobilerun Cloud device. | deviceId |
| `manage_device` | Device lifecycle operations. | operation [deviceId] [name] |
| `device_action` | Low-level device input. | operation deviceId [displayId] [x] [y] [startX] [startY] [endX] [endY] [duration] [stealth] [text] [clear] [errorRate] [wpm] [key] [action] |
| `manage_device_apps` | Mutate apps on a device. | operation deviceId [packageName] [bundleId] [activity] [includeSystemPackages] [includeProtectedPackages] |
| `manage_device_files` | Device filesystem access. | operation deviceId path [contentBase64] [fileName] [contentType] |
| `configure_device` | Read/write device settings. | operation deviceId [locale] [restart] [timezone] [latitude] [longitude] [visible] [proxyName] [smartIp] [socks5Host] [socks5Port] [socks5User] [socks5Password] |
| `manage_esim` | eSIM subscription management (Mobilerun Cloud devices). | operation deviceId [enable] [smDpAddr] [confirmationCode] [matchingId] [subId] |
| `list_credentials` | List saved credentials (metadata only; values never leave credentials storage). | [packageName] |
| `list_credential_packages` | List app packages that have any credential configured. | none |
| `manage_credentials` | Write path for the credentials vault. | operation [packageName] [credentialName] [fieldType] [value] [fields] |
| `webhooks` | Manage outbound webhooks and inspect deliveries. | operation [endpointId] [deliveryId] [url] [eventTypes] [description] [state] [status] [page] [pageSize] [since] |
| `proxies` | Manage device-bound proxy configs (socks5 or wireguard). | operation [proxyId] [protocol] [name] [host] [port] [user] [password] [config] [lookupUser] [lookupPassword] |
| `connect` | droidrun-connect residential SOCKS5 proxies and their users (distinct from the device-bound `proxies` tool). | operation [proxyId] [userId] [country] [type] [page] [pageSize] [status] [protocol] [provider] [dstHost] [dstPort] [sessionId] [startedAfter] [startedBefore] [endedAfter] [endedBefore] [order] [orderBy] |
| `apps` | Manage uploaded apps (APKs) in Mobilerun Cloud. | operation [id] [query] [platform] [status] [sortBy] [order] [page] [pageSize] [bundleId] [displayName] [versionCode] [versionName] [sizeBytes] [files] [uploadPlatform] [country] [description] [developerName] [iconURL] [targetSdk] |
| `platform_catalog` | Read-only platform reference data: models (LLM model ids), timezones (IANA strings for create_trigger), app_event_types (every selectable app/system event type). | catalog |
| `list_workflow_resources` | List one kind of workflow resource. | resource [service] [search] [activation] [eventType] [enabled] [triggerId] [flowId] [status] [page] [pageSize] [limit] |
| `get_workflow_resource` | Fetch one workflow resource by id (full config/params). | resource id |
| `create_action` | Create an action from a catalog entry. | catalogEntryId name [description] [isAsync] [params] |
| `create_trigger` | Create a trigger. | name activation [eventType] [scheduleRule] [customPayloadSchema] [timezone] [description] [conditions] |
| `create_flow` | Create a flow binding a trigger to ordered actions ([{actionId, position (1-based)}]); target devices go in the top-level deviceIds. | name triggerId actions deviceIds [description] [cooldownSeconds] [cooldownScope] |
| `manage_flow` | Flow lifecycle beyond create_flow. | operation [flowId] [name] [deviceIds] [actionId] [position] [continueOnError] [nameOverride] [overrides] [parentFlowActionId] [children] [flowActionId] [actions] [triggerId] [from] [to] |
| `workflow_events` | Ingest, simulate and catalog custom app/system events for trigger evaluation. | operation [eventType] [payload] [source] [page] [pageSize] [events] |

```
get_device {"deviceId": "192.168.1.20:5555"}
get_device_screenshot {"deviceId": "192.168.1.20:5555"}
get_device_ui_state {"deviceId": "192.168.1.20:5555"}
list_apps_on_device {"deviceId": "192.168.1.20:5555"}
create_device {}
terminate_device {"deviceId": "192.168.1.20:5555"}
manage_device {"operation": "reboot"}
device_action {"deviceId": "192.168.1.20:5555", "operation": "tap", "x": 540, "y": 1200}
manage_device_apps {"operation": "install", "deviceId": "192.168.1.20:5555"}
manage_device_files {"operation": "list", "deviceId": "192.168.1.20:5555", "path": "/sdcard/Download/a.apk"}
configure_device {"operation": "get_language", "deviceId": "192.168.1.20:5555"}
manage_esim {"operation": "list", "deviceId": "192.168.1.20:5555"}
list_credentials {}
list_credential_packages {}
manage_credentials {"operation": "init_package"}
webhooks {"operation": "create"}
proxies {"operation": "list"}
connect {"operation": "list_countries"}
apps {"operation": "list"}
platform_catalog {"catalog": "models"}
list_workflow_resources {"resource": "action_catalog"}
get_workflow_resource {"resource": "action_catalog", "id": "abc123"}
create_action {"catalogEntryId": "value", "name": "Ali"}
create_trigger {"name": "Ali", "activation": "event"}
create_flow {"name": "Ali", "triggerId": "value", "actions": [], "deviceIds": ["192.168.1.20:5555"]}
manage_flow {"operation": "clone"}
workflow_events {"operation": "ingest"}
```

### Devices and connection

| Tool | What it does | Arguments |
|---|---|---|
| `get_device_status` | Battery, screen power, foreground app, size, storage, network addresses, volume. | none |
| `list_devices` | Devices you can control. | [scope=local] [state] [type] [name] [country] [page] [pageSize] [filters] |
| `ping_device` | Is the device reachable? | none |
| `connect_device` | (Re)connect adb and the Portal for a device; use after the network path came back. | none |
| `disconnect_device` | Disconnect a TCP/IP adb device (adb disconnect host:port) and drop its session. | none |
| `setup_portal` | Install and enable the Mobilerun Portal on the device (mobilerun setup); path installs a specific Portal APK. | [path] |
| `doctor` | Health check of adb, the Portal and the device (mobilerun doctor). | none |
| `request_screen_capture_permission` | Compatibility no-op: screenshots use the Portal / adb screencap, no prompt is needed. | none |
| `echo` | Returns text verbatim: a check that the MCP transport is alive (no device access). | [text] [message] |

```
get_device_status {}
list_devices {}
ping_device {}
connect_device {}
disconnect_device {}
setup_portal {}
doctor {}
request_screen_capture_permission {}
echo {}
```

### Compatibility

| Tool | What it does | Arguments |
|---|---|---|
| `press` | Press home, back or enter (kept for old clients; prefer press_home/back/enter). | button |

```
press {"button": "back"}
```

### Raw adb

Only when `MOBILERUN_MCP_ENABLE_ADB=1`; refused while a safety policy is on.

| Tool | What it does | Arguments |
|---|---|---|
| `aura-adb` | Run an adb command against the device. | command |
| `adb` | Run an adb command against the device. | command |

```
aura-adb {"command": "shell dumpsys window | grep mCurrentFocus"}
adb {"command": "shell dumpsys battery"}
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOBILERUN_DEVICE` | the only attached device | Default device (see the `device` table above) |
| `MOBILERUN_MCP_POLICY` | `off` | Safety policy: `off`, `standard`, `strict` |
| `MOBILERUN_MCP_SCOPES` | `read,write` | Set to `read` to expose only read-only tools |
| `MOBILERUN_MCP_ENABLE_ADB` | `0` | Set to `1` to expose the raw `adb` tool |
| `BRAVE_API_KEY` | unset | `web_search` uses Brave when set, DuckDuckGo otherwise |
| `TAVILY_API_KEY` | unset | `web_search` uses Tavily (synthesized answer, AURA's provider) when set |
| `MOBILERUN_CLOUD_API_KEY` | unset | Mobilerun Cloud devices, tasks and the cloud platform tools |
| `MOBILERUN_CREDENTIALS` | `config/credentials.yaml` | Secrets file for `type_secret` (mobilerun format) |
| `MOBILERUN_DETECTOR_MODEL` | downloaded | Path to an OmniParser icon-detect `.onnx` |
| `MOBILERUN_IOS_PORTAL_URL`, `MOBILERUN_IOS_PORTAL_TOKEN` | `http://127.0.0.1:6643` | iOS portal for `device="ios"` |
| `MOBILERUN_ANDROID_PORTAL_TOKEN` | unset | Bearer token for Portal-HTTP-only Android targets |
| `MOBILERUN_MCP_HTTP_HOST`, `MOBILERUN_MCP_HTTP_PORT` | `127.0.0.1`, `4816` | Address for `--http` |
| `MOBILERUN_ADB_BIN`, `MOBILERUN_BIN` | on `PATH` | Binary overrides |

### Safety policy

The policy is **off by default**, so an agent can sign in to accounts and use any app.

- `standard` blocks banking, payment and wallet apps, authenticator apps and password managers,
  Luhn-valid card numbers, and fields asking for a card security code.
- `strict` additionally refuses password and PIN fields and national-id numbers.

Blocked actions fail with `[policy_blocked]`. `run_task` and the raw `adb` tool are disabled while
a policy is on, because they cannot be policed. Read `mobilerun://policy` for the active rules.

## Limitations

- **Tested live on redroid 12 (Android 12, x86_64).** Physical phones, other Android versions, iOS
  and cloud devices go through the same code paths but were not driven live here; the cloud tools
  are verified against mocked API responses.
- **Icon detection** (`detail="full"`) is a host-side guess: red boxes, not facts.
- **`media_control` with `package_name`** goes to the active media session (adb cannot address one
  app's session); the reply says when that is a different app.
- **Scratch-browser tabs** (WebView Browser Tester has one page) are remembered URLs that reload on
  `switch`; the signed-in browser (`session="mine"`, e.g. Chrome) has real tabs.
- **Notification actions and dismissal** drive the notification shade, because `adb` cannot fire a
  PendingIntent. Ongoing notifications cannot be dismissed.
- **Local `run_task`** runs the mobilerun CLI agent; `outputSchema`, `apps`, `credentials`, `files`
  and `stealth` apply to cloud tasks only. The agent's self-report can be wrong: verify on screen.
- **Launcher shortcuts** come from `dumpsys shortcut`; Android elides the path of https shortcut
  URIs there, so those open the app without the exact page.
- Volume commands succeed on redroid but have no audible effect.

## Development

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest                          # unit tests, no device needed
MOBILERUN_DEVICE=<serial> .venv/bin/python -m pytest -m live     # drives a real device
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

- Unit tests run against real output captured from a device (`tests/fixtures`; regenerate with
  `scripts/capture_fixtures.py`). The JavaScript snippets are syntax-checked with `node` when it
  is installed.
- Live tests drive the device through an in-process MCP client and assert observable effects
  (foreground app, screen contents, notification state, page state), not just that a call returned.
  They post notifications, change the media volume and open apps, so use a scratch device.

```
src/mobilerun_mcp/
  adb.py portal.py          transports: adb wrapper, Portal HTTP client
  session.py observe.py     per-device state, settle-and-observe
  models.py marks.py        screen model, numbered marks, signatures
  parsers/                  pure parsers for accessibility state, dumpsys, intent filters, ...
  policy.py ledger.py       safety rules, plan ledger
  browser/                  CDP client, target discovery, navigation, page scripts
  tools/                    one small module per tool group
```

## Relationship to other projects

Independent; not affiliated with AURA, Mobilerun/droidrun or redroid.

- [AURA](https://dinesh210805.github.io/aura-app/): same tool names, parameters and output formats,
  re-implemented on the host. No AURA code or assets are included.
- [mobilerun-core](https://pypi.org/project/mobilerun-core/) (Apache-2.0) is a dependency; its
  `Device` runs on this server's fast Android transport.
- [mobilerun](https://github.com/droidrun/mobilerun) (MIT): the agent's element indexing is adapted
  in `src/mobilerun_mcp/agentui.py`.
- [droidrun/mobilerun-mcp](https://github.com/droidrun/mobilerun-mcp) (Apache-2.0): the cloud tools
  in `src/mobilerun_mcp/tools/cloud.py` are a port of its tool layer.
- [Mobilerun Portal](https://github.com/droidrun/mobilerun-portal) provides screen access on Android.
- [OmniParser v2](https://huggingface.co/microsoft/OmniParser-v2.0) icon detector (AGPL-3.0),
  downloaded at runtime, not redistributed.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
