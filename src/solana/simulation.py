"""Transaction simulation & preview (anti-phishing / safety layer).

Complements the static `preview_transaction` (byte-level decode in
wallet_standard.py) with a *live* RPC simulation via `simulateTransaction`.
This is what catches dangerous transactions before the user signs them:

    - real SOL balance changes per account (who pays, who receives)
    - real SPL/Token-2022 balance changes (mint, owner, delta)
    - compute units (gas) + transaction fee
    - predicted success / failure with the program error
    - warnings for risky patterns (outflow to unknown accounts, unknown programs,
      token account drain)

Works on *unsigned* dApp transactions: simulation is run with
``sigVerify: false`` and ``replaceRecentBlockhash: true`` so a stale blockhash
or missing signatures never produce a false "failure".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import base64

import httpx

from solana.versioned_transaction import (
    extract_message,
    get_message_version,
    get_instruction_program_ids,
    _parse_account_keys,
)
from solana.wallet_standard import KNOWN_PROGRAMS, describe_program

LAMPORTS_PER_SOL = 1_000_000_000


# ---------------------------------------------------------------------------
# low-level RPC helper
# ---------------------------------------------------------------------------
async def _rpc(
    network: str,
    method: str,
    params: List[Any],
    *,
    client: Optional[httpx.AsyncClient] = None,
    max_429_retries: int = 4,
) -> Dict[str, Any]:
    """Call a Solana JSON-RPC method. Returns the parsed ``result`` value.

    Backs off on the public-RPC rate limit (HTTP 429 / JSON-RPC code 429) like
    ``transfer_sol.confirm_transaction`` does, so simulation on public endpoints
    is resilient instead of failing outright. A caller may pass a shared
    ``client`` to reuse one connection pool across several calls.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30)
    try:
        attempt = 0
        while True:
            resp = await client.post(network, json=payload)
            # HTTP-level 429
            if resp.status_code == 429:
                attempt += 1
                if attempt > max_429_retries:
                    raise Exception(f"{method} rate limited (HTTP 429) after {max_429_retries} retries")
                retry_after = float(resp.headers.get("retry-after") or min(2 ** attempt, 20))
                await _sleep(retry_after)
                continue
            try:
                data = resp.json()
            except Exception:
                # non-JSON body (some 429/5xx pages are HTML) — back off once
                attempt += 1
                if attempt > max_429_retries:
                    raise Exception(f"{method} non-JSON response (HTTP {resp.status_code})")
                await _sleep(min(2 ** attempt, 20))
                continue
            # protocol-level 429
            maybe_err = data.get("error")
            if isinstance(maybe_err, dict) and maybe_err.get("code") == 429:
                attempt += 1
                if attempt > max_429_retries:
                    raise Exception(f"{method} RPC error: {maybe_err}")
                await _sleep(min(2 ** attempt, 20))
                continue
            if maybe_err:
                raise Exception(f"{method} RPC error: {maybe_err}")
            return data["result"]
    finally:
        if own_client:
            await client.aclose()


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


async def get_fee_for_message(
    transaction_b64: str,
    network: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[int]:
    """Return the transaction fee (lamports) for a serialized tx, or None on error.

    Uses ``getFeeForMessage`` on the extracted message bytes.
    """
    raw = base64.b64decode(transaction_b64)
    message = extract_message(raw)
    return await get_fee_for_message_bytes(message, network, client=client)


async def get_fee_for_message_bytes(
    message: bytes,
    network: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[int]:
    """Like :func:`get_fee_for_message` but takes already-extracted message bytes.

    Lets :func:`analyze_transaction` reuse the message it has already decoded
    instead of re-decoding the wire transaction.
    """
    message_b64 = base64.b64encode(message).decode("ascii")
    try:
        result = await _rpc(network, "getFeeForMessage", [message_b64], client=client)
    except Exception:
        return None
    # result shape: {"context": {...}, "value": <int|null>}
    if isinstance(result, dict):
        return result.get("value")
    return result


async def simulate_transaction_raw(
    transaction_b64: str,
    network: str,
    *,
    sig_verify: bool = False,
    replace_recent_blockhash: bool = True,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Run ``simulateTransaction`` and return the raw ``result.value`` dict.

    Defaults are tuned for unsigned dApp transactions: signature verification is
    skipped and the blockhash is refreshed so the simulation reflects the real
    outcome instead of failing on a stale/unsigned precondition.
    """
    config: Dict[str, Any] = {
        "encoding": "base64",
        "sigVerify": sig_verify,
        "replaceRecentBlockhash": replace_recent_blockhash,
    }
    result = await _rpc(network, "simulateTransaction", [transaction_b64, config], client=client)
    return result.get("value", result)


# ---------------------------------------------------------------------------
# human-readable analysis
# ---------------------------------------------------------------------------
def _short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}" if len(addr) > 12 else addr


def _sol_change_rows(account_keys: List[str], pre: List[int], post: List[int], signer: Optional[str]) -> List[Dict[str, Any]]:
    """Map pre/post SOL balances to per-account deltas (non-zero only)."""
    rows: List[Dict[str, Any]] = []
    for idx, key in enumerate(account_keys):
        if idx >= len(post) or idx >= len(pre):
            continue
        delta = post[idx] - pre[idx]
        if delta == 0:
            continue
        rows.append({
            "account": key,
            "label": "you" if key == signer else describe_program(key),
            "delta_lamports": delta,
            "delta_sol": delta / LAMPORTS_PER_SOL,
            "direction": "out" if delta < 0 else "in",
        })
    return rows


def _token_change_rows(pre: List[Dict], post: List[Dict], account_keys: List[str], signer: Optional[str]) -> List[Dict[str, Any]]:
    """Compute SPL/Token-2022 balance deltas from preTokenBalances/postTokenBalances."""
    index_to_pre = {e.get("accountIndex"): e for e in pre or []}
    index_to_post = {e.get("accountIndex"): e for e in post or []}
    rows: List[Dict[str, Any]] = []
    for idx, entry in index_to_post.items():
        before = index_to_pre.get(idx)
        before_amt = int((before or {}).get("uiTokenAmount", {}).get("amount", 0)) if before else 0
        after_amt = int(entry.get("uiTokenAmount", {}).get("amount", 0))
        delta = after_amt - before_amt
        if delta == 0:
            continue
        decimals = int(entry.get("uiTokenAmount", {}).get("decimals", 0))
        owner = entry.get("owner")
        account_addr = account_keys[idx] if idx is not None and idx < len(account_keys) else entry.get("mint")
        rows.append({
            "account": account_addr,
            "mint": entry.get("mint"),
            "owner": owner,
            "is_yours": owner == signer,
            "delta_raw": delta,
            "delta_amount": delta / (10 ** decimals) if decimals else float(delta),
            "decimals": decimals,
            "direction": "out" if delta < 0 else "in",
        })
    # accounts that existed only in pre (fully drained / closed)
    for idx, entry in index_to_pre.items():
        if idx in index_to_post:
            continue
        before_amt = int(entry.get("uiTokenAmount", {}).get("amount", 0))
        if before_amt == 0:
            continue
        decimals = int(entry.get("uiTokenAmount", {}).get("decimals", 0))
        owner = entry.get("owner")
        rows.append({
            "account": account_keys[idx] if idx is not None and idx < len(account_keys) else entry.get("mint"),
            "mint": entry.get("mint"),
            "owner": owner,
            "is_yours": owner == signer,
            "delta_raw": -before_amt,
            "delta_amount": -before_amt / (10 ** decimals) if decimals else float(-before_amt),
            "decimals": decimals,
            "direction": "out",
        })
    return rows


def _build_warnings(
    status: str,
    error: Optional[str],
    sol_changes: List[Dict[str, Any]],
    token_changes: List[Dict[str, Any]],
    signer: Optional[str],
    unknown_programs: List[str],
) -> List[str]:
    warnings: List[str] = []
    if status == "error":
        warnings.append(f"Transaction will FAIL: {error or 'unknown error'}")
    if signer:
        outflow = sum(c["delta_sol"] for c in sol_changes if c["account"] == signer and c["delta_sol"] < 0)
        if outflow < 0:
            warnings.append(f"You will spend {abs(outflow):.9f} SOL (incl. fee)")
        for t in token_changes:
            if t.get("is_yours") and t["direction"] == "out":
                warnings.append(
                    f"You will send {abs(t['delta_amount'])} of token {_short(t['mint'])} away"
                )
    # outflow to a non-program, non-signer account
    for c in sol_changes:
        if c["direction"] == "in" and c["account"] != signer and c["account"] not in KNOWN_PROGRAMS:
            warnings.append(f"Recipient {_short(c['account'])} receives {c['delta_sol']:.9f} SOL")
    for pid in unknown_programs:
        warnings.append(f"Involves unverified program {_short(pid)} ({describe_program(pid)})")
    return warnings


async def analyze_transaction(
    transaction_b64: str,
    network: str,
    *,
    signer_pubkey: Optional[str] = None,
) -> Dict[str, Any]:
    """Simulate a transaction and return a human-friendly safety summary.

    Combines a live RPC simulation with the static program/account preview:

    Returns:
        ``{status, error, compute_units, fee_lamports, fee_sol, message_version,
        fee_payer, programs, unknown_programs, account_count, sol_changes,
        token_changes, logs, warnings}``
    """
    raw = base64.b64decode(transaction_b64)
    message = extract_message(raw)
    version = get_message_version(message)
    account_keys, _offset = _parse_account_keys(message)

    # static program preview (own decode so it works even if simulation fails)
    try:
        program_ids = list(set(get_instruction_program_ids(message)))
    except Exception:
        program_ids = []
    unknown = sorted({p for p in program_ids if p not in KNOWN_PROGRAMS})
    programs = sorted({describe_program(p) for p in program_ids})

    # One shared connection pool + parallel round-trips: fetch the fee (reusing
    # the already-decoded message) and the simulation concurrently.
    import asyncio
    async with httpx.AsyncClient(timeout=30) as client:
        fee_task = asyncio.create_task(get_fee_for_message_bytes(message, network, client=client))
        sim_task = asyncio.create_task(
            simulate_transaction_raw(
                transaction_b64, network,
                sig_verify=False, replace_recent_blockhash=True, client=client,
            )
        )
        fee_lamports = await fee_task
        try:
            sim = await sim_task
            sim_error: Optional[Exception] = None
        except Exception as e:  # noqa: BLE001
            # Degrade instead of raising: the static preview + fee are still
            # useful, and public-RPC 429s must not crash the caller.
            sim = {}
            sim_error = e

    fee_payer = account_keys[0] if account_keys else None

    if sim_error is not None:
        return {
            "status": "simulation_failed",
            "error": f"simulation unavailable: {sim_error}",
            "compute_units": None,
            "fee_lamports": fee_lamports,
            "fee_sol": (fee_lamports / LAMPORTS_PER_SOL) if fee_lamports is not None else None,
            "message_version": version,
            "fee_payer": fee_payer,
            "programs": programs,
            "unknown_programs": unknown,
            "account_count": len(account_keys),
            "sol_changes": [],
            "token_changes": [],
            "logs": [],
            "warnings": [f"Simulation unavailable: {sim_error}"] + (
                [f"Involves unverified program {_short(pid)} ({describe_program(pid)})"
                 for pid in unknown]
            ),
        }

    err = sim.get("err")
    status = "error" if err else "ok"
    err_text: Optional[str] = err if isinstance(err, str) else (str(err) if err else None)

    pre_bal = sim.get("preBalances") or []
    post_bal = sim.get("postBalances") or []
    sol_changes = _sol_change_rows(account_keys, pre_bal, post_bal, signer_pubkey)

    pre_tok = sim.get("preTokenBalances") or []
    post_tok = sim.get("postTokenBalances") or []
    token_changes = _token_change_rows(pre_tok, post_tok, account_keys, signer_pubkey)

    compute_units = sim.get("unitsConsumed")
    logs = sim.get("logs") or []

    warnings = _build_warnings(status, err_text, sol_changes, token_changes, signer_pubkey, unknown)

    return {
        "status": status,
        "error": err_text,
        "compute_units": compute_units,
        "fee_lamports": fee_lamports,
        "fee_sol": (fee_lamports / LAMPORTS_PER_SOL) if fee_lamports is not None else None,
        "message_version": version,
        "fee_payer": fee_payer,
        "programs": programs,
        "unknown_programs": unknown,
        "account_count": len(account_keys),
        "sol_changes": sol_changes,
        "token_changes": token_changes,
        "logs": logs,
        "warnings": warnings,
    }


__all__ = [
    "get_fee_for_message",
    "get_fee_for_message_bytes",
    "simulate_transaction_raw",
    "analyze_transaction",
]
