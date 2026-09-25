# Security & responsible use

Android Use lets an AI read and control a real Android phone over the network.
That is powerful, and it is also exactly the capability phone-scam malware
abuses. Read this before you run it, and before you ask anyone else to.

## Threat model — be honest with yourself

Anyone who can (a) reach the phone's bridge on the network **and** (b) present
a valid token can read the screen and tap anything. So the security of a phone
running this rests on two things: **network reachability** and **the token**.

- **Do not expose the bridge to the public internet.** It is designed to sit on
  a private network — a Tailscale tailnet or a LAN you trust. There is no
  hardening that makes a phone-control endpoint safe to leave open to the world.
- **Treat the token like a password.** It grants full control. It lives in the
  phone's app storage and in your MCP config. Don't paste it into chats, issues,
  screenshots, or logs.
- **The MCP server is a bridge to your phone.** If you expose the MCP server
  itself (e.g. via a tunnel so claude.ai can reach it), that endpoint must be
  authenticated. A capability URL or bearer token is the minimum; anyone with it
  is you.

## Defaults are conservative on purpose

- **App allowlist is ON.** Only a short list of safe apps (Settings, Chrome,
  Clock, …) can be launched. Banking, payment, and messaging apps are refused
  until you explicitly add them. Enforced **on the device**, not just the
  server; the owner edits the device list in the app.
- **Banking and payment apps are not read or operated** unless allowlisted —
  even when they reach the screen some other way (the owner opened one, a link
  bounced there). Both the server and the phone check the app in front.
- **Irreversible taps need a second step.** A tap whose label sends, pays,
  deletes, calls or signs out is held back until the model repeats it with
  `confirm=True`. With the phone app's **Ask me first** setting (on by
  default), the person holding the phone must also tap *Yes* on an on-screen
  card before it happens.
- **Indexes are verified.** A tap by index is checked against the screen the
  model actually saw; a moved "Delete" button is never tapped on the
  assumption that it is the same one.
- **Lock-out guard.** Turning off Wi-Fi or mobile data, or turning on airplane
  mode, is held back when it would cut the connection to a far-away phone.
- **Links are filtered.** `intent:`, `upi:`, `javascript:`, `file:` and
  similar schemes are refused; a link that resolves to a non-allowlisted app is
  opened in the browser instead, or refused if it would land in a banking app.
  `tel:` links only ever fill in the dialler.
- **Control is time-boxed.** A grant expires; after it lapses the bridge refuses
  everything until the phone's owner grants again. `request_control` can ask
  for a grant, but only the owner's tap on the notification gives one, and
  requests are rate-limited to one a minute.
- **The owner can always see it.** A persistent notification with **Stop**
  while control is possible; a *"Your assistant is using this phone · Stop"*
  banner and a ring on every tap while commands arrive; and a log of what was
  done on the app's main screen. The server keeps its own audit log
  (`activity_log`, `~/.android-use/audit.jsonl`); typed passwords are never
  recorded or echoed.
- **Nothing bypasses a lock screen.** A PIN, pattern, or fingerprint cannot be
  defeated. The app can wake the screen and dismiss a *swipe* lock; for a secure
  lock it shows Android's unlock screen to the owner.

## What this project will not help you do

This is built for helping people operate **their own phones**, or a phone whose
owner has knowingly consented and can revoke access at any time. It is not for
controlling a phone without the owner's ongoing awareness. The always-on
notification, the time-boxed grant, and the on-device allowlist exist to keep it
on the right side of that line. Do not remove them.

If you are setting this up for someone else (an older relative, say), they must
understand what they granted and how to stop it. Full stop.

## Known limitations (documented, not hidden)

- **Tokens are stored as plaintext** in the app's private storage and the MCP
  config. They are long-lived and rotate only on reinstall/data-clear.
- **The on-device bridge trusts any caller with the token** — there is no
  per-request identity beyond the token.
- **Risky-tap detection is a heuristic.** It reads labels (English plus common
  Hindi, Telugu, Spanish, French, German and Portuguese words) and view ids. An
  app that labels its "Pay" button "Continue" will not be caught. It is a seat
  belt, not a guarantee; the allowlist is the real boundary.
- **Prompt injection.** Anything on the screen — a web page, a message, a
  notification — reaches the model as text. The server instructs the model to
  treat it as content, not instructions, and the guards above limit what a
  fooled model can do, but no instruction is a hard boundary.
- **The ADB transport has only the server-side guards.** The on-device
  allowlist, *Ask me first*, the session banner and the owner's log belong to
  the phone app. Over ADB, whoever holds the USB-debugging authorisation has
  full control of the phone by design.
- **`WRITE_SECURE_SETTINGS`** (used for self-repair of the accessibility
  service) requires a one-time ADB grant; without it the phone cannot re-enable
  its own service remotely. The optional *Modify system settings* and *Do Not
  Disturb access* grants widen what a token holder can change; they are off
  unless the owner allows them.
- The MCP server's `accounts` bearer tokens add identity, expiry, and
  revocation, but device-level tokens do not yet.

The phone app's protocol 2 also removed the unused `/secure_setting` route
(which let a token holder write any global setting), restricts `/settings` to
settings pages, and caps request sizes.

## Reporting a vulnerability

Open a private security advisory on GitHub, or email the maintainer. Please do
not file a public issue with a working exploit. Describe the class of problem;
we'll coordinate a fix.
