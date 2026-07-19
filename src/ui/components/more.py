"""More hub page (extracted from ``main.py`` — Phase 7 Group 6d).

Owns the ``more-page`` View assembled in Phase 2 of the tiered-UI redesign and
filtered per-experience-mode in Phase 4. The hub groups features into three
sections — **WEB3 & DeFi** (Connect dApp / NFT Gallery / Liquid Staking),
**Tools** (Address Book / Settings), **Developer** (storage inspector /
simulation inspector / raw RPC / export raw keys / clear storage) — with the
WEB3 + Developer sections omitted entirely when their items are all gated out
by the active experience mode (so Simple mode shows only Tools).

The single destructive action — **Clear all storage** — also lives here (the
``clear_client_storage`` wipe itself is in :mod:`ui.security_gate`); it shows a
confirmation ``AlertDialog`` before wiping every wallet + the PIN + contacts +
the WalletConnect pairing.

Public surface:

* :func:`build_more_page(ctx)` — build the View once at bootstrap, register it
  in ``ctx.controls["more_page"]`` so :func:`more_enter` can mutate it.
* :func:`more_enter(ctx)` — async enter hook: re-reads the experience mode and
  rebuilds the hub's controls from the ``feature()`` matrix.
* :func:`clear_storage_click(ctx, e)` — module-level handler wired from
  :func:`more_enter` via a named ``async def`` adapter (Group 5 rule).

The AppBar back button + navbar are wired from ``ctx.controls["view_pop"]`` /
``["navbar"]`` (registered by ``main()``), matching the other view builders.
"""

import asyncio

import flet

from ui.context import AppContext
from ui.experience import feature, get_experience
from ui.security_gate import clear_client_storage


def _hub_item(icon, title: str, subtitle: str, on_click, badge: str = "") -> flet.Card:
    """One tappable entry in the 'More' hub.

    Pure builder — icon + title + description + optional badge + chevron. The
    ``on_click`` is wired into the ``Container.ink`` tap, so it can be any sync
    or async callable (flet awaits coroutine handlers).
    """
    trailing = []
    if badge:
        trailing.append(
            flet.Container(
                content=flet.Text(badge, size=10, color=flet.Colors.WHITE, weight=flet.FontWeight.BOLD),
                bgcolor=flet.Colors.GREY_500,
                border_radius=6,
                padding=4,
            )
        )
    return flet.Card(
        content=flet.Container(
            ink=True,
            on_click=on_click,
            padding=12,
            width=440,
            content=flet.Row(
                [
                    flet.Icon(icon, size=28, color=flet.Colors.BLUE_700),
                    flet.Column(
                        [
                            flet.Text(title, size=15, weight=flet.FontWeight.BOLD),
                            flet.Text(subtitle, size=11, color=flet.Colors.GREY_700),
                        ],
                        expand=True,
                        spacing=1,
                    ),
                    *trailing,
                    flet.Icon(flet.Icons.CHEVRON_RIGHT, color=flet.Colors.GREY_400),
                ],
                alignment=flet.MainAxisAlignment.START,
            ),
        ),
    )


async def clear_storage_click(ctx: AppContext, e) -> None:
    """Wipe ALL local storage (wallets, PIN, contacts, WC pairing). Destructive.

    Shows a confirmation ``AlertDialog`` first; the wipe only runs in
    :func:`_do_clear_storage`. The dialog is ``actions``-driven (no
    ``on_dismiss``), so a barrier-click / Escape is a no-op (Cancel must be
    pressed explicitly).
    """
    page = ctx.page
    dlg = flet.AlertDialog(
        title=flet.Text("Clear ALL local storage?"),
        content=flet.Text(
            "This permanently deletes every wallet, the PIN, contacts and "
            "WalletConnect pairing. Encrypted secrets cannot be recovered.",
            size=12,
        ),
        actions=[
            flet.TextButton("Cancel", on_click=lambda ev: ctx.close_dialog(dlg)),
            flet.TextButton(
                "Clear everything",
                style=flet.ButtonStyle(color=flet.Colors.RED),
                on_click=lambda ev: asyncio.create_task(_do_clear_storage(ctx, dlg)),
            ),
        ],
    )
    page.show_dialog(dlg)


async def _do_clear_storage(ctx: AppContext, dlg) -> None:
    """Wipe storage + reset to the homepage (called from clear_storage_click)."""
    page = ctx.page
    ctx.close_dialog(dlg)
    await clear_client_storage(ctx)
    page.show_dialog(flet.AlertDialog(title=flet.Text("All local storage cleared.")))
    await page.push_route("/")


def build_more_page(ctx: AppContext) -> flet.View:
    """Build the More hub View (called once at bootstrap).

    Returns an empty-controls View; :func:`more_enter` populates it on each
    visit based on the active experience mode. The View is also registered in
    ``ctx.controls["more_page"]`` so the enter hook can mutate it without being
    passed the View explicitly.
    """
    more_page = flet.View(
        route="more-page",
        appbar=flet.AppBar(
            title=flet.Text("More"),
            color="white",
            bgcolor="#1da1f2",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=ctx.controls["view_pop"]),
        ),
        navigation_bar=ctx.controls["navbar"],
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[],  # populated by more_enter() based on the active experience mode
    )
    ctx.controls["more_page"] = more_page
    return more_page


async def more_enter(ctx: AppContext) -> None:
    """Rebuild the More hub controls for the persisted experience mode.

    Sections whose items are all gated out are omitted entirely (header +
    divider included), so Simple mode shows only the Tools section. Each visit
    re-reads the mode so a fresh mode change in Settings takes effect on the
    next More visit.
    """
    page = ctx.page
    mode = await get_experience(page)

    # ---- Navigation handlers (tiny async closures — Group 5 rule: never
    # lambdas for async handlers). Each just pushes a route.
    async def nav_wc(e): await page.push_route("wc-page")
    async def nav_nft(e): await page.push_route("nft-page")
    async def nav_stake(e): await page.push_route("stake-page")
    async def nav_addressbook(e): await page.push_route("addressbook-page")
    async def nav_dev_storage(e): await page.push_route("dev-storage-page")
    async def nav_settings(e): await page.push_route("settings-page")
    async def nav_sim(e): await page.push_route("sim-page")
    async def nav_rpc(e): await page.push_route("rpc-page")
    async def nav_rawkey(e): await page.push_route("raw-key-page")

    async def _clear_storage(e):
        await clear_storage_click(ctx, e)

    controls: list = []

    # WEB3 & DeFi — Pro+ only; section is skipped entirely in Simple mode.
    web3_items = []
    if feature("walletconnect", mode):
        web3_items.append(
            _hub_item(
                flet.Icons.LINK, "Connect dApp",
                "Pair with a dApp via WalletConnect v2 and sign requests.", nav_wc,
            )
        )
    if feature("nft", mode):
        web3_items.append(
            _hub_item(
                flet.Icons.COLLECTIONS, "NFT Gallery",
                "Browse and send your non-fungible tokens.", nav_nft,
            )
        )
    if feature("staking", mode):
        web3_items.append(
            _hub_item(
                flet.Icons.SAVINGS, "Liquid Staking",
                "Stake SOL into JitoSOL / mSOL / bSOL / jupSOL.", nav_stake,
            )
        )
    if web3_items:
        controls.append(
            flet.Text("WEB3 & DeFi", size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_600)
        )
        controls.extend(web3_items)
        controls.append(flet.Divider())

    # Tools — always visible in every mode.
    controls.append(flet.Text("Tools", size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_600))
    controls.append(
        _hub_item(
            flet.Icons.CONTACTS, "Address Book",
            "Saved recipients with address-poisoning protection.", nav_addressbook,
        )
    )
    controls.append(
        _hub_item(
            flet.Icons.SETTINGS, "Settings",
            "Theme, security and app preferences.", nav_settings,
        )
    )

    # Developer — Developer mode only. Each tool is gated by its own feature
    # key so the section assembles from whatever the matrix exposes.
    dev_items = []
    if feature("devtools", mode):
        dev_items.append(
            _hub_item(
                flet.Icons.STORAGE, "Storage inspector",
                "View and edit raw shared_preferences keys.", nav_dev_storage, badge="dev",
            )
        )
    if feature("sim_detail", mode):
        dev_items.append(
            _hub_item(
                flet.Icons.BIOTECH, "Simulation inspector",
                "Run the anti-phishing simulation on a pasted transaction.", nav_sim, badge="dev",
            )
        )
    if feature("custom_rpc", mode):
        dev_items.append(
            _hub_item(
                flet.Icons.DVR, "Raw RPC inspector",
                "Run read-only JSON-RPC calls against any endpoint.", nav_rpc, badge="dev",
            )
        )
    if feature("raw_export", mode):
        dev_items.append(
            _hub_item(
                flet.Icons.VPN_KEY, "Export raw keys",
                "Reveal & copy a wallet's private key / mnemonic. DANGEROUS.",
                nav_rawkey, badge="danger",
            )
        )
    if feature("devtools", mode):
        dev_items.append(
            _hub_item(
                flet.Icons.DELETE_SWEEP_OUTLINED, "Clear all storage",
                "Wipe every wallet, PIN and pairing. Irreversible.", _clear_storage, badge="danger",
            )
        )
    if dev_items:
        controls.append(flet.Divider())
        controls.append(
            flet.Text("Developer", size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_600)
        )
        controls.extend(dev_items)

    more_page = ctx.controls["more_page"]
    more_page.controls = [
        flet.Column(
            controls,
            spacing=6,
            width=460,
        ),
    ]
