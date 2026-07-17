"""Offline and read-only checks for the Liquid Staking convenience layer.

Run with: PYTHONPATH=src venv/bin/python tests/test_liquid_staking.py
"""
import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from solana import liquid_staking as lst
from solana.prices import SOL_MINT
from solana.swap import get_order


def test_registry_and_input_guards():
    assert lst.is_lst_mint(lst.LST_TOKENS["JitoSOL"][0])
    assert not lst.is_lst_mint(SOL_MINT)
    assert lst.lst_info(lst.LST_TOKENS["mSOL"][0])[0] == "mSOL"
    assert lst._amount_to_raw("1.000000001", 9) == 1_000_000_001

    for value in ("0", "-1", "0.0000000001"):
        try:
            lst._amount_to_raw(value, 9)
            raise AssertionError(f"{value} should fail")
        except ValueError:
            pass
    for bps in (0, 501, 1.5):
        try:
            lst._validate_slippage_bps(bps)
            raise AssertionError(f"{bps} should fail")
        except ValueError:
            pass
    print("OK  registry and input guards")


async def test_quote_and_swap_wrappers():
    calls = []

    async def fake_quote(input_mint, output_mint, amount, slippage_bps):
        calls.append((input_mint, output_mint, amount, slippage_bps))
        return {"outAmount": "1010000000", "otherAmountThreshold": "1000000000", "priceImpactPct": "0.001"}

    async def fake_prices(mints):
        return {SOL_MINT: {"usd": 100.0}, lst.LST_TOKENS["JitoSOL"][0]: {"usd": 102.0}}

    old_quote, old_prices = lst.jup_get_quote, lst.get_prices
    lst.jup_get_quote, lst.get_prices = fake_quote, fake_prices
    try:
        quote = await lst.get_stake_quote("JitoSOL", "1.000000001", slippage_bps=100)
    finally:
        lst.jup_get_quote, lst.get_prices = old_quote, old_prices
    assert calls == [(SOL_MINT, lst.LST_TOKENS["JitoSOL"][0], 1_000_000_001, 100)]
    assert quote["out_amount_lst"] == 1.01
    assert quote["min_out_lst"] == 1.0
    assert quote["sol_per_lst"] == 1.02

    async def fake_swap(*args, **kwargs):
        calls.append((args, kwargs))
        return {"signature": "test-signature", "outAmount": "2500000000"}

    old_swap = lst.jup_swap
    lst.jup_swap = fake_swap
    try:
        staked = await lst.stake_sol("mSOL", "1.25", "signer", "00" * 32, confirm=False)
        unstaked = await lst.unstake_sol("mSOL", "2.5", "signer", "00" * 32, confirm=False)
        assert staked["out_amount_lst"] == 2.5
        assert unstaked["out_amount_sol"] == 2.5
        try:
            await lst.stake_sol("mSOL", "1", "signer", "00" * 32, network="https://api.devnet.solana.com")
            raise AssertionError("non-mainnet stake should fail")
        except ValueError as er:
            assert "mainnet" in str(er)
    finally:
        lst.jup_swap = old_swap
    assert calls[-2][0][2] == 1_250_000_000
    assert calls[-1][0][2] == 2_500_000_000
    print("OK  quote and swap wrappers")


async def test_positions_aggregate_accounts():
    jito = lst.LST_TOKENS["JitoSOL"][0]

    async def fake_balances(address, network):
        return {jito: 1.25, "unrelated": 10.0}

    async def fake_prices(mints):
        return {SOL_MINT: {"usd": 100.0}, jito: {"usd": 102.0}}

    old_balances, old_prices = lst._get_token_balances, lst.get_prices
    lst._get_token_balances, lst.get_prices = fake_balances, fake_prices
    try:
        result = await lst.get_lst_positions("wallet")
    finally:
        lst._get_token_balances, lst.get_prices = old_balances, old_prices
    assert result["total_usd"] == 127.5
    assert result["positions"] == [{
        "symbol": "JitoSOL", "mint": jito, "provider": "Jito", "amount": 1.25,
        "usd_value": 127.5, "lst_price_usd": 102.0, "sol_per_lst": 1.02,
    }]
    print("OK  LST positions")


async def test_order_error_is_actionable():
    """A Jupiter HTTP-200 order error must not be treated as an empty transaction."""
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"error": "Insufficient funds", "transaction": ""}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    # ``swap`` owns the HTTP client reference; patch its module directly.
    from solana import swap
    old_swap_client = swap.httpx.AsyncClient
    swap.httpx.AsyncClient = FakeClient
    try:
        try:
            await get_order(SOL_MINT, lst.LST_TOKENS["JitoSOL"][0], 1, "wallet")
            raise AssertionError("empty Jupiter order should fail")
        except Exception as er:
            assert str(er) == "Jupiter /order error: Insufficient funds"
    finally:
        swap.httpx.AsyncClient = old_swap_client
    print("OK  actionable Jupiter order errors")


async def test_swap_retries_safe_order_only():
    """A transient untrusted route is skipped; only an allowlisted order signs."""
    from solana import swap

    calls = 0

    async def fake_order(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "transaction": base64.b64encode(b"fake transaction").decode(),
            "inAmount": "1",
            "outAmount": "2",
        }

    def fake_programs(message):
        return ["untrusted-program"] if calls == 1 else ["11111111111111111111111111111111"]

    async def fake_send(wire, network):
        assert wire == b"signed"
        return "signature"

    originals = {
        "get_order": swap.get_order,
        "extract_message": swap.extract_message,
        "get_message_version": swap.get_message_version,
        "get_instruction_program_ids": swap.get_instruction_program_ids,
        "sign_order_transaction": swap.sign_order_transaction,
        "send_raw_transaction": swap.send_raw_transaction,
        "confirm_transaction": swap.confirm_transaction,
    }
    swap.get_order = fake_order
    swap.extract_message = lambda wire: b"message"
    swap.get_message_version = lambda message: "v0"
    swap.get_instruction_program_ids = fake_programs
    swap.sign_order_transaction = lambda order, private_key: b"signed"
    swap.send_raw_transaction = fake_send
    try:
        result = await swap.swap("in", "out", 1, "signer", "00" * 32, confirm=False)
    finally:
        for name, original in originals.items():
            setattr(swap, name, original)
    assert calls == 2
    assert result["signature"] == "signature"
    assert result["messageVersion"] == "v0"
    print("OK  transient untrusted route is retried safely")


async def run_all():
    test_registry_and_input_guards()
    await test_quote_and_swap_wrappers()
    await test_positions_aggregate_accounts()
    await test_order_error_is_actionable()
    await test_swap_retries_safe_order_only()
    print("\nALL LIQUID STAKING OFFLINE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(run_all())
