"""Headless tests for the extracted wallet create/recover/add module
(Phase 7 Group 6a).

Run:
    PYTHONPATH=src venv/bin/python tests/test_wallet_create_ui.py

Covers:
  * ctx.encrypt_for_storage — locked passthrough vs unlocked Fernet ciphertext
    (round-trip).
  * build_wallet_pages — returns 3 Views with the right routes + navbar wiring,
    and the form input TextFields are present in each view.
  * Save-handler storage path — ctx.encrypt_for_storage persists under a
    `wallet.<ts>` key and round-trips through Fernet.
  * Watch-only marker preserved through encrypt_for_storage (locked).

These tests assert on built control structure only — flet registers click
handlers in an internal registry so .on_click reads as None outside a live
session (see info/ui-testing-playbook.md §13).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

from ui.components import wallet_create
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
    """Minimal flet.Page surface for the wallet_create module."""

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


def make_ctx(page, unlocked=False, key=None):
    ctx = AppContext(page=page, session={})
    ctx.session["unlocked"] = unlocked
    ctx.session["key"] = key
    ctx.session["last_activity"] = 0.0
    ctx.session["lock_dialog"] = None
    ctx.controls["view_pop"] = lambda e: None
    ctx.controls["navbar"] = flet.NavigationBar()
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


def find_textfield(view, label):
    """Find a TextField by its `label` anywhere in a View's control tree."""
    found = []

    def walk(c):
        if isinstance(c, flet.TextField) and c.label == label:
            found.append(c)
        for child in getattr(c, "controls", []) or []:
            walk(child)
        content = getattr(c, "content", None)
        if content is None:
            return
        if isinstance(content, list):
            for sub in content:
                walk(sub)
        else:
            walk(content)

    for ctrl in view.controls:
        walk(ctrl)
    return found[0] if found else None


# ============================ ctx.encrypt_for_storage =======================

def test_encrypt_for_storage_locked_passthrough():
    page = MockPage()
    ctx = make_ctx(page, unlocked=False, key=None)
    record = {"name": "alice", "private_key_hex": "abc"}
    out = ctx.encrypt_for_storage(record)
    check("locked: passthrough identity", out is record)
    check("locked: no encrypted marker",
          WALLET_ENCRYPTED_FIELD not in out)


def test_encrypt_for_storage_unlocked_encrypts():
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    ctx = make_ctx(page, unlocked=True, key=key)
    record = {
        "name": "alice",
        "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "private_key_hex": "deadbeef",
        "public_key_hex": "feedface",
        "words": "word " * 12,
        "secret_key_base58": "x" * 88,
    }
    out = ctx.encrypt_for_storage(record)
    check("unlocked: not the same object", out is not record)
    check("unlocked: ciphertext marker present",
          out.get(WALLET_ENCRYPTED_FIELD) is True)
    check("unlocked: plaintext address preserved",
          out["address_base58"] == record["address_base58"])
    check("unlocked: plaintext name preserved",
          out["name"] == record["name"])
    # Secrets no longer readable as plaintext.
    check("unlocked: private_key_hex not plaintext",
          out.get("private_key_hex") != "deadbeef")
    # Round-trips through Fernet decrypt back to the original.
    restored = decrypt_wallet_secrets(out, key)
    check("unlocked: Fernet round-trip restores secrets",
          restored["private_key_hex"] == "deadbeef")


def test_encrypt_for_storage_preserves_watch_only_flag():
    """The add-address save handler writes WATCH_ONLY_FIELD before persistence;
    the field must survive ctx.encrypt_for_storage (it's not a secret)."""
    page = MockPage()
    ctx = make_ctx(page, unlocked=False, key=None)
    record = {
        "name": "watch",
        "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "private_key_hex": "",
        WATCH_ONLY_FIELD: True,
    }
    out = ctx.encrypt_for_storage(record)
    check("watch-only: flag preserved (locked passthrough)",
          out.get(WATCH_ONLY_FIELD) is True)

    # Also verify in the unlocked path.
    salt = make_salt()
    key = derive_key("1234", salt)
    ctx2 = make_ctx(MockPage(), unlocked=True, key=key)
    out2 = ctx2.encrypt_for_storage(record)
    check("watch-only: flag preserved (unlocked ciphertext)",
          out2.get(WATCH_ONLY_FIELD) is True)


# ============================ build_wallet_pages ===========================

def test_build_wallet_pages_structure():
    page = MockPage()
    ctx = make_ctx(page)
    create_p, recover_p, add_p = asyncio.run(wallet_create.build_wallet_pages(ctx))

    check("create route", create_p.route == "create-wallet-page")
    check("recover route", recover_p.route == "recover-wallet-page")
    check("add route", add_p.route == "add-wallet-address-page")

    for v in (create_p, recover_p, add_p):
        check(f"{v.route}: navigation_bar wired",
              v.navigation_bar is ctx.controls["navbar"])
        check(f"{v.route}: appbar present", v.appbar is not None)
        check(f"{v.route}: horizontal_alignment CENTER",
              v.horizontal_alignment == flet.CrossAxisAlignment.CENTER)
        check(f"{v.route}: scroll AUTO",
              v.scroll == flet.ScrollMode.AUTO)

    # Form inputs present in each page.
    check("create: Wallet Name input present",
          find_textfield(create_p, "Wallet Name") is not None)
    check("create: Wallet description input present",
          find_textfield(create_p, "Wallet description") is not None)
    check("recover: Wallet Name input present",
          find_textfield(recover_p, "Wallet Name") is not None)
    check("recover: Wallet description input present",
          find_textfield(recover_p, "Wallet description") is not None)
    check("recover: Secret input present",
          find_textfield(
              recover_p,
              "Wallet Secret Words (12/24) or Secret Key base58 (length=88)",
          ) is not None)
    check("add: Wallet Name input present",
          find_textfield(add_p, "Wallet Name") is not None)
    check("add: Wallet description input present",
          find_textfield(add_p, "Wallet description") is not None)
    check("add: Address input present",
          find_textfield(add_p, "Add Wallet Address (base58) ") is not None)


def test_build_wallet_pages_returns_distinct_objects():
    """The three pages must be distinct objects with distinct form fields so
    that user input on one page doesn't bleed into another."""
    page = MockPage()
    ctx = make_ctx(page)
    create_p, recover_p, add_p = asyncio.run(wallet_create.build_wallet_pages(ctx))
    check("three distinct views", len({id(create_p), id(recover_p), id(add_p)}) == 3)
    # Each page has its own Wallet Name field (no shared object).
    n_create = find_textfield(create_p, "Wallet Name")
    n_recover = find_textfield(recover_p, "Wallet Name")
    n_add = find_textfield(add_p, "Wallet Name")
    check("Wallet Name fields are distinct objects",
          len({id(n_create), id(n_recover), id(n_add)}) == 3)


# ===================== persistence path (save handler) =====================

def test_save_persistence_round_trip():
    """Simulate the create-save handler's storage write end-to-end: encrypt
    the record via ctx.encrypt_for_storage, persist under `wallet.<ts>`, then
    reload + decrypt and verify the secrets round-trip."""
    page = MockPage()
    salt = make_salt()
    key = derive_key("1234", salt)
    ctx = make_ctx(page, unlocked=True, key=key)

    record = {
        "name": "alice",
        "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "private_key_hex": "deadbeef",
        "public_key_hex": "feedface",
        "words": "word " * 12,
        "secret_key_base58": "x" * 88,
    }
    enc = ctx.encrypt_for_storage(record)
    asyncio.run(page.shared_preferences.set("wallet.test", json.dumps(enc)))

    raw = asyncio.run(page.shared_preferences.get("wallet.test"))
    loaded = json.loads(raw)
    keys = asyncio.run(page.shared_preferences.get_keys("wallet."))
    check("persisted under wallet.<key>", keys == ["wallet.test"])
    check("ciphertext marker on stored record",
          loaded.get(WALLET_ENCRYPTED_FIELD) is True)
    check("plaintext address on stored record",
          loaded["address_base58"] == record["address_base58"])
    restored = decrypt_wallet_secrets(loaded, key)
    check("Fernet round-trip after persistence",
          restored["private_key_hex"] == "deadbeef"
          and restored["words"] == record["words"]
          and restored["secret_key_base58"] == record["secret_key_base58"])


# ============================ main =========================================

def main():
    print("=== ctx.encrypt_for_storage ===")
    test_encrypt_for_storage_locked_passthrough()
    test_encrypt_for_storage_unlocked_encrypts()
    test_encrypt_for_storage_preserves_watch_only_flag()

    print("\n=== build_wallet_pages structure ===")
    test_build_wallet_pages_structure()
    test_build_wallet_pages_returns_distinct_objects()

    print("\n=== save / persist round-trip ===")
    test_save_persistence_round_trip()

    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
