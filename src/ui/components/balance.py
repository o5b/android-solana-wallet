"""Balance / history / address page / wallet cards (extracted from ``main.py``).

Phase 7 Group 6e. The largest cohesive block of the migration: every screen
and handler that spins off the homepage wallet cards. Lifts:

* :func:`get_storage_data` — the legacy ``shared_preferences`` reader (injects
  ``storage_key`` into every dict record; the Wallet Info / delete flows still
  need this key, so :func:`ui.wallets.load_wallets` — which deliberately drops
  it — is not a drop-in replacement here).
* :func:`get_wallets_cards` — the homepage wallet ListView (one Card per
  wallet, with a "Show More" button → :func:`go_to_address_page`).
* :func:`delete_wallet_click` / :func:`wallet_info_click` /
  :func:`show_qr_click` — the three address-page action handlers (Wallet Info
  dialog with QR + copy-all-data + edit name/description; "Show QR Code"
  receive-SOL dialog; delete wallet).
* :func:`go_to_address_page` — builds the per-wallet address page (name /
  description / address / QR / network checkboxes / "Show Balance" /
  "Show History" buttons).
* :func:`get_history_button_click` — renders the transaction history with
  progressive disclosure per experience mode (Simple header-only / Pro
  expandable Signature-Status-Fee / Developer + Slot-Version-CU + logs + CSV
  export via the shared ``csv_file_picker``).
* :func:`get_balance_button_click` — renders SOL + SPL balances, USD portfolio
  banner, spam-filter summary banner, per-network results with transfer / swap
  / airdrop buttons. Progressive disclosure: Simple hides SPL + spam filter
  entirely (and uses the NFT-gallery fast path); Developer adds raw token
  dump + Solscan link (via :func:`ui.components.transfer` token-detail
  expander).
* :func:`build_address_page` — view builder that binds the shared
  ``el_address_page`` Column.

Coupling
--------
* The two shared Columns (``el_address_page`` / ``el_token_balance_data``) and
  the ``csv_file_picker`` are read from ``ctx.controls`` (registered by
  ``main()`` at bootstrap). The ``address_page`` View binds
  ``el_address_page`` directly via :func:`build_address_page`.
* The transfer / swap / airdrop / token-detail handlers are reused from
  :mod:`ui.components.transfer` / :mod:`ui.components.swap`. They have a
  ``(ctx, e)`` signature, so the balance screen wires them as flet
  ``on_click`` callbacks via **named ``async def`` adapter closures defined
  inside** :func:`get_balance_button_click` (Group 5 rule #7: never lambdas
  for async handlers — flet 0.82.2 only awaits handlers for which
  ``inspect.iscoroutinefunction`` is True, and a ``lambda e: coro_call`` is a
  sync callable whose returned coroutine is silently dropped). This also
  restores the 5 adapter closures that Group 6c accidentally deleted alongside
  ``lock_app``.
* The fixed-bug ``lambda e: page.clipboard.set(...)`` on the address page is
  replaced with a named ``async def`` adapter (the lambda silently dropped the
  clipboard coroutine — pre-existing latent bug surfaced by the move).

INVARIANTS preserved
--------------------
* ``homepage.controls[-1]`` is still the wallets list (the homepage View is
  built in ``main()``; :func:`get_wallets_cards` is called from
  ``route_change`` and the homepage builder, same as before).
* The ``data`` dict passed to balance/history/transfer/swap buttons is
  byte-identical to the legacy one (so the transfer handlers' positional reads
  keep working): ``wallet_address`` / ``network`` / ``spl_amount`` /
  ``sol_amount`` / ``symbol`` / ``raw_data`` / ``wallet_data``.
* The address-page control order (icon buttons / name / description / created
  / address / QR / network checkboxes / Show History / Show Balance /
  ``el_token_balance_data``) is preserved — the balance + history handlers
  read network checkbox state via ``e.control.parent.parent.controls[-3]
  .controls[0].controls[N]``.
* ``solana/`` business layer is never touched.
* No per-session mutable state in this module (the CSV export closure is
  per-click; the only state is the in-``ctx.controls`` Column holders).
"""

import asyncio
import json
from datetime import datetime

import flet

from solana.balance import get_sol_spl_balance
from solana.history_csv import transaction_history_to_csv
from solana.prices import enrich_balance_result_with_prices, fmt_change, fmt_usd
from solana.security import WATCH_ONLY_FIELD
from solana.spam_filter import (
    enrich_balance_result_with_spam_filter,
    is_hidden_spam,
    is_suspicious,
)
from solana.transaction_history import get_transaction_history
from ui.components.swap import go_to_swap_page_click
from ui.components.transfer import (
    go_to_spl_token_page_click,
    go_to_token_page_click,
    request_airdrop_click,
    spl_token_arrow_drop_down_click,
)
from ui.context import AppContext
from ui.experience import feature, get_experience
from ui.qr import generate_qr_base64

#: Mainnet RPC URL (used by the balance screen's "Swap" button disabled-flag).
_MAINNET_RPC = "https://api.mainnet-beta.solana.com"

#: (network name, RPC URL) tuples — the only networks the wallet talks to.
#: Read in declaration order by :func:`_collect_selected_networks`, mirroring
#: the order the network checkboxes are built in :func:`go_to_address_page`.
_NETWORKS = (
    ("mainnet-beta", "https://api.mainnet-beta.solana.com"),
    ("testnet", "https://api.testnet.solana.com"),
    ("devnet", "https://api.devnet.solana.com"),
)


def _collect_selected_networks(e, with_names: bool = False) -> list:
    """Read the 3 network checkboxes from the address-page event tree.

    The balance + history handlers read the user's network selection via the
    (fragile) positional chain ``e.control.parent.parent.controls[-3]
    .controls[0].controls[N].value`` — ``go_to_address_page`` builds exactly
    that Row → Column → 3 Checkboxes layout, in :data:`_NETWORKS` declaration
    order. Centralising the read here keeps the two handlers in lock-step if
    the layout ever changes.

    Parameters
    ----------
    e:
        The flet event carrying the address-page button's control tree.
    with_names:
        ``False`` (default) -> return ``[url, ...]`` for the balance handler.
        ``True`` -> return ``[(name, url), ...]`` for the history handler.
    """
    checks = e.control.parent.parent.controls[-3].controls[0].controls
    selected = [
        _NETWORKS[i]
        for i in range(min(len(_NETWORKS), len(checks)))
        if checks[i].value
    ]
    return selected if with_names else [url for _, url in selected]


# ============================ shared helpers ================================

async def get_storage_data(ctx: AppContext, prefix: str = "") -> list:
    """Read every ``shared_preferences`` value under ``prefix`` (legacy reader).

    JSON-decodes string values; injects ``storage_key`` into dict records so
    the Wallet Info / delete flows can mutate or remove the original entry.
    Mirrors the legacy ``main()`` closure verbatim (kept here instead of
    switching to :func:`ui.wallets.load_wallets` because the address page's
    Wallet Info dialog and delete handler both rely on ``storage_key``).
    """
    page = ctx.page
    data_list: list = []
    keys = await page.shared_preferences.get_keys(prefix)
    print(f"keys: {keys}")
    for key in keys:
        val = await page.shared_preferences.get(key)
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except json.JSONDecodeError:
                pass
        if isinstance(val, dict):
            val["storage_key"] = key
        data_list.append(val)
    print(f"data_list: {data_list}")
    return data_list


# ============================ wallet cards ==================================

async def get_wallets_cards(ctx: AppContext) -> flet.ListView:
    """Render the homepage wallet list (one Card per wallet)."""
    wallets = await get_storage_data(ctx, prefix="wallet.")
    print(f"wallets: {wallets}")

    # Group 5 rule #7: named async def adapter (NOT a lambda) so flet awaits it.
    async def _go_to_address_page(ev):
        await go_to_address_page(ctx, ev)

    lv = flet.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)
    for wallet in wallets:
        lv.controls.append(
            flet.Card(
                content=flet.Container(
                    content=flet.Column(
                        [
                            flet.Text(
                                ctx.t("wallet_name_label"),
                                size=16,
                                font_family="Georgia",
                                text_align=flet.TextAlign.RIGHT,
                                spans=[
                                    flet.TextSpan(
                                        f'{wallet["name"]}',
                                        flet.TextStyle(size=12, weight=flet.FontWeight.BOLD),
                                    ),
                                ],
                            ),
                            flet.Text(
                                ctx.t("wallet_desc_label"),
                                size=16,
                                font_family="Georgia",
                                text_align=flet.TextAlign.RIGHT,
                                spans=[
                                    flet.TextSpan(
                                        f'{wallet["description"]}',
                                        flet.TextStyle(size=12, weight=flet.FontWeight.BOLD),
                                    ),
                                ],
                            ),
                            flet.Text(
                                ctx.t("address_label"),
                                size=16,
                                font_family="Georgia",
                                selectable=True,
                                spans=[
                                    flet.TextSpan(
                                        f'{wallet["address_base58"]}',
                                        flet.TextStyle(size=12, weight=flet.FontWeight.BOLD),
                                    ),
                                ],
                            ),
                            flet.Text(
                                ctx.t("watch_only_badge"),
                                size=11, color="orange", weight=flet.FontWeight.BOLD,
                                visible=bool(wallet.get(WATCH_ONLY_FIELD)),
                            ),
                            flet.Divider(thickness=1),
                            flet.Row(
                                [
                                    flet.ElevatedButton(
                                        content=flet.Text(ctx.t("show_more")),
                                        on_click=_go_to_address_page,
                                        data=wallet,
                                    ),
                                ],
                                alignment=flet.MainAxisAlignment.START,
                            ),
                        ]
                    ),
                    width=400,
                    padding=10,
                )
            )
        )
    return lv


# ============================ address page actions ==========================

async def delete_wallet_click(ctx: AppContext, e):
    """Delete one wallet record from ``shared_preferences`` and return home."""
    page = ctx.page
    wallet = e.control.data
    if "storage_key" in wallet:
        await page.shared_preferences.remove(wallet["storage_key"])
        page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wallet_deleted_ok"))))
        await page.push_route("/")


async def wallet_info_click(ctx: AppContext, e):
    """Open the Wallet Info dialog (QR + reveal-all-fields + edit name/desc)."""
    page = ctx.page
    wallet = e.control.data

    def close_dlg(_e):
        dlg_info.open = False
        page.update()

    async def save_info(_e):
        if "storage_key" in wallet:
            wallet["name"] = tf_name.value
            wallet["description"] = tf_desc.value
            await page.shared_preferences.set(wallet["storage_key"], json.dumps(wallet))
            dlg_info.open = False
            page.update()
            await page.push_route("/")

    async def copy_data(_e):
        copy_src = ctx.decrypt_for_display(wallet)
        copy_val = {k: v for k, v in copy_src.items() if k != "storage_key"}
        await page.clipboard.set(json.dumps(copy_val, indent=2))

    tf_name = flet.TextField(label=ctx.t("field_name"), value=wallet.get("name", ""))
    tf_desc = flet.TextField(label=ctx.t("field_description"), value=wallet.get("description", ""), multiline=True)

    # Decrypt secrets on demand (records are stored encrypted once a PIN exists).
    w_dec = ctx.decrypt_for_display(wallet)
    watch_only_tag = ctx.t("watch_only_tag") if wallet.get(WATCH_ONLY_FIELD) else ""
    info_text = "\n".join([
        ctx.t("info_address", val=wallet.get('address_base58')),
        ctx.t("info_created", val=wallet.get('created')) + watch_only_tag,
        ctx.t("info_private_key", val=w_dec.get('private_key_hex')),
        ctx.t("info_public_key", val=w_dec.get('public_key_hex')),
        ctx.t("info_words", val=w_dec.get('words')),
        ctx.t("info_secret_key", val=w_dec.get('secret_key_base58')),
    ])

    dlg_info = flet.AlertDialog(
        title=flet.Text(ctx.t("wallet_info")),
        content=flet.Column(
            [
                tf_name,
                tf_desc,
                flet.Row(
                    [
                        flet.Image(
                            src=await asyncio.to_thread(
                                generate_qr_base64, wallet.get("address_base58", "")
                            ),
                            width=140,
                            height=140,
                            fit=flet.BoxFit.CONTAIN,
                            border_radius=flet.border_radius.all(8),
                        ),
                    ],
                    alignment=flet.MainAxisAlignment.CENTER,
                ),
                flet.Text(info_text, selectable=True, size=12),
                flet.ElevatedButton(ctx.t("copy_all_data"), on_click=copy_data, icon=flet.Icons.COPY),
            ],
            scroll=flet.ScrollMode.AUTO,
            height=400,
        ),
        actions=[
            flet.TextButton(ctx.t("save"), on_click=save_info),
            flet.TextButton(ctx.t("cancel"), on_click=close_dlg),
        ],
        actions_alignment=flet.MainAxisAlignment.END,
    )
    page.show_dialog(dlg_info)


async def show_qr_click(ctx: AppContext, e):
    """Open the 'Receive SOL' QR dialog for one wallet address."""
    page = ctx.page
    address = e.control.data

    def close_qr_dlg(_ev):
        dlg_qr.open = False
        page.update()

    qr_b64 = await asyncio.to_thread(generate_qr_base64, address)
    dlg_qr = flet.AlertDialog(
        title=flet.Text(ctx.t("receive_sol"), text_align=flet.TextAlign.CENTER),
        content=flet.Column(
            [
                flet.Image(src=qr_b64, width=280, height=280, fit=flet.BoxFit.CONTAIN),
                flet.Text(address, selectable=True, size=11, text_align=flet.TextAlign.CENTER),
            ],
            horizontal_alignment=flet.CrossAxisAlignment.CENTER,
            tight=True,
        ),
        actions=[flet.TextButton(ctx.t("close"), on_click=close_qr_dlg)],
        actions_alignment=flet.MainAxisAlignment.CENTER,
    )
    page.show_dialog(dlg_qr)


# ============================ address page builder ==========================

async def go_to_address_page(ctx: AppContext, e):
    """Build the per-wallet address page into ``ctx.controls["el_address_page"]``."""
    page = ctx.page
    print(f"****** go_to_address_page e.control.data: {e.control.data}")
    wallet = e.control.data
    el_address_page = ctx.controls["el_address_page"]
    el_token_balance_data = ctx.controls["el_token_balance_data"]

    # Group 5 rule #7: named async def adapters (NOT lambdas) so flet awaits them.
    async def _wallet_info(ev):
        await wallet_info_click(ctx, ev)

    async def _delete_wallet(ev):
        await delete_wallet_click(ctx, ev)

    async def _show_qr(ev):
        await show_qr_click(ctx, ev)

    async def _copy_address(_ev):
        # Was `lambda e: page.clipboard.set(...)` — silently dropped the
        # clipboard coroutine (flet 0.82.2 doesn't await lambda-returned
        # coroutines). Now a proper async adapter.
        await page.clipboard.set(wallet["address_base58"])

    async def _show_history(ev):
        await get_history_button_click(ctx, ev)

    async def _show_balance(ev):
        await get_balance_button_click(ctx, ev)

    qr_b64 = await asyncio.to_thread(generate_qr_base64, wallet["address_base58"])
    el_address_page.controls = [
        flet.Row(
            [
                flet.IconButton(icon=flet.Icons.INFO, tooltip=ctx.t("wallet_info"), on_click=_wallet_info, data=wallet),
                flet.IconButton(
                    icon=flet.Icons.DELETE, tooltip=ctx.t("delete_wallet"),
                    on_click=_delete_wallet, data=wallet, icon_color="red",
                ),
            ],
            alignment=flet.MainAxisAlignment.END,
        ),
        flet.Row(
            [
                flet.Text(
                    ctx.t("wallet_name_label"),
                    size=16, font_family="Georgia", text_align=flet.TextAlign.RIGHT,
                    spans=[
                        flet.TextSpan(
                            f'{wallet["name"]}',
                            flet.TextStyle(size=12, weight=flet.FontWeight.BOLD),
                        ),
                    ],
                ),
            ]
        ),
        flet.Row(
            [
                flet.Text(
                    ctx.t("wallet_desc_label"),
                    size=16, font_family="Georgia", text_align=flet.TextAlign.RIGHT,
                    spans=[
                        flet.TextSpan(
                            f'{wallet["description"]}',
                            flet.TextStyle(size=12, weight=flet.FontWeight.BOLD),
                        ),
                    ],
                ),
            ]
        ),
        flet.Row(
            [
                flet.Text(
                    "",
                    font_family="Georgia",
                    selectable=True,
                    text_align=flet.TextAlign.RIGHT,
                    spans=[
                        flet.TextSpan(ctx.t("created_label"), flet.TextStyle(size=16)),
                        flet.TextSpan(
                            f'{wallet["created"]}',
                            flet.TextStyle(size=12, weight=flet.FontWeight.BOLD),
                        ),
                    ],
                ),
            ]
        ),
        flet.Row(
            [
                flet.Text(
                    ctx.t("address_label"),
                    size=16, text_align=flet.TextAlign.RIGHT, font_family="Georgia",
                ),
            ]
        ),
        flet.Row(
            [
                flet.Text(
                    f'{wallet["address_base58"]}',
                    size=12, font_family="Georgia", weight=flet.FontWeight.BOLD,
                    text_align=flet.TextAlign.RIGHT, selectable=True,
                ),
            ]
        ),
        flet.Row(
            [
                flet.Image(
                    src=qr_b64, width=160, height=160, fit=flet.BoxFit.CONTAIN,
                    border_radius=flet.border_radius.all(8),
                ),
            ],
            alignment=flet.MainAxisAlignment.CENTER,
        ),
        flet.Row(
            [
                flet.ElevatedButton(
                    content=flet.Text(ctx.t("show_qr_code")),
                    icon=flet.Icons.QR_CODE_2,
                    on_click=_show_qr,
                    data=wallet["address_base58"],
                ),
                flet.IconButton(
                    icon=flet.Icons.CONTENT_COPY,
                    tooltip=ctx.t("copy_address"),
                    on_click=_copy_address,
                ),
            ],
            alignment=flet.MainAxisAlignment.CENTER,
        ),
        flet.Divider(thickness=2),
        flet.Row([flet.Text(ctx.t("solana_networks"), size=16, font_family="Georgia", weight=flet.FontWeight.BOLD)]),
        flet.Row(
            [
                flet.Column(
                    [
                        flet.Checkbox(label=ctx.t("net_mainnet_beta"), value=True),
                        flet.Checkbox(label=ctx.t("net_testnet"), value=False),
                        flet.Checkbox(label=ctx.t("net_devnet"), value=False),
                    ]
                ),
            ],
            alignment=flet.MainAxisAlignment.START,
        ),
        flet.Row(
            [
                flet.ElevatedButton(
                    content=flet.Text(ctx.t("show_history")),
                    on_click=_show_history,
                    data=wallet,
                ),
                flet.ElevatedButton(
                    content=flet.Text(ctx.t("show_balance")),
                    on_click=_show_balance,
                    data=wallet,
                ),
            ],
            alignment=flet.MainAxisAlignment.END,
        ),
        el_token_balance_data,
    ]
    await page.push_route("address-page")


# ============================ history button ================================

async def get_history_button_click(ctx: AppContext, e):
    """Render the transaction history for the selected networks.

    Progressive disclosure (Phase 5): Simple = header only; Pro = + expandable
    Signature / Status-Fee; Developer = + Slot-Version-CU + logs + CSV button.
    """
    page = ctx.page
    el_token_balance_data = ctx.controls["el_token_balance_data"]
    csv_file_picker = ctx.controls["csv_file_picker"]
    try:
        wallet = e.control.data
        print(f"****** address >> get_history_button_click: {wallet}")
        el_token_balance_data.controls.clear()
        page.update()

        # Walk the address-page network checkboxes (shared helper so the
        # history + balance handlers can't drift on the positional read).
        networks = _collect_selected_networks(e, with_names=True)

        e.control.disabled = True   # блокируем кнопку
        el_token_balance_data.controls.append(
            flet.Row([flet.ProgressRing(), flet.Text(ctx.t("loading_history"))], alignment=flet.MainAxisAlignment.CENTER)
        )
        page.update()

        tmp_history_result = [flet.Divider(thickness=3)]
        csv_history = []

        # Progressive disclosure (Phase 5):
        # - Simple   -> header only (time/type/amount/status), no expandable details.
        # - Pro      -> + expandable Signature/Status/Fee.
        # - Developer -> + Slot/Version/CU + logs + CSV export button.
        _mode = await get_experience(page)
        show_detail = feature("history_detail", _mode)
        show_tech = feature("history_tech", _mode)
        show_csv = feature("csv_export", _mode)

        for net_name, net_url in networks:
            tmp_history_result.append(
                flet.Row([flet.Text(ctx.t("network_label", net=net_name), size=16, weight=flet.FontWeight.BOLD)])
            )

            # Запрашиваем историю
            history_data = await get_transaction_history(wallet["address_base58"], net_url)

            if "error" in history_data:
                tmp_history_result.append(flet.Text(ctx.t("error_prefix", err=history_data['error']), color="red"))
            elif "result" in history_data and history_data["result"]:
                csv_history.append((net_name, history_data["result"]))
                for tx in history_data["result"]:
                    time_str = (
                        datetime.fromtimestamp(tx["block_time"]).strftime("%Y-%m-%d %H:%M:%S")
                        if tx["block_time"]
                        else ctx.t("unknown")
                    )

                    sol_change = tx.get("sol_change", 0)
                    if sol_change > 0:
                        change_color, change_sign = "green", "+"
                    elif sol_change < 0:
                        change_color, change_sign = "red", ""
                    else:
                        change_color = "black" if page.theme_mode == flet.ThemeMode.LIGHT else "white"
                        change_sign = ""

                    balance_spans = [
                        flet.TextSpan(
                            f"{change_sign}{sol_change:.9f} SOL",
                            flet.TextStyle(size=14, color=change_color, weight=flet.FontWeight.BOLD),
                        )
                    ]

                    if "spl_changes" in tx and tx["spl_changes"]:
                        for spl in tx["spl_changes"]:
                            change = spl["change"]
                            spl_color = "green" if change > 0 else "red"
                            spl_sign = "+" if change > 0 else ""

                            # Если символ найден, используем его, иначе режем mint адрес
                            display_name = spl.get("symbol") or f"{spl['mint'][:4]}...{spl['mint'][-4:]}"

                            balance_spans.append(
                                flet.TextSpan(
                                    f"\n{spl_sign}{change} ",
                                    flet.TextStyle(size=14, color=spl_color, weight=flet.FontWeight.BOLD),
                                )
                            )
                            balance_spans.append(
                                flet.TextSpan(f"{display_name}", flet.TextStyle(size=12, color="grey"))
                            )

                    # Изолированная функция для создания интерактивной карточки
                    def create_tx_card(tx_data, t_str, b_spans):
                        # Progressive disclosure: Simple = header only;
                        # Pro = + expandable Signature/Status/Fee;
                        # Developer = + Slot/Version/CU + logs.
                        _status_word = ctx.t(
                            "status_success" if tx_data["success"] else "status_failed"
                        )
                        header_lines = [
                            flet.Text(
                                f"{t_str} • {tx_data.get('tx_type') or ctx.t('unknown')}",
                                size=12, weight=flet.FontWeight.BOLD, color="grey700",
                            ),
                            flet.Text(spans=b_spans),
                        ]
                        # Simple mode has no expandable details, so surface
                        # the status directly under the amount.
                        if not show_detail:
                            header_lines.append(
                                flet.Text(
                                    _status_word,
                                    size=11,
                                    color="green" if tx_data["success"] else "red",
                                )
                            )

                        if not show_detail:
                            return flet.Card(
                                content=flet.Container(
                                    padding=10,
                                    content=flet.Column(header_lines),
                                )
                            )

                        details_inner = [
                            flet.Divider(thickness=1),
                            flet.Text(ctx.t("signature_label", sig=tx_data['signature']), selectable=True, size=12, italic=True),
                            flet.Text(
                                ctx.t(
                                    "history_status_fee",
                                    status=_status_word,
                                    fee=f"{tx_data.get('fee', 0):.9f}",
                                ),
                                size=12,
                                color="green" if tx_data["success"] else "red",
                            ),
                        ]
                        if show_tech:
                            # Формируем логи в виде прокручиваемого списка
                            logs_controls = [flet.Text(ctx.t("logs_label"), size=12, weight=flet.FontWeight.BOLD)]
                            if tx_data.get("logs"):
                                for log in tx_data["logs"]:
                                    # Подсвечиваем ошибки красным для удобства
                                    log_color = (
                                        "red"
                                        if "failed" in log.lower() or "error" in log.lower()
                                        else "grey"
                                    )
                                    logs_controls.append(
                                        flet.Text(f"• {log}", size=10, color=log_color, selectable=True)
                                    )
                            else:
                                logs_controls.append(flet.Text(ctx.t("no_logs"), size=10, color="grey"))

                            # Оборачиваем логи в Column с фиксированной высотой и скроллом
                            logs_column = flet.Container(
                                content=flet.Column(logs_controls, spacing=2, scroll=flet.ScrollMode.AUTO),
                                height=100,
                                padding=5,
                                border=flet.border.all(1, "black12"),
                                border_radius=5,
                            )
                            details_inner.append(
                                flet.Text(
                                    ctx.t(
                                        "slot_version_cu",
                                        slot=tx_data.get('slot'),
                                        version=tx_data.get('version'),
                                        cu=tx_data.get('compute_units'),
                                    ),
                                    size=12, color="blue",
                                )
                            )
                            details_inner.append(logs_column)

                        # Скрытая колонка с деталями
                        details_col = flet.Column(visible=False, controls=details_inner)

                        # Обработчик кнопки-стрелки
                        def toggle_details(te):
                            details_col.visible = not details_col.visible
                            te.control.icon = (
                                flet.Icons.ARROW_DROP_UP if details_col.visible else flet.Icons.ARROW_DROP_DOWN
                            )
                            te.control.update()
                            details_col.update()

                        return flet.Card(
                            content=flet.Container(
                                padding=10,
                                content=flet.Column(
                                    [
                                        flet.Row(
                                            [
                                                flet.Column(header_lines, expand=True),
                                                flet.IconButton(
                                                    icon=flet.Icons.ARROW_DROP_DOWN,
                                                    icon_size=30,
                                                    on_click=toggle_details,
                                                ),
                                            ],
                                            alignment=flet.MainAxisAlignment.SPACE_BETWEEN,
                                            vertical_alignment=flet.CrossAxisAlignment.START,
                                        ),
                                        details_col,
                                    ]
                                ),
                            )
                        )

                    # Добавляем созданную интерактивную карточку в общий список
                    tmp_history_result.append(create_tx_card(tx, time_str, balance_spans))

            else:
                tmp_history_result.append(flet.Text(ctx.t("no_transactions"), italic=True))

            tmp_history_result.append(flet.Divider(thickness=1))

        if csv_history and show_csv:
            csv_content = transaction_history_to_csv(csv_history)

            async def export_history_csv_click(_):
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                saved_path = await csv_file_picker.save_file(
                    dialog_title=ctx.t("save_history_csv_title"),
                    file_name=f"solana-history-{timestamp}.csv",
                    file_type=flet.FilePickerFileType.CUSTOM,
                    allowed_extensions=["csv"],
                    src_bytes=csv_content.encode("utf-8-sig"),
                )
                if saved_path:
                    page.show_dialog(
                        flet.AlertDialog(
                            title=flet.Text(ctx.t("csv_saved")),
                            content=flet.Text(ctx.t("csv_saved_to", path=saved_path)),
                        )
                    )

            tmp_history_result.insert(
                1,
                flet.Row(
                    [
                        flet.ElevatedButton(
                            content=flet.Text(ctx.t("save_history_csv")),
                            icon=flet.Icons.DOWNLOAD,
                            on_click=export_history_csv_click,
                        ),
                    ],
                    alignment=flet.MainAxisAlignment.END,
                ),
            )

        el_token_balance_data.controls.clear()
        el_token_balance_data.controls.extend(tmp_history_result)
        e.control.disabled = False  # разблокируем кнопку

    except Exception as er:
        print(f"Error get_history_button_click: {er}")
        page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("err_loading_history"))))
        e.control.disabled = False
    finally:
        page.update()


# ============================ balance button ================================

async def get_balance_button_click(ctx: AppContext, e):
    """Render SOL + SPL balances, USD portfolio + spam banners, transfer/swap rows.

    The biggest handler in the app. Progressive disclosure:
    * **Simple** — native SOL rows only + USD banner (SOL-only subtotal); skips
      the slow per-token SPL fetch (NFT-gallery fast path).
    * **Pro**    — + SPL token rows + spam filter (hidden spam / suspicious badges).
    * **Developer** — + raw token dump + Solscan link (via the transfer module's
      token-detail expander).
    """
    page = ctx.page
    el_token_balance_data = ctx.controls["el_token_balance_data"]

    # Group 5 rule #7: named async def adapters (NOT lambdas) so flet awaits
    # them. The transfer / swap handlers have a (ctx, e) signature; flet only
    # awaits handlers for which inspect.iscoroutinefunction() is True, so a
    # plain `lambda ev: go_to_token_page_click(ctx, ev)` would silently drop
    # the coroutine. (These five adapters also restore the closures that
    # Group 6c accidentally deleted alongside `lock_app`.)
    async def on_go_to_token_page(ev):
        await go_to_token_page_click(ctx, ev)

    async def on_go_to_spl_token_page(ev):
        await go_to_spl_token_page_click(ctx, ev)

    async def on_spl_arrow_drop_down(ev):
        await spl_token_arrow_drop_down_click(ctx, ev)

    async def on_request_airdrop(ev):
        await request_airdrop_click(ctx, ev)

    async def on_go_to_swap_page(ev):
        await go_to_swap_page_click(ctx, ev)

    try:
        wallet = e.control.data
        print(f"****** address >> get_balance_button_click: {wallet}")
        el_token_balance_data.controls.clear()
        page.update()
        # Walk the address-page network checkboxes (shared helper).
        networks = _collect_selected_networks(e)
        print(f"networks: {networks}")
        e.control.disabled = True   # блокируем кнопку
        el_token_balance_data.controls.append(
            flet.Row([flet.ProgressRing(), flet.Text(ctx.t("please_wait"))], alignment=flet.MainAxisAlignment.CENTER)
        )
        page.update()
        tmp_balance_result = []
        start = datetime.now()
        # Progressive disclosure (Phase 5): SPL tokens, the spam filter and
        # the raw token dump are Pro/Developer-only. Simple mode shows just
        # the native SOL rows + the USD portfolio banner, so it also skips
        # the slow per-token priority-fee RPC + raw image-byte downloads
        # (same fast path as the NFT gallery). SOL USD pricing still works
        # via the wrapped-SOL mint in enrich_balance_result_with_prices.
        mode = await get_experience(page)
        show_spl = feature("spl_tokens", mode)
        result = await get_sol_spl_balance(
            wallet["address_base58"],
            networks,
            include_transfer_cost=show_spl,
            include_image_bytes=show_spl,
        )
        print(f"****** get_sol_spl_balance result: {result}")

        # USD pricing (Jupiter Price API v3). Values are attached only to
        # mainnet entries — devnet/testnet holdings have no real value.
        try:
            price_info = await enrich_balance_result_with_prices(result)
            print(f"****** price_info: {price_info}")
        except Exception as price_er:
            print(f"price enrichment skipped: {price_er}")
            price_info = {"total_usd": 0.0, "priced": 0, "tokens": 0, "mainnet": False}

        # Spam / scam token filter. Runs AFTER pricing so it can use the
        # real-market-liquidity signal (token['usd_price']) to downgrade an
        # isolated open-mint-authority hit. Hides confirmed spam, badges
        # suspicious tokens. Never breaks balance display on failure.
        # Skipped in Simple mode (no SPL tokens are rendered anyway).
        spam_info = {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}
        if show_spl:
            try:
                spam_info = await enrich_balance_result_with_spam_filter(result)
                print(f"****** spam_info: {spam_info}")
            except Exception as spam_er:
                print(f"spam enrichment skipped: {spam_er}")
                spam_info = {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}

        for i, r in enumerate(result):
            tmp_balance_spl = []
            tmp_spam_spl = []
            spam_token_count = 0

            # Builds the (Transfer button + logo + amount text) + expand
            # detail row pair for one token. Defined per-network so it can
            # close over `wallet` and the current `r` without late-binding.
            def _build_spl_token_controls(spl_token):
                token_symbol = ""
                if "symbol_metaplex" in spl_token:
                    token_symbol += f"{spl_token['symbol_metaplex']} (symbol_metaplex) "
                if "symbol_2022" in spl_token:
                    token_symbol += f"{spl_token['symbol_2022']} (symbol_2022)"
                spl_token_logo = flet.Image(
                    width=100,
                    height=100,
                    src="spl-token-placeholder.png",
                    fit=flet.BoxFit.CONTAIN,
                    border_radius=flet.border_radius.all(10),
                )
                if "logo" in spl_token and spl_token["logo"]:
                    spl_token_logo.src = spl_token["logo"]
                # USD value spans (mainnet-priced tokens only)
                _spl_usd_spans = []
                if spl_token.get("usd_value") is not None:
                    _spl_usd_spans.append(
                        flet.TextSpan(
                            f"   {fmt_usd(spl_token['usd_value'])}",
                            flet.TextStyle(size=14, color=flet.Colors.GREY_700),
                        )
                    )
                    if spl_token.get("change_24h") is not None:
                        _chg = spl_token["change_24h"]
                        _spl_usd_spans.append(
                            flet.TextSpan(
                                f" {fmt_change(_chg)}",
                                flet.TextStyle(
                                    size=12,
                                    color=flet.Colors.GREEN if _chg >= 0 else flet.Colors.RED,
                                ),
                            )
                        )
                return [
                    flet.Row(
                        [
                            flet.ElevatedButton(
                                content=flet.Text(ctx.t("transfer_this_token")),
                                on_click=on_go_to_spl_token_page,
                                data={
                                    "wallet_address": wallet["address_base58"],
                                    "network": r["network"],
                                    "spl_amount": spl_token["amount"],
                                    "symbol": token_symbol,
                                    "sol_amount": r["sol"],
                                    "raw_data": spl_token,
                                    "wallet_data": wallet,
                                },
                                disabled=(
                                    False
                                    if (r["sol"] and spl_token["amount"]
                                        and r["sol"] > spl_token["transfer_cost"]["total_sol"])
                                    else True
                                ),
                            ),
                            spl_token_logo,
                            flet.Text(
                                value="",
                                spans=[
                                    flet.TextSpan(
                                        f"{spl_token['amount']}",
                                        flet.TextStyle(size=16, weight=flet.FontWeight.BOLD),
                                    ),
                                    flet.TextSpan(f" {token_symbol}", flet.TextStyle(size=16)),
                                    *_spl_usd_spans,
                                ],
                            ),
                        ],
                    ),
                    flet.Column(
                        [
                            flet.Row(
                                [
                                    flet.TextButton(
                                        content=flet.Row(
                                            [flet.Icon(flet.Icons.ARROW_DROP_DOWN, size=50)],
                                        ),
                                        on_click=on_spl_arrow_drop_down,
                                        data={
                                            **{
                                                k: v
                                                for k, v in spl_token.items()
                                                if k not in ("logo", "spam")
                                            },
                                            "network": r["network"],
                                        },
                                    ),
                                ],
                                alignment=flet.MainAxisAlignment.CENTER,
                            ),
                        ],
                    ),
                ]

            if show_spl:
                for spl_token in r["spl"]:
                    if spl_token["amount"] <= 0:
                        continue
                    # Confirmed-spam tokens are hidden behind a toggle; they
                    # can still be inspected/shown, but don't clutter the list.
                    if is_hidden_spam(spl_token):
                        spam_token_count += 1
                        tmp_spam_spl.extend(_build_spl_token_controls(spl_token))
                        continue
                    # Suspicious (not confirmed) tokens stay visible but are
                    # badged with the detection reasons so the user is warned.
                    if is_suspicious(spl_token):
                        _sv = spl_token.get("spam") or {}
                        _reasons = ", ".join(_sv.get("reasons") or []) or ctx.t("flagged_risky")
                        tmp_balance_spl.append(
                            flet.Row(
                                [
                                    flet.Icon(
                                        flet.Icons.WARNING_AMBER_ROUNDED,
                                        color=flet.Colors.ORANGE, size=18,
                                    ),
                                    flet.Text(
                                        ctx.t("suspicious_label", reasons=_reasons),
                                        size=12, color=flet.Colors.ORANGE_800, selectable=True,
                                    ),
                                ],
                            )
                        )
                    tmp_balance_spl.extend(_build_spl_token_controls(spl_token))

                # "N spam tokens hidden" expander. Hidden rows live in a column
                # that is shown on demand; the toggle carries the column ref in
                # its `data` so the handler needs no per-loop closure state.
                if tmp_spam_spl:
                    _spam_col = flet.Column(controls=tmp_spam_spl, visible=False)

                    async def _toggle_spam(te):
                        col = te.control.data
                        col.visible = not col.visible
                        await page.update()

                    tmp_balance_spl.extend([
                        flet.Container(
                            content=flet.TextButton(
                                on_click=_toggle_spam,
                                data=_spam_col,
                                content=flet.Row(
                                    [
                                        flet.Icon(flet.Icons.WARNING, color=flet.Colors.RED, size=18),
                                        flet.Text(
                                            ctx.tp("spam_hidden_click_pl", "spam_hidden_click_sg",
                                                   spam_token_count, mid="spam_hidden_click_mid"),
                                            size=12, color=flet.Colors.RED,
                                        ),
                                    ],
                                ),
                            ),
                            padding=flet.padding.symmetric(vertical=2, horizontal=8),
                            margin=flet.margin.only(top=4, bottom=4),
                            bgcolor=flet.Colors.with_opacity(0.08, flet.Colors.RED),
                            border_radius=flet.border_radius.all(8),
                        ),
                        _spam_col,
                    ])
            tmp_request_airdrop = []
            if (
                r["network"] == "https://api.testnet.solana.com"
                or r["network"] == "https://api.devnet.solana.com"
            ):
                tmp_request_airdrop.append(
                    flet.ElevatedButton(
                        content=flet.Text(ctx.t("request_airdrop_1")),
                        on_click=on_request_airdrop,
                        data={
                            "wallet_address": wallet["address_base58"],
                            "network": r["network"],
                            "sol_amount": r["sol"],
                            "symbol": "SOL",
                            "wallet_data": wallet,
                        },
                        disabled=False,
                    ),
                )
            # USD value spans for native SOL (mainnet-priced rows only)
            _sol_usd_spans = []
            if r.get("sol_usd") is not None:
                _sol_usd_spans.append(
                    flet.TextSpan(
                        f"   {fmt_usd(r['sol_usd'])}",
                        flet.TextStyle(size=14, color=flet.Colors.GREY_700),
                    )
                )
                if r.get("sol_change_24h") is not None:
                    _chg = r["sol_change_24h"]
                    _sol_usd_spans.append(
                        flet.TextSpan(
                            f" {fmt_change(_chg)}",
                            flet.TextStyle(
                                size=12,
                                color=flet.Colors.GREEN if _chg >= 0 else flet.Colors.RED,
                            ),
                        )
                    )
            tmp_balance_result.extend(
                [
                    flet.Row(
                        [
                            flet.Text(
                                value="",
                                spans=[
                                    flet.TextSpan(
                                        ctx.t("network_label", net=r['network']),
                                        flet.TextStyle(size=16, weight=flet.FontWeight.BOLD),
                                    )
                                ],
                            ),
                        ],
                    ),
                    flet.Row(
                        [
                            flet.ElevatedButton(
                                content=flet.Text(ctx.t("transfer_this_token")),
                                on_click=on_go_to_token_page,
                                data={
                                    "wallet_address": wallet["address_base58"],
                                    "network": r["network"],
                                    "sol_amount": r["sol"],
                                    "symbol": "SOL",
                                    "wallet_data": wallet,
                                },
                                disabled=False if r["sol"] else True,
                            ),
                            flet.ElevatedButton(
                                content=flet.Text(ctx.t("swap_btn")),
                                on_click=on_go_to_swap_page,
                                data={
                                    "wallet_address": wallet["address_base58"],
                                    "network": r["network"],
                                    "sol_amount": r["sol"],
                                    "wallet_data": wallet,
                                },
                                disabled=(r["network"] != _MAINNET_RPC) or (not r["sol"]),
                            ),
                            flet.Text(
                                value="",
                                spans=[
                                    flet.TextSpan(
                                        f"{r['sol']}",
                                        flet.TextStyle(size=16, weight=flet.FontWeight.BOLD),
                                    ),
                                    flet.TextSpan(" SOL", flet.TextStyle(size=16)),
                                    *_sol_usd_spans,
                                ],
                            ),
                            *tmp_request_airdrop,
                        ],
                    ),
                    *tmp_balance_spl,
                ]
            )
            if i < len(result) - 1:  # добавляем разделяющую линию после каждого результата кроме последнего
                tmp_balance_result.append(flet.Divider(thickness=1))
        el_token_balance_data.controls.clear()
        _balance_controls = [flet.Divider(thickness=3)]
        # Portfolio value banner (mainnet holdings only). In Simple mode
        # SPL rows are hidden, so the banner must reflect native SOL only
        # (otherwise it advertises value the user cannot see). sol_usd is
        # only attached to mainnet entries by enrich_balance_result_with_prices.
        _banner_total = price_info.get("total_usd", 0.0)
        _note = ""
        if not show_spl:
            _banner_total = sum(nr.get("sol_usd") or 0.0 for nr in result)
        else:
            _priced = price_info.get("priced", 0)
            _tokens = price_info.get("tokens", 0)
            _note = "" if _priced or not _tokens else ctx.t("no_priced_tokens")
        if price_info.get("mainnet") and _banner_total:
            _balance_controls.append(
                flet.Container(
                    content=flet.Row(
                        [
                            flet.Text(
                                value="",
                                spans=[
                                    flet.TextSpan(
                                        ctx.t("portfolio_value"),
                                        flet.TextStyle(size=14, color=flet.Colors.GREY_700),
                                    ),
                                    flet.TextSpan(
                                        fmt_usd(_banner_total),
                                        flet.TextStyle(size=22, weight=flet.FontWeight.BOLD),
                                    ),
                                ],
                            ),
                        ],
                        alignment=flet.MainAxisAlignment.CENTER,
                    ),
                    padding=flet.padding.symmetric(vertical=6, horizontal=10),
                    margin=flet.margin.only(bottom=6),
                    bgcolor=flet.Colors.with_opacity(0.08, flet.Colors.GREEN),
                    border_radius=flet.border_radius.all(10),
                )
            )
            if _note:
                _balance_controls.append(flet.Text(_note.strip(), size=12, color=flet.Colors.GREY_500))
        # Spam-filter summary banner (when anything was flagged). Spam
        # tokens are hidden behind per-network toggles; suspicious tokens
        # are shown with an inline badge. This banner makes the filtering
        # visible even before the user scrolls to a token list.
        if spam_info.get("flagged"):
            _spam_txt = []
            if spam_info.get("spam"):
                _spam_txt.append(ctx.tp("spam_count_pl", "spam_count_sg", spam_info['spam']))
            if spam_info.get("suspicious"):
                _spam_txt.append(ctx.tp("suspicious_count_pl", "suspicious_count_sg", spam_info['suspicious']))
            _balance_controls.append(
                flet.Container(
                    content=flet.Row(
                        [
                            flet.Icon(flet.Icons.SHIELD_OUTLINED, color=flet.Colors.RED_700, size=18),
                            flet.Text(
                                ctx.t("spam_filter_summary", summary=" / ".join(_spam_txt)),
                                size=12, color=flet.Colors.RED_700, selectable=True,
                            ),
                        ],
                    ),
                    padding=flet.padding.symmetric(vertical=4, horizontal=10),
                    margin=flet.margin.only(bottom=6),
                    bgcolor=flet.Colors.with_opacity(0.06, flet.Colors.RED),
                    border_radius=flet.border_radius.all(10),
                )
            )
        _balance_controls.extend(tmp_balance_result)
        el_token_balance_data.controls.extend(_balance_controls)
        e.control.disabled = False  # разблокируем кнопку
        print(f"time: {datetime.now() - start} sec")
        page.show_dialog(
            flet.AlertDialog(
                title=flet.Text(ctx.t("balance_received_ok", addr=wallet['address_base58'])),
            )
        )
    except Exception as er:
        print(f"Error get_balance_button_click: {er}")
        el_token_balance_data.controls.clear()
        el_token_balance_data.controls.append(
            flet.Text(ctx.t("error_prefix", err=er), color=flet.Colors.RED, size=14)
        )
        try:
            e.control.disabled = False
        except Exception:
            pass
        page.show_dialog(
            flet.AlertDialog(title=flet.Text(ctx.t("err_balance"))),
        )
    finally:
        try:
            e.control.disabled = False
        except Exception:
            pass
        page.update()


# ============================ address page View =============================

def build_address_page(ctx: AppContext) -> flet.View:
    """Build the per-wallet address-page View once at bootstrap.

    Binds the shared ``el_address_page`` Column (registered in ``ctx.controls``
    by ``main()``). The balance / history handlers populate
    ``ctx.controls["el_token_balance_data"]`` which is itself a child of
    ``el_address_page`` (appended by :func:`go_to_address_page`).
    """
    return flet.View(
        route="address-page",
        appbar=flet.AppBar(
            title=flet.Text(ctx.t("address_page_title")),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=ctx.controls["view_pop"]),
        ),
        navigation_bar=ctx.controls["navbar"],
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text(ctx.t("information"), size=30, font_family="Georgia"),
            ctx.controls["el_address_page"],
        ],
    )
