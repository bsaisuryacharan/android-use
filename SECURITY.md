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
  until you explicitly add them. Enforced **on the device**, not just the server.
- **Control is time-boxed.** A grant expires; after it lapses the bridge refuses
  everything until the phone's owner grants again.
- **A persistent notification is always shown** while control is possible, with
  a one-tap **Stop**. The owner can always see that the phone can be driven, and
  end it instantly.
- **Nothing bypasses a lock screen.** A PIN, pattern, or fingerprint cannot be
  defeated. The owner must unlock the phone.

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
- **`WRITE_SECURE_SETTINGS`** (used for self-repair of the accessibility
  service) requires a one-time ADB grant; without it the phone cannot re-enable
  its own service remotely.
- The MCP server's `accounts` bearer tokens add identity, expiry, and
  revocation, but device-level tokens do not yet.

## Reporting a vulnerability

Open a private security advisory on GitHub, or email the maintainer. Please do
not file a public issue with a working exploit. Describe the class of problem;
we'll coordinate a fix.
