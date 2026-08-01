"""Final orchestrator (Phase 7 Group 6g — the refactor phase's last step).

This module is what's left of ``main.py`` once every screen + handler has been
extracted into ``ui/components/*``. It owns only the **bootstrap + routing
plumbing**:

* Page configuration (title / alignment / theme / padding).
* The in-memory ``session`` dict (PIN unlock state) + the
  :class:`~ui.context.AppContext` shared with every extracted module.
* The shared flet Columns (registered in ``ctx.controls``) that the
  extracted view builders bind and the ``*_enter`` hooks repopulate.
* The bottom :class:`flet.NavigationBar`, the ``view_pop`` back-nav handler
  and the :func:`route_change` dispatcher — the three pieces of routing state
  every extracted view wires through ``ctx.controls``.
* The homepage View (logo + New/Recover/Add buttons + wallet cards list).
* The bootstrap sequence: register handlers -> trigger the initial route
  render -> start the inactivity auto-lock watcher -> present the PIN gate.

``main.py`` is now a one-line entry point: ``await build_app(page)``.

Migration contract (Group 6g):
* **App bootstrap + routing → ``ui.app`` module.** Any future extracted
  module that needs the navbar, the back-nav handler, the route dispatcher,
  or the homepage imports nothing extra — they all reach these through
  ``ctx.controls["navbar"]`` / ``["view_pop"]`` registered here. New routes
  are added by (a) a ``build_*_page(ctx)`` in the module that owns the screen
  and (b) one ``elif page.route == ...`` branch in the nested ``route_change``
  closure inside :func:`build_app`.
* **Homepage stays in this module.** It is the root view (``route="/"``),
  owns no business logic, and is read by ``route_change`` on every navigation.
  The wallet cards themselves are built by :func:`ui.components.balance.
  get_wallets_cards` (Group 6e).
* The ``solana/`` business layer is never touched.

Invariants preserved:
* ``homepage.controls[-1]`` is still the wallets list (so the per-navigation
  ``homepage.controls[-1] = await get_wallets_cards(ctx)`` refresh keeps
  working).
* All Views are built once at bootstrap at the same code location as before,
  in the same order; ``route_change``'s branches are byte-identical.
* The shared Columns + ``view_pop`` + ``navbar`` are the same live objects
  that ``ctx.controls`` exposes, so the extracted modules' reads/writes are
  unaffected.
* PIN never persisted; auto-lock + plaintext-wallet migration live in
  :mod:`ui.security_gate`; per-session state isolation preserved (no
  module-level mutable state — everything is local to :func:`build_app`).
"""

import asyncio
import time

import flet

from ui.context import AppContext
from ui.i18n import get_lang
from ui.security_gate import auto_lock_watcher, refresh_lock_state
from ui.components.addressbook import addressbook_enter, build_addressbook_page
from ui.components.balance import build_address_page, get_wallets_cards
from ui.components.devtools import (
    build_dev_storage_page,
    build_rawkey_page,
    build_rpc_page,
    build_sim_page,
    dev_storage_enter,
    rawkey_enter,
)
from ui.components.more import build_more_page, more_enter
from ui.components.nft import build_nft_page, nft_enter
from ui.components.settings import build_settings_page, settings_enter
from ui.components.staking import build_staking_page, lst_enter
from ui.components.swap import build_swap_page
from ui.components.transfer import (
    build_spl_token_page,
    build_token_page,
    open_spl_token_page,
)
from ui.components.walletconnect import build_wc_page, wc_enter
from ui.components.wallet_create import build_wallet_pages


async def build_app(page: flet.Page) -> None:
    """Bootstrap the whole app: page config, ctx, views, routing, PIN gate.

    This is the sole entry point invoked by ``main.py``'s ``async def main``.
    Everything the extracted ``ui/components/*`` modules need (shared
    Columns, the navbar, the back-nav handler) is registered in
    ``ctx.controls`` here before any view is built, matching the contract
    every prior group relied on.
    """
    page.scroll = flet.ScrollMode.AUTO
    page.title = "Solana Wallet"
    page.vertical_alignment = flet.MainAxisAlignment.CENTER
    page.horizontal_alignment = flet.CrossAxisAlignment.CENTER
    csv_file_picker = flet.FilePicker()
    page.services.append(csv_file_picker)
    page.bgcolor = 'white'
    page.padding = flet.Padding(top=50, left=10, right=10, bottom=10)

    if await page.shared_preferences.contains_key("theme_mode"):
        if await page.shared_preferences.get("theme_mode") == 'LIGHT':
            page.theme_mode = flet.ThemeMode.LIGHT
        elif await page.shared_preferences.get("theme_mode") == 'DARK':
            page.theme_mode = flet.ThemeMode.DARK
    else:
        page.theme_mode = flet.ThemeMode.LIGHT
        await page.shared_preferences.set("theme_mode", "LIGHT")

    # In-memory session state held only while the app is unlocked. The PIN
    # gate (ui/security_gate.py) mutates this dict via `ctx`; the Fernet
    # `key` lives only in memory (the PIN is never persisted).
    PIN_SALT_KEY = "security.pin_salt"
    PIN_VERIFIER_KEY = "security.pin_verifier"
    AUTO_LOCK_SECONDS = 300  # lock after 5 minutes of inactivity

    session = {
        "unlocked": False,        # is the session key currently available?
        "key": None,              # Fernet key derived from the PIN (in-memory only)
        "last_activity": time.time(),
        "lock_dialog": None,      # currently-shown lock/setup dialog (if any)
    }

    ctx = AppContext(
        page=page,
        session=session,
        pin_salt_key=PIN_SALT_KEY,
        pin_verifier_key=PIN_VERIFIER_KEY,
        auto_lock_seconds=AUTO_LOCK_SECONDS,
    )
    # UI language cache: read once here, then kept in sync by the Settings
    # dropdown's on_select. Every ctx.t(...) call reads this field, so a
    # language switch never needs to thread ``page`` through call sites.
    ctx.lang = await get_lang(page)
    # The CSV file picker is used by the Developer-mode "Save History as CSV"
    # button (history handler in ui/components/balance.py). It must be appended
    # to ``page.services`` (above) for the picker to actually render; exposed
    # via ctx.controls so the extracted handler reaches it without a dep on
    # this module.
    ctx.controls["csv_file_picker"] = csv_file_picker

    # ---- Shared Columns that the extracted modules bind + repopulate -------
    # Each Column is the live object a ``build_*_page(ctx)`` view wires into
    # its controls list and a ``*_enter(ctx)`` hook clears/rebuilds on each
    # visit. The View builders + enter hooks read them via ctx.controls, so
    # they must be registered before any view is built.
    for name in (
        "el_address_page",
        "el_token_balance_data",
        "el_address_book",
        "el_nft_page",
        "el_lst_page",
        "el_rawkey_page",
        "el_dev_storage_page",
        "el_token_page",
        "el_spl_token_page",
        "el_swap_page",
    ):
        ctx.controls[name] = flet.Column()

    # ===================== Navigation bar + routing ========================

    async def nav_more(e): await page.push_route("more-page")

    async def selected_navbar(e):
        idx = e.control.selected_index
        if idx == 0:
            await page.push_route("/")
        elif idx == 1:
            await page.push_route("create-wallet-page")
        elif idx == 2:
            await page.push_route("recover-wallet-page")
        elif idx == 3:
            await page.push_route("add-wallet-address-page")
        elif idx == 4:
            await page.push_route("more-page")

    navbar = flet.NavigationBar(
        on_change=selected_navbar,
        destinations=[
            flet.NavigationBarDestination(
                label="Home",
                icon=flet.Icon(flet.Icons.HOME_OUTLINED),
                selected_icon=flet.Icon(flet.Icons.HOME),
            ),
            flet.NavigationBarDestination(
                label="New",
                icon=flet.Icon(flet.Icons.ADD_OUTLINED),
                selected_icon=flet.Icon(flet.Icons.ADD),
            ),
            flet.NavigationBarDestination(
                label="Recover",
                icon=flet.Icon(flet.Icons.ROCKET_LAUNCH_OUTLINED),
                selected_icon=flet.Icon(flet.Icons.ROCKET_LAUNCH),
            ),
            flet.NavigationBarDestination(
                label="Add",
                icon=flet.Icon(flet.Icons.LINK_OUTLINED),
                selected_icon=flet.Icon(flet.Icons.LINK),
            ),
            flet.NavigationBarDestination(
                label="More",
                icon=flet.Icon(flet.Icons.APPS_OUTLINED),
                selected_icon=flet.Icon(flet.Icons.APPS),
            ),
        ],
    )
    ctx.controls["navbar"] = navbar

    async def view_pop(view):
        ctx.reset_activity()
        print(f'########### start >> page.views >> len={len(page.views)}, page.views: {page.views}')
        page.views.pop()
        print(f'########### after pop() >> page.views >> len={len(page.views)}, page.views: {page.views}')
        top_view = page.views[-1]
        await page.push_route(top_view.route)

    # Shared back-navigation handler used by every View's AppBar leading
    # button; registered so the extracted view builders can wire it.
    ctx.controls["view_pop"] = view_pop

    # ===================== Homepage ========================================
    # The root View (route="/"). Owns the logo + three wallet-entry buttons +
    # the wallet cards list. The cards list (``homepage.controls[-1]``) is
    # refreshed on every navigation by ``route_change``; the invariant
    # ``homepage.controls[-1] == wallets list`` is preserved.

    async def nav_recover(e): await page.push_route("recover-wallet-page")
    recover_wallet_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            width=200,
            content=flet.Column(controls=[flet.Image(src="recover.png"), flet.Text('Recover Wallet', size=12)])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=nav_recover,
    )

    async def nav_add(e): await page.push_route("add-wallet-address-page")
    add_wallet_address_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            width=200,
            content=flet.Column(controls=[flet.Image(src="add.png"), flet.Text('Add Wallet Address', size=12)])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=nav_add,
    )

    async def nav_create(e): await page.push_route('create-wallet-page')
    create_wallet_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            width=200,
            content=flet.Column(controls=[flet.Image(src="create.png"), flet.Text('New Wallet')])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=nav_create,
    )

    button_group_1 = flet.Row(
        width=page.width,
        alignment=flet.MainAxisAlignment.SPACE_EVENLY,
        controls=[
            create_wallet_button,
            recover_wallet_button,
            add_wallet_address_button,
        ],
    )

    homepage = flet.View(
        route="/",
        appbar=flet.AppBar(
            bgcolor="#1da1f2",
            color="white",
            title=flet.Text("Solana Wallet"),
            actions=[
                flet.IconButton(icon=flet.Icons.APPS, tooltip="More", on_click=nav_more),
            ],
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Solana', size=30, font_family="Georgia", weight=flet.FontWeight.BOLD),
            button_group_1,
            flet.Text('Wallets:', size=30, font_family="Georgia", weight=flet.FontWeight.BOLD),
            await get_wallets_cards(ctx),
        ],
    )

    # ===================== Per-route views (built once) ====================
    # Each View is built once here at bootstrap by its owning module's
    # ``build_*_page(ctx)``. The route dispatcher below appends the matching
    # view on each navigation; ``*_enter(ctx)`` hooks rebuild the shared
    # Column contents on each visit. Built in the same order + at the same
    # code location as the pre-refactor ``main.py`` so behaviour is identical.
    create_wallet_page, recover_wallet_page, add_wallet_address_page = await build_wallet_pages(ctx)
    dev_storage_page = build_dev_storage_page(ctx)
    sim_page = build_sim_page(ctx)
    rpc_page = build_rpc_page(ctx)
    raw_key_page = build_rawkey_page(ctx)
    wc_page = build_wc_page(ctx)
    address_page = build_address_page(ctx)
    token_page = build_token_page(ctx)
    spl_token_page = build_spl_token_page(ctx)
    swap_page = build_swap_page(ctx)
    addressbook_page = build_addressbook_page(ctx)
    nft_page = build_nft_page(ctx)
    stake_page = build_staking_page(ctx)
    more_page = build_more_page(ctx)
    settings_page = build_settings_page(ctx)

    # ===================== Route dispatcher ================================
    # The single source of truth for which view is shown on which route. The
    # branches are byte-identical to the pre-refactor ``route_change``; the
    # ``*_enter(ctx)`` calls that repopulate the shared Columns run before
    # the view is appended so the user always sees fresh content.

    async def route_change(route):
        ctx.reset_activity()
        page.views.clear()
        homepage.controls[-1] = await get_wallets_cards(ctx)
        page.views.append(homepage)
        if page.route == "create-wallet-page":
            page.views.append(create_wallet_page)
        elif page.route == "recover-wallet-page":
            page.views.append(recover_wallet_page)
        elif page.route == "add-wallet-address-page":
            page.views.append(add_wallet_address_page)
        elif page.route == "dev-storage-page":
            await dev_storage_enter(ctx)
            page.views.append(dev_storage_page)
        elif page.route == "address-page":
            ctx.controls["el_token_balance_data"].controls.clear()
            page.views.append(address_page)
        elif page.route == "token-page":
            page.views.append(token_page)
        elif page.route == "spl-token-page":
            page.views.append(spl_token_page)
        elif page.route == "swap-page":
            page.views.append(swap_page)
        elif page.route == "wc-page":
            await wc_enter(ctx)
            page.views.append(wc_page)
        elif page.route == "nft-page":
            await nft_enter(ctx, lambda data: open_spl_token_page(ctx, data))
            page.views.append(nft_page)
        elif page.route == "addressbook-page":
            await addressbook_enter(ctx)
            page.views.append(addressbook_page)
        elif page.route == "stake-page":
            await lst_enter(ctx)
            page.views.append(stake_page)
        elif page.route == "more-page":
            await more_enter(ctx)
            page.views.append(more_page)
        elif page.route == "settings-page":
            await settings_enter(ctx)
            page.views.append(settings_page)
        elif page.route == "sim-page":
            page.views.append(sim_page)
        elif page.route == "rpc-page":
            page.views.append(rpc_page)
        elif page.route == "raw-key-page":
            await rawkey_enter(ctx)
            page.views.append(raw_key_page)
        page.update()

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    await route_change(None)  # Manually trigger the initial UI load since push_route is ignored on identical paths
    await page.push_route(page.route)
    page.update()

    # Start the inactivity auto-lock watcher and present the PIN gate.
    asyncio.create_task(auto_lock_watcher(ctx))
    await refresh_lock_state(ctx)
