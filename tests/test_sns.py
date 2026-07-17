"""Offline tests for Solana Name Service name resolution helpers.

Run:
    PYTHONPATH=src venv/bin/python tests/test_sns.py
"""
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from solana.sns import (
    SNS_PROGRAM_ID,
    SNSResolutionError,
    get_sns_name_account,
    normalize_sns_name,
    parse_sns_name_account,
)
from solana.publickey import PublicKey


ADDRESS = "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz"
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


def raises(fn):
    try:
        fn()
    except SNSResolutionError:
        return True
    return False


def main():
    print("== normalize_sns_name ==")
    check("normalizes case and whitespace", normalize_sns_name("  Bonfida.sol ") == "bonfida.sol")
    check("rejects missing suffix", raises(lambda: normalize_sns_name("bonfida")))
    check("rejects subdomain", raises(lambda: normalize_sns_name("a.b.sol")))
    check("rejects unicode", raises(lambda: normalize_sns_name("тест.sol")))

    print("== PDA ==")
    pda = get_sns_name_account("bonfida.sol")
    check("is a public key", len(bytes(PublicKey(pda))) == 32)
    check("is deterministic", pda == get_sns_name_account("BONFIDA.SOL"))
    check("matches official SNS SDK derivation", pda == "Crf8hzfthWGbGbLTVCiqRqV5MVnbpHB1L9KQMd6gsinb")

    print("== parse_sns_name_account ==")
    raw = b"\0" * 64 + bytes(PublicKey(ADDRESS)) + b"domain data"
    account = {"owner": SNS_PROGRAM_ID, "data": [base64.b64encode(raw).decode(), "base64"]}
    check("extracts owner address", parse_sns_name_account(account) == ADDRESS)
    check("rejects wrong program", raises(lambda: parse_sns_name_account({**account, "owner": ADDRESS})))
    check("rejects truncated data", raises(lambda: parse_sns_name_account({"owner": SNS_PROGRAM_ID, "data": ["AA==", "base64"]})))
    empty_owner = b"\0" * 96
    check("rejects unconfigured owner", raises(lambda: parse_sns_name_account({"owner": SNS_PROGRAM_ID, "data": [base64.b64encode(empty_owner).decode(), "base64"]})))

    print()
    print(f"TOTAL: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
