"""Drive Chrome on the phone through the Chrome DevTools Protocol.

Chrome for Android exposes a DevTools socket (`@chrome_devtools_remote`) that
adb can forward to a local port. That gives the same protocol Chrome DevTools
and claude-in-chrome use, so web pages are handled as a DOM rather than as
pixels: real selectors, real text, real navigation.

It also sidesteps the ADB text limitation - `Input.insertText` types any script
or emoji, which `adb shell input text` cannot.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request

from . import adb

CHROME_PACKAGE = "com.android.chrome"
SOCKET = "localabstract:chrome_devtools_remote"
LOCAL_PORT = 9222
_BASE = f"http://localhost:{LOCAL_PORT}"


class ChromeError(RuntimeError):
    pass


def _http(path: str, timeout: int = 8):
    try:
        with urllib.request.urlopen(f"{_BASE}{path}", timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ChromeError(f"DevTools endpoint not responding: {exc}") from exc


def _socket_exists() -> bool:
    try:
        out = adb.shell("cat /proc/net/unix | grep chrome_devtools_remote")
        return "chrome_devtools_remote" in out
    except adb.AdbError:
        return False


def _forward() -> None:
    cmd = [adb._require_adb()]
    if adb.SERIAL:
        cmd += ["-s", adb.SERIAL]
    proc = subprocess.run(
        cmd + ["forward", f"tcp:{LOCAL_PORT}", SOCKET],
        capture_output=True, text=True, timeout=20,
    )
    if proc.returncode != 0:
        raise ChromeError(f"Could not forward the DevTools port: {proc.stderr.strip()}")


def ensure_ready(restart_if_needed: bool = True) -> str:
    """Make Chrome's DevTools reachable, restarting Chrome if it has no socket.

    Chrome only opens the socket when it starts with debugging available, so a
    Chrome that was already running before adb connected will not have one.
    """
    if not _socket_exists():
        if not restart_if_needed:
            raise ChromeError("Chrome is not exposing a DevTools socket.")
        adb.shell(f"am force-stop {CHROME_PACKAGE}")
        time.sleep(1.0)
        adb.shell(
            f"monkey -p {CHROME_PACKAGE} -c android.intent.category.LAUNCHER 1"
            " >/dev/null 2>&1"
        )
        for _ in range(10):
            time.sleep(1.0)
            if _socket_exists():
                break
        else:
            raise ChromeError(
                "Chrome did not open a DevTools socket. Check that Chrome is "
                "installed and that USB debugging is on."
            )
    _forward()
    version = _http("/json/version")
    return version.get("Browser", "Chrome")


class Session:
    """One CDP websocket. Used as a context manager so sockets always close."""

    def __init__(self, ws_url: str):
        import websocket  # imported lazily so the rest of the server works without it

        # Chrome rejects a websocket carrying an Origin it did not expect.
        self.ws = websocket.create_connection(ws_url, timeout=20, suppress_origin=True)
        self._id = 0

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        self._id += 1
        want = self._id
        self.ws.send(json.dumps({"id": want, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == want:
                if "error" in msg:
                    raise ChromeError(msg["error"].get("message", "CDP error"))
                return msg.get("result", {})
        raise ChromeError(f"{method} timed out")

    def evaluate(self, expression: str):
        """Run JS in the page and return its value, raising on a page error."""
        res = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in res:
            detail = res["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise ChromeError(f"JavaScript error: {text}")
        return res.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def tabs() -> list[dict]:
    return [t for t in _http("/json/list") if t.get("type") == "page"]


def browser_session() -> Session:
    return Session(_http("/json/version")["webSocketDebuggerUrl"])


def page_session(target_id: str | None = None) -> tuple[Session, dict]:
    """Attach to a tab - the given one, or the most recently active."""
    open_tabs = tabs()
    if not open_tabs:
        raise ChromeError("Chrome has no open tabs.")
    if target_id:
        match = [t for t in open_tabs if t.get("id") == target_id]
        if not match:
            raise ChromeError(f"No tab with id {target_id}.")
        tab = match[0]
    else:
        tab = open_tabs[0]
    if not tab.get("webSocketDebuggerUrl"):
        raise ChromeError(f"Tab '{tab.get('title')}' cannot be attached to.")
    return Session(tab["webSocketDebuggerUrl"]), tab


def open_tab(url: str) -> str:
    with browser_session() as s:
        res = s.call("Target.createTarget", {"url": url})
    return res.get("targetId", "")


def close_tab(target_id: str) -> None:
    with browser_session() as s:
        s.call("Target.closeTarget", {"targetId": target_id})


# JS that summarises a page the way the UI parser summarises a screen: the
# things worth acting on, numbered, rather than the whole DOM.
READ_PAGE_JS = r"""
(function () {
  const vis = (e) => {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const out = { title: document.title, url: location.href, items: [] };
  const sel = 'a[href], button, input, textarea, select, [role=button], [role=link], [onclick]';
  let i = 0;
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e) || i >= __MAXITEMS__) continue;
    const tag = e.tagName.toLowerCase();
    let label = clean(e.innerText || e.value || e.placeholder ||
                      e.getAttribute('aria-label') || e.getAttribute('title') || e.name || '');
    if (!label && tag === 'a') label = clean(e.getAttribute('href'));
    out.items.push({
      i: i++, tag,
      type: e.getAttribute('type') || '',
      label,
      href: tag === 'a' ? (e.href || '').slice(0, 120) : ''
    });
    e.setAttribute('data-au-idx', String(i - 1));
  }
  out.text = clean(document.body ? document.body.innerText : '').slice(0, __MAXTEXT__);
  return JSON.stringify(out);
})()
"""


def read_page(session: Session, max_items: int = 60, max_text: int = 1500) -> dict:
    js = READ_PAGE_JS.replace("__MAXITEMS__", str(max_items)).replace(
        "__MAXTEXT__", str(max_text)
    )
    raw = session.evaluate(js)
    return json.loads(raw) if raw else {}


def render_page(page: dict) -> str:
    lines = [
        f"Title: {page.get('title', '')}",
        f"URL:   {page.get('url', '')}",
    ]
    text = page.get("text", "")
    if text:
        lines += ["", "Page text:", f"  {text}"]
    items = page.get("items", [])
    lines.append("")
    if items:
        lines.append("Clickable / input elements (act on these by index):")
        for it in items:
            kind = it["tag"] + (f":{it['type']}" if it.get("type") else "")
            extra = f"  -> {it['href']}" if it.get("href") else ""
            lines.append(f"  [{it['i']}] {it.get('label') or '(no label)'} <{kind}>{extra}")
    else:
        lines.append("(no interactive elements found)")
    return "\n".join(lines)


def act_on_index(session: Session, index: int, action: str) -> str | None:
    """Act on an element stamped by a previous read_page.

    The stamps persist on the DOM nodes, so an index stays pointing at the same
    element even if the page has changed around it. Returns the element's label,
    or None when the stamp is gone and the caller must re-read.
    """
    js = (
        "(function(){"
        f"var e=document.querySelector('[data-au-idx=\"{index}\"]');"
        "if(!e) return null;"
        "var l=(e.innerText||e.value||e.placeholder||e.getAttribute('aria-label')"
        "||e.name||'').replace(/\\s+/g,' ').trim().slice(0,80);"
        "e.scrollIntoView({block:'center'});"
        f"e.{action}();"
        "return l||'(no label)';"
        "})()"
    )
    return session.evaluate(js)


def stamped(session: Session) -> bool:
    """Has this page been read (and therefore indexed) already?"""
    return bool(session.evaluate("!!document.querySelector('[data-au-idx]')"))
