"""Versioned (V0) transaction support for signing pre-built transactions.

Jupiter and other DEX aggregators return fully-built V0 transactions whose
message contains an Address Lookup Table (ALT) section. Resolving the ALT is the
validator's job at execution time — the *signer* only needs to sign the raw
serialized message bytes. This module therefore never resolves ALTs; it signs
the message in place, which is correct for both legacy and V0 transactions.

Wire format of a serialized transaction:
    [compact-u16 signature_count][signature_0 ... signature_N][message]

A "message" is either:
    legacy: [header(3)][account_keys][recent_blockhash][instructions]
    V0:     [0x80 prefix][header(3)][account_keys][recent_blockhash]
            [instructions][address_table_lookups]
"""
from __future__ import annotations

from typing import List, Tuple

import base64

from .keypair import Keypair
from .publickey import PublicKey
from .utils import shortvec_encoding as shortvec

PREFIX_BIT = 0x80
MESSAGE_VERSION_PREFIX = 1
LEGACY_VERSION = "legacy"


def _split_signatures(raw: bytes) -> Tuple[bytes, List[bytes], int, bytes]:
    """Split a wire transaction into signature-count, signatures, message.

    Returns:
        (sig_count_prefix_bytes, list_of_signature_bytes, count, message_bytes)
    """
    sig_count, count_size = shortvec.decode_length(raw)
    sig_prefix = raw[:count_size]
    offset = count_size
    sigs: List[bytes] = []
    for _ in range(sig_count):
        sigs.append(raw[offset : offset + 64])  # noqa: E203
        offset += 64
    message_bytes = raw[offset:]  # noqa: E203
    return sig_prefix, sigs, sig_count, message_bytes


def get_message_version(message_bytes: bytes) -> str:
    """Return the message version ('legacy' or 'v0')."""
    if message_bytes and message_bytes[0] & PREFIX_BIT:
        return "v0"
    return LEGACY_VERSION


def extract_message(wire: bytes) -> bytes:
    """Return the message bytes from a full serialized transaction.

    Strips the compact-u16 signature count and the signature bytes, returning
    everything that follows (the message that a signer signs verbatim).
    """
    _sig_prefix, _sigs, _count, message_bytes = _split_signatures(wire)
    return message_bytes


def get_fee_payer(message_bytes: bytes) -> str:
    """Return the base58 fee-payer (first account key) of the message.

    Works for both legacy and V0: skip the version prefix (if present) and the
    3-byte header, then the compact-u16 account-keys length, then read the first
    32-byte account key.
    """
    version = get_message_version(message_bytes)
    offset = MESSAGE_VERSION_PREFIX if version == "v0" else 0
    offset += 3  # header
    _account_count, length_size = shortvec.decode_length(message_bytes[offset:])
    offset += length_size
    fee_payer = message_bytes[offset : offset + PublicKey.LENGTH]  # noqa: E203
    return str(PublicKey(fee_payer))


def _parse_account_keys(message_bytes: bytes):
    """Return (account_keys, offset_after_keys) for a legacy or V0 message."""
    version = get_message_version(message_bytes)
    offset = MESSAGE_VERSION_PREFIX if version == "v0" else 0
    offset += 3  # header
    count, count_size = shortvec.decode_length(message_bytes[offset:])
    offset += count_size
    keys = []
    for _ in range(count):
        keys.append(str(PublicKey(message_bytes[offset : offset + PublicKey.LENGTH])))  # noqa: E203
        offset += PublicKey.LENGTH
    return keys, offset


def get_instruction_program_ids(message_bytes: bytes) -> List[str]:
    """Return the base58 program IDs referenced by each top-level instruction.

    Parses account keys, skips the 32-byte recent blockhash, then walks the
    instruction list reading each instruction's ``program_id_index`` into the
    account-keys table. Works for legacy and V0 messages (V0 address-table
    lookups live after the instructions and are not needed here).
    """
    keys, offset = _parse_account_keys(message_bytes)
    offset += PublicKey.LENGTH  # skip recent_blockhash
    instr_count, instr_size = shortvec.decode_length(message_bytes[offset:])
    offset += instr_size
    program_ids: List[str] = []
    for _ in range(instr_count):
        program_id_index = message_bytes[offset]
        offset += 1
        accts_len, accts_size = shortvec.decode_length(message_bytes[offset:])
        offset += accts_size + accts_len
        data_len, data_size = shortvec.decode_length(message_bytes[offset:])
        offset += data_size + data_len
        if program_id_index < len(keys):
            program_ids.append(keys[program_id_index])
        else:
            raise ValueError(
                f"instruction references program_id_index {program_id_index} "
                f"which resolves via Address Lookup Table — refusing to sign "
                f"unchecked program"
            )
    return program_ids


def sign_serialized(raw: bytes, keypair: Keypair, require_single_signer: bool = True) -> bytes:
    """Sign a pre-built serialized transaction (legacy or V0) in place.

    The caller must be the fee payer / sole required signer. The raw message
    bytes are signed verbatim (ed25519 signs its own hash internally), so ALTs
    do not need to be resolved here.

    Args:
        raw: the raw wire transaction bytes (as produced by an aggregator),
            containing zero-placeholder signatures.
        keypair: the signer keypair (must match the message's fee payer).
        require_single_signer: if True, assert the transaction needs exactly one
            signature (typical for aggregator swaps where the user is the only
            signer).

    Returns:
        A fully signed wire transaction (same length as the input).
    """
    sig_prefix, sigs, sig_count, message_bytes = _split_signatures(raw)
    if require_single_signer and sig_count != 1:
        raise ValueError(
            f"expected a single-signer transaction, got {sig_count} required signatures"
        )

    fee_payer = get_fee_payer(message_bytes)
    if fee_payer != str(keypair.public_key):
        raise ValueError(
            f"transaction fee payer {fee_payer} does not match signer {keypair.public_key}"
        )

    signature = keypair.sign(message_bytes).signature
    if len(signature) != 64:
        raise RuntimeError("invalid signature length")

    # Replace the first (fee-payer) placeholder signature with the real one.
    sigs[0] = signature

    out = bytearray()
    out.extend(sig_prefix)
    for s in sigs:
        out.extend(s)
    out.extend(message_bytes)
    return bytes(out)


def sign_base64(transaction_b64: str, keypair: Keypair, require_single_signer: bool = True) -> bytes:
    """Decode a base64-encoded serialized transaction, sign it, return raw bytes.

    Args:
        transaction_b64: base64-encoded wire transaction (as returned by
            Jupiter's swap endpoint).
        keypair: the signer keypair.

    Returns:
        Raw signed transaction bytes ready for ``sendTransaction``.
    """
    raw = base64.b64decode(transaction_b64)
    return sign_serialized(raw, keypair, require_single_signer=require_single_signer)
