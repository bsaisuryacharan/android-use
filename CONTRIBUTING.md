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

- MCP server (Python): `uv pip install -e ".[test]"` then run `python -m android_use.server`.
- Tests: `pytest` — about 200 tests, no phone needed. Tools run against a fake
  phone (`tests/conftest.py`), and the phone-app transport runs against a fake
  bridge over real HTTP (`tests/test_app_backend.py`).
- Companion app (Kotlin): open in Android Studio, or `./gradlew assembleDebug`.
  CI builds the APK.

## Changing the phone protocol

The server and the phone app are released separately, so a phone may run an
older app than the server expects. When you add a route or a field:

1. Bump `PROTOCOL` in `HttpBridge.kt`.
2. In `app_backend.py`, only use it when `self.protocol` is high enough
   (`self._needs(2, "...")` gives the user a clear "update the app").
3. Add a contract test in `tests/test_app_backend.py` pinning the path and body.

## Ground rules

- **Keep the safety defaults.** The on-device allowlist, time-boxed grants, and
  always-on notification are not optional extras — they're what keeps this tool
  on the right side of the line. PRs that remove them won't be merged.
- Match the surrounding style. Explain *why* in comments, not *what*.
- One focused change per PR. Describe what you tested and on which device/OS.

## Reporting bugs

Use the issue templates. Always include device model, Android version, and
whether you're on the ADB or companion-app path.
