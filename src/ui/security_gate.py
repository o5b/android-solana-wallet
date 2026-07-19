"""PIN gate, encrypted-secret migration and auto-lock (Phase 7 Group 6c).

Lifted out of ``main()``'s security block. Owns the modal PIN-setup /
unlock dialogs, the legacy plaintext-wallet migration, the inactivity
auto-lock watcher, and the destructive "Clear ALL local storage" wipe
(shared with the More hub). All functions take an :class:`AppContext`
as their first arg and mutate ``ctx.session`` / ``ctx.page.shared_preferences``
in place — there is no module-level mutable state, so web-mode's one
``main()`` per connected client stays correctly isolated.

The dialogs intentionally use ``ctx.page.update()`` (not ``safe_update``)
to mirror the legacy behaviour: the PIN gate must reliably render and a
silent swallow would hide a real bootstrap defect.

Security invariants preserved
-----------------------------
* The PIN is *never* persisted — only the scrypt ``salt`` and the
  encrypted ``verifier`` token are stored under ``ctx.pin_salt_key`` /
  ``ctx.pin_verifier_key``.
* ``session["key"]`` (the Fernet key derived from the PIN) lives only in
  memory and is dropped on ``lock_app`` / wipe.
* ``migrate_plaintext_wallets`` is idempotent (skips records already
  carrying the ``WALLET_ENCRYPTED_FIELD`` marker) and runs both after a
  first PIN setup and defensively on every successful unlock.
"""

import asyncio
import json
import time

import flet

from solana.security import (
    WALLET_ENCRYPTED_FIELD,
    MIN_PIN_LENGTH,
    make_salt,
    derive_key,
    make_verifier,
    verify_pin,
    validate_pin,
    encode_salt,
    decode_salt,
    encrypt_wallet_secrets,
)


# ============================ PIN storage ==================================

async def load_pin(ctx):
    """Return ``(salt_bytes, verifier_str)`` or ``(None, None)`` if no PIN set."""
    page = ctx.page
    if not await page.shared_preferences.contains_key(ctx.pin_salt_key):
        return None, None
    salt_str = await page.shared_preferences.get(ctx.pin_salt_key)
    verifier = await page.shared_preferences.get(ctx.pin_verifier_key)
    if not salt_str or not verifier:
        return None, None
    try:
        return decode_salt(salt_str), verifier
    except Exception:
        return None, None


async def save_pin(ctx, salt: bytes, verifier: str):
    """Persist the scrypt salt + encrypted verifier (never the PIN)."""
    page = ctx.page
    await page.shared_preferences.set(ctx.pin_salt_key, encode_salt(salt))
    await page.shared_preferences.set(ctx.pin_verifier_key, verifier)


async def migrate_plaintext_wallets(ctx, key: bytes):
    """Encrypt the secrets of every legacy (plaintext) wallet record.

    Called once after a PIN is first set up and defensively on every
    successful unlock. Already-encrypted records and watch-only wallets
    (empty secrets) are handled gracefully by ``encrypt_wallet_secrets``.
    """
    page = ctx.page
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


async def clear_client_storage(ctx):
    """Wipe ALL ``shared_preferences`` keys (wallets, PIN, contacts, WC pairing).

    Destructive / irreversible — used by the "Forgot PIN?" reset flow and
    by the More hub's "Clear all storage" button.
    """
    page = ctx.page
    keys = await page.shared_preferences.get_keys("")
    for key in keys:
        await page.shared_preferences.remove(key)


# ============================ Dialog primitives ============================

def close_lock_dialog(ctx):
    """Dismiss the currently-shown PIN/setup dialog (if any)."""
    session = ctx.session
    if session["lock_dialog"] is not None:
        session["lock_dialog"].open = False
        session["lock_dialog"] = None
        ctx.page.update()


async def lock_app(ctx):
    """Drop the in-memory Fernet key and require the PIN again."""
    session = ctx.session
    session["unlocked"] = False
    session["key"] = None
    await refresh_lock_state(ctx)


async def show_setup_dialog(ctx):
    """First-run modal: force the user to create a PIN."""
    page = ctx.page
    session = ctx.session
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
            page.update()
            return
        if p1 != p2:
            err.value = "PINs do not match."
            page.update()
            return
        salt = make_salt()
        key = derive_key(p1, salt)
        verifier = make_verifier(key)
        await save_pin(ctx, salt, verifier)
        await migrate_plaintext_wallets(ctx, key)
        session["unlocked"] = True
        session["key"] = key
        ctx.reset_activity()
        close_lock_dialog(ctx)

    dlg = flet.AlertDialog(
        modal=True,
        title=flet.Text("Set up a PIN"),
        content=flet.Column(
            [
                flet.Text(
                    "This PIN encrypts your private keys at rest and unlocks the app. "
                    "Do not forget it: lost PINs cannot be recovered.",
                    size=12,
                ),
                tf1, tf2, err,
            ],
            tight=True,
        ),
        actions=[flet.ElevatedButton("Set PIN", on_click=confirm)],
        actions_alignment=flet.MainAxisAlignment.END,
    )
    session["lock_dialog"] = dlg
    page.show_dialog(dlg)


async def show_unlock_dialog(ctx):
    """Subsequent-run modal: enter the PIN to derive the in-memory key."""
    page = ctx.page
    session = ctx.session
    tf = flet.TextField(
        label="Enter PIN", password=True, can_reveal_password=True,
        keyboard_type=flet.KeyboardType.NUMBER, autofocus=True,
    )
    err = flet.Text("", color="red")

    async def do_unlock(ev):
        salt, verifier = await load_pin(ctx)
        pin = tf.value or ""
        if salt is not None and verify_pin(pin, salt, verifier):
            session["unlocked"] = True
            session["key"] = derive_key(pin, salt)
            # Defensive: encrypt any wallet that slipped through while locked.
            await migrate_plaintext_wallets(ctx, session["key"])
            ctx.reset_activity()
            tf.value = ""
            err.value = ""
            close_lock_dialog(ctx)
        else:
            err.value = "Incorrect PIN."
            page.update()

    async def forgot_pin(ev):
        # Losing the PIN means the encrypted secrets are unrecoverable.
        # Offer a destructive reset that wipes the whole wallet store.
        async def do_wipe(inner):
            await clear_client_storage(ctx)
            session["unlocked"] = False
            session["key"] = None
            session["lock_dialog"] = None
            confirm_dlg.open = False
            page.update()
            await refresh_lock_state(ctx)  # PIN wiped -> shows the setup dialog

        async def cancel(inner):
            confirm_dlg.open = False
            page.update()
            await show_unlock_dialog(ctx)  # re-show the unlock dialog

        confirm_dlg = flet.AlertDialog(
            modal=True,
            title=flet.Text("Reset everything?"),
            content=flet.Text(
                "This will permanently delete the PIN and ALL stored wallets "
                "(their encrypted keys become unrecoverable). Only continue "
                "if you have your seed phrases backed up."
            ),
            actions=[
                flet.TextButton("Cancel", on_click=cancel),
                flet.ElevatedButton(
                    "Reset & Wipe", on_click=do_wipe, icon=flet.Icons.DELETE_FOREVER
                ),
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


async def refresh_lock_state(ctx):
    """Show the setup dialog (first run) or unlock dialog (subsequent runs)."""
    salt, _ = await load_pin(ctx)
    if salt is None:
        await show_setup_dialog(ctx)
    else:
        await show_unlock_dialog(ctx)


async def auto_lock_watcher(ctx):
    """Periodically lock the app after ``ctx.auto_lock_seconds`` of inactivity."""
    session = ctx.session
    while True:
        await asyncio.sleep(10)
        if (
            session["lock_dialog"] is None
            and ctx.is_unlocked()
            and (time.time() - session["last_activity"]) > ctx.auto_lock_seconds
        ):
            await lock_app(ctx)
