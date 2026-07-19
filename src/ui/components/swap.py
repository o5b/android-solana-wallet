"""Swap page (Jupiter) — extracted from ``main.py`` (Phase 7 Group 6b).

Owns the swap screen: pick an input/output token + amount + slippage -> get a
Jupiter quote -> execute the swap (signs with the wallet's private key).
Mainnet-only (Jupiter's hosted API serves mainnet-beta).

Coupling
--------
Swap signing needs the wallet's private key. Rather than reaching back into
``main.py``'s ``get_wallet_private_key``/``has_wallet_private_key`` legacy
closures (which depend on the live unlock state), this module resolves the
signer key via ``ctx.get_wallet_private_key`` / ``ctx.has_wallet_private_key``
— the :class:`~ui.context.AppContext` accessors added in Phase 7 Group 3.
They are behaviourally identical to the legacy closures (``""`` while locked,
else decrypt on demand via :func:`solana.security.get_secret`).

The ``el_swap_page`` holder Column + the shared view chrome (AppBar back
button, navbar) are read from ``ctx.controls`` (registered by ``main.py``
during bootstrap). No outbound navigation to other pages — the page is
self-contained (quote / swap render inline into ``el_swap_page``).

The button ``data``-dict contract is preserved byte-for-byte (``wallet_data`` /
``network`` / ``sol_amount`` / ``wallet_address``), so the balance-screen call
site is untouched.
"""

from decimal import Decimal, ROUND_HALF_UP

import flet

from solana.swap import get_quote as jup_get_quote, swap as jup_swap
from solana.validators import is_valid_amount
from ui.context import AppContext

# Mainnet token registry for the swap screen (symbol -> (mint, decimals)).
# Jupiter's hosted API serves mainnet-beta only, so swaps are mainnet-only.
SWAP_TOKENS = {
    "SOL": ("So11111111111111111111111111111111111111112", 9),
    "USDC": ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
    "USDT": ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
    "JUP": ("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
}

_MAINNET = "https://api.mainnet-beta.solana.com"


async def go_to_swap_page_click(ctx: AppContext, e) -> None:
    """Entry handler for the balance-screen "Swap" button.

    Reads the clicked button's ``data`` dict (``wallet_data`` + ``network`` +
    ``sol_amount`` + ``wallet_address``), validates that the network is
    mainnet-beta and the wallet has a usable private key, then builds the swap
    form (token dropdowns / amount + slippage fields / Get Quote + Swap buttons
    / quote text) into ``ctx.controls["el_swap_page"]`` and navigates to
    ``swap-page``.
    """
    page = ctx.page
    data = e.control.data
    if data['network'] != _MAINNET:
        page.show_dialog(
            flet.AlertDialog(title=flet.Text("Swaps are only supported on mainnet-beta."))
        )
        return
    if not ctx.has_wallet_private_key(data['wallet_data']):
        page.show_dialog(
            flet.AlertDialog(title=flet.Text("Swap needs the wallet's private key. Recover the wallet with its secret to enable swaps."))
        )
        return
    dd_in = flet.Dropdown(
        label="Input token", value="SOL", width=200,
        options=[flet.dropdown.Option(sym) for sym in SWAP_TOKENS],
    )
    dd_out = flet.Dropdown(
        label="Output token", value="USDC", width=200,
        options=[flet.dropdown.Option(sym) for sym in SWAP_TOKENS],
    )
    tf_amount = flet.TextField(label="Amount", width=200, max_length=30)
    tf_slippage = flet.TextField(label="Slippage %", value="1.0", width=120, max_length=6)
    txt_quote = flet.Text(value="Enter an amount and press Get Quote.", selectable=True)
    # store the last quote + the exact inputs it was computed for
    await_holder = {"quote": None, "in_raw": None, "in_sym": None, "out_sym": None, "amount_str": None, "slippage_bps": None}

    def _parse_slippage_bps() -> int:
        try:
            pct = float((tf_slippage.value or "1").strip() or "1")
        except ValueError:
            pct = 1.0
        return max(1, int(round(pct * 100)))

    async def get_quote_button_click(ev):
        print(f"[SWAP] get_quote clicked: in={dd_in.value} out={dd_out.value} amount={tf_amount.value}")
        try:
            if dd_in.value == dd_out.value:
                txt_quote.value = "Input and output tokens must differ."
                page.update(); return
            amount_str = (tf_amount.value or "").strip()
            if not is_valid_amount(amount_str):
                txt_quote.value = "Invalid amount."; page.update(); return
            decimals = SWAP_TOKENS[dd_in.value][1]
            in_raw = int((Decimal(amount_str) * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_HALF_UP))
            if in_raw <= 0:
                txt_quote.value = "Amount must be greater than 0."; page.update(); return
            slippage_bps = _parse_slippage_bps()
            in_mint = SWAP_TOKENS[dd_in.value][0]
            out_mint = SWAP_TOKENS[dd_out.value][0]
            txt_quote.value = "Fetching quote..."
            page.update()
            q = await jup_get_quote(in_mint, out_mint, in_raw, slippage_bps=slippage_bps)
            print(f"[SWAP] quote ok: outAmount={q.get('outAmount')} threshold={q.get('otherAmountThreshold')}")
            out_decimals = SWAP_TOKENS[dd_out.value][1]
            out_ui = int(q["outAmount"]) / (10 ** out_decimals)
            min_out = int(q["otherAmountThreshold"]) / (10 ** out_decimals)
            txt_quote.value = (
                f"{amount_str} {dd_in.value} -> {out_ui:.6f} {dd_out.value}\n"
                f"Min received (with slippage): {min_out:.6f} {dd_out.value}\n"
                f"Price impact: {float(q.get('priceImpactPct', 0)) * 100:.3f}%"
            )
            await_holder["quote"] = q
            await_holder["in_raw"] = in_raw
            await_holder["in_sym"] = dd_in.value
            await_holder["out_sym"] = dd_out.value
            await_holder["amount_str"] = amount_str
            await_holder["slippage_bps"] = slippage_bps
            page.update()
        except Exception as er:
            import traceback
            print(f"[SWAP] quote ERROR: {er}\n{traceback.format_exc()}")
            txt_quote.value = f"Quote error: {er}"
            page.update()

    async def swap_button_click(ev):
        print(f"[SWAP] swap clicked: in={dd_in.value} out={dd_out.value} quote_cached={await_holder['quote'] is not None}")
        try:
            if await_holder["quote"] is None:
                txt_quote.value = "Press Get Quote first."; page.update(); return
            if dd_in.value == dd_out.value:
                txt_quote.value = "Input and output tokens must differ."; page.update(); return
            # Refuse to swap if the inputs changed since the quote was taken:
            # the cached in_raw is scaled to the quoted token's decimals and
            # reusing it with a different token would swap the wrong amount.
            changed = (
                dd_in.value != await_holder["in_sym"]
                or dd_out.value != await_holder["out_sym"]
                or (tf_amount.value or "").strip() != await_holder["amount_str"]
                or _parse_slippage_bps() != await_holder["slippage_bps"]
            )
            if changed:
                txt_quote.value = "Inputs changed since the quote. Press Get Quote again, then Swap."; page.update(); return
            ev.control.disabled = True
            txt_quote.value = "Swapping... please wait"
            page.update()
            in_mint = SWAP_TOKENS[dd_in.value][0]
            out_mint = SWAP_TOKENS[dd_out.value][0]
            res = await jup_swap(
                input_mint=in_mint,
                output_mint=out_mint,
                amount=await_holder["in_raw"],
                signer_address=data['wallet_data']['address_base58'],
                private_key_hex=ctx.get_wallet_private_key(data['wallet_data']),
                slippage_bps=await_holder["slippage_bps"],
                network=_MAINNET,
            )
            print(f"[SWAP] swap result: sig={res['signature']} outAmount={res.get('outAmount')}")
            out_decimals = SWAP_TOKENS[dd_out.value][1]
            out_ui = int(res["outAmount"]) / (10 ** out_decimals)
            conf = res.get("confirmation", {}).get("result", {}).get("value", [{}])[0]
            status = conf.get("confirmationStatus") if conf else "unknown"
            err = conf.get("err")
            if err:
                txt_quote.value = f"Swap FAILED: {err}\nsignature: {res['signature']}"
            else:
                txt_quote.value = (
                    f"Swap SUCCESS ({status})!\n"
                    f"Received ~{out_ui:.6f} {dd_out.value}\n"
                    f"signature: {res['signature']}"
                )
        except Exception as er:
            import traceback
            print(f"[SWAP] swap ERROR: {er}\n{traceback.format_exc()}")
            txt_quote.value = f"Swap error: {er}"
        finally:
            ev.control.disabled = False
            page.update()

    el_swap_page = ctx.controls["el_swap_page"]
    el_swap_page.controls.clear()
    el_swap_page.controls.extend([
        flet.Row([flet.Text(
            value='',
            spans=[
                flet.TextSpan('Wallet: ', flet.TextStyle(size=16)),
                flet.TextSpan(f"{data['wallet_data']['address_base58']}", flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
            ]
        )]),
        flet.Row([dd_in, dd_out]),
        flet.Row([tf_amount, tf_slippage]),
        flet.Row([
            flet.ElevatedButton("Get Quote", on_click=get_quote_button_click),
            flet.ElevatedButton("Swap", on_click=swap_button_click),
        ]),
        flet.Row([txt_quote], wrap=True),
    ])
    await page.push_route("swap-page")


def build_swap_page(ctx: AppContext) -> flet.View:
    """Build the swap page View (binds ``ctx.controls["el_swap_page"]``)."""
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    return flet.View(
        route="swap-page",
        appbar=flet.AppBar(
            title=flet.Text("Swap (Jupiter)"),
            color="white",
            bgcolor="green",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Swap Tokens', size=30, font_family="Georgia"),
            ctx.controls["el_swap_page"],
        ]
    )
