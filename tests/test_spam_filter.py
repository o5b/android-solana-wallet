"""Offline tests for the spam / scam token filter.

Run:
    PYTHONPATH=src venv/bin/python tests/test_spam_filter.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from solana.spam_filter import (
    KNOWN_GOOD_MINTS,
    KNOWN_SPAM_MINTS,
    classify_token,
    detect_impersonation,
    detect_authority_risk,
    detect_suspicious_text,
    enrich_balance_result_with_spam_filter,
    get_mint_authorities,
    is_hidden_spam,
    is_suspicious,
)

_passed = 0
_failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
FAKE_MINT = "FakeFakeFakeFakeFakeFakeFakeFakeFakeFake12"


def token(mint, *, name="", symbol="", amount=1.0, decimals=6, price=False):
    t = {
        "mint": mint,
        "amount": amount,
        "decimals": decimals,
        "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "metadata_from_uri": {},
    }
    if name or symbol:
        t["metadata_from_uri"] = {"name": name, "symbol": symbol}
    if price:
        t["usd_price"] = 1.0
    return t


def run():
    print("== impersonation ==")
    # real USDC mint -> never impersonating
    check("real USDC not impersonating",
          detect_impersonation("USD Coin", "USDC", USDC) is None)
    # same symbol, different mint -> impersonating USDC
    check("fake USDC symbol impersonates",
          detect_impersonation("USD Coin", "USDC", FAKE_MINT) == "USDC")
    # name starts with ticker + separator + bait
    check("'BONK airdrop' name impersonates BONK",
          detect_impersonation("BONK airdrop", "BONKx", FAKE_MINT) == "BONK")
    # totally unrelated -> not impersonating
    check("random symbol not impersonating",
          detect_impersonation("My Doge", "DOGE2", FAKE_MINT) is None)
    # normalization: lowercase + punctuation
    check("normalized 'usdc.' impersonates",
          detect_impersonation("u s d c", "usdc.", FAKE_MINT) == "USDC")

    print("== suspicious text ==")
    check("URL .com detected", len(detect_suspicious_text("claim at x.com", "X")) >= 1)
    check("bait 'airdrop' detected", len(detect_suspicious_text("Free airdrop", "FREE")) >= 1)
    check("clean name no reasons", detect_suspicious_text("Wrapped SOL", "SOL") == [])
    check("https URL detected",
          any("URL" in r for r in detect_suspicious_text("visit https://evil.io", "")))

    print("== authority risk ==")
    # open mint authority on unpriced token -> risk
    check("open mint authority risk",
          len(detect_authority_risk("SomeAuth", None)) == 1)
    # priced token with mint authority -> downgraded (no reason)
    check("mint authority downgraded when priced",
          detect_authority_risk("SomeAuth", None, has_market_price=True) == [])
    # freeze authority + impersonation -> risk
    check("freeze + impersonation risk",
          len(detect_authority_risk(None, "SomeAuth", impersonating="USDC")) == 1)
    # freeze authority alone (no impersonation) -> not flagged
    check("freeze alone not flagged",
          detect_authority_risk(None, "SomeAuth") == [])

    print("== classify_token ==")
    # known-good mint -> clean even with scary metadata
    v = classify_token(token(USDC, name="USDC claim x.com", symbol="USDC"))
    check("known-good mint never flagged", v["flag"] is False)

    # confirmed spam mint -> spam
    v = classify_token(token(FAKE_MINT))
    # inject into the blacklist for the test
    import solana.spam_filter as sf
    sf.KNOWN_SPAM_MINTS.add(FAKE_MINT)
    v = classify_token(token(FAKE_MINT))
    check("blacklisted mint -> spam", v["flag"] and v["severity"] == "spam")
    sf.KNOWN_SPAM_MINTS.discard(FAKE_MINT)

    # impersonation -> spam
    v = classify_token(token(FAKE_MINT, name="USD Coin", symbol="USDC"))
    check("impersonation -> spam", v["flag"] and v["severity"] == "spam")
    check("impersonation reason present",
          any("impersonates" in r for r in v["reasons"]))

    # bait word only -> suspicious (not spam)
    v = classify_token(token(FAKE_MINT, name="Free airdrop reward", symbol="RWD"))
    check("bait-only -> suspicious", v["flag"] and v["severity"] == "suspicious")

    # open mint authority only (no price, no impersonation) -> suspicious
    mi = {FAKE_MINT: {"mint_authority": "Auth1", "freeze_authority": None, "supply": 1000}}
    v = classify_token(token(FAKE_MINT, name="Random Token", symbol="RND"), mint_info=mi)
    check("open-mint-only -> suspicious", v["flag"] and v["severity"] == "suspicious")

    # same token but priced -> clean
    v = classify_token(token(FAKE_MINT, name="Random Token", symbol="RND", price=True), mint_info=mi)
    check("priced token with mint auth -> clean", v["flag"] is False)

    # clean unknown token -> not flagged
    v = classify_token(token(FAKE_MINT, name="Acme Points", symbol="ACME"))
    check("clean unknown token", v["flag"] is False)

    # classify never raises on malformed input
    check("classify malformed never raises",
          classify_token({})["flag"] is False)

    print("== is_hidden_spam / is_suspicious ==")
    check("is_hidden_spam true", is_hidden_spam({"spam": {"flag": True, "severity": "spam"}}))
    check("is_hidden_spam false for suspicious",
          is_hidden_spam({"spam": {"flag": True, "severity": "suspicious"}}) is False)
    check("is_suspicious true", is_suspicious({"spam": {"flag": True, "severity": "suspicious"}}))

    print("== enrich_balance_result_with_spam_filter ==")
    result = [
        {
            "network": "https://api.mainnet-beta.solana.com",
            "sol": 1.0,
            "spl": [
                token(USDC, name="USD Coin", symbol="USDC", price=True),          # clean (known-good)
                token(FAKE_MINT, name="USD Coin", symbol="USDC"),                 # spam (impersonation)
                token(FAKE_MINT + "1", name="Acme", symbol="ACME", price=True),   # clean (priced)
            ],
        }
    ]
    summary = asyncio.run(enrich_balance_result_with_spam_filter(result, fetch_authorities=False))
    print(f"  summary: {summary}")
    t0, t1, t2 = result[0]["spl"]
    check("enrich total counted", summary["total"] == 3)
    check("enrich USDC clean", t0["spam"]["flag"] is False)
    check("enrich fake USDC flagged spam", is_hidden_spam(t1))
    check("enrich priced ACME clean", t2["spam"]["flag"] is False)

    # never raises on empty / malformed result
    check("enrich empty result",
          asyncio.run(enrich_balance_result_with_spam_filter([], fetch_authorities=False))["total"] == 0)

    print("== get_mint_authorities (batched, mocked RPC) ==")
    # verify it degrades gracefully (no real network in tests) by pointing at a
    # bogus endpoint — must return {} not raise.
    out = asyncio.run(get_mint_authorities([USDC], "http://127.0.0.1:1/bogus", timeout=1.0))
    check("authority fetch no-raise on failure", out == {})


if __name__ == "__main__":
    run()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
