"""Developer-only tool pages (extracted from ``main.py``).

Owns the three Developer-layer screens assembled in Phase 6 of the tiered-UI
redesign, each gated by its own experience feature key:

* :func:`build_sim_page` — **Simulation inspector** (``sim_detail``): paste a
  base64 transaction and run the anti-phishing
  :func:`solana.simulation.analyze_transaction` the WalletConnect flow uses, to
  inspect fee / programs / SOL & token deltas / warnings / logs *without
  signing*. Read-only (``sigVerify=false``, ``replaceRecentBlockhash=true``).
* :func:`build_rpc_page` — **Raw RPC inspector** (``custom_rpc``): run arbitrary
  read-only JSON-RPC calls directly against a chosen endpoint + commitment via
  ``httpx``. Read-only methods only — never broadcasts.
* :func:`build_rawkey_page` + :func:`rawkey_enter` — **Export raw keys**
  (``raw_export``): warning-gated reveal + copy of a wallet's
  ``private_key_hex`` / ``secret_key_base58`` / mnemonic / ``public_key_hex``.
  Secrets are already PIN-gated at rest; this page just makes the reveal a
  deliberate, clearly-labelled Developer action.

Every function that needs ``page``/``session`` takes an :class:`AppContext` as
its first argument (Phase 7 migration contract). The module never reaches back
into ``main.py``: it depends only on ``solana/`` business logic, ``ui.context``
and shared controls registered in ``ctx.controls`` (``view_pop``, ``navbar``,
``el_rawkey_page``). Wallets are read directly from
``ctx.page.shared_preferences`` under the ``"wallet."`` prefix so there is no
dependency on ``main.py``'s ``get_storage_data`` closure.
"""

import asyncio
import json

import flet
import httpx

from solana.security import WATCH_ONLY_FIELD, decrypt_wallet_secrets
from solana.simulation import analyze_transaction
from ui.context import AppContext
from ui.wallets import load_wallets

#: Read-only Solana RPC endpoints keyed by the source-dropdown value.
_ENDPOINTS = {
    "mainnet": "https://api.mainnet-beta.solana.com",
    "testnet": "https://api.testnet.solana.com",
    "devnet": "https://api.devnet.solana.com",
}
_MAINNET = _ENDPOINTS["mainnet"]


# ============================ shared helpers ================================

def _sim_row(label: str, value, color=None) -> flet.Text:
    """One labelled output row used by the simulation + RPC inspectors."""
    return flet.Text(f"{label}: {value}", size=12, selectable=True,
                     color=color or flet.Colors.BLACK87)


def _decrypt(ctx: AppContext, wallet: dict) -> dict:
    """Wallet dict with secrets decrypted (mirror of the old closure).

    Returns the wallet unchanged when the app is locked (no in-memory key), so
    the reveal/copy actions degrade to "(app locked …)" instead of crashing.
    """
    if not ctx.is_unlocked():
        return wallet
    return decrypt_wallet_secrets(wallet, ctx.session["key"])


# ===================== Developer: Simulation inspector ======================

def build_sim_page(ctx: AppContext) -> flet.View:
    """Build the Simulation inspector page.

    The page holds its own transient controls (network dropdown, tx textarea,
    signer field, output column); the handlers close over those local controls.
    The AppBar back button and the navigation bar are read from
    ``ctx.controls`` ("view_pop" / "navbar"), which ``main()`` registers during
    bootstrap.
    """
    page = ctx.page
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    sim_out = flet.Column(spacing=4)
    sim_net_dd = flet.Dropdown(
        label="Network",
        width=420,
        value=_MAINNET,
        options=[
            flet.dropdown.Option(key=_MAINNET, text="mainnet-beta"),
            flet.dropdown.Option(key=_ENDPOINTS["testnet"], text="testnet"),
            flet.dropdown.Option(key=_ENDPOINTS["devnet"], text="devnet"),
        ],
    )
    sim_signer_tf = flet.TextField(
        label="Signer pubkey (optional — for relative SOL/token deltas)",
        width=420, dense=True,
    )
    sim_tx_ta = flet.TextField(
        label="Transaction (base64)",
        width=420, min_lines=3, max_lines=6, multiline=True,
    )

    async def sim_analyze_click(e):
        tx_b64 = (sim_tx_ta.value or "").strip()
        if not tx_b64:
            sim_out.controls = [_sim_row("Error", "paste a base64 transaction first", color="red")]
            page.update()
            return
        sim_out.controls = [flet.Row([flet.ProgressRing(), flet.Text("Simulating...")],
                                     alignment=flet.MainAxisAlignment.CENTER)]
        page.update()
        try:
            signer = (sim_signer_tf.value or "").strip() or None
            res = await analyze_transaction(tx_b64, sim_net_dd.value, signer_pubkey=signer)
        except Exception as er:
            sim_out.controls = [_sim_row("Error", f"analyze failed: {er}", color="red")]
            page.update()
            return
        sim_out.controls = []
        status = res.get("status")
        status_color = "green" if status == "ok" else ("red" if status == "error" else flet.Colors.ORANGE_800)
        sim_out.controls.append(_sim_row("Status", status, color=status_color))
        if res.get("error"):
            sim_out.controls.append(_sim_row("Error", res["error"], color="red"))
        if res.get("fee_sol") is not None:
            sim_out.controls.append(_sim_row("Fee", f"{res['fee_sol']} SOL  ({res.get('fee_lamports')} lamports)"))
        sim_out.controls.append(_sim_row("Message version", res.get("message_version")))
        sim_out.controls.append(_sim_row("Fee payer", res.get("fee_payer")))
        sim_out.controls.append(_sim_row("Account count", res.get("account_count")))
        sim_out.controls.append(_sim_row("Compute units", res.get("compute_units")))
        if res.get("programs"):
            sim_out.controls.append(_sim_row("Programs", ", ".join(res["programs"])))
        if res.get("unknown_programs"):
            sim_out.controls.append(
                _sim_row("⚠ Unverified programs", ", ".join(res["unknown_programs"]), color="red")
            )
        for ch in res.get("sol changes") or []:
            acct = str(ch.get("account", ""))
            sim_out.controls.append(
                _sim_row(f"SOL Δ {acct[:12]}…", f"{ch.get('delta_sol', 0):+.9f} SOL")
            )
        for ch in res.get("token_changes") or []:
            acct = str(ch.get("account", ""))
            sim_out.controls.append(
                _sim_row(f"Token Δ {acct[:12]}…",
                         f"{ch.get('delta_amount', '?')}  (mint {str(ch.get('mint', ''))[:10]}…)")
            )
        for w in res.get("warnings") or []:
            sim_out.controls.append(_sim_row("⚠ Warning", w, color=flet.Colors.ORANGE_800))
        logs = res.get("logs") or []
        if logs:
            sim_out.controls.append(flet.Text("Simulation logs:", size=11, weight=flet.FontWeight.BOLD))
            log_controls = []
            for log in logs:
                lc = "red" if ("failed" in str(log).lower() or "error" in str(log).lower()) else flet.Colors.GREY_700
                log_controls.append(flet.Text(f"• {log}", size=10, color=lc, selectable=True))
            sim_out.controls.append(
                flet.Container(
                    content=flet.Column(log_controls, spacing=1, scroll=flet.ScrollMode.AUTO),
                    height=140, padding=5,
                    border=flet.border.all(1, "black12"), border_radius=5,
                )
            )
        sim_out.controls.append(
            flet.ElevatedButton(
                "Copy raw JSON", icon=flet.Icons.COPY,
                on_click=lambda ev: page.clipboard.set(json.dumps(res, indent=2, default=str)),
            )
        )
        page.update()

    return flet.View(
        route="sim-page",
        appbar=flet.AppBar(
            title=flet.Text("Simulation inspector"),
            color="white",
            bgcolor="#6d28d9",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text("Simulation inspector", size=18, weight=flet.FontWeight.BOLD),
            flet.Text(
                "Run the anti-phishing simulation on a base64 transaction WITHOUT signing. "
                "Read-only (sigVerify=false, replaceRecentBlockhash=true).",
                size=11, color=flet.Colors.GREY_700, text_align=flet.TextAlign.CENTER,
            ),
            flet.Row([sim_net_dd], alignment=flet.MainAxisAlignment.CENTER),
            sim_signer_tf,
            sim_tx_ta,
            flet.Row(
                [flet.ElevatedButton("Analyze", icon=flet.Icons.PLAY_ARROW, on_click=sim_analyze_click)],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Divider(),
            sim_out,
        ],
    )


# ===================== Developer: Raw RPC inspector ========================

def build_rpc_page(ctx: AppContext) -> flet.View:
    """Build the Raw RPC inspector page.

    Posts direct JSON-RPC requests via ``httpx`` against a chosen endpoint +
    commitment and renders the pretty-printed response. Read-only methods only;
    errors degrade to a red "RPC failed: …" row instead of crashing.
    """
    page = ctx.page
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    rpc_out = flet.Column(spacing=4)
    rpc_source_dd = flet.Dropdown(
        label="Endpoint",
        width=420,
        value="mainnet",
        options=[
            flet.dropdown.Option(key="mainnet", text="mainnet-beta"),
            flet.dropdown.Option(key="testnet", text="testnet"),
            flet.dropdown.Option(key="devnet", text="devnet"),
            flet.dropdown.Option(key="custom", text="custom RPC URL"),
        ],
    )
    rpc_custom_tf = flet.TextField(
        label="Custom RPC URL (used when Endpoint = custom)",
        width=420, dense=True, value="",
    )
    rpc_commit_dd = flet.Dropdown(
        label="Commitment",
        width=200,
        value="confirmed",
        options=[
            flet.dropdown.Option(key="processed", text="processed"),
            flet.dropdown.Option(key="confirmed", text="confirmed"),
            flet.dropdown.Option(key="finalized", text="finalized"),
        ],
    )
    rpc_method_dd = flet.Dropdown(
        label="Method",
        width=420,
        value="getBalance",
        options=[
            flet.dropdown.Option(key="getBalance", text="getBalance (address)"),
            flet.dropdown.Option(key="getAccountInfo", text="getAccountInfo (address)"),
            flet.dropdown.Option(key="getTransaction", text="getTransaction (signature)"),
            flet.dropdown.Option(key="getSignaturesForAddress", text="getSignaturesForAddress (address)"),
            flet.dropdown.Option(key="getLatestBlockhash", text="getLatestBlockhash (no input)"),
        ],
    )
    rpc_input_tf = flet.TextField(
        label="Input (address or signature; ignored for getLatestBlockhash)",
        width=420, dense=True,
    )

    def _rpc_endpoint() -> str:
        src = rpc_source_dd.value
        if src == "custom":
            return (rpc_custom_tf.value or "").strip() or _MAINNET
        return _ENDPOINTS.get(src, _MAINNET)

    async def rpc_run_click(e):
        method = rpc_method_dd.value or "getBalance"
        endpoint = _rpc_endpoint()
        commitment = rpc_commit_dd.value or "confirmed"
        raw_input = (rpc_input_tf.value or "").strip()
        params: list
        if method == "getLatestBlockhash":
            params = [{"commitment": commitment}]
        else:
            params = [raw_input]
            if method in ("getBalance", "getAccountInfo"):
                params.append({"commitment": commitment})
            elif method == "getSignaturesForAddress":
                params.append({"commitment": commitment, "limit": 20})
            elif method == "getTransaction":
                params.append({"commitment": commitment, "maxSupportedTransactionVersion": 0})
        rpc_out.controls = [flet.Row([flet.ProgressRing(), flet.Text(f"POST {method} …")],
                                     alignment=flet.MainAxisAlignment.CENTER)]
        page.update()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    endpoint,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                body = resp.text
                try:
                    pretty = json.dumps(resp.json(), indent=2)
                except Exception:
                    pretty = body
        except Exception as er:
            rpc_out.controls = [
                _sim_row("Error", f"RPC failed: {er}", color="red"),
                _sim_row("Endpoint", endpoint),
            ]
            page.update()
            return
        rpc_out.controls = [
            _sim_row("Endpoint", endpoint),
            _sim_row("Method", method),
            _sim_row("HTTP status", "200 OK" if pretty != "" else "?"),
            flet.ElevatedButton(
                "Copy response", icon=flet.Icons.COPY,
                on_click=lambda ev: page.clipboard.set(pretty),
            ),
            flet.Container(
                content=flet.Text(pretty, selectable=True, size=10,
                                  color=flet.Colors.GREY_900, font_family="monospace" if False else None),
                padding=6,
                border=flet.border.all(1, "black12"),
                border_radius=5,
                width=460,
            ),
        ]
        page.update()

    return flet.View(
        route="rpc-page",
        appbar=flet.AppBar(
            title=flet.Text("Raw RPC inspector"),
            color="white",
            bgcolor="#6d28d9",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text("Raw RPC inspector", size=18, weight=flet.FontWeight.BOLD),
            flet.Text(
                "Run read-only JSON-RPC calls directly against any endpoint + commitment. "
                "Read-only methods only — never broadcasts.",
                size=11, color=flet.Colors.GREY_700, text_align=flet.TextAlign.CENTER,
            ),
            rpc_source_dd,
            rpc_custom_tf,
            rpc_commit_dd,
            rpc_method_dd,
            rpc_input_tf,
            flet.Row(
                [flet.ElevatedButton("Run", icon=flet.Icons.PLAY_ARROW, on_click=rpc_run_click)],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Divider(),
            rpc_out,
        ],
    )


# ===================== Developer: Export raw keys ==========================

async def _rawkey_reveal_click(ctx: AppContext, wallet: dict, field: str, out_text: flet.Text) -> None:
    """Decrypt one secret field into the paired (initially hidden) Text control."""
    page = ctx.page
    if not ctx.is_unlocked():
        out_text.value = "(app locked — unlock with PIN to reveal secrets)"
        out_text.color = flet.Colors.RED
        page.update()
        return
    dec = _decrypt(ctx, wallet)
    val = dec.get(field)
    out_text.value = val if val else "(empty / not available)"
    out_text.color = flet.Colors.BLACK87
    page.update()


async def _rawkey_copy_click(ctx: AppContext, wallet: dict, field: str) -> None:
    if not ctx.is_unlocked():
        return
    dec = _decrypt(ctx, wallet)
    val = dec.get(field)
    if val:
        await ctx.page.clipboard.set(val)


async def rawkey_enter(ctx: AppContext) -> None:
    """(Re)build the Export raw keys page into ``ctx.controls["el_rawkey_page"]``."""
    page = ctx.page
    el_rawkey_page = ctx.controls["el_rawkey_page"]
    el_rawkey_page.controls.clear()
    wallets = await load_wallets(ctx)
    if not wallets:
        el_rawkey_page.controls.append(
            flet.Text("No wallets yet. Add a wallet first.", size=14, color=flet.Colors.GREY_600)
        )
        page.update()
        return

    el_rawkey_page.controls.append(
        flet.Container(
            content=flet.Row(
                [
                    flet.Icon(flet.Icons.WARNING_AMBER, color=flet.Colors.RED),
                    flet.Text(
                        "These secrets grant FULL control of the wallet. Anyone with them "
                        "can drain all funds. Never share, screenshot, or paste into untrusted apps.",
                        size=11, color=flet.Colors.RED,
                    ),
                ],
                spacing=8,
            ),
            padding=10,
            border=flet.border.all(1, flet.Colors.RED_200),
            border_radius=8,
            bgcolor=flet.Colors.RED_50,
            width=440,
        )
    )

    for w in wallets:
        watch_only = w.get(WATCH_ONLY_FIELD)
        addr = w["address_base58"]
        name = w.get("name", "Wallet")
        rows: list = []
        for field, label in (
            ("private_key_hex", "Private key (hex)"),
            ("secret_key_base58", "Secret key (base58)"),
            ("words", "Mnemonic (12/24 words)"),
            ("public_key_hex", "Public key (hex)"),
        ):
            out = flet.Text("(hidden — press Reveal)", size=12, selectable=True,
                            color=flet.Colors.GREY_600)
            if watch_only and field != "public_key_hex":
                out.value = "(watch-only wallet — no private key)"
                rows.append(flet.Text(f"{label}:", size=12, weight=flet.FontWeight.BOLD))
                rows.append(out)
                continue
            rows.append(
                flet.Row(
                    [
                        flet.Text(f"{label}:", size=12, weight=flet.FontWeight.BOLD),
                        flet.OutlinedButton(
                            "Reveal", on_click=lambda ev, fld=field, o=out: asyncio.create_task(
                                _rawkey_reveal_click(ctx, w, fld, o)
                            ),
                        ),
                        flet.OutlinedButton(
                            "Copy", icon=flet.Icons.COPY,
                            on_click=lambda ev, fld=field: asyncio.create_task(
                                _rawkey_copy_click(ctx, w, fld)
                            ),
                        ),
                    ],
                    wrap=True, spacing=6,
                )
            )
            rows.append(out)

        el_rawkey_page.controls.append(
            flet.Card(
                content=flet.Container(
                    padding=12, width=440,
                    content=flet.Column(
                        [
                            flet.Row(
                                [
                                    flet.Text(name, size=14, weight=flet.FontWeight.BOLD),
                                    flet.Text(
                                        "  (watch-only)" if watch_only else "",
                                        size=11, color=flet.Colors.ORANGE_800,
                                    ),
                                ],
                            ),
                            flet.Text(f"Address: {addr}", size=11, selectable=True,
                                      color=flet.Colors.GREY_700),
                            flet.Divider(),
                            *rows,
                        ],
                        spacing=4, tight=True,
                    ),
                )
            )
        )
    page.update()


def build_rawkey_page(ctx: AppContext) -> flet.View:
    """Build the Export raw keys page (binds the shared ``el_rawkey_page`` column)."""
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    return flet.View(
        route="raw-key-page",
        appbar=flet.AppBar(
            title=flet.Text("Export raw keys"),
            color="white",
            bgcolor="#b91c1c",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text("Export raw keys", size=18, weight=flet.FontWeight.BOLD, color=flet.Colors.RED_700),
            ctx.controls["el_rawkey_page"],
        ],
    )
