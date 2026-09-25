# Android Use

**Computer-use for Android.** An [MCP](https://modelcontextprotocol.io) server
that lets Claude *see and operate a real Android phone* — read the screen, tap,
type, scroll, change settings, launch apps — from anywhere, over cellular. No
root, no per-app API. Just the phone, driven the way a person drives it.

Think of it as the mobile counterpart to computer-use: instead of pixels and
blind coordinate-guessing, it reads the phone's **accessibility tree** and hands
the model a numbered list of what's actually on screen. The model acts by index.

![Android Use driving a phone](assets/demo.gif)

```
App: com.android.deskclock
  [0] More settings <Button> @975,437
  [1] 7:00 AM / gym / Mon,Tue,Wed,Thu,Fri,Sat <item> [ON]  @540,835
  [2] 10:00 AM / pick up parcel / Today       <item> [OFF] @540,1101
```

*"Turn off my gym alarm"* becomes `tap(1)` — precise, cheap, and it survives
layout shifts that break coordinate-based control.

Built for the people who find phones hard: an older relative in another city,
anyone who doesn't know where a setting lives. *"Why doesn't my phone ring?"*
→ Claude reads the ringer, sees it's on silent, and fixes it — over their
mobile data, while they watch.

> ⚠️ This is a **remote phone-control tool**. Read [SECURITY.md](SECURITY.md)
> before you run it. Conservative defaults are on for a reason; don't strip them.

## Why it's different

- **Accessibility tree, not screenshots.** Real, labelled, tappable targets —
  far more reliable than asking a model to guess pixels. When the tree isn't
  enough (videos, games, icon-only buttons), `take_screenshot(annotate=True)`
  draws the **same numbers on the picture**, so the model still acts by index.
- **Taps are verified.** An index is checked against the screen the model
  actually saw. If a notification pushed the list down, the tap follows the
  element to its new place; if it can't be sure, it refuses and shows the new
  screen. It never taps "whatever is at index 3 now".
- **Irreversible taps wait for a yes.** Send, pay, delete, call, sign out —
  held back until the model confirms, and (with the phone app) the person
  holding the phone gets a big *"Is that OK?"* card before it happens.
- **Shortcuts instead of navigation.** `change_setting('volume_ring', 80)`,
  `open_url('geo:…')`, `open_settings('wifi')`, `device_status()`,
  `scroll_to('Bluetooth')`, `run_steps([...])` — one call where a person would
  make ten taps through OEM-specific menus.
- **Talks to the person holding the phone.** `say_to_owner` shows a large
  message (and can read it aloud, in Telugu or Hindi too); `ask_owner` puts a
  question with buttons on their screen and waits for the answer.
- **Works from anywhere.** Cellular + [Tailscale](https://tailscale.com) means
  the phone needs no Wi-Fi and no cable — the case that actually matters when
  you're helping someone in another city.
- **Types any language.** The companion app uses `ACTION_SET_TEXT`, so Telugu,
  Hindi, emoji all work — `adb shell input text` can't do that.

## How it works

```
Claude (Code / Desktop / claude.ai / mobile)
    │  MCP
    ▼
Android Use server  ──►  Backend interface
    │                      ├── AdbBackend      (USB or Wi-Fi, via adb)
    │                      └── AppBackend       (on-device app + HTTP bridge)
    ▼
your phone  ── accessibility service reads the screen, dispatches taps/gestures
```

Two ways to reach the phone:

1. **ADB** — quickest to try. Plug in (or pair wirelessly), and it works.
2. **Companion app** — a small Android app with an AccessibilityService and a
   token-authenticated bridge. No dev options, survives reboot, reaches the
   phone over Tailscale from anywhere, wakes the screen, and can repair its own
   service remotely. `scripts/setup_phone.sh` does the one-time setup.

## Quick start (ADB, 2 minutes)

```bash
git clone https://github.com/bsaisuryacharan/android-use && cd android-use
uv venv && uv pip install -e .

# phone: Settings → About → tap Build number ×7 → Developer options → USB debugging
adb devices          # should list your phone

# register with Claude Code
claude mcp add -s user android-use -- "$PWD/.venv/bin/python" -m android_use.server
```

Restart Claude Code, then just talk: *"what's on my phone screen?"*,
*"turn off my 7am alarm"*, *"make the text bigger"*, *"open YouTube and search
for lo-fi"*.

For the fully-remote path (companion app + Tailscale + claude.ai connector), see
[SETUP_APP.md](SETUP_APP.md), [CONNECTOR.md](CONNECTOR.md), and
[MULTI_DEVICE.md](MULTI_DEVICE.md). When something goes wrong,
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Tools (51)

| | |
|---|---|
| **See** | `get_screen` (numbered elements; `all_text` for articles), `take_screenshot` (`annotate` numbers the picture, `index` zooms in), `wait_for` |
| **Act** | `tap` (verified; `hold`, `double`), `tap_text`, `tap_coordinates`, `type_text` (any field; `clear`, `submit`), `press_key`, `scroll`, `scroll_to`, `swipe`, `drag`, `pinch_zoom`, `run_steps`, `wake_screen` |
| **Apps & links** | `open_app` (allowlisted), `list_apps`, `open_url` (web, maps, Play Store, mailto:, tel: — never dials), `open_settings` |
| **Settings** | `device_status` ("why is it silent / slow / offline"), `change_setting` (volumes, ringer, Do Not Disturb, brightness, text size, screen timeout, flashlight, Wi-Fi…), `check_internet` |
| **The owner** | `say_to_owner`, `ask_owner`, `request_control` (one-tap "allow" notification) |
| **Health** | `check_device`, `phone_health`, `repair_phone`, `watchdog_report`, `activity_log` |
| **Phones** | `list_phones`, `add_phone`, `discover_phones`, `use_phone`, `lock_phone`, `unlock_phone`, `forget_phone` |
| **Chrome (ADB)** | `chrome_*` — drive Chrome via the DevTools protocol: real DOM, any language |
| **Setup (ADB)** | `pair_wireless`, `enable_wireless`, `connect_wireless`, `disconnect_wireless`, `list_adb_devices`, `list_devices` |

Read-only tools are marked as such, so clients can let them run without asking
each time. The server also offers ready-made prompts — *Phone check-up*, *Fix
my internet*, *Why doesn't my phone ring?*, *Make my phone easier to use* — for
people who would not know what to ask.

## Safety, briefly

- **Verified indexes** and **confirmation for irreversible taps** (above).
- **Banking and payment apps** are neither read nor operated unless the owner
  adds them to the allowlist — even when they are opened some other way.
- **Lock-out guard**: turning off Wi-Fi or mobile data, or turning on airplane
  mode, is held back when it would cut the connection to a far-away phone.
- **Visible sessions**: while Claude works, the phone shows *"Your assistant is
  using this phone — Stop"*, marks every tap with a ring, and keeps a log the
  owner can read in the app. The server keeps its own log (`activity_log`).
- **Time-boxed grants**, an **on-device allowlist**, and **no lock-screen
  bypass** — a PIN is only ever entered by the owner.
- Text on the phone is treated as **content, not instructions**: a web page
  that says "tap Send" is not the user asking.

Details and the threat model: [SECURITY.md](SECURITY.md).

## Using Claude on the same phone

Chatting with Claude in the Claude app *on the phone being controlled* works:

- Put Claude in **split screen or a pop-up window** next to the app you want
  help with — the companion app reads the other app, never the chat.
- Or just ask: Claude switches to the app it needs (the chat keeps running in
  the background) and, when the job ends with an answer, reopens Claude so you
  can read it.

## Honest limitations

- **Setup is the hard part**, especially the companion app on Android 13+
  ("restricted settings" blocks sideloaded accessibility apps until allowed).
  `scripts/setup_phone.sh` does it over one cable connection.
- **A PIN/pattern lock can't be bypassed** — the owner unlocks the phone.
- **Secure screens block screenshots** — banking apps often set `FLAG_SECURE`.
- **Android does not let apps switch Wi-Fi, mobile data or airplane mode**;
  over the app, `change_setting` opens the right panel for one tap instead.
  Over ADB it switches them directly.
- **Tokens are plaintext and rotate on reinstall.** Pairing UX is rough.
- **Google restricts accessibility apps** on the Play Store; distribution is a
  real consideration, not a formality.

## Development

```bash
uv pip install -e ".[test]" && pytest     # ~200 tests, no phone needed
```

The tests include a protocol-contract suite that runs the phone-app transport
against a fake bridge over real HTTP. CI builds the APK.

## Status

Working prototype. The core loop was verified end-to-end on vivo
(Funtouch/Android 15) and a Pixel 9 emulator (Android 16), driven from Claude
Code, Claude Desktop, and claude.ai over cellular. The 0.3 additions (verified
taps, owner prompts, direct settings, waking, protocol 2 of the phone app) are
covered by the test suite and a compile check against the Android 15 API, but
have not yet been through the same on-device testing — reports from real phones
are very welcome, especially OEM compatibility, onboarding UX, and the security
items in SECURITY.md.

## License

[Apache 2.0](LICENSE).
