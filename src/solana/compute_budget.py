"""Solana ComputeBudget program instructions (priority fees).

The ComputeBudget program (`ComputeBudget111111111111111111111111111111`)
lets a transaction advertise its compute-unit limit and a per-CU price in
micro-lamports (the "priority fee"). Validators order transactions by this
price, so a higher price lands sooner in congested slots.

Instruction discriminants (single u8 prefix, little-endian args):
    0 = RequestHeapFrame(frames: u32)
    2 = SetComputeUnitLimit(units: u32)
    3 = SetComputeUnitPrice(micro_lamports: u64)
    4 = SetLoadedAccountsDataSizeLimit(limit: Option<u32>)

These instructions take NO accounts — only a program id + data — so they are
cheap and simply prepend to any transaction's instruction list.

Priority fee actually charged = (compute units *consumed*) * micro_lamports / 1_000_000.
The unit *limit* is a scheduling cap (avoids "compute budget exceeded"); the
unit *price* is what each consumed CU is worth in micro-lamports.
"""
from __future__ import annotations

import struct
from typing import List

from solana.publickey import PublicKey
from solana.transaction import TransactionInstruction

COMPUTE_BUDGET_PROGRAM_ID: PublicKey = PublicKey(
    "ComputeBudget111111111111111111111111111111"
)

# Discriminants (u8) of the ComputeBudget instruction variants.
_REQUEST_HEAP_FRAME = 0
_SET_COMPUTE_UNIT_LIMIT = 2
_SET_COMPUTE_UNIT_PRICE = 3


def set_compute_unit_limit(units: int) -> TransactionInstruction:
    """SetComputeUnitLimit (discriminant 2): max CUs this transaction may spend."""
    if units < 0:
        raise ValueError("compute unit limit must be >= 0")
    data = struct.pack("<BI", _SET_COMPUTE_UNIT_LIMIT, int(units))
    return TransactionInstruction(keys=[], program_id=COMPUTE_BUDGET_PROGRAM_ID, data=data)


def set_compute_unit_price(micro_lamports: int) -> TransactionInstruction:
    """SetComputeUnitPrice (discriminant 3): per-CU price in micro-lamports."""
    if micro_lamports < 0:
        raise ValueError("compute unit price must be >= 0")
    data = struct.pack("<BQ", _SET_COMPUTE_UNIT_PRICE, int(micro_lamports))
    return TransactionInstruction(keys=[], program_id=COMPUTE_BUDGET_PROGRAM_ID, data=data)


def priority_fee_instructions(
    micro_lamports: int | None,
    cu_limit: int | None = None,
) -> List[TransactionInstruction]:
    """Build the ComputeBudget instructions for a priority fee.

    Args:
        micro_lamports: per-CU price in micro-lamports (0 / None / negative => no fee).
        cu_limit: optional compute-unit cap; emitted as SetComputeUnitLimit when
            the price is active. Omit to leave the cluster default (200k / ix).

    Returns:
        A list (possibly empty) to ``txn.add(*...)`` before other instructions.
        Order: limit first, then price — the canonical placement.
    """
    price = int(micro_lamports or 0)
    if price <= 0:
        return []
    ixs: List[TransactionInstruction] = []
    if cu_limit is not None and cu_limit > 0:
        ixs.append(set_compute_unit_limit(cu_limit))
    ixs.append(set_compute_unit_price(price))
    return ixs
