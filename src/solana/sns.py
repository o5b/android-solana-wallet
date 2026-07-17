"""Solana Name Service (.sol) name resolution."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

import base64
import httpx

from .publickey import PublicKey


SNS_PROGRAM_ID = "namesLPneVptA9Z5rqUDD9tMTWEJwofgaYwp8cawRkX"
SNS_ROOT_DOMAIN = "58PwtjSDuFHuUkYjH9BYnnQKHfwo9reZhC2zMJv9JPkx"
_HASH_PREFIX = b"SPL Name Service"
_NAME_HEADER_LEN = 96


class SNSResolutionError(ValueError):
    """Raised when a .sol name cannot be resolved to a wallet address."""


def normalize_sns_name(name: str) -> str:
    """Validate and normalize a bare .sol domain name."""
    normalized = (name or "").strip().lower()
    if not normalized.endswith(".sol"):
        raise SNSResolutionError("Solana Name Service names must end with .sol.")
    label = normalized[:-4]
    if not label or "." in label or len(label) > 64:
        raise SNSResolutionError("Invalid .sol name.")
    try:
        label.encode("ascii")
    except UnicodeEncodeError as err:
        raise SNSResolutionError(".sol names may contain ASCII characters only.") from err
    if any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in label):
        raise SNSResolutionError("Invalid .sol name.")
    return normalized


def get_sns_name_account(name: str) -> str:
    """Return the SNS name-account PDA for a top-level .sol domain."""
    normalized = normalize_sns_name(name)
    hashed_name = sha256(_HASH_PREFIX + normalized[:-4].encode("utf-8")).digest()
    program_id = PublicKey(SNS_PROGRAM_ID)
    # The .sol root domain is the parent seed for every top-level .sol name.
    name_account, _ = PublicKey.find_program_address(
        [hashed_name, bytes(PublicKey(0)), bytes(PublicKey(SNS_ROOT_DOMAIN))],
        program_id,
    )
    return str(name_account)


def parse_sns_name_account(account: dict[str, Any]) -> str:
    """Extract the owner public key from a base64-encoded SNS name account."""
    if not isinstance(account, dict) or account.get("owner") != SNS_PROGRAM_ID:
        raise SNSResolutionError("This account is not a Solana Name Service record.")
    data = account.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise SNSResolutionError("Invalid Solana Name Service account data.")
    try:
        raw = base64.b64decode(data[0], validate=True)
    except Exception as err:
        raise SNSResolutionError("Invalid Solana Name Service account data.") from err
    if len(raw) < _NAME_HEADER_LEN:
        raise SNSResolutionError("Invalid Solana Name Service account data.")
    try:
        owner = str(PublicKey(raw[64:96]))
    except ValueError as err:
        raise SNSResolutionError("Invalid owner in Solana Name Service record.") from err
    if owner == str(PublicKey(0)):
        raise SNSResolutionError("This .sol name does not have a wallet address.")
    return owner


async def resolve_sns_name(name: str, network: str, client: httpx.AsyncClient | None = None) -> str:
    """Resolve ``name.sol`` to its owning Solana wallet address.

    SNS domains are mainnet records. The caller must therefore supply the mainnet
    RPC endpoint; resolving on devnet or testnet would be misleading.
    """
    normalized = normalize_sns_name(name)
    if "mainnet" not in (network or "").lower():
        raise SNSResolutionError(".sol names are available on mainnet-beta only.")
    name_account = get_sns_name_account(normalized)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [name_account, {"encoding": "base64", "commitment": "confirmed"}],
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.post(network, json=payload)
        if response.status_code != 200:
            raise SNSResolutionError("Could not contact the Solana RPC to resolve this .sol name.")
        body = response.json()
        if body.get("error"):
            raise SNSResolutionError(f"SNS lookup failed: {body['error'].get('message', 'unknown RPC error')}")
        account = body.get("result", {}).get("value")
        if account is None:
            raise SNSResolutionError(f"No Solana Name Service record exists for {normalized}.")
        return parse_sns_name_account(account)
    except httpx.HTTPError as err:
        raise SNSResolutionError("Could not contact the Solana RPC to resolve this .sol name.") from err
    finally:
        if own_client:
            await client.aclose()
