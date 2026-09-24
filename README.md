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

**1. Prepare the device.** You need an Android device reachable by `adb` with the Mobilerun Portal
installed and its accessibility service enabled:

```bash
adb connect 192.168.1.50:5555            # skip for USB or emulator-5554
uv tool install mobilerun                # only used for the one-time Portal setup and run_task
mobilerun setup -d 192.168.1.50:5555     # installs the Portal APK and enables the service
```

Need a device? A redroid container is the quickest x86_64 target (see the redroid docs for the
kernel modules it needs):

```bash
docker run -itd --privileged -p 5555:5555 redroid/redroid:12.0.0-latest
```

**2. Install the server.**

```bash
git clone https://github.com/Hi-im-Connect/mobilerun-mcp.git
cd mobilerun-mcp
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .
```

Requirements: Python 3.11+, `adb` on `PATH`. Optional: `tesseract` for OCR of screens whose
accessibility tree is sparse.

**3. Register it with your MCP client.**

```bash
# Claude Code
claude mcp add --scope user mobilerun -e MOBILERUN_DEVICE=192.168.1.50:5555 -- \
  "$PWD/.venv/bin/python" -m mobilerun_mcp
```

Any other client (Claude Desktop, Cursor, ...):

```json
{
  "mcpServers": {
    "mobilerun": {
      "command": "/path/to/mobilerun-mcp/.venv/bin/python",
      "args": ["-m", "mobilerun_mcp"],
      "env": { "MOBILERUN_DEVICE": "192.168.1.50:5555" }
    }
  }
}
```

If `MOBILERUN_DEVICE` is unset and exactly one device is attached to `adb`, that device is used.
Every device tool also accepts a `device` argument, so one server can drive several devices.

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

## Tools

| Group | Tool | What it does |
|---|---|---|
| Perception | `perceive_screen` | Numbered marks plus an annotated screenshot; OCR for sparse screens |
| | `read_screen` | Text-only view of the same numbered elements |
| | `get_ui_tree` | Compact accessibility tree with flags and bounds |
| | `get_screenshot` / `screenshot` / `screenshot_path` | Plain screenshot as an image or a saved file |
| | `get_device_status` | Battery, screen power, foreground app, size, storage, addresses, volume |
| Gestures | `tap`, `double_tap`, `long_press` | At `x,y` or a `som_id` |
| | `swipe`, `scroll_up/down/left/right` | Free swipe or directional scroll |
| | `scroll_to` | Scroll until an element with the given text is visible |
| | `type_text` | Type into the focused field (`clear`, `submit`, or `som_id` to focus first) |
| | `press_home`, `press_back`, `press_enter`, `open_recent_apps`, `press` | Hardware keys |
| Apps | `launch_app` | By name (fuzzy) or package; ambiguous names return ranked candidates |
| | `lookup_app`, `list_apps`, `start_app` | Find and list installed apps |
| | `list_app_deeplinks`, `resolve_deeplink`, `open_deeplink` | Discover, check and open deep links or intent actions |
| Intents | `system_intent` | Alarm, timer, dial, SMS, calendar event, share, navigate in one call |
| | `resolve_contact` | Contact name to phone number |
| Notifications | `read_notifications` | Key, app, title, text, action labels, clearable |
| | `notification_action`, `dismiss_notification` | Tap a notification's own button; dismiss one or all |
| Media | `get_media_sessions`, `media_control` | Playback state and play/pause/next/previous/stop |
| | `volume_up`, `volume_down`, `mute` | Music-stream volume |
| Files | `find_files`, `open_file` | Search and open files on shared storage |
| Waiting | `wait_for`, `watch_device_events` | Wait for text/app/activity; collect foreground, keyboard, screen and notification events |
| | `validate_action`, `verify_action` | Dry-run an action; check an outcome against the live screen |
| Plan | `set_plan`, `mark_step`, `record_finding`, `end_session` | Checklist and findings; success is refused until enough findings are recorded |
| | `web_search`, `get_usage_guide` | Web search (Brave API or DuckDuckGo); built-in usage guide |
| Session | `list_devices`, `ping_device`, `connect_device`, `echo`, `request_screen_capture_permission` | Connection management |
| Browser | `browser_open`, `browser_close`, `browser_tabs` | Open a URL in the on-device browser or attach to an in-app WebView |
| | `browser_read`, `browser_find`, `browser_extract`, `browser_wait` | Read text and structure, find elements, pull tables and links, wait for content |
| | `browser_act`, `browser_upload`, `browser_screenshot`, `browser_handoff` | Click, type, select, scroll, press keys, upload files, capture, hand control to a person |
| Agent | `run_task` | Hand a goal to the Mobilerun LLM agent (best-effort) |
| Raw | `adb` | Raw adb passthrough, only when explicitly enabled |

Resources: `mobilerun://guide`, `mobilerun://policy`, `mobilerun://ledger`,
`mobilerun://device/snapshot`. Prompts: `perceive_act_verify`, `research_then_act`.

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
