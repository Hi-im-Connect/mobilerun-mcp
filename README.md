# mobilerun-mcp

**An MCP server that gives an AI agent eyes and hands on an Android device, including x86_64
devices and emulators such as [redroid](https://github.com/remote-android/redroid-doc).**

It exposes 68 tools (numbered on-screen elements, gestures, typing, apps, deep links, intents,
notifications, media, files, a browser, a plan ledger and more) to any MCP client: Claude Code,
Claude Desktop, Cursor, OpenCode, and so on. Everything runs on the host over `adb`.

```
  MCP client (Claude Code, Cursor, ...)
        |  stdio
  mobilerun-mcp ---- adb ----------------> Android device / redroid
     |    |                                 |- Mobilerun Portal  (accessibility service, local HTTP)
     |    '--- CDP over an adb forward ---> |- WebView browser   (DevTools)
     '--- tesseract (host-side OCR)
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

About ten minutes. You need three things: an Android device, this server, and an MCP client. Nothing
here assumes prior Android or MCP experience.

### What you need

| You need | What it is for | Get it |
|---|---|---|
| An Android device that `adb` can reach | The thing the agent controls | [Step 1](#1-get-an-android-device-ready) |
| `adb` (Android platform-tools) | Talks to the device | [Download](https://developer.android.com/tools/releases/platform-tools), or `sudo apt install adb`, or `brew install android-platform-tools` |
| `uv` | Installs Python and this project's dependencies (you do not need to install Python yourself) | [Install uv](https://docs.astral.sh/uv/getting-started/installation/) |
| `git` | Downloads this repo (or use the green **Code** button, then **Download ZIP**) | [git-scm.com](https://git-scm.com/downloads) |
| An MCP client | The AI app that will call the tools | [Claude Code](https://docs.claude.com/en/docs/claude-code/mcp), [Claude Desktop](https://modelcontextprotocol.io/quickstart/user), [Cursor](https://docs.cursor.com/context/model-context-protocol) |
| `tesseract` (optional) | Reads text on screens that expose little to accessibility | [Install guide](https://tesseract-ocr.github.io/tessdoc/Installation.html), or `sudo apt install tesseract-ocr`, or `brew install tesseract` |

### 1. Get an Android device ready

Pick whichever you have:

| Option | What to do |
|---|---|
| **A real phone over USB** | Turn on Developer options and USB debugging ([how](https://developer.android.com/studio/debug/dev-options)), plug it in, and tap **Allow** on the phone. |
| **The Android Studio emulator** | Create a virtual device with an x86_64 system image ([how](https://developer.android.com/studio/run/managing-avds)) and start it. |
| **redroid (Android in Docker)** | Install [Docker](https://docs.docker.com/get-docker/), load the kernel modules described in the [redroid docs](https://github.com/remote-android/redroid-doc), then run `docker run -itd --privileged -p 5555:5555 redroid/redroid:12.0.0-latest` and `adb connect localhost:5555`. |

Check it worked:

```bash
adb devices
```

Your device must be listed with the state `device` (not `unauthorized` or `offline`). Note the name in
the first column, its **serial**: for example `emulator-5554`, `R58M123ABC` or `localhost:5555`. You
will use it below as `<serial>`.

### 2. Install the Mobilerun Portal on the device

The Portal is a small Android app (an accessibility service) that lets the server read the screen.
It comes from [droidrun/mobilerun-portal](https://github.com/droidrun/mobilerun-portal). The
`mobilerun` command line tool installs and enables it for you:

```bash
uv tool install mobilerun
mobilerun setup -d <serial>
mobilerun ping -d <serial>
```

The last command should print `Portal is installed and accessible. You're good to go!` The setup
step downloads the APK (about 50 MB) from GitHub, so it can take a few minutes on a slow link.

<details>
<summary>Installing the Portal by hand instead</summary>

1. Download the newest `.apk` from the [Portal releases page](https://github.com/droidrun/mobilerun-portal/releases).
2. `adb -s <serial> install -r path/to/the.apk`
3. On the device: **Settings, Accessibility, Mobilerun Portal**, then turn it on.
   On a device with no screen to tap, this enables it from the command line, but it replaces any other
   accessibility services you had switched on:
   `adb -s <serial> shell settings put secure enabled_accessibility_services com.mobilerun.portal/com.mobilerun.portal.service.MobilerunAccessibilityService`
   and then `adb -s <serial> shell settings put secure accessibility_enabled 1`.

</details>

### 3. Install this server

```bash
git clone https://github.com/Hi-im-Connect/mobilerun-mcp.git
cd mobilerun-mcp
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
```

On Windows, use `.venv\Scripts\python.exe` wherever `.venv/bin/python` appears in this README.

### 4. Connect it to your MCP client

Use the full path to this project's Python. `$PWD` below is the folder you just cloned into.

**Claude Code:**

```bash
claude mcp add --scope user mobilerun -e MOBILERUN_DEVICE=<serial> -- "$PWD/.venv/bin/python" -m mobilerun_mcp
claude mcp list        # mobilerun should show as Connected
```

**Claude Desktop, Cursor and most other clients** read a JSON file. Add this entry, then restart the app:

```json
{
  "mcpServers": {
    "mobilerun": {
      "command": "/full/path/to/mobilerun-mcp/.venv/bin/python",
      "args": ["-m", "mobilerun_mcp"],
      "env": { "MOBILERUN_DEVICE": "<serial>" }
    }
  }
}
```

| Client | Config file |
|---|---|
| Claude Desktop, macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop, Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json` |

`MOBILERUN_DEVICE` is optional: if it is left out and exactly one device is attached to `adb`, that
device is used. Every device tool also takes a `device` argument, so one server can drive several.

### 5. Try it

Ask your agent in plain words:

- "Take a screenshot of my phone and tell me what is on it."
- "Open Settings and tell me the Android version."
- "Open the Clock app and set an alarm for 7:30."

Behind the scenes the agent calls tools such as `perceive_screen`, `launch_app` and `system_intent`,
and reads what the screen looks like after each step. The first launch of an app can take up to 20
seconds on a slow device. To call tools yourself and see what each one returns, go to
[Calling the tools](#calling-the-tools).

## Troubleshooting

| What you see | Why | What to do |
|---|---|---|
| `[device_unreachable] no device selected` | `MOBILERUN_DEVICE` is not set and `adb` sees no device, or more than one | Run `adb devices`, then set `MOBILERUN_DEVICE=<serial>` in the client config |
| `adb devices` shows `unauthorized` | The phone has not approved this computer | Unlock the phone and tap **Allow** on the USB debugging prompt (tick "Always allow"); if it never appears, run `adb kill-server` and reconnect |
| `adb devices` shows nothing, or `offline` | Cable, container or network problem | Replug the cable; for a container or remote device run `adb connect <host>:<port>` first and check the device is running |
| `adb: command not found` | platform-tools is not installed or not on `PATH` | Install it (link above), or point `MOBILERUN_ADB_BIN` at the `adb` binary |
| `Mobilerun Portal is not enabled as an accessibility service` | The Portal is installed but switched off | Turn it on under **Settings, Accessibility, Mobilerun Portal**, or run step 2 again |
| `mobilerun setup` stalls after "Found Portal APK" | Google Play Protect is scanning the install and never answers (seen on redroid and emulators with Google Play) | `adb -s <serial> shell settings put global verifier_verify_adb_installs 0` and `adb -s <serial> shell settings put global package_verifier_enable 0`, then run setup again |
| The client shows no `mobilerun` tools | The client has not reloaded, or the config path or JSON is wrong | Restart the client. To see the real error, run `.venv/bin/python -m mobilerun_mcp` by hand: a healthy server prints a FastMCP banner and then waits for input (press Ctrl+C); a traceback or error message instead is the problem |
| `[stale_som_id]` | An action changed the screen, so the old numbers are out of date | Normal. Call `perceive_screen` again and use the new numbers |
| A launch takes a long time | Cold app starts can take 20 to 30 seconds on a slow device | Wait; `launch_app` waits for the app for you |
| `web_search` returns an error | DuckDuckGo throttled the request | Retry shortly, or set `BRAVE_API_KEY` |
| Nothing is read from a screen that is mostly images | The screen exposes little to accessibility | Install `tesseract` so `perceive_screen` can add OCR text, or tap by coordinates from the screenshot |

Still stuck? [Open an issue](https://github.com/Hi-im-Connect/mobilerun-mcp/issues) with the exact
message and the output of `adb devices`.

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

Every capability is an ordinary MCP tool: a name plus a JSON `arguments` object. Your MCP client
makes the call when you describe what you want in words, and you can also invoke a tool by name.
On the wire a call looks like this:

```json
{"method": "tools/call", "params": {"name": "tap", "arguments": {"som_id": 2}}}
```

What holds for every tool:

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
| `settled` | The screen stopped changing before the wait ended |

### A typical session

Search the Contacts app for "ali". Calls are shown as `tool arguments`; outputs are real.

**1. Look at the screen.**

```
perceive_screen {}
```

The reply is a JSON object with `foreground_app`, `package`, `activity`, `keyboard_visible`,
`screen_size`, `mark_count`, `ocr_used` and `elements`. Here `elements` contains:

```
  1 [button] "Open navigation drawer" @(56,104)
  2 [button] "Search contacts" @(664,104)
  3 [text] "Contacts" @(224,104)
  4 [button] "A / Ali Omar" @(360,230)
  5 [button] "Create new contact" @(632,1096)
```

The number in front of each line is its `som_id`, and `@(x,y)` is where a tap would land. An image
of the screen with the same numbers drawn on it is returned alongside the JSON.

**2. Tap the search icon by its number.**

```
tap {"som_id": 2}
```

The reply has `"ok": true`, and its `post_action_observation` shows `keyboard_visible: true`. The
numbers are now stale: any action invalidates them, so a later `tap` with `som_id` 2 would fail with
`stale_som_id` until you call `perceive_screen` again.

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
    "settled": true
  }
}
```

**4. Verify.** Ask the screen instead of assuming.

```
verify_action {"expected": "Ali Omar"}
```

```json
{
  "verified": true,
  "evidence": "text=\"Ali Omar\"",
  "foreground": "com.android.contacts",
  "visible_text": ""
}
```

## Tool reference

One table per group, then example calls in the same order (`tool arguments`). In the arguments column, `[brackets]` mean optional and `=` shows the default. There are 68 tools, plus the raw `adb` tool when it is enabled.

### Perception

See the screen. `perceive_screen` is the one to call first.

| Tool | What it does | Arguments |
|---|---|---|
| `perceive_screen` | Numbered elements (`som_id`) plus an annotated screenshot. OCR is added when the accessibility tree is sparse. | [include_image=true] [ocr=auto/always/never] [max_marks=150] [lang=eng] |
| `read_screen` | The same numbered elements as text only (cheaper, no image). | none |
| `get_ui_tree` | Accessibility tree: class, id, label, flags (C click, L long-click, E editable, S scroll, K checkable, P password), bounds. | [max_depth=8] |
| `get_screenshot` | Plain screenshot as an image. | none |
| `screenshot` | Alias of `get_screenshot`. | none |
| `screenshot_path` | Save a screenshot as a PNG and return its path. | none |
| `get_device_status` | Model, Android version, battery, screen power, foreground app, size, storage, addresses, music volume. | none |

```
perceive_screen {}
perceive_screen {"include_image": false, "ocr": "never"}
read_screen {}
get_ui_tree {"max_depth": 5}
get_screenshot {}
screenshot {}
screenshot_path {}
get_device_status {}
```

### Gestures and typing

Every one of these waits for the screen to settle and returns a `post_action_observation`. Target a spot with `x`/`y` or with a `som_id` from the latest `perceive_screen`.

| Tool | What it does | Arguments |
|---|---|---|
| `tap` | Tap a point or the center of a numbered element. | x, y  or  som_id |
| `double_tap` | Two quick taps. | x, y  or  som_id |
| `long_press` | Press and hold. | x, y  or  som_id, [duration_ms=800] |
| `swipe` | Swipe between two points. | x1, y1, x2, y2, [duration_ms=300] |
| `scroll_down` | Scroll so content further down comes into view. | [amount=0.5] [som_id] |
| `scroll_up` | Scroll back up. | [amount=0.5] [som_id] |
| `scroll_left` | Scroll content to the left. | [amount=0.5] [som_id] |
| `scroll_right` | Scroll content to the right. | [amount=0.5] [som_id] |
| `scroll_to` | Keep scrolling until an element whose label contains `text` is visible; returns its mark. | text, [direction=down/up/left/right] [max_scrolls=8] |
| `type_text` | Type into the focused field. Tap the field first, or pass its `som_id`. | text, [clear=false] [submit=false] [som_id] |
| `press_home` | Home button. | none |
| `press_back` | Back button (also closes the keyboard). | none |
| `press_enter` | Enter key (submits a search bar). | none |
| `open_recent_apps` | Recent-apps overview. | none |
| `press` | Older form of the three buttons above. | button (home/back/enter) |

```
tap {"som_id": 2}
tap {"x": 360, "y": 640}
double_tap {"som_id": 5}
long_press {"som_id": 4, "duration_ms": 1000}
swipe {"x1": 360, "y1": 1000, "x2": 360, "y2": 400}
scroll_down {"amount": 0.7}
scroll_up {}
scroll_left {}
scroll_right {}
scroll_to {"text": "About phone"}
type_text {"text": "hello"}
type_text {"text": "hello", "clear": true, "submit": true}
press_home {}
press_back {}
press_enter {}
open_recent_apps {}
press {"button": "back"}
```

### Apps and deep links

| Tool | What it does | Arguments |
|---|---|---|
| `launch_app` | Open an app by name (fuzzy) or exact package. A vague name returns ranked candidates instead of guessing. | [app_name] [package] |
| `start_app` | Open an app by package name. | package |
| `lookup_app` | Search installed apps; returns package, label and a score. | query, [limit=5] |
| `list_apps` | Installed apps (user apps unless `system` is true). | [system=false] |
| `list_app_deeplinks` | Deep links an app registers (checked against the device), plus curated entries. | [package] [app_name] |
| `resolve_deeplink` | Which app would open a URI or intent action? Nothing is opened. | uri |
| `open_deeplink` | Jump straight to a screen by URI or intent action. | uri, [package] |

```
launch_app {"app_name": "settings"}
launch_app {"package": "com.android.contacts"}
start_app {"package": "com.android.settings"}
lookup_app {"query": "face"}
list_apps {}
list_apps {"system": true}
list_app_deeplinks {"package": "com.android.settings"}
resolve_deeplink {"uri": "android.settings.WIFI_SETTINGS"}
open_deeplink {"uri": "android.settings.WIFI_SETTINGS"}
```

### System intents and contacts

`system_intent` does common phone tasks in one call. `dial` and `compose_sms` only prefill; a person still presses call or send.

| Tool | What it does | Arguments |
|---|---|---|
| `system_intent` | One-call action. `verb` selects it and decides which other arguments apply: `set_alarm` (hour, minute, label), `set_timer` (seconds, label), `dial` (phone_number), `compose_sms` (phone_number, body), `add_calendar_event` (title, start, end, location, notes), `share_text` (text, subject), `navigate` (destination, mode drive/walk/bike/transit). | verb, plus the arguments of that verb |
| `resolve_contact` | Contact name to phone number(s). | name, [limit=5] |

```
system_intent {"verb": "set_alarm", "hour": 7, "minute": 30, "label": "Gym"}
system_intent {"verb": "set_timer", "seconds": 300}
system_intent {"verb": "dial", "phone_number": "+15551234567"}
system_intent {"verb": "compose_sms", "phone_number": "+15551234567", "body": "On my way"}
system_intent {"verb": "add_calendar_event", "title": "Demo", "start": "2026-10-01T15:00", "location": "Cairo"}
system_intent {"verb": "share_text", "text": "Look at this", "subject": "FYI"}
system_intent {"verb": "navigate", "destination": "Cairo Tower", "mode": "walk"}
resolve_contact {"name": "ali"}
```

### Notifications

Read them without opening the app. `key` comes from `read_notifications`. Acting on and dismissing notifications drives the notification shade, so treat those two as best-effort; ongoing notifications cannot be dismissed.

| Tool | What it does | Arguments |
|---|---|---|
| `read_notifications` | Posted notifications: key, app, title, text, action button labels, whether it can be cleared. | [package] [limit=30] [include_ongoing=true] |
| `notification_action` | Tap one of a notification's own buttons. `reply_text` fills an inline reply and sends it. | action, [key] [package] [title] [reply_text] |
| `dismiss_notification` | Dismiss one notification, or every clearable one. | [key] [package] [title] [clear_all=false] |

```
read_notifications {}
read_notifications {"package": "com.whatsapp"}
notification_action {"action": "Reply", "package": "com.whatsapp", "reply_text": "On my way"}
dismiss_notification {"clear_all": true}
```

### Media and volume

Controls the music stream.

| Tool | What it does | Arguments |
|---|---|---|
| `get_media_sessions` | What is playing (app, state, title) and the current volume. | [include_system=false] |
| `media_control` | Send a media key. `action` is one of play, pause, play_pause, stop, next, previous, rewind, fast_forward. | action |
| `volume_up` | Raise the volume. | [steps=1] |
| `volume_down` | Lower the volume. | [steps=1] |
| `mute` | Mute (the previous level is remembered) or unmute. | [muted=true] |

```
get_media_sessions {}
media_control {"action": "pause"}
volume_up {"steps": 2}
volume_down {}
mute {"muted": false}
```

### Files

Shared storage only (`/sdcard`).

| Tool | What it does | Arguments |
|---|---|---|
| `find_files` | Find files whose name contains `query`. | [query] [path=/sdcard] [limit=50] [max_depth=6] |
| `open_file` | Open a file in whichever app handles its type. | path |

```
find_files {"query": "invoice"}
open_file {"path": "/sdcard/Download/report.pdf"}
```

### Waiting and checking

Use these to confirm what happened instead of assuming.

| Tool | What it does | Arguments |
|---|---|---|
| `wait_for` | Wait until text is on screen and/or an app or activity is in front (`gone` waits for it to disappear). Meant for long waits such as downloads. | [text] [package] [activity] [gone=false] [timeout=15] [interval=0.5] |
| `verify_action` | Check an outcome against the live screen. `kind` is text (visible), gone (not visible), app, activity or changed (the last action changed the screen). | expected, [kind=text] [timeout=3] [use_ocr=false] |
| `validate_action` | Dry run: would this action be allowed and does its target exist? Nothing is executed. | action (tap/double_tap/long_press/swipe/type_text/launch_app/open_deeplink), then x, y, som_id, text, package, app_name or uri |
| `watch_device_events` | Collect what changes over a few seconds: foreground app, keyboard, screen content, notifications. | [duration=5] [interval=0.5] [kinds] |

```
wait_for {"text": "Download complete", "timeout": 60}
verify_action {"expected": "Ali Omar"}
verify_action {"expected": "com.android.settings", "kind": "app"}
validate_action {"action": "tap", "som_id": 4}
validate_action {"action": "type_text", "text": "hello"}
watch_device_events {"duration": 10, "kinds": ["foreground", "notifications"]}
```

### Plan, findings and research

For multi-step goals. `end_session(outcome="success")` is refused until `target_count` findings are recorded, which keeps the agent honest.

| Tool | What it does | Arguments |
|---|---|---|
| `set_plan` | Start a checklist. With 3+ steps and a `search_query`, the first web search comes back in the reply. | steps, [goal] [deliverable] [target_count=0] [search_query] |
| `mark_step` | Update a step: pending, in_progress, done, skipped or failed. | index, status, [note] |
| `record_finding` | Record one item. `quote` must appear on the current screen. | item, quote |
| `end_session` | Close the run: success, partial or failed. | [outcome=success] [summary] |
| `web_search` | Search the web (Brave with `BRAVE_API_KEY`, otherwise DuckDuckGo). Returns title, url, snippet. | query, [limit=5] |
| `get_usage_guide` | Built-in tips. Topics: overview, shortcuts, text_entry, failures, ledger, browser. | [topic] |

```
set_plan {"steps": ["Open Contacts", "Read the first 3 names"], "target_count": 3}
mark_step {"index": 0, "status": "done", "note": "opened"}
record_finding {"item": "first contact", "quote": "Ali Omar"}
end_session {"outcome": "partial", "summary": "Read 2 of 3"}
web_search {"query": "how to enable dark mode in Instagram android"}
get_usage_guide {}
get_usage_guide {"topic": "text_entry"}
```

### Browser

Drives the on-device browser (WebView Browser Tester) or an in-app WebView through Chrome DevTools. `browser_find` and `browser_read` (with `structure`) return element `ref`s such as `e3`; pass a `ref` or a CSS `selector` to `browser_act`. Refs go stale after navigation.

| Tool | What it does | Arguments |
|---|---|---|
| `browser_open` | Open a URL, or attach to an existing page (`target_id` from `browser_tabs`, or `app` for an in-app WebView). | [url] [session=default] [target_id] [app] [wait=true] [timeout=15] |
| `browser_tabs` | Every open page across the browser and in-app WebViews. | none |
| `browser_read` | Title, URL and visible text. `structure` also lists interactive elements with refs. | [session] [selector] [max_chars=6000] [structure=false] |
| `browser_find` | Find visible elements by text, label, placeholder or name; returns refs. | query, [session] [limit=10] |
| `browser_extract` | Structured data: `kind` is table (headers and row objects), links or text. | [kind=table] [selector] [limit=20] |
| `browser_wait` | Wait for text, a selector or a URL fragment (or just for the page to finish loading). | [text] [selector] [url_contains] [timeout=15] |
| `browser_act` | Act on the page. `action` is click, type, press, focus, hover, select, check, uncheck, scroll or scroll_into_view. | action, [ref] [selector] [text] [value] [key] [clear=false] [submit=false] [amount=600] |
| `browser_upload` | Attach a file to a file input. A host file is pushed to `/sdcard/Download` first. | path, ref or selector |
| `browser_screenshot` | Screenshot of the page (its app is brought to the foreground first, because a hidden WebView cannot render). | [session] [full_page=false] |
| `browser_handoff` | Bring the browser to the foreground so a person can finish a login or captcha, then continue with `browser_read`. | [message] |
| `browser_close` | Detach a browser session and blank the page. | [session] [close_app=false] |

```
browser_open {"url": "https://example.com"}
browser_open {"app": "com.example.app"}
browser_tabs {}
browser_read {"structure": true}
browser_read {"selector": "#price"}
browser_find {"query": "Sign in"}
browser_extract {"kind": "table"}
browser_extract {"kind": "links", "selector": "nav"}
browser_wait {"text": "Order confirmed", "timeout": 30}
browser_act {"action": "click", "ref": "e3"}
browser_act {"action": "type", "selector": "#email", "text": "me@example.com", "submit": true}
browser_act {"action": "select", "selector": "#country", "value": "Egypt"}
browser_act {"action": "press", "key": "Enter"}
browser_act {"action": "scroll", "amount": 800}
browser_upload {"path": "/sdcard/Download/photo.jpg", "selector": "input[type=file]"}
browser_screenshot {}
browser_screenshot {"full_page": true}
browser_handoff {"message": "please sign in"}
browser_close {}
```

### Devices, sessions and agents

| Tool | What it does | Arguments |
|---|---|---|
| `list_devices` | Devices `adb` can see. | none |
| `ping_device` | Is the Portal reachable? Returns its transport. | none |
| `connect_device` | Reconnect adb and the Portal (use after the network path came back). | none |
| `echo` | Check that the MCP server itself is alive (does not touch the device). | [message] |
| `request_screen_capture_permission` | Compatibility no-op; screenshots need no permission over adb. | none |
| `run_task` | Hand a goal in plain language to the Mobilerun LLM agent. Best-effort: verify the result on screen. Disabled while a safety policy is on. | task, [vision=false] [reasoning=false] [steps=15] |
| `adb` | Raw `adb` command. Only present when `MOBILERUN_MCP_ENABLE_ADB=1`; refused while a safety policy is on. | command |

```
list_devices {}
ping_device {}
connect_device {}
echo {"message": "hi"}
request_screen_capture_permission {}
run_task {"task": "Open Clock and tell me the first alarm", "steps": 20}
adb {"command": "shell dumpsys battery"}
```

Resources: `mobilerun://guide`, `mobilerun://policy`, `mobilerun://ledger`, `mobilerun://device/snapshot`. Prompts: `perceive_act_verify`, `research_then_act`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOBILERUN_DEVICE` | the only attached device | adb serial (`host:port`, `emulator-5554`, USB serial) |
| `MOBILERUN_MCP_POLICY` | `off` | Safety policy: `off`, `standard`, `strict` |
| `MOBILERUN_MCP_SCOPES` | `read,write` | Set to `read` to expose only read-only tools |
| `MOBILERUN_MCP_ENABLE_ADB` | `0` | Set to `1` to expose the raw `adb` tool |
| `BRAVE_API_KEY` | unset | `web_search` uses Brave when set, DuckDuckGo otherwise |
| `MOBILERUN_ADB_BIN`, `MOBILERUN_BIN` | on `PATH` | Binary overrides |

### Safety policy

The policy is **off by default**, so an agent can sign in to accounts and use any app.

- `standard` blocks banking, payment and wallet apps, authenticator apps and password managers,
  Luhn-valid card numbers, and fields asking for a card security code.
- `strict` additionally refuses password and PIN fields and national-id numbers.

Blocked actions fail with `[policy_blocked]`. `run_task` and the raw `adb` tool are disabled while
a policy is on, because they cannot be policed. Read `mobilerun://policy` for the active rules.

## Limitations

- **Developed and tested on redroid 12 (Android 12, x86_64).** Other Android versions and real
  phones should work wherever the Portal works, but they are untested.
- **No icon detector.** An element the app draws itself without exposing it to accessibility (a
  game, some custom canvases) and that has no text is not numbered. Tap it by coordinates from the
  screenshot. There is no vision model in the loop.
- **Notification actions and dismissal** drive the notification shade, because `adb` cannot fire a
  PendingIntent. They are best-effort, and ongoing notifications cannot be dismissed.
- **The browser tools** drive WebView Browser Tester (one page) or an in-app WebView. A
  backgrounded WebView cannot render, so `browser_screenshot` brings its app to the foreground.
- **`run_task`** delegates to the Mobilerun LLM agent; its self-reported result can be wrong, so
  verify it against the screen.
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

This project is independent and not affiliated with AURA, Mobilerun/droidrun, or redroid.

- It reproduces a compatible **tool surface** for [AURA](https://dinesh210805.github.io/aura-app/)'s
  MCP server, based on its public documentation. It contains no AURA code or assets. AURA itself is
  an on-device app and remains the better choice on a real ARM phone, where its on-device
  perception model, voice features and native notification access are available.
- Screen access and typing rely on the [Mobilerun Portal](https://github.com/droidrun/mobilerun-portal).
- [redroid](https://github.com/remote-android/redroid-doc) is the reference x86_64 target.

## License

MIT. See [LICENSE](LICENSE).
