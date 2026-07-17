"""Liquid staking via swap (SOL <-> Liquid Staking Tokens).

Liquid staking = staking through Liquid Staking Tokens (JitoSOL, mSOL, bSOL,
jupSOL). Instead of delegating to the native Stake Program (which needs stake
accounts, epochs of warmup/cooldown, and rent), the user simply swaps SOL for an
LST. The LST appreciates against SOL over time — that appreciation IS the staking
yield, so there is no claim/withdraw instruction to run.

This module is a thin convenience layer over the existing Jupiter swap
(`solana.swap`) and price feeds (`solana.prices`):

    * STAKE   = swap(SOL_MINT  -> LST mint)
    * UNSTAKE = swap(LST mint  -> SOL_MINT)
    * yield   = LST price growing faster than SOL price (1 LST > 1 SOL over time)

All LST mints are a hand-curated allowlist (anti-phishing): the wallet will only
ever "stake" into these known tokens. Jupiter is mainnet-only (no DEX liquidity
on devnet/testnet), so the whole flow is mainnet-only — the same constraint as
the existing swap screen.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import httpx

from solana.swap import swap as jup_swap, get_quote as jup_get_quote
from solana.prices import get_prices, SOL_MINT

MAINNET_RPC = "https://api.mainnet-beta.solana.com"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# A liquid-staking swap should never be submitted with an effectively unlimited
# minimum output. Five percent leaves room for volatile routes without making a
# typo such as "100" silently acceptable.
MAX_SLIPPAGE_BPS = 500

# Curated Liquid Staking Token registry (symbol -> (mint, decimals, provider)).
# Only these known-good LSTs may ever be the target of a "stake" action.
# Mints verified on-chain (owned by SPL Token Program) + priced/tradable via Jupiter.
LST_TOKENS: Dict[str, tuple] = {
    "JitoSOL": ("J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", 9, "Jito"),
    "mSOL": ("mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", 9, "Marinade"),
    "bSOL": ("bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1", 9, "BlazeStake"),
    "jupSOL": ("jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v", 9, "Jupiter"),
}

# The set of LST mint addresses (anti-phishing allowlist).
LST_MINTS = {v[0] for v in LST_TOKENS.values()}

# Reverse lookup: mint -> (symbol, decimals, provider)
_LST_BY_MINT: Dict[str, tuple] = {v[0]: (sym, v[1], v[2]) for sym, v in LST_TOKENS.items()}


def is_lst_mint(mint: str) -> bool:
    """True if `mint` is one of the curated LST mints."""
    return mint in LST_MINTS


def lst_info(mint: str) -> Optional[tuple]:
    """Return (symbol, decimals, provider) for an LST mint, or None."""
    return _LST_BY_MINT.get(mint)


def _amount_to_raw(amount: float | str | Decimal, decimals: int) -> int:
    """Convert a user amount to base units without float rounding loss."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as er:
        raise ValueError("Invalid amount") from er
    if not value.is_finite() or value <= 0:
        raise ValueError("Amount must be greater than 0")

    raw_value = value * (Decimal(10) ** decimals)
    raw = raw_value.to_integral_value()
    if raw_value != raw:
        raise ValueError(f"Amount supports at most {decimals} decimal places")
    if raw <= 0:
        raise ValueError("Amount is too small")
    return int(raw)


def _validate_slippage_bps(slippage_bps: int) -> int:
    """Validate a bounded, non-zero slippage tolerance for a Jupiter order."""
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int):
        raise ValueError("Slippage must be an integer number of basis points")
    if not 1 <= slippage_bps <= MAX_SLIPPAGE_BPS:
        raise ValueError(f"Slippage must be between 1 and {MAX_SLIPPAGE_BPS} bps")
    return slippage_bps


def _require_mainnet(network: str) -> None:
    """Prevent a valid Jupiter mainnet order from being sent to another cluster."""
    if network != MAINNET_RPC:
        raise ValueError("Liquid staking is supported only on mainnet-beta")


async def get_stake_quote(
    lst_symbol: str, amount_sol: float, slippage_bps: int = 100
) -> Dict[str, Any]:
    """Quote staking `amount_sol` SOL into the LST `lst_symbol`.

    Read-only (mainnet): never sends a transaction. Returns the raw Jupiter quote
    plus convenience fields the UI shows:

        out_amount_lst      : expected LST output (human, float)
        min_out_lst         : minimum received after slippage (human, float)
        price_impact_pct    : route price impact (%)
        lst_price_usd       : current LST USD price (or None)
        sol_price_usd       : current SOL USD price (or None)
        sol_per_lst         : exchange rate — how many SOL 1 LST is worth
                              (the accumulated-yield indicator: > 1.0 = positive yield)
    """
    if lst_symbol not in LST_TOKENS:
        raise ValueError(f"Unknown LST: {lst_symbol}")
    lst_mint, lst_dec, _ = LST_TOKENS[lst_symbol]
    lamports = _amount_to_raw(amount_sol, 9)
    slippage_bps = _validate_slippage_bps(slippage_bps)

    q = await jup_get_quote(SOL_MINT, lst_mint, lamports, slippage_bps=slippage_bps)
    out_lst = int(q["outAmount"]) / (10 ** lst_dec)
    min_out = int(q["otherAmountThreshold"]) / (10 ** lst_dec)

    prices = await get_prices([SOL_MINT, lst_mint])
    sol_usd = prices.get(SOL_MINT, {}).get("usd")
    lst_usd = prices.get(lst_mint, {}).get("usd")
    sol_per_lst = (lst_usd / sol_usd) if (sol_usd and lst_usd) else None

    return {
        "quote": q,
        "out_amount_lst": out_lst,
        "min_out_lst": min_out,
        "price_impact_pct": float(q.get("priceImpactPct", 0)) * 100,
        "lst_price_usd": lst_usd,
        "sol_price_usd": sol_usd,
        "sol_per_lst": sol_per_lst,
    }


async def stake_sol(
    lst_symbol: str,
    amount_sol: float,
    signer_address: str,
    private_key_hex: str,
    slippage_bps: int = 100,
    network: str = MAINNET_RPC,
    confirm: bool = True,
) -> Dict[str, Any]:
    """Stake SOL into an LST: swap SOL -> LST mint (mainnet).

    The underlying ``swap.swap`` refuses to sign any transaction containing a
    program outside its allowlist, so a tampered Jupiter order cannot drain the
    wallet. Returns the swap result with ``lst_symbol`` and ``out_amount_lst``.
    """
    if lst_symbol not in LST_TOKENS:
        raise ValueError(f"Unknown LST: {lst_symbol}")
    _require_mainnet(network)
    lst_mint, lst_dec, _ = LST_TOKENS[lst_symbol]
    lamports = _amount_to_raw(amount_sol, 9)
    slippage_bps = _validate_slippage_bps(slippage_bps)

    res = await jup_swap(
        SOL_MINT, lst_mint, lamports, signer_address, private_key_hex,
        slippage_bps=slippage_bps, network=network, confirm=confirm,
    )
    res["lst_symbol"] = lst_symbol
    if res.get("outAmount"):
        res["out_amount_lst"] = int(res["outAmount"]) / (10 ** lst_dec)
    return res


async def unstake_sol(
    lst_symbol: str,
    amount_lst: float,
    signer_address: str,
    private_key_hex: str,
    slippage_bps: int = 100,
    network: str = MAINNET_RPC,
    confirm: bool = True,
) -> Dict[str, Any]:
    """Unstake an LST back to SOL: swap LST mint -> SOL (mainnet).

    This is the "withdraw" of liquid staking — no epoch cooldown, settles in one
    swap. Pass the human-readable LST amount (e.g. 0.5 JitoSOL).
    """
    if lst_symbol not in LST_TOKENS:
        raise ValueError(f"Unknown LST: {lst_symbol}")
    _require_mainnet(network)
    lst_mint, lst_dec, _ = LST_TOKENS[lst_symbol]
    raw = _amount_to_raw(amount_lst, lst_dec)
    slippage_bps = _validate_slippage_bps(slippage_bps)

    res = await jup_swap(
        lst_mint, SOL_MINT, raw, signer_address, private_key_hex,
        slippage_bps=slippage_bps, network=network, confirm=confirm,
    )
    res["lst_symbol"] = lst_symbol
    if res.get("outAmount"):
        res["out_amount_sol"] = int(res["outAmount"]) / 1_000_000_000
    return res


async def _get_token_balances(address: str, network: str = MAINNET_RPC) -> Dict[str, float]:
    """Return {mint: human_amount} for all SPL tokens held by `address`."""
    out: Dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for program_id in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        address,
                        {"programId": program_id},
                        {"encoding": "jsonParsed", "commitment": "confirmed"},
                    ],
                }
                resp = await client.post(network, json=payload)
                data = resp.json() or {}
                if data.get("error"):
                    continue
                for acct in data.get("result", {}).get("value", []):
                    info = (
                        acct.get("account", {})
                        .get("data", {})
                        .get("parsed", {})
                        .get("info", {})
                    )
                    mint = info.get("mint")
                    ta = info.get("tokenAmount", {})
                    amount = int(ta.get("amount", 0))
                    decimals = int(ta.get("decimals", 0))
                    if mint:
                        # A wallet can hold a mint in more than one token account.
                        # Aggregate them rather than showing only the last account.
                        human = amount / (10 ** decimals) if decimals else float(amount)
                        out[mint] = out.get(mint, 0.0) + human
    except Exception as er:
        print(f"_get_token_balances error: {er}")
    return out


async def get_lst_positions(address: str, network: str = MAINNET_RPC) -> Dict[str, Any]:
    """Fetch the wallet's LST holdings + current value (mainnet, read-only).

    Returns::

        {
          "positions": [{symbol, mint, provider, amount, usd_value,
                         lst_price_usd, sol_per_lst}],
          "total_usd": float,
          "sol_price_usd": float|None,
        }

    Positions are sorted by USD value descending. Never raises — a balance/price
    failure yields an empty position list.
    """
    balances = await _get_token_balances(address, network)
    held = {mint: amt for mint, amt in balances.items() if mint in LST_MINTS and amt > 0}

    prices = await get_prices([SOL_MINT] + list(held.keys()))
    sol_usd = prices.get(SOL_MINT, {}).get("usd")

    positions: List[Dict[str, Any]] = []
    total_usd = 0.0
    for mint, amt in held.items():
        sym, _dec, provider = _LST_BY_MINT[mint]
        lst_usd = prices.get(mint, {}).get("usd")
        usd_value = lst_usd * amt if (lst_usd is not None) else None
        sol_per_lst = (lst_usd / sol_usd) if (lst_usd and sol_usd) else None
        positions.append({
            "symbol": sym,
            "mint": mint,
            "provider": provider,
            "amount": amt,
            "usd_value": usd_value,
            "lst_price_usd": lst_usd,
            "sol_per_lst": sol_per_lst,
        })
        if usd_value:
            total_usd += usd_value

    positions.sort(key=lambda p: (p["usd_value"] or 0), reverse=True)
    return {"positions": positions, "total_usd": total_usd, "sol_price_usd": sol_usd}
