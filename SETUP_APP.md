# Setting up the phone client

One-time setup over ADB. After this the phone needs no cable, no Wi-Fi
debugging, and no developer options — it works from anywhere over Tailscale.

## 1. Get the APK

Download `app-debug.apk` from the latest GitHub release (CI builds it), or
build it yourself:

```bash
./gradlew assembleRelease     # or assembleDebug
```

## 2. One command: install and grant

With the phone connected over adb (cable, or wireless debugging):

```bash
./scripts/setup_phone.sh path/to/app.apk
```

It installs the app with Play attribution, allows restricted settings, grants
`WRITE_SECURE_SETTINGS` (so the phone can repair itself remotely), allows
notifications, exempts the app from battery optimisation, turns on the
accessibility service **without switching off any others**, and opens the app.

Two optional extras, only with the owner's agreement:

```bash
./scripts/setup_phone.sh app.apk --allow-settings   # brightness, text size, screen timeout
./scripts/setup_phone.sh app.apk --allow-dnd        # Do Not Disturb on/off
```

The owner can also allow those later from the app's *Extra permissions*.

<details>
<summary>What the script runs, if you would rather do it by hand</summary>

On Android 13+, a sideloaded app is **blocked from enabling an
AccessibilityService through the UI** — the toggle is greyed out as a
"Restricted setting". Setting it over ADB is the documented path around that:

```bash
PKG=com.androiduse.client
SVC="$PKG/$PKG.ControlAccessibilityService"
adb install -r -i com.android.vending app.apk
CUR=$(adb shell settings get secure enabled_accessibility_services | tr -d '\r')
[ "$CUR" = "null" ] && CUR=""
case ":$CUR:" in *":$SVC:"*) NEW="$CUR";; *) NEW="${CUR:+$CUR:}$SVC";; esac
adb shell settings put secure enabled_accessibility_services "$NEW"
adb shell settings put secure accessibility_enabled 1

# THE IMPORTANT ONE. Without this, Android silently revokes the app's
# accessibility access minutes to hours later, and every tool starts failing.
adb shell cmd appops set $PKG ACCESS_RESTRICTED_SETTINGS allow

adb shell pm grant $PKG android.permission.WRITE_SECURE_SETTINGS
adb shell pm grant $PKG android.permission.POST_NOTIFICATIONS   # Android 13+
adb shell dumpsys deviceidle whitelist +$PKG
```

Note the `case` block: it **appends** to the existing list. Overwriting it
would silently disable any accessibility service the user already relies on.
</details>

## 3. On the phone

Open **Android Use** and:
- tap **Allow help for 1 hour / 8 hours / 30 days** (control is always
  time-boxed; when it lapses, `request_control` sends the owner a one-tap
  "allow" notification)
- finish the checklist (notifications, background running, Tailscale)
- tap **Send these details to my helper** — or **Show the code on screen**

Then register the phone from Claude:

```
add_phone("mum", "100.x.y.z", "<the code>", label="Mum's phone")
```

or, for a single phone, put it in `~/.android-use/config.json`:

```json
{
  "bridge_host": "100.x.y.z",
  "bridge_port": 8765,
  "bridge_token": "<the token>"
}
```

## What the owner sees

- A notification while control is possible, with the time left and a **Stop**
  button.
- While commands arrive, a small banner — *"Your assistant is using this
  phone · Stop"* — and a ring wherever a tap lands. The banner also keeps the
  screen from locking mid-task.
- With **Ask me first** on (the default), a large *"Is that OK?"* card before
  any tap that sends, pays, deletes or calls.
- Messages and questions from the helper (`say_to_owner`, `ask_owner`), in big
  print, optionally read aloud.
- A list of what the helper did recently, on the app's main screen, and a
  picker for which apps the helper may open.

## 4. Remote access

Install Tailscale on both the phone and the computer, signed into the same
account. Use the phone's Tailscale address as `bridge_host`. Traffic is
end-to-end encrypted and nothing is exposed to the public internet.

For a same-network test without Tailscale, forward the port over ADB instead:

```bash
adb forward tcp:18765 tcp:8765   # then bridge_host 127.0.0.1, bridge_port 18765
```

## Known operational risks

**The accessibility service gets silently revoked** - root cause found.

This is not the OEM being aggressive. It is Android 13+ enforcing *restricted
settings* on sideloaded apps. Because the app has `installerPackageName=null`
and its accessibility access was granted over adb rather than through the
system's own flow, Android revokes it minutes to hours later. The giveaway:

```
$ adb shell cmd appops get com.androiduse.client ACCESS_RESTRICTED_SETTINGS
ACCESS_RESTRICTED_SETTINGS: default; rejectTime=+14m30s ago
```

A Play Store app with an accessibility service (ChatGPT, say) is unaffected,
which is why only ours kept dying.

The fix is the appop equivalent of tapping *App info > ... > Allow restricted
settings*:

```bash
adb shell cmd appops set com.androiduse.client ACCESS_RESTRICTED_SETTINGS allow
```

Verified: after setting it, the service survives the exact action that used to
kill it. Check state with:

```bash
adb shell dumpsys accessibility | grep -i androiduse
adb shell cmd appops get com.androiduse.client ACCESS_RESTRICTED_SETTINGS
```

Re-apply it after any reinstall of the APK.

**Funtouch kills background services.** The battery-optimisation whitelist above
helps, but vivo also has its own autostart control that cannot be set over ADB:
Settings → Battery → Background power consumption management → allow Android Use.

**A grant expires.** By design. After it lapses every route except `/status`
returns 403 until someone grants again on the phone.

## Remote access over Tailscale

Tailscale is not "Wi-Fi free" so much as **network-agnostic**: it builds an
encrypted tunnel over whatever the phone has — home Wi-Fi, a café, or 4G in
another city — and the phone keeps one stable `100.x` address throughout. That
is what makes helping someone in another city actually work.

### Mac without admin rights

The Homebrew cask runs a `.pkg` installer that needs sudo. If you cannot or do
not want to give it, the formula plus userspace networking needs no admin at
all:

```bash
brew install tailscale                       # no sudo
mkdir -p ~/.tailscale-userspace
/opt/homebrew/opt/tailscale/bin/tailscaled \
  --tun=userspace-networking \
  --outbound-http-proxy-listen=localhost:1055 \
  --socks5-server=localhost:1055 \
  --statedir="$HOME/.tailscale-userspace" \
  --socket="$HOME/.tailscale-userspace/tailscaled.sock" &

/opt/homebrew/opt/tailscale/bin/tailscale \
  --socket="$HOME/.tailscale-userspace/tailscaled.sock" \
  up --hostname=claude-mac --accept-routes
```

That prints a login URL to approve in a browser.

The trade-off: userspace mode gives no system route to `100.x`, so `ping` and
other ordinary tools cannot reach the tailnet — only traffic sent through the
proxy. That is fine here because the backend only makes HTTP calls; set
`bridge_proxy` and it routes through the proxy:

```json
{
  "bridge_host": "100.x.y.z",
  "bridge_port": 8765,
  "bridge_token": "...",
  "bridge_proxy": "http://localhost:1055"
}
```

With a normal (sudo) Tailscale install, leave `bridge_proxy` out entirely.

### On the phone

Tailscale needs the same battery-optimisation exemption as the client app. If
Funtouch kills Tailscale, the phone goes dark and you will not find out until
you try to help.

## Screenshots are rate limited

`takeScreenshot()` rejects a second call made too soon with
`ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT`. The app now waits out the interval
and retries once, which turns a hard failure into a short delay. Without that,
any burst of screenshots fails on the second one.


## Self-repair: why cellular now works like Wi-Fi

Android keeps revoking a sideloaded app's accessibility access. Setting
`ACCESS_RESTRICTED_SETTINGS` to `allow` helps but does **not** fully stop it -
verified the hard way: the service survived a ten-operation stress test and
then died anyway, with the appop still set to `allow`.

That was survivable on Wi-Fi (reconnect adb, re-enable) and fatal on cellular:
adb runs over the LAN, so with Wi-Fi off there was no way back in at all.

The fix is that the phone repairs itself. Two facts make it work:

1. When Android revokes the accessibility service, **the app process keeps
   running** - `/status` still answers, reporting `service_running: false`.
2. The app holds `WRITE_SECURE_SETTINGS`, granted once over adb at setup, which
   is exactly the permission needed to write `enabled_accessibility_services`.

So `POST /repair` puts the service back from inside the app, over whatever
network is available. It appends to the enabled list rather than replacing it,
so other accessibility services (ChatGPT's, say) are left alone.

This is wired in three places:

- `AppBackend._request` catches a 503 naming the accessibility service, repairs,
  and retries once - tools self-heal without anyone noticing.
- The watchdog tries the network repair *before* adb, so it works on cellular.
- `repair_phone` exposes it as an MCP tool for manual use.

Verified end to end with Wi-Fi off and adb offline:

```
adb:                   offline
service over cellular: False
repair_phone():        "Repaired - the accessibility service is running again."
get_screen():          working
```

Also install with Play attribution, which reduces how often Android revokes in
the first place:

```bash
adb install -r -i com.android.vending app-release.apk
```

### Still needs a person

A **PIN, pattern or fingerprint lock**. The app now wakes the screen by itself
and dismisses a swipe lock, and while a helper is working it keeps the screen
from locking. But a secure lock is never bypassed: the phone shows its unlock
screen, and the owner has to unlock it. Reading state, repairing and
`request_control` all work while it is locked; tapping through apps does not.
