# Contributing

Thanks for helping. Android Use is early, and the most valuable contributions
are the unglamorous ones: OEM compatibility, onboarding friction, and the
security items in [SECURITY.md](SECURITY.md).

## Good first areas

- **OEM quirks.** Tested mainly on vivo (Funtouch) and Pixel. Samsung, Xiaomi,
  Oppo, OnePlus, Motorola all differ — accessibility trees, settings deep links,
  aggressive background-killing. Report what breaks; fixes are welcome.
- **Onboarding UX.** The setup barrier is the real blocker for non-technical
  users. Anything that reduces it (pairing codes, QR, clearer prompts) matters
  more than features.
- **Security.** Per-device token rotation/expiry, tighter auth, audit logging.
  See the known-limitations section of SECURITY.md.

## Setup

- MCP server (Python): `uv pip install -e .` then run `python -m android_use.server`.
- Companion app (Kotlin): open in Android Studio, or `./gradlew assembleDebug`.

## Ground rules

- **Keep the safety defaults.** The on-device allowlist, time-boxed grants, and
  always-on notification are not optional extras — they're what keeps this tool
  on the right side of the line. PRs that remove them won't be merged.
- Match the surrounding style. Explain *why* in comments, not *what*.
- One focused change per PR. Describe what you tested and on which device/OS.

## Reporting bugs

Use the issue templates. Always include device model, Android version, and
whether you're on the ADB or companion-app path.
