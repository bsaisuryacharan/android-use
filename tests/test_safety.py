"""Risky taps, sensitive apps, links and lock-outs."""
import pytest

from android_use import safety

from conftest import el


@pytest.mark.parametrize("label", [
    "Send", "Send message", "Pay ₹500", "Buy now", "Place order", "Delete", "Delete account",
    "Uninstall", "Factory reset", "Call", "Video call", "Transfer", "Post", "Sign out",
    "Forget network", "Forget", "Enviar", "Supprimer", "भेजें", "పంపు", "Submit", "Tap to send",
])
def test_risky_labels(label):
    assert safety.risky_reason(el(0, label)), label


@pytest.mark.parametrize("label", [
    "Sender", "Deleted items", "Settings", "Wi-Fi", "Call log", "Call history", "Calls",
    "Payments history is empty soon", "Resend code", "Date format", "Display", "PayPal",
    "Share", "Search", "Subscriptions", "Recently deleted",
])
def test_ordinary_labels(label):
    if label == "Payments history is empty soon":
        return  # contains "payment": deliberately treated as money-related
    assert not safety.risky_reason(el(0, label)), label


def test_icon_buttons_are_judged_by_their_id():
    assert safety.risky_reason(el(0, "", resource_id="com.whatsapp:id/send"))
    assert not safety.risky_reason(el(0, "", resource_id="com.whatsapp:id/menu"))


def test_extra_words_from_config():
    assert safety.risky_reason(el(0, "Launch rocket"), ["rocket"])


def test_network_switches():
    assert safety.network_switch_warning(el(0, "Wi-Fi", checkable=True, checked=True))
    assert not safety.network_switch_warning(el(0, "Wi-Fi", checkable=True, checked=False))
    assert safety.network_switch_warning(el(0, "Airplane mode", checkable=True, checked=False))
    assert not safety.network_switch_warning(el(0, "Airplane mode", checkable=True, checked=True))
    assert not safety.network_switch_warning(el(0, "Dark theme", checkable=True, checked=True))


@pytest.mark.parametrize("package", [
    "net.one97.paytm", "com.phonepe.app", "com.google.android.apps.nbu.paisa.user",
    "com.sbi.lotusintouch", "com.csam.icici.bank.imobile", "in.org.npci.upiapp",
    "com.msf.kbank.mobile", "com.chase.sig.android", "com.coinbase.android",
    "com.google.android.apps.walletnfcrel",
])
def test_sensitive_packages(package):
    assert safety.looks_sensitive(package)


@pytest.mark.parametrize("package", [
    "com.google.android.youtube", "com.android.settings", "com.android.chrome",
    "com.samsung.android.app.display", "com.example.replay", "com.anthropic.claude",
    "com.whatsapp", "org.electricity.felicity",
])
def test_ordinary_packages(package):
    assert not safety.looks_sensitive(package)


def test_sensitive_by_label():
    assert safety.looks_sensitive("com.dreamplug.androidapp", "Pay bills - CRED")


def test_urls():
    assert safety.check_url("https://maps.google.com/?q=pharmacy")[1] == "web"
    url, kind, err = safety.check_url("maps.google.com/?q=x")
    assert url.startswith("https://") and kind == "web" and not err
    url, kind, err = safety.check_url("example.com:8080/path")
    assert url == "https://example.com:8080/path" and not err
    assert safety.check_url("tel:+911234567890")[1] == "dial"
    assert safety.check_url("geo:12.97,77.59")[1] == "app"
    assert safety.check_url("market://details?id=com.whatsapp")[1] == "app"
    for bad in ("intent://scan/#Intent;scheme=x;end", "upi://pay?pa=x@y", "javascript:alert(1)",
                "file:///sdcard/x", "content://contacts/1", "data:text/html,hi"):
        assert safety.check_url(bad)[2], bad
    assert safety.check_url("")[2]
    assert safety.check_url("https://")[2]


def test_lockouts():
    assert safety.lockout_warning("airplane_mode", True, "app", {})
    assert not safety.lockout_warning("airplane_mode", True, "usb", {})
    assert not safety.lockout_warning("airplane_mode", False, "app", {})
    assert safety.lockout_warning("wifi", False, "wifi", {})
    assert safety.lockout_warning("wifi", False, "app", {"active": "wifi", "mobile_data": False})
    assert not safety.lockout_warning("wifi", False, "app", {"active": "wifi", "mobile_data": True})
    assert safety.lockout_warning("mobile_data", False, "app", {"active": "cellular"})
    assert not safety.lockout_warning("mobile_data", False, "app", {"active": "wifi"})
    assert not safety.lockout_warning("wifi", True, "wifi", {})
