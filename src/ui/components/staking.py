"""Liquid staking page (extracted from ``main.py``).

Owns the Liquid Staking screen from the 2026-07-17 session: pick a wallet + LST +
SOL amount -> quote -> stake / unstake SOL <-> Liquid Staking Token
(JitoSOL/mSOL/bSOL/jupSOL) via Jupiter, plus a live positions table with per-LST
unstake. Mainnet-only (Jupiter is mainnet-only, same constraint as the swap
screen).

Coupling
--------
Stake / unstake sign with the wallet's private key. Rather than reaching back
into ``main.py``'s ``get_wallet_private_key``/``has_wallet_private_key`` closures
(which depend on the live unlock state), this module resolves the signer key via
``ctx.get_wallet_private_key`` / ``ctx.has_wallet_private_key`` — convenience
accessors added to :class:`~ui.context.AppContext` during this group. They are
behaviourally identical to the legacy closures (``""`` while locked, else decrypt
on demand via :func:`solana.security.get_secret`), so the page behaves exactly as
before.

Wallet records are read via :func:`ui.wallets.load_wallets`. No outbound
navigation to other pages — the page is self-contained (quote / stake / unstake /
refresh all render inline into ``ctx.controls["el_lst_page"]``).
"""

import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import flet

from solana.liquid_staking import (
    LST_TOKENS,
    MAX_SLIPPAGE_BPS,
    get_stake_quote as lst_get_quote,
    stake_sol as lst_stake,
    unstake_sol as lst_unstake,
    get_lst_positions as lst_positions,
)
from solana.prices import fmt_usd
from solana.validators import is_valid_amount
from ui.context import AppContext
from ui.formatting import short_addr
from ui.wallets import load_wallets

_MAINNET = "https://api.mainnet-beta.solana.com"


async def lst_enter(ctx: AppContext) -> None:
    """(Re)build the Liquid Staking page contents into ``ctx.controls["el_lst_page"]``.

    Liquid staking = swap SOL for a Liquid Staking Token (JitoSOL/mSOL/bSOL/
    jupSOL). The LST appreciates against SOL over time — that appreciation is the
    staking yield (no claim/withdraw instruction). Mainnet-only (Jupiter is
    mainnet-only, like the swap screen).
    """
    page = ctx.page
    el_lst_page = ctx.controls["el_lst_page"]
    el_lst_page.controls.clear()
    wallets = await load_wallets(ctx)
    if not wallets:
        el_lst_page.controls.append(
            flet.Text("No wallets yet. Add a wallet first to use liquid staking.", size=14, color=flet.Colors.GREY_600)
        )
        page.update()
        return

    wallets_by_addr = {w['address_base58']: w for w in wallets}
    wallet_dd = flet.Dropdown(
        label="Wallet", width=420,
        options=[flet.dropdown.Option(
            key=w['address_base58'],
            text=f"{w.get('name', 'Wallet')} · {short_addr(w['address_base58'])}",
        ) for w in wallets],
        value=wallets[0]['address_base58'],
    )
    lst_dd = flet.Dropdown(
        label="Stake into", width=260,
        options=[flet.dropdown.Option(key=sym, text=f"{sym} · {info[2]}") for sym, info in LST_TOKENS.items()],
        value="JitoSOL",
    )
    tf_amount = flet.TextField(label="Amount (SOL)", width=160, max_length=30)
    tf_slippage = flet.TextField(label="Slippage %", value="1.0", width=100, max_length=6)
    txt_quote = flet.Text(selectable=True, text_align=flet.TextAlign.CENTER)
    quote_holder: dict = {"quote": None, "amount_str": None, "lst_sym": None, "slippage_bps": None}
    positions_holder = flet.Column()
    status_txt = flet.Text(size=12, selectable=True, text_align=flet.TextAlign.CENTER)

    def _slippage_bps() -> int:
        try:
            pct = Decimal((tf_slippage.value or "").strip())
        except (InvalidOperation, ValueError):
            raise ValueError("Slippage must be a number")
        if not pct.is_finite() or pct < Decimal("0.01") or pct > Decimal(MAX_SLIPPAGE_BPS) / 100:
            raise ValueError(f"Slippage must be between 0.01% and {MAX_SLIPPAGE_BPS / 100:g}%")
        return max(1, int((pct * 100).to_integral_value(rounding=ROUND_HALF_UP)))

    async def _quote_click(ev):
        try:
            amount_str = (tf_amount.value or "").strip()
            if not is_valid_amount(amount_str):
                txt_quote.value = "Invalid amount."
                el_lst_page.update(); return
            slippage_bps = _slippage_bps()
            txt_quote.value = "Fetching quote..."
            el_lst_page.update()
            q = await lst_get_quote(lst_dd.value, amount_str, slippage_bps=slippage_bps)
            rate = q["sol_per_lst"]
            rate_txt = f"  (1 {lst_dd.value} ≈ {rate:.4f} SOL — accumulated yield)" if rate else ""
            txt_quote.value = (
                f"{amount_str} SOL -> {q['out_amount_lst']:.8f} {lst_dd.value}\n"
                f"Min received (with slippage): {q['min_out_lst']:.8f} {lst_dd.value}\n"
                f"Price impact: {q['price_impact_pct']:.3f}%"
                f"{rate_txt}"
            )
            quote_holder.update({"quote": q, "amount_str": amount_str, "lst_sym": lst_dd.value, "slippage_bps": slippage_bps})
        except Exception as er:
            txt_quote.value = f"Quote error: {er}"
        el_lst_page.update()

    async def _stake_click(ev):
        try:
            addr = wallet_dd.value
            wallet = wallets_by_addr.get(addr)
            if not ctx.has_wallet_private_key(wallet):
                txt_quote.value = "Staking needs the wallet's private key. Unlock the wallet or recover it with its secret."
                el_lst_page.update(); return
            if (tf_amount.value or "").strip() != (quote_holder.get("amount_str") or "") \
                    or lst_dd.value != quote_holder.get("lst_sym") \
                    or _slippage_bps() != quote_holder.get("slippage_bps"):
                txt_quote.value = "Inputs changed. Press Get Quote again, then Stake SOL."
                el_lst_page.update(); return
            ev.control.disabled = True
            txt_quote.value = "Staking... please wait"
            el_lst_page.update()
            res = await lst_stake(
                lst_symbol=lst_dd.value,
                amount_sol=(tf_amount.value or "").strip(),
                signer_address=addr,
                private_key_hex=ctx.get_wallet_private_key(wallet),
                slippage_bps=_slippage_bps(),
                network=_MAINNET,
            )
            conf = res.get("confirmation", {}).get("result", {}).get("value", [{}])[0]
            status = conf.get("confirmationStatus") if conf else "unknown"
            if conf and conf.get("err"):
                txt_quote.value = f"Stake FAILED: {conf['err']}\nsignature: {res['signature']}"
            else:
                out = res.get("out_amount_lst")
                out_txt = f"\nReceived ~{out:.8f} {lst_dd.value}" if out else ""
                txt_quote.value = f"Stake SUCCESS ({status})!{out_txt}\nsignature: {res['signature']}"
            await _refresh_positions()
        except Exception as er:
            txt_quote.value = f"Stake error: {er}"
        finally:
            ev.control.disabled = False
            el_lst_page.update()

    async def _refresh_positions():
        addr = wallet_dd.value
        if not addr:
            return
        positions_holder.controls.clear()
        positions_holder.controls.append(
            flet.Row([flet.ProgressRing(), flet.Text("Loading positions...")], alignment=flet.MainAxisAlignment.CENTER)
        )
        el_lst_page.update()
        try:
            pos = await lst_positions(addr, network=_MAINNET)
        except Exception as er:
            positions_holder.controls.clear()
            positions_holder.controls.append(flet.Text(f"Error loading positions: {er}", size=13, color=flet.Colors.RED_400))
            el_lst_page.update(); return
        positions_holder.controls.clear()
        positions = pos.get("positions", [])
        if not positions:
            positions_holder.controls.append(
                flet.Text("No liquid-staking positions yet for this wallet.", size=13, color=flet.Colors.GREY_600)
            )
            el_lst_page.update(); return

        wallet = wallets_by_addr.get(addr)
        has_key = ctx.has_wallet_private_key(wallet) if wallet else False
        for p in positions:
            rate = p.get("sol_per_lst")
            usd = fmt_usd(p.get("usd_value")) if p.get("usd_value") is not None else ""
            rate_txt = f"  ·  1 {p['symbol']} ≈ {rate:.4f} SOL" if rate else ""
            tf_unstake = flet.TextField(label=f"Unstake {p['symbol']}", width=140, max_length=30)

            async def _unstake(ev, sym=p["symbol"], fld=tf_unstake):
                try:
                    amt = (fld.value or "").strip()
                    if not is_valid_amount(amt):
                        status_txt.value = f"Invalid {sym} amount."; el_lst_page.update(); return
                    if not (wallets_by_addr.get(wallet_dd.value) and ctx.has_wallet_private_key(wallets_by_addr[wallet_dd.value])):
                        status_txt.value = "Unstake needs the wallet's private key. Unlock or recover the wallet."
                        el_lst_page.update(); return
                    ev.control.disabled = True
                    status_txt.value = f"Unstaking {amt} {sym}..."
                    el_lst_page.update()
                    res = await lst_unstake(
                        lst_symbol=sym, amount_lst=amt,
                        signer_address=wallet_dd.value,
                        private_key_hex=ctx.get_wallet_private_key(wallets_by_addr[wallet_dd.value]),
                        slippage_bps=_slippage_bps(), network=_MAINNET,
                    )
                    conf = res.get("confirmation", {}).get("result", {}).get("value", [{}])[0]
                    if conf and conf.get("err"):
                        status_txt.value = f"Unstake FAILED: {conf['err']}\n{res['signature']}"
                    else:
                        out = res.get("out_amount_sol")
                        otxt = f" (~{out:.6f} SOL)" if out else ""
                        status_txt.value = f"Unstake SUCCESS{otxt}\n{res['signature']}"
                    await _refresh_positions()
                except Exception as er:
                    status_txt.value = f"Unstake error: {er}"
                finally:
                    ev.control.disabled = False
                    el_lst_page.update()

            positions_holder.controls.append(flet.Row([
                flet.Column([
                    flet.Text(f"{p['amount']:.6f} {p['symbol']}  ({p['provider']})", weight=flet.FontWeight.BOLD),
                    flet.Text(f"Value {usd}{rate_txt}", size=12, selectable=True),
                ]),
                tf_unstake,
                flet.ElevatedButton("Unstake", on_click=_unstake, disabled=not has_key),
            ], alignment=flet.MainAxisAlignment.SPACE_BETWEEN, wrap=True))
        el_lst_page.update()

    quote_btn = flet.ElevatedButton("Get Quote", on_click=_quote_click)
    stake_btn = flet.ElevatedButton("Stake SOL", on_click=_stake_click)
    refresh_btn = flet.ElevatedButton("Refresh Positions", icon=flet.Icons.REFRESH, on_click=lambda ev: asyncio.ensure_future(_refresh_positions()))

    el_lst_page.controls.extend([
        flet.Text("Liquid Staking", size=16, weight=flet.FontWeight.BOLD),
        flet.Text(
            "Stake SOL into a Liquid Staking Token via Jupiter. The token gains value "
            "against SOL over time — that growth is your yield. Unstake = swap back to SOL. "
            "Mainnet only.",
            size=12, color=flet.Colors.GREY_700, text_align=flet.TextAlign.CENTER,
        ),
        flet.Row([wallet_dd], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([lst_dd], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([tf_amount, tf_slippage], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([quote_btn, stake_btn], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([txt_quote], alignment=flet.MainAxisAlignment.CENTER),
        flet.Divider(),
        flet.Row([refresh_btn], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([status_txt], alignment=flet.MainAxisAlignment.CENTER),
        positions_holder,
    ])
    page.update()
