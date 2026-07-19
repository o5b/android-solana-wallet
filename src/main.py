from datetime import datetime
import asyncio
import time
import os
import flet
import base64
import json
import io
import qrcode

# create_solana_wallet -> used by ui.components.wallet_create (Phase 7 Group 6a).
from solana.balance import get_sol_spl_balance
# transfer / burn / airdrop / SNS / signing-key helpers live in
# ui.components.transfer (Phase 7); main.py no longer uses them directly.
# jup_get_quote / jup_swap -> moved to ui.components.swap (Phase 7 Group 6b).
from solana.prices import enrich_balance_result_with_prices, fmt_usd, fmt_change
from solana.spam_filter import (
    enrich_balance_result_with_spam_filter,
    is_hidden_spam,
    is_suspicious,
)
# is_valid_amount -> moved to ui.components.swap (Phase 7 Group 6b).
from solana.transaction_history import get_transaction_history
from solana.history_csv import transaction_history_to_csv
from solana.security import (
    WALLET_ENCRYPTED_FIELD,
    WATCH_ONLY_FIELD,
    SECRET_FIELDS,
    MIN_PIN_LENGTH,
    make_salt,
    derive_key,
    make_verifier,
    verify_pin,
    validate_pin,
    encode_salt,
    decode_salt,
    encrypt_wallet_secrets,
    decrypt_wallet_secrets,
    get_secret,
)
from ui.experience import (
    SIMPLE,
    DEVELOPER,
    MODES,
    label as experience_label,
    description as experience_description,
    feature,
    get_experience,
    set_experience,
    has_seen_dev_warning,
    mark_dev_warning_seen,
)
from ui.context import AppContext
from ui.formatting import short_addr as _short_addr
from ui.components.priority_fee import (
    make_priority_fee_block,
    pf_from_data as _pf_from_data,
)
from ui.components.addressbook import (
    make_poisoning_banner,
    update_poisoning_banner,
    open_contact_picker,
    open_save_contact_dialog,
    maybe_block_for_poisoning as _maybe_block_for_poisoning,
    addressbook_enter,
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
    burn_and_close_click,
    burn_spl_click,
    go_to_spl_token_page_click,
    go_to_token_page_click,
    open_spl_token_page,
    request_airdrop_click,
    resolve_recipient_input,
    resolve_signing_key,
    spl_token_arrow_drop_down_click,
    spl_token_arrow_drop_up_click,
    transfer_sol_click,
    transfer_spl_click,
)
from ui.components.walletconnect import build_wc_page, wc_enter
from ui.components.wallet_create import build_wallet_pages
from ui.components.swap import build_swap_page, go_to_swap_page_click


def generate_qr_base64(data: str, box_size: int = 8, border: int = 2) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

# LAMPORT_TO_SOL_RATIO = 10 ** 9

# SWAP_TOKENS (mainnet token registry) -> moved to ui.components.swap
# (Phase 7 Group 6b). MAINNET_RPC stays here: it's also used by the
# balance-screen "Swap" button's `disabled=` flag.
MAINNET_RPC = "https://api.mainnet-beta.solana.com"

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

    def reset_activity():
        """Mark now as the most recent user activity (postpones auto-lock)."""
        session["last_activity"] = time.time()

    async def load_pin():
        """Return (salt_bytes, verifier_str) or (None, None) if no PIN is set."""
        if not await page.shared_preferences.contains_key(PIN_SALT_KEY):
            return None, None
        salt_str = await page.shared_preferences.get(PIN_SALT_KEY)
        verifier = await page.shared_preferences.get(PIN_VERIFIER_KEY)
        if not salt_str or not verifier:
            return None, None
        try:
            return decode_salt(salt_str), verifier
        except Exception:
            return None, None

    async def save_pin(salt: bytes, verifier: str):
        await page.shared_preferences.set(PIN_SALT_KEY, encode_salt(salt))
        await page.shared_preferences.set(PIN_VERIFIER_KEY, verifier)

    async def migrate_plaintext_wallets(key: bytes):
        """Encrypt the secrets of every legacy (plaintext) wallet record.

        Called once after a PIN is first set up.  Already-encrypted records
        and watch-only wallets (empty secrets) are handled gracefully.
        """
        keys = await page.shared_preferences.get_keys("wallet.")
        for k in keys:
            val = await page.shared_preferences.get(k)
            if not isinstance(val, str):
                continue
            try:
                wallet = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(wallet, dict):
                continue
            if wallet.get(WALLET_ENCRYPTED_FIELD):
                continue  # already encrypted
            encrypted = encrypt_wallet_secrets(wallet, key)
            await page.shared_preferences.set(k, json.dumps(encrypted))

    def is_unlocked() -> bool:
        return session["unlocked"] and session["key"] is not None

    # encrypt_for_storage -> moved to AppContext (Phase 7 Group 6a). The
    # legacy `get_wallet_private_key`/`has_wallet_private_key` closures below
    # stay until their remaining main.py call sites migrate to ctx.*.

    def get_wallet_private_key(wallet: dict) -> str:
        """Plaintext private key hex for a wallet ('' if watch-only / locked)."""
        if not is_unlocked():
            return ""
        return get_secret(wallet, "private_key_hex", session["key"])

    def has_wallet_private_key(wallet: dict) -> bool:
        return bool(get_wallet_private_key(wallet))

    # make_priority_fee_block / _pf_from_data -> moved to ui/components/priority_fee.py (Phase 7).
    # resolve_signing_key -> moved to ui/components/transfer.py (Phase 7 Group 5).

    def decrypt_for_display(wallet: dict) -> dict:
        """Wallet dict with secrets decrypted (for the Wallet Info dialog)."""
        if not is_unlocked():
            return wallet
        return decrypt_wallet_secrets(wallet, session["key"])

    # Async click-handler adapters binding the extracted transfer module's
    # `(ctx, e)` handlers into flet's `(e)` on_click signature (lambdas would
    # silently drop the returned coroutine — Phase 7 Group 5).
    async def on_go_to_spl_token_page(e): await go_to_spl_token_page_click(ctx, e)
    async def on_go_to_token_page(e): await go_to_token_page_click(ctx, e)
    async def on_spl_arrow_drop_down(e): await spl_token_arrow_drop_down_click(ctx, e)
    async def on_request_airdrop(e): await request_airdrop_click(ctx, e)
    async def on_go_to_swap_page(e): await go_to_swap_page_click(ctx, e)

    async def lock_app():
        """Drop the in-memory key and require the PIN again."""
        session["unlocked"] = False
        session["key"] = None
        await refresh_lock_state()

    def close_lock_dialog():
        if session["lock_dialog"] is not None:
            session["lock_dialog"].open = False
            session["lock_dialog"] = None
            page.update()

    async def show_setup_dialog():
        tf1 = flet.TextField(
            label=f"Create a PIN ({MIN_PIN_LENGTH}+ digits)", password=True,
            can_reveal_password=True, keyboard_type=flet.KeyboardType.NUMBER, autofocus=True,
        )
        tf2 = flet.TextField(
            label="Confirm PIN", password=True, can_reveal_password=True,
            keyboard_type=flet.KeyboardType.NUMBER,
        )
        err = flet.Text("", color="red")

        async def confirm(ev):
            p1, p2 = tf1.value or "", tf2.value or ""
            if not validate_pin(p1):
                err.value = f"PIN must be {MIN_PIN_LENGTH}+ digits."
                page.update(); return
            if p1 != p2:
                err.value = "PINs do not match."
                page.update(); return
            salt = make_salt()
            key = derive_key(p1, salt)
            verifier = make_verifier(key)
            await save_pin(salt, verifier)
            await migrate_plaintext_wallets(key)
            session["unlocked"] = True
            session["key"] = key
            reset_activity()
            close_lock_dialog()

        dlg = flet.AlertDialog(
            modal=True,
            title=flet.Text("Set up a PIN"),
            content=flet.Column(
                [
                    flet.Text("This PIN encrypts your private keys at rest and unlocks the app. Do not forget it: lost PINs cannot be recovered.", size=12),
                    tf1, tf2, err,
                ],
                tight=True,
            ),
            actions=[flet.ElevatedButton("Set PIN", on_click=confirm)],
            actions_alignment=flet.MainAxisAlignment.END,
        )
        session["lock_dialog"] = dlg
        page.show_dialog(dlg)

    async def show_unlock_dialog():
        tf = flet.TextField(
            label="Enter PIN", password=True, can_reveal_password=True,
            keyboard_type=flet.KeyboardType.NUMBER, autofocus=True,
        )
        err = flet.Text("", color="red")

        async def do_unlock(ev):
            salt, verifier = await load_pin()
            pin = tf.value or ""
            if salt is not None and verify_pin(pin, salt, verifier):
                session["unlocked"] = True
                session["key"] = derive_key(pin, salt)
                # Defensive: encrypt any wallet that slipped through while locked.
                await migrate_plaintext_wallets(session["key"])
                reset_activity()
                tf.value = ""
                err.value = ""
                close_lock_dialog()
            else:
                err.value = "Incorrect PIN."
                page.update()

        async def forgot_pin(ev):
            # Losing the PIN means the encrypted secrets are unrecoverable.
            # Offer a destructive reset that wipes the whole wallet store.
            async def do_wipe(inner):
                await clear_client_storage()
                session["unlocked"] = False
                session["key"] = None
                session["lock_dialog"] = None
                confirm_dlg.open = False
                page.update()
                await refresh_lock_state()  # PIN wiped -> shows the setup dialog

            async def cancel(inner):
                confirm_dlg.open = False
                page.update()
                await show_unlock_dialog()  # re-show the unlock dialog

            confirm_dlg = flet.AlertDialog(
                modal=True,
                title=flet.Text("Reset everything?"),
                content=flet.Text("This will permanently delete the PIN and ALL stored wallets (their encrypted keys become unrecoverable). Only continue if you have your seed phrases backed up."),
                actions=[
                    flet.TextButton("Cancel", on_click=cancel),
                    flet.ElevatedButton("Reset & Wipe", on_click=do_wipe, icon=flet.Icons.DELETE_FOREVER),
                ],
                actions_alignment=flet.MainAxisAlignment.END,
            )
            page.show_dialog(confirm_dlg)

        dlg = flet.AlertDialog(
            modal=True,
            title=flet.Text("Enter PIN"),
            content=flet.Column([tf, err], tight=True),
            actions=[
                flet.ElevatedButton("Unlock", on_click=do_unlock),
                flet.TextButton("Forgot PIN?", on_click=forgot_pin),
            ],
            actions_alignment=flet.MainAxisAlignment.END,
        )
        tf.on_submit = do_unlock
        session["lock_dialog"] = dlg
        page.show_dialog(dlg)

    async def refresh_lock_state():
        """Show the setup dialog (first run) or unlock dialog (subsequent runs)."""
        salt, _ = await load_pin()
        if salt is None:
            await show_setup_dialog()
        else:
            await show_unlock_dialog()

    async def auto_lock_watcher():
        """Periodically lock the app after AUTO_LOCK_SECONDS of inactivity."""
        while True:
            await asyncio.sleep(10)
            if (
                session["lock_dialog"] is None
                and is_unlocked()
                and (time.time() - session["last_activity"]) > AUTO_LOCK_SECONDS
            ):
                await lock_app()

    async def get_storage_data(prefix=''):
        data_list = []
        keys = await page.shared_preferences.get_keys(prefix)
        print(f'keys: {keys}')
        for key in keys:
            val = await page.shared_preferences.get(key)
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    pass
            if isinstance(val, dict):
                val['storage_key'] = key
            data_list.append(val)
        print(f'data_list: {data_list}')
        return data_list

    # ===================== Address book + poisoning protection =====================
    # Address book, contact dialogs, the live poisoning banner, the blocking
    # poisoning gate, and `_short_addr` -> moved to ui/components/addressbook.py
    # (+ ui/formatting.py). The Address Book page control is registered in
    # `ctx.controls["el_address_book"]`. `resolve_recipient_input` (SNS helper)
    # -> moved to ui/components/transfer.py (Phase 7 Group 5).

    async def get_wallets_cards():
        wallets = await get_storage_data(prefix="wallet.")
        print(f'wallets: {wallets}')
        lv = flet.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)
        for wallet in wallets:
            lv.controls.append(
                flet.Card(
                    content=flet.Container(
                        content=flet.Column(
                            [
                                flet.Text(
                                    "Wallet Name: ",
                                    size=16,
                                    font_family="Georgia",
                                    # weight=flet.FontWeight.BOLD,
                                    text_align=flet.TextAlign.RIGHT,
                                    spans=[
                                        flet.TextSpan(f'{wallet['name']}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                                    ],
                                ),
                                flet.Text(
                                    "Wallet Description: ",
                                    size=16,
                                    font_family="Georgia",
                                    # weight=flet.FontWeight.BOLD,
                                    text_align=flet.TextAlign.RIGHT,
                                    spans=[
                                        flet.TextSpan(f'{wallet['description']}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                                    ],
                                ),
                                flet.Text(
                                    "Address: ",
                                    size=16,
                                    font_family="Georgia",
                                    # weight=flet.FontWeight.BOLD,
                                    selectable=True,
                                    spans=[
                                        flet.TextSpan(f'{wallet['address_base58']}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                                    ]
                                ),
                                flet.Text(
                                    "Watch-only (no private key)",
                                    size=11, color="orange", weight=flet.FontWeight.BOLD,
                                    visible=bool(wallet.get(WATCH_ONLY_FIELD)),
                                ),
                                flet.Divider(thickness=1),
                                flet.Row(
                                    [
                                        flet.ElevatedButton(
                                            content=flet.Text("Show More"),
                                            on_click=go_to_address_page,
                                            data=wallet,
                                        ),
                                        # flet.Text("Real Network", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                                    ],
                                    alignment=flet.MainAxisAlignment.START,
                                ),
                                # flet.Column([]),
                            ]
                        ),
                        width=400,
                        padding=10,
                    )
                )
            )
        return lv

    el_address_page = flet.Column()
    el_token_balance_data = flet.Column()
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

    async def delete_wallet_click(e):
        wallet = e.control.data
        if 'storage_key' in wallet:
            await page.shared_preferences.remove(wallet['storage_key'])
            page.show_dialog(flet.AlertDialog(title=flet.Text("Wallet deleted successfully!")))
            await page.push_route("/")

    async def wallet_info_click(e):
        wallet = e.control.data

        def close_dlg(e):
            dlg_info.open = False
            page.update()

        async def save_info(e):
            if 'storage_key' in wallet:
                wallet['name'] = tf_name.value
                wallet['description'] = tf_desc.value
                await page.shared_preferences.set(wallet['storage_key'], json.dumps(wallet))
                dlg_info.open = False
                page.update()
                await page.push_route("/")

        async def copy_data(e):
            copy_src = decrypt_for_display(wallet)
            copy_val = {k: v for k, v in copy_src.items() if k != 'storage_key'}
            await page.clipboard.set(json.dumps(copy_val, indent=2))

        tf_name = flet.TextField(label="Name", value=wallet.get('name', ''))
        tf_desc = flet.TextField(label="Description", value=wallet.get('description', ''), multiline=True)

        # Decrypt secrets on demand (records are stored encrypted once a PIN exists).
        w_dec = decrypt_for_display(wallet)
        watch_only_tag = "  (watch-only)" if wallet.get(WATCH_ONLY_FIELD) else ""
        info_text = f"Address: {wallet.get('address_base58')}\n" \
                    f"Created: {wallet.get('created')}{watch_only_tag}\n" \
                    f"Private Key: {w_dec.get('private_key_hex')}\n" \
                    f"Public Key: {w_dec.get('public_key_hex')}\n" \
                    f"Words: {w_dec.get('words')}\n" \
                    f"Secret Key (base58): {w_dec.get('secret_key_base58')}"

        dlg_info = flet.AlertDialog(
            title=flet.Text("Wallet Info"),
            content=flet.Column([
                tf_name,
                tf_desc,
                flet.Row(
                    [
                        flet.Image(
                            src=await asyncio.to_thread(generate_qr_base64, wallet.get('address_base58', '')),
                            width=140,
                            height=140,
                            fit=flet.BoxFit.CONTAIN,
                            border_radius=flet.border_radius.all(8),
                        ),
                    ],
                    alignment=flet.MainAxisAlignment.CENTER,
                ),
                flet.Text(info_text, selectable=True, size=12),
                flet.ElevatedButton("Copy All Data", on_click=copy_data, icon=flet.Icons.COPY)
            ], scroll=flet.ScrollMode.AUTO, height=400),
            actions=[
                flet.TextButton("Save", on_click=save_info),
                flet.TextButton("Cancel", on_click=close_dlg)
            ],
            actions_alignment=flet.MainAxisAlignment.END
        )
        page.show_dialog(dlg_info)

    async def show_qr_click(e):
        address = e.control.data

        def close_qr_dlg(ev):
            dlg_qr.open = False
            page.update()

        qr_b64 = await asyncio.to_thread(generate_qr_base64, address)
        dlg_qr = flet.AlertDialog(
            title=flet.Text("Receive SOL", text_align=flet.TextAlign.CENTER),
            content=flet.Column(
                [
                    flet.Image(
                        src=qr_b64,
                        width=280,
                        height=280,
                        fit=flet.BoxFit.CONTAIN,
                    ),
                    flet.Text(address, selectable=True, size=11, text_align=flet.TextAlign.CENTER),
                ],
                horizontal_alignment=flet.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            actions=[
                flet.TextButton("Close", on_click=close_qr_dlg),
            ],
            actions_alignment=flet.MainAxisAlignment.CENTER,
        )
        page.show_dialog(dlg_qr)

    async def go_to_address_page(e):
        print(f'****** go_to_address_page e.control.data: {e.control.data}')
        wallet = e.control.data
        qr_b64 = await asyncio.to_thread(generate_qr_base64, wallet["address_base58"])
        el_address_page.controls = [
            flet.Row(
                [
                    flet.IconButton(icon=flet.Icons.INFO, tooltip="Wallet Info", on_click=wallet_info_click, data=wallet),
                    flet.IconButton(icon=flet.Icons.DELETE, tooltip="Delete Wallet", on_click=delete_wallet_click, data=wallet, icon_color="red"),
                ],
                alignment=flet.MainAxisAlignment.END
            ),
            flet.Row(
                [
                    flet.Text(
                        "Wallet Name: ",
                        size=16,
                        font_family="Georgia",
                        text_align=flet.TextAlign.RIGHT,
                        spans=[
                            flet.TextSpan(f'{wallet["name"]}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                        ],
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        "Wallet Description: ",
                        size=16,
                        font_family="Georgia",
                        text_align=flet.TextAlign.RIGHT,
                        spans=[
                            flet.TextSpan(f'{wallet["description"]}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
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
                            flet.TextSpan('Created: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{wallet["created"]}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                        ]
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        'Address: ',
                        size=16,
                        text_align=flet.TextAlign.RIGHT,
                        font_family="Georgia",
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        f'{wallet["address_base58"]}',
                        size=12,
                        font_family="Georgia",
                        weight=flet.FontWeight.BOLD,
                        text_align=flet.TextAlign.RIGHT,
                        selectable=True,
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Image(
                        src=qr_b64,
                        width=160,
                        height=160,
                        fit=flet.BoxFit.CONTAIN,
                        border_radius=flet.border_radius.all(8),
                    ),
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Row(
                [
                    flet.ElevatedButton(
                        content=flet.Text("Show QR Code"),
                        icon=flet.Icons.QR_CODE_2,
                        on_click=show_qr_click,
                        data=wallet["address_base58"],
                    ),
                    flet.IconButton(
                        icon=flet.Icons.CONTENT_COPY,
                        tooltip="Copy Address",
                        on_click=lambda e: page.clipboard.set(wallet["address_base58"]),
                    ),
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Divider(thickness=2),
            flet.Row([flet.Text("Solana Networks:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),]),
            flet.Row(
                [
                    flet.Column(
                        [
                            flet.Checkbox(label="mainnet-beta (real network)", value=True),
                            flet.Checkbox(label="testnet (not a real network)", value=False),
                            flet.Checkbox(label="devnet (not a real network)", value=False),
                        ]
                    ),
                ],
                alignment=flet.MainAxisAlignment.START,
            ),
            flet.Row(
                [
                    flet.ElevatedButton(
                        content=flet.Text("Show History"),
                        on_click=get_history_button_click,
                        data=wallet,
                    ),
                    flet.ElevatedButton(
                        content=flet.Text("Show Balance"),
                        on_click=get_balance_button_click,
                        # data=wallet['address_base58'],
                        data=wallet,
                    ),
                ],
                alignment=flet.MainAxisAlignment.END,
            ),
            el_token_balance_data,
        ]
        await page.push_route("address-page")

    async def get_history_button_click(e):
        try:
            wallet = e.control.data
            print(f'****** address >> get_history_button_click: {wallet}')
            el_token_balance_data.controls.clear()
            page.update()

            networks = []
            # Собираем выбранные сети аналогично балансу
            if e.control.parent.parent.controls[-3].controls[0].controls[0].value:
                networks.append(("mainnet-beta", "https://api.mainnet-beta.solana.com"))
            if e.control.parent.parent.controls[-3].controls[0].controls[1].value:
                networks.append(("testnet", "https://api.testnet.solana.com"))
            if e.control.parent.parent.controls[-3].controls[0].controls[2].value:
                networks.append(("devnet", "https://api.devnet.solana.com"))

            e.control.disabled = True   # блокируем кнопку
            el_token_balance_data.controls.append(
                flet.Row([flet.ProgressRing(), flet.Text("LOADING HISTORY...")], alignment=flet.MainAxisAlignment.CENTER)
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
                    flet.Row([flet.Text(f'Network: {net_name}', size=16, weight=flet.FontWeight.BOLD)])
                )

                # Запрашиваем историю
                history_data = await get_transaction_history(wallet['address_base58'], net_url)

                if "error" in history_data:
                    tmp_history_result.append(flet.Text(f"Error: {history_data['error']}", color="red"))
                elif "result" in history_data and history_data["result"]:
                    csv_history.append((net_name, history_data["result"]))
                    for tx in history_data["result"]:
                        time_str = datetime.fromtimestamp(tx['block_time']).strftime('%Y-%m-%d %H:%M:%S') if tx['block_time'] else "Unknown"

                        sol_change = tx.get('sol_change', 0)
                        if sol_change > 0:
                            change_color, change_sign = "green", "+"
                        elif sol_change < 0:
                            change_color, change_sign = "red", ""
                        else:
                            change_color = "black" if page.theme_mode == flet.ThemeMode.LIGHT else "white"
                            change_sign = ""

                        balance_spans = [
                            flet.TextSpan(f"{change_sign}{sol_change:.9f} SOL", flet.TextStyle(size=14, color=change_color, weight=flet.FontWeight.BOLD))
                        ]

                        if "spl_changes" in tx and tx["spl_changes"]:
                            for spl in tx["spl_changes"]:
                                change = spl["change"]
                                spl_color = "green" if change > 0 else "red"
                                spl_sign = "+" if change > 0 else ""

                                # Если символ найден, используем его, иначе режем mint адрес
                                display_name = spl.get("symbol") or f"{spl['mint'][:4]}...{spl['mint'][-4:]}"

                                balance_spans.append(
                                    flet.TextSpan(f"\n{spl_sign}{change} ", flet.TextStyle(size=14, color=spl_color, weight=flet.FontWeight.BOLD))
                                )
                                balance_spans.append(
                                    flet.TextSpan(f"{display_name}", flet.TextStyle(size=12, color="grey"))
                                )

                        # Изолированная функция для создания интерактивной карточки
                        def create_tx_card(tx_data, t_str, b_spans):
                            # Progressive disclosure: Simple = header only;
                            # Pro = + expandable Signature/Status/Fee;
                            # Developer = + Slot/Version/CU + logs.
                            header_lines = [
                                flet.Text(f"{t_str} • {tx_data.get('tx_type', 'Unknown')}", size=12, weight=flet.FontWeight.BOLD, color="grey700"),
                                flet.Text(spans=b_spans),
                            ]
                            # Simple mode has no expandable details, so surface
                            # the status directly under the amount.
                            if not show_detail:
                                header_lines.append(
                                    flet.Text(
                                        f"{'Success' if tx_data['success'] else 'Failed'}",
                                        size=11,
                                        color="green" if tx_data['success'] else "red",
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
                                flet.Text(f"Signature: {tx_data['signature']}", selectable=True, size=12, italic=True),
                                flet.Text(
                                    f"Status: {'Success' if tx_data['success'] else 'Failed'} | Fee: {tx_data.get('fee', 0):.9f} SOL",
                                    size=12,
                                    color="green" if tx_data['success'] else "red"
                                ),
                            ]
                            if show_tech:
                                # Формируем логи в виде прокручиваемого списка
                                logs_controls = [flet.Text("Logs:", size=12, weight=flet.FontWeight.BOLD)]
                                if tx_data.get('logs'):
                                    for log in tx_data['logs']:
                                        # Подсвечиваем ошибки красным для удобства
                                        log_color = "red" if "failed" in log.lower() or "error" in log.lower() else "grey"
                                        logs_controls.append(flet.Text(f"• {log}", size=10, color=log_color, selectable=True))
                                else:
                                    logs_controls.append(flet.Text("No logs available", size=10, color="grey"))

                                # Оборачиваем логи в Column с фиксированной высотой и скроллом
                                logs_column = flet.Container(
                                    content=flet.Column(logs_controls, spacing=2, scroll=flet.ScrollMode.AUTO),
                                    height=100,
                                    padding=5,
                                    border=flet.border.all(1, "black12"),
                                    border_radius=5
                                )
                                details_inner.append(
                                    flet.Text(f"Slot: {tx_data.get('slot')} | Version: {tx_data.get('version')} | CU Consumed: {tx_data.get('compute_units')}", size=12, color="blue")
                                )
                                details_inner.append(logs_column)

                            # Скрытая колонка с деталями
                            details_col = flet.Column(visible=False, controls=details_inner)

                            # Обработчик кнопки-стрелки
                            def toggle_details(e):
                                details_col.visible = not details_col.visible
                                e.control.icon = flet.Icons.ARROW_DROP_UP if details_col.visible else flet.Icons.ARROW_DROP_DOWN
                                e.control.update()
                                details_col.update()

                            return flet.Card(
                                content=flet.Container(
                                    padding=10,
                                    content=flet.Column([
                                        flet.Row([
                                            flet.Column(header_lines, expand=True),
                                            flet.IconButton(
                                                icon=flet.Icons.ARROW_DROP_DOWN,
                                                icon_size=30,
                                                on_click=toggle_details
                                            )
                                        ], alignment=flet.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=flet.CrossAxisAlignment.START),
                                        details_col
                                    ])
                                )
                            )

                        # Добавляем созданную интерактивную карточку в общий список
                        tmp_history_result.append(create_tx_card(tx, time_str, balance_spans))

                else:
                    tmp_history_result.append(flet.Text("No transactions found.", italic=True))

                tmp_history_result.append(flet.Divider(thickness=1))

            if csv_history and show_csv:
                csv_content = transaction_history_to_csv(csv_history)

                async def export_history_csv_click(_):
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    saved_path = await csv_file_picker.save_file(
                        dialog_title="Save transaction history CSV",
                        file_name=f"solana-history-{timestamp}.csv",
                        file_type=flet.FilePickerFileType.CUSTOM,
                        allowed_extensions=["csv"],
                        src_bytes=csv_content.encode("utf-8-sig"),
                    )
                    if saved_path:
                        page.show_dialog(
                            flet.AlertDialog(
                                title=flet.Text("CSV saved"),
                                content=flet.Text(f"Transaction history saved to:\n{saved_path}"),
                            )
                        )

                tmp_history_result.insert(
                    1,
                    flet.Row(
                        [
                            flet.ElevatedButton(
                                content=flet.Text("Save History as CSV"),
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
            print(f'Error get_history_button_click: {er}')
            page.show_dialog(
                flet.AlertDialog(title=flet.Text("Error loading history!"))
            )
            e.control.disabled = False
        finally:
            page.update()

    async def get_balance_button_click(e):
        try:
            wallet = e.control.data
            print(f'****** address >> get_balance_button_click: {wallet}')
            el_token_balance_data.controls.clear()
            page.update()
            networks = []       # ["mainnet-beta", "testnet", "devnet"]
            if e.control.parent.parent.controls[-3].controls[0].controls[0].value:
                # networks.append("mainnet-beta")
                networks.append("https://api.mainnet-beta.solana.com")
            if e.control.parent.parent.controls[-3].controls[0].controls[1].value:
                # networks.append("testnet")
                networks.append("https://api.testnet.solana.com")
            if e.control.parent.parent.controls[-3].controls[0].controls[2].value:
                # networks.append("devnet")
                networks.append("https://api.devnet.solana.com")
            print(f'networks: {networks}')
            e.control.disabled = True   # блокируем кнопку
            el_token_balance_data.controls.append(
                flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
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
                wallet['address_base58'], networks,
                include_transfer_cost=show_spl,
                include_image_bytes=show_spl,
            )
            print(f'****** get_sol_spl_balance result: {result}')

            # USD pricing (Jupiter Price API v3). Values are attached only to
            # mainnet entries — devnet/testnet holdings have no real value.
            try:
                price_info = await enrich_balance_result_with_prices(result)
                print(f'****** price_info: {price_info}')
            except Exception as price_er:
                print(f'price enrichment skipped: {price_er}')
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
                    print(f'****** spam_info: {spam_info}')
                except Exception as spam_er:
                    print(f'spam enrichment skipped: {spam_er}')
                    spam_info = {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}

            for i, r in enumerate(result):
                tmp_balance_spl = []
                tmp_spam_spl = []
                spam_token_count = 0

                # Builds the (Transfer button + logo + amount text) + expand
                # detail row pair for one token. Defined per-network so it can
                # close over `wallet` and the current `r` without late-binding.
                def _build_spl_token_controls(spl_token):
                    token_symbol = ''
                    if 'symbol_metaplex' in spl_token:
                        token_symbol += f'{spl_token['symbol_metaplex']} (symbol_metaplex) '
                    if 'symbol_2022' in spl_token:
                        token_symbol += f'{spl_token['symbol_2022']} (symbol_2022)'
                    spl_token_logo = flet.Image(
                        width=100,
                        height=100,
                        src="spl-token-placeholder.png",
                        fit=flet.BoxFit.CONTAIN,
                        border_radius=flet.border_radius.all(10),
                    )
                    if 'logo' in spl_token and spl_token['logo']:
                        spl_token_logo.src = spl_token['logo']
                    # USD value spans (mainnet-priced tokens only)
                    _spl_usd_spans = []
                    if spl_token.get('usd_value') is not None:
                        _spl_usd_spans.append(flet.TextSpan(
                            f'   {fmt_usd(spl_token["usd_value"])}',
                            flet.TextStyle(size=14, color=flet.Colors.GREY_700),
                        ))
                        if spl_token.get('change_24h') is not None:
                            _chg = spl_token['change_24h']
                            _spl_usd_spans.append(flet.TextSpan(
                                f' {fmt_change(_chg)}',
                                flet.TextStyle(
                                    size=12,
                                    color=flet.Colors.GREEN if _chg >= 0 else flet.Colors.RED,
                                ),
                            ))
                    return [
                        flet.Row(
                            [
                                flet.ElevatedButton(
                                    content=flet.Text("Transfer this token"),
                                    on_click=on_go_to_spl_token_page,
                                    data={
                                        'wallet_address': wallet['address_base58'],
                                        'network': r['network'],
                                        'spl_amount': spl_token['amount'],
                                        'symbol': token_symbol,
                                        'sol_amount': r['sol'],
                                        'raw_data': spl_token,
                                        'wallet_data': wallet,
                                    },
                                    # disabled=False if (r['sol'] and spl_token['amount']) else True,
                                    disabled=False if (r['sol'] and spl_token['amount'] and r['sol'] > spl_token['transfer_cost']["total_sol"]) else True,
                                ),
                                spl_token_logo,
                                flet.Text(
                                    value='',
                                    spans=[
                                        flet.TextSpan(f'{spl_token['amount']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                        flet.TextSpan(f' {token_symbol}', flet.TextStyle(size=16)),
                                        *_spl_usd_spans,
                                    ]
                                ),
                            ],
                        ),
                        flet.Column(
                            [
                                flet.Row(
                                    [
                                        flet.TextButton(
                                            content=flet.Row(
                                                [
                                                    flet.Icon(flet.Icons.ARROW_DROP_DOWN, size=50),
                                                ],
                                            ),
                                            on_click=on_spl_arrow_drop_down,
                                            data={**{k: v for k, v in spl_token.items() if k not in ('logo', 'spam')}, 'network': r['network']},
                                        ),
                                    ],
                                    alignment=flet.MainAxisAlignment.CENTER,
                                ),
                            ],
                        ),
                    ]

                if show_spl:
                    for spl_token in r['spl']:
                        if spl_token['amount'] <= 0:
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
                            _sv = spl_token.get('spam') or {}
                            _reasons = ', '.join(_sv.get('reasons') or []) or 'flagged as risky'
                            tmp_balance_spl.append(
                                flet.Row(
                                    [
                                        flet.Icon(flet.Icons.WARNING_AMBER_ROUNDED, color=flet.Colors.ORANGE, size=18),
                                        flet.Text(f'Suspicious: {_reasons}', size=12, color=flet.Colors.ORANGE_800, selectable=True),
                                    ],
                                )
                            )
                        tmp_balance_spl.extend(_build_spl_token_controls(spl_token))

                    # "N spam tokens hidden" expander. Hidden rows live in a column
                    # that is shown on demand; the toggle carries the column ref in
                    # its `data` so the handler needs no per-loop closure state.
                    if tmp_spam_spl:
                        _spam_col = flet.Column(controls=tmp_spam_spl, visible=False)

                        async def _toggle_spam(e):
                            col = e.control.data
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
                                                f'{spam_token_count} spam token(s) hidden — click to show',
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
                if r['network'] == "https://api.testnet.solana.com" or r['network'] == "https://api.devnet.solana.com":
                    tmp_request_airdrop.append(
                        flet.ElevatedButton(
                            content=flet.Text("Request Airdrop 1 SOL"),
                            on_click=on_request_airdrop,
                            data={
                                'wallet_address': wallet['address_base58'],
                                'network': r['network'],
                                'sol_amount': r['sol'],
                                'symbol': 'SOL',
                                'wallet_data': wallet,
                            },
                            disabled=False,
                        ),
                    )
                # USD value spans for native SOL (mainnet-priced rows only)
                _sol_usd_spans = []
                if r.get('sol_usd') is not None:
                    _sol_usd_spans.append(flet.TextSpan(
                        f'   {fmt_usd(r["sol_usd"])}',
                        flet.TextStyle(size=14, color=flet.Colors.GREY_700),
                    ))
                    if r.get('sol_change_24h') is not None:
                        _chg = r['sol_change_24h']
                        _sol_usd_spans.append(flet.TextSpan(
                            f' {fmt_change(_chg)}',
                            flet.TextStyle(
                                size=12,
                                color=flet.Colors.GREEN if _chg >= 0 else flet.Colors.RED,
                            ),
                        ))
                tmp_balance_result.extend(
                    [
                        flet.Row(
                            [
                                flet.Text(
                                    value='',
                                    spans=[flet.TextSpan(f'Network: {r['network']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD))]
                                ),
                            ],
                        ),
                        flet.Row(
                            [
                                flet.ElevatedButton(
                                    content=flet.Text("Transfer this token"),
                                    on_click=on_go_to_token_page,
                                    data={
                                        # 'wallet_address': e.control.data,
                                        'wallet_address': wallet['address_base58'],
                                        'network': r['network'],
                                        'sol_amount': r['sol'],
                                        'symbol': 'SOL',
                                        'wallet_data': wallet,
                                    },
                                    disabled=False if r['sol'] else True,
                                ),
                                flet.ElevatedButton(
                                    content=flet.Text("Swap"),
                                    on_click=on_go_to_swap_page,
                                    data={
                                        'wallet_address': wallet['address_base58'],
                                        'network': r['network'],
                                        'sol_amount': r['sol'],
                                        'wallet_data': wallet,
                                    },
                                    disabled=(r['network'] != MAINNET_RPC) or (not r['sol']),
                                ),
                                flet.Text(
                                    value='',
                                    spans=[
                                        flet.TextSpan(f'{r['sol']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                        flet.TextSpan(' SOL', flet.TextStyle(size=16)),
                                        *_sol_usd_spans,
                                    ]
                                ),
                                *tmp_request_airdrop,
                            ],
                        ),
                        *tmp_balance_spl,
                    ]
                )
                if i < len(result) - 1: # добавляем разделяющую линию после каждого результата кроме последнего
                    tmp_balance_result.append(flet.Divider(thickness=1))
            el_token_balance_data.controls.clear()
            _balance_controls = [flet.Divider(thickness=3)]
            # Portfolio value banner (mainnet holdings only). In Simple mode
            # SPL rows are hidden, so the banner must reflect native SOL only
            # (otherwise it advertises value the user cannot see). sol_usd is
            # only attached to mainnet entries by enrich_balance_result_with_prices.
            _banner_total = price_info.get('total_usd', 0.0)
            _note = ''
            if not show_spl:
                _banner_total = sum(nr.get('sol_usd') or 0.0 for nr in result)
            else:
                _priced = price_info.get('priced', 0)
                _tokens = price_info.get('tokens', 0)
                _note = '' if _priced or not _tokens else ' (no priced tokens)'
            if price_info.get('mainnet') and _banner_total:
                _balance_controls.append(
                    flet.Container(
                        content=flet.Row(
                            [
                                flet.Text(
                                    value='',
                                    spans=[
                                        flet.TextSpan('Portfolio value  ', flet.TextStyle(size=14, color=flet.Colors.GREY_700)),
                                        flet.TextSpan(fmt_usd(_banner_total), flet.TextStyle(size=22, weight=flet.FontWeight.BOLD)),
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
            if spam_info.get('flagged'):
                _spam_txt = []
                if spam_info.get('spam'):
                    _spam_txt.append(f"{spam_info['spam']} spam hidden")
                if spam_info.get('suspicious'):
                    _spam_txt.append(f"{spam_info['suspicious']} suspicious")
                _balance_controls.append(
                    flet.Container(
                        content=flet.Row(
                            [
                                flet.Icon(flet.Icons.SHIELD_OUTLINED, color=flet.Colors.RED_700, size=18),
                                flet.Text(
                                    f"Spam filter: {' / '.join(_spam_txt)}",
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
            print(f'time: {datetime.now() - start} sec')
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text(f"Balance for {wallet['address_base58']} received successfully!"),
                )
            )
        except Exception as er:
            print(f'Error get_balance_button_click: {er}')
            el_token_balance_data.controls.clear()
            el_token_balance_data.controls.append(
                flet.Text(f'Error: {er}', color=flet.Colors.RED, size=14)
            )
            try:
                e.control.disabled = False
            except Exception:
                pass
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text("Error get_balance_button_click!"),
                )
            )
        finally:
            try:
                e.control.disabled = False
            except Exception:
                pass
            page.update()

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

    async def theme_changed(e):
        page.theme_mode = flet.ThemeMode.DARK if page.theme_mode == flet.ThemeMode.LIGHT else flet.ThemeMode.LIGHT
        theme_control.label = "Light theme" if page.theme_mode == flet.ThemeMode.LIGHT else "Dark theme"
        if page.theme_mode == flet.ThemeMode.LIGHT:
            await page.shared_preferences.set("theme_mode", "LIGHT")
        else:
            await page.shared_preferences.set("theme_mode", "DARK")
        page.update()

    theme_control = flet.Switch(
        label="Light theme" if page.theme_mode == flet.ThemeMode.LIGHT else "Dark theme",
        on_change=theme_changed,
    )

    # ---- Experience level (Simple / Pro / Developer) ----
    experience_dd = flet.Dropdown(
        label="Experience level",
        options=[flet.dropdown.Option(key=m, text=experience_label(m)) for m in MODES],
        value=SIMPLE,
        dense=True,
        on_select=lambda e: asyncio.create_task(experience_changed(e)),
    )
    experience_desc = flet.Text(
        experience_description(SIMPLE), size=11, color=flet.Colors.GREY_700,
    )

    async def settings_enter() -> None:
        """Read the persisted experience level into the Settings selector."""
        mode = await get_experience(page)
        experience_dd.value = mode
        experience_desc.value = experience_description(mode)

    async def _apply_experience(mode: str) -> None:
        mode = await set_experience(page, mode)
        experience_dd.value = mode
        experience_desc.value = experience_description(mode)
        page.update()

    async def experience_changed(e):
        new_mode = experience_dd.value
        prev = await get_experience(page)
        # Gate the first switch INTO Developer with a destructive-tool warning.
        if (
            new_mode == DEVELOPER
            and prev != DEVELOPER
            and not await has_seen_dev_warning(page)
        ):
            _show_dev_warning(new_mode, prev)
            return
        await _apply_experience(new_mode)

    def _show_dev_warning(new_mode, prev_mode):
        dlg = flet.AlertDialog(
            modal=True,
            title=flet.Text("Enable Developer mode?"),
            content=flet.Column(
                [
                    flet.Text(
                        "Developer mode unlocks raw, potentially destructive tools: "
                        "the storage inspector, raw-key export, simulation details and more.",
                        size=12,
                    ),
                    flet.Text(
                        "These can expose private keys or wipe local data if misused. "
                        "Only enable this if you know what you are doing.",
                        size=12,
                        color=flet.Colors.GREY_700,
                    ),
                ],
                spacing=6,
                tight=True,
            ),
            actions=[
                flet.TextButton("Cancel", on_click=lambda ev: _cancel_dev_warning(dlg, prev_mode)),
                flet.TextButton(
                    "Enable Developer",
                    style=flet.ButtonStyle(color=flet.Colors.RED),
                    on_click=lambda ev: asyncio.create_task(_confirm_dev_warning(dlg, new_mode)),
                ),
            ],
        )
        page.show_dialog(dlg)

    def _cancel_dev_warning(dlg, prev_mode):
        ctx.close_dialog(dlg)
        # Revert the dropdown to the previously persisted mode.
        experience_dd.value = prev_mode
        page.update()

    async def _confirm_dev_warning(dlg, mode):
        ctx.close_dialog(dlg)
        await mark_dev_warning_seen(page)
        await _apply_experience(mode)

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

    async def clear_client_storage():
        keys = await page.shared_preferences.get_keys('')
        for key in keys:
            await page.shared_preferences.remove(key)

    # ---- Navigation handlers used by the "More" hub ----
    async def nav_addressbook(e): await page.push_route("addressbook-page")
    async def nav_dev_storage(e): await page.push_route("dev-storage-page")

    async def clear_storage_click(e):
        """Wipe ALL local storage (wallets, PIN, contacts, WC pairing). Destructive."""
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
                    on_click=lambda ev: asyncio.create_task(_do_clear_storage(dlg)),
                ),
            ],
        )
        page.show_dialog(dlg)

    async def _do_clear_storage(dlg):
        ctx.close_dialog(dlg)
        await clear_client_storage()
        page.show_dialog(flet.AlertDialog(title=flet.Text("All local storage cleared.")))
        await page.push_route("/")

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
    # per-session live client lives in ctx.session["_wc_state"]. nav_wc stays
    # here (it is a "More" hub navigation handler used by more_enter).

    # ---- "More" hub: navigation handlers + item builder ----
    async def nav_wc(e): await page.push_route("wc-page")
    async def nav_nft(e): await page.push_route("nft-page")
    async def nav_stake(e): await page.push_route("stake-page")
    async def nav_more(e): await page.push_route("more-page")
    async def nav_settings(e): await page.push_route("settings-page")
    async def nav_sim(e): await page.push_route("sim-page")
    async def nav_rpc(e): await page.push_route("rpc-page")
    async def nav_rawkey(e): await page.push_route("raw-key-page")

    def _hub_item(icon, title: str, subtitle: str, on_click, badge: str = "") -> flet.Card:
        """One tappable entry in the 'More' hub: icon + title + description + chevron."""
        trailing = []
        if badge:
            trailing.append(flet.Container(
                content=flet.Text(badge, size=10, color=flet.Colors.WHITE, weight=flet.FontWeight.BOLD),
                bgcolor=flet.Colors.GREY_500, border_radius=6, padding=4,
            ))
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

    async def route_change(route):
        reset_activity()
        page.views.clear()
        homepage.controls[-1] = await get_wallets_cards()
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
            await more_enter()
            page.views.append(more_page)
        elif page.route == "settings-page":
            await settings_enter()
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
        reset_activity()
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
            await get_wallets_cards(),
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

    address_page = flet.View(
        route="address-page",
        appbar=flet.AppBar(
            title=flet.Text("Address Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Information:', size=30, font_family="Georgia"),
            el_address_page,
        ]
    )

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

    more_page = flet.View(
        route="more-page",
        appbar=flet.AppBar(
            title=flet.Text("More"),
            color="white",
            bgcolor="#1da1f2",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[],  # populated by more_enter() based on the active experience mode
    )

    async def more_enter() -> None:
        """Rebuild the More hub controls for the persisted experience mode.

        Sections whose items are all gated out are omitted entirely (header +
        divider included), so Simple mode shows only the Tools section.
        """
        mode = await get_experience(page)
        controls: list = []

        # WEB3 & DeFi — Pro+ only; section is skipped entirely in Simple mode.
        web3_items = []
        if feature("walletconnect", mode):
            web3_items.append(_hub_item(flet.Icons.LINK, "Connect dApp",
                                        "Pair with a dApp via WalletConnect v2 and sign requests.", nav_wc))
        if feature("nft", mode):
            web3_items.append(_hub_item(flet.Icons.COLLECTIONS, "NFT Gallery",
                                        "Browse and send your non-fungible tokens.", nav_nft))
        if feature("staking", mode):
            web3_items.append(_hub_item(flet.Icons.SAVINGS, "Liquid Staking",
                                        "Stake SOL into JitoSOL / mSOL / bSOL / jupSOL.", nav_stake))
        if web3_items:
            controls.append(flet.Text("WEB3 & DeFi", size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_600))
            controls.extend(web3_items)
            controls.append(flet.Divider())

        # Tools — always visible in every mode.
        controls.append(flet.Text("Tools", size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_600))
        controls.append(_hub_item(flet.Icons.CONTACTS, "Address Book",
                                  "Saved recipients with address-poisoning protection.", nav_addressbook))
        controls.append(_hub_item(flet.Icons.SETTINGS, "Settings",
                                  "Theme, security and app preferences.", nav_settings))

        # Developer — Developer mode only. Each tool is gated by its own feature
        # key so the section assembles from whatever the matrix exposes.
        dev_items = []
        if feature("devtools", mode):
            dev_items.append(_hub_item(flet.Icons.STORAGE, "Storage inspector",
                                       "View and edit raw shared_preferences keys.", nav_dev_storage, badge="dev"))
        if feature("sim_detail", mode):
            dev_items.append(_hub_item(flet.Icons.BIOTECH, "Simulation inspector",
                                       "Run the anti-phishing simulation on a pasted transaction.", nav_sim, badge="dev"))
        if feature("custom_rpc", mode):
            dev_items.append(_hub_item(flet.Icons.DVR, "Raw RPC inspector",
                                       "Run read-only JSON-RPC calls against any endpoint.", nav_rpc, badge="dev"))
        if feature("raw_export", mode):
            dev_items.append(_hub_item(flet.Icons.VPN_KEY, "Export raw keys",
                                       "Reveal & copy a wallet's private key / mnemonic. DANGEROUS.",
                                       nav_rawkey, badge="danger"))
        if feature("devtools", mode):
            dev_items.append(_hub_item(flet.Icons.DELETE_SWEEP_OUTLINED, "Clear all storage",
                                       "Wipe every wallet, PIN and pairing. Irreversible.", clear_storage_click, badge="danger"))
        if dev_items:
            controls.append(flet.Divider())
            controls.append(flet.Text("Developer", size=13, weight=flet.FontWeight.BOLD, color=flet.Colors.GREY_600))
            controls.extend(dev_items)

        more_page.controls = [
            flet.Column(
                controls,
                spacing=6,
                width=460,
            ),
        ]

    settings_page = flet.View(
        route="settings-page",
        appbar=flet.AppBar(
            title=flet.Text("Settings"),
            color="white",
            bgcolor="#1da1f2",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Column(
                [
                    flet.Text("Appearance", size=18, weight=flet.FontWeight.BOLD),
                    flet.Card(
                        content=flet.Container(
                            padding=12,
                            width=440,
                            content=flet.Row(
                                [flet.Icon(flet.Icons.PALETTE_OUTLINED), theme_control],
                                alignment=flet.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                        )
                    ),
                    flet.Divider(),
                    flet.Text("About", size=18, weight=flet.FontWeight.BOLD),
                    flet.Card(
                        content=flet.Container(
                            padding=12,
                            width=440,
                            content=flet.Column(
                                [
                                    flet.Text("Solana Wallet", size=15, weight=flet.FontWeight.BOLD),
                                    flet.Text("Hand-rolled Solana wallet (Python + Flet).",
                                              size=11, color=flet.Colors.GREY_700),
                                    flet.Text("All blockchain logic is implemented from scratch "
                                              "(no solana-py / solders).",
                                              size=11, color=flet.Colors.GREY_700),
                                ],
                                spacing=2,
                            ),
                        )
                    ),
                    flet.Container(height=8),
                    flet.Text("Experience level", size=18, weight=flet.FontWeight.BOLD),
                    flet.Card(
                        content=flet.Container(
                            padding=12,
                            width=440,
                            content=flet.Column(
                                [
                                    flet.Row(
                                        [flet.Icon(flet.Icons.TUNE_OUTLINED), experience_dd],
                                        alignment=flet.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    experience_desc,
                                ],
                                spacing=6,
                                tight=True,
                            ),
                        )
                    ),
                ],
                spacing=6,
                width=460,
            ),
        ],
    )

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    await route_change(None) # Manually trigger the initial UI load since push_route is ignored on identical paths
    await page.push_route(page.route)
    page.update()

    # Start the inactivity auto-lock watcher and present the PIN gate.
    asyncio.create_task(auto_lock_watcher())
    await refresh_lock_state()


flet.run(main)
