"""NFT gallery page (extracted from ``main.py``).

Owns the NFT gallery screen assembled in the 2026-07-16 "NFT gallery" session:
pick a wallet + networks -> :func:`solana.nft.get_nfts` -> a grid of clickable
thumbnails, each opening a detail dialog (image / collection / network / copyable
mint / traits / description) with a **Send NFT** action that hands off to the SPL
transfer page.

Coupling
--------
The "Send NFT" action navigates to the SPL transfer page, which is built by
``main.py``'s ``_open_spl_token_page`` closure (it migrates with the transfer
screens in a later Phase-7 group). Rather than reaching back into ``main.py``,
:func:`nft_enter` receives ``open_spl_page`` (that closure) as an explicit
argument and wires it into the detail dialog. The ``spl_data`` dict shape passed
to it is identical to what ``main.py`` built inline before extraction, so the
transfer page's field-index reads are untouched.

Wallet records are read via :func:`ui.wallets.load_wallets` (no dependency on
``main.py``'s ``get_storage_data`` closure). The gallery is otherwise read-only;
no signer keys are needed here (the Send action delegates to the transfer page,
which resolves the key itself).
"""

import flet

from solana.nft import get_nfts
from ui.context import AppContext
from ui.formatting import short_addr
from ui.wallets import load_wallets

_MAINNET = "https://api.mainnet-beta.solana.com"
_TESTNET = "https://api.testnet.solana.com"
_DEVNET = "https://api.devnet.solana.com"


def _network_tag(network: str) -> str:
    """Short label (mainnet/testnet/devnet) for an NFT's RPC endpoint URL."""
    if network == _MAINNET:
        return "mainnet"
    if "testnet" in (network or ""):
        return "testnet"
    if "devnet" in (network or ""):
        return "devnet"
    return "mainnet" if network else ""


def _nft_tile(nft: dict, wallet: dict, on_click) -> flet.TextButton:
    """A single clickable NFT thumbnail used in the gallery grid (pure builder).

    ``on_click`` is the bound detail-dialog handler (:func:`nft_enter` supplies
    a closure that captures ``open_spl_page``).
    """
    img_src = nft.get('image') or "spl-token-placeholder.png"
    tag = _network_tag(nft.get('network'))
    return flet.TextButton(
        content=flet.Container(
            width=150,
            content=flet.Column(
                [
                    flet.Image(
                        src=img_src,
                        width=150,
                        height=150,
                        fit=flet.BoxFit.COVER,
                        border_radius=flet.border_radius.all(8),
                    ),
                    flet.Text(
                        nft.get('name', 'Unnamed NFT'),
                        size=12, weight=flet.FontWeight.BOLD,
                        max_lines=1, overflow=flet.TextOverflow.ELLIPSIS,
                        text_align=flet.TextAlign.CENTER,
                    ),
                    flet.Text(
                        nft.get('collection') or nft.get('symbol') or tag or '',
                        size=10, color=flet.Colors.GREY_600,
                        max_lines=1, overflow=flet.TextOverflow.ELLIPSIS,
                        text_align=flet.TextAlign.CENTER,
                    ),
                ],
                spacing=2, tight=True,
                horizontal_alignment=flet.CrossAxisAlignment.CENTER,
            ),
        ),
        data={"nft": nft, "wallet": wallet},
        on_click=on_click,
    )


async def nft_enter(ctx: AppContext, open_spl_page) -> None:
    """(Re)build the NFT Gallery page contents into ``ctx.controls["el_nft_page"]``.

    Parameters
    ----------
    ctx:
        Shared app context.
    open_spl_page:
        ``main.py``'s ``_open_spl_token_page(data)`` async closure — injected so
        the detail dialog's "Send NFT" action can navigate to the SPL transfer
        page without this module importing from ``main.py``. Receives a
        ``spl_data`` dict with the same shape ``main.py`` built inline.
    """
    page = ctx.page
    el_nft_page = ctx.controls["el_nft_page"]
    el_nft_page.controls.clear()
    wallets = await load_wallets(ctx)
    if not wallets:
        el_nft_page.controls.append(
            flet.Text("No wallets yet. Add a wallet first to view its NFTs.", size=14, color=flet.Colors.GREY_600)
        )
        page.update()
        return

    wallets_by_addr = {w['address_base58']: w for w in wallets}
    wallet_dd = flet.Dropdown(
        label="Wallet",
        width=420,
        options=[
            flet.dropdown.Option(
                key=w['address_base58'],
                text=f"{w.get('name', 'Wallet')} · {short_addr(w['address_base58'])}",
            )
            for w in wallets
        ],
        value=wallets[0]['address_base58'],
    )
    cb_mainnet = flet.Checkbox(label="mainnet-beta", value=True)
    cb_testnet = flet.Checkbox(label="testnet", value=False)
    cb_devnet = flet.Checkbox(label="devnet", value=False)
    grid_holder = flet.Column()
    status_txt = flet.Text(size=12, selectable=True, text_align=flet.TextAlign.CENTER)

    async def _detail_click(e):
        """Open a detail/preview dialog for an NFT, with a Send action."""
        info = e.control.data
        nft = info["nft"]
        wallet = info["wallet"]

        def _close(ev):
            dlg.open = False
            page.update()

        async def _send(ev):
            dlg.open = False
            page.update()
            if not nft.get('mint'):
                page.show_dialog(flet.AlertDialog(title=flet.Text("This NFT has no mint address; cannot send.")))
                return
            spl_data = {
                'wallet_address': wallet['address_base58'],
                'network': nft['network'],
                'spl_amount': nft.get('amount', 1),
                'symbol': nft.get('symbol') or 'NFT',
                'sol_amount': 0,
                'raw_data': {
                    'mint': nft['mint'],
                    'decimals': 0,
                    'program_id': nft.get('program_id'),
                },
                'wallet_data': wallet,
                'nft_prefill_amount': 1,
            }
            await open_spl_page(spl_data)

        img_src = nft.get('image') or "spl-token-placeholder.png"
        attr_rows = []
        for attr in nft.get('attributes', []) or []:
            attr_rows.append(
                flet.Row(
                    [
                        flet.Text(attr.get('trait_type', '') or '', size=12, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_700),
                        flet.Text(attr.get('value', '') or '', size=12),
                    ],
                    alignment=flet.MainAxisAlignment.SPACE_BETWEEN,
                )
            )
        if not attr_rows:
            attr_rows.append(flet.Text("(no traits)", size=11, italic=True, color=flet.Colors.GREY_500))

        mint_row = flet.Row(
            [
                flet.Text(f"Mint: {short_addr(nft.get('mint', ''))}", size=11, selectable=True, color=flet.Colors.GREY_700),
                flet.IconButton(
                    icon=flet.Icons.CONTENT_COPY, icon_size=16, tooltip="Copy mint",
                    on_click=lambda ev: page.clipboard.set(nft.get('mint', '')),
                ),
            ],
            alignment=flet.MainAxisAlignment.SPACE_BETWEEN,
        )

        dlg = flet.AlertDialog(
            modal=True,
            title=flet.Text(nft.get('name', 'Unnamed NFT'), max_lines=2, overflow=flet.TextOverflow.ELLIPSIS),
            content=flet.Container(
                width=340,
                content=flet.Column(
                    [
                        flet.Image(
                            src=img_src, width=300, height=300,
                            fit=flet.BoxFit.CONTAIN,
                            border_radius=flet.border_radius.all(10),
                        ),
                        flet.Text(
                            nft.get('collection') or nft.get('symbol') or '',
                            size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_700,
                        ),
                        flet.Text(f"Network: {_network_tag(nft.get('network'))}   Amount: {nft.get('amount', 1)}", size=11, color=flet.Colors.GREY_600),
                        mint_row,
                        flet.Divider(thickness=1),
                        flet.Text("Attributes", size=12, weight=flet.FontWeight.BOLD),
                        *attr_rows,
                    ] + ([flet.Text(nft['description'], size=11, selectable=True, color=flet.Colors.GREY_600)] if nft.get('description') else []),
                    tight=True, scroll=flet.ScrollMode.AUTO, spacing=4,
                ),
            ),
            actions=[
                flet.TextButton("Close", on_click=_close),
                flet.ElevatedButton("Send NFT", icon=flet.Icons.SEND, on_click=_send),
            ],
            actions_alignment=flet.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)

    async def _load(ev):
        addr = wallet_dd.value
        if not addr:
            status_txt.value = "Pick a wallet first."
            page.update()
            return
        nets = []
        if cb_mainnet.value:
            nets.append(_MAINNET)
        if cb_testnet.value:
            nets.append(_TESTNET)
        if cb_devnet.value:
            nets.append(_DEVNET)
        if not nets:
            status_txt.value = "Select at least one network."
            page.update()
            return
        grid_holder.controls.clear()
        status_txt.value = ""
        grid_holder.controls.append(
            flet.Row([flet.ProgressRing(), flet.Text("Loading NFTs...")], alignment=flet.MainAxisAlignment.CENTER)
        )
        page.update()
        try:
            nfts = await get_nfts(addr, nets)
        except Exception as er:
            print(f'nft_enter load error: {er}')
            nfts = []
            status_txt.value = f"Error loading NFTs: {er}"
        grid_holder.controls.clear()
        if not nfts:
            grid_holder.controls.append(
                flet.Text("No NFTs found on the selected networks.", size=13, color=flet.Colors.GREY_600)
            )
            if not status_txt.value:
                status_txt.value = ""
        else:
            status_txt.value = f"{len(nfts)} NFT(s) found"
            wallet = wallets_by_addr.get(addr)
            gallery = flet.Row(
                [
                    _nft_tile(nft, wallet, _detail_click)
                    for nft in nfts
                ],
                wrap=True, alignment=flet.MainAxisAlignment.CENTER,
                spacing=10, run_spacing=10,
            )
            grid_holder.controls.append(gallery)
        page.update()

    load_btn = flet.ElevatedButton("Load NFTs", icon=flet.Icons.COLLECTIONS, on_click=_load)

    el_nft_page.controls.extend([
        flet.Text("NFT Gallery", size=16, weight=flet.FontWeight.BOLD),
        flet.Row([wallet_dd], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([cb_mainnet, cb_testnet, cb_devnet], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([load_btn], alignment=flet.MainAxisAlignment.CENTER),
        flet.Row([status_txt], alignment=flet.MainAxisAlignment.CENTER),
        flet.Divider(),
        grid_holder,
    ])
    page.update()


def build_nft_page(ctx: AppContext) -> flet.View:
    """Build the NFT Gallery page (binds the shared ``el_nft_page`` column;
    ``nft_enter(ctx, open_spl_page)`` repopulates it on each visit).

    Extracted from ``main.py`` during Phase 7 Group 6g — mirrors the
    ``build_*_page`` pattern used by the other extracted modules: the View is
    built once at bootstrap, binds the shared Column registered in
    ``ctx.controls["el_nft_page"]``, and wires the shared view chrome (AppBar
    back button + navbar) from ``ctx.controls``.
    """
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    return flet.View(
        route="nft-page",
        appbar=flet.AppBar(
            title=flet.Text("NFT Gallery"),
            color="white",
            bgcolor="#7c3aed",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('NFT Gallery', size=30, font_family="Georgia"),
            ctx.controls["el_nft_page"],
        ],
    )
