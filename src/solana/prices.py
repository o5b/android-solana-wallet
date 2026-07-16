"""USD price feeds via Jupiter Price API V3 (free, no key, mainnet).

Pricing is a global market value, not per-cluster: it is always pulled from
Jupiter's mainnet price endpoint regardless of which network the balance was
fetched on. USD values are only attached to **mainnet** balance entries,
because devnet/testnet holdings have no real-world value (a devnet SOL airdrop
is not worth $X even though the mainnet SOL price applies to the wrapped mint).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import asyncio

import httpx

JUP_PRICE_API = "https://api.jup.ag/price/v3"
MAINNET_RPC = "https://api.mainnet-beta.solana.com"

# Wrapped SOL mint — native SOL is priced through it.
SOL_MINT = "So11111111111111111111111111111111111111112"

# Cap ids per request so the query string stays well within URL limits.
_CHUNK = 50

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def get_prices(
    mints: Iterable[str],
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 15.0,
) -> Dict[str, Dict[str, float]]:
    """Fetch USD prices + 24h change for a set of token mints.

    Returns ``{mint: {"usd": float, "change_24h": float | None}}``. Mints with
    no known price are omitted. Never raises on network/parse errors — returns
    the partial/empty map so balance display never breaks.
    """
    mint_list = [m for m in dict.fromkeys(mints) if m]  # dedupe, drop empties
    if not mint_list:
        return {}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(headers=_HEADERS, timeout=timeout)
    try:
        async def _fetch_chunk(chunk: list) -> Dict[str, Dict[str, float]]:
            """One GET for a batch of mints -> {mint: {usd, change_24h}} ({} on error)."""
            out: Dict[str, Dict[str, float]] = {}
            try:
                resp = await client.get(
                    JUP_PRICE_API,
                    params={"ids": ",".join(chunk)},
                    headers=_HEADERS,
                )
                if resp.status_code != 200:
                    return out
                data = resp.json() or {}
            except Exception as er:
                print(f"prices.get_prices chunk error: {er}")
                return out
            for mint, info in (data or {}).items():
                if not isinstance(info, dict):
                    continue
                try:
                    usd = float(info.get("usdPrice"))
                except (TypeError, ValueError):
                    continue
                change = info.get("priceChange24h")
                try:
                    change = float(change) if change is not None else None
                except (TypeError, ValueError):
                    change = None
                out[mint] = {"usd": usd, "change_24h": change}
            return out

        # Chunks are independent GETs with disjoint ids — run them concurrently
        # so the round-trip count is 1 regardless of how many tokens there are.
        chunks = [mint_list[i:i + _CHUNK] for i in range(0, len(mint_list), _CHUNK)]
        batch_results = await asyncio.gather(
            *(_fetch_chunk(c) for c in chunks), return_exceptions=True
        )
        results: Dict[str, Dict[str, float]] = {}
        for br in batch_results:
            if isinstance(br, dict):
                results.update(br)
        return results
    finally:
        if owns_client:
            await client.aclose()


async def enrich_balance_result_with_prices(result: list) -> Dict[str, Any]:
    """Attach USD price/value to a ``get_sol_spl_balance`` result (mainnet only).

    Mutates each **mainnet** network entry and its tokens in place:
        network_result['sol_price'], ['sol_usd'], ['total_usd']
        token['usd_price'], ['usd_value'], ['change_24h']

    Returns ``{"total_usd": float, "priced": int, "tokens": int, "mainnet": bool}``.
    Never raises — a price fetch failure leaves the USD fields simply absent.
    """
    mainnet_entries = [nr for nr in result if nr.get("network") == MAINNET_RPC]
    if not mainnet_entries:
        return {"total_usd": 0.0, "priced": 0, "tokens": 0, "mainnet": False}

    mints = {SOL_MINT}
    for nr in mainnet_entries:
        mints.update(
            t.get("mint") for t in nr.get("spl", []) if t.get("mint")
        )

    prices = await get_prices(mints)
    sol_price = prices.get(SOL_MINT, {}).get("usd")

    grand_total = 0.0
    priced = 0
    tokens = 0
    for nr in mainnet_entries:
        net_total = 0.0
        if sol_price is not None and nr.get("sol"):
            nr["sol_price"] = sol_price
            nr["sol_usd"] = sol_price * nr["sol"]
            nr["sol_change_24h"] = prices.get(SOL_MINT, {}).get("change_24h")
            net_total += nr["sol_usd"]
        for t in nr.get("spl", []):
            tokens += 1
            if (t.get("amount") or 0) <= 0:
                continue
            p = prices.get(t.get("mint"))
            if not p:
                continue
            priced += 1
            t["usd_price"] = p["usd"]
            t["usd_value"] = p["usd"] * t["amount"]
            t["change_24h"] = p.get("change_24h")
            net_total += t["usd_value"]
        nr["total_usd"] = net_total
        grand_total += net_total

    return {
        "total_usd": grand_total,
        "priced": priced,
        "tokens": tokens,
        "mainnet": True,
    }


def fmt_usd(value: Optional[float]) -> str:
    """Format a USD amount; returns ``""`` when there is no value to show."""
    if value is None:
        return ""
    if value == 0:
        return "$0.00"
    if abs(value) < 0.01:
        s = f"{value:.6f}".rstrip("0").rstrip(".")
        return f"${s}"
    return f"${value:,.2f}"


def fmt_change(pct: Optional[float]) -> str:
    """Format a 24h percentage change, e.g. ``+1.23%`` / ``-0.50%``."""
    if pct is None:
        return ""
    return f"{pct:+.2f}%"
