#!/usr/bin/env bash
# One-time setup of the Android Use app on a phone, over adb (a cable, or
# wireless debugging). Everything SETUP_APP.md describes, in one go:
#
#   ./scripts/setup_phone.sh [path/to/app.apk] [--allow-settings] [--allow-dnd]
#
# - installs the app with Play attribution, which makes Android less eager to
#   revoke a sideloaded app's accessibility access
# - allows "restricted settings" and grants WRITE_SECURE_SETTINGS, so the
#   phone can switch its own accessibility service back on remotely
# - allows notifications, so the "someone can control this phone - Stop"
#   notification is actually visible (Android 13+ hides it otherwise)
# - enables the accessibility service WITHOUT turning off any others
# - exempts the app from battery optimisation, then opens it
#
# Optional, only with the owner's agreement:
#   --allow-settings  lets the assistant change brightness, text size and the
#                     screen timeout directly
#   --allow-dnd       lets the assistant turn Do Not Disturb on and off
#
# With several devices attached, pick one with ANDROID_SERIAL=<serial>.
set -euo pipefail

PKG=com.androiduse.client
SVC="$PKG/$PKG.ControlAccessibilityService"
ADB="${ADB:-adb}"
APK=""
ALLOW_SETTINGS=0
ALLOW_DND=0

say()  { printf "\033[1;36m▸ %s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
die()  { printf "  \033[31m✗ %s\033[0m\n" "$1"; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --allow-settings) ALLOW_SETTINGS=1 ;;
    --allow-dnd) ALLOW_DND=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *.apk) APK="$arg" ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

command -v "$ADB" >/dev/null || die "adb not found - install Android platform-tools or set ADB=/path/to/adb"
on_phone() { "$ADB" shell "$@" | tr -d '\r'; }
# Run a grant, and say plainly whether it worked.
grant() {
  local what="$1"; shift
  if on_phone "$@" >/dev/null 2>&1; then ok "$what"; else warn "could not: $what"; fi
}

say "Checking the phone"
state="$("$ADB" get-state 2>&1 || true)"
case "$state" in
  device) ;;
  *unauthorized*) die "the phone has not allowed this computer yet - unlock it and tap 'Allow' on the USB debugging prompt" ;;
  *"more than one"*) die "several devices are attached - set ANDROID_SERIAL to the one you mean (adb devices)" ;;
  *) die "no phone found ($state). Plug it in with USB debugging on, or pair wireless debugging first" ;;
esac
sdk="$(on_phone getprop ro.build.version.sdk)"
ok "$(on_phone getprop ro.product.manufacturer) $(on_phone getprop ro.product.model), Android $(on_phone getprop ro.build.version.release) (SDK $sdk)"
[ "${sdk:-0}" -ge 30 ] || die "Android 11 or newer is needed (screenshots without a consent prompt)"

if [ -z "$APK" ]; then
  # A freshly built APK in this checkout, if there is one.
  APK="$(ls -t app/build/outputs/apk/*/*.apk 2>/dev/null | head -1 || true)"
fi
if [ -n "$APK" ]; then
  say "Installing $APK"
  [ -f "$APK" ] || die "no such file: $APK"
  if "$ADB" install -r -i com.android.vending "$APK" >/dev/null 2>&1; then
    ok "installed (with Play attribution)"
  else
    "$ADB" install -r "$APK" >/dev/null || die "install failed"
    ok "installed"
  fi
fi
on_phone pm path "$PKG" | grep -q package: || die "Android Use is not installed - pass the APK path (download it from the GitHub release)"

say "Granting what the app needs to keep working remotely"
# THE IMPORTANT ONE. Without it Android silently revokes a sideloaded app's
# accessibility access minutes to hours later.
grant "restricted settings allowed" cmd appops set "$PKG" ACCESS_RESTRICTED_SETTINGS allow
grant "can repair its own accessibility service" pm grant "$PKG" android.permission.WRITE_SECURE_SETTINGS
if [ "$sdk" -ge 33 ]; then
  grant "notifications allowed (the Stop notification is visible)" \
    pm grant "$PKG" android.permission.POST_NOTIFICATIONS
fi
grant "exempt from battery optimisation" dumpsys deviceidle whitelist "+$PKG"

if [ "$ALLOW_SETTINGS" = 1 ]; then
  grant "may change brightness, text size and screen timeout" appops set "$PKG" WRITE_SETTINGS allow
fi
if [ "$ALLOW_DND" = 1 ]; then
  grant "may turn Do Not Disturb on and off" cmd notification allow_dnd "$PKG"
fi

say "Turning on the accessibility service"
current="$(on_phone settings get secure enabled_accessibility_services)"
[ "$current" = "null" ] && current=""
case ":$current:" in
  *":$SVC:"*) updated="$current" ;;
  *) updated="${current:+$current:}$SVC" ;;   # append: never switch off the owner's other services
esac
on_phone settings put secure enabled_accessibility_services "$updated"
on_phone settings put secure accessibility_enabled 1
sleep 2
if on_phone dumpsys accessibility | grep -q "$PKG"; then ok "accessibility service running"
else warn "the service is not running yet - open Android Use and check step 1"; fi

say "Opening the app"
on_phone am start -n "$PKG/.MainActivity" >/dev/null && ok "Android Use is open on the phone"

cat <<'NEXT'

Next, on the phone:
  1. In Android Use, tap "Allow help for ..." (control is always time-limited).
  2. Install Tailscale, sign in to the same account as this computer, and set
     it as the always-on VPN (the app walks through this).
  3. Tap "Send these details to my helper".

Then, from Claude:  add_phone("mum", "<the 100.x address>", "<the code>")
NEXT
