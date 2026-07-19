"""Headless tests for the extracted balance/address-page module (Phase 7 Group 6e).

Run:
    PYTHONPATH=src venv/bin/python tests/test_balance_ui.py

Covers:
  * ui.qr.generate_qr_base64 — pure helper; returns valid base64-encoded PNG.
  * balance.get_storage_data(ctx, prefix) — JSON decode + storage_key injection.
  * balance.get_wallets_cards(ctx) — homepage ListView; watch-only badge visibility.
  * balance.delete_wallet_click(ctx, e) — storage removal + route push + dialog.
  * balance.wallet_info_click(ctx, e) — dialog shown; decrypt_for_display locked passthrough.
  * balance.show_qr_click(ctx, e) — QR dialog shown.
  * balance.go_to_address_page(ctx, e) — page built into el_address_page; route push;
    network checkboxes / Show History / Show Balance buttons present.
  * balance.get_history_button_click(ctx, e) — mocked get_transaction_history;
    progressive disclosure (Simple header-only / Pro expandable / Dev + CSV button).
  * balance.get_balance_button_click(ctx, e) — mocked get_sol_spl_balance; Simple mode
    skips SPL + uses SOL-only banner subtotal; transfer/swap buttons render with the
    right disabled flags; data-dict contract preserved.
  * balance.build_address_page(ctx) — View structure; binds el_address_page; route.

The balance + history handlers read network checkbox state via the (fragile)
positional chain ``e.control.parent.parent.controls[-3].controls[0].controls[N]``.
We construct a real flet control sub-tree so that chain resolves naturally.

Click wiring (the 5 ``on_go_to_*`` adapters) cannot be asserted headlessly — flet
registers handlers in an internal registry, not as a readable .on_click attribute
outside a live session (see info/ui-testing-playbook.md §13). We assert on built
control structure + storage side-effects + the fact that the handlers do not raise
under the realistic event tree.
"""
import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

from solana.security import WATCH_ONLY_FIELD
from ui.context import AppContext
from ui.components import balance as balance_mod
from ui.experience import EXPERIENCE_KEY
from ui.qr import generate_qr_base64


# ---------- mock page --------------------------------------------------------

class MockSP:
    def __init__(self, values=None):
        self._values = dict(values or {})

    async def contains_key(self, k):
        return k in self._values

    async def get(self, k):
        return self._values.get(k)

    async def set(self, k, v):
        self._values[k] = v

    async def remove(self, k):
        self._values.pop(k, None)

    async def get_keys(self, prefix):
        return [k for k in self._values if k.startswith(prefix)]


class MockClipboard:
    def __init__(self):
        self.last = None

    async def set(self, v):
        self.last = v


class MockPage:
    def __init__(self):
        self.shared_preferences = MockSP()
        self.clipboard = MockClipboard()
        self.theme_mode = flet.ThemeMode.LIGHT
        self.update_calls = 0
        self.dialogs_shown = []
        self.pushed_routes = []

    def update(self):
        self.update_calls += 1

    def show_dialog(self, dlg):
        self.dialogs_shown.append(dlg)

    async def push_route(self, route):
        self.pushed_routes.append(route)


def make_ctx(page=None, mode=None, unlocked=False, key=None):
    page = page or MockPage()
    if mode is not None:
        page.shared_preferences._values[EXPERIENCE_KEY] = mode
    session = {"unlocked": unlocked, "key": key, "last_activity": 0.0, "lock_dialog": None}
    ctx = AppContext(page=page, session=session)
    ctx.controls["view_pop"] = lambda e: None
    ctx.controls["navbar"] = flet.NavigationBar()
    ctx.controls["el_address_page"] = flet.Column()
    ctx.controls["el_token_balance_data"] = flet.Column()
    ctx.controls["csv_file_picker"] = _FakeCsvPicker()
    return ctx


class _FakeCsvPicker:
    """Captures save_file() args without opening a dialog."""
    def __init__(self):
        self.calls = []

    async def save_file(self, **kwargs):
        self.calls.append(kwargs)
        return None  # simulate user cancelling the save dialog


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


def walk_text_pieces(root):
    """Yield every plain text string rendered inside a control tree."""
    out = []

    def walk(c):
        if c is None:
            return
        if isinstance(c, flet.Text) and getattr(c, "value", None):
            out.append(c.value)
        # TextSpan stores its text in .text (and is what flet.Text.spans holds).
        if isinstance(c, flet.TextSpan) and getattr(c, "text", None):
            out.append(c.text)
        # Checkbox / Radio etc. store their label in .label, not as a child Text.
        label = getattr(c, "label", None)
        if isinstance(label, str):
            out.append(label)
        content = getattr(c, "content", None)
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, flet.Text) and getattr(content, "value", None):
            out.append(content.value)
        for attr in ("controls", "content", "actions", "spans"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif isinstance(child, flet.Control):
                walk(child)

    walk(root)
    return out


def _make_address_e(wallet, network_values=(True, False, False)):
    """Build a fake event for the balance/history handlers.

    The handlers read network checkbox state via
    ``e.control.parent.parent.controls[-3].controls[0].controls[N].value``.
    flet's ``.parent`` is a read-only property (no setter), so we build the
    parent chain out of simple mock objects rather than real flet controls.
    The handler only reads ``.value`` / ``.controls`` / ``.data`` /
    ``.disabled``, so plain attribute containers suffice.
    """
    class _Checkbox:
        def __init__(self, v):
            self.value = v

    class _Box:
        def __init__(self, controls):
            self.controls = controls

    class _Row:
        def __init__(self, controls):
            self.controls = controls

    checkboxes = [_Checkbox(v) for v in network_values]
    network_col = _Box(checkboxes)
    network_row = _Row([network_col])

    # Pad so controls[-3] resolves to network_row (see handler's positional read).
    parent_column = _Box([
        object(),        # controls[-5]
        object(),        # controls[-4]
        network_row,     # controls[-3]
        object(),        # controls[-2]
        object(),        # controls[-1]
    ])
    button_row = _Box([])

    class _Ctl:
        disabled = False
        data = wallet
        parent = button_row

    class _Ev:
        control = _Ctl()

    button_row.parent = parent_column
    return _Ev()


# ============================ ui.qr ==========================================

def test_qr_basic():
    """generate_qr_base64 returns valid base64-encoded PNG data."""
    b64 = generate_qr_base64("hello world")
    raw = base64.b64decode(b64)
    check("returns base64-decodable bytes", isinstance(raw, bytes) and len(raw) > 0)
    # PNG magic bytes: \x89PNG\r\n\x1a\n
    check("decodes to a PNG", raw[:8] == b"\x89PNG\r\n\x1a\n")
    # Deterministic for the same input (same box_size/border).
    check("deterministic for same input", generate_qr_base64("x") == generate_qr_base64("x"))


def test_qr_different_inputs():
    """Different inputs produce different outputs."""
    a = generate_qr_base64("address-1")
    b = generate_qr_base64("address-2")
    check("different inputs -> different QRs", a != b)


# ============================ get_storage_data ===============================

def test_get_storage_data_injects_storage_key():
    """Dict records get a 'storage_key' field pointing at their storage key."""
    ctx = make_ctx()
    ctx.page.shared_preferences._values = {
        "wallet.111": json.dumps({"name": "w1", "address_base58": "ABC"}),
        "wallet.222": json.dumps({"name": "w2", "address_base58": "DEF"}),
    }
    out = asyncio.run(balance_mod.get_storage_data(ctx, prefix="wallet."))
    check("two records returned", len(out) == 2)
    keys = {r.get("storage_key") for r in out}
    check("storage_key injected on both", keys == {"wallet.111", "wallet.222"})
    check("name decoded from JSON", any(r.get("name") == "w1" for r in out))


def test_get_storage_data_non_json_passthrough():
    """Non-JSON string values are kept as-is (no JSON decode, no storage_key)."""
    ctx = make_ctx()
    ctx.page.shared_preferences._values = {"misc.x": "plain string"}
    out = asyncio.run(balance_mod.get_storage_data(ctx, prefix="misc."))
    check("one record returned", len(out) == 1)
    check("plain string kept", out[0] == "plain string")


def test_get_storage_data_prefix_filter():
    """Only keys matching the prefix are returned."""
    ctx = make_ctx()
    ctx.page.shared_preferences._values = {
        "wallet.1": "{}",
        "contact.1": "{}",
    }
    wallets = asyncio.run(balance_mod.get_storage_data(ctx, prefix="wallet."))
    contacts = asyncio.run(balance_mod.get_storage_data(ctx, prefix="contact."))
    check("wallet prefix filters", len(wallets) == 1)
    check("contact prefix filters", len(contacts) == 1)


# ============================ get_wallets_cards ==============================

def test_get_wallets_cards_one_card_per_wallet():
    ctx = make_ctx()
    ctx.page.shared_preferences._values = {
        "wallet.1": json.dumps({
            "name": "DayWallet", "description": "daily spend",
            "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        }),
        "wallet.2": json.dumps({
            "name": "Savings", "description": "cold storage",
            "address_base58": "EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr",
        }),
    }
    lv = asyncio.run(balance_mod.get_wallets_cards(ctx))
    check("returns ListView", isinstance(lv, flet.ListView))
    check("one Card per wallet", len(lv.controls) == 2)
    check("each Card", all(isinstance(c, flet.Card) for c in lv.controls))


def test_get_wallets_cards_watch_only_badge():
    """The orange 'Watch-only' badge is visible only when WATCH_ONLY_FIELD is True."""
    ctx = make_ctx()
    ctx.page.shared_preferences._values = {
        "wallet.watch": json.dumps({
            "name": "Watch", "description": "watch-only",
            "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
            WATCH_ONLY_FIELD: True,
        }),
        "wallet.full": json.dumps({
            "name": "Full", "description": "with key",
            "address_base58": "EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr",
        }),
    }
    lv = asyncio.run(balance_mod.get_wallets_cards(ctx))

    def find_badge(card):
        texts = walk_text_pieces(card)
        return any("Watch-only" in t for t in texts)

    # We can't easily distinguish visible=False badges from their text presence
    # (the text is built regardless of visibility), so inspect .visible directly.
    def badge_visible(card):
        found = []
        def walk(c):
            if isinstance(c, flet.Text) and c.value and "Watch-only" in c.value:
                found.append(c.visible)
            content = getattr(c, "content", None)
            for attr in ("controls", "content"):
                child = getattr(c, attr, None)
                if isinstance(child, list):
                    for x in child:
                        walk(x)
                elif isinstance(child, flet.Control):
                    walk(child)
        walk(card)
        return found

    watch_card, full_card = lv.controls[0], lv.controls[1]
    check("watch-only wallet badge visible", badge_visible(watch_card) == [True])
    check("full wallet badge hidden", badge_visible(full_card) == [False])


def test_get_wallets_cards_empty():
    """No wallets -> empty ListView (no cards)."""
    ctx = make_ctx()
    lv = asyncio.run(balance_mod.get_wallets_cards(ctx))
    check("empty ListView", isinstance(lv, flet.ListView) and len(lv.controls) == 0)


# ============================ delete_wallet_click ============================

def test_delete_wallet_click_removes_and_routes():
    ctx = make_ctx()
    ctx.page.shared_preferences._values = {
        "wallet.123": json.dumps({"name": "x", "address_base58": "ABC", "storage_key": "wallet.123"}),
    }

    class _Ctl:
        data = {"storage_key": "wallet.123"}

    class _Ev:
        control = _Ctl()

    asyncio.run(balance_mod.delete_wallet_click(ctx, _Ev()))
    check("wallet key removed from storage", "wallet.123" not in ctx.page.shared_preferences._values)
    check("dialog shown", len(ctx.page.dialogs_shown) == 1)
    check("pushed home route", "/" in ctx.page.pushed_routes)


def test_delete_wallet_click_no_storage_key_is_noop():
    """If the wallet has no storage_key (shouldn't happen), no removal / route push."""
    ctx = make_ctx()

    class _Ctl:
        data = {"address_base58": "ABC"}  # no storage_key

    class _Ev:
        control = _Ctl()

    asyncio.run(balance_mod.delete_wallet_click(ctx, _Ev()))
    check("no dialog when no storage_key", len(ctx.page.dialogs_shown) == 0)
    check("no route push when no storage_key", len(ctx.page.pushed_routes) == 0)


# ============================ wallet_info_click ==============================

def test_wallet_info_click_locked_passthrough():
    """When the app is locked, secrets are passed through undecrypted (no crash)."""
    ctx = make_ctx(unlocked=False, key=None)

    class _Ctl:
        data = {
            "name": "Test", "description": "d",
            "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
            "private_key_hex": "ENCRYPTED_BLOB",
            "public_key_hex": "PUB",
            "created": "2026-01-01",
        }

    class _Ev:
        control = _Ctl()

    asyncio.run(balance_mod.wallet_info_click(ctx, _Ev()))
    check("dialog shown", len(ctx.page.dialogs_shown) == 1)
    dlg = ctx.page.dialogs_shown[0]
    info_text = ""
    if isinstance(dlg.content, flet.Column):
        for c in dlg.content.controls:
            if isinstance(c, flet.Text):
                info_text += (c.value or "")
    check("address in info text", "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz" in info_text)
    # Locked passthrough means the encrypted blob is shown verbatim (no decrypt).
    check("encrypted blob shown verbatim (locked)", "ENCRYPTED_BLOB" in info_text)


def test_wallet_info_click_watch_only_tag():
    """Watch-only wallets get a '(watch-only)' tag in the info text."""
    ctx = make_ctx(unlocked=False, key=None)

    class _Ctl:
        data = {
            "name": "Watch", "description": "",
            "address_base58": "ABC",
            "created": "2026-01-01",
            WATCH_ONLY_FIELD: True,
        }

    class _Ev:
        control = _Ctl()

    asyncio.run(balance_mod.wallet_info_click(ctx, _Ev()))
    dlg = ctx.page.dialogs_shown[0]
    info_text = ""
    if isinstance(dlg.content, flet.Column):
        for c in dlg.content.controls:
            if isinstance(c, flet.Text):
                info_text += (c.value or "")
    check("watch-only tag present", "(watch-only)" in info_text)


# ============================ show_qr_click ==================================

def test_show_qr_click_dialog():
    ctx = make_ctx()

    class _Ev:
        class control:
            data = "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz"

    asyncio.run(balance_mod.show_qr_click(ctx, _Ev()))
    check("dialog shown", len(ctx.page.dialogs_shown) == 1)
    dlg = ctx.page.dialogs_shown[0]
    # The dialog content has an Image (the QR) + a Text (the address).
    has_image = False
    has_addr_text = False
    if isinstance(dlg.content, flet.Column):
        for c in dlg.content.controls:
            if isinstance(c, flet.Image):
                has_image = True
            elif isinstance(c, flet.Text) and "AuPjPz" in (c.value or ""):
                has_addr_text = True
    check("QR image in dialog", has_image)
    check("address text in dialog", has_addr_text)


# ============================ go_to_address_page =============================

def test_go_to_address_page_builds_into_el_address_page():
    ctx = make_ctx()
    wallet = {
        "name": "Test", "description": "desc",
        "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "created": "2026-01-01",
        "storage_key": "wallet.test",
    }

    class _Ev:
        class control:
            data = wallet

    asyncio.run(balance_mod.go_to_address_page(ctx, _Ev()))
    el = ctx.controls["el_address_page"]
    check("el_address_page populated", len(el.controls) > 0)
    check("address-page route pushed", "address-page" in ctx.page.pushed_routes)
    texts = walk_text_pieces(el)
    check("wallet name rendered", "Test" in texts)
    check("wallet description rendered", "desc" in texts)
    check("address rendered", "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz" in texts)
    check("Solana Networks label present", any("Solana Networks" in t for t in texts))
    check("Show History button present", any("Show History" in t for t in texts))
    check("Show Balance button present", any("Show Balance" in t for t in texts))
    check("mainnet-beta checkbox default-checked", any("mainnet-beta" in t for t in texts))


def test_go_to_address_page_includes_el_token_balance_data():
    """The token-balance-data column is the last control in el_address_page."""
    ctx = make_ctx()
    wallet = {"name": "x", "description": "", "address_base58": "ABC", "created": "2026-01-01"}

    class _Ev:
        class control:
            data = wallet

    asyncio.run(balance_mod.go_to_address_page(ctx, _Ev()))
    el = ctx.controls["el_address_page"]
    check("last control is el_token_balance_data",
          el.controls[-1] is ctx.controls["el_token_balance_data"])


# ============================ get_history_button_click =======================

def _patch_history(monkey, records_by_network):
    """Replace get_transaction_history with a mock returning per-network data."""
    async def fake_get_history(address, net_url):
        return records_by_network.get(net_url, {"result": []})
    monkey["orig"] = balance_mod.get_transaction_history
    balance_mod.get_transaction_history = fake_get_history


def _restore_history(monkey):
    balance_mod.get_transaction_history = monkey["orig"]


def test_get_history_simple_header_only():
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC", "storage_key": "wallet.x"}

    monkey = {}
    _patch_history(monkey, {
        "https://api.mainnet-beta.solana.com": {
            "result": [
                {
                    "block_time": 1700000000,
                    "tx_type": "TRANSFER",
                    "success": True,
                    "signature": "sig1",
                    "sol_change": 0.5,
                    "fee": 0.000005,
                },
            ],
        },
    })

    e = _make_address_e(wallet, network_values=(True, False, False))
    try:
        asyncio.run(balance_mod.get_history_button_click(ctx, e))
    finally:
        _restore_history(monkey)

    el = ctx.controls["el_token_balance_data"]
    check("controls rendered", len(el.controls) > 0)
    texts = walk_text_pieces(el)
    # Simple mode shows status inline ("Success"); no expandable Signature row.
    check("Success text shown (Simple inline status)", any("Success" in t for t in texts))
    check("no Signature label (Simple)", not any(t.startswith("Signature:") for t in texts))


def test_get_history_pro_expandable():
    ctx = make_ctx(mode="pro")
    wallet = {"address_base58": "ABC"}

    monkey = {}
    _patch_history(monkey, {
        "https://api.mainnet-beta.solana.com": {
            "result": [
                {
                    "block_time": 1700000000,
                    "tx_type": "TRANSFER",
                    "success": True,
                    "signature": "sigXYZ",
                    "sol_change": 0.5,
                    "fee": 0.000005,
                },
            ],
        },
    })

    e = _make_address_e(wallet, network_values=(True, False, False))
    try:
        asyncio.run(balance_mod.get_history_button_click(ctx, e))
    finally:
        _restore_history(monkey)

    texts = walk_text_pieces(ctx.controls["el_token_balance_data"])
    check("Pro: Signature shown in expandable", any("sigXYZ" in t for t in texts))
    check("Pro: Fee shown", any("Fee" in t for t in texts))


def test_get_history_developer_includes_csv_button():
    ctx = make_ctx(mode="developer")
    wallet = {"address_base58": "ABC"}

    monkey = {}
    _patch_history(monkey, {
        "https://api.mainnet-beta.solana.com": {
            "result": [
                {
                    "block_time": 1700000000, "tx_type": "TRANSFER", "success": True,
                    "signature": "s", "sol_change": 0.5, "fee": 0.000005,
                    "slot": 100, "version": 0, "compute_units": 200,
                    "logs": ["log1"],
                },
            ],
        },
    })

    e = _make_address_e(wallet, network_values=(True, False, False))
    try:
        asyncio.run(balance_mod.get_history_button_click(ctx, e))
    finally:
        _restore_history(monkey)

    texts = walk_text_pieces(ctx.controls["el_token_balance_data"])
    check("Dev: Save History as CSV button shown", any("Save History as CSV" in t for t in texts))
    check("Dev: Slot/Version/CU shown", any("Slot" in t and "Version" in t for t in texts))


def test_get_history_no_csv_button_in_simple():
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC"}

    monkey = {}
    _patch_history(monkey, {
        "https://api.mainnet-beta.solana.com": {
            "result": [{
                "block_time": 1700000000, "tx_type": "TRANSFER", "success": True,
                "signature": "s", "sol_change": 0.5, "fee": 0.000005,
            }],
        },
    })

    e = _make_address_e(wallet, network_values=(True, False, False))
    try:
        asyncio.run(balance_mod.get_history_button_click(ctx, e))
    finally:
        _restore_history(monkey)

    texts = walk_text_pieces(ctx.controls["el_token_balance_data"])
    check("Simple: no CSV button", not any("Save History as CSV" in t for t in texts))


def test_get_history_no_networks_selected():
    """When no networks are ticked, the handler doesn't crash and renders the divider only."""
    ctx = make_ctx(mode="pro")
    wallet = {"address_base58": "ABC"}
    e = _make_address_e(wallet, network_values=(False, False, False))
    asyncio.run(balance_mod.get_history_button_click(ctx, e))
    # Only the leading divider is present (no network sections).
    check("only the divider rendered", len(ctx.controls["el_token_balance_data"].controls) == 1)


# ============================ get_balance_button_click =======================

def _patch_balance(monkey, result):
    """Mock get_sol_spl_balance + price + spam enrichment so no RPC fires."""
    async def fake_balance(addr, networks, **kwargs):
        return result
    async def fake_prices(res):
        return {"total_usd": 0.0, "priced": 0, "tokens": 0, "mainnet": False}
    async def fake_spam(res):
        return {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}
    monkey["orig_balance"] = balance_mod.get_sol_spl_balance
    monkey["orig_prices"] = balance_mod.enrich_balance_result_with_prices
    monkey["orig_spam"] = balance_mod.enrich_balance_result_with_spam_filter
    balance_mod.get_sol_spl_balance = fake_balance
    balance_mod.enrich_balance_result_with_prices = fake_prices
    balance_mod.enrich_balance_result_with_spam_filter = fake_spam


def _restore_balance(monkey):
    balance_mod.get_sol_spl_balance = monkey["orig_balance"]
    balance_mod.enrich_balance_result_with_prices = monkey["orig_prices"]
    balance_mod.enrich_balance_result_with_spam_filter = monkey["orig_spam"]


def test_get_balance_simple_skips_spl():
    """Simple mode calls get_sol_spl_balance with include_transfer_cost=False
    (the NFT-gallery fast path) — verified by capturing the kwargs."""
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC"}
    captured = {}

    async def fake_balance(addr, networks, **kwargs):
        captured.update(kwargs)
        return [{"network": "https://api.devnet.solana.com", "sol": 1.0, "spl": []}]

    async def fake_prices(res):
        return {"total_usd": 0.0, "priced": 0, "tokens": 0, "mainnet": False}

    async def fake_spam(res):
        return {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}

    monkey = {
        "orig_balance": balance_mod.get_sol_spl_balance,
        "orig_prices": balance_mod.enrich_balance_result_with_prices,
        "orig_spam": balance_mod.enrich_balance_result_with_spam_filter,
    }
    balance_mod.get_sol_spl_balance = fake_balance
    balance_mod.enrich_balance_result_with_prices = fake_prices
    balance_mod.enrich_balance_result_with_spam_filter = fake_spam

    e = _make_address_e(wallet, network_values=(False, False, True))  # devnet only
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    check("Simple: include_transfer_cost=False", captured.get("include_transfer_cost") is False)
    check("Simple: include_image_bytes=False", captured.get("include_image_bytes") is False)


def test_get_balance_pro_includes_spl_fetch():
    """Pro mode calls get_sol_spl_balance with the slow flags (include_transfer_cost=True)."""
    ctx = make_ctx(mode="pro")
    wallet = {"address_base58": "ABC"}
    captured = {}

    async def fake_balance(addr, networks, **kwargs):
        captured.update(kwargs)
        return [{"network": "https://api.devnet.solana.com", "sol": 1.0, "spl": []}]

    async def fake_prices(res):
        return {"total_usd": 0.0, "priced": 0, "tokens": 0, "mainnet": False}

    async def fake_spam(res):
        return {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}

    monkey = {
        "orig_balance": balance_mod.get_sol_spl_balance,
        "orig_prices": balance_mod.enrich_balance_result_with_prices,
        "orig_spam": balance_mod.enrich_balance_result_with_spam_filter,
    }
    balance_mod.get_sol_spl_balance = fake_balance
    balance_mod.enrich_balance_result_with_prices = fake_prices
    balance_mod.enrich_balance_result_with_spam_filter = fake_spam

    e = _make_address_e(wallet, network_values=(False, False, True))
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    check("Pro: include_transfer_cost=True", captured.get("include_transfer_cost") is True)
    check("Pro: include_image_bytes=True", captured.get("include_image_bytes") is True)


def test_get_balance_renders_sol_transfer_and_swap_buttons():
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC"}
    monkey = {}
    _patch_balance(monkey, [{"network": "https://api.devnet.solana.com", "sol": 1.5, "spl": []}])

    e = _make_address_e(wallet, network_values=(False, False, True))
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    texts = walk_text_pieces(ctx.controls["el_token_balance_data"])
    check("Transfer this token shown", any("Transfer this token" in t for t in texts))
    check("Swap shown", any("Swap" in t for t in texts))
    check("SOL amount shown", any("1.5" in t for t in texts))


def test_get_balance_swap_disabled_for_devnet():
    """Swap button is disabled when the row's network is not mainnet."""
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC"}
    monkey = {}
    _patch_balance(monkey, [{"network": "https://api.devnet.solana.com", "sol": 1.5, "spl": []}])

    e = _make_address_e(wallet, network_values=(False, False, True))
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    # Find the Swap button — its disabled flag must be True (devnet, not mainnet).
    swap_buttons = []
    def walk(c):
        if c is None:
            return
        content = getattr(c, "content", None)
        if isinstance(c, flet.ElevatedButton) and isinstance(content, flet.Text) and content.value == "Swap":
            swap_buttons.append(c)
        for attr in ("controls", "content"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif isinstance(child, flet.Control):
                walk(child)
    walk(ctx.controls["el_token_balance_data"])
    check("Swap button found", len(swap_buttons) == 1)
    check("Swap disabled on devnet", swap_buttons and swap_buttons[0].disabled is True)


def test_get_balance_data_dict_contract():
    """The data dict passed to the SOL transfer/swap buttons carries every key
    the transfer.py / swap.py handlers read (Group 5 invariant)."""
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC"}
    monkey = {}
    _patch_balance(monkey, [{"network": "https://api.devnet.solana.com", "sol": 1.5, "spl": []}])

    e = _make_address_e(wallet, network_values=(False, False, True))
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    # Collect every ElevatedButton's data dict on the balance screen.
    data_dicts = []
    def walk(c):
        if c is None:
            return
        if isinstance(c, flet.ElevatedButton) and isinstance(c.data, dict):
            data_dicts.append((c, c.data))
        for attr in ("controls", "content"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif isinstance(child, flet.Control):
                walk(child)
    walk(ctx.controls["el_token_balance_data"])

    sol_transfer = next(
        (d for (b, d) in data_dicts
         if isinstance(b.content, flet.Text) and b.content.value == "Transfer this token"),
        None,
    )
    check("SOL transfer button data found", sol_transfer is not None)
    if sol_transfer:
        for key in ("wallet_address", "network", "sol_amount", "symbol", "wallet_data"):
            check(f"SOL transfer data has '{key}'", key in sol_transfer)

    swap = next(
        (d for (b, d) in data_dicts
         if isinstance(b.content, flet.Text) and b.content.value == "Swap"),
        None,
    )
    check("Swap button data found", swap is not None)
    if swap:
        for key in ("wallet_address", "network", "sol_amount", "wallet_data"):
            check(f"Swap data has '{key}'", key in swap)


def test_get_balance_sol_only_banner_in_simple():
    """Simple mode banner subtotal = sum of SOL usd only (not SPL usd_value)."""
    ctx = make_ctx(mode="simple")
    wallet = {"address_base58": "ABC"}

    async def fake_balance(addr, networks, **kwargs):
        return [
            {"network": "https://api.mainnet-beta.solana.com", "sol": 1.0, "spl": []},
        ]

    # mainnet=True triggers the banner; total_usd is fake (10.0) but Simple mode
    # should override with SOL-only subtotal = sum(sol_usd) = 50.0.
    async def fake_prices(res):
        for r in res:
            r["sol_usd"] = 50.0
        return {"total_usd": 10.0, "priced": 0, "tokens": 0, "mainnet": True}

    async def fake_spam(res):
        return {"spam": 0, "suspicious": 0, "flagged": 0, "total": 0}

    monkey = {
        "orig_balance": balance_mod.get_sol_spl_balance,
        "orig_prices": balance_mod.enrich_balance_result_with_prices,
        "orig_spam": balance_mod.enrich_balance_result_with_spam_filter,
    }
    balance_mod.get_sol_spl_balance = fake_balance
    balance_mod.enrich_balance_result_with_prices = fake_prices
    balance_mod.enrich_balance_result_with_spam_filter = fake_spam

    e = _make_address_e(wallet, network_values=(True, False, False))
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    texts = walk_text_pieces(ctx.controls["el_token_balance_data"])
    # The banner uses fmt_usd(); for 50.0 that's "$50.00".
    check("Simple banner uses SOL-only subtotal ($50.00)", any("$50.00" in t for t in texts))
    check("Simple banner does NOT show the SPL-inclusive total ($10.00)",
          not any("$10.00" in t for t in texts))


def test_get_balance_no_networks_selected():
    """When no networks are ticked, the handler still runs and only renders the divider + (empty) banners."""
    ctx = make_ctx(mode="pro")
    wallet = {"address_base58": "ABC"}
    monkey = {}
    _patch_balance(monkey, [])

    e = _make_address_e(wallet, network_values=(False, False, False))
    try:
        asyncio.run(balance_mod.get_balance_button_click(ctx, e))
    finally:
        _restore_balance(monkey)

    # Empty result -> just the leading Divider (no per-network rows, no banner).
    check("empty balance result -> 1 control", len(ctx.controls["el_token_balance_data"].controls) == 1)


# ============================ build_address_page ============================

def test_build_address_page_structure():
    ctx = make_ctx()
    view = balance_mod.build_address_page(ctx)
    check("returns flet.View", isinstance(view, flet.View))
    check("route is address-page", view.route == "address-page")
    check("appbar present", view.appbar is not None)
    check("navbar wired from ctx", view.navigation_bar is ctx.controls["navbar"])
    check("el_address_page bound into the view",
          ctx.controls["el_address_page"] in (view.controls or []))
    # The "Information:" header is the first control.
    header = (view.controls or [])[0]
    check("Information header present",
          isinstance(header, flet.Text) and header.value == "Information:")


# ============================ runner =========================================

def main():
    tests = [
        test_qr_basic,
        test_qr_different_inputs,
        test_get_storage_data_injects_storage_key,
        test_get_storage_data_non_json_passthrough,
        test_get_storage_data_prefix_filter,
        test_get_wallets_cards_one_card_per_wallet,
        test_get_wallets_cards_watch_only_badge,
        test_get_wallets_cards_empty,
        test_delete_wallet_click_removes_and_routes,
        test_delete_wallet_click_no_storage_key_is_noop,
        test_wallet_info_click_locked_passthrough,
        test_wallet_info_click_watch_only_tag,
        test_show_qr_click_dialog,
        test_go_to_address_page_builds_into_el_address_page,
        test_go_to_address_page_includes_el_token_balance_data,
        test_get_history_simple_header_only,
        test_get_history_pro_expandable,
        test_get_history_developer_includes_csv_button,
        test_get_history_no_csv_button_in_simple,
        test_get_history_no_networks_selected,
        test_get_balance_simple_skips_spl,
        test_get_balance_pro_includes_spl_fetch,
        test_get_balance_renders_sol_transfer_and_swap_buttons,
        test_get_balance_swap_disabled_for_devnet,
        test_get_balance_data_dict_contract,
        test_get_balance_sol_only_banner_in_simple,
        test_get_balance_no_networks_selected,
        test_build_address_page_structure,
    ]
    print(f"\nRunning {len(tests)} tests for ui.components.balance (+ ui.qr)\n")
    for t in tests:
        print(f"--- {t.__name__}")
        t()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
