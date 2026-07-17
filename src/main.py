from datetime import datetime
import asyncio
import time
import random
import os
import flet
import base64
import json
import io
import qrcode
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from solana.create_wallet import create_solana_wallet
from solana.balance import get_sol_spl_balance, get_sol_balance
from solana.nft import get_nfts
from solana.transfer_sol import transfer_sol_token, get_min_sol_balance
# from solana.transfer_spl import transfer_spl_token
from solana.spl_token import request_airdrop, transfer_spl_token, burn_token, close_token_account, burn_and_close_token_account
from solana.swap import get_quote as jup_get_quote, swap as jup_swap
from solana.liquid_staking import (
    LST_TOKENS,
    MAX_SLIPPAGE_BPS,
    get_stake_quote as lst_get_quote,
    stake_sol as lst_stake,
    unstake_sol as lst_unstake,
    get_lst_positions as lst_positions,
)
from solana.prices import enrich_balance_result_with_prices, fmt_usd, fmt_change
from solana.spam_filter import (
    enrich_balance_result_with_spam_filter,
    is_hidden_spam,
    is_suspicious,
)
from solana.sns import SNSResolutionError, resolve_sns_name
from solana.validators import is_valid_amount, is_valid_wallet_address, is_valid_private_key, is_valid_wallet_seed_phrase
from solana.transaction_history import get_transaction_history
from solana.history_csv import transaction_history_to_csv
from solana.simulation import analyze_transaction
import httpx
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

# Mainnet token registry for the swap screen (symbol -> (mint, decimals)).
# Jupiter's hosted API serves mainnet-beta only, so swaps are mainnet-only.
SWAP_TOKENS = {
    "SOL": ("So11111111111111111111111111111111111111112", 9),
    "USDC": ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
    "USDT": ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
    "JUP": ("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
}
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

    def encrypt_for_storage(value: dict) -> dict:
        """Encrypt a wallet record's secrets before persistence.

        When the session is unlocked (a PIN is active), secrets are encrypted
        with the in-memory key.  Otherwise the record is stored in plaintext
        and migrated later once a PIN is established.
        """
        if is_unlocked():
            return encrypt_wallet_secrets(value, session["key"])
        return value

    def get_wallet_private_key(wallet: dict) -> str:
        """Plaintext private key hex for a wallet ('' if watch-only / locked)."""
        if not is_unlocked():
            return ""
        return get_secret(wallet, "private_key_hex", session["key"])

    def has_wallet_private_key(wallet: dict) -> bool:
        return bool(get_wallet_private_key(wallet))

    def resolve_signing_key(data: dict, secret_control=None) -> tuple[str, str]:
        """Resolve a private key hex for a token/burn action.

        Returns (private_key_hex, error_message). Tries the stored (decrypted)
        key first, then falls back to the secret TextField (12/24 words or raw
        hex private key) entered on the page. Mirrors the transfer handlers.
        """
        pk = get_wallet_private_key(data.get('wallet_data') or {})
        if pk:
            return pk, ''
        input_secret = ''
        if secret_control is not None:
            try:
                input_secret = (secret_control.value or '').strip()
            except Exception:
                input_secret = ''
        if not input_secret:
            return '', "Private key is required (unlock the wallet or enter the secret)."
        wallet_data = data['wallet_data']
        if is_valid_wallet_seed_phrase(input_secret):
            for attempt in range(10):
                words, wallet_address_base58, secret_key_base58, new_private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
                if wallet_address_base58 == wallet_data['address_base58']:
                    return new_private_key_hex, ''
                if error:
                    return '', f"Error getting private key: {error}"
            return '', "Failed to get private key from seed phrase."
        if is_valid_private_key(input_secret) and len(input_secret) == 64:
            return input_secret, ''
        return '', "Invalid secret."

    # make_priority_fee_block / _pf_from_data -> moved to ui/components/priority_fee.py (Phase 7).

    def decrypt_for_display(wallet: dict) -> dict:
        """Wallet dict with secrets decrypted (for the Wallet Info dialog)."""
        if not is_unlocked():
            return wallet
        return decrypt_wallet_secrets(wallet, session["key"])

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

    input_wallet_name = flet.TextField(label="Wallet Name", min_lines=1, max_lines=1, max_length=50)
    input_wallet_description = flet.TextField(label="Wallet description", min_lines=2, max_lines=5, max_length=200)

    txt_wallet_name = flet.TextField()
    txt_wallet_description = flet.TextField()
    # txt_wallet_name = flet.Text()
    # txt_wallet_description = flet.Text()
    txt_wallet_address = flet.Text(selectable=True)
    txt_private_key = flet.Text(selectable=True)
    txt_secret_key_base58 = flet.Text(selectable=True)
    txt_public_key = flet.Text(selectable=True)
    txt_words = flet.Text(selectable=True)
    # txt_seed = flet.Text()
    txt_error = flet.Text(selectable=True)
    txt_wallet_created = flet.Text(selectable=True)

    input_recover_wallet_name = flet.TextField(label="Wallet Name", min_lines=1, max_lines=1, max_length=50)
    input_recover_wallet_description = flet.TextField(label="Wallet description", min_lines=2, max_lines=5, max_length=200)
    input_recover_wallet_secret = flet.TextField(label="Wallet Secret Words (12/24) or Secret Key base58 (length=88)", min_lines=2, max_lines=5, max_length=200)

    txt_recover_wallet_name = flet.TextField()
    txt_recover_wallet_description = flet.TextField()
    txt_recover_wallet_address = flet.Text(selectable=True)
    txt_recover_private_key = flet.Text(selectable=True)
    txt_recover_secret_key_base58 = flet.Text(selectable=True)
    txt_recover_public_key = flet.Text(selectable=True)
    txt_recover_words = flet.Text(selectable=True)
    txt_recover_error = flet.Text(selectable=True)
    txt_recover_wallet_created = flet.Text(selectable=True)
    txt_recover_wallet_secret = flet.Text(selectable=True)

    input_add_address_wallet_name = flet.TextField(label="Wallet Name", min_lines=1, max_lines=1, max_length=50)
    input_add_address_wallet_description = flet.TextField(label="Wallet description", min_lines=2, max_lines=5, max_length=200)
    input_add_wallet_address = flet.TextField(label="Add Wallet Address (base58) ", min_lines=2, max_lines=5, max_length=200)

    txt_add_address_wallet_name = flet.TextField()
    txt_add_address_wallet_description = flet.TextField()
    txt_add_address_wallet_address = flet.Text(selectable=True)
    # txt_recover_public_key = flet.Text(selectable=True)
    txt_add_address_error = flet.Text(selectable=True)
    txt_add_address_wallet_created = flet.Text(selectable=True)

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
    # `ctx.controls["el_address_book"]`. `resolve_recipient_input` stays here for
    # now (it is a transfer-screen SNS helper; it migrates with the transfer group).

    async def resolve_recipient_input(recipient_raw: str, network: str) -> tuple[str, str | None]:
        """Resolve a .sol recipient name to its wallet address when necessary."""
        entered = (recipient_raw or "").strip()
        if not entered.lower().endswith(".sol"):
            return entered, None
        try:
            address = await resolve_sns_name(entered, network)
        except SNSResolutionError as err:
            raise ValueError(str(err)) from err
        return address, f"{entered} resolved to {address}"

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
    el_rawkey_page = flet.Column()

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
                                    on_click=go_to_spl_token_page_button_click,
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
                                            on_click=spl_token_arrow_drop_down_button_click,
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
                            on_click=request_airdrop_sol_button_click,
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
                                    on_click=go_to_token_page_button_click,
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
                                    on_click=go_to_swap_page_button_click,
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
    el_swap_page = flet.Column()

    async def go_to_swap_page_button_click(e):
        data = e.control.data
        if data['network'] != MAINNET_RPC:
            page.show_dialog(
                flet.AlertDialog(title=flet.Text("Swaps are only supported on mainnet-beta."))
            )
            return
        if not has_wallet_private_key(data['wallet_data']):
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
                    private_key_hex=get_wallet_private_key(data['wallet_data']),
                    slippage_bps=await_holder["slippage_bps"],
                    network=MAINNET_RPC,
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

    async def spl_token_arrow_drop_down_button_click(e):
        try:
            data = e.control.data
            print(f'****** spl_token_arrow_drop_down_button_click >> data: {data}')
            print(f"*** data len: {len(data)}")
            print(f"*** e.control.parent.parent: {e.control.parent.parent}")
            e.control.parent.parent.controls.clear()
            e.control.parent.parent.controls.append(
                flet.Row(
                    [
                        flet.TextButton(
                            content=flet.Row(
                                [
                                    flet.Icon(flet.Icons.ARROW_DROP_UP, size=50),
                                ],
                            ),
                            on_click=spl_token_arrow_drop_up_button_click,
                            data=data,
                        ),
                    ],
                    alignment=flet.MainAxisAlignment.CENTER,
                ),
            )
            detail_controls = await _build_spl_token_detail(data)
            e.control.parent.parent.controls.extend(detail_controls)
        except Exception as er:
            print(f'Error spl_token_arrow_drop_down_button_click: {er}')
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text("Error spl_token_arrow_drop_down_button_click!"),
                )
            )
        finally:
            page.update()

    async def _build_spl_token_detail(data: dict) -> list:
        """Token detail rows for the arrow-drop-down expander.

        Progressive disclosure (Phase 5):
        - Pro       -> friendly summary (symbol, amount, USD, mint short, program).
        - Developer -> full raw key/value dump + a Solscan explorer link.
        """
        mode = await get_experience(page)
        rows: list = []
        if feature("balance_raw", mode):
            spl_token_data_text = ''
            for k, v in data.items():
                spl_token_data_text = spl_token_data_text + f'{k}: {v}\n'
            rows.append(flet.Text(value=spl_token_data_text, selectable=True))
            mint = str(data.get('mint') or '')
            if mint:
                net = str(data.get('network') or '')
                cluster = ''
                if 'devnet' in net:
                    cluster = '?cluster=devnet'
                elif 'testnet' in net:
                    cluster = '?cluster=testnet'
                url = f"https://solscan.io/token/{mint}{cluster}"
                rows.append(
                    flet.Row(
                        [
                            flet.ElevatedButton(
                                content=flet.Text("Inspect on Solscan"),
                                icon=flet.Icons.OPEN_IN_NEW,
                                on_click=lambda _e, u=url: page.launch_url(u),
                            ),
                        ],
                    )
                )
            return rows
        # Pro: friendly summary only.
        symbol = ''
        if data.get('symbol_metaplex'):
            symbol += f"{data['symbol_metaplex']} "
        if data.get('symbol_2022'):
            symbol += f"{data['symbol_2022']}"
        symbol = (symbol or data.get('name_2022') or '').strip() or '(unknown)'
        mint = str(data.get('mint') or '')
        mint_short = f"{mint[:6]}…{mint[-4:]}" if len(mint) >= 12 else mint
        program = str(data.get('program_id') or '')
        program_tag = 'Token-2022' if program.startswith('Tokenz') else ('Token' if program.startswith('Token') else program[:12])
        usd_line = ''
        if data.get('usd_value') is not None:
            usd_line = f"USD value: {fmt_usd(data['usd_value'])}"
            if data.get('usd_price') is not None:
                usd_line += f"  (price {fmt_usd(data['usd_price'])})"
        summary = (
            f"Token: {symbol}\n"
            f"Amount: {data.get('amount')}\n"
            f"Decimals: {data.get('decimals')}\n"
            f"Mint: {mint_short}\n"
            f"Program: {program_tag}\n"
        )
        if usd_line:
            summary += f"{usd_line}\n"
        rows.append(flet.Text(value=summary, selectable=True))
        return rows

    async def spl_token_arrow_drop_up_button_click(e):
        try:
            data = e.control.data
            e.control.parent.parent.controls.clear()
            e.control.parent.parent.controls.append(
                flet.Row(
                    [
                        flet.TextButton(
                            content=flet.Row(
                                [
                                    flet.Icon(flet.Icons.ARROW_DROP_DOWN, size=50),
                                ],
                            ),
                            on_click=spl_token_arrow_drop_down_button_click,
                            data=data,
                        ),
                    ],
                    alignment=flet.MainAxisAlignment.CENTER,
                ),
            )
        except Exception as er:
            print(f'Error spl_token_arrow_drop_up_button_click: {er}')
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text("Error spl_token_arrow_drop_up_button_click!"),
                )
            )
        finally:
            page.update()

    # def spl_token_data_copy_clicked(e):
    #     alert_text = "The data to copy does not exist."
    #     data = e.control.data
    #     if data:
    #         # Основной метод для копирования в буфер
    #         page.set_clipboard(data)

    #         # # Показываем уведомление (SnackBar), что текст скопирован
    #         # page.snack_bar = flet.SnackBar(
    #         #     flet.Text(f"Скопировано: {e.control.data}"),
    #         #     action="OK"
    #         # )
    #         # page.snack_bar.open = True

    #         alert_text = f"Copied: {data}"
    #     print(alert_text)

    async def go_to_spl_token_page_button_click(e):
        print(f'****** go_to_spl_token_page_button_click >> e.control.data: {e.control.data}')
        await _open_spl_token_page(e.control.data)

    async def _open_spl_token_page(data):
        el_spl_token_page.controls.clear()
        amount_tf = flet.TextField(
            label="Input the amount",
            value=("1" if data.get('nft_prefill_amount') is not None else None),
            min_lines=1, max_lines=1, max_length=20,
        )
        recipient_tf = flet.TextField(label="Recipient address or name.sol", min_lines=1, max_lines=1, max_length=100, expand=True)
        sns_status = flet.Text(size=11, selectable=True, color=flet.Colors.BLUE_700)
        secret_tf = flet.TextField(label="Enter Secret (12/24 Words or Private Key)", min_lines=1, max_lines=1, max_length=100)
        burn_status = flet.Column()
        pf_block, pf_state = await make_priority_fee_block(ctx, data['network'], data['raw_data']['mint'], cu_limit=80000)

        poisoning_banner = make_poisoning_banner()

        async def _on_recipient_change(ev):
            await update_poisoning_banner(ctx, poisoning_banner, recipient_tf.value or "")

        recipient_tf.on_change = _on_recipient_change

        async def _pick_contact(addr, name):
            recipient_tf.value = addr
            try:
                recipient_tf.update()
            except Exception:
                pass
            await update_poisoning_banner(ctx, poisoning_banner, addr)

        async def _open_picker(ev):
            await open_contact_picker(ctx, _pick_contact)

        async def _save_contact(ev):
            await open_save_contact_dialog(ctx, (recipient_tf.value or "").strip())
        burn_data = {
            **data,
            'amount_tf': amount_tf,
            'secret_tf': secret_tf,
            'status': burn_status,
            'pf_state': pf_state,
            'cu_limit': 80000,
        }
        close_data = {
            **data,
            'secret_tf': secret_tf,
            'status': burn_status,
            'pf_state': pf_state,
            'cu_limit': 80000,
        }
        transfer_data = {**data, 'pf_state': pf_state, 'cu_limit': 80000,
                         'recipient_tf': recipient_tf, 'poisoning_banner': poisoning_banner,
                         'sns_status': sns_status}
        burn_section = flet.Column(
            [
                flet.Row(
                    [
                        flet.ElevatedButton(
                            content=flet.Text("Burn"),
                            on_click=burn_spl_button_click,
                            data=burn_data,
                            disabled=False if (data['spl_amount'] and data['spl_amount'] > 0) else True,
                        ),
                        flet.ElevatedButton(
                            content=flet.Text("Burn All & Close Account"),
                            on_click=burn_and_close_button_click,
                            data=close_data,
                        ),
                    ],
                ),
                flet.Text(
                    value="Burn destroys tokens. Close Account also refunds the rent SOL (~0.002) to your wallet.",
                    size=11,
                    color=flet.Colors.GREY_600,
                ),
                burn_status,
            ]
        )
        el_spl_token_page.controls.extend(
            [
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Network: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data["network"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('From Address: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data["wallet_address"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Token: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data["symbol"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Amount: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data["spl_amount"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row([amount_tf]),
                flet.Row(
                    [
                        recipient_tf,
                        flet.IconButton(
                            icon=flet.Icons.CONTACTS_OUTLINED,
                            tooltip="Pick from address book",
                            on_click=_open_picker,
                        ),
                        flet.IconButton(
                            icon=flet.Icons.PERSON_ADD_ALT_OUTLINED,
                            tooltip="Save recipient as contact",
                            on_click=_save_contact,
                        ),
                    ],
                ),
                flet.Row([poisoning_banner]),
                flet.Row([sns_status]),
                burn_section,
                pf_block,
                flet.Row(
                    [
                        flet.ElevatedButton(
                            content=flet.Text("Transfer Token"),
                            on_click=transfer_spl_button_click,
                            data=transfer_data,
                        ),
                    ],
                ),
                flet.Column(),
            ]
        )
        if not has_wallet_private_key(data['wallet_data']):
             el_spl_token_page.controls.insert(
                6,
                flet.Row([secret_tf])
            )
        await page.push_route("spl-token-page")


    async def transfer_spl_button_click(e):
        data = e.control.data
        recipient_input = (data.get('recipient_tf').value or "").strip() if data.get('recipient_tf') else ""
        try:
            recipient_address, resolution_message = await resolve_recipient_input(recipient_input, data['network'])
        except ValueError as err:
            page.show_dialog(flet.AlertDialog(title=flet.Text(str(err))))
            return
        if resolution_message:
            data['sns_status'].value = resolution_message
            page.update()
        # Address-poisoning gate (before disabling the button).
        _rtf = data.get('recipient_tf')
        if _rtf is not None and not await _maybe_block_for_poisoning(
                ctx, recipient_address, lambda: transfer_spl_button_click(e)):
            return
        e.control.disabled = True
        e.control.parent.parent.controls[-1].controls.clear()
        e.control.parent.parent.controls[-1].controls.append(
            flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
        )
        page.update()

        alert_dialog_text = ''
        private_key_hex = get_wallet_private_key(data['wallet_data'])

        if not private_key_hex:
            input_secret = e.control.parent.parent.controls[6].controls[0].value.strip()
            if is_valid_wallet_seed_phrase(input_secret):
                for attempt in range(10):
                    words, wallet_address_base58, secret_key_base58, new_private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
                    if wallet_address_base58 == data['wallet_data']['address_base58']:
                        private_key_hex = new_private_key_hex
                        break
                    elif error:
                        alert_dialog_text = f"Error getting private key: {error}"
                else:
                    alert_dialog_text = "Failed to get private key from seed phrase."
            elif is_valid_private_key(input_secret):
                 if len(input_secret) == 64:
                    private_key_hex = input_secret
            else:
                alert_dialog_text = "Invalid secret."

        if private_key_hex:
            if is_valid_wallet_address(recipient_address):
                transfer_amount_str = e.control.parent.parent.controls[4].controls[0].value
                if is_valid_amount(transfer_amount_str):
                    transfer_amount = float(transfer_amount_str)
                    if 0 < transfer_amount <= data['spl_amount']:
                        print("------ DEBUG: transfer_spl_token call ------")
                        print(f"sender_address: {data['wallet_address']}")
                        print(f"recipient_address: {recipient_address}")
                        print(f"mint_address: {data['raw_data']['mint']}")
                        print(f"amount: {transfer_amount}")
                        print(f"decimals: {data['raw_data']['decimals']}")
                        print(f"network: {data['network']}")
                        print("------------------------------------------")
                        result = await transfer_spl_token(
                            sender_address=data['wallet_address'],
                            sender_private_key=private_key_hex,
                            recipient_address=recipient_address,
                            mint_address=data['raw_data']['mint'],
                            amount=transfer_amount,
                            decimals=data['raw_data']['decimals'],
                            network=data['network'],
                            program_id=data['raw_data']['program_id'],
                            priority_fee=_pf_from_data(data),
                            cu_limit=data.get('cu_limit', 80000),
                        )
                        if 'result' in result:
                            alert_dialog_text = f"Transfer of {transfer_amount} {data['symbol']} was successful!"
                        elif 'error' in result:
                            alert_dialog_text = f"Transfer Error: {result['error']}"
                        else:
                            alert_dialog_text = "Transfer failed for an unknown reason."
                    else:
                        alert_dialog_text = "Invalid transfer amount."
                else:
                    alert_dialog_text = "Invalid amount format."
            else:
                alert_dialog_text = "Invalid recipient address."

        if not alert_dialog_text:
             alert_dialog_text = "Could not proceed with transfer. Private key is missing or invalid."

        page.show_dialog(flet.AlertDialog(title=flet.Text(alert_dialog_text)))
        e.control.parent.parent.controls[-1].controls.clear()
        e.control.disabled = False
        page.update()


    async def burn_spl_button_click(e):
        data = e.control.data
        status = data.get('status')
        e.control.disabled = True
        if status is not None:
            status.controls.clear()
            status.controls.append(
                flet.Row([flet.ProgressRing(), flet.Text("BURNING...")], alignment=flet.MainAxisAlignment.CENTER)
            )
        page.update()

        alert_dialog_text = ''
        private_key_hex, key_err = resolve_signing_key(data, data.get('secret_tf'))

        if private_key_hex:
            amount_tf = data.get('amount_tf')
            amount_str = ''
            if amount_tf is not None:
                try:
                    amount_str = (amount_tf.value or '').strip()
                except Exception:
                    amount_str = ''
            if is_valid_amount(amount_str):
                amount = float(amount_str)
                if 0 < amount <= data['spl_amount']:
                    result = await burn_token(
                        owner_address=data['wallet_address'],
                        owner_private_key=private_key_hex,
                        mint_address=data['raw_data']['mint'],
                        amount=amount,
                        decimals=data['raw_data']['decimals'],
                        network=data['network'],
                        program_id=data['raw_data'].get('program_id'),
                        priority_fee=_pf_from_data(data),
                        cu_limit=data.get('cu_limit', 80000),
                    )
                    if 'result' in result:
                        alert_dialog_text = f"Burn of {amount} {data['symbol']} was successful!"
                    elif 'error' in result:
                        alert_dialog_text = f"Burn Error: {result['error']}"
                    else:
                        alert_dialog_text = "Burn failed for an unknown reason."
                else:
                    alert_dialog_text = "Invalid burn amount."
            else:
                alert_dialog_text = "Invalid amount format."
        else:
            alert_dialog_text = key_err or "Could not proceed. Private key is missing or invalid."

        page.show_dialog(flet.AlertDialog(title=flet.Text(alert_dialog_text)))
        e.control.disabled = False
        if status is not None:
            status.controls.clear()
        page.update()


    async def burn_and_close_button_click(e):
        data = e.control.data

        async def _execute(ev):
            dlg.open = False
            page.update()
            status = data.get('status')
            ev.control.disabled = True
            if status is not None:
                status.controls.clear()
                status.controls.append(
                    flet.Row([flet.ProgressRing(), flet.Text("BURNING & CLOSING...")], alignment=flet.MainAxisAlignment.CENTER)
                )
            page.update()

            alert_dialog_text = ''
            private_key_hex, key_err = resolve_signing_key(data, data.get('secret_tf'))

            if private_key_hex:
                result = await burn_and_close_token_account(
                    owner_address=data['wallet_address'],
                    owner_private_key=private_key_hex,
                    mint_address=data['raw_data']['mint'],
                    network=data['network'],
                    program_id=data['raw_data'].get('program_id'),
                    priority_fee=_pf_from_data(data),
                    cu_limit=data.get('cu_limit', 80000),
                )
                if 'result' in result:
                    alert_dialog_text = (
                        f"All {data['symbol']} burned and the token account was closed. "
                        f"Rent SOL has been refunded to {data['wallet_address']}."
                    )
                elif 'error' in result:
                    alert_dialog_text = f"Burn & Close Error: {result['error']}"
                else:
                    alert_dialog_text = "Burn & Close failed for an unknown reason."
            else:
                alert_dialog_text = key_err or "Could not proceed. Private key is missing or invalid."

            page.show_dialog(flet.AlertDialog(title=flet.Text(alert_dialog_text)))
            if status is not None:
                status.controls.clear()
            page.update()

        def _cancel(ev):
            dlg.open = False
            page.update()

        dlg = flet.AlertDialog(
            modal=True,
            title=flet.Text("Burn all and close account?"),
            content=flet.Text(
                f"This will DESTROY your entire balance of {data['symbol']} and close the "
                f"token account, refunding the rent (~0.002 SOL) to your wallet. "
                f"This action cannot be undone.",
                selectable=True,
            ),
            actions=[
                flet.TextButton("Cancel", on_click=_cancel),
                flet.ElevatedButton("Burn & Close", on_click=_execute, bgcolor=flet.Colors.RED, color=flet.Colors.WHITE),
            ],
        )
        page.show_dialog(dlg)


    # ===================== NFT gallery =====================
    def _nft_network_tag(network: str) -> str:
        if network == MAINNET_RPC:
            return "mainnet"
        if "testnet" in network:
            return "testnet"
        if "devnet" in network:
            return "devnet"
        return "mainnet" if network else ""

    def _nft_tile(nft: dict, wallet: dict) -> flet.TextButton:
        """A single clickable NFT thumbnail used in the gallery grid."""
        img_src = nft.get('image') or "spl-token-placeholder.png"
        tag = _nft_network_tag(nft.get('network'))
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
            on_click=nft_detail_click,
        )

    async def nft_detail_click(e):
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
            await _open_spl_token_page(spl_data)

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
                flet.Text(f"Mint: {_short_addr(nft.get('mint', ''))}", size=11, selectable=True, color=flet.Colors.GREY_700),
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
                        flet.Text(f"Network: {_nft_network_tag(nft.get('network'))}   Amount: {nft.get('amount', 1)}", size=11, color=flet.Colors.GREY_600),
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

    async def nft_enter() -> None:
        """(Re)build the NFT Gallery page contents into `el_nft_page`."""
        el_nft_page.controls.clear()
        wallets = await get_storage_data(prefix="wallet.")
        wallets = [w for w in wallets if isinstance(w, dict) and w.get('address_base58')]
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
                    text=f"{w.get('name', 'Wallet')} · {_short_addr(w['address_base58'])}",
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

        async def _load(ev):
            addr = wallet_dd.value
            if not addr:
                status_txt.value = "Pick a wallet first."
                page.update()
                return
            nets = []
            if cb_mainnet.value:
                nets.append(MAINNET_RPC)
            if cb_testnet.value:
                nets.append("https://api.testnet.solana.com")
            if cb_devnet.value:
                nets.append("https://api.devnet.solana.com")
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
                        _nft_tile(nft, wallet)
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
    # ===================== /NFT gallery =====================

    # ===================== Liquid staking (SOL <-> LST via swap) =====================
    async def lst_enter() -> None:
        """(Re)build the Liquid Staking page contents into `el_lst_page`.

        Liquid staking = swap SOL for a Liquid Staking Token (JitoSOL/mSOL/bSOL/
        jupSOL). The LST appreciates against SOL over time — that appreciation is
        the staking yield (no claim/withdraw instruction). Mainnet-only (Jupiter is
        mainnet-only, like the swap screen).
        """
        el_lst_page.controls.clear()
        wallets = await get_storage_data(prefix="wallet.")
        wallets = [w for w in wallets if isinstance(w, dict) and w.get('address_base58')]
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
                text=f"{w.get('name', 'Wallet')} · {_short_addr(w['address_base58'])}",
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
                if not has_wallet_private_key(wallet):
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
                    private_key_hex=get_wallet_private_key(wallet),
                    slippage_bps=_slippage_bps(),
                    network=MAINNET_RPC,
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
                pos = await lst_positions(addr, network=MAINNET_RPC)
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
            has_key = has_wallet_private_key(wallet) if wallet else False
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
                        if not (wallets_by_addr.get(wallet_dd.value) and has_wallet_private_key(wallets_by_addr[wallet_dd.value])):
                            status_txt.value = "Unstake needs the wallet's private key. Unlock or recover the wallet."
                            el_lst_page.update(); return
                        ev.control.disabled = True
                        status_txt.value = f"Unstaking {amt} {sym}..."
                        el_lst_page.update()
                        res = await lst_unstake(
                            lst_symbol=sym, amount_lst=amt,
                            signer_address=wallet_dd.value,
                            private_key_hex=get_wallet_private_key(wallets_by_addr[wallet_dd.value]),
                            slippage_bps=_slippage_bps(), network=MAINNET_RPC,
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
    # ===================== /Liquid staking =====================

    async def go_to_token_page_button_click(e):
        print(f'****** go_to_token_page_button_click >> e.control.data: {e.control.data}')
        data = e.control.data
        el_token_page.controls.clear()
        pf_block, pf_state = await make_priority_fee_block(ctx, data['network'], data['wallet_address'], cu_limit=2000)

        recipient_tf = flet.TextField(
            label="Recipient address or name.sol", min_lines=1, max_lines=1, max_length=100, expand=True,
        )
        sns_status = flet.Text(size=11, selectable=True, color=flet.Colors.BLUE_700)
        poisoning_banner = make_poisoning_banner()

        async def _on_recipient_change(ev):
            await update_poisoning_banner(ctx, poisoning_banner, recipient_tf.value or "")

        recipient_tf.on_change = _on_recipient_change

        async def _pick_contact(addr, name):
            recipient_tf.value = addr
            try:
                recipient_tf.update()
            except Exception:
                pass
            await update_poisoning_banner(ctx, poisoning_banner, addr)

        async def _open_picker(ev):
            await open_contact_picker(ctx, _pick_contact)

        async def _save_contact(ev):
            await open_save_contact_dialog(ctx, (recipient_tf.value or "").strip())

        transfer_data = {
            **data, 'pf_state': pf_state, 'cu_limit': 2000,
            'recipient_tf': recipient_tf, 'poisoning_banner': poisoning_banner,
            'sns_status': sns_status,
        }
        el_token_page.controls.extend(
            [
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Network: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data['network']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Address: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data['wallet_address']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Amount: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data['sol_amount']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                flet.TextSpan('SOL', flet.TextStyle(size=16)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.TextField(label="Input the amount of SOL", min_lines=1, max_lines=1, max_length=20)
                    ],
                ),
                flet.Row(
                    [
                        recipient_tf,
                        flet.IconButton(
                            icon=flet.Icons.CONTACTS_OUTLINED,
                            tooltip="Pick from address book",
                            on_click=_open_picker,
                        ),
                        flet.IconButton(
                            icon=flet.Icons.PERSON_ADD_ALT_OUTLINED,
                            tooltip="Save recipient as contact",
                            on_click=_save_contact,
                        ),
                    ],
                ),
                flet.Row([poisoning_banner]),
                flet.Row([sns_status]),
                pf_block,
                flet.Row(
                    [
                        flet.ElevatedButton(
                            content=flet.Text("Transfer SOL"),
                            on_click=transfer_sol_button_click,
                            data=transfer_data,
                        ),
                    ],
                ),
                flet.Column(),
            ]
        )
        if not has_wallet_private_key(data['wallet_data']):
            el_token_page.controls.insert(
                5,
                flet.Row(
                    [
                        flet.TextField(label="Enter Secret (12/24 Words or Private Key)", min_lines=1, max_lines=1, max_length=100)
                    ],
                )
            )
        await page.push_route("token-page")


    async def transfer_sol_button_click(e):
        data = e.control.data
        recipient_input = (data.get('recipient_tf').value or "").strip() if data.get('recipient_tf') else ""
        try:
            resolved_recipient, resolution_message = await resolve_recipient_input(recipient_input, data['network'])
        except ValueError as err:
            page.show_dialog(flet.AlertDialog(title=flet.Text(str(err))))
            return
        if resolution_message:
            data['sns_status'].value = resolution_message
            data['sns_status'].color = flet.Colors.BLUE_700
            page.update()
        # print(f'****** transfer_sol_button_click >> e.control.data: {data}')
        # Address-poisoning gate (before disabling the button). On confirm it
        # re-invokes this same handler; the address is then whitelisted for the session.
        _rtf = data.get('recipient_tf')
        if _rtf is not None and not await _maybe_block_for_poisoning(
                ctx, resolved_recipient, lambda: transfer_sol_button_click(e)):
            return
        e.control.disabled = True  # блокируем кнопку
        e.control.parent.parent.controls[-1].controls.clear()
        e.control.parent.parent.controls[-1].controls.append(
            flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
        )
        page.update()
        result_transfer_txt = ''
        sol_balance_after = ''
        alert_dialog_text = ''
        transfer_sol_amount = ''
        recipient_address = ''

        private_key_hex = get_wallet_private_key(data['wallet_data'])

        if not private_key_hex:
            input_secret = e.control.parent.parent.controls[5].controls[0].value.strip()
            if is_valid_wallet_seed_phrase(input_secret):
                # преобразовать секретные слова 12/24 в приватный ключ в hex формате
                for attempt in range(10):
                    words, wallet_address_base58, secret_key_base58, new_private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
                    if wallet_address_base58 == data['wallet_data']['address_base58']:
                        private_key_hex = new_private_key_hex
                        break
                    elif error:
                        alert_dialog_text = f"Error after: {attempt} attempts to get private key from secret words: {input_secret}! Error Msg: {error}"
                else:
                    alert_dialog_text = f'Failed to get private key after: {attempt} attempts from secret words: {input_secret}'
            elif is_valid_private_key(input_secret):
                if len(input_secret) == 64:
                    private_key_hex = input_secret
            else:
                alert_dialog_text = "Error Secret!"

        if private_key_hex:
            recipient_address = resolved_recipient
            # print(f'**** recipient: {recipient_address}')
            if is_valid_wallet_address(recipient_address):

                transfer_sol_amount = e.control.parent.parent.controls[3].controls[0].value
                # print(f'**** transfer_sol_button_click >> SOL: {transfer_sol_amount}')
                if is_valid_amount(transfer_sol_amount):
                    transfer_sol_amount = float(transfer_sol_amount)

                    min_sol_balance = await get_min_sol_balance(data['network'])
                    # print(f'**** min_sol_balance: {min_sol_balance}')
                    if min_sol_balance is None:
                        min_sol_balance = 0

                    if (transfer_sol_amount > 0) and (transfer_sol_amount < data['sol_amount'] - min_sol_balance):
                        result = await transfer_sol_token(
                            sender_address=data['wallet_data']['address_base58'],
                            sender_private_key=private_key_hex,
                            recipient_address=recipient_address,
                            amount=transfer_sol_amount,
                            network=data['network'],
                            priority_fee=_pf_from_data(data),
                            cu_limit=data.get('cu_limit', 2000),
                        )
                        # print(f'****** RESULT: {result}')

                        if 'result' in result:
                            sol_balance_after = await get_sol_balance(address=data['wallet_data']['address_base58'], network=data['network'])
                            if sol_balance_after:
                                e.control.parent.parent.controls[2].controls[0].spans=[
                                    flet.TextSpan('Amount: ', flet.TextStyle(size=16)),
                                    flet.TextSpan(f'{sol_balance_after} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                    flet.TextSpan('SOL', flet.TextStyle(size=16)),
                                ]
                                transfer_fee = data['sol_amount'] - sol_balance_after - transfer_sol_amount
                                result_transfer_txt = f"Transfer fee: {transfer_fee:.9f} SOL"
                            alert_dialog_text = f"Transfer of {transfer_sol_amount} SOL was Successfully!"
                        elif 'error' in result:
                            alert_dialog_text = f"Error during Transfer. Error Msg: {result['error']}"
                        elif not result:
                            alert_dialog_text = "Error during Transfer!"
                        else:
                            alert_dialog_text = f"Error Result: {result}"
                    else:
                        alert_dialog_text = "Not enough SOL balance for transfer."
                else:
                    alert_dialog_text = f"The amount of SOL={transfer_sol_amount} is not valid. Please enter the correct number."
            else:
                alert_dialog_text = f"The recipient wallet address: {recipient_address} is not valid. Please enter the correct recipient wallet address."
        page.show_dialog(
            flet.AlertDialog(
                title=flet.Text(alert_dialog_text),
            )
        )
        e.control.parent.parent.controls[3].controls[0].value = ''
        e.control.parent.parent.controls[-1].controls.clear()
        e.control.parent.parent.controls[-1].controls.extend(
            [
                flet.Divider(thickness=3),
                flet.Row(
                    [
                        flet.Text(value='Transfer sol info:', size=14),
                    ],
                    alignment=flet.MainAxisAlignment.CENTER,
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Information message: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{alert_dialog_text}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                    scroll=flet.ScrollMode.AUTO,
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('From: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data['wallet_data']['address_base58']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('To: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{recipient_address}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Transfer: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{transfer_sol_amount} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                flet.TextSpan('SOL', flet.TextStyle(size=16)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Balance before: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{data['sol_amount']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                flet.TextSpan('SOL', flet.TextStyle(size=16)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value='',
                            selectable=True,
                            spans=[
                                flet.TextSpan('Balance after: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{sol_balance_after} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                flet.TextSpan('SOL', flet.TextStyle(size=16)),
                            ]
                        ),
                    ],
                ),
                flet.Row(
                    [
                        flet.Text(
                            value=result_transfer_txt,
                            size=14,
                            selectable=True,
                        ),
                    ],
                ),
            ]
        )
        e.control.disabled = False  # разблокируем кнопку
        page.update()

    async def request_airdrop_sol_button_click(e):
        data = e.control.data
        print(f'****** request_airdrop_sol_button_click >> e.control.data: {data}')
        e.control.disabled = True  # блокируем кнопку
        e.control.parent.parent.controls[-1].controls.clear()
        e.control.parent.parent.controls[-1].controls.append(
            flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
        )
        page.update()
        result_transfer_txt = ''
        sol_balance_after = ''
        alert_dialog_text = f"Not Result request airdrop sol for wallet: {data['wallet_address']}"
        transfer_sol_amount = ''
        recipient_address = ''

        if is_valid_wallet_address(data['wallet_address']):
            result = await request_airdrop(pubkey=data['wallet_address'], lamports=1_000_000_000, network=data['network'])

            alert_dialog_text = f"The result airdrop SOL for wallet address: {data['wallet_address']}: {result}"

        page.show_dialog(
            flet.AlertDialog(
                title=flet.Text(alert_dialog_text),
            )
        )

        # e.control.parent.parent.controls[3].controls[0].value = ''
        e.control.parent.parent.controls[-1].controls.clear()

        e.control.disabled = False  # разблокируем кнопку
        page.update()


    async def generate_new_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        value['name'] = txt_wallet_name.value
        value['description'] = txt_wallet_description.value
        value['address_base58'] = txt_wallet_address.value
        value['private_key_hex'] = txt_private_key.value
        value['public_key_hex'] = txt_public_key.value
        value['words'] = txt_words.value
        value['secret_key_base58'] = txt_secret_key_base58.value

        print(f'generate_new_wallet_storage >> key: {key}')
        print(f'generate_new_wallet_storage >> value: {value}')

        await page.shared_preferences.set(key, json.dumps(encrypt_for_storage(value)))

        txt_error.value = ''
        txt_wallet_created.value = ''
        txt_wallet_name.value = ''
        txt_wallet_description.value = ''
        txt_wallet_address.value = ''
        txt_private_key.value = ''
        txt_public_key.value = ''
        txt_words.value = ''
        txt_secret_key_base58.value = ''
        create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        page.update()

    generate_new_solana_wallet_card_save_button = flet.TextButton(
        "Save",
        on_click=generate_new_solana_wallet_card_save_button_clicked,
        data=0,
    )


    async def recover_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        value['name'] = txt_recover_wallet_name.value
        value['description'] = txt_recover_wallet_description.value
        value['address_base58'] = txt_recover_wallet_address.value
        value['private_key_hex'] = txt_recover_private_key.value
        value['public_key_hex'] = txt_recover_public_key.value
        value['words'] = txt_recover_words.value
        value['secret_key_base58'] = txt_recover_secret_key_base58.value

        print(f'recover_wallet_storage >> key: {key}')
        print(f'recover_wallet_storage >> value: {value}')

        await page.shared_preferences.set(key, json.dumps(encrypt_for_storage(value)))

        txt_recover_error.value = ''
        txt_recover_wallet_created.value = ''
        txt_recover_wallet_name.value = ''
        txt_recover_wallet_description.value = ''
        txt_recover_wallet_address.value = ''
        txt_recover_private_key.value = ''
        txt_recover_public_key.value = ''
        txt_recover_words.value = ''
        txt_recover_secret_key_base58.value = ''
        txt_recover_wallet_secret.value = ''
        recover_wallet_page.controls.remove(recover_solana_wallet_card)
        page.update()

    recover_solana_wallet_card_save_button = flet.TextButton(
        "Save",
        on_click=recover_solana_wallet_card_save_button_clicked,
        data=0,
    )


    async def add_address_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        value['name'] = txt_add_address_wallet_name.value
        value['description'] = txt_add_address_wallet_description.value
        value['address_base58'] = txt_add_address_wallet_address.value
        value['private_key_hex'] = ''
        value['public_key_hex'] = ''
        value['words'] = ''
        value['secret_key_base58'] = ''
        value[WATCH_ONLY_FIELD] = True

        print(f'add_address_wallet_storage >> key: {key}')
        print(f'add_address_wallet_storage >> value: {value}')

        await page.shared_preferences.set(key, json.dumps(encrypt_for_storage(value)))

        txt_add_address_error.value = ''
        txt_add_address_wallet_created.value = ''
        txt_add_address_wallet_name.value = ''
        txt_add_address_wallet_description.value = ''
        txt_add_address_wallet_address.value = ''
        add_wallet_address_page.controls.remove(add_address_solana_wallet_card)
        page.update()

    add_address_solana_wallet_card_save_button = flet.TextButton(
        "Save",
        on_click=add_address_solana_wallet_card_save_button_clicked,
        data=0,
    )

    async def generate_new_solana_wallet_card_clear_button_clicked(e):
        txt_error.value = ''
        txt_wallet_created.value = ''
        txt_wallet_name.value = ''
        txt_wallet_description.value = ''
        txt_wallet_address.value = ''
        txt_private_key.value = ''
        txt_public_key.value = ''
        txt_words.value = ''
        txt_secret_key_base58.value = ''
        if generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        if error_generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(error_generate_new_solana_wallet_card)
        page.update()

    generate_new_solana_wallet_card_clear_button = flet.TextButton(
        "Clear",
        on_click=generate_new_solana_wallet_card_clear_button_clicked,
        data=0,
    )

    async def recover_solana_wallet_card_clear_button_clicked(e):
        txt_recover_error.value = ''
        txt_recover_wallet_created.value = ''
        txt_recover_wallet_name.value = ''
        txt_recover_wallet_description.value = ''
        txt_recover_wallet_address.value = ''
        txt_recover_private_key.value = ''
        txt_recover_public_key.value = ''
        txt_recover_words.value = ''
        txt_recover_secret_key_base58.value = ''
        txt_recover_wallet_secret.value = ''
        if recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(recover_solana_wallet_card)
        if error_recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(error_recover_solana_wallet_card)
        page.update()

    recover_solana_wallet_card_clear_button = flet.TextButton(
        "Clear",
        on_click=recover_solana_wallet_card_clear_button_clicked,
        data=0,
    )

    async def add_address_solana_wallet_card_clear_button_clicked(e):
        txt_add_address_error.value = ''
        txt_add_address_wallet_created.value = ''
        txt_add_address_wallet_name.value = ''
        txt_add_address_wallet_description.value = ''
        txt_add_address_wallet_address.value = ''
        if add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(add_address_solana_wallet_card)
        if error_add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(error_add_address_solana_wallet_card)
        page.update()

    add_address_solana_wallet_card_clear_button = flet.TextButton(
        "Clear",
        on_click=add_address_solana_wallet_card_clear_button_clicked,
        data=0,
    )

    generate_new_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Created:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_created,
                    flet.Text("Wallet Name:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_name,
                    flet.Text("Wallet Description:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_description,
                    flet.Text("Wallet Address (Base58, size 44):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_address,
                    flet.Text("Secret Key (Base58, size 88, e.g. Phantom):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_secret_key_base58,
                    flet.Text("Private Key (Hex, size 64):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_private_key,
                    flet.Text("Public Key (Hex):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_public_key,
                    flet.Text("Mnemonic Words (12/24 words):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_words,
                    # flet.Text("Seed (Hex):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    # txt_seed,
                    flet.Row(
                        [
                            generate_new_solana_wallet_card_save_button,
                            flet.TextButton("Copy", on_click=lambda e: page.run_task(copy_wallet_data_click, e, 'create')),
                            generate_new_solana_wallet_card_clear_button,
                        ],
                        alignment=flet.MainAxisAlignment.END,
                    ),
                ]
            ),
            width=400,
            padding=10,
        )
    )

    recover_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Created:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_created,
                    flet.Text("Wallet Name:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_name,
                    flet.Text("Wallet Description:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_description,
                    flet.Text("Wallet Address (Base58, size 44):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_address,
                    flet.Text("Secret Key (Base58, size 88, e.g. Phantom):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_secret_key_base58,
                    flet.Text("Private Key (Hex, size 64):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_private_key,
                    flet.Text("Public Key (Hex):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_public_key,
                    flet.Text("Mnemonic Words (12/24 words):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_words,
                    flet.Row(
                        [
                            recover_solana_wallet_card_save_button,
                            flet.TextButton("Copy", on_click=lambda e: page.run_task(copy_wallet_data_click, e, 'recover')),
                            recover_solana_wallet_card_clear_button,
                        ],
                        alignment=flet.MainAxisAlignment.END,
                    ),
                ]
            ),
            width=400,
            padding=10,
        )
    )

    add_address_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Created:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_created,
                    flet.Text("Wallet Name:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_name,
                    flet.Text("Wallet Description:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_description,
                    flet.Text("Wallet Address (Base58, size 44):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_address,
                    flet.Row(
                        [
                            add_address_solana_wallet_card_save_button,
                            flet.TextButton("Copy", on_click=lambda e: page.run_task(copy_wallet_data_click, e, 'add')),
                            add_address_solana_wallet_card_clear_button,
                        ],
                        alignment=flet.MainAxisAlignment.END,
                    ),
                ]
            ),
            width=400,
            padding=10,
        )
    )

    async def copy_wallet_data_click(e, mode):
        data_to_copy = {}
        if mode == 'create':
            data_to_copy = {
                'created': txt_wallet_created.value,
                'name': txt_wallet_name.value,
                'description': txt_wallet_description.value,
                'address_base58': txt_wallet_address.value,
                'private_key_hex': txt_private_key.value,
                'public_key_hex': txt_public_key.value,
                'words': txt_words.value,
                'secret_key_base58': txt_secret_key_base58.value,
            }
        elif mode == 'recover':
            data_to_copy = {
                'created': txt_recover_wallet_created.value,
                'name': txt_recover_wallet_name.value,
                'description': txt_recover_wallet_description.value,
                'address_base58': txt_recover_wallet_address.value,
                'private_key_hex': txt_recover_private_key.value,
                'public_key_hex': txt_recover_public_key.value,
                'words': txt_recover_words.value,
                'secret_key_base58': txt_recover_secret_key_base58.value,
            }
        elif mode == 'add':
            data_to_copy = {
                'created': txt_add_address_wallet_created.value,
                'name': txt_add_address_wallet_name.value,
                'description': txt_add_address_wallet_description.value,
                'address_base58': txt_add_address_wallet_address.value,
            }
        await page.clipboard.set(json.dumps(data_to_copy, indent=2))
        page.show_dialog(flet.AlertDialog(title=flet.Text("Data copied to clipboard!")))

    error_generate_new_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_error
                ]
            ),
            width=400,
            padding=10,
        )
    )

    error_recover_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_recover_error
                ]
            ),
            width=400,
            padding=10,
        )
    )

    error_add_address_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_recover_error
                ]
            ),
            width=400,
            padding=10,
        )
    )

    async def generate_new_solana_wallet_button(e):
        if generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        if error_generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(error_generate_new_solana_wallet_card)
        words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error = create_solana_wallet()
        if error:
            txt_error.value = error
            create_wallet_page.controls.append(error_generate_new_solana_wallet_card)
            page.update()
            return

        txt_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        txt_wallet_name.value = input_wallet_name.value.strip()
        txt_wallet_description.value = input_wallet_description.value.strip()
        txt_wallet_address.value = wallet_address_base58
        txt_private_key.value = private_key_hex
        txt_public_key.value = public_key_hex
        txt_words.value = words
        # txt_seed.value = seed_hex
        txt_secret_key_base58.value = secret_key_base58
        page.update()

        # Backup verification: reveal the seed, then quiz the user on it
        # before allowing them to see the full secret card / save it.
        words_list = words.split()
        reveal_dlg = None  # assigned by show_reveal(); closed over by start_quiz

        async def start_quiz(ev):
            nonlocal reveal_dlg
            if reveal_dlg is not None:
                reveal_dlg.open = False
            page.update()

            positions = sorted(random.sample(range(len(words_list)), min(2, len(words_list))))
            fields = []
            quiz_rows = [
                flet.Text("Confirm your recovery phrase by entering the requested words.", size=12),
            ]
            for pos in positions:
                tf = flet.TextField(label=f"Word #{pos + 1}", min_lines=1, max_lines=1, max_length=30)
                fields.append((pos, tf))
                quiz_rows.append(tf)
            quiz_err = flet.Text("", color="red")

            async def verify(inner):
                ok = True
                for pos, tf in fields:
                    if (tf.value or "").strip().lower() != words_list[pos].lower():
                        ok = False
                if ok:
                    quiz_dlg.open = False
                    page.update()
                    create_wallet_page.controls.append(generate_new_solana_wallet_card)
                    page.update()
                else:
                    quiz_err.value = "One or more words are incorrect. Check your spelling and try again."
                    page.update()

            async def reveal_again(inner):
                quiz_dlg.open = False
                page.update()
                await show_reveal()

            quiz_dlg = flet.AlertDialog(
                modal=True,
                title=flet.Text("Verify your backup"),
                content=flet.Column(quiz_rows + [quiz_err], tight=True),
                actions=[
                    flet.TextButton("Show words again", on_click=reveal_again),
                    flet.ElevatedButton("Verify", on_click=verify),
                ],
                actions_alignment=flet.MainAxisAlignment.END,
            )
            page.show_dialog(quiz_dlg)

        async def show_reveal():
            nonlocal reveal_dlg
            reveal_rows = [
                flet.Text(
                    "These 12 words are the ONLY way to recover this wallet. "
                    "Write them down and store them safely. No one can recover them for you.",
                    size=12, color="red",
                ),
                flet.Text(words, selectable=True, size=14, weight=flet.FontWeight.BOLD),
                flet.Text("You will be asked to confirm them on the next screen.", size=12),
            ]
            reveal_dlg = flet.AlertDialog(
                modal=True,
                title=flet.Text("Your recovery phrase"),
                content=flet.Column(reveal_rows, tight=True),
                actions=[flet.ElevatedButton("I've written it down", on_click=start_quiz)],
                actions_alignment=flet.MainAxisAlignment.END,
            )
            page.show_dialog(reveal_dlg)

        await show_reveal()

    async def recover_solana_wallet_button(e):
        if recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(recover_solana_wallet_card)
        if error_recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(error_recover_solana_wallet_card)

        if input_recover_wallet_secret.value:
            words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_recover_wallet_secret.value.strip())
        else:
            error = 'Input the secret'

        if error:
            txt_recover_error.value = error
            recover_wallet_page.controls.append(error_recover_solana_wallet_card)
        else:
            txt_recover_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            txt_recover_wallet_name.value = input_recover_wallet_name.value.strip()
            txt_recover_wallet_description.value = input_recover_wallet_description.value.strip()
            txt_recover_wallet_secret.value = input_recover_wallet_secret.value.strip()
            txt_recover_wallet_address.value = wallet_address_base58
            txt_recover_private_key.value = private_key_hex
            txt_recover_public_key.value = public_key_hex
            txt_recover_words.value = words
            txt_recover_secret_key_base58.value = secret_key_base58
            recover_wallet_page.controls.append(recover_solana_wallet_card)
        page.update()

    async def add_address_solana_wallet_button(e):
        if add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(add_address_solana_wallet_card)
        if error_add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(error_add_address_solana_wallet_card)

        error = ''
        if not input_add_wallet_address.value.strip():
            error = 'Input the wallet address'

        if error:
            txt_add_address_error.value = error
            add_wallet_address_page.controls.append(error_add_address_solana_wallet_card)
        else:
            txt_add_address_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            txt_add_address_wallet_name.value = input_add_address_wallet_name.value.strip()
            txt_add_address_wallet_description.value = input_add_address_wallet_description.value.strip()
            txt_add_address_wallet_address.value = input_add_wallet_address.value.strip()
            add_wallet_address_page.controls.append(add_address_solana_wallet_card)
        page.update()

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

    # ===================== WalletConnect v2 =====================
    from solana.walletconnect import WalletConnectClient, WalletConnectError, SOLANA_CHAINS

    WC_PROJECT_ID_KEY = "wc.project_id"
    WC_IDENTITY_KEY = "wc.identity_seed"
    wc_state: dict = {"client": None}

    async def _wc_get_project_id() -> str | None:
        if await page.shared_preferences.contains_key(WC_PROJECT_ID_KEY):
            v = await page.shared_preferences.get(WC_PROJECT_ID_KEY)
            if v:
                return v
        return None

    async def _wc_get_identity_seed() -> bytes:
        if await page.shared_preferences.contains_key(WC_IDENTITY_KEY):
            v = await page.shared_preferences.get(WC_IDENTITY_KEY)
            try:
                return bytes.fromhex(v)
            except Exception:
                pass
        seed = os.urandom(32)
        await page.shared_preferences.set(WC_IDENTITY_KEY, seed.hex())
        return seed

    async def _wc_resolve_signer(account_b58: str | None) -> str | None:
        if not account_b58:
            return None
        wallets = await get_storage_data(prefix="wallet.")
        for w in wallets:
            if w.get("address_base58") == account_b58:
                if w.get(WATCH_ONLY_FIELD):
                    return None
                try:
                    return get_wallet_private_key(w)
                except Exception:
                    return None
        return None

    def _wc_dapp_name(obj: dict) -> str:
        md = obj.get("peerMetadata") or obj.get("proposer", {}).get("metadata") or {}
        return md.get("name") or md.get("url") or "dApp"

    async def _wc_refresh_sessions() -> None:
        wc_sessions_list.controls.clear()
        client = wc_state["client"]
        if client is None or not client.list_sessions():
            wc_sessions_list.controls.append(flet.Text("No active sessions."))
        else:
            for s in client.list_sessions():
                peer_md = s.get("peerMetadata", {}) or {}
                acct_short = ", ".join(a.split(":")[-1] for a in s.get("accounts", []))
                wc_sessions_list.controls.append(
                    flet.Card(
                        content=flet.Container(
                            content=flet.Column(
                                [
                                    flet.Text(_wc_dapp_name(s), size=16, weight=flet.FontWeight.BOLD),
                                    flet.Text(peer_md.get("url") or "", size=11, selectable=True),
                                    flet.Text("accounts: " + acct_short, size=11, selectable=True),
                                    flet.Row(
                                        [flet.OutlinedButton("Disconnect", data=s["topic"], on_click=wc_disconnect_click)]
                                    ),
                                ]
                            ),
                            padding=10,
                            width=360,
                        )
                    )
                )
        try:
            page.update()
        except Exception:
            pass

    async def wc_disconnect_click(e):
        client = wc_state["client"]
        if client:
            await client.disconnect_session(e.control.data)
        await _wc_refresh_sessions()

    async def on_wc_proposal(proposal: dict) -> None:
        wallets = await get_storage_data(prefix="wallet.")
        addrs = [w.get("address_base58") for w in wallets if w.get("address_base58")]
        if not addrs:
            page.show_dialog(flet.AlertDialog(title=flet.Text("No wallets available. Add a wallet first.")))
            return
        dd = flet.Dropdown(
            label="Account to connect",
            options=[flet.dropdown.Option(a) for a in addrs],
            value=addrs[0],
            width=320,
        )
        req_ns = proposal.get("requiredNamespaces", {}) or {}
        chains: list = []
        methods: list = []
        for ns in req_ns.values():
            chains += (ns or {}).get("chains", []) or []
            methods += (ns or {}).get("methods", []) or []
        meta = (proposal.get("proposer", {}) or {}).get("metadata", {}) or {}

        async def do_approve(e):
            dlg_p.open = False
            page.update()
            client = wc_state["client"]
            try:
                topic = await client.approve(proposal["id"], accounts=[dd.value])
                page.show_dialog(flet.AlertDialog(title=flet.Text(f"Session approved ({topic[:8]}…).")))
            except Exception as ex:
                page.show_dialog(flet.AlertDialog(title=flet.Text(f"Approve failed: {ex}")))

        async def do_reject(e):
            dlg_p.open = False
            page.update()
            client = wc_state["client"]
            if client:
                await client.reject(proposal["id"])

        dlg_p = flet.AlertDialog(
            title=flet.Text(f"Connect to {meta.get('name', 'dApp')}?"),
            content=flet.Column(
                [
                    flet.Text(meta.get("url") or "", size=11, selectable=True),
                    flet.Text((meta.get("description") or "")[:160], size=11),
                    flet.Text("Chains: " + ", ".join(chains), size=12),
                    flet.Text("Methods: " + ", ".join(methods), size=12),
                    dd,
                ],
                scroll=flet.ScrollMode.AUTO,
                height=280,
            ),
            actions=[
                flet.TextButton("Reject", on_click=do_reject),
                flet.ElevatedButton("Approve", on_click=do_approve),
            ],
            actions_alignment=flet.MainAxisAlignment.END,
        )
        page.show_dialog(dlg_p)

    def _wc_render_preview(preview: dict, show_program_ids: bool = False) -> str:
        method = preview.get("method")
        lines = [f"Method: {method}", f"Chain: {preview.get('chain_id')}"]
        decoded = preview.get("decoded") or {}
        if decoded.get("programs"):
            lines.append("Programs: " + ", ".join(decoded["programs"]))
        if decoded.get("unknown_programs"):
            if show_program_ids:
                lines.append("⚠ Unverified programs: " + ", ".join(decoded["unknown_programs"]))
            else:
                lines.append(f"⚠ {len(decoded['unknown_programs'])} unverified program(s)")
        sim = preview.get("simulation") or {}
        if sim:
            lines.append("Predicted status: " + str(sim.get("status")))
            if sim.get("fee_sol") is not None:
                lines.append("Fee: " + str(sim.get("fee_sol")) + " SOL")
            for ch in (sim.get("sol_changes") or [])[:8]:
                acct = str(ch.get("account", ""))
                lines.append(f"SOL Δ {acct[:10]}…: {ch.get('delta_sol', 0):+.9f}")
            for ch in (sim.get("token_changes") or [])[:8]:
                acct = str(ch.get("account", ""))
                lines.append(
                    f"Token Δ {acct[:10]}…: {ch.get('delta_amount', '?')} ({ch.get('mint', '')[:8]}…)"
                )
            for w in sim.get("warnings") or []:
                lines.append("⚠ " + w)
        if preview.get("message_utf8") is not None:
            lines.append("Message: " + str(preview["message_utf8"]))
        if preview.get("preview_error"):
            lines.append("preview error: " + str(preview["preview_error"]))
        return "\n".join(lines)

    async def on_wc_request(session: dict, request: dict, preview: dict) -> None:
        rid = request["id"]
        method = request.get("method")
        accounts = [a.split(":")[-1] for a in session.get("accounts", [])]
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        target = params.get("pubkey") if params else None
        if not target and accounts:
            target = accounts[0]
        _wc_mode = await get_experience(page)
        preview_text = _wc_render_preview(preview, show_program_ids=feature("sim_detail", _wc_mode))

        async def do_approve(e):
            dlg_r.open = False
            page.update()
            client = wc_state["client"]
            priv = await _wc_resolve_signer(target)
            if not priv:
                page.show_dialog(
                    flet.AlertDialog(title=flet.Text(f"No private key for {target} (watch-only / not found)."))
                )
                await client.reject_request(rid)
                return
            try:
                await client.approve_request(rid, priv)
                page.show_dialog(flet.AlertDialog(title=flet.Text("Signed & sent to dApp.")))
            except Exception as ex:
                page.show_dialog(flet.AlertDialog(title=flet.Text(f"Sign failed: {ex}")))
                await client.reject_request(rid)

        async def do_reject(e):
            dlg_r.open = False
            page.update()
            client = wc_state["client"]
            if client:
                await client.reject_request(rid)

        sim = preview.get("simulation") or {}
        sim_fail = sim.get("status") == "error"
        content_controls = [
            flet.Text(f"Account: {target or '?'}", size=11, selectable=True),
            flet.Text(preview_text, selectable=True, size=12),
        ]
        if sim_fail:
            content_controls.append(
                flet.Text(
                    "⚠ Simulation predicts this transaction will FAIL. Signing is blocked.",
                    color="red", size=12,
                )
            )
        # Developer-only (Phase 5): full simulation logs + raw session/request JSON.
        # Reuse _wc_mode (already awaited above) — avoids two extra shared_preferences round-trips.
        if feature("sim_detail", _wc_mode):
            sim_logs = sim.get("logs") or []
            if sim_logs:
                content_controls.append(flet.Text("Simulation logs:", size=11, weight=flet.FontWeight.BOLD))
                log_controls = []
                for log in sim_logs:
                    log_color = "red" if ("failed" in str(log).lower() or "error" in str(log).lower()) else "grey"
                    log_controls.append(flet.Text(f"• {log}", size=10, color=log_color, selectable=True))
                content_controls.append(
                    flet.Container(
                        content=flet.Column(log_controls, spacing=1, scroll=flet.ScrollMode.AUTO),
                        height=120,
                        padding=5,
                        border=flet.border.all(1, "black12"),
                        border_radius=5,
                    )
                )
            # SECURITY: scrub relay keying material before dumping. The live
            # WC2 `symkey` (ChaCha20-Poly1305 relay session key) would let
            # anyone decrypt + forge relay messages for this session — its
            # topic is public. The other session fields (peer, accounts,
            # namespaces, public X25519 keys) are dApp-known and safe to show.
            scrubbed_session = {k: v for k, v in session.items() if k != "symkey"}
            try:
                raw_json = json.dumps(
                    {"session": scrubbed_session, "request": request, "simulation": sim},
                    indent=2, default=str,
                )
            except Exception:
                raw_json = "raw JSON unavailable"
            content_controls.append(flet.Text("Raw session/request JSON:", size=11, weight=flet.FontWeight.BOLD))
            content_controls.append(
                flet.Container(
                    content=flet.Column(
                        [flet.Text(raw_json, size=9, selectable=True, color=flet.Colors.GREY_700)],
                        spacing=1, scroll=flet.ScrollMode.AUTO,
                    ),
                    height=160,
                    padding=5,
                    border=flet.border.all(1, "black12"),
                    border_radius=5,
                )
            )
        dlg_r = flet.AlertDialog(
            title=flet.Text(f"dApp request: {method}"),
            content=flet.Column(
                content_controls,
                scroll=flet.ScrollMode.AUTO,
                height=380,
            ),
            actions=[
                flet.TextButton("Reject", on_click=do_reject),
                flet.ElevatedButton("Approve & Sign", on_click=do_approve, disabled=sim_fail),
            ],
            actions_alignment=flet.MainAxisAlignment.END,
        )
        page.show_dialog(dlg_r)

    async def on_wc_session(event: str, session: dict) -> None:
        await _wc_refresh_sessions()

    async def _wc_ensure_client() -> WalletConnectClient | None:
        if wc_state["client"] is not None:
            return wc_state["client"]
        pid = await _wc_get_project_id()
        if not pid:
            pid = (wc_pid_input.value or "").strip()
            if pid:
                await page.shared_preferences.set(WC_PROJECT_ID_KEY, pid)
        if not pid:
            return None
        seed = await _wc_get_identity_seed()
        client = WalletConnectClient(
            pid,
            seed,
            signer_resolver=_wc_resolve_signer,
            on_proposal=on_wc_proposal,
            on_request=on_wc_request,
            on_session=on_wc_session,
        )
        await client.start()
        wc_state["client"] = client
        wc_status_text.value = f"WC ready (clientId {client.client_id[:18]}…)"
        try:
            page.update()
        except Exception:
            pass
        return client

    async def wc_connect_click(e):
        client = await _wc_ensure_client()
        if client is None:
            page.show_dialog(
                flet.AlertDialog(
                    title=flet.Text("Enter your WalletConnect projectId first (free at cloud.walletconnect.com).")
                )
            )
            return
        uri = (wc_uri_input.value or "").strip()
        if not uri.startswith("wc:"):
            page.show_dialog(flet.AlertDialog(title=flet.Text("Paste a valid 'wc:' URI copied from a dApp.")))
            return
        try:
            await client.pair(uri)
            wc_status_text.value = "Pairing… waiting for the dApp's session proposal."
            try:
                page.update()
            except Exception:
                pass
        except Exception as ex:
            page.show_dialog(flet.AlertDialog(title=flet.Text(f"Pair failed: {ex}")))

    async def wc_save_pid_click(e):
        pid = (wc_pid_input.value or "").strip()
        if pid:
            await page.shared_preferences.set(WC_PROJECT_ID_KEY, pid)
            page.show_dialog(flet.AlertDialog(title=flet.Text("projectId saved.")))

    wc_uri_input = flet.TextField(label="Paste dApp 'wc:' URI", width=340, multiline=True, max_lines=3)
    wc_pid_input = flet.TextField(label="WalletConnect projectId", width=300)
    wc_status_text = flet.Text("WC: idle", size=12, selectable=True)
    wc_sessions_list = flet.Column(spacing=8)

    async def wc_enter_page():
        pid = await _wc_get_project_id()
        wc_pid_input.value = pid or ""
        await _wc_refresh_sessions()
        await _wc_ensure_client()

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
            await wc_enter_page()
            page.views.append(wc_page)
        elif page.route == "nft-page":
            await nft_enter()
            page.views.append(nft_page)
        elif page.route == "addressbook-page":
            await addressbook_enter(ctx)
            page.views.append(addressbook_page)
        elif page.route == "stake-page":
            await lst_enter()
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
            await rawkey_enter()
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

    recover_wallet_page = flet.View(
        route="recover-wallet-page",
        appbar=flet.AppBar(
            title=flet.Text("Recover Wallet Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            # flet.Text('Recover wallet', size=30, font_family="Georgia"),
            flet.Row([flet.Text('Recover wallet', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_secret], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(content=flet.Text('Recover Wallet'), width=200, height=40, on_click=recover_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ]
    )

    add_wallet_address_page = flet.View(
        route="add-wallet-address-page",
        appbar=flet.AppBar(
            title=flet.Text("Add Wallet Address Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Row([flet.Text('Add wallet address', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_add_address_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_add_address_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_add_wallet_address], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(content=flet.Text('Add Wallet Address'), width=200, height=40, on_click=add_address_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ]
    )

    create_wallet_page = flet.View(
        route="create-wallet-page",
        appbar=flet.AppBar(
            title=flet.Text("Create New Wallet Page"),
            color="white",
            bgcolor="teal",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Row([flet.Text('Create New Wallet', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            # generate_new_solana_wallet_card,
            flet.Row([input_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(content=flet.Text('Create New Wallet'), width=200, height=40, on_click=generate_new_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ]
    )

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

    # ===================== Developer: Simulation inspector =====================
    # Dev-only (gated by `sim_detail`). Paste a base64 transaction and run the
    # same anti-phishing analyze_transaction() the WalletConnect flow uses, to
    # inspect fee / programs / SOL & token deltas / warnings / logs without signing.
    sim_out = flet.Column(spacing=4)
    sim_net_dd = flet.Dropdown(
        label="Network",
        width=420,
        value=MAINNET_RPC,
        options=[
            flet.dropdown.Option(key=MAINNET_RPC, text="mainnet-beta"),
            flet.dropdown.Option(key="https://api.testnet.solana.com", text="testnet"),
            flet.dropdown.Option(key="https://api.devnet.solana.com", text="devnet"),
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

    def _sim_row(label: str, value, color=None) -> flet.Text:
        return flet.Text(f"{label}: {value}", size=12, selectable=True,
                         color=color or flet.Colors.BLACK87)

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
        for ch in res.get("sol_changes") or []:
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

    sim_page = flet.View(
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

    # ===================== Developer: Raw RPC inspector =====================
    # Dev-only (gated by `custom_rpc`). Run arbitrary read-only JSON-RPC calls
    # against a chosen endpoint + commitment. No solana/ changes — direct httpx.
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
            return (rpc_custom_tf.value or "").strip() or MAINNET_RPC
        return {
            "mainnet": MAINNET_RPC,
            "testnet": "https://api.testnet.solana.com",
            "devnet": "https://api.devnet.solana.com",
        }.get(src, MAINNET_RPC)

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

    rpc_page = flet.View(
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

    # ===================== Developer: Export raw keys =====================
    # Dev-only (gated by `raw_export`). Dedicated, warning-gated reveal of a
    # wallet's private_key_hex / secret_key_base58 / mnemonic / public_key_hex.
    # Secrets are already PIN-gated at rest (decrypt_for_display needs session key);
    # this page just makes the reveal a deliberate, clearly-labelled Dev action.

    async def rawkey_reveal_click(wallet: dict, field: str, out_text: flet.Text) -> None:
        """Decrypt one secret field into the paired (initially hidden) Text control."""
        if not is_unlocked():
            out_text.value = "(app locked — unlock with PIN to reveal secrets)"
            out_text.color = flet.Colors.RED
            page.update()
            return
        dec = decrypt_for_display(wallet)
        val = dec.get(field)
        out_text.value = val if val else "(empty / not available)"
        out_text.color = flet.Colors.BLACK87
        page.update()

    async def rawkey_copy_click(wallet: dict, field: str) -> None:
        if not is_unlocked():
            return
        dec = decrypt_for_display(wallet)
        val = dec.get(field)
        if val:
            await page.clipboard.set(val)

    async def rawkey_enter() -> None:
        """(Re)build the Export raw keys page into `el_rawkey_page`."""
        el_rawkey_page.controls.clear()
        wallets = await get_storage_data(prefix="wallet.")
        wallets = [w for w in wallets if isinstance(w, dict) and w.get("address_base58")]
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
                                    rawkey_reveal_click(w, fld, o)
                                ),
                            ),
                            flet.OutlinedButton(
                                "Copy", icon=flet.Icons.COPY,
                                on_click=lambda ev, fld=field: asyncio.create_task(
                                    rawkey_copy_click(w, fld)
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

    raw_key_page = flet.View(
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
            el_rawkey_page,
        ],
    )

    wc_page = flet.View(
        route="wc-page",
        appbar=flet.AppBar(
            title=flet.Text("Connect dApp (WalletConnect v2)"),
            color="white",
            bgcolor="#8b5cf6",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text("Connect to a dApp", size=26, font_family="Georgia", weight=flet.FontWeight.BOLD),
            flet.Text(
                "1) Get a free projectId at cloud.walletconnect.com (one-time).\n"
                "2) Save it below. 3) Paste the 'wc:' URI a dApp shows you and Connect.",
                size=11,
                text_align=flet.TextAlign.CENTER,
            ),
            flet.Row(
                [wc_pid_input, flet.ElevatedButton("Save projectId", on_click=wc_save_pid_click)],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Divider(),
            flet.Column(
                [wc_uri_input,
                 flet.ElevatedButton("Connect", on_click=wc_connect_click, icon=flet.Icons.LINK, width=200)],
                horizontal_alignment=flet.CrossAxisAlignment.CENTER,
            ),
            wc_status_text,
            flet.Divider(),
            flet.Text("Active sessions:", size=16, weight=flet.FontWeight.BOLD),
            wc_sessions_list,
        ],
    )

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

    token_page = flet.View(
        route="token-page",
        appbar=flet.AppBar(
            title=flet.Text("Token Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Information:', size=30, font_family="Georgia"),
            el_token_page,
        ]
    )

    spl_token_page = flet.View(
        route="spl-token-page",
        appbar=flet.AppBar(
            title=flet.Text("SPL Token Transfer"),
            color="white",
            bgcolor="purple",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Transfer SPL Token', size=30, font_family="Georgia"),
            el_spl_token_page,
        ]
    )

    swap_page = flet.View(
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
            el_swap_page,
        ]
    )

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
