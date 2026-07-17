"""Shared wallet-record access for the ``ui/`` package.

Wallet records live in Flet ``shared_preferences`` under keys prefixed
``"wallet."`` (a JSON-serialised dict each). Several extracted ``ui/components``
modules (dev tools, NFT gallery, liquid staking — and later the transfer screens
+ wallet cards) need the same list of wallet dicts, so this centralises the read
+ parse + junk-filter into one place instead of each module re-declaring its own
inline copy.

Behaviourally this is the slice of ``main.py``'s ``get_storage_data(prefix=
"wallet.")`` that downstream UI actually consumes: it JSON-decodes each value and
keeps only dict records carrying an ``address_base58``. The unused
``storage_key`` field that ``get_storage_data`` injects is omitted (no current
``ui/`` consumer needs it; ``main.py``'s own delete/rename flows still go through
the closure).
"""

import json

from ui.context import AppContext


async def load_wallets(ctx: AppContext) -> list:
    """Return the user's wallet dicts from ``shared_preferences`` (``wallet.`` keys).

    Non-JSON values and records without an ``address_base58`` are skipped. Never
    raises — a ``shared_preferences`` failure returns ``[]`` (mirrors the legacy
    closure's callers, which all treat an empty wallet list as "no wallets yet").
    """
    wallets: list = []
    try:
        keys = await ctx.page.shared_preferences.get_keys("wallet.")
    except Exception:
        return wallets
    for key in keys:
        raw = await ctx.page.shared_preferences.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(raw, dict) and raw.get("address_base58"):
            wallets.append(raw)
    return wallets
