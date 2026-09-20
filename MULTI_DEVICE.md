# Running several phones

One person helping one relative needs one phone. Helping a parent *and* a
grandparent needs several — and they must never be confused with each other.
Sending "turn off the alarm" to the wrong phone is the kind of mistake that
destroys trust in the whole thing, so phones are named and names are always
resolved explicitly.

## How it looks

```json
{
  "devices": {
    "mine":    { "host": "100.x.y.z", "port": 8765, "token": "…", "label": "My phone" },
    "grandma": { "host": "100.x.y.z",     "port": 8765, "token": "…", "label": "Amma's phone" }
  },
  "default_device": "mine"
}
```

Every tool takes an optional `device`:

```
get_screen(device="grandma")
open_settings("wifi", device="grandma")
tap(3, device="grandma")
```

Omit it and the default is used. With exactly one phone configured, the name is
optional entirely.

**Ambiguity is refused, not guessed.** With several phones and no default set,
tools return the list of names instead of picking one. That is deliberate: a
wrong guess here operates someone's real phone.

## Tools

| Tool | Purpose |
|---|---|
| `list_phones` | Configured phones and whether each is reachable |
| `discover_phones` | Android devices on your tailnet, flagged with whether the client app answers |
| `add_phone` | Register one by name, host and pairing token |
| `use_phone` | Change the default |
| `forget_phone` | Remove one (the phone itself is untouched) |

`use_phone` changes the default **for everyone using this server**, so when
switching between phones prefer naming the phone per call.

## Adding a second phone

Each phone needs its own client app install. Do the one-time setup with a cable
or over wireless debugging, exactly as in `SETUP_APP.md`:

```bash
adb install -r -i com.android.vending app-release.apk
PKG=com.androiduse.client
adb shell cmd appops set $PKG ACCESS_RESTRICTED_SETTINGS allow
adb shell pm grant $PKG android.permission.WRITE_SECURE_SETTINGS
adb shell settings put secure enabled_accessibility_services "<existing>:$PKG/$PKG.ControlAccessibilityService"
adb shell settings put secure accessibility_enabled 1
```

Then on that phone: install Tailscale, sign into the same tailnet, open
**Android Use**, tap **Grant control**, and read its **pairing token**.

Finally, from Claude:

```
discover_phones()                     → find its 100.x address
add_phone("grandma", "100.x.y.z", "<token>", label="Amma's phone")
```

Each phone has its **own** token. A token is per-device, never shared — so a
leaked token compromises one phone, not all of them.

## What is shared and what is not

- **Shared:** the MCP server, the Tailscale tailnet, the connector URL.
- **Per phone:** the client app, its pairing token, its on-device allowlist,
  its grant window.

So revoking control on one phone (its **Stop** button, or a lapsed grant) has
no effect on the others.
