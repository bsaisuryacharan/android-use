"""android-use: an MCP server that lets Claude see and operate a real Android phone.

The loop is deliberately simple: read the screen, act on an element by index,
read the screen again. Every action returns the resulting screen, so the model
always knows what its last tap actually did.

Tools talk only to the Backend interface, so the transport (ADB over USB, ADB
over Wi-Fi, or the on-device client app) is swappable underneath them.
"""
from __future__ import annotations

import io
import time

from mcp.server.mcpserver import Image, MCPServer

from . import apps, chrome, wireless
from .adb_backend import backend_for, get_backend
from .backend import BackendError

INSTRUCTIONS = """
Controls a real Android phone, for people who find their phone hard to operate
(older users, anyone unsure where a setting lives).

How to work:
1. Call get_screen first. It lists the tappable elements on screen, numbered.
2. Act by index: tap(3). Never guess pixel coordinates - use the index.
3. Every action returns the new screen, so check it before acting again.
4. If get_screen shows nothing useful (a video, a game, a custom view), call
   take_screenshot and look at the phone directly.
5. To change a setting, prefer open_settings('wifi') over navigating the
   Settings app by hand - it jumps straight to the right page.
6. check_internet() diagnoses connectivity without any tapping - start there
   for "my internet is broken".
7. The phone may be connected by cable or over Wi-Fi; this is handled for you.
   If nothing is reachable, check_device explains how to fix it.

Several phones can be registered. When more than one is, ALWAYS pass `device`
with the phone's name - call list_phones to see them. Acting on the wrong
person's phone is the worst mistake this tool can make, so never rely on the
default when there is more than one phone. Every result says which phone it
touched; check it.

The user may be touching the phone at the same time, so the screen can change
underneath you. Re-read it if anything looks stale.

This is someone's real phone. Do not tap anything that sends a message, makes a
payment, makes a call, or deletes data unless the user asked for exactly that.
Explain what you are about to do before doing it.
""".strip()

mcp = MCPServer(name="android-use", version="0.2.0", instructions=INSTRUCTIONS)

_SETTLE = 1.0  # seconds to let the UI animate before re-reading it


def _which(device: str = "") -> str:
    """Name the phone in output whenever more than one is configured.

    With several phones registered, a silent default is how "turn off the
    alarm" ends up on the wrong person's phone. Saying which one was touched
    makes that mistake visible immediately instead of hours later.
    """
    from . import devices

    phones = devices.load_phones()
    if len(phones) < 2:
        return ""
    phone, _ = devices.resolve(device)
    if phone is None:
        return ""
    if devices.locked_name():
        return f"[on '{phone.name}' - {phone.display}]  (LOCKED to this phone)\n"
    suffix = " (default - name the phone explicitly to be sure)" if not device else ""
    return f"[on '{phone.name}' - {phone.display}]{suffix}\n"


def _screen_after(note: str, settle: float = _SETTLE, device: str = "") -> str:
    """Report what we did, then what the screen looks like now."""
    note = _which(device) + note
    time.sleep(settle)
    backend, err = backend_for(device)
    if backend is None:
        return f"{note}\n\n(Could not re-read the screen: {err})"
    try:
        return f"{note}\n\n{backend.get_screen().render()}"
    except BackendError as exc:
        return f"{note}\n\n(Could not re-read the screen: {exc})"


def _ready(device: str = ""):
    """Resolve a phone by name and confirm it is usable.

    Returns (backend, None) or (None, error string). The name is always
    resolved explicitly: with several phones configured, guessing which one
    the user meant is the one mistake that must never happen.
    """
    backend, err = backend_for(device)
    if backend is None:
        return None, err
    try:
        backend.require_ready()
    except BackendError as exc:
        return None, f"Phone not reachable.\n\n{exc}"
    return backend, None


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
@mcp.tool()
def check_device(device: str = "") -> str:
    """Check that a phone is reachable and report what it is. Call this first
    if anything seems wrong. Connects over Wi-Fi automatically if it can."""
    backend, err = _ready(device)
    if err:
        return err
    return _which(device) + f"{backend.status().render()}\n{backend.device_info()}"


@mcp.tool()
def list_devices(device: str = "") -> str:
    """Find phones that could be added: devices on your tailnet running the
    client app. This is the normal way to discover a phone.

    ADB is only relevant while setting a phone up from this machine; use
    list_adb_devices if you specifically need that."""
    from . import devices as registry

    found = registry.discover()
    known = registry.load_phones()
    lines = []
    if known:
        lines.append(f"Already registered ({len(known)}):")
        lines += [f"  {n} - {p.display}  {p.host}:{p.port}" for n, p in sorted(known.items())]
        lines.append("")

    if not found:
        lines.append(
            "No Android devices found on your tailnet. The phone needs Tailscale "
            "installed and signed into the same account."
        )
        return "\n".join(lines)

    lines.append(f"On your tailnet ({len(found)}):")
    for e in found:
        bits = ["online" if e["online"] else "offline"]
        if e["running_app"]:
            bits.append("client app answering")
        elif e["online"]:
            bits.append("no client app on port 8765")
        if e["already_configured"]:
            bits.append("already registered")
        lines.append(f"  {e['hostname'] or '(unnamed)':26} {e['ip']:16} {', '.join(bits)}")
    lines.append("")
    lines.append(
        "To register one: open Android Use on that phone, tap 'Show the code on "
        "screen', then call add_phone(name, host, token)."
    )
    return "\n".join(lines)


@mcp.tool()
def list_adb_devices(device: str = "") -> str:
    """Phones reachable over ADB from this machine - cable or wireless debugging.

    Only useful while setting a phone up locally. Day-to-day the phones are
    reached over the network, not ADB."""
    from . import adb

    lines = ["Attached to adb:"]
    try:
        attached = adb.list_devices()
    except adb.AdbError as exc:
        return f"ADB is not usable here: {exc}"
    if attached:
        for d in attached:
            how = "wifi" if wireless.is_wireless(d.serial) else "usb"
            lines.append(f"  {d.serial:26} {d.state:14} ({how}) {d.model}")
    else:
        lines.append("  (none)")

    found = [d for d in wireless.discover() if not d.is_pairing]
    lines.append("\nAdvertising wireless debugging on this network:")
    lines += [f"  {d.serial or '(unknown)':26} {d.address}" for d in found] or ["  (none)"]
    return "\n".join(lines)


@mcp.tool()
def pair_wireless(address: str, code: str, device: str = "") -> str:
    """Pair with a phone over Wi-Fi, once, so no cable is needed afterwards.

    On the phone: Settings > Developer options > Wireless debugging > 'Pair
    device with pairing code'. Pass the ip:port and the 6-digit code it shows.
    Note that the pairing port differs from the connection port."""
    result = wireless.pair(address.strip(), code.strip())
    if result.startswith("Pairing failed"):
        return result
    ok, msg = wireless.ensure_connected()
    return f"{result}\n{msg if ok else 'Paired, but could not connect yet: ' + msg}"


@mcp.tool()
def enable_wireless(device: str = "") -> str:
    """Switch a cabled phone over to Wi-Fi so the cable can be unplugged.

    Needs the phone plugged in for this one step, but no pairing code. Use this
    when the user wants to stop using the cable. It does not survive a phone
    reboot - pair_wireless does."""
    ok, msg = wireless.enable_via_usb()
    if not ok:
        return f"Could not enable wireless.\n\n{msg}"
    return f"{msg}\n\n{get_backend().status().render()}"


@mcp.tool()
def connect_wireless(device: str = "") -> str:
    """Connect to an already-paired phone over Wi-Fi. Use after the phone
    reboots or wireless debugging is toggled - the port changes each time, so
    this rediscovers it."""
    ok, msg = wireless.ensure_connected()
    if not ok:
        return f"Could not connect wirelessly.\n\n{msg}"
    return f"{msg}\n{get_backend().status().render()}"


@mcp.tool()
def disconnect_wireless(device: str = "") -> str:
    """Drop the wireless connection. The pairing is remembered, so
    connect_wireless will reconnect without pairing again."""
    return wireless.disconnect()


# --------------------------------------------------------------------------
# Perception
# --------------------------------------------------------------------------
@mcp.tool()
def get_screen(device: str = "") -> str:
    """Read what is currently on the phone screen, as a numbered list of
    tappable elements. This is the main way to see the phone - call it before
    acting, and after anything unexpected."""
    backend, err = _ready(device)
    if err:
        return err
    try:
        return _which(device) + backend.get_screen().render()
    except BackendError as exc:
        return f"Could not read the screen: {exc}\nTry wake_screen() first."


@mcp.tool()
def take_screenshot(max_width: int = 800, device: str = "") -> Image:
    """Take a picture of the phone screen. Use this when get_screen doesn't
    show what you need - videos, games, photos, or custom-drawn interfaces."""
    backend, err = backend_for(device)
    if backend is None:
        raise ValueError(err)
    backend.require_ready()
    raw = backend.screenshot()
    try:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(raw))
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), PILImage.LANCZOS)
        buf = io.BytesIO()
        # JPEG, not PNG: phone screens carry photo wallpaper and thumbnails, and
        # PNG encodes those ~10x larger for no gain in readability.
        img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
        return Image(data=buf.getvalue(), format="jpeg")
    except Exception:
        return Image(data=raw, format="png")  # send the original rather than fail


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
@mcp.tool()
def tap(index: int, device: str = "") -> str:
    """Tap an element by its number from get_screen. This is the normal way to
    tap - the index comes straight from the last get_screen listing."""
    backend, err = _ready(device)
    if err:
        return err
    screen = backend.get_screen()
    match = [e for e in screen.elements if e.index == index]
    if not match:
        return f"No element [{index}] on screen; it may have changed.\n\n{screen.render()}"
    element = match[0]
    if not element.enabled:
        return f"Element [{index}] '{element.label}' is disabled and cannot be tapped."
    backend.tap_element(element)
    return _screen_after(f"Tapped [{index}] '{element.label}'", device=device)


@mcp.tool()
def tap_text(text: str, device: str = "") -> str:
    """Tap the element whose label matches this text. Use when you know what
    the button says but not its index."""
    backend, err = _ready(device)
    if err:
        return err
    screen = backend.get_screen()
    found = screen.find(text)
    if not found:
        return f"Nothing on screen matches '{text}'.\n\n{screen.render()}"
    if len(found) > 1:
        listing = "\n".join(f"  {e.render()}" for e in found)
        return f"'{text}' matches {len(found)} elements - tap by index instead:\n{listing}"
    backend.tap_element(found[0])
    return _screen_after(f"Tapped '{found[0].label}'", device=device)


@mcp.tool()
def tap_coordinates(x: int, y: int, device: str = "") -> str:
    """Tap an exact pixel position. Only use this when an element has no index -
    for example something you found in a screenshot."""
    backend, err = _ready(device)
    if err:
        return err
    backend.tap_xy(x, y)
    return _screen_after(f"Tapped ({x}, {y})", device=device)


@mcp.tool()
def scroll(direction: str = "down", device: str = "") -> str:
    """Scroll the screen: 'down' to see what is further down the page, 'up' to
    go back, or 'left'/'right' to move between pages."""
    backend, err = _ready(device)
    if err:
        return err
    try:
        backend.scroll(direction.lower().strip())
    except BackendError as exc:
        return str(exc)
    return _screen_after(f"Scrolled {direction}", device=device)


@mcp.tool()
def type_text(text: str, device: str = "") -> str:
    """Type text into the field that is currently focused. Tap a text field
    first."""
    backend, err = _ready(device)
    if err:
        return err
    if not text.isascii() and not backend.supports_unicode_text():
        return (
            "This connection can only type plain ASCII - emoji and non-Latin "
            "scripts will not go through. Ask the user to type those by hand."
        )
    backend.type_text(text)
    return _screen_after(f"Typed: {text}", device=device)


@mcp.tool()
def press_key(key: str, device: str = "") -> str:
    """Press a hardware or system key. Common ones: back, home, recents, enter,
    delete, volume_up, volume_down."""
    backend, err = _ready(device)
    if err:
        return err
    try:
        backend.press(key)
    except BackendError as exc:
        return f"{exc}\nKnown keys: {', '.join(backend.known_keys())}"
    return _screen_after(f"Pressed {key}", device=device)


@mcp.tool()
def wake_screen(device: str = "") -> str:
    """Wake the phone and dismiss a simple swipe lock. A PIN, pattern or
    fingerprint lock cannot be bypassed - the user must unlock it themselves."""
    backend, err = _ready(device)
    if err:
        return err
    status = backend.wake_and_unlock()
    if status.startswith("locked"):
        return f"{status}. Ask the user to unlock the phone, then continue."
    return _screen_after(status)


# --------------------------------------------------------------------------
# Apps and settings
# --------------------------------------------------------------------------
@mcp.tool()
def list_apps(device: str = "") -> str:
    """List the apps installed on the phone and which ones this server is
    allowed to open."""
    backend, err = _ready(device)
    if err:
        return err
    return apps.describe_apps(backend.installed_packages())


@mcp.tool()
def open_app(name: str, device: str = "") -> str:
    """Open an app by name, e.g. 'youtube' or 'chrome'. Only apps on the
    allowlist can be opened - call list_apps to see them."""
    backend, err = _ready(device)
    if err:
        return err
    installed = backend.installed_packages()
    matches = apps.resolve(name, installed)
    if not matches:
        return f"No installed app matches '{name}'. Call list_apps to see what is available."
    if len(matches) > 1:
        listing = "\n".join(f"  {apps.friendly_label(p)} ({p})" for p in matches)
        return f"'{name}' matches several apps - be more specific:\n{listing}"

    package = matches[0]
    if not apps.is_allowed(package):
        return (
            f"'{apps.friendly_label(package)}' ({package}) is not on the allowlist, "
            "so this server will not open it. This guard exists because the phone "
            "also has banking and payment apps on it.\n"
            f"To allow it, add the package to {apps.CONFIG_PATH}."
        )
    backend.launch_package(package)
    return _screen_after(f"Opened {apps.friendly_label(package)}", settle=1.8, device=device)


@mcp.tool()
def open_settings(page: str, device: str = "") -> str:
    """Jump straight to a Settings page instead of navigating there by hand.
    Pages: wifi, mobile_data, network, airplane_mode, hotspot, bluetooth,
    display, sound, battery, storage, apps, location, security, accessibility,
    date_time, language, privacy, notifications, about_phone, settings_home."""
    backend, err = _ready(device)
    if err:
        return err
    try:
        backend.open_settings_page(page)
    except BackendError as exc:
        return f"{exc}\nAvailable pages: {', '.join(backend.settings_pages())}"
    return _screen_after(f"Opened settings: {page}", settle=1.5, device=device)


@mcp.tool()
def check_internet(device: str = "") -> str:
    """Diagnose the phone's internet connection without touching the screen.
    Start here for 'my internet isn't working' - it shows whether the radios are
    on, whether traffic flows, and whether DNS resolves."""
    backend, err = _ready(device)
    if err:
        return err
    return backend.connectivity_report()


# --------------------------------------------------------------------------
# Chrome on the phone, via the DevTools protocol
# --------------------------------------------------------------------------
def _chrome_ready(device: str = "") -> str | None:
    backend, err = _ready(device)
    if err:
        return err
    try:
        chrome.ensure_ready(device)
    except (chrome.ChromeError, Exception) as exc:
        return f"Chrome is not reachable.\n\n{exc}"
    return None


def _page(tab_id: str = "", device: str = ""):
    return chrome.page_session(tab_id or None)


@mcp.tool()
def chrome_tabs(device: str = "") -> str:
    """List the tabs open in Chrome on the phone. Use the ids with the other
    chrome_ tools; most of them default to the most recent tab."""
    if err := _chrome_ready(device):
        return err
    try:
        found = chrome.tabs()
    except chrome.ChromeError as exc:
        return str(exc)
    if not found:
        return "Chrome has no open tabs."
    lines = [f"{len(found)} tab(s) open:"]
    for t in found:
        lines.append(f"  {t['id']}  {t.get('title','')[:60]!r}\n      {t.get('url','')[:100]}")
    return "\n".join(lines)


@mcp.tool()
def chrome_read(tab_id: str = "", device: str = "") -> str:
    """Read a page in Chrome as structured content: its text plus a numbered
    list of links, buttons and fields. This is the web equivalent of
    get_screen, and far more reliable than reading the page off a screenshot."""
    if err := _chrome_ready(device):
        return err
    try:
        session, tab = _page(tab_id, device)
        with session:
            return chrome.render_page(chrome.read_page(session))
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def chrome_open(url: str, device: str = "") -> str:
    """Open a URL in a new Chrome tab on the phone."""
    if err := _chrome_ready(device):
        return err
    try:
        target = chrome.open_tab(url)
        time.sleep(2.0)
        session, tab = _page(target)
        with session:
            return f"Opened {url}\n\n{chrome.render_page(chrome.read_page(session))}"
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def chrome_navigate(url: str, tab_id: str = "", device: str = "") -> str:
    """Point an existing Chrome tab at a different URL."""
    if err := _chrome_ready(device):
        return err
    try:
        session, tab = _page(tab_id, device)
        with session:
            session.call("Page.navigate", {"url": url})
            time.sleep(2.5)
            return f"Navigated to {url}\n\n{chrome.render_page(chrome.read_page(session))}"
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def chrome_click(index: int, tab_id: str = "", device: str = "") -> str:
    """Click an element by its number from chrome_read."""
    if err := _chrome_ready(device):
        return err
    try:
        session, tab = _page(tab_id, device)
        with session:
            # Act on the element the index was stamped onto, not on whatever
            # sits at that position now - re-reading would renumber the page.
            if not chrome.stamped(session):
                chrome.read_page(session)
            label = chrome.act_on_index(session, index, "click")
            if label is None:
                page = chrome.read_page(session)
                return (
                    f"Element [{index}] is gone - the page changed. "
                    f"Here it is again:\n\n{chrome.render_page(page)}"
                )
            time.sleep(1.8)
            return (
                f"Clicked [{index}] '{label}'\n\n"
                f"{chrome.render_page(chrome.read_page(session))}"
            )
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def chrome_type(text: str, index: int = -1, tab_id: str = "", device: str = "") -> str:
    """Type into a field in Chrome. Give the field's number from chrome_read,
    or omit it to type into whatever is already focused.

    Unlike the phone's own keyboard, this handles any language and emoji."""
    if err := _chrome_ready(device):
        return err
    try:
        session, tab = _page(tab_id, device)
        with session:
            if index >= 0:
                if not chrome.stamped(session):
                    chrome.read_page(session)
                label = chrome.act_on_index(session, index, "focus")
                if label is None:
                    page = chrome.read_page(session)
                    return (
                        f"Field [{index}] is gone - the page changed. "
                        f"Here it is again:\n\n{chrome.render_page(page)}"
                    )
            session.call("Input.insertText", {"text": text})
            time.sleep(0.5)
            # Report the field's actual contents: typing can land somewhere
            # unexpected, and saying "typed X" proves nothing on its own.
            landed = session.evaluate(
                "(function(){var e=document.activeElement;"
                "return e?((e.value!==undefined?e.value:e.innerText)||''):''})()"
            )
            return (
                f"Typed into the page: {text}\n"
                f"Field now contains: {landed!r}\n\n"
                f"{chrome.render_page(chrome.read_page(session))}"
            )
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def chrome_javascript(expression: str, tab_id: str = "", device: str = "") -> str:
    """Run JavaScript in a Chrome tab and return the result. Use for anything
    the other chrome_ tools do not cover - extracting data, submitting a form,
    scrolling to an element."""
    if err := _chrome_ready(device):
        return err
    try:
        session, tab = _page(tab_id, device)
        with session:
            return f"Result: {session.evaluate(expression)!r}"
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def chrome_close_tab(tab_id: str, device: str = "") -> str:
    """Close a Chrome tab by its id from chrome_tabs."""
    if err := _chrome_ready(device):
        return err
    try:
        chrome.close_tab(tab_id)
        return f"Closed tab {tab_id}."
    except chrome.ChromeError as exc:
        return str(exc)


@mcp.tool()
def phone_health(device: str = "") -> str:
    """Check whether the phone is reachable and why not, if not.

    Worth calling first when something stops working: the accessibility service
    can be switched off by the system without warning, and the failure looks
    like a network problem until you look."""
    from .adb_backend import reset_backend

    reset_backend()  # re-select, in case the app came back or went away
    backend = get_backend()
    state = backend.status()
    lines = [f"Transport: {backend.name}", state.render(), ""]

    if state.connected:
        lines.append("Everything is working.")
        return "\n".join(lines)

    detail = state.detail.lower()
    if "accessibility" in detail:
        lines += [
            "The phone app is installed but its accessibility service is off.",
            "This happens when the system kills the app - a known behaviour on",
            "vivo/Funtouch. It cannot be fixed remotely; it needs either:",
            "  - the phone's owner to re-enable Android Use in",
            "    Settings > Accessibility, or",
            "  - a cable/ADB connection to run the setup command again.",
            "To stop it recurring, allow auto-start for Android Use in",
            "i Manager / Settings > Battery > Background power management.",
        ]
    elif "not granted" in detail:
        lines += [
            "The phone is reachable but control has expired or been stopped.",
            "Ask the phone's owner to open Android Use and grant control again.",
        ]
    else:
        lines += [
            "Could not reach the phone at all. Check that it is powered on and",
            "online, and that Tailscale is connected on both this computer and",
            "the phone.",
        ]
    return "\n".join(lines)


@mcp.tool()
def watchdog_report() -> str:
    """Show what the background watchdog last saw, including when the phone
    went down and whether a repair was attempted. Use this to answer 'has the
    phone been reachable?' without poking the phone right now."""
    from .watchdog import last_report

    return last_report()


@mcp.tool()
def repair_phone(device: str = "") -> str:
    """Put the phone's accessibility service back when it has been switched off.

    Android periodically revokes accessibility access from apps it did not
    install. The phone can re-enable its own service, so this works over any
    network including cellular - no cable, and nobody has to touch the phone."""
    from .adb_backend import reset_backend
    from .app_backend import AppBackend

    reset_backend()
    backend, err = backend_for(device)
    if backend is None:
        return err
    if not isinstance(backend, AppBackend):
        return (
            "Repair only applies to the phone app transport. This connection is "
            f"using '{backend.name}', which does not need it."
        )
    try:
        result = backend.repair()
    except BackendError as exc:
        return f"Could not repair the phone.\n\n{exc}"
    if result.get("service_running"):
        return "Repaired - the accessibility service is running again."
    return (
        "The repair ran but the service did not come back. Someone may need to "
        "enable Android Use under Settings > Accessibility on the phone."
    )


# --------------------------------------------------------------------------
# Managing several phones
# --------------------------------------------------------------------------
@mcp.tool()
def list_phones() -> str:
    """List the phones this server can drive, and whether each is reachable.

    Every other tool takes an optional `device` naming one of these. With more
    than one phone configured, always name the one you mean."""
    from . import devices

    phones = devices.load_phones()
    if not phones:
        return (
            "No phones configured yet. Use discover_phones to find one on your "
            "tailnet, then add_phone to register it."
        )
    default = devices.default_name()
    pinned = devices.locked_name()
    lines = [f"{len(phones)} phone(s) configured:", ""]
    if pinned:
        lines = [
            f"LOCKED to '{pinned}' - every tool acts on this phone only "
            "(call unlock_phone to change).",
            "",
            f"{len(phones)} phone(s) configured:",
            "",
        ]
    for name, phone in sorted(phones.items()):
        backend, err = backend_for(name)
        if backend is None:
            state = err
        else:
            status = backend.status()
            state = "reachable" if status.connected else f"unreachable - {status.detail[:70]}"
        marker = "  (default)" if name == default else ""
        label = f" - {phone.label}" if phone.label else ""
        lines.append(f"  {name}{label}{marker}")
        lines.append(f"      {phone.host}:{phone.port}  |  {state}")
    lines.append("")
    lines.append("Use a phone by name, e.g. get_screen(device=\"" + next(iter(sorted(phones))) + "\").")
    return "\n".join(lines)


@mcp.tool()
def use_phone(name: str) -> str:
    """Set which phone the other tools act on when no device is named.

    This changes the default for everyone using this server, so prefer naming
    the phone per call when you are switching between them."""
    from . import devices

    phones = devices.load_phones()
    if name not in phones:
        return f"No phone called '{name}'. Configured: {', '.join(sorted(phones))}"
    devices.set_default(name)
    from .adb_backend import reset_backend

    reset_backend()
    return f"Default phone is now '{name}' ({phones[name].display})."


@mcp.tool()
def discover_phones() -> str:
    """Look for phones on your tailnet that are running the client app.

    Finds Android devices on the tailnet and checks whether each answers on the
    bridge port. A phone found here still needs its pairing token before it can
    be driven - read that from the Android Use app on the phone itself."""
    from . import devices

    found = devices.discover()
    if not found:
        return (
            "No Android devices found on your tailnet. Check that the phone has "
            "Tailscale installed and signed into the same account."
        )
    lines = [f"{len(found)} Android device(s) on the tailnet:", ""]
    for entry in found:
        bits = []
        bits.append("online" if entry["online"] else "offline")
        if entry["running_app"]:
            bits.append("client app answering")
        elif entry["online"]:
            bits.append("no client app on port 8765")
        if entry["already_configured"]:
            bits.append("already configured")
        lines.append(f"  {entry['hostname'] or '(unnamed)'}  {entry['ip']}")
        lines.append(f"      {', '.join(bits)}")
    lines.append("")
    lines.append(
        "To add one: open Android Use on that phone, tap 'Show pairing token', "
        "then call add_phone(name, host, token)."
    )
    return "\n".join(lines)


@mcp.tool()
def add_phone(name: str, host: str, token: str, label: str = "", port: int = 8765) -> str:
    """Register a phone so it can be driven by name.

    `host` is its Tailscale address (see discover_phones) and `token` is the
    pairing token shown in the Android Use app on that phone."""
    from . import devices

    if not name.strip():
        return "Give the phone a short name, e.g. 'grandma'."
    devices.add_phone(name.strip(), host.strip(), token.strip(), port, label.strip())
    from .adb_backend import reset_backend

    reset_backend()
    backend, err = backend_for(name.strip())
    if backend is None:
        return f"Saved '{name}', but it could not be resolved: {err}"
    status = backend.status()
    verdict = "reachable" if status.connected else f"not reachable yet - {status.detail[:90]}"
    return f"Added '{name}' at {host}:{port}. It is {verdict}."


@mcp.tool()
def forget_phone(name: str) -> str:
    """Remove a phone from the registry. The phone itself is left untouched."""
    from . import devices
    from .adb_backend import reset_backend

    if devices.remove_phone(name):
        reset_backend()
        return f"Removed '{name}'."
    return f"No phone called '{name}'."


@mcp.tool()
def lock_phone(name: str) -> str:
    """Work on one phone only, until unlocked.

    Every tool then acts on this phone, and a request naming a different one is
    refused rather than redirected. Use this when helping one person, so a
    misread instruction cannot reach somebody else's phone."""
    from . import devices
    from .adb_backend import reset_backend

    phone, err = devices.resolve(name)
    if phone is None:
        devices.unlock()
        phone, err = devices.resolve(name)
        if phone is None:
            return err
    devices.lock(phone.name)
    reset_backend()
    return (
        f"Locked to '{phone.name}' ({phone.display}).\n"
        "Every tool will act on this phone only. Requests naming another phone "
        "will be refused until you call unlock_phone."
    )


@mcp.tool()
def unlock_phone() -> str:
    """Stop working on just one phone, so all registered phones are usable again."""
    from . import devices
    from .adb_backend import reset_backend

    was = devices.locked_name()
    devices.unlock()
    reset_backend()
    if not was:
        return "No phone was locked; nothing changed."
    return f"Unlocked. '{was}' is no longer the only phone this server will act on."


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
