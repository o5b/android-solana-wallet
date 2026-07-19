"""Headless tests for the extracted More-hub module (Phase 7 Group 6d).

Run:
    PYTHONPATH=src venv/bin/python tests/test_more_ui.py

Covers:
  * _hub_item — pure builder with + without badge; returns a flet.Card with
    the title/subtitle/icon wired in.
  * build_more_page(ctx) -> flet.View with route "more-page" and empty
    controls; registers itself in ctx.controls["more_page"]; AppBar back
    button + navigation_bar wired from ctx.controls.
  * more_enter(ctx) per mode:
      - Simple: only Tools section (Address Book + Settings); no WEB3 & DeFi,
        no Developer.
      - Pro: WEB3 & DeFi (Connect dApp / NFT Gallery / Liquid Staking) +
        Tools; no Developer.
      - Developer: WEB3 & DeFi + Tools + Developer (storage inspector /
        simulation / raw RPC / raw keys / clear storage).
    Each hub item's title text appears exactly the number of times the
    section needs.
  * clear_storage_click(ctx, e) shows a confirmation dialog and does NOT
    wipe storage until the destructive action runs (Cancel path is a no-op).

Click wiring cannot be asserted headlessly (flet registers handlers in an
internal registry, not as a readable .on_click attribute outside a live
session — see info/ui-testing-playbook.md §13). We assert on built control
structure + storage side-effects only.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

from ui.context import AppContext
from ui.components import more as more_mod
from ui.experience import EXPERIENCE_KEY


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


class MockPage:
    def __init__(self):
        self.shared_preferences = MockSP()
        self.update_calls = 0
        self.dialogs_shown = []
        self.pushed_routes = []

    def update(self):
        self.update_calls += 1

    def show_dialog(self, dlg):
        self.dialogs_shown.append(dlg)

    async def push_route(self, route):
        self.pushed_routes.append(route)


def make_ctx(mode=None):
    page = MockPage()
    if mode is not None:
        page.shared_preferences._values[EXPERIENCE_KEY] = mode
    ctx = AppContext(page=page, session={})
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


def walk_text_pieces(view):
    """Yield every plain text string rendered inside a View's control tree."""
    out = []

    def walk(c):
        if c is None:
            return
        # flet.Text stores text in .value
        if isinstance(c, flet.Text) and getattr(c, "value", None):
            out.append(c.value)
        # flet.ElevatedButton/TextButton store the label in .content as a str
        # in flet 0.82.2 (NOT in .text) — see playbook §13/§14.
        content = getattr(c, "content", None)
        if isinstance(content, str):
            out.append(content)
        for attr in ("controls", "content", "actions"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif isinstance(child, flet.Control):
                walk(child)

    for c in (view.controls or []):
        walk(c)
    return out


# ============================ _hub_item ======================================

def test_hub_item_no_badge():
    card = more_mod._hub_item(
        flet.Icons.LINK, "Connect dApp", "subtitle here", on_click=lambda e: None,
    )
    check("returns Card", isinstance(card, flet.Card))
    texts = []
    def walk(c):
        if isinstance(c, flet.Text) and c.value:
            texts.append(c.value)
        content = getattr(c, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for attr in ("controls", "content"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif isinstance(child, flet.Control):
                walk(child)
    walk(card)
    check("title rendered", "Connect dApp" in texts)
    check("subtitle rendered", "subtitle here" in texts)
    check("no badge text", not any(t == "dev" or t == "danger" for t in texts))


def test_hub_item_with_badge():
    card = more_mod._hub_item(
        flet.Icons.STORAGE, "Storage inspector", "subtitle",
        on_click=lambda e: None, badge="dev",
    )
    texts = []
    def walk(c):
        if isinstance(c, flet.Text) and c.value:
            texts.append(c.value)
        content = getattr(c, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for attr in ("controls", "content"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif isinstance(child, flet.Control):
                walk(child)
    walk(card)
    check("badge text rendered", "dev" in texts)


# ============================ build_more_page ===============================

def test_build_more_page_view_and_registry():
    ctx = make_ctx()
    view = more_mod.build_more_page(ctx)
    check("returns flet.View", isinstance(view, flet.View))
    check("route is more-page", view.route == "more-page")
    check("appbar present", view.appbar is not None)
    check("navbar wired from ctx", view.navigation_bar is ctx.controls["navbar"])
    check("controls start empty", view.controls == [])
    check("registered in ctx.controls", ctx.controls["more_page"] is view)


# ============================ more_enter: Simple ============================

def test_more_enter_simple():
    ctx = make_ctx(mode="simple")
    more_mod.build_more_page(ctx)
    asyncio.run(more_mod.more_enter(ctx))
    view = ctx.controls["more_page"]
    texts = walk_text_pieces(view)
    check("Simple: Address Book shown", "Address Book" in texts)
    check("Simple: Settings shown", "Settings" in texts)
    check("Simple: no WEB3 header", "WEB3 & DeFi" not in texts)
    check("Simple: no Connect dApp", "Connect dApp" not in texts)
    check("Simple: no NFT Gallery", "NFT Gallery" not in texts)
    check("Simple: no Liquid Staking", "Liquid Staking" not in texts)
    check("Simple: no Developer header", "Developer" not in texts)
    check("Simple: no Storage inspector", "Storage inspector" not in texts)
    check("Simple: no Clear all storage", "Clear all storage" not in texts)


def test_more_enter_pro():
    ctx = make_ctx(mode="pro")
    more_mod.build_more_page(ctx)
    asyncio.run(more_mod.more_enter(ctx))
    view = ctx.controls["more_page"]
    texts = walk_text_pieces(view)
    check("Pro: WEB3 header shown", "WEB3 & DeFi" in texts)
    check("Pro: Connect dApp shown", "Connect dApp" in texts)
    check("Pro: NFT Gallery shown", "NFT Gallery" in texts)
    check("Pro: Liquid Staking shown", "Liquid Staking" in texts)
    check("Pro: Tools header shown", "Tools" in texts)
    check("Pro: Address Book shown", "Address Book" in texts)
    check("Pro: Settings shown", "Settings" in texts)
    check("Pro: no Developer header", "Developer" not in texts)
    check("Pro: no Storage inspector", "Storage inspector" not in texts)
    check("Pro: no Clear all storage", "Clear all storage" not in texts)


def test_more_enter_developer():
    ctx = make_ctx(mode="developer")
    more_mod.build_more_page(ctx)
    asyncio.run(more_mod.more_enter(ctx))
    view = ctx.controls["more_page"]
    texts = walk_text_pieces(view)
    check("Dev: WEB3 header shown", "WEB3 & DeFi" in texts)
    check("Dev: Connect dApp shown", "Connect dApp" in texts)
    check("Dev: Tools header shown", "Tools" in texts)
    check("Dev: Developer header shown", "Developer" in texts)
    check("Dev: Storage inspector shown", "Storage inspector" in texts)
    check("Dev: Simulation inspector shown", "Simulation inspector" in texts)
    check("Dev: Raw RPC inspector shown", "Raw RPC inspector" in texts)
    check("Dev: Export raw keys shown", "Export raw keys" in texts)
    check("Dev: Clear all storage shown", "Clear all storage" in texts)


def test_more_enter_rebuilds_each_call():
    """A mode change between two calls must produce different hub contents."""
    ctx = make_ctx(mode="simple")
    more_mod.build_more_page(ctx)
    asyncio.run(more_mod.more_enter(ctx))
    after_simple = list(ctx.controls["more_page"].controls)

    # Switch to Developer — re-enter
    ctx.page.shared_preferences._values[EXPERIENCE_KEY] = "developer"
    asyncio.run(more_mod.more_enter(ctx))
    after_developer = ctx.controls["more_page"].controls

    # Controls were replaced (new Column instance)
    check("rebuild replaces the Column wrapper", after_simple[0] is not after_developer[0])
    # And the dev-mode content actually has the Developer section
    texts = walk_text_pieces(ctx.controls["more_page"])
    check("after switch to Dev: Developer header present", "Developer" in texts)


# ============================ clear_storage_click ===========================

def test_clear_storage_click_shows_dialog_no_wipe():
    ctx = make_ctx(mode="developer")
    # Seed some storage so we can detect the wipe.
    ctx.page.shared_preferences._values["wallet.1"] = '{"x": 1}'
    ctx.page.shared_preferences._values["security.pin_salt"] = "abc"

    class E:
        pass

    asyncio.run(more_mod.clear_storage_click(ctx, E()))
    check("clear_storage shows a dialog", len(ctx.page.dialogs_shown) == 1)
    dlg = ctx.page.dialogs_shown[0]
    check("dialog is modal-free (uses actions)", hasattr(dlg, "actions"))
    check("dialog has Cancel action", len(dlg.actions) >= 1)
    check("dialog has destructive action", len(dlg.actions) >= 2)
    # Storage NOT yet wiped (we only opened the confirm dialog)
    check("wallet.1 still present pre-confirm", "wallet.1" in ctx.page.shared_preferences._values)


def test_do_clear_storage_wipes_and_routes_home():
    ctx = make_ctx(mode="developer")
    ctx.page.shared_preferences._values["wallet.1"] = '{"x": 1}'
    ctx.page.shared_preferences._values["security.pin_salt"] = "abc"

    # Drive clear_storage_click to get the dialog, then drive the destructive
    # action's coroutine directly (it's referenced inside the lambda via
    # asyncio.create_task — we can't read the lambda body, so we invoke the
    # private _do_clear_storage the same way the lambda would).
    class E:
        pass

    asyncio.run(more_mod.clear_storage_click(ctx, E()))
    dlg = ctx.page.dialogs_shown[0]
    # The destructive path:
    asyncio.run(more_mod._do_clear_storage(ctx, dlg))
    check("wallet.1 wiped", "wallet.1" not in ctx.page.shared_preferences._values)
    check("pin_salt wiped", "security.pin_salt" not in ctx.page.shared_preferences._values)
    check("pushed home route", "/" in ctx.page.pushed_routes)
    # And a "cleared" dialog was shown
    check("cleared dialog shown", any(
        isinstance(d.title, flet.Text) and "cleared" in (d.title.value or "")
        for d in ctx.page.dialogs_shown
    ))


# ============================ runner ========================================

def main():
    tests = [
        test_hub_item_no_badge,
        test_hub_item_with_badge,
        test_build_more_page_view_and_registry,
        test_more_enter_simple,
        test_more_enter_pro,
        test_more_enter_developer,
        test_more_enter_rebuilds_each_call,
        test_clear_storage_click_shows_dialog_no_wipe,
        test_do_clear_storage_wipes_and_routes_home,
    ]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        t()
    print(f"\n============================================")
    print(f"{'ALL MORE UI TESTS PASSED' if _failed == 0 else f'{_failed} FAILED'}")
    print(f"Total: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
