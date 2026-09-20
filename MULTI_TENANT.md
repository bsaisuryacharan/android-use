# Running this for more than one customer

## The shape

One **process per customer**. Each has its own config file, its own URL secret,
its own port, and its own device list.

```
customer A ──▶ /mcp/<secretA> ──▶ :8100 ──▶ tenants/acme/config.json   ──▶ A's phones
customer B ──▶ /mcp/<secretB> ──▶ :8101 ──▶ tenants/globex/config.json ──▶ B's phones
```

Isolation is **structural, not conditional**. There is no "which tenant is
this?" check anywhere, because a process only ever loads one customer's config.
A scoping bug cannot leak phones between customers, because there is no scoping
code to get wrong.

That matters more than it sounds. These device tokens grant full screen-reading
and tapping on someone's phone. A single shared server holding every customer's
tokens means one breach owns every customer's phone.

## Using it

```bash
./tenant.sh create acme        # own config, port, secret; empty device list
./tenant.sh start acme
./tenant.sh url acme           # the connector URL to hand that customer
./tenant.sh list
./tenant.sh stop acme
```

`expose` prints the `tailscale funnel` command rather than running it.
Publishing a phone-control endpoint to the internet should be a deliberate act,
not a side effect of a provisioning script.

## Verified

- Separate secrets per tenant
- A device added to `acme` is invisible to `globex`
- Every cross-tenant secret returns **404** - the same response as any unknown
  route, so a prober cannot tell a wrong secret from a wrong path
- The operator's own devices are untouched by either tenant

## What this does NOT solve

**Network isolation.** Every tenant process here runs on one machine, on one
tailnet. A tenant's server can only reach phones whose tokens it holds, but the
machine itself can see every phone on that tailnet. For real separation each
customer's server must sit on **their** tailnet, with their auth key - which
means the customer self-hosts, or you run one container per customer joined to
their tailnet.

**Identity - partly addressed.** Minted bearer tokens now carry a subject, an
expiry and a revoke switch:

```bash
python -m android_use.tokens mint acme --days 90 --label "Acme Ltd"
python -m android_use.tokens list
python -m android_use.tokens revoke <token-id>
```

Send as `Authorization: Bearer au_...`, to `/mcp` - no secret in the path, so
the credential stays out of logs, referrers and screenshots. Tokens are stored
only as a salted hash; the plaintext is shown once. Revocation takes effect on
the next request.

The URL secret still works, deliberately, so connectors already configured with
it keep running. A wrong bearer token gets 401; no credential at all still gets
404.

This is not yet OAuth. The subject is recorded on the request but device
resolution is not yet filtered by it - with one process per customer there is
nothing to filter, and adding half-scoping to a shared server would invite
exactly the bug that per-process isolation avoids.

**Token handling.** Device tokens sit in plaintext config, long-lived and
non-expiring. They should be short-lived, refreshable, and revocable from the
phone.

## The path to a real product

Move identity into the protocol rather than the URL:

1. OAuth, so each request carries a verified `AccessToken.subject`
2. A device registry keyed by owner, in a database rather than a JSON file
3. Every `resolve()` filtered by that subject
4. Per-device tokens issued and revoked server-side, with expiry

The MCP SDK supports this directly (`auth_server_provider`, `token_verifier`,
and `Context.headers` for per-request identity). It is a proper build with a
security review attached - not an afternoon - and until then, one process per
customer is the honest way to keep customers apart.
