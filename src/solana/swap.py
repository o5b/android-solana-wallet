"""Token swaps via Jupiter Swap API V2 (Meta-Aggregator /order path).

Flow:
    GET /swap/v2/order  -> assembled V0 transaction + quote (one call)
    sign in place       -> src.solana.versioned_transaction
    sendTransaction     -> own RPC (reuse existing infra)
    confirm             -> src.solana.transfer_sol.confirm_transaction

Jupiter's hosted API serves mainnet-beta only. Devnet has no DEX liquidity,
so swaps are mainnet-only by design.
"""
from __future__ import annotations

from typing import Any, Dict, List

import base64
import httpx

from solana.keypair import Keypair
from solana.versioned_transaction import (
    sign_base64,
    get_message_version,
    get_instruction_program_ids,
    extract_message,
)
from solana.transfer_sol import confirm_transaction

JUP_API = "https://api.jup.ag/swap/v2"
_MAINNET = "https://api.mainnet-beta.solana.com"

# Program IDs permitted in a swap transaction. Any instruction referencing a
# program outside this set is rejected before signing, so a tampered /order
# response that injects an unexpected program cannot be signed by the wallet.
ALLOWED_PROGRAM_IDS = {
    "11111111111111111111111111111111",  # System Program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token Account
    "ComputeBudget111111111111111111111111111111",  # Compute Budget (priority fees)
    "AddressLookupTab1e1111111111111111111111111",  # Address Lookup Table
    "SysvarRent111111111111111111111111111111111",  # Rent sysvar
    # Jupiter swap routers
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter v6
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33nhc5j5",  # Jupiter v4
    "JUP2jxvXaUg8GfvKjNTvU5d3ks7Yz8jvNcF8a6ZK7P5",  # Jupiter program
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def get_order(
    input_mint: str,
    output_mint: str,
    amount: int,
    taker: str,
    slippage_bps: int = 50,
) -> Dict[str, Any]:
    """Fetch a swap quote and an assembled, unsigned V0 transaction.

    Args:
        input_mint: input token mint (use wrapped-SOL mint for SOL swaps).
        output_mint: output token mint.
        amount: input amount in raw token units (lamports for SOL).
        taker: the signer / fee-payer public key (base58).
        slippage_bps: slippage tolerance in basis points (50 = 0.5%).

    Returns:
        Parsed /order response: includes ``transaction`` (base64 unsigned V0 tx),
        ``inAmount``, ``outAmount``, ``otherAmountThreshold``, ``requestId``,
        and fee breakdown fields.
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "taker": taker,
        "slippageBps": str(slippage_bps),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{JUP_API}/order", params=params, headers=_HEADERS)
    if resp.status_code != 200:
        raise Exception(f"Jupiter /order failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def get_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 50,
) -> Dict[str, Any]:
    """Fetch a quote only (no transaction). Uses the lite quote endpoint.

    Useful for showing expected output before the user confirms a swap.
    Returns parsed quote: ``inAmount``, ``outAmount``, ``otherAmountThreshold``,
    ``priceImpactPct``, ``routePlan``.
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": str(slippage_bps),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://lite-api.jup.ag/swap/v1/quote", params=params, headers=_HEADERS
        )
    if resp.status_code != 200:
        raise Exception(f"Jupiter quote failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def sign_order_transaction(order: Dict[str, Any], private_key_hex: str) -> bytes:
    """Sign the assembled transaction from a /order response.

    Args:
        order: response from ``get_order``.
        private_key_hex: the signer's 32-byte private key as 64 hex chars.

    Returns:
        Raw signed transaction bytes ready for ``sendTransaction``.
    """
    tx_b64 = order.get("transaction")
    if not tx_b64:
        raise ValueError("order response has no 'transaction' field")
    keypair = Keypair.from_seed(bytes.fromhex(private_key_hex))
    return sign_base64(tx_b64, keypair)


async def send_raw_transaction(raw_tx: bytes, network: str = _MAINNET) -> str:
    """Submit a raw signed transaction via RPC sendTransaction.

    Returns the transaction signature on success, raises on RPC error.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            base64.b64encode(raw_tx).decode("utf-8"),
            {"encoding": "base64", "max_retries": 0},
        ],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(network, json=payload)
    data = resp.json()
    if data.get("error"):
        raise Exception(f"sendTransaction RPC error: {data['error']}")
    return data["result"]


async def swap(
    input_mint: str,
    output_mint: str,
    amount: int,
    signer_address: str,
    private_key_hex: str,
    slippage_bps: int = 50,
    network: str = _MAINNET,
    confirm: bool = True,
) -> Dict[str, Any]:
    """Full swap: quote+order -> sign -> submit -> confirm.

    Args:
        input_mint: input token mint (wrapped-SOL mint for SOL input).
        output_mint: output token mint.
        amount: input amount in raw units.
        signer_address: the signer / fee-payer public key.
        private_key_hex: signer's 32-byte private key (64 hex chars).
        slippage_bps: slippage tolerance in basis points.
        network: RPC URL (mainnet-beta only for Jupiter).
        confirm: wait for transaction confirmation via confirm_transaction.

    Returns:
        Dict with signature, order details, and confirmation status.
    """
    order = await get_order(input_mint, output_mint, amount, signer_address, slippage_bps)

    wire = base64.b64decode(order["transaction"])
    message = extract_message(wire)
    version = get_message_version(message)

    program_ids = set(get_instruction_program_ids(message))
    unknown = program_ids - ALLOWED_PROGRAM_IDS
    if unknown:
        raise ValueError(
            f"order transaction contains untrusted program(s): {sorted(unknown)}; refusing to sign"
        )

    signed_raw = sign_order_transaction(order, private_key_hex)
    signature = await send_raw_transaction(signed_raw, network=network)

    result: Dict[str, Any] = {
        "signature": signature,
        "inAmount": order.get("inAmount"),
        "outAmount": order.get("outAmount"),
        "otherAmountThreshold": order.get("otherAmountThreshold"),
        "router": order.get("router"),
        "messageVersion": version,
        "requestId": order.get("requestId"),
    }

    if confirm:
        conf = await confirm_transaction(signature, network=network)
        result["confirmation"] = conf

    return result
