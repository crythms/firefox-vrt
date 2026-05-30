#!/usr/bin/env python3
"""Decode the Auth0 token mach uses for Lando, and print what the
identity provider thinks of you.

When Lando says "Missing permissions: main.scm_level_1" despite you
having been granted access, this script answers "what does Lando
actually see?" — by reading the JWT mach has cached and showing its
claims. The key one is the `https://sso.mozilla.com/claim/groups`
list; it should include `active_scm_level_1`.

Output is safe to share with the support team verbatim — JWTs aren't
secret, they're claims signed by Auth0, and the team can verify what
permissions your session carries.

Usage
-----
    python3 scripts/decode_mach_jwt.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional


def find_token_files() -> list[Path]:
    """Walk ~/.mozbuild and find every JSON file that contains an
    `access_token` field. Returns a list of paths (sometimes there are
    several caches; we'll decode all of them)."""
    root = Path.home() / ".mozbuild"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and "access_token" in data:
            out.append(p)
    return out


def decode_jwt(token: str) -> Optional[dict]:
    """Decode the payload section of a JWT (`header.payload.signature`).
    Returns the parsed dict or None if it isn't a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    # JWTs use base64url and often omit padding. Re-add it before decoding.
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64)
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None


def summarize(claims: dict) -> None:
    """Print a friendly summary plus the full claims."""
    # Common Auth0 claim keys.
    sub = claims.get("sub")
    email = claims.get("email") or claims.get("https://sso.mozilla.com/claim/emails") or "(not in claims)"
    iat = claims.get("iat")
    exp = claims.get("exp")
    groups = (
        claims.get("https://sso.mozilla.com/claim/groups")
        or claims.get("groups")
        or []
    )

    print("Identity:")
    print(f"  sub:   {sub}")
    print(f"  email: {email}")
    print(f"  iat:   {iat}  (token issued at, unix seconds)")
    print(f"  exp:   {exp}  (token expires at, unix seconds)")
    print()

    if isinstance(groups, list) and groups:
        print(f"Groups ({len(groups)}):")
        for g in sorted(str(x) for x in groups):
            marker = "  ★ " if "scm_level" in g else "    "
            print(f"{marker}{g}")
        scm_groups = [g for g in groups if "scm_level" in str(g)]
        if scm_groups:
            print()
            print(f"SCM level claims found: {scm_groups}")
            print("→ Lando should accept your push.")
        else:
            print()
            print("No `scm_level_*` claim found in this token.")
            print("→ This is what's making Lando reject the push.")
    else:
        print("No groups claim found at all.")
        print("→ This token doesn't carry permission claims; auth is mis-routed.")

    print()
    print("Full claims (share with support if needed):")
    print(json.dumps(claims, indent=2, sort_keys=True))


def main() -> int:
    files = find_token_files()
    if not files:
        print("No cached access_token found under ~/.mozbuild.")
        print("Run a `mach try fuzzy ...` (or similar) command first so mach")
        print("creates its auth cache, then re-run this script.")
        return 1

    for path in files:
        print("=" * 72)
        print(f"Token file: {path}")
        print("=" * 72)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"(could not read: {exc!r})")
            continue
        token = data.get("access_token")
        if not isinstance(token, str):
            print("(no string access_token field)")
            continue
        claims = decode_jwt(token)
        if claims is None:
            print(f"(access_token isn't a JWT; first 80 chars: {token[:80]!r})")
            continue
        summarize(claims)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
