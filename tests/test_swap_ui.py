"""Headless tests for the extracted swap module (Phase 7 Group 6b).

Run with:  PYTHONPATH=src venv/bin/python tests/test_swap_ui.py

Verifies:
- module constants (SWAP_TOKENS, _MAINNET)
- `build_swap_page(ctx)` returns a `flet.View` with the correct route + chrome
  wiring + binds `ctx.controls["el_swap_page"]`
- `go_to_swap_page_click(ctx, e)` validation paths:
  - non-mainnet network -> shows the "mainnet only" dialog and returns early
  - watch-only / no-private-key wallet -> shows the "needs private key" dialog
- happy-path form construction ( Dropdowns / TextFields / Get Quote + Swap
  buttons / quote Text ) into `el_swap_page` + `page.push_route("swap-page")`
- the nested `get_quote_button_click` / `swap_button_click` clicks cannot be
  asserted headlessly (flet registers handlers in an internal registry, not as
  a readable `.on_click` attribute outside a live session) — we assert control
  construction + structure instead (per Phase 7 testing playbook §13).
"""

import asyncio
import sys

import flet

from ui.context import AppContext
from ui.components.swap import (
    SWAP_TOKENS,
    _MAINNET,
    build_swap_page,
    go_to_swap_page_click,
)


# ---------- mock page --------------------------------------------------------

class MockSP:
    def __init__(self):
        self.store = {}
    async def contains_key(self, k):
        return k in self.store
    async def get(self, k):
        return self.store.get(k)
    async def set(self, k, v):
        self.store[k] = v
    async def get_keys(self, prefix):
        return [k for k in self.store if k.startswith(prefix)]


class MockControl:
    """Stand-in for a flet control capturing `.controls` + `.disabled`."""
    def __init__(self):
        self.controls = []
        self.disabled = False
        self.value = None
        self.open = None


class MockPage:
    def __init__(self):
        self.shared_preferences = MockSP()
        self.update_calls = 0
        self.dialogs_shown = []
        self.routes_pushed = []
    def update(self):
        self.update_calls += 1  # sync, like real flet (NOT a coroutine)
    def show_dialog(self, dlg):
        self.dialogs_shown.append(dlg)
    async def push_route(self, route):
        self.routes_pushed.append(route)


class FakeEvent:
    """Stand-in for `e` (flet.ControlEvent). `.control.data` is the button data."""
    def __init__(self, control):
        self.control = control


def make_ctx(unlocked=True):
    page = MockPage()
    session = {
        "unlocked": unlocked,
        "key": b"fake_fernet_key_32_bytes_long_xxx" if unlocked else None,
        "last_activity": 0.0,
        "lock_dialog": None,
    }
    ctx = AppContext(page=page, session=session)
    # populate the controls registry that build_swap_page / go_to_swap_page_click need
    ctx.controls["el_swap_page"] = flet.Column()
    ctx.controls["view_pop"] = lambda e: None
    ctx.controls["navbar"] = flet.NavigationBar()
    return ctx


def _dialog_title(dlg):
    """Pull the text out of a `flet.AlertDialog(title=flet.Text(...))`."""
    title = getattr(dlg, "title", None)
    if title is None:
        return None
    return getattr(title, "value", None) or getattr(getattr(title, "content", None), "value", None)


# ---------- tests ------------------------------------------------------------

def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
        return True
    print(f"  FAIL  {msg}")
    return False


def test_constants():
    print("[constants]")
    ok = True
    ok &= check(set(SWAP_TOKENS.keys()) == {"SOL", "USDC", "USDT", "JUP"}, "SWAP_TOKENS has the 4 expected symbols")
    ok &= check(SWAP_TOKENS["SOL"] == ("So11111111111111111111111111111111111111112", 9), "SOL mint + decimals")
    ok &= check(SWAP_TOKENS["USDC"][1] == 6, "USDC decimals = 6")
    ok &= check(_MAINNET == "https://api.mainnet-beta.solana.com", "_MAINNET constant correct")
    return ok


def test_build_swap_page():
    print("[build_swap_page]")
    ok = True
    ctx = make_ctx()
    view = build_swap_page(ctx)
    ok &= check(isinstance(view, flet.View), "returns a flet.View")
    ok &= check(view.route == "swap-page", "route = swap-page")
    # AppBar chrome
    appbar = view.appbar
    ok &= check(appbar is not None, "has an AppBar")
    ok &= check(appbar.bgcolor == "green", "AppBar bgcolor = green (swap-screen colour)")
    title = getattr(appbar.title, "value", None)
    ok &= check(title == "Swap (Jupiter)", "AppBar title = Swap (Jupiter)")
    # Leading back button wired to view_pop
    leading = appbar.leading
    ok &= check(isinstance(leading, flet.IconButton), "leading is an IconButton")
    ok &= check(leading.icon == flet.Icons.ARROW_BACK, "leading icon = ARROW_BACK")
    # Navbar wired
    ok &= check(view.navigation_bar is ctx.controls["navbar"], "navigation_bar = ctx navbar")
    # Binds el_swap_page (the last control is the holder Column)
    ok &= check(ctx.controls["el_swap_page"] in view.controls, "view binds el_swap_page")
    ok &= check(len(view.controls) >= 2, "view has at least the heading + the holder")
    return ok


def test_go_to_swap_non_mainnet_blocked():
    print("[go_to_swap_page_click: non-mainnet blocked]")
    ok = True
    ctx = make_ctx()
    page = ctx.page
    btn = MockControl()
    btn.data = {
        "wallet_address": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "network": "https://api.devnet.solana.com",  # NOT mainnet
        "sol_amount": 1.0,
        "wallet_data": {"address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz"},
    }
    asyncio.run(go_to_swap_page_click(ctx, FakeEvent(btn)))
    ok &= check(len(page.dialogs_shown) == 1, "exactly one dialog shown")
    ok &= check("mainnet" in (_dialog_title(page.dialogs_shown[0]) or "").lower(),
                "dialog mentions mainnet")
    ok &= check(len(page.routes_pushed) == 0, "no route pushed (blocked)")
    ok &= check(len(ctx.controls["el_swap_page"].controls) == 0,
                "el_swap_page left empty (no form built)")
    return ok


def test_go_to_swap_watch_only_blocked():
    print("[go_to_swap_page_click: watch-only wallet blocked]")
    ok = True
    ctx = make_ctx()
    page = ctx.page
    btn = MockControl()
    btn.data = {
        "wallet_address": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "network": _MAINNET,
        "sol_amount": 1.0,
        # watch-only wallet: no private_key_hex -> ctx.has_wallet_private_key False
        "wallet_data": {"address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz"},
    }
    asyncio.run(go_to_swap_page_click(ctx, FakeEvent(btn)))
    ok &= check(len(page.dialogs_shown) == 1, "exactly one dialog shown")
    title = _dialog_title(page.dialogs_shown[0]) or ""
    ok &= check("private key" in title.lower(), "dialog mentions private key")
    ok &= check(len(page.routes_pushed) == 0, "no route pushed (blocked)")
    ok &= check(len(ctx.controls["el_swap_page"].controls) == 0,
                "el_swap_page left empty")
    return ok


def test_go_to_swap_happy_path_form_built():
    print("[go_to_swap_page_click: happy-path form built]")
    ok = True
    ctx = make_ctx(unlocked=True)
    page = ctx.page
    btn = MockControl()
    # Simulate a real (decrypted) wallet record with a private_key_hex.
    # ctx.get_wallet_private_key returns get_secret(wallet, "private_key_hex", key)
    # -> as long as the field is present and non-empty, has_wallet_private_key True.
    btn.data = {
        "wallet_address": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "network": _MAINNET,
        "sol_amount": 1.0,
        "wallet_data": {
            "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
            "private_key_hex": "deadbeef" * 8,  # non-empty -> has_wallet_private_key True
        },
    }
    asyncio.run(go_to_swap_page_click(ctx, FakeEvent(btn)))
    ok &= check(len(page.dialogs_shown) == 0, "no dialog shown (validation passed)")
    ok &= check(page.routes_pushed == ["swap-page"], "pushed swap-page route")
    col = ctx.controls["el_swap_page"]
    ok &= check(len(col.controls) == 5, "form has 5 rows (wallet/in-out/amount-slippage/buttons/quote)")
    # Row 0: wallet label TextSpan
    r0 = col.controls[0]
    ok &= check(isinstance(r0, flet.Row), "row 0 is a Row")
    ok &= check(isinstance(r0.controls[0], flet.Text), "row 0 holds a Text")
    ok &= check(len(r0.controls[0].spans) == 2, "row 0 text has 2 spans (label + address)")
    # Row 1: two Dropdowns (in / out)
    r1 = col.controls[1]
    dd_in, dd_out = r1.controls
    ok &= check(isinstance(dd_in, flet.Dropdown) and isinstance(dd_out, flet.Dropdown),
                "row 1 has two Dropdowns")
    ok &= check(dd_in.value == "SOL", "in-dropdown defaults to SOL")
    ok &= check(dd_out.value == "USDC", "out-dropdown defaults to USDC")
    ok &= check(len(dd_in.options) == 4, "in-dropdown has 4 options (SOL/USDC/USDT/JUP)")
    # Row 2: amount + slippage TextFields
    r2 = col.controls[2]
    tf_amount, tf_slippage = r2.controls
    ok &= check(isinstance(tf_amount, flet.TextField) and isinstance(tf_slippage, flet.TextField),
                "row 2 has two TextFields (amount + slippage)")
    ok &= check(tf_slippage.value == "1.0", "slippage defaults to 1.0%")
    # Row 3: Get Quote + Swap buttons
    r3 = col.controls[3]
    bq, bs = r3.controls
    # flet 0.82.2 ElevatedButton stores the label as a plain string in `.content`
    def btn_label(b):
        c = getattr(b, "content", None)
        if isinstance(c, str):
            return c
        if isinstance(c, flet.Text):
            return c.value
        return getattr(b, "text", None)
    ok &= check(btn_label(bq) == "Get Quote", "Get Quote button labelled correctly")
    ok &= check(btn_label(bs) == "Swap", "Swap button labelled correctly")
    # Row 4: quote text
    r4 = col.controls[4]
    qt = r4.controls[0]
    ok &= check(isinstance(qt, flet.Text), "row 4 holds a Text for the quote")
    ok &= check("Enter an amount" in (qt.value or ""), "quote text is the initial prompt")
    ok &= check(qt.selectable is True, "quote text is selectable")
    return ok


def test_locked_wallet_blocked_as_watch_only():
    """When the app is locked, ctx.has_wallet_private_key returns False (the
    in-memory Fernet key is None), so the swap handler must block with the
    'needs private key' dialog — same as a watch-only wallet."""
    print("[go_to_swap_page_click: locked session blocks like watch-only]")
    ok = True
    ctx = make_ctx(unlocked=False)
    page = ctx.page
    btn = MockControl()
    btn.data = {
        "wallet_address": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
        "network": _MAINNET,
        "sol_amount": 1.0,
        "wallet_data": {
            "address_base58": "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz",
            "private_key_hex": "deadbeef" * 8,
        },
    }
    asyncio.run(go_to_swap_page_click(ctx, FakeEvent(btn)))
    ok &= check(len(page.dialogs_shown) == 1, "locked -> exactly one dialog shown")
    title = _dialog_title(page.dialogs_shown[0]) or ""
    ok &= check("private key" in title.lower(), "locked -> dialog mentions private key")
    ok &= check(len(page.routes_pushed) == 0, "locked -> no route pushed")
    return ok


def test_signature():
    print("[signatures]")
    ok = True
    import inspect
    ok &= check(inspect.iscoroutinefunction(go_to_swap_page_click),
                "go_to_swap_page_click is a coroutine function")
    ok &= check(not inspect.iscoroutinefunction(build_swap_page),
                "build_swap_page is sync")
    sig = inspect.signature(go_to_swap_page_click)
    params = list(sig.parameters.keys())
    ok &= check(params == ["ctx", "e"], f"go_to_swap_page_click signature = (ctx, e) (got {params})")
    sig2 = inspect.signature(build_swap_page)
    ok &= check(list(sig2.parameters.keys()) == ["ctx"], "build_swap_page signature = (ctx,)")
    return ok


def main():
    print("=" * 60)
    print("Swap module tests (Phase 7 Group 6b)")
    print("=" * 60)
    all_ok = True
    for fn in [
        test_constants,
        test_build_swap_page,
        test_go_to_swap_non_mainnet_blocked,
        test_go_to_swap_watch_only_blocked,
        test_go_to_swap_happy_path_form_built,
        test_locked_wallet_blocked_as_watch_only,
        test_signature,
    ]:
        all_ok &= fn()
    print("=" * 60)
    if all_ok:
        print("ALL SWAP UI TESTS PASSED")
        sys.exit(0)
    print("SOME TESTS FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
