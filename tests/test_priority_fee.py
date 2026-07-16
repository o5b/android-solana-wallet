"""Tests for the priority-fee (ComputeBudget) feature.

Run with:  PYTHONPATH=src venv/bin/python tests/test_priority_fee.py

Covers:
  * SetComputeUnitLimit / SetComputeUnitPrice instruction encoding (exact bytes).
  * priority_fee_instructions() helper (auto=none / active / limit-less modes).
  * A full SOL transfer tx assembled WITH a priority fee includes the
    ComputeBudget program + the encoded price instruction (offline, no submit).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from solana.compute_budget import (
    COMPUTE_BUDGET_PROGRAM_ID,
    set_compute_unit_limit,
    set_compute_unit_price,
    priority_fee_instructions,
)
from solana.publickey import PublicKey
from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction
from solana.transfer_sol import get_blockhash  # noqa: F401  (ensures import path ok)

COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"


def test_program_id():
    assert str(COMPUTE_BUDGET_PROGRAM_ID) == COMPUTE_BUDGET
    print("OK  program id")


def test_set_compute_unit_limit_encoding():
    ix = set_compute_unit_limit(2000)
    assert str(ix.program_id) == COMPUTE_BUDGET
    assert ix.keys == []  # no accounts
    # [2 (discriminant)] + u32 LE of 2000
    assert ix.data == bytes([2, 208, 7, 0, 0]), ix.data.hex()
    print("OK  set_compute_unit_limit encoding")


def test_set_compute_unit_price_encoding():
    ix = set_compute_unit_price(5000)
    assert str(ix.program_id) == COMPUTE_BUDGET
    assert ix.keys == []
    # [3 (discriminant)] + u64 LE of 5000
    assert ix.data == bytes([3, 136, 19, 0, 0, 0, 0, 0, 0]), ix.data.hex()
    print("OK  set_compute_unit_price encoding")


def test_set_compute_unit_price_large():
    # 1_000_000 micro-lamports = 1 lamport/CU
    ix = set_compute_unit_price(1_000_000)
    assert ix.data[0] == 3
    import struct
    assert struct.unpack("<Q", ix.data[1:])[0] == 1_000_000
    print("OK  set_compute_unit_price large value")


def test_priority_fee_instructions_none():
    # Auto mode (0 / None / negative) => no instructions.
    assert priority_fee_instructions(0) == []
    assert priority_fee_instructions(None) == []
    assert priority_fee_instructions(-5) == []
    print("OK  priority_fee_instructions auto = none")


def test_priority_fee_instructions_active():
    ixs = priority_fee_instructions(5000, cu_limit=2000)
    assert len(ixs) == 2
    assert ixs[0].data[0] == 2  # limit first
    assert ixs[1].data[0] == 3  # price second
    print("OK  priority_fee_instructions active = [limit, price]")


def test_priority_fee_instructions_no_limit():
    ixs = priority_fee_instructions(5000)  # no cu_limit
    assert len(ixs) == 1
    assert ixs[0].data[0] == 3  # only price
    print("OK  priority_fee_instructions no-limit = [price]")


def test_sol_transfer_includes_priority_fee():
    """Assemble a SOL transfer tx with a priority fee and assert the CB ix present."""
    from solana.keypair import Keypair

    sender = Keypair.from_seed(bytes(range(32)))
    recipient = PublicKey("EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr")

    txn = Transaction()
    txn.recent_blockhash = "11111111111111111111111111111111"  # placeholder
    txn.fee_payer = sender.public_key
    txn.add(*priority_fee_instructions(5000, cu_limit=2000))
    txn.add(
        transfer(
            TransferParams(
                from_pubkey=sender.public_key,
                to_pubkey=recipient,
                lamports=100_000,
            )
        )
    )

    # The ComputeBudget program must be among the tx's referenced programs.
    program_ids = set()
    for ix in txn.instructions:
        program_ids.add(str(ix.program_id))
    assert COMPUTE_BUDGET in program_ids, program_ids

    # Exactly one SetComputeUnitPrice ix + one SetComputeUnitLimit ix.
    cb_ixs = [ix for ix in txn.instructions if str(ix.program_id) == COMPUTE_BUDGET]
    kinds = sorted(ix.data[0] for ix in cb_ixs)
    assert kinds == [2, 3], kinds

    # The message must compile + serialize cleanly (signing not required to inspect message).
    msg = txn.compile_message()
    wire = msg.serialize()
    assert len(wire) > 0
    print("OK  SOL transfer tx assembled with priority fee")


def run_all():
    test_program_id()
    test_set_compute_unit_limit_encoding()
    test_set_compute_unit_price_encoding()
    test_set_compute_unit_price_large()
    test_priority_fee_instructions_none()
    test_priority_fee_instructions_active()
    test_priority_fee_instructions_no_limit()
    test_sol_transfer_includes_priority_fee()
    print("\nALL OFFLINE TESTS PASSED")


if __name__ == "__main__":
    run_all()
