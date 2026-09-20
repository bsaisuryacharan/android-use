# Using android-use from claude.ai

Claude Code and Claude Desktop talk to this server over **stdio**, locally.
claude.ai cannot: a custom connector is dialled from **Anthropic's cloud**, not
from your machine, so the endpoint has to be reachable over the public internet
and speak Streamable HTTP.

That is what `android_use.http_server` is for.

```
claude.ai  ──▶  Anthropic cloud  ──HTTPS──▶  Tailscale Funnel
                                                  │
                                            MCP server (Mac, :8080)
                                                  │ Tailscale
                                                  ▼
                                            phone (any network)
```

## Security

This process is exposed to the internet and it controls a phone, so it is
gated two ways:

1. **An unguessable secret in the URL path** (`/mcp/<43 random chars>`). Claude
   stores the whole URL with the connector, so no interactive login is needed.
   A wrong secret returns the same 404 as any other miss, so a prober cannot
   tell a bad secret from a bad route.
2. **An optional bearer token** (`mcp_bearer_token` in config), compared in
   constant time.

Be clear-eyed about what this is: capability-URL security. Anyone holding the
URL can read the phone's screen and tap on it. **Treat it like a password** —
never paste it into a chat, an issue, or a screenshot. To invalidate it, delete
`mcp_url_secret` from `~/.android-use/config.json` and restart; a new one is
generated and the old URL stops working.

## Running it

```bash
PYTHONPATH=src .venv/bin/python -m android_use.http_server --port 8080
PYTHONPATH=src .venv/bin/python -m android_use.http_server --show-url   # print the path
```

## Exposing it

```bash
tailscale --socket="$HOME/.tailscale-userspace/tailscaled.sock" \
  funnel --bg --https=443 8080
```

Funnel needs HTTPS and the funnel node attribute enabled for the tailnet; the
command says so if they are not, with a link to the admin console.

Then set `public_base_url` in `~/.android-use/config.json` to
`https://<your-node>.<tailnet>.ts.net` and run `--show-url` to get the full
connector URL. Add it in claude.ai under Settings → Connectors → Add custom
connector.

To stop exposing it: `tailscale funnel --https=443 off`.

## What this does and does not solve

- **The phone needs no Wi-Fi.** Cellular plus Tailscale is enough. That is the
  case this was built for — helping someone in another city.
- **The host does need to be online.** With Funnel on the Mac, the Mac must be
  awake and connected. If that is not acceptable, run the MCP server on
  something always-on (a Pi or a small VPS) that has joined the tailnet; nothing
  else changes.
- **Mobile is unconfirmed.** Anthropic's support article lists custom connectors
  for "Claude, Cowork, and Claude Desktop" and does not mention the mobile apps.
  claude.ai on the web is confirmed. Check the app before relying on it.


## Verified working

Funnel requires **kernel networking**. It does not work with
`tailscaled --tun=userspace-networking`: the node never advertises an HTTPS
ingress service (`Hostinfo.Services` shows only `peerapi`), so the control
plane never publishes the public DNS record. This is a known upstream
limitation - see tailscale/tailscale#12788. Symptom: `funnel status` cheerfully
reports "Funnel on" while the name resolves to nothing, and the authoritative
nameserver answers NOERROR with zero records.

With the proper (kernel-mode) install, DNS publishes within seconds.

Confirmed end to end over the public internet:

- `GET /healthz` -> 200, valid TLS
- `GET /mcp/<wrong>` -> 404, same as any other miss
- MCP `initialize` + `tools/list` -> 29 tools
- `tools/call get_screen` -> live phone screen

## Getting the connector URL

```bash
PYTHONPATH=src .venv/bin/python -m android_use.http_server --show-url
```

Paste the result into claude.ai -> Settings -> Connectors -> Add custom
connector. Treat that URL as a password.
