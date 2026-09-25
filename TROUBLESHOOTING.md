# Troubleshooting

What goes wrong when Claude drives a real phone, why, and what to do. Start
with `phone_health` or `check_device` — they say which of these you are in.

## "Phone not reachable"

| What it says | What is going on | Fix |
|---|---|---|
| *Could not reach the phone at 100.x…* | The phone is off, offline, or Tailscale is down on one side. The first request after the phone has been idle can fail once while Tailscale wakes up; it is retried automatically. | Check the phone has signal and Tailscale shows *Connected*. Set Tailscale as the **always-on VPN** so a reboot does not leave it off. |
| *The accessibility service is off…* | Android revoked the app's accessibility access (it does this to sideloaded apps). | `repair_phone` fixes it remotely if setup was done over a cable. Otherwise the owner re-enables *Android Use* under Settings › Accessibility — the app shows a notification with a shortcut. Re-running `scripts/setup_phone.sh` makes it repairable in future. |
| *Control is not granted…* | The owner's time-limited grant ran out, or they tapped Stop. | `request_control` — the owner gets a notification and allows it with one tap. |
| *The phone rejected the pairing token* | The app was reinstalled or its data cleared, which makes a new token. | Owner opens Android Use › *Send these details to my helper*; `add_phone` again with the new code. |
| *…too old for this (/route)* | The phone runs an older Android Use app than the server. | Install the latest APK over the old one (settings and token are kept). |

## The screen

- **The phone keeps locking while Claude works.** Handled: actions wake the
  screen, and while commands keep arriving the app keeps it on. A PIN, pattern
  or fingerprint lock is never bypassed — the phone shows its unlock screen and
  the owner has to unlock it. Consider a longer screen timeout
  (`change_setting('screen_timeout', '2m')`) for someone who is helped often.
- **"Did not act on [3]…: the screen has changed."** Working as intended. The
  element Claude picked moved or disappeared between reading the screen and
  tapping (a notification, a pop-up, the owner touching the phone). The tool
  shows the new screen; pick again from it.
- **"The screen did not change."** The tap registered nowhere visible. Try
  `take_screenshot(annotate=True)` — the element may be a decoy container, or
  the real target may be an unlabelled icon next to it.
- **Nothing tappable is listed / only "(no label)".** Games, video, maps and
  some custom apps draw their own UI. Use `take_screenshot(annotate=True)` and
  act on the numbers, or `tap_coordinates` for things with no element at all.
- **The screenshot is black.** The app sets `FLAG_SECURE` (banking, some
  video apps). That is a protection, not a bug.
- **The Claude app is what gets read.** You are chatting on the phone being
  controlled. Put Claude in split screen or a pop-up window, or let Claude
  switch apps (it will come back when it is done).

## Typing

- **"This connection (ADB) can only type plain English…"** `adb shell input
  text` cannot send other scripts or emoji. Use the Android Use app transport,
  or `chrome_type` for web pages.
- **"Several text fields are on screen…"** Say which one: `type_text('…',
  index=N)`.
- **Search does not run after typing.** Pass `submit=True` — it presses the
  keyboard's enter/search key, which many search boxes need.

## "Held back"

These are deliberate and need a second, explicit step:

- **A tap that sends, pays, deletes, calls or signs out.** Repeat with
  `confirm=True` once the user has asked for exactly that. If the phone's owner
  has *Ask me first* on, they also get an on-screen *Is that OK?* card.
- **A banking or payment app is on screen.** Not read or driven unless the
  owner adds its package to the allowlist (server `config.json` *and* the app).
- **A change that could cut the connection** — Wi-Fi or mobile data off,
  airplane mode on — when it is how you reach the phone. Repeat with
  `confirm=True` only if someone local can undo it.
- **"not on the allowlist"** — `open_app` and links only open allowlisted apps
  (web links to other apps open in the browser instead). Add packages to
  `~/.android-use/config.json` under `allowed_packages`.

To turn off the tap confirmation entirely (not recommended), set
`"confirm_risky_actions": false` in the config. Extra words to treat as risky:
`"risky_words": ["transfer", "..."]`.

## Settings that cannot be changed directly

Android does not let ordinary apps switch Wi-Fi, mobile data, airplane mode,
Bluetooth or location. Over the app, `change_setting` opens the right system
panel and says so — one tap on the switch finishes it. Over ADB they are
switched directly. Brightness, text size and screen timeout need the owner to
allow *Modify system settings* (Android Use › Extra permissions), and Do Not
Disturb needs *Do Not Disturb access*.

## Where the logs are

- `activity_log` — everything this server did, per phone
  (`~/.android-use/audit.jsonl`).
- The Android Use app's main screen — what was done on that phone, as its
  owner sees it.
- `watchdog_report` — reachability over time, if the watchdog is running.
