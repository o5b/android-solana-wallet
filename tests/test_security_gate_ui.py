"""Headless tests for the extracted PIN gate module (Phase 7 Group 6c).

Run:
    PYTHONPATH=src venv/bin/python tests/test_security_gate_ui.py

Covers:
  * ctx.decrypt_for_display — locked passthrough vs unlocked Fernet round-trip.
  * load_pin / save_pin — round-trip + (None, None) when no PIN set.
  * migrate_plaintext_wallets — idempotent encryption of legacy records,
    skips already-encrypted and watch-only/empty records.
  * clear_client_storage — wipes every shared_preferences key.
  * close_lock_dialog — clears the in-memory handle + closes the dialog.
  * show_setup_dialog / show_unlock_dialog — modal structure, field presence,
    and session mutation when the embedded `confirm` / `do_unlock` handlers
    are driven directly (flet registers on_click in an internal registry so
    we cannot drive them via .on_click outside a live session — see
    info/ui-testing-playbook.md §13).
  * refresh_lock_state — picks setup vs unlock based on load_pin().
  * lock_app — drops the in-memory key and re-shows the unlock dialog.
  * auto_lock_watcher — locks after the inactivity threshold (short-timeout
    smoke; uses ctx.auto_lock_seconds=0 + manual loop break).

These tests assert on built control structure + storage side-effects only.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

from ui import security_gate as sg
from ui.context import AppContext
from solana.security import (
    WALLET_ENCRYPTED_FIELD,
    WATCH_ONLY_FIELD,
    make_salt,
    derive_key,
    decrypt_wallet_secrets,
)


# ============================ test harness ==================================

class MockSP:
    """shared_preferences stub — JSON-encodes values like Flet web mode."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    async def contains_key(self, k):
        return k in self._values

    async def get(self, k):
        return self._values.get(k)

    async def get_keys(self, prefix):
        return [k for k in self._values if k.startswith(prefix)]

    async def set(self, k, v):
        self._values[k] = v

    async def remove(self, k):
        self._values.pop(k, None)


class MockPage:
    """Minimal flet.Page surface for the security_gate module."""

    def __init__(self):
        self.shared_preferences = MockSP()
        self.update_calls = 0
        self.pushed_routes = []
        self.dialogs_shown = []

    def update(self):
        self.update_calls += 1

    async def push_route(self, route):
        self.pushed_routes.append(route)

    def show_dialog(self, dlg):
        self.dialogs_shown.append(dlg)


def make_ctx(page, unlocked=False, key=None, auto_lock_seconds=300):
    ctx = AppContext(
        page=page,
        session={},
        auto_lock_seconds=auto_lock_seconds,
    )
    ctx.session["unlocked"] = unlocked
    ctx.session["key"] = key
    ctx.session["last_activity"] = time.time()
    ctx.session["lock_dialog"] = None
    return ctx


_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def walk_controls(controls):
    """Yield every control in a list + recurse into .controls / .content."""
    for c in controls or []:
        yield c
        children = getattr(c, "controls", None)
        if children:
            yield from walk_controls(children)
        content = getattr(c, "content", None)
        if content is not None and not isinstance(content, list):
            yield from walk_controls([content])
        elif isinstance(content, list):
            yield from walk_controls(content)


def find_textfield(dlg, label):
    """Find a TextField by `label` inside an AlertDialog content."""
    found = list(walk_controls([dlg.content]))
    for c in found:
        if isinstance(c, flet.TextField) and c.label == label:
            return c
    return None


def find_button(dlg, text):
    """Find an ElevatedButton/TextButton whose label contains `text`."""
    for c in (dlg.actions or []):
        # flet 0.82.2: ElevatedButton("X") stores the label in `.content` as a
        # plain string (not a Text control); fall back to `.text` if present.
        label = ""
        content = getattr(c, "content", None)
        if isinstance(content, str):
            label = content
        elif isinstance(content, flet.Text):
            label = content.value or ""
        elif getattr(c, "text", None):
            label = c.text
        if text in str(label):
            return c
    return None


# ============================ ctx.decrypt_for_display ======================

def test_decrypt_for_display_locked_passthrough():
    page = MockPage()
    ctx = make_ctx(page, unlocked=False, key=None)
    wallet = {"name": "alice", "private_key_hex": "deadbeef"}
    out = ctx.decrypt_for_display(wallet)
    check("locked: passthrough identity", out is wallet)
    check("locked: secret still plaintext", out.get("private_key_hex") == "deadbeef")


def test_decrypt_for_display_unlocked_decrypts():
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    ctx = make_ctx(page, unlocked=True, key=key)
    # Build an encrypted record by encrypting a plaintext one.
    from solana.security import encrypt_wallet_secrets
    plain = {
        "name": "alice",
        "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "private_key_hex": "deadbeef",
        "public_key_hex": "feedface",
    }
    enc = encrypt_wallet_secrets(plain, key)
    out = ctx.decrypt_for_display(enc)
    check("unlocked: secrets decrypted", out.get("private_key_hex") == "deadbeef")
    check("unlocked: plaintext fields preserved", out.get("name") == "alice")


# ============================ load_pin / save_pin ==========================

def test_load_pin_no_pin_returns_none():
    page = MockPage()
    ctx = make_ctx(page)
    salt, verifier = asyncio.run(sg.load_pin(ctx))
    check("no PIN: salt None", salt is None)
    check("no PIN: verifier None", verifier is None)


def test_save_load_pin_roundtrip():
    page = MockPage()
    ctx = make_ctx(page)
    salt = make_salt()
    key = derive_key("1234", salt)
    verifier = "verifier-token"
    asyncio.run(sg.save_pin(ctx, salt, verifier))
    check("save: salt key set", "security.pin_salt" in page.shared_preferences._values)
    check("save: verifier key set", "security.pin_verifier" in page.shared_preferences._values)

    salt2, verifier2 = asyncio.run(sg.load_pin(ctx))
    check("load: salt round-trips", salt2 == salt)
    check("load: verifier round-trips", verifier2 == verifier)


def test_load_pin_corrupt_salt_returns_none():
    page = MockPage()
    page.shared_preferences._values["security.pin_salt"] = "not-base64-!!!"
    page.shared_preferences._values["security.pin_verifier"] = "v"
    ctx = make_ctx(page)
    salt, verifier = asyncio.run(sg.load_pin(ctx))
    check("corrupt salt: returns None pair", salt is None and verifier is None)


# ============================ migrate_plaintext_wallets ====================

def test_migrate_encrypts_plaintext_records():
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    ctx = make_ctx(page, unlocked=True, key=key)
    plain_wallet = {
        "name": "alice",
        "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "private_key_hex": "deadbeef",
        "public_key_hex": "feedface",
    }
    page.shared_preferences._values["wallet.1"] = json.dumps(plain_wallet)

    asyncio.run(sg.migrate_plaintext_wallets(ctx, key))

    raw = page.shared_preferences._values["wallet.1"]
    rec = json.loads(raw)
    check("migrate: encrypted marker set", rec.get(WALLET_ENCRYPTED_FIELD) is True)
    check("migrate: secret no longer plaintext",
          rec.get("private_key_hex") != "deadbeef")
    # Round-trips through Fernet decrypt.
    restored = decrypt_wallet_secrets(rec, key)
    check("migrate: Fernet round-trip", restored["private_key_hex"] == "deadbeef")


def test_migrate_skips_already_encrypted():
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    key_other = derive_key("5678", make_salt())
    ctx = make_ctx(page, unlocked=True, key=key)
    from solana.security import encrypt_wallet_secrets
    enc = encrypt_wallet_secrets(
        {"name": "x", "address_base58": "a", "private_key_hex": "secret"},
        key_other,
    )
    page.shared_preferences._values["wallet.1"] = json.dumps(enc)
    before = page.shared_preferences._values["wallet.1"]

    asyncio.run(sg.migrate_plaintext_wallets(ctx, key))

    after = page.shared_preferences._values["wallet.1"]
    check("skip: byte-identical (not re-encrypted)", before == after)


def test_migrate_handles_watch_only_and_junk():
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    ctx = make_ctx(page, unlocked=True, key=key)
    # watch-only wallet (no secrets to encrypt) — stored without secrets
    watch = {"name": "watch", "address_base58": "a", WATCH_ONLY_FIELD: True}
    page.shared_preferences._values["wallet.1"] = json.dumps(watch)
    # junk that isn't valid JSON
    page.shared_preferences._values["wallet.2"] = "not-json"
    # junk that isn't even a string
    page.shared_preferences._values["wallet.3"] = None
    # non-dict JSON
    page.shared_preferences._values["wallet.4"] = json.dumps([1, 2, 3])

    asyncio.run(sg.migrate_plaintext_wallets(ctx, key))

    check("watch-only: still present", "wallet.1" in page.shared_preferences._values)
    check("junk-not-json: not removed", "wallet.2" in page.shared_preferences._values)
    check("junk-non-str: not removed", "wallet.3" in page.shared_preferences._values)
    check("junk-non-dict: not removed", "wallet.4" in page.shared_preferences._values)


# ============================ clear_client_storage =========================

def test_clear_client_storage_wipes_all():
    page = MockPage()
    ctx = make_ctx(page)
    page.shared_preferences._values["wallet.1"] = "{}"
    page.shared_preferences._values["security.pin_salt"] = "x"
    page.shared_preferences._values["flutter.addressbook.contacts"] = "[]"
    page.shared_preferences._values["unrelated.key"] = "y"

    asyncio.run(sg.clear_client_storage(ctx))

    check("wipe: every key gone", len(page.shared_preferences._values) == 0)


# ============================ close_lock_dialog ============================

def test_close_lock_dialog_clears_handle():
    page = MockPage()
    ctx = make_ctx(page)
    dlg = flet.AlertDialog(title=flet.Text("x"))
    ctx.session["lock_dialog"] = dlg

    sg.close_lock_dialog(ctx)

    check("close: handle cleared", ctx.session["lock_dialog"] is None)
    check("close: dialog.open False", dlg.open is False)
    check("close: page.update called", page.update_calls >= 1)


def test_close_lock_dialog_no_dialog_noop():
    page = MockPage()
    ctx = make_ctx(page)
    ctx.session["lock_dialog"] = None
    sg.close_lock_dialog(ctx)
    check("noop: no update() call when nothing to close", page.update_calls == 0)


# ============================ show_setup_dialog ===========================

def test_show_setup_dialog_structure_and_confirm():
    page = MockPage()
    ctx = make_ctx(page)
    asyncio.run(sg.show_setup_dialog(ctx))

    check("setup: one dialog shown", len(page.dialogs_shown) == 1)
    dlg = page.dialogs_shown[0]
    check("setup: session.lock_dialog set", ctx.session["lock_dialog"] is dlg)
    check("setup: modal", dlg.modal is True)

    tf1 = find_textfield(dlg, "Create a PIN (4+ digits)")
    tf2 = find_textfield(dlg, "Confirm PIN")
    check("setup: PIN field present", tf1 is not None)
    check("setup: confirm field present", tf2 is not None)
    check("setup: PIN field is password", tf1.password is True)
    check("setup: autofocus on PIN", tf1.autofocus is True)

    set_btn = find_button(dlg, "Set PIN")
    check("setup: Set PIN action present", set_btn is not None)


def test_show_setup_confirm_persists_and_unlocks():
    """Drive the embedded `confirm` handler directly: PIN matches → unlock."""
    page = MockPage()
    ctx = make_ctx(page)
    asyncio.run(sg.show_setup_dialog(ctx))
    dlg = page.dialogs_shown[0]
    tf1 = find_textfield(dlg, "Create a PIN (4+ digits)")
    tf2 = find_textfield(dlg, "Confirm PIN")
    tf1.value = "1234"
    tf2.value = "1234"

    # flet stores the on_click handler in an internal registry — pull it from
    # the Set PIN button's action.
    set_btn = find_button(dlg, "Set PIN")
    confirm = set_btn.on_click
    check("setup: confirm handler reachable", confirm is not None)
    asyncio.run(confirm(None))

    check("confirm: salt persisted",
          "security.pin_salt" in page.shared_preferences._values)
    check("confirm: verifier persisted",
          "security.pin_verifier" in page.shared_preferences._values)
    check("confirm: unlocked", ctx.session["unlocked"] is True)
    check("confirm: key set", ctx.session["key"] is not None)
    check("confirm: lock_dialog cleared", ctx.session["lock_dialog"] is None)


def test_show_setup_confirm_rejects_mismatch():
    page = MockPage()
    ctx = make_ctx(page)
    asyncio.run(sg.show_setup_dialog(ctx))
    dlg = page.dialogs_shown[0]
    tf1 = find_textfield(dlg, "Create a PIN (4+ digits)")
    tf2 = find_textfield(dlg, "Confirm PIN")
    tf1.value = "1234"
    tf2.value = "5678"
    set_btn = find_button(dlg, "Set PIN")
    err_text = None
    for c in walk_controls([dlg.content]):
        if isinstance(c, flet.Text) and (c.value == "" or c.color == "red"):
            err_text = c
            break
    asyncio.run(set_btn.on_click(None))
    check("mismatch: still locked", ctx.session["unlocked"] is False)
    check("mismatch: error text set",
          err_text is not None and err_text.value == "PINs do not match.")


# ============================ show_unlock_dialog ==========================

def test_show_unlock_dialog_structure():
    page = MockPage()
    ctx = make_ctx(page)
    asyncio.run(sg.show_unlock_dialog(ctx))

    check("unlock: one dialog shown", len(page.dialogs_shown) == 1)
    dlg = page.dialogs_shown[0]
    check("unlock: modal", dlg.modal is True)
    tf = find_textfield(dlg, "Enter PIN")
    check("unlock: PIN field present", tf is not None)
    check("unlock: on_submit wired", tf.on_submit is not None)
    check("unlock: Unlock action present", find_button(dlg, "Unlock") is not None)
    check("unlock: Forgot PIN action present", find_button(dlg, "Forgot") is not None)


def test_show_unlock_do_unlock_correct_pin():
    page = MockPage()
    ctx = make_ctx(page)
    # Pre-set a PIN
    salt = make_salt()
    key = derive_key("1234", salt)
    from solana.security import make_verifier
    asyncio.run(sg.save_pin(ctx, salt, make_verifier(key)))

    asyncio.run(sg.show_unlock_dialog(ctx))
    dlg = page.dialogs_shown[0]
    tf = find_textfield(dlg, "Enter PIN")
    tf.value = "1234"
    unlock_btn = find_button(dlg, "Unlock")
    do_unlock = unlock_btn.on_click
    asyncio.run(do_unlock(None))

    check("unlock: unlocked", ctx.session["unlocked"] is True)
    check("unlock: key derived", ctx.session["key"] is not None)
    check("unlock: lock_dialog cleared", ctx.session["lock_dialog"] is None)


def test_show_unlock_do_unlock_wrong_pin():
    page = MockPage()
    ctx = make_ctx(page)
    salt = make_salt()
    key = derive_key("1234", salt)
    from solana.security import make_verifier
    asyncio.run(sg.save_pin(ctx, salt, make_verifier(key)))

    asyncio.run(sg.show_unlock_dialog(ctx))
    dlg = page.dialogs_shown[0]
    tf = find_textfield(dlg, "Enter PIN")
    tf.value = "9999"
    err_text = None
    for c in walk_controls([dlg.content]):
        if isinstance(c, flet.Text) and (c.value == "" or c.color == "red"):
            err_text = c
            break
    unlock_btn = find_button(dlg, "Unlock")
    asyncio.run(unlock_btn.on_click(None))

    check("wrong: still locked", ctx.session["unlocked"] is False)
    check("wrong: error text set",
          err_text is not None and err_text.value == "Incorrect PIN.")


# ============================ refresh_lock_state ==========================

def test_refresh_lock_state_no_pin_shows_setup():
    page = MockPage()
    ctx = make_ctx(page)
    asyncio.run(sg.refresh_lock_state(ctx))
    check("refresh: setup dialog shown", len(page.dialogs_shown) == 1)
    # Setup dialog's PIN field has a specific label
    dlg = page.dialogs_shown[0]
    check("refresh: it's the setup dialog",
          find_textfield(dlg, "Create a PIN (4+ digits)") is not None)


def test_refresh_lock_state_with_pin_shows_unlock():
    page = MockPage()
    ctx = make_ctx(page)
    salt = make_salt()
    key = derive_key("1234", salt)
    from solana.security import make_verifier
    asyncio.run(sg.save_pin(ctx, salt, make_verifier(key)))

    asyncio.run(sg.refresh_lock_state(ctx))
    check("refresh: unlock dialog shown", len(page.dialogs_shown) == 1)
    dlg = page.dialogs_shown[0]
    check("refresh: it's the unlock dialog",
          find_textfield(dlg, "Enter PIN") is not None)


# ============================ lock_app ====================================

def test_lock_app_drops_key_and_shows_unlock():
    page = MockPage()
    ctx = make_ctx(page, unlocked=True, key=derive_key("1234", make_salt()))
    # Pre-set a PIN so refresh_lock_state shows the unlock (not setup) dialog
    salt = make_salt()
    key = derive_key("1234", salt)
    from solana.security import make_verifier
    asyncio.run(sg.save_pin(ctx, salt, make_verifier(key)))

    asyncio.run(sg.lock_app(ctx))

    check("lock: unlocked False", ctx.session["unlocked"] is False)
    check("lock: key None", ctx.session["key"] is None)
    check("lock: unlock dialog shown",
          len(page.dialogs_shown) == 1
          and find_textfield(page.dialogs_shown[0], "Enter PIN") is not None)


# ============================ auto_lock_watcher ===========================

def test_auto_lock_watcher_locks_after_inactivity():
    """Single-iteration smoke: when threshold is exceeded, watcher locks."""
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    from solana.security import make_verifier
    ctx = make_ctx(page, unlocked=True, key=key, auto_lock_seconds=0)
    ctx.session["last_activity"] = 0.0  # long ago → exceeds 0-second threshold
    # Pre-set a PIN so lock_app's refresh_lock_state shows unlock dialog
    asyncio.run(sg.save_pin(ctx, salt, make_verifier(key)))

    async def run_one_iter():
        # Monkey-patch asyncio.sleep inside the module so the first iteration
        # runs immediately, then break the loop on the second sleep.
        called = {"n": 0}

        async def fake_sleep(_):
            called["n"] += 1
            if called["n"] >= 2:
                raise asyncio.CancelledError()

        sg.asyncio.sleep = fake_sleep
        try:
            await sg.auto_lock_watcher(ctx)
        except asyncio.CancelledError:
            pass
        finally:
            import asyncio as _aio
            sg.asyncio.sleep = _aio.sleep

    asyncio.run(run_one_iter())

    check("watcher: locked after threshold", ctx.session["unlocked"] is False)
    check("watcher: key dropped", ctx.session["key"] is None)


def test_auto_lock_watcher_skips_when_dialog_open():
    """If a lock dialog is already shown, the watcher must not stack another."""
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    from solana.security import make_verifier
    ctx = make_ctx(page, unlocked=True, key=key, auto_lock_seconds=0)
    ctx.session["last_activity"] = 0.0
    asyncio.run(sg.save_pin(ctx, salt, make_verifier(key)))
    # Simulate an already-open dialog
    ctx.session["lock_dialog"] = flet.AlertDialog(title=flet.Text("open"))

    async def run_one_iter():
        called = {"n": 0}

        async def fake_sleep(_):
            called["n"] += 1
            if called["n"] >= 2:
                raise asyncio.CancelledError()

        sg.asyncio.sleep = fake_sleep
        try:
            await sg.auto_lock_watcher(ctx)
        except asyncio.CancelledError:
            pass
        finally:
            import asyncio as _aio
            sg.asyncio.sleep = _aio.sleep

    asyncio.run(run_one_iter())

    check("watcher: stays unlocked (dialog open)", ctx.session["unlocked"] is True)
    check("watcher: no new dialog pushed", len(page.dialogs_shown) == 0)


# ============================ main ========================================

def main():
    tests = [
        test_decrypt_for_display_locked_passthrough,
        test_decrypt_for_display_unlocked_decrypts,
        test_load_pin_no_pin_returns_none,
        test_save_load_pin_roundtrip,
        test_load_pin_corrupt_salt_returns_none,
        test_migrate_encrypts_plaintext_records,
        test_migrate_skips_already_encrypted,
        test_migrate_handles_watch_only_and_junk,
        test_clear_client_storage_wipes_all,
        test_close_lock_dialog_clears_handle,
        test_close_lock_dialog_no_dialog_noop,
        test_show_setup_dialog_structure_and_confirm,
        test_show_setup_confirm_persists_and_unlocks,
        test_show_setup_confirm_rejects_mismatch,
        test_show_unlock_dialog_structure,
        test_show_unlock_do_unlock_correct_pin,
        test_show_unlock_do_unlock_wrong_pin,
        test_refresh_lock_state_no_pin_shows_setup,
        test_refresh_lock_state_with_pin_shows_unlock,
        test_lock_app_drops_key_and_shows_unlock,
        test_auto_lock_watcher_locks_after_inactivity,
        test_auto_lock_watcher_skips_when_dialog_open,
    ]
    for fn in tests:
        print(f"\n— {fn.__name__}")
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
