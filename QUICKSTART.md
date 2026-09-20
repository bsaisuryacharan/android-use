# Quick start

The fastest way to try Android Use: your phone on a USB cable, driven from
Claude Code. ~2 minutes.

## 1. Install

```bash
git clone https://github.com/bsaisuryacharan/android-use && cd android-use
./install.sh
```

`install.sh` sets up the Python environment and registers the server with
Claude Code. (No `uv`? It falls back to `python3 -m venv`.)

## 2. Enable USB debugging on the phone

Settings → About phone → tap **Build number** 7 times → back → Developer options
→ turn on **USB debugging**. Plug the phone into the computer and accept the
"Allow USB debugging?" prompt.

```bash
adb devices     # should list your phone as "device"
```

## 3. Use it

Restart Claude Code, then just talk:

- *"What's on my phone screen?"*
- *"Turn off my 7am alarm"*
- *"Why isn't my internet working?"*
- *"Open YouTube and search for lo-fi"*

Claude reads the screen, acts by index, and shows you what changed after each
step.

## Going wireless / fully remote

The cable is only for the quick start. To drive the phone over Wi-Fi or over
cellular from anywhere (the real use case — helping someone in another city):

- **Wireless ADB:** `enable_wireless` / `pair_wireless` — see the tools list.
- **Companion app + Tailscale + claude.ai:** the no-cable, no-dev-options,
  survives-reboot path. See [SETUP_APP.md](SETUP_APP.md) and
  [CONNECTOR.md](CONNECTOR.md).

## Before you help someone else

Read [SECURITY.md](SECURITY.md). This is a remote-phone-control tool; the person
whose phone it is must understand what they granted and how to stop it.
