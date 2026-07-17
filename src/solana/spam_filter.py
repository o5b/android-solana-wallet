"""Spam / scam token detection for the balance screen.

Solana wallets are routinely airdropped malicious tokens (fake "USDC", "claim
your reward at evil.com", honeypots, dusting scams). The goal of these tokens
is to bait the user into interacting with a hostile contract or phishing site
that drains their SOL. Phantom / Solflare hide or flag these.

This module classifies each ``get_sol_spl_balance`` token record as
``spam`` / ``suspicious`` / ``clean`` using three complementary layers:

1. **Curated registries**
   - ``KNOWN_GOOD_MINTS`` / ``KNOWN_GOOD_SYMBOLS`` — well-known legitimate
     tokens. A mint here is *never* flagged (real USDC has a freeze authority;
     that is expected and not a scam signal).
   - ``KNOWN_SPAM_MINTS`` — confirmed scam mints.

2. **Heuristics** (when a mint is in neither registry):
   - **Impersonation**: name/symbol equals (or closely resembles) a curated
     token's symbol but the mint differs — e.g. an airdrop named "USDC" that is
     not the real USDC mint.
   - **Suspicious text**: name/symbol embedding URLs (``.com``/``.io``/``http``),
     bait words (``claim``/``airdrop``/``visit``/``free``), or a known symbol
     followed by scam bait (``"USDC claim at x.io"``).
   - **Open mint / freeze authority**: a still-present ``mintAuthority`` can
     inflate supply to anything (classic rug/honeypot); a ``freezeAuthority``
     on an impersonating token is a strong scam signal.
   - **No market liquidity**: a token that impersonates a priced symbol but
     carries no Jupiter price (``usd_price`` set by ``prices.py``) is almost
     always spam.

3. **Price signal** (from ``prices.py`` enrichment, optional): a token that
   *has* a real ``usd_price`` is treated as having genuine market liquidity,
   which downgrades an isolated open-mint-authority hit from ``suspicious`` to
   ``clean`` (real, traded tokens legitimately keep authorities early on).

Each flagged token gets ``token['spam'] = {flag, severity, reasons}``
(``severity`` ∈ ``{"spam", "suspicious"}``). The module never raises and never
blocks balance display — on any error a token is simply left un-flagged.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import asyncio

import httpx


# ---------------------------------------------------------------------------
# Curated registries
# ---------------------------------------------------------------------------
#
# Canonical mainnet mints for widely-held tokens. Used both as a "never flag"
# whitelist and as the impersonation reference (symbol -> real mint).
# IMPORTANT: only verified-correct mints belong here. A *wrong* mint here would
# cause the REAL token to be mis-flagged as impersonating its own symbol
# (detect_impersonation fires when symbol matches but mint differs), so every
# entry must be the exact canonical mint address.
KNOWN_GOOD_SYMBOLS: Dict[str, str] = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanXYwCux9g6aNQsqnBACzRZES1B",
    "JTO": "jtojtomepa8beP8AuQc6eXt5Friqnwf7faHrxYYm7C5",
}

# A curated good-mint set is derived from the symbol table; extra well-known
# mints with no stable ticker live here.
KNOWN_GOOD_MINTS: set = set(KNOWN_GOOD_SYMBOLS.values()) | {
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # JitoSOL (liquid staking)
}

# Confirmed spam mints (well-known airdrop scams). Start small — the registry
# is extended as scams are identified. A mint here is always ``severity=spam``.
KNOWN_SPAM_MINTS: set = set()  # confirmed scam mints -> always severity=spam


# ---------------------------------------------------------------------------
# Text-normalization + impersonation heuristics (pure, offline-testable)
# ---------------------------------------------------------------------------

# Substrings commonly embedded in spam token names/symbols. A URL fragment is
# the strongest single signal (scammers embed "claim at <site>").
_URL_FRAGMENTS = (
    "http://", "https://", "www.", ".com", ".io", ".xyz", ".site",
    ".fun", ".net", ".app", ".link", ".finance", ".money", ".cash",
    ".club", ".online", ".art", ".page", ".pro",
)

# Bait words that lure users to interact / click.
_BAIT_WORDS = (
    "claim", "airdrop", "visiting", "visit", "reward", "rewards",
    "connect wallet", "connect", "free", "bonus", "giveaway",
    "unlock", "redeem", "stake now", "swap now", "bridge",
    "click", "$$", "winner",
)


def _norm_symbol(sym: Optional[str]) -> str:
    """Normalize a symbol for impersonation comparison: strip + upper + alnum."""
    if not sym or not isinstance(sym, str):
        return ""
    out = []
    for ch in sym.strip().upper():
        # keep ascii letters/digits, drop punctuation/space/zero-width junk
        if ch.isascii() and ch.isalnum():
            out.append(ch)
    return "".join(out)


def _has_url_fragment(text: str) -> Optional[str]:
    low = text.lower()
    for frag in _URL_FRAGMENTS:
        if frag in low:
            return frag
    return None


def _has_bait_word(text: str) -> Optional[str]:
    low = text.lower()
    return next((w for w in _BAIT_WORDS if w in low), None)


def detect_impersonation(name: Optional[str], symbol: Optional[str], mint: Optional[str]) -> Optional[str]:
    """Return the impersonated canonical symbol, or ``None`` if not impersonating.

    Fires when a name/symbol normalizes to a known-good symbol but the mint is
    *not* that symbol's real mint. Also catches ``"USDC <bait>"`` style names
    (the leading token ticker is the impersonation target).
    """
    if mint and mint in KNOWN_GOOD_MINTS:
        return None  # the real thing
    real_mint_for = {sym: m for sym, m in KNOWN_GOOD_SYMBOLS.items()}
    candidates = {symbol, name}
    for raw in candidates:
        norm = _norm_symbol(raw)
        if not norm:
            continue
        # exact normalized match against a known symbol whose mint differs
        if norm in real_mint_for and real_mint_for[norm] != mint:
            return norm
    # name begins with a known ticker then non-alnum (e.g. "USDC-claim",
    # "BONK airdrop", "WEN.claim") — strong impersonation signal.
    nm = (name or "").strip().upper()
    for sym in real_mint_for:
        if len(sym) >= 3 and nm.startswith(sym):
            rest = nm[len(sym):]
            if rest and not rest[0].isalnum():
                if real_mint_for[sym] != mint:
                    return sym
    return None


def detect_suspicious_text(name: Optional[str], symbol: Optional[str]) -> List[str]:
    """Return a list of suspicious-text reasons (URL fragments, bait words)."""
    blob = f"{name or ''} {symbol or ''}"
    if not blob.strip():
        return []
    reasons: List[str] = []
    url = _has_url_fragment(blob)
    if url:
        reasons.append(f"name/symbol contains URL '{url}'")
    bait = _has_bait_word(blob)
    if bait:
        reasons.append(f"name/symbol contains bait word '{bait}'")
    return reasons


def detect_authority_risk(
    mint_authority: Optional[str],
    freeze_authority: Optional[str],
    *,
    impersonating: Optional[str] = None,
    has_market_price: bool = False,
) -> List[str]:
    """Authority-based scam signals from the on-chain mint account.

    - ``mintAuthority`` present on an *unknown* token => infinite-supply rug
      risk. Downgraded to informational when the token genuinely trades
      (``has_market_price``), because many early legit tokens keep it.
    - ``freezeAuthority`` present *while impersonating* a known token =>
      strong scam signal (the attacker can lock victims out after they buy).
    """
    reasons: List[str] = []
    if mint_authority and not has_market_price:
        reasons.append("open mint authority (supply can be inflated)")
    if freeze_authority and impersonating:
        reasons.append(f"freeze authority while impersonating {impersonating}")
    return reasons


# ---------------------------------------------------------------------------
# On-chain authority fetch (batched, best-effort)
# ---------------------------------------------------------------------------

async def get_mint_authorities(
    mints: Iterable[str],
    network: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 30.0,
) -> Dict[str, Dict[str, Any]]:
    """Batch-fetch ``{mintAuthority, freezeAuthority, supply}`` for mints.

    Uses one ``getMultipleAccountsInfo`` with ``jsonParsed`` per chunk of 100
    (the RPC limit). Returns ``{mint: {"mint_authority": str|None,
    "freeze_authority": str|None, "supply": int|None}}``. Never raises — a
    network/RPC failure returns the empty/partial map.
    """
    mint_list = [m for m in dict.fromkeys(mints) if m]
    out: Dict[str, Dict[str, Any]] = {}
    if not mint_list:
        return out

    headers = {"Content-Type": "application/json"}
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)

    async def _chunk(chunk: List[str]) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getMultipleAccountsInfo",
            "params": [chunk, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        }
        try:
            resp = await client.post(network, headers=headers, json=payload)
            if resp.status_code != 200:
                return
            data = resp.json()
        except Exception as er:
            print(f"spam_filter.get_mint_authorities chunk error: {er}")
            return
        values = (data or {}).get("result", {}).get("value", []) or []
        for mint, acct in zip(chunk, values):
            if not isinstance(acct, dict):
                continue
            parsed = (acct.get("data") or {}).get("parsed", {})
            info = parsed.get("info", {}) if isinstance(parsed, dict) else {}
            try:
                supply = int(info.get("supply", 0) or 0)
            except (TypeError, ValueError):
                supply = None
            out[mint] = {
                "mint_authority": info.get("mintAuthority"),
                "freeze_authority": info.get("freezeAuthority"),
                "supply": supply,
            }

    try:
        chunks = [mint_list[i:i + 100] for i in range(0, len(mint_list), 100)]
        await asyncio.gather(*(_chunk(c) for c in chunks), return_exceptions=True)
        return out
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Per-token classification
# ---------------------------------------------------------------------------

def _token_symbol_name(token: dict) -> tuple:
    """Best-effort (name, symbol) from a balance token record."""
    meta = token.get("metadata_from_uri") or {}
    name = (meta.get("name") or token.get("name_metaplex")
            or token.get("name_2022") or "")
    symbol = (meta.get("symbol") or token.get("symbol_metaplex")
              or token.get("symbol_2022") or "")
    return str(name) if name else "", str(symbol) if symbol else ""


def classify_token(
    token: dict,
    *,
    mint_info: Optional[Dict[str, Any]] = None,
    priced_mints: Optional[set] = None,
) -> Dict[str, Any]:
    """Classify one balance token record.

    Args:
        token: a ``get_sol_spl_balance`` token dict.
        mint_info: optional ``{mint: {mint_authority, freeze_authority, supply}}``
            map (from :func:`get_mint_authorities`). When absent the authority
            heuristics are simply skipped.
        priced_mints: optional set of mints known to have a real Jupiter price
            (market liquidity). When absent, ``token['usd_price']`` set by
            ``prices.py`` is used instead.

    Returns ``{"flag": bool, "severity": "spam"|"suspicious"|None, "reasons": [...]}``.
    """
    verdict: Dict[str, Any] = {"flag": False, "severity": None, "reasons": []}
    try:
        mint = token.get("mint")

        # 1) curated whitelist short-circuit
        if mint in KNOWN_GOOD_MINTS:
            return verdict
        # 2) curated spam blacklist
        if mint in KNOWN_SPAM_MINTS:
            verdict.update(flag=True, severity="spam",
                           reasons=["confirmed spam mint"])
            return verdict

        name, symbol = _token_symbol_name(token)

        # market-liquidity signal (price set by prices.py, or caller override)
        has_price = bool(token.get("usd_price")) or (mint in (priced_mints or set()))

        impersonating = detect_impersonation(name, symbol, mint)
        text_reasons = detect_suspicious_text(name, symbol)

        reasons: List[str] = []
        if impersonating:
            reasons.append(f"impersonates {impersonating}")
        reasons.extend(text_reasons)

        # 3) authority heuristics (only if we fetched them)
        if mint_info and mint in mint_info:
            info = mint_info[mint] or {}
            reasons.extend(detect_authority_risk(
                info.get("mint_authority"),
                info.get("freeze_authority"),
                impersonating=impersonating,
                has_market_price=has_price,
            ))

        if not reasons:
            return verdict

        # severity: impersonation OR url OR spam-mint => 'spam'; else 'suspicious'
        is_spam = bool(impersonating) or any("URL" in r for r in text_reasons)
        verdict.update(flag=True, severity="spam" if is_spam else "suspicious", reasons=reasons)
        return verdict
    except Exception as er:
        # never let classification break balance display
        print(f"spam_filter.classify_token error on {token.get('mint')}: {er}")
        return verdict


# ---------------------------------------------------------------------------
# Balance-result enrichment (mirrors prices.enrich_balance_result_with_prices)
# ---------------------------------------------------------------------------

def _verdict_summary():
    return {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}


async def enrich_balance_result_with_spam_filter(
    result: list,
    *,
    fetch_authorities: bool = True,
) -> Dict[str, Any]:
    """Attach a ``spam`` verdict to each token in a balance result.

    Mutates each token record in place by setting ``token['spam']``. Returns a
    summary ``{"spam": int, "suspicious": int, "flagged": int, "total": int}``.

    Authority data is fetched once per network with a single batched
    ``getMultipleAccountsInfo`` when ``fetch_authorities`` is True; on any
    failure the authority heuristics are skipped (text/impersonation heuristics
    still run). Never raises.
    """
    summary = _verdict_summary()
    try:
        # collect mints per network for the batched authority fetch
        for nr in result or []:
            for t in nr.get("spl", []) or []:
                summary["total"] += 1

        # a set of mints the prices layer marked as having a real price
        priced_mints = {
            t.get("mint") for nr in (result or [])
            for t in (nr.get("spl") or [])
            if t.get("usd_price") is not None
        }

        mint_info: Dict[str, Dict[str, Any]] = {}
        if fetch_authorities:
            for nr in result or []:
                network = nr.get("network")
                mints = [t.get("mint") for t in (nr.get("spl") or []) if t.get("mint")]
                if not mints:
                    continue
                try:
                    part = await get_mint_authorities(mints, network)
                    mint_info.update(part)
                except Exception as er:
                    print(f"spam_filter: authority fetch failed for {network}: {er}")

        for nr in result or []:
            for t in nr.get("spl", []) or []:
                v = classify_token(t, mint_info=mint_info, priced_mints=priced_mints)
                t["spam"] = v
                if v["flag"]:
                    summary["flagged"] += 1
                    if v["severity"] == "spam":
                        summary["spam"] += 1
                    else:
                        summary["suspicious"] += 1
        return summary
    except Exception as er:
        print(f"spam_filter.enrich_balance_result_with_spam_filter error: {er}")
        return summary


def is_hidden_spam(token: dict) -> bool:
    """True when a token should be hidden from the default balance view."""
    v = token.get("spam") or {}
    return bool(v.get("flag") and v.get("severity") == "spam")


def is_suspicious(token: dict) -> bool:
    """True when a token is flagged but only ``suspicious`` (still shown, badged)."""
    v = token.get("spam") or {}
    return bool(v.get("flag") and v.get("severity") == "suspicious")


__all__ = [
    "KNOWN_GOOD_MINTS",
    "KNOWN_GOOD_SYMBOLS",
    "KNOWN_SPAM_MINTS",
    "get_mint_authorities",
    "classify_token",
    "detect_impersonation",
    "detect_suspicious_text",
    "detect_authority_risk",
    "enrich_balance_result_with_spam_filter",
    "is_hidden_spam",
    "is_suspicious",
]
