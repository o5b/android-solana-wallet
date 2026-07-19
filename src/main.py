import asyncio
import json
import time

import flet

# create_solana_wallet -> used by ui.components.wallet_create (Phase 7 Group 6a).
# get_sol_spl_balance / enrich_balance_result_with_prices / fmt_usd / fmt_change /
# enrich_balance_result_with_spam_filter / is_hidden_spam / is_suspicious /
# get_transaction_history / transaction_history_to_csv / feature / get_experience /
# WATCH_ONLY_FIELD / short_addr -> moved to ui.components.balance (Phase 7 Group 6e)
# and earlier modules. main.py no longer uses them directly.
# transfer / burn / airdrop / SNS / signing-key helpers live in
# ui.components.transfer (Phase 7); main.py no longer uses them directly.
# jup_get_quote / jup_swap -> moved to ui.components.swap (Phase 7 Group 6b).
# make_poisoning_banner / update_poisoning_banner / open_contact_picker /
# open_save_contact_dialog / maybe_block_for_poisoning -> moved to
# ui.components.transfer (Phase 7 Group 5); only addressbook_enter is still
# wired here (route_change).
from ui.context import AppContext
from ui.components.addressbook import addressbook_enter
from ui.components.balance import (
    build_address_page,
    get_wallets_cards,
)
from ui.components.devtools import (
    build_sim_page,
    build_rpc_page,
    build_rawkey_page,
    rawkey_enter,
)
from ui.components.nft import nft_enter
from ui.components.staking import lst_enter
from ui.components.transfer import (
    build_spl_token_page,
    build_token_page,
    open_spl_token_page,
)
from ui.components.walletconnect import build_wc_page, wc_enter
from ui.components.wallet_create import build_wallet_pages
from ui.components.swap import build_swap_page
from ui.components.more import build_more_page, more_enter
from ui.components.settings import build_settings_page, settings_enter
# clear_client_storage -> moved to ui/components/more.py (Phase 7 Group 6d):
# it's only called from the More hub's "Clear all storage" destructive flow.
from ui.security_gate import (
    auto_lock_watcher,
    refresh_lock_state,
)

# LAMPORT_TO_SOL_RATIO = 10 ** 9

# SWAP_TOKENS (mainnet token registry) -> moved to ui.components.swap
# (Phase 7 Group 6b). MAINNET_RPC -> moved to ui.components.balance (Phase 7
# Group 6e); every ui/ module that needs it (balance / swap / staking / nft /
# devtools) carries its own local constant now, so main.py no longer defines
# it.

async def main(page: flet.Page):
    page.scroll = flet.ScrollMode.AUTO
    page.title = "Solana Wallet"
    page.vertical_alignment = flet.MainAxisAlignment.CENTER
    page.horizontal_alignment = flet.CrossAxisAlignment.CENTER
    csv_file_picker = flet.FilePicker()
    page.services.append(csv_file_picker)
    page.bgcolor = 'white'
    page.padding = flet.Padding(top=50, left=10, right=10, bottom=10)
    # page.scroll = flet.ScrollMode.AUTO
    # page.theme_mode = flet.ThemeMode.LIGHT

    if await page.shared_preferences.contains_key("theme_mode"):
        if await page.shared_preferences.get("theme_mode") == 'LIGHT':
            page.theme_mode = flet.ThemeMode.LIGHT
        elif await page.shared_preferences.get("theme_mode") == 'DARK':
            page.theme_mode = flet.ThemeMode.DARK
    else:
        page.theme_mode = flet.ThemeMode.LIGHT
        await page.shared_preferences.set("theme_mode", "LIGHT")

    # ---------------------------------------------------------------------------
    # Security: PIN gate, encrypted secrets, auto-lock on inactivity.
    # The PIN gate (setup/unlock dialogs, lock_app, refresh_lock_state,
    # auto_lock_watcher, migrate_plaintext_wallets, clear_client_storage)
    # -> moved to ui/security_gate.py (Phase 7 Group 6c). The in-memory
    # `session` dict and the PIN constants stay here; the module mutates
    # both via `ctx`.
    # ---------------------------------------------------------------------------
    PIN_SALT_KEY = "security.pin_salt"
    PIN_VERIFIER_KEY = "security.pin_verifier"
    AUTO_LOCK_SECONDS = 300  # lock after 5 minutes of inactivity

    # Session state held only in memory while the app is unlocked.
    session = {
        "unlocked": False,        # is the session key currently available?
        "key": None,              # Fernet key derived from the PIN (in-memory only)
        "last_activity": time.time(),
        "lock_dialog": None,      # currently-shown lock/setup dialog (if any)
    }

    # Shared context handed to ui/ modules (Phase 7 refactor). It wraps the live
    # `page` + `session` objects by reference so legacy closures and extracted
    # modules share one source of truth during the incremental migration.
    ctx = AppContext(
        page=page,
        session=session,
        pin_salt_key=PIN_SALT_KEY,
        pin_verifier_key=PIN_VERIFIER_KEY,
        auto_lock_seconds=AUTO_LOCK_SECONDS,
    )
    # The CSV file picker is used by the Developer-mode "Save History as CSV"
    # button (history handler in ui/components/balance.py). It must be appended
    # to ``page.services`` (above) for the picker to actually render; we also
    # expose it via ctx.controls so the extracted handler reaches it without a
    # dependency on main.py.
    ctx.controls["csv_file_picker"] = csv_file_picker

    # make_priority_fee_block / _pf_from_data -> moved to ui/components/priority_fee.py (Phase 7).
    # resolve_signing_key -> moved to ui/components/transfer.py (Phase 7 Group 5).
    # Wallet-key accessors (is_unlocked / get_wallet_private_key /
    # has_wallet_private_key / encrypt_for_storage / decrypt_for_display) ->
    # all moved to AppContext (Phase 7 Group 6c); main.py call sites use ctx.*.

    # ===================== Wallet cards + balance + history + address page ====
    # get_storage_data / get_wallets_cards / delete_wallet_click /
    # wallet_info_click / show_qr_click / go_to_address_page /
    # get_history_button_click / get_balance_button_click + the address-page
    # View builder -> moved to ui/components/balance.py (Phase 7 Group 6e).
    # `generate_qr_base64` -> moved to ui/qr.py (pure helper, no flet dep).
    # The two shared Columns (``el_address_page`` / ``el_token_balance_data``)
    # are still created here and registered in ctx.controls; the address-page
    # View (built by ``build_address_page(ctx)``) binds ``el_address_page``
    # directly, and the handlers mutate both Columns through ctx.controls.

    el_address_page = flet.Column()
    el_token_balance_data = flet.Column()
    # Register the shared controls with ctx so the extracted balance module
    # (Phase 7 Group 6e) can read/mutate them. The `address_page` view built
    # by ``build_address_page(ctx)`` binds ``el_address_page`` directly;
    # ``el_token_balance_data`` is appended to ``el_address_page`` by
    # ``go_to_address_page`` and repopulated by the balance/history handlers.
    ctx.controls["el_address_page"] = el_address_page
    ctx.controls["el_token_balance_data"] = el_token_balance_data
    el_address_book = flet.Column()
    # Register the shared control with ctx so the extracted address-book module
    # can rebuild it (Phase 7). The `addressbook_page` view below still binds
    # this same object directly.
    ctx.controls["el_address_book"] = el_address_book
    el_nft_page = flet.Column()
    el_lst_page = flet.Column()
    # Register the shared controls with ctx so the extracted NFT gallery and
    # Liquid staking modules can rebuild them (Phase 7). The `nft_page` /
    # `stake_page` views below still bind these same objects directly.
    ctx.controls["el_nft_page"] = el_nft_page
    ctx.controls["el_lst_page"] = el_lst_page
    el_rawkey_page = flet.Column()
    # Register the shared control with ctx so the extracted devtools module can
    # rebuild it (Phase 7). The `raw_key_page` view below still binds this same
    # object directly.
    ctx.controls["el_rawkey_page"] = el_rawkey_page

    # ===================== Wallet cards + balance + history + address ========
    # The address-page action handlers (delete_wallet / wallet_info / show_qr),
    # `go_to_address_page`, the balance + history handlers
    # (`get_balance_button_click` / `get_history_button_click`) and the wallet
    # cards list (`get_wallets_cards`) -> moved to ui/components/balance.py
    # (Phase 7 Group 6e). They are wired into the homepage + the address-page
    # buttons via named ``async def`` adapter closures defined at the call
    # sites (Group 5 rule #7; never lambdas for async handlers — flet 0.82.2
    # only awaits handlers for which ``inspect.iscoroutinefunction`` is True,
    # so a ``lambda e: coro_call`` silently drops the coroutine). The 5
    # ``on_go_to_*`` adapter closures that Group 6c accidentally deleted
    # alongside ``lock_app`` are now defined inside ``get_balance_button_click``
    # in the balance module — the regression is fixed by the move itself.

    el_token_page = flet.Column()
    el_spl_token_page = flet.Column()
    # Register the shared transfer-page holders with ctx so the extracted
    # transfer module can clear/rebuild them on each visit (Phase 7 Group 5).
    # The `token_page` / `spl_token_page` views below still bind these same
    # objects directly.
    ctx.controls["el_token_page"] = el_token_page
    ctx.controls["el_spl_token_page"] = el_spl_token_page
    el_swap_page = flet.Column()
    # Register the swap-page holder with ctx so the extracted swap module
    # (Phase 7 Group 6b) can clear/rebuild it on each visit. The `swap_page`
    # view below still binds this same object directly.
    ctx.controls["el_swap_page"] = el_swap_page

    # go_to_swap_page_button_click / get_quote_button_click / swap_button_click
    # -> moved to ui/components/swap.py (Phase 7 Group 6b) as
    # `go_to_swap_page_click(ctx, e)`. The balance-screen "Swap" button is
    # wired via the `on_go_to_swap_page` adapter closure below (Group 5 rule:
    # named `async def` adapter, never a lambda).

    # ===================== Transfer screens =====================
    # SPL/SOL transfer pages, burn/close, airdrop, token-detail expander,
    # `resolve_recipient_input` (SNS) and `resolve_signing_key` -> moved to
    # ui/components/transfer.py (Phase 7 Group 5). The handlers are wired
    # into the balance screen + NFT gallery via `(ctx, e)` adapter lambdas;
    # `open_spl_token_page(ctx, data)` is also injected into `nft_enter`.

    # ===================== Create / Recover / Add wallet pages =================
    # The three wallet-entry Views (Create New Wallet / Recover Wallet / Add
    # Wallet Address) + their form fields, save / clear / copy handlers, the
    # seed-phrase backup quiz, the success and error cards -> moved to
    # ui/components/wallet_create.py (Phase 7 Group 6a). The Views are built
    # once here at bootstrap; the form fields persist across navigations
    # (legacy "global objects" behaviour). `encrypt_for_storage` lives on ctx.

    # ===================== Theme + Experience level ============================
    # theme_changed / theme_control / experience_dd / experience_desc /
    # settings_enter / _apply_experience / experience_changed /
    # _show_dev_warning / _cancel_dev_warning / _confirm_dev_warning
    # -> moved to ui/components/settings.py (Phase 7 Group 6d). The Settings
    # page View + the three long-lived controls are built by
    # build_settings_page(ctx) at bootstrap; settings_enter(ctx) re-reads the
    # persisted mode on each visit. theme_mode is still initialized above (in
    # main) so the bootstrap UI uses the persisted theme before the Settings
    # page is built.

    async def dev_tools_storage_list():
        lv = flet.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)
        keys = await page.shared_preferences.get_keys('')
        for i, key in enumerate(keys):
            val = await page.shared_preferences.get(key)
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    pass
            lv.controls.append(
                flet.Row(
                    scroll=flet.ScrollMode.AUTO,
                    controls=[
                        flet.ElevatedButton(content="Delete", on_click=storage_delete_button_click, data=key),
                        flet.Text(f"{i+1}. {key}: {val}", max_lines=2),
                    ]
                )
            )
        return lv

    async def storage_delete_button_click(e):
        try:
            await page.shared_preferences.remove(e.control.data)
        except Exception as er:
            print(f'Error deleted data from shared_preferences: {er}')
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text("Во время удаления произошла ошибка!"),
                )
            )
        else:
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text(f"{e.control.data} успешно удалён!"),
                )
            )
        page.update()

    # clear_client_storage -> moved to ui/security_gate.py (Phase 7 Group 6c).

    # ===================== More hub navigation handlers ========================
    # nav_addressbook / nav_dev_storage / nav_wc / nav_nft / nav_stake /
    # nav_settings / nav_sim / nav_rpc / nav_rawkey / clear_storage_click /
    # _do_clear_storage / _hub_item / more_enter -> moved to
    # ui/components/more.py (Phase 7 Group 6d). They are defined as tiny
    # `async def` closures inside more_enter (so they capture `page` from ctx)
    # and the More page View is built by build_more_page(ctx).
    #
    # nav_more stays here: it's the homepage AppBar "More" action icon
    # (homepage stays in main.py — it migrates with the orchestrator group 6g).
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
    # Shared across every View's navigation_bar; registered so the extracted
    # devtools (and future ui/) view builders can reference it (Phase 7).
    ctx.controls["navbar"] = navbar

    # ===================== WalletConnect v2 =====================
    # The WalletConnect v2 responder UI + the WalletConnectClient callbacks
    # (_wc_*/on_wc_*) -> moved to ui/components/walletconnect.py. The four
    # long-lived WC controls (URI input / projectId input / status text /
    # sessions list) are registered in ctx.controls by build_wc_page(ctx); the
    # per-session live client lives in ctx.session["_wc_state"].

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
            page.views.append(dev_storage_page)
        elif page.route == "address-page":
            el_token_balance_data.controls.clear()
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
        # else:
        #     page.views.append(homepage)
        page.update()

    async def view_pop(view):
        ctx.reset_activity()
        print(f'########### start >> page.views >> len={len(page.views)}, page.views: {page.views}')
        page.views.pop()
        print(f'########### after pop() >> page.views >> len={len(page.views)}, page.views: {page.views}')
        top_view = page.views[-1]
        await page.push_route(top_view.route)

    # Shared back-navigation handler used by every View's AppBar leading button;
    # registered so the extracted devtools (and future ui/) view builders can
    # wire it (Phase 7).
    ctx.controls["view_pop"] = view_pop

    async def nav_recover(e): await page.push_route("recover-wallet-page")
    recover_wallet_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            width=200,
            content=flet.Column(controls=[flet.Image(src="recover.png"), flet.Text('Recover Wallet', size=12)])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=nav_recover
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
        on_click=nav_add
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
        on_click=nav_create
        # on_click=lambda _:await page.push_route('create-wallet-page')
    )

    button_group_1 = flet.Row(
        width=page.width,
        alignment=flet.MainAxisAlignment.SPACE_EVENLY,
        controls=[
            create_wallet_button,
            recover_wallet_button,
            add_wallet_address_button,
        ]
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
            # flet.Image(src="solana.jpg", width=page.width, height=200, fit=flet.ImageFit.FILL),
            flet.Text('Solana', size=30, font_family="Georgia", weight=flet.FontWeight.BOLD),
            button_group_1,
            flet.Text('Wallets:', size=30, font_family="Georgia", weight=flet.FontWeight.BOLD),
            await get_wallets_cards(ctx),
        ],
    )

    # Wallet-entry Views are built once here at bootstrap by the extracted
    # wallet_create module (Phase 7 Group 6a). `route_change` appends each view
    # on its matching route, exactly as before.
    create_wallet_page, recover_wallet_page, add_wallet_address_page = await build_wallet_pages(ctx)

    dev_storage_page = flet.View(
        route="dev-storage-page",
        appbar=flet.AppBar(
            title=flet.Text("DevTools: Storage"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text(value='Редактирование client_storage:', size=20),
            await dev_tools_storage_list(),
        ]
    )

    # =================== Developer: dev tools pages =========================
    # Simulation inspector / Raw RPC inspector / Export raw keys -> moved to
    # ui/components/devtools.py (Phase 7). The three Views are built once here
    # (they reference ctx.controls["view_pop"] / ["navbar"]); rawkey_enter(ctx)
    # rebuilds ctx.controls["el_rawkey_page"] on each visit, mirroring the
    # address-book enter pattern. analyze_transaction / httpx now live in the
    # devtools module, so their imports were removed from main.py.
    sim_page = build_sim_page(ctx)
    rpc_page = build_rpc_page(ctx)
    raw_key_page = build_rawkey_page(ctx)

    wc_page = build_wc_page(ctx)

    address_page = build_address_page(ctx)

    token_page = build_token_page(ctx)
    spl_token_page = build_spl_token_page(ctx)
    swap_page = build_swap_page(ctx)

    addressbook_page = flet.View(
        route="addressbook-page",
        appbar=flet.AppBar(
            title=flet.Text("Address Book"),
            color="white",
            bgcolor="#0d9488",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Address Book', size=30, font_family="Georgia"),
            el_address_book,
        ]
    )

    nft_page = flet.View(
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
            el_nft_page,
        ]
    )

    stake_page = flet.View(
        route="stake-page",
        appbar=flet.AppBar(
            title=flet.Text("Liquid Staking"),
            color="white",
            bgcolor="#0d9488",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Liquid Staking', size=30, font_family="Georgia"),
            el_lst_page,
        ]
    )

    # ===================== More hub + Settings pages ===========================
    # More hub + Settings page Views are built once here at bootstrap by the
    # extracted modules (Phase 7 Group 6d). `more_enter(ctx)` rebuilds the
    # hub's controls on each visit (experience-mode filtering); the View itself
    # is registered in `ctx.controls["more_page"]` so `more_enter` can mutate
    # it. `build_settings_page(ctx)` registers `theme_control` /
    # `experience_dd` / `experience_desc` in `ctx.controls`; `settings_enter
    # (ctx)` re-reads the persisted mode into them on each visit.
    more_page = build_more_page(ctx)
    settings_page = build_settings_page(ctx)

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    await route_change(None) # Manually trigger the initial UI load since push_route is ignored on identical paths
    await page.push_route(page.route)
    page.update()

    # Start the inactivity auto-lock watcher and present the PIN gate.
    asyncio.create_task(auto_lock_watcher(ctx))
    await refresh_lock_state(ctx)


flet.run(main)
