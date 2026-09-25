"""What this server will not do without a second, deliberate step.

Four guards live here, because they fail in different ways:

- Risky taps. A tap that sends, pays, deletes or calls cannot be taken back.
  The model has to say it means it (confirm=True), which in practice means
  checking with the user first.
- Sensitive apps. Banking and payment apps are neither read nor driven unless
  the owner has deliberately allowed them. The allowlist already stops them
  being *opened*; this stops them being operated once they are on screen some
  other way - the owner opened one, or a web page bounced there.
- Links. An app link can land in a payment app just as surely as a tap can.
- Lock-outs. Turning off Wi-Fi or mobile data can cut the only connection to a
  phone in another city, and nobody there may know how to turn it back on.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import Element

# -- risky taps ----------------------------------------------------------------

# Patterns over the element's label. Word boundaries matter: "Sender" and
# "Deleted items" are not actions, "Send" and "Delete" are.
_RISKY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("spends money", re.compile(
        r"\b(pay|pay now|payment|make payment|buy|buy now|purchase|confirm purchase|"
        r"checkout|check out|place order|order now|confirm order|proceed to pay|"
        r"transfer|send money|donate|subscribe|top up|top-up|recharge|add money|"
        r"book now|confirm booking)\b", re.I)),
    ("sends something to other people", re.compile(
        r"^(send|post|publish|forward|submit)\b|\b(send|post|publish|submit)$", re.I)),
    ("starts a phone call", re.compile(
        r"^((voice|video|audio) )?call(?! (log|logs|history|settings|forwarding|waiting|"
        r"barring|blocking|recording|screening))\b|^dial\b|\bcall now\b", re.I)),
    ("deletes or erases something", re.compile(
        r"\b(delete|remove|erase|wipe|uninstall|factory reset|reset|clear data|"
        r"clear storage|empty trash|discard)\b|^format\b", re.I)),
    ("signs out or disconnects an account or device", re.compile(
        r"\b(sign out|log out|logout|log off|deactivate|close account|unpair|unlink|"
        r"forget network|forget this network|forget device)\b|^forget$", re.I)),
]

# Common translations of the same actions. Non-Latin scripts are matched as
# plain substrings: their vowel signs are not "word" characters to Python's
# regex engine, so \b boundaries fall in the middle of words.
_RISKY_WORDS_INTL: list[tuple[str, tuple[str, ...]]] = [
    ("spends money", ("pagar", "comprar", "payer", "acheter", "bezahlen", "kaufen",
                      "भुगतान", "पे करें", "చెల్లించు", "చెల్లించండి")),
    ("sends something to other people", ("enviar", "envoyer", "senden", "भेजें", "भेजे",
                                         "పంపు", "పంపండి")),
    ("starts a phone call", ("llamar", "appeler", "anrufen", "कॉल करें", "కాల్ చేయి")),
    ("deletes or erases something", ("eliminar", "borrar", "excluir", "supprimer",
                                     "löschen", "entfernen", "हटाएं", "मिटाएं",
                                     "తొలగించు", "తొలగించండి")),
]

# Icon-only buttons often carry the verb only in their view id.
_RISKY_ID = re.compile(
    r"(^|_)(send|pay|delete|remove|call|dial|purchase|buy|checkout|uninstall|erase)(_|$)",
    re.I,
)

# Switches whose flipping can take the phone off the network.
_NETWORK_SWITCH = re.compile(
    r"\b(wi-?fi|wlan|mobile data|cellular data|data roaming|airplane|aeroplane|flight mode)\b",
    re.I,
)


def risky_reason(element: Element, extra_words: list[str] | None = None) -> str:
    """Why tapping this element deserves a second look, or "" if it does not."""
    label = (element.label or "").strip()
    if label:
        for reason, pattern in _RISKY_PATTERNS:
            if pattern.search(label):
                return reason
        low = label.lower()
        for reason, words in _RISKY_WORDS_INTL:
            if any(w.lower() in low for w in words):
                return reason
        for word in extra_words or []:
            if word and word.lower() in low:
                return f"matches '{word}' from your risky-words list"
    elif _RISKY_ID.search(element.short_id):
        return f"its id '{element.short_id}' suggests it sends, pays, deletes or calls"
    return ""


def network_switch_warning(element: Element) -> str:
    """Flipping this switch may cut the phone off. Say so before it happens."""
    if not element.checkable or not _NETWORK_SWITCH.search(element.label or ""):
        return ""
    label = element.label.lower()
    turning_on = not element.checked
    if any(w in label for w in ("airplane", "aeroplane", "flight")):
        if turning_on:
            return (
                "Turning airplane mode ON takes the phone off the network. If you are "
                "reaching this phone remotely you will lose it until someone turns "
                "airplane mode off by hand."
            )
        return ""
    if not turning_on:
        return (
            f"Turning '{element.label}' OFF may take the phone off the network. If this "
            "is the connection you reach it through, you will lose contact with it."
        )
    return ""


# -- sensitive apps ------------------------------------------------------------

# Distinctive enough to match anywhere in a package name.
_SUBSTRING_HINTS = (
    "bank", "wallet", "pay", "crypto", "binance", "coinbase", "phonepe", "paisa",
    "freecharge", "mobikwik", "venmo", "zelle", "revolut", "monzo", "wellsfargo",
    "barclays", "hsbc", "icici", "hdfc", "kotak", "zerodha", "groww", "upstox",
    "robinhood", "transferwise", "cashapp", "loan", "npci",
)
# Short, but only ever the start of a segment in practice ("upiapp", "bhimpay").
_PREFIX_HINTS = ("upi", "bhim")
# Short enough to collide inside ordinary words ("felicity", "purchase"), so
# they must be a whole segment of the package name.
_TOKEN_HINTS = {
    "upi", "sbi", "axis", "citi", "chase", "amex", "card", "cash", "bhim", "idfc",
    "cred", "trading", "stocks", "broker",
}
_LABEL_HINTS = re.compile(r"\b(bank|banking|pay|wallet|upi|loan|credit card|crypto)\b", re.I)


def looks_sensitive(package: str, label: str = "") -> bool:
    """Does this look like a banking, payment or trading app?

    A heuristic, and deliberately a generous one: a false positive costs the
    owner one allowlist entry, a false negative lets an assistant operate
    their bank.
    """
    pkg = (package or "").lower()
    if not pkg:
        return False
    if any(h in pkg for h in _SUBSTRING_HINTS):
        return True
    tokens = set(re.split(r"[._]", pkg))
    if tokens & _TOKEN_HINTS:
        return True
    if any(t.startswith(_PREFIX_HINTS) for t in tokens):
        return True
    return bool(label and _LABEL_HINTS.search(label))


# -- links -----------------------------------------------------------------------

# Schemes that can reach far past "open a page": intent: URIs can start any
# component with any extras, upi: is a payment request, file:/content: read
# local data, javascript:/data: run code in whatever handles them.
_BLOCKED_SCHEMES = {
    "intent", "upi", "javascript", "vbscript", "file", "content", "data",
    "android-app", "chrome", "about", "smsto", "mmsto", "mms",
}
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")


def check_url(raw: str) -> tuple[str, str, str]:
    """Normalise a link and decide whether it may be opened.

    Returns (url, kind, error). `kind` is "web", "dial", or "app"; `error` is
    empty when the link is allowed. Where the link finally lands is checked
    again on the phone, against the allowlist, once Android has resolved it.
    """
    url = (raw or "").strip()
    if not url:
        return "", "", "Give a link to open, e.g. https://maps.google.com/?q=pharmacy"
    m = _SCHEME_RE.match(url)
    scheme = m.group(1).lower() if m else ""
    # "maps.google.com/x" and "example.com:8080" have no real scheme - a dot
    # in the would-be scheme means it is a host name.
    if not m or "." in scheme or scheme == "localhost":
        url = "https://" + url
        scheme = "https"
    if scheme in _BLOCKED_SCHEMES:
        return "", "", (
            f"'{scheme}:' links are refused. They can start payments or reach into "
            "apps in ways a normal link cannot."
        )
    if scheme in ("http", "https"):
        if not urlsplit(url).netloc:
            return "", "", f"'{raw}' is not a complete web address."
        return url, "web", ""
    if scheme == "tel":
        # Opened with ACTION_DIAL, which fills the number in and stops there.
        return url, "dial", ""
    return url, "app", ""


# -- lock-outs -------------------------------------------------------------------

def lockout_warning(setting: str, turning_on: bool, transport: str, network: dict) -> str:
    """Would this change cut the connection this server reaches the phone by?

    `transport` is "usb", "wifi" (wireless debugging) or "app". `network`
    describes how the phone is online right now, as far as it can tell:
    {"active": "wifi"|"cellular"|"none"|"", "mobile_data": bool|None}.
    """
    active = (network or {}).get("active", "")
    data_on = (network or {}).get("mobile_data")
    if setting == "airplane_mode" and turning_on:
        if transport == "usb":
            return ""
        return (
            "Airplane mode switches off mobile data and usually Wi-Fi too. The phone "
            "will drop off the network and you will not be able to reach it again "
            "until someone turns airplane mode off by hand."
        )
    if setting == "wifi" and not turning_on:
        if transport == "wifi":
            return (
                "You are connected to this phone over Wi-Fi (wireless debugging). "
                "Turning Wi-Fi off cuts that connection immediately."
            )
        if transport == "app" and active == "wifi" and data_on is not True:
            return (
                "The phone is online only through Wi-Fi right now (mobile data is off "
                "or unknown), so turning Wi-Fi off can take it out of reach."
            )
    if setting == "mobile_data" and not turning_on:
        if transport == "app" and active == "cellular":
            return (
                "The phone is online through mobile data right now. Turning it off "
                "takes the phone offline and out of reach."
            )
    return ""
