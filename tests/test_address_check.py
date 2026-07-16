"""Headless tests for address-poisoning detection (`solana.address_check`).

No network, no UI, no Flet — pure algorithm verification.

Run:
    PYTHONPATH=src venv/bin/python tests/test_address_check.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from solana.address_check import (
    normalize_address,
    hidden_char_positions,
    invalid_chars,
    is_base58_address,
    levenshtein,
    check_address_poisoning,
)

# A real-looking base58 address (W1 from devnet-wallets.txt).
REAL = "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz"
W2 = "EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr"

KNOWN = [
    {"address": REAL, "label": "My Wallet"},
    {"address": W2, "label": "Friend"},
]

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def main():
    print("== normalize / hidden chars ==")
    check("strips whitespace", normalize_address("  " + REAL + "  ") == REAL)
    check("strips zero-width space", normalize_address(REAL[:5] + "\u200b" + REAL[5:]) == REAL)
    check("strips multiple hidden", normalize_address("\ufeff" + REAL + "\u200d") == REAL)
    check("empty -> empty", normalize_address("") == "")

    print("== hidden_char_positions ==")
    hp = hidden_char_positions(REAL[:3] + "\u200b" + REAL[3:])
    check("detects one hidden at idx 3", hp == [(0x200B, 3)])
    check("no hidden in clean", hidden_char_positions(REAL) == [])

    print("== invalid_chars / homoglyphs ==")
    check("clean has none", invalid_chars(REAL) == [])
    check("cyrillic a detected", "а" in invalid_chars(REAL[:-1] + "а"))

    print("== is_base58_address ==")
    check("real is valid", is_base58_address(REAL))
    check("too short invalid", not is_base58_address("abc"))
    check("has space invalid", not is_base58_address(REAL + " "))
    check("has O invalid (base58 has no O)", not is_base58_address(REAL.replace(REAL[0], "O", 1)) if "O" not in REAL else True)

    print("== levenshtein ==")
    check("identical=0", levenshtein(REAL, REAL) == 0)
    check("kitten/sitting=3", levenshtein("kitten", "sitting") == 3)
    check("empty vs abc=3", levenshtein("", "abc") == 3)

    print("== check_address_poisoning: exact match (positive) ==")
    r = check_address_poisoning(REAL, KNOWN)
    check("exact match found", r["exact"] is not None and r["exact"]["address"] == REAL)
    check("no warnings on exact", r["warnings"] == [])
    check("valid", r["valid"])

    print("== check_address_poisoning: classic poisoning (same head+tail) ==")
    # Build a fake address sharing first 5 and last 5 chars but different middle.
    fake = REAL[:5] + "ZZZZZZZZZZZZZZZZZZZZZZZZZZZ" + REAL[-5:]
    # ensure it is still 32..44 chars and base58
    fake = fake[:len(REAL)]
    r = check_address_poisoning(fake, KNOWN)
    check("fake flagged as danger", r["has_danger"])
    check("fake not exact", r["exact"] is None)
    top = max((w["score"] for w in r["warnings"]), default=0)
    check("fake high score", top >= 100)

    print("== check_address_poisoning: zero-width injection ==")
    poisoned = REAL[:6] + "\u200b" + REAL[6:]
    r = check_address_poisoning(poisoned, KNOWN)
    check("hidden detected", r["hidden_chars"])
    check("hidden is danger", r["has_danger"])
    check("normalized back to real -> exact match too", r["exact"] is not None)

    print("== check_address_poisoning: homoglyph (Cyrillic а) ==")
    homo = REAL[:-1] + "а"  # latin ends replaced with cyrillic а
    r = check_address_poisoning(homo, KNOWN)
    check("homoglyph not valid base58", not r["valid"])
    check("homoglyph flagged danger", r["has_danger"])

    print("== check_address_poisoning: unrelated address (no warning) ==")
    # An unrelated but valid base58 of same length.
    unrelated = "1" * len(REAL)
    # '1' is valid base58; make it distinct enough from REAL
    r = check_address_poisoning(unrelated, KNOWN)
    check("unrelated valid", r["valid"])
    check("unrelated no danger", not r["has_danger"])
    # It may still produce a low 'warning' if it shares head/tail by chance with
    # an all-something string; that is acceptable. Just assert no danger.

    print("== check_address_poisoning: empty input ==")
    r = check_address_poisoning("", KNOWN)
    check("empty invalid", not r["valid"])
    check("empty no warnings", r["warnings"] == [])

    print("== check_address_poisoning: hidden char even without known set ==")
    r = check_address_poisoning(REAL[:4] + "\u200b" + REAL[4:], [])
    check("flags hidden with empty known", r["has_danger"])

    print("== check_address_poisoning: bare-string known entries ==")
    r = check_address_poisoning(REAL, [REAL])
    check("bare-string exact match", r["exact"] is not None)

    print("== robustness: None / dict missing fields ==")
    r = check_address_poisoning(None, [None, {}, {"address": REAL}])
    check("None input handled", r["valid"] is False)
    check("None input no exact", r["exact"] is None)
    r = check_address_poisoning(REAL, [None, {}, {"address": REAL}])
    check("sparse known handled exact", r["exact"] is not None)

    print()
    print(f"TOTAL: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
