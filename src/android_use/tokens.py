"""CLI for issuing and revoking access tokens.

    python -m android_use.tokens mint <subject> [--days N] [--label TEXT]
    python -m android_use.tokens list
    python -m android_use.tokens revoke <token-id>
"""
from __future__ import annotations

import argparse

from . import auth


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage android-use access tokens.")
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("mint", help="issue a token")
    m.add_argument("subject", help="who this token is for, e.g. a customer id")
    m.add_argument("--days", type=int, default=90)
    m.add_argument("--label", default="")

    sub.add_parser("list", help="show issued tokens")

    r = sub.add_parser("revoke", help="revoke a token by id")
    r.add_argument("token_id")

    args = parser.parse_args()

    if args.command == "mint":
        token, grant = auth.mint(args.subject, args.days, args.label)
        print(f"subject : {grant.subject}")
        print(f"token id: {grant.token_id}")
        print(f"expires : {grant.expires_at}")
        print()
        print("Token (shown once - it is stored only as a hash):")
        print(f"  {token}")
        print()
        print("Send it as:  Authorization: Bearer <token>")
    elif args.command == "list":
        grants = auth.listing()
        if not grants:
            print("No tokens issued. The URL secret is still what governs access.")
            return
        print(f"{'TOKEN ID':18} {'SUBJECT':14} {'STATE':10} EXPIRES")
        for g in grants:
            state = "revoked" if g.revoked else ("expired" if g.expired else "active")
            print(f"{g.token_id:18} {g.subject:14} {state:10} {g.expires_at[:19]}")
    elif args.command == "revoke":
        print("revoked" if auth.revoke(args.token_id) else "no such token id")


if __name__ == "__main__":
    main()
