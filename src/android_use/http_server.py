"""Serve the MCP server over HTTP so claude.ai can reach it.

Claude connects to a custom connector from Anthropic's cloud, not from your
machine, so the endpoint has to be publicly reachable. That means this process
is exposed to the internet, and it controls a phone - so it is gated two ways:

1. An unguessable secret in the URL path. Claude stores the whole URL, so this
   travels with the connector and needs no interactive login.
2. An optional bearer token, checked in constant time, for clients that can
   send one.

This is capability-URL security. It is meaningfully better than nothing and
appropriate for a personal setup, but be clear-eyed: anyone who obtains the URL
can drive the phone. Treat it like a password - never paste it into a chat, an
issue, or a screenshot.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from .server import mcp

CONFIG_PATH = Path(
    os.environ.get("ANDROID_USE_CONFIG", Path.home() / ".android-use" / "config.json")
)


def _config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def url_secret() -> str:
    """The unguessable path segment, generated once and reused."""
    cfg = _config()
    existing = cfg.get("mcp_url_secret")
    if existing:
        return existing
    generated = secrets.token_urlsafe(32)
    cfg["mcp_url_secret"] = generated
    _save(cfg)
    return generated


def bearer_token() -> str:
    return _config().get("mcp_bearer_token", "")


class Gate(BaseHTTPMiddleware):
    """Reject anything that is not the exact secret path (plus health checks)."""

    def __init__(self, app, secret_path: str, token: str = ""):
        super().__init__(app)
        self.secret_path = secret_path
        self.token = token

    async def dispatch(self, request, call_next):
        from . import auth

        path = request.url.path
        if path == "/healthz":
            return PlainTextResponse("ok")

        presented = (request.headers.get("authorization") or "").removeprefix(
            "Bearer "
        ).strip()

        # A minted bearer token is the stronger credential: it has an identity,
        # an expiry and a revoke switch, and it travels in a header rather than
        # a URL that ends up in logs and screenshots.
        if presented.startswith("au_"):
            subject = auth.verify(presented)
            if not subject:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.scope["android_use_subject"] = subject
            # The MCP app is mounted on the secret path. A bearer-authenticated
            # caller should not need to know that secret, so route their
            # request there internally.
            if not request.url.path.startswith(self.secret_path):
                request.scope["path"] = self.secret_path
                request.scope["raw_path"] = self.secret_path.encode()
            return await call_next(request)

        # Fall back to the capability URL. Kept because connectors already
        # configured with it must keep working; a wrong secret looks exactly
        # like a wrong route so a prober learns nothing.
        if not path.startswith(self.secret_path):
            return JSONResponse({"error": "not found"}, status_code=404)
        if self.token and not hmac.compare_digest(presented, self.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app() -> tuple[Starlette, str]:
    secret = url_secret()
    path = f"/mcp/{secret}"
    app = mcp.streamable_http_app(streamable_http_path=path, host="0.0.0.0")
    app.add_middleware(Gate, secret_path=path, token=bearer_token())
    # claude.ai and Claude Desktop reach this server from a browser, so they send
    # a CORS preflight before connecting. Without this the OPTIONS request 405s
    # and the client just reports "no server responded at this URL". Added after
    # Gate so it wraps it: the preflight is answered before the secret check, and
    # the client can read mcp-session-id back off the response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://claude.ai", "https://claude.com"],
        allow_origin_regex=r"https://([a-z0-9-]+\.)*(claude\.ai|claude\.com|anthropic\.com)",
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    app.router.routes.append(
        Route("/healthz", lambda r: PlainTextResponse("ok"), methods=["GET"])
    )
    return app, path


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve android-use over HTTP.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address; Funnel forwards to localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--show-url", action="store_true",
                        help="print the connector URL and exit")
    args = parser.parse_args()

    app, path = build_app()
    cfg = _config()
    public = cfg.get("public_base_url", "")
    if args.show_url:
        print(f"path: {path}")
        print(f"connector URL: {public}{path}" if public
              else "Set 'public_base_url' in config once Funnel is on.")
        return

    import uvicorn

    # Never log the secret path: this output goes to a log file that outlives
    # the process. Use --show-url when you actually want to read it.
    print(f"Serving MCP on http://{args.host}:{args.port} (path hidden)")
    if public:
        print("Public connector URL available via --show-url")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
