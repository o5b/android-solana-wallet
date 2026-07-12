"""dApp signing capability layer (Wallet Standard primitives).

Implements the low-level signing surface that any Solana dApp transport
(WalletConnect v2, Solana Wallet Standard injection, manual/QR bridge) needs:

    - sign_message        : ed25519 sign of raw bytes (wallet-adapter `signMessage`)
    - verify_message      : ed25519 verify (for tests / incoming auth)
    - sign_transaction    : sign a serialized (legacy or V0) tx, return base64
    - sign_and_send_transaction : sign + broadcast + optional confirm
    - preview_transaction : human-readable summary (fee payer, programs, signers)
    - SIWS                : Sign In With Solana payload model + plaintext format
                            + sign_in_with_solana()

This module is transport-agnostic: it never talks to a dApp directly. It only
exposes the *capabilities*. The transport layer will call these functions and
relay the results.

Reuses the existing hand-rolled Solana primitives:
    - solana.keypair.Keypair           (ed25519)
    - solana.versioned_transaction     (message introspection + signing)
    - solana.swap.send_raw_transaction (broadcast)
    - solana.transfer_sol.confirm_transaction
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union

import base64
import base58

from nacl.signing import VerifyKey  # type: ignore
from nacl.exceptions import BadSignatureError  # type: ignore
from pydantic import BaseModel, ConfigDict, Field, field_validator

from solana.keypair import Keypair
from solana.utils import shortvec_encoding as shortvec
from solana.versioned_transaction import (
    sign_base64,
    get_message_version,
    get_fee_payer,
    get_instruction_program_ids,
    _parse_account_keys,
    _split_signatures,
)

# ---------------------------------------------------------------------------
# Known program registry (for preview_transaction annotation / safety flags)
#
# Canonical registry for the wallet. swap.ALLOWED_PROGRAM_ID is an explicit
# subset of this map; ``validate_program_registries()`` enforces that invariant
# so the "unverified program" verdict (here) and the "refuse to sign" verdict
# (swap) can never drift.
# ---------------------------------------------------------------------------
KNOWN_PROGRAMS: Dict[str, str] = {
    "11111111111111111111111111111111": "System Program",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "Token-2022",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "Associated Token Account",
    "ComputeBudget111111111111111111111111111111": "Compute Budget (priority fee)",
    "AddressLookupTab1e1111111111111111111111111": "Address Lookup Table",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr": "Memo",
    "Stake11111111111111111111111111111111111111": "Stake",
    "Vote111111111111111111111111111111111111111": "Vote",
    "BPFLoader2111111111111111111111111111111111": "BPF Loader 2",
    "BPFLoaderUpgradeab1e11111111111111111111111": "BPF Upgradeable Loader",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8btF18xhn": "Metaplex Token Metadata",
    "SysvarRent111111111111111111111111111111111": "Rent Sysvar",
    "SysvarC1ock11111111111111111111111111111111": "Clock Sysvar",
    # DEX / aggregators
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33nhc5j5": "Jupiter v4",
    "JUP2jxvXaUg8GfvKjNTvU5d3ks7Yz8jvNcF8a6ZK7P5": "Jupiter",
}


def validate_program_registries() -> None:
    """Assert that ``swap.ALLOWED_PROGRAM_ID`` is a subset of ``KNOWN_PROGRAMS``.

    Guards against silent drift between the "warn" verdict (simulation preview,
    uses KNOWN_PROGRAMS) and the "refuse to sign" verdict (swap, uses
    ALLOWED_PROGRAM_ID). Safe to call from tests / startup.
    """
    try:
        from solana.swap import ALLOWED_PROGRAM_ID
    except Exception:
        return  # swap not importable in this context; nothing to check
    missing = set(ALLOWED_PROGRAM_ID) - set(KNOWN_PROGRAMS)
    if missing:
        raise RuntimeError(
            f"swap.ALLOWED_PROGRAM_ID contains programs not in KNOWN_PROGRAMS: {sorted(missing)}"
        )


def describe_program(program_id: str) -> str:
    """Return a human-readable name for a known program id, else the raw id."""
    return KNOWN_PROGRAMS.get(program_id, program_id)


# ---------------------------------------------------------------------------
# Message signing (wallet-adapter `signMessage`)
# ---------------------------------------------------------------------------
def _to_keypair(private_key_hex: str) -> Keypair:
    seed = bytes.fromhex(private_key_hex)
    if len(seed) != 32:
        raise ValueError("private_key_hex must be 32 bytes (64 hex chars)")
    return Keypair.from_seed(seed)


def _as_bytes(message: Union[bytes, bytearray, str]) -> bytes:
    if isinstance(message, str):
        return message.encode("utf-8")
    if isinstance(message, (bytes, bytearray)):
        return bytes(message)
    raise TypeError(f"message must be bytes or str, got {type(message).__name__}")


def _looks_like_tx_message(b: bytes) -> bool:
    """Heuristic: do these bytes parse as a valid Solana transaction message?

    Guards ``sign_message`` against being misused as a transaction-signing
    oracle: a Solana fee-payer signature is ``ed25519(secret, message_bytes)``,
    so if a dApp submits a payload that *is* a valid transaction message, the
    returned signature could be assembled into a broadcastable transaction and
    would bypass every check in :func:`sign_transaction`.

    Uses *strict* bounds/structure checking (not the lenient introspection
    helpers) so that ordinary text / SIWS plaintext is not misclassified: the
    message must declare a number of account keys that actually fits, an
    instruction list that parses within bounds, and (for legacy) consume the
    buffer exactly. Legitimate ``signMessage`` payloads (text) almost never
    satisfy this, while a real transaction message always does.
    """
    if not isinstance(b, (bytes, bytearray)) or len(b) < 4:
        return False
    m = bytes(b)
    try:
        off = 0
        if get_message_version(m) == "v0":
            off = 1  # version prefix
        # header (3 bytes)
        if off + 3 > len(m):
            return False
        off += 3
        # account keys: compact-u16 count + count*32 bytes
        count, csize = shortvec.decode_length(m[off:])
        off += csize
        if off + count * 32 > len(m):
            return False
        off += count * 32
        # recent blockhash (32 bytes)
        if off + 32 > len(m):
            return False
        off += 32
        # instructions: compact-u16 count
        icount, isize = shortvec.decode_length(m[off:])
        off += isize
        for _ in range(icount):
            if off + 1 > len(m):
                return False
            off += 1  # program_id_index
            accts_len, asize = shortvec.decode_length(m[off:])
            off += asize + accts_len
            data_len, dsize = shortvec.decode_length(m[off:])
            off += dsize + data_len
            if off > len(m):
                return False
    except Exception:
        return False
    # legacy messages must be consumed exactly; v0 may have trailing ALT lookups
    if get_message_version(m) == "legacy" and off != len(m):
        return False
    return True


def sign_message(
    private_key_hex: str,
    message: Union[bytes, str],
    *,
    display: Optional[str] = None,
) -> Dict[str, Any]:
    """Sign an arbitrary message with the account's ed25519 key.

    Mirrors the Solana wallet-adapter ``signMessage`` capability: it signs the
    raw bytes (UTF-8-encoded if a ``str`` is passed) and returns the signature
    + public key. ``display`` is metadata only (used by hardware wallets) and is
    accepted for interface parity.

    Refuses to sign a payload that parses as a valid transaction message — that
    is what :func:`sign_transaction` is for, which applies fee-payer / single-
    signer / program checks first.

    Returns:
        ``{public_key, signature, signature_hex, message_utf8?}``
    """
    kp = _to_keypair(private_key_hex)
    msg = _as_bytes(message)
    if _looks_like_tx_message(msg):
        raise ValueError(
            "refusing to sign a payload that parses as a valid transaction "
            "message; use sign_transaction (which enforces fee-payer / single-"
            "signer / program checks) instead"
        )
    signature = kp.sign(msg).signature
    if len(signature) != 64:
        raise RuntimeError("invalid signature length")
    out: Dict[str, Any] = {
        "public_key": str(kp.public_key),
        "signature": base58.b58encode(signature).decode("ascii"),
        "signature_hex": signature.hex(),
    }
    if display is not None:
        out["display"] = display
    if isinstance(message, str):
        out["message_utf8"] = message
    return out


def verify_message(
    public_key_b58: str,
    message: Union[bytes, str],
    signature: Union[bytes, str],
) -> bool:
    """Verify an ed25519 signature. ``signature`` may be raw bytes or base58."""
    msg = _as_bytes(message)
    if isinstance(signature, str):
        sig_bytes = base58.b58decode(signature)
    else:
        sig_bytes = bytes(signature)
    try:
        VerifyKey(base58.b58decode(public_key_b58)).verify(msg, sig_bytes)
    except (BadSignatureError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Transaction signing (wallet-adapter `signTransaction` / `signAndSendTransaction`)
# ---------------------------------------------------------------------------
def preview_transaction(transaction_b64: str) -> Dict[str, Any]:
    """Decode a serialized transaction and return a human-readable summary.

    Does NOT sign or broadcast. Used by the UI to show the user *what* a dApp is
    asking them to sign before they approve it (the foundation for tx
    simulation / phishing protection).

    Returns:
        ``{version, fee_payer, account_count, required_signatures,
        readonly_signed, readonly_unsigned, signature_count, programs,
        unknown_programs}``
    """
    raw = base64.b64decode(transaction_b64)
    _sig_prefix, _sigs, sig_count, message_bytes = _split_signatures(raw)
    version = get_message_version(message_bytes)

    fee_payer = get_fee_payer(message_bytes)
    keys, _offset = _parse_account_keys(message_bytes)

    # Legacy/V0 message header is the 3 bytes right after the (optional) prefix.
    header_offset = 1 if version == "v0" else 0
    header = message_bytes[header_offset:header_offset + 3]
    required_sigs = header[0]

    program_ids: list = []
    try:
        program_ids = list(get_instruction_program_ids(message_bytes))
    except Exception:
        # V0 transactions whose instruction program ids resolve via an Address
        # Lookup Table raise here (the ALT is only resolved at execution time).
        # Degrade gracefully: the caller still gets the fee-payer / signer info.
        program_ids = []
    programs = {describe_program(pid) for pid in program_ids}
    unknown = sorted({pid for pid in program_ids if pid not in KNOWN_PROGRAMS})

    return {
        "version": version,
        "fee_payer": fee_payer,
        "account_count": len(keys),
        "accounts": keys,
        "required_signatures": required_sigs,
        "readonly_signed": header[1],
        "readonly_unsigned": header[2],
        "signature_count": sig_count,
        "programs": sorted(programs),
        "unknown_programs": unknown,
    }


def sign_transaction(
    private_key_hex: str,
    transaction_b64: str,
    *,
    require_single_signer: bool = True,
    allow_unknown_programs: bool = False,
) -> Dict[str, Any]:
    """Sign a serialized transaction (legacy or V0) WITHOUT broadcasting.

    Mirrors wallet-adapter ``signTransaction``: the dApp keeps the signed tx and
    may submit it itself, or combine it with other partial signatures.

    Args:
        transaction_b64: base64 wire transaction with placeholder signature(s).
        require_single_signer: if True, refuse multi-signer transactions
            (default — our account is expected to be the sole signer).
        allow_unknown_programs: by default, refuse to sign a transaction that
            invokes programs not in :data:`KNOWN_PROGRAMS` (parity with
            ``swap.swap``'s allowlist). Set True only after the caller has shown
            the user a simulation/preview and obtained explicit approval.

    Returns:
        ``{signed_transaction (base64), message_version, fee_payer, programs,
        unknown_programs}``
    """
    kp = _to_keypair(private_key_hex)
    preview = preview_transaction(transaction_b64)
    if require_single_signer and preview["required_signatures"] != 1:
        raise ValueError(
            f"transaction requires {preview['required_signatures']} signatures; "
            "refusing to sign multi-signer transaction"
        )
    if preview["fee_payer"] != str(kp.public_key):
        raise ValueError(
            f"transaction fee payer {preview['fee_payer']} does not match "
            f"signer {kp.public_key}"
        )
    if preview["unknown_programs"] and not allow_unknown_programs:
        raise ValueError(
            f"transaction invokes unverified program(s): {preview['unknown_programs']}; "
            "pass allow_unknown_programs=True after user-approved simulation"
        )
    signed_raw = sign_base64(transaction_b64, kp, require_single_signer=require_single_signer)
    return {
        "signed_transaction": base64.b64encode(signed_raw).decode("ascii"),
        "message_version": preview["version"],
        "fee_payer": preview["fee_payer"],
        "programs": preview["programs"],
        "unknown_programs": preview["unknown_programs"],
    }


async def sign_and_send_transaction(
    private_key_hex: str,
    transaction_b64: str,
    network: str,
    *,
    require_single_signer: bool = True,
    allow_unknown_programs: bool = False,
    confirm: bool = True,
    confirm_timeout: float = 60.0,
) -> Dict[str, Any]:
    """Sign a transaction and broadcast it via RPC (wallet-adapter
    ``signAndSendTransaction``).

    Args:
        transaction_b64: base64 unsigned wire transaction.
        network: RPC URL (devnet/testnet/mainnet-beta).
        require_single_signer: refuse multi-signer transactions when True.
        allow_unknown_programs: forward to :func:`sign_transaction`; default
            False refuses to broadcast transactions invoking unverified programs
            unless the caller approved them.
        confirm: wait for confirmation via ``confirm_transaction``.
        confirm_timeout: confirmation poll timeout in seconds.

    Returns:
        ``{signature, signed_transaction, message_version, fee_payer, programs,
        unknown_programs, confirmation?}``
    """
    from solana.swap import send_raw_transaction
    from solana.transfer_sol import confirm_transaction

    signed = sign_transaction(
        private_key_hex,
        transaction_b64,
        require_single_signer=require_single_signer,
        allow_unknown_programs=allow_unknown_programs,
    )
    raw_signed = base64.b64decode(signed["signed_transaction"])
    signature = await send_raw_transaction(raw_signed, network=network)
    result: Dict[str, Any] = {
        "signature": signature,
        "signed_transaction": signed["signed_transaction"],
        "message_version": signed["message_version"],
        "fee_payer": signed["fee_payer"],
        "programs": signed["programs"],
        "unknown_programs": signed["unknown_programs"],
    }
    if confirm:
        result["confirmation"] = await confirm_transaction(
            signature, network=network, timeout_seconds=confirm_timeout
        )
    return result


# ---------------------------------------------------------------------------
# Sign In With Solana (SIWS) — CAIP-122 / Phantom spec
# ---------------------------------------------------------------------------
class SIWSPayload(BaseModel):
    """Structured Sign In With Solana payload (CAIP-122).

    Accepts both snake_case and camelCase input via field aliases, so a dApp can
    send ``{"chainId": "mainnet-beta", "issuedAt": "..."}`` directly.
    """

    model_config = ConfigDict(populate_by_name=True)

    domain: str
    address: str
    statement: Optional[str] = None
    uri: str
    version: str = "1"
    nonce: str
    chain_id: str = Field(default="mainnet-beta", alias="chainId")
    issued_at: Optional[str] = Field(default=None, alias="issuedAt")
    expiration_time: Optional[str] = Field(default=None, alias="expirationTime")

    @field_validator("domain", "address", "statement", "uri", "version", "nonce", "chain_id")
    @classmethod
    def _no_newlines(cls, v: Optional[str]) -> Optional[str]:
        # CAIP-122 / EIP-4361 mandate single-line fields; embedded newlines let a
        # malicious dApp inject spoofed header/URI/nonce lines into the signed
        # plaintext. Reject CR/LF in every line-oriented field.
        if v is not None and ("\n" in v or "\r" in v):
            raise ValueError("SIWS field must not contain newline characters")
        return v


def format_siws_message(payload: SIWSPayload) -> str:
    """Render a SIWSPayload into the canonical plaintext that gets signed.

    Format (compatible with Phantom / Solana SIWS):

        <domain> wants you to sign in with your Solana account:
        <address>

        <statement>          (only if present, followed by a blank line)

        URI: <uri>
        Version: <version>
        Chain ID: <chain_id>
        Nonce: <nonce>
        Issued At: <issued_at>          (optional)
        Expiration Time: <expiration>   (optional)
    """
    lines = [
        f"{payload.domain} wants you to sign in with your Solana account:",
        payload.address,
        "",
    ]
    if payload.statement:
        lines += [payload.statement, ""]
    lines += [
        f"URI: {payload.uri}",
        f"Version: {payload.version}",
        f"Chain ID: {payload.chain_id}",
        f"Nonce: {payload.nonce}",
    ]
    if payload.issued_at:
        lines.append(f"Issued At: {payload.issued_at}")
    if payload.expiration_time:
        lines.append(f"Expiration Time: {payload.expiration_time}")
    return "\n".join(lines)


def sign_in_with_solana(private_key_hex: str, payload: Union[SIWSPayload, Dict[str, Any]]) -> Dict[str, Any]:
    """Sign a SIWS request: format the plaintext, sign it, return everything.

    Args:
        payload: a ``SIWSPayload`` or a dict (validated through the model).
        private_key_hex: signer's 32-byte private key (64 hex chars).

    Returns:
        ``{public_key, message, signature, signature_hex, payload}``
    """
    if not isinstance(payload, SIWSPayload):
        payload = SIWSPayload.model_validate(payload)
    if not payload.address:
        raise ValueError("SIWS payload requires an 'address'")

    # Bind the identity claim to the actual signing key: refuse to sign an
    # assertion about an address this key does not control.
    signer_address = str(_to_keypair(private_key_hex).public_key)
    if payload.address != signer_address:
        raise ValueError(
            f"SIWS address {payload.address} does not match signing key {signer_address}"
        )

    message = format_siws_message(payload)
    signed = sign_message(private_key_hex, message)
    return {
        "public_key": signed["public_key"],
        "message": message,
        "signature": signed["signature"],
        "signature_hex": signed["signature_hex"],
        "payload": payload.model_dump(by_alias=True),
    }


__all__ = [
    "KNOWN_PROGRAMS",
    "describe_program",
    "validate_program_registries",
    "sign_message",
    "verify_message",
    "preview_transaction",
    "sign_transaction",
    "sign_and_send_transaction",
    "SIWSPayload",
    "format_siws_message",
    "sign_in_with_solana",
]
