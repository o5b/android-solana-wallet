"""Headless tests for the Burn / Close token-account feature.

Covers:
  1. Instruction encoding (BURN / CLOSE_ACCOUNT) — offline, deterministic.
  2. ATA derivation determinism.
  3. `get_ata_raw_amount` chain read (readonly devnet — verifies the exact
     on-chain balance that a full burn would consume).
  4. Full burn + close transaction ASSEMBLY + SIGNING without submission
     (validates keypair/blockhash/serialization; never sends).
  5. (OPT-IN, destructive) actually burn + close W1's devnet token end-to-end.

Destructive step 5 only runs with RUN_DESTRUCTIVE=1 and DESTROYS W1's token
holding for the test mint (recoverable later by transferring it back).

Run:
    PYTHONPATH=src venv/bin/python tests/test_burn_close.py
"""
import asyncio
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.transaction import Transaction
from solana.transfer_sol import get_blockhash
from solana.spl_token import (
    TOKEN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    burn_instruction,
    close_account_instruction,
    get_associated_token_address,
    get_ata_raw_amount,
    burn_and_close_token_account,
)

NET = "https://api.devnet.solana.com"
# Token-2022 mint both devnet wallets hold (9 decimals).
MINT = "Ejxf4ZKJnyCbgHdEAkWhaR7qjGvT7vpMYxiAeWyLG62b"


def _load_env(path):
    vals = {}
    if not os.path.exists(path):
        return vals
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def test_instruction_encoding():
    src = PublicKey("6qZZfzPn7n7gQ9QhJz8n3uJ4r2oA5wYbXcDvEkLmNpR")
    mint = PublicKey("9qFXrPyZqMiX5eAWr7Fxm1mWb3b4ZkSnVhJtYoUdPmEa")
    owner = PublicKey("AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz")

    ix = burn_instruction(src, mint, owner, 5000, TOKEN_PROGRAM_ID)
    assert ix.program_id == TOKEN_PROGRAM_ID
    assert ix.data[0] == 8, "BURN instruction type byte"
    assert struct.unpack("<q", ix.data[1:9])[0] == 5000
    assert len(ix.keys) == 3
    assert ix.keys[0].pubkey == src and ix.keys[0].is_writable and not ix.keys[0].is_signer
    assert ix.keys[1].pubkey == mint and ix.keys[1].is_writable and not ix.keys[1].is_signer
    assert ix.keys[2].pubkey == owner and ix.keys[2].is_signer and not ix.keys[2].is_writable

    cix = close_account_instruction(src, owner, owner, TOKEN_PROGRAM_ID)
    assert cix.program_id == TOKEN_PROGRAM_ID
    assert cix.data == bytes([9]), "CLOSE_ACCOUNT is a single type byte (no args)"
    assert len(cix.keys) == 3
    assert cix.keys[0].pubkey == src and cix.keys[0].is_writable          # account to close
    assert cix.keys[1].pubkey == owner and cix.keys[1].is_writable        # refund destination
    assert cix.keys[2].pubkey == owner and cix.keys[2].is_signer          # authority
    print("1. instruction encoding OK (BURN + CLOSE_ACCOUNT)")


def test_ata_derivation():
    owner = PublicKey("AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz")
    mint = PublicKey(MINT)
    ata = get_associated_token_address(owner, mint, TOKEN_2022_PROGRAM_ID)
    assert get_associated_token_address(owner, mint, TOKEN_2022_PROGRAM_ID) == ata  # deterministic
    assert len(str(ata)) > 30
    print("2. ATA derivation deterministic OK:", str(ata))


async def test_get_ata_raw_amount(w1):
    ata, raw = await get_ata_raw_amount(
        PublicKey(w1), PublicKey(MINT), TOKEN_2022_PROGRAM_ID, NET
    )
    # ATA matches the derivation.
    expected_ata = get_associated_token_address(PublicKey(w1), PublicKey(MINT), TOKEN_2022_PROGRAM_ID)
    assert ata == expected_ata, (ata, expected_ata)
    assert raw > 0, f"W1 expected to hold the token on devnet, got raw amount {raw}"
    print(f"3. get_ata_raw_amount OK — ATA {str(ata)} holds {raw} base units "
          f"({raw / 1e9:.9f} with 9 decimals)")
    return raw, ata


async def _get_blockhash_with_retry():
    last = None
    for _ in range(5):
        bh = await get_blockhash(NET)
        if bh:
            return bh
        await asyncio.sleep(2)
    raise RuntimeError("Failed to fetch blockhash from devnet after retries")


async def test_assemble_and_sign_no_submit(w1, w1_priv, raw_amount, ata):
    """Assemble + sign a real burn+close tx but DO NOT submit it."""
    kp = Keypair.from_seed(bytes.fromhex(w1_priv))
    owner = PublicKey(w1)
    mint = PublicKey(MINT)
    prog = TOKEN_2022_PROGRAM_ID

    blockhash = await _get_blockhash_with_retry()
    tx = Transaction(recent_blockhash=blockhash, fee_payer=owner)
    tx.add(burn_instruction(ata, mint, owner, raw_amount, prog))
    tx.add(close_account_instruction(ata, owner, owner, prog))
    tx.sign(kp)

    wire = tx.serialize()
    assert wire and len(wire) > 0
    sig = tx.signature()
    assert sig is not None and len(sig) == 64, "tx must carry a 64-byte payer signature"
    print(f"4. burn+close tx assembled + signed OK (wire {len(wire)} bytes, NOT submitted)")


async def test_destructive_burn_and_close(w1, w1_priv):
    """OPT-IN: actually burn W1's full balance and close the ATA on devnet."""
    result = await burn_and_close_token_account(
        owner_address=w1,
        owner_private_key=w1_priv,
        mint_address=MINT,
        network=NET,
        program_id=str(TOKEN_2022_PROGRAM_ID),
    )
    if "error" in result:
        raise AssertionError(f"burn_and_close failed: {result['error']}")
    # After success the ATA must be gone (raw amount 0 / account nonexistent).
    ata, raw = await get_ata_raw_amount(
        PublicKey(w1), PublicKey(MINT), TOKEN_2022_PROGRAM_ID, NET
    )
    assert raw == 0, f"account should be closed / empty, still holds {raw}"
    print("5. DESTRUCTIVE burn + close OK — rent refunded, account closed")


async def main():
    env = _load_env(os.path.join(os.path.dirname(__file__), "..", "devnet-wallets.txt"))
    w1 = env.get("W1_ADDR")
    w1_priv = env.get("W1_PRIV")
    if not w1 or not w1_priv:
        print("SKIP devnet tests: devnet-wallets.txt missing W1_ADDR/W1_PRIV")
        test_instruction_encoding()
        test_ata_derivation()
        print("\nOffline tests PASSED")
        return

    test_instruction_encoding()
    test_ata_derivation()
    raw_amount, ata = await test_get_ata_raw_amount(w1)
    await test_assemble_and_sign_no_submit(w1, w1_priv, raw_amount, ata)

    if os.environ.get("RUN_DESTRUCTIVE") == "1":
        print("\n--- RUN_DESTRUCTIVE=1: executing real burn+close on W1 ---")
        await test_destructive_burn_and_close(w1, w1_priv)
    else:
        print("\n(set RUN_DESTRUCTIVE=1 to execute the real burn+close end-to-end)")

    print("\nALL BURN/CLOSE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
