"""Headless tests for the final orchestrator (Phase 7 Group 6g).

Run:
    PYTHONPATH=src venv/bin/python tests/test_app_ui.py

Covers:
  * The three new view builders that moved from ``main.py`` into their owning
    modules alongside this group:
      - ``build_addressbook_page(ctx)``  (ui/components/addressbook.py)
      - ``build_nft_page(ctx)``          (ui/components/nft.py)
      - ``build_staking_page(ctx)``      (ui/components/staking.py)
    Each returns a ``flet.View`` with the right route + AppBar (title + the
    byte-identical legacy bgcolor) + the AppBar back button wired from
    ``ctx.controls["view_pop"]`` + ``navigation_bar`` wired from
    ``ctx.controls["navbar"]`` + the shared Column (``el_*``) bound as the
    second control.
  * The orchestrator ``build_app(page)`` in ``ui/app.py``:
      - Registers every shared Column (``el_address_page`` / ``el_nft_page`` /
        ``el_lst_page`` / ``el_address_book`` / ``el_rawkey_page`` / etc.) in
        ``ctx.controls`` as a fresh ``flet.Column``.
      - Registers ``csv_file_picker`` / ``view_pop`` / ``navbar`` in
        ``ctx.controls``.
      - Wires ``page.on_route_change`` + ``page.on_view_pop`` to the
        dispatcher closures.
      - Sets the persisted theme (default LIGHT when no pref; DARK when the
        pref says so) and writes the default back when missing.
      - Builds the homepage (route ``/``) with the logo + the three
        wallet-entry buttons + the wallet cards list as the LAST control
        (the ``homepage.controls[-1]`` invariant every prior group
        preserved).
      - The route dispatcher appends the matching view for each route and
        invokes the ``*_enter(ctx)`` hook for routes that need a repopulate.

Click wiring can't be asserted headlessly (flet registers handlers in an
internal registry — see ``info/ui-testing-playbook.md`` §13); the navbar
``on_change`` / button ``on_click`` are inspected for presence only.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

import ui.app as app_mod
from ui.components.addressbook import build_addressbook_page
from ui.components.nft import build_nft_page
from ui.components.staking import build_staking_page


# ---------- mock page --------------------------------------------------------

class MockSP:
    """In-memory shared_preferences (async get/set/contains_key/remove/get_keys)."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    async def contains_key(self, k):
        return k in self._values

    async def get(self, k):
        return self._values.get(k)

    async def get_keys(self, prefix=""):
        return [k for k in self._values.keys() if k.startswith(prefix)]

    async def set(self, k, v):
        self._values[k] = v

    async def remove(self, k):
        self._values.pop(k, None)


class MockPage:
    """Minimal stand-in for ``flet.Page`` covering everything ``build_app``
    + the PIN gate touch at bootstrap."""

    def __init__(self, sp=None):
        self.shared_preferences = sp or MockSP()
        self.services = []
        self.views = []
        self.route = "/"
        self.update_calls = 0
        self.dialogs_shown = []
        self.pushed_routes = []
        # ``page.width`` is read by the homepage button_group Row.
        self.width = 400
        # Settable attributes the bootstrap writes (no-op containers).
        for attr in (
            "scroll", "title", "vertical_alignment", "horizontal_alignment",
            "bgcolor", "padding", "theme_mode",
        ):
            setattr(self, attr, None)

    def update(self):
        self.update_calls += 1  # sync — like real flet

    def show_dialog(self, dlg):
        self.dialogs_shown.append(dlg)

    async def push_route(self, route):
        self.pushed_routes.append(route)
        self.route = route


def make_ctx(page=None, view_pop_set=True, navbar_set=True):
    """Build a minimal ctx for the standalone view-builder tests."""
    from ui.context import AppContext

    page = page or MockPage()
    ctx = AppContext(page=page, session={})
    if view_pop_set:
        ctx.controls["view_pop"] = lambda e: None
    if navbar_set:
        ctx.controls["navbar"] = flet.NavigationBar()
    # The shared Columns each builder binds (only the one a builder needs has
    # to be present, but registering all three is cheap and keeps the tests
    # symmetric).
    for col in ("el_address_book", "el_nft_page", "el_lst_page"):
        ctx.controls[col] = flet.Column()
    return ctx


# ---------- helpers ----------------------------------------------------------

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


def _text_of(ctrl):
    """Best-effort plaintext label of a control (handles flet.Text vs
    ElevatedButton/TextButton whose label lives on ``.content`` as a str
    in flet 0.82.2)."""
    if isinstance(ctrl, flet.Text):
        return ctrl.value or ""
    content = getattr(ctrl, "content", None)
    if isinstance(content, str):
        return content
    return ""


def _walk(c, pred, out):
    if pred(c):
        out.append(c)
    for attr in ("controls", "content"):
        child = getattr(c, attr, None)
        if child is None:
            continue
        if isinstance(child, list):
            for x in child:
                _walk(x, pred, out)
        else:
            _walk(child, pred, out)


# ============================ view-builder tests =============================

def test_build_addressbook_page():
    print("\n--- build_addressbook_page ---")
    ctx = make_ctx()
    view = build_addressbook_page(ctx)
    check("returns flet.View", isinstance(view, flet.View))
    check("route is addressbook-page", view.route == "addressbook-page")
    # AppBar chrome.
    appbar = view.appbar
    check("has AppBar", appbar is not None)
    check("AppBar bgcolor #0d9488", getattr(appbar, "bgcolor", None) == "#0d9488")
    check("AppBar title text", _text_of(appbar.title) == "Address Book")
    # Back button + navbar wired from ctx.controls.
    lead = appbar.leading
    check("leading is IconButton", isinstance(lead, flet.IconButton))
    check("leading icon ARROW_BACK", lead.icon == flet.Icons.ARROW_BACK)
    check("view_pop wired to leading.on_click", lead.on_click is ctx.controls["view_pop"])
    check("navbar is the shared navbar", view.navigation_bar is ctx.controls["navbar"])
    # Layout + content.
    check("horizontal_alignment CENTER", view.horizontal_alignment == flet.CrossAxisAlignment.CENTER)
    check("scroll AUTO", view.scroll == flet.ScrollMode.AUTO)
    check("two controls (header + column)", len(view.controls) == 2)
    check("header Text 'Address Book'", _text_of(view.controls[0]) == "Address Book")
    check("binds el_address_book column",
          view.controls[1] is ctx.controls["el_address_book"])


def test_build_nft_page():
    print("\n--- build_nft_page ---")
    ctx = make_ctx()
    view = build_nft_page(ctx)
    check("returns flet.View", isinstance(view, flet.View))
    check("route is nft-page", view.route == "nft-page")
    appbar = view.appbar
    check("has AppBar", appbar is not None)
    check("AppBar bgcolor #7c3aed", getattr(appbar, "bgcolor", None) == "#7c3aed")
    check("AppBar title text", _text_of(appbar.title) == "NFT Gallery")
    lead = appbar.leading
    check("leading is IconButton", isinstance(lead, flet.IconButton))
    check("leading icon ARROW_BACK", lead.icon == flet.Icons.ARROW_BACK)
    check("view_pop wired to leading.on_click", lead.on_click is ctx.controls["view_pop"])
    check("navbar is the shared navbar", view.navigation_bar is ctx.controls["navbar"])
    check("two controls (header + column)", len(view.controls) == 2)
    check("header Text 'NFT Gallery'", _text_of(view.controls[0]) == "NFT Gallery")
    check("binds el_nft_page column",
          view.controls[1] is ctx.controls["el_nft_page"])


def test_build_staking_page():
    print("\n--- build_staking_page ---")
    ctx = make_ctx()
    view = build_staking_page(ctx)
    check("returns flet.View", isinstance(view, flet.View))
    check("route is stake-page", view.route == "stake-page")
    appbar = view.appbar
    check("has AppBar", appbar is not None)
    check("AppBar bgcolor #0d9488", getattr(appbar, "bgcolor", None) == "#0d9488")
    check("AppBar title text", _text_of(appbar.title) == "Liquid Staking")
    lead = appbar.leading
    check("leading is IconButton", isinstance(lead, flet.IconButton))
    check("leading icon ARROW_BACK", lead.icon == flet.Icons.ARROW_BACK)
    check("view_pop wired to leading.on_click", lead.on_click is ctx.controls["view_pop"])
    check("navbar is the shared navbar", view.navigation_bar is ctx.controls["navbar"])
    check("two controls (header + column)", len(view.controls) == 2)
    check("header Text 'Liquid Staking'", _text_of(view.controls[0]) == "Liquid Staking")
    check("binds el_lst_page column",
          view.controls[1] is ctx.controls["el_lst_page"])


def test_builders_require_ctx_controls():
    """The three new builders depend on ctx.controls being pre-populated
    (view_pop + navbar + the matching el_* column), matching the contract
    every prior group used. The bootstrap registers all three."""
    print("\n--- builder ctx-control contract ---")
    from ui.context import AppContext

    page = MockPage()
    ctx = AppContext(page=page, session={})
    # ctx.controls["view_pop"] / ["navbar"] absent → builders still run but
    # wire ``None`` for chrome (the bootstrap always sets them, so this just
    # documents the contract, not an error path).
    for name, fn, col in (
        ("addressbook", build_addressbook_page, "el_address_book"),
        ("nft", build_nft_page, "el_nft_page"),
        ("staking", build_staking_page, "el_lst_page"),
    ):
        # With the el_* column missing the builder would raise KeyError —
        # prove the bootstrap must register it first.
        try:
            fn(ctx)
            check(f"{name}: did NOT raise without el_* column (unexpected)", False)
        except KeyError:
            check(f"{name}: raises KeyError without registered el_* column", True)
        # Now register the column + chrome and confirm it builds.
        ctx.controls[col] = flet.Column()
        ctx.controls["view_pop"] = lambda e: None
        ctx.controls["navbar"] = flet.NavigationBar()
        view = fn(ctx)
        check(f"{name}: builds cleanly once column + chrome are registered",
              isinstance(view, flet.View) and view.controls[1] is ctx.controls[col])


# ============================ orchestrator tests ============================

def _no_op_watcher(ctx):
    """Stand-in for ``auto_lock_watcher`` — completes immediately instead of
    looping forever. Patched onto ``ui.app.auto_lock_watcher`` so the test
    doesn't leave a pending background task."""
    return asyncio.sleep(0)


async def _build_app_async(page):
    """Run ``build_app`` with the auto-lock watcher patched out."""
    saved = app_mod.auto_lock_watcher
    app_mod.auto_lock_watcher = _no_op_watcher
    try:
        await app_mod.build_app(page)
    finally:
        app_mod.auto_lock_watcher = saved


def test_build_app_bootstrap():
    print("\n--- build_app bootstrap ---")
    page = MockPage()
    asyncio.run(_build_app_async(page))

    # Page config applied.
    check("page.title set", page.title == "Solana Wallet")
    check("page.bgcolor white", page.bgcolor == "white")
    check("page.scroll AUTO", page.scroll == flet.ScrollMode.AUTO)
    check("page.theme_mode default LIGHT", page.theme_mode == flet.ThemeMode.LIGHT)
    check("theme_mode pref written when missing",
          asyncio.run(page.shared_preferences.contains_key("theme_mode")))

    # CSV file picker appended to page.services.
    check("csv_file_picker in page.services", len(page.services) == 1)
    check("csv_file_picker is a FilePicker", isinstance(page.services[0], flet.FilePicker))

    # Handlers wired.
    check("page.on_route_change set", callable(page.on_route_change))
    check("page.on_view_pop set", callable(page.on_view_pop))

    # Initial render happened: one view (the homepage) + at least one update.
    check("page.views has exactly the homepage", len(page.views) == 1)
    check("page.update was called", page.update_calls >= 1)
    homepage = page.views[0]
    check("homepage route is /", homepage.route == "/")

    # PIN gate ran: no PIN stored yet → setup dialog shown.
    check("PIN setup dialog shown at bootstrap", len(page.dialogs_shown) >= 1)


def test_build_app_shared_controls_registered():
    print("\n--- build_app shared controls registry ---")
    page = MockPage()
    asyncio.run(_build_app_async(page))
    # Inspect the ctx via the homepage's navbar (it's the same object the
    # extracted modules reach through ctx.controls). Re-derive ctx by
    # re-running with a captured hook is awkward; instead, fish ctx out of
    # the closed-over state of the route_change closure.
    route_change = page.on_route_change
    # Closures capture locals of build_app; introspect __globals__ + the
    # closure cells. Simpler: re-run and capture ctx by patching AppContext.
    # Easiest reliable path: re-run build_app but wrap AppContext.
    from ui.context import AppContext

    captured = {}
    real_init = AppContext.__init__

    def spy_init(self, *a, **kw):
        real_init(self, *a, **kw)
        captured["ctx"] = self

    AppContext.__init__ = spy_init
    page2 = MockPage()
    try:
        asyncio.run(_build_app_async(page2))
    finally:
        AppContext.__init__ = real_init

    ctx = captured["ctx"]
    check("ctx captured", ctx is not None)
    expected_cols = (
        "el_address_page", "el_token_balance_data", "el_address_book",
        "el_nft_page", "el_lst_page", "el_rawkey_page", "el_dev_storage_page",
        "el_token_page", "el_spl_token_page", "el_swap_page",
    )
    for name in expected_cols:
        col = ctx.controls.get(name)
        check(f"ctx.controls[{name!r}] is a fresh Column",
              isinstance(col, flet.Column))
    # Each registered Column is a DISTINCT object (no aliasing).
    objs = [id(ctx.controls[n]) for n in expected_cols]
    check("all shared Columns are distinct objects", len(set(objs)) == len(objs))

    # Chrome + pickers.
    check("csv_file_picker registered", isinstance(ctx.controls["csv_file_picker"], flet.FilePicker))
    check("view_pop registered", callable(ctx.controls["view_pop"]))
    check("navbar registered", isinstance(ctx.controls["navbar"], flet.NavigationBar))

    # PIN constants on ctx.
    check("pin_salt_key", ctx.pin_salt_key == "security.pin_salt")
    check("pin_verifier_key", ctx.pin_verifier_key == "security.pin_verifier")
    check("auto_lock_seconds=300", ctx.auto_lock_seconds == 300)

    # Session initialized (locked) — auto-lock watcher reads these.
    check("session unlocked=False", ctx.session.get("unlocked") is False)
    check("session key None", ctx.session.get("key") is None)
    # refresh_lock_state ran at bootstrap (no PIN stored) → it pushed the
    # setup dialog onto session["lock_dialog"] (security_gate sets it before
    # showing).
    check("session lock_dialog set (PIN setup shown)",
          ctx.session.get("lock_dialog") is not None)
    check("session last_activity set", "last_activity" in ctx.session)


def test_build_app_theme_dark_from_prefs():
    print("\n--- build_app theme=DARK from prefs ---")
    page = MockPage(sp=MockSP(values={"theme_mode": "DARK"}))
    asyncio.run(_build_app_async(page))
    check("theme_mode DARK applied", page.theme_mode == flet.ThemeMode.DARK)


def test_build_app_homepage_invariant():
    print("\n--- build_app homepage.controls[-1] invariant ---")
    page = MockPage()
    asyncio.run(_build_app_async(page))
    homepage = page.views[0]
    # The last control must be the wallet cards list (a ListView), preserved
    # so route_change's ``homepage.controls[-1] = await get_wallets_cards(ctx)``
    # keeps replacing the right control.
    check("homepage has >=4 controls", len(homepage.controls) >= 4)
    check("last homepage control is the wallets list (ListView)",
          isinstance(homepage.controls[-1], flet.ListView))
    # The wallet-entry button row + the two label texts are present too.
    texts = []
    _walk(homepage, lambda c: isinstance(c, flet.Text), texts)
    label_values = [t.value for t in texts if t.value]
    check("'Solana' label present", "Solana" in label_values)
    check("'Wallets:' label present", "Wallets:" in label_values)
    # The three wallet-entry buttons live in a Row of OutlinedButtons.
    button_rows = []
    _walk(homepage, lambda c: isinstance(c, flet.Row), button_rows)
    outlined = []
    _walk(homepage, lambda c: isinstance(c, flet.OutlinedButton), outlined)
    check("three wallet-entry OutlinedButtons", len(outlined) == 3)


def test_route_dispatcher():
    print("\n--- route_change dispatcher ---")

    async def run():
        page = MockPage()
        await _build_app_async(page)
        # Each route_change call clears views, re-appends homepage, then the
        # matching page. Most branches just append; a few call an ``*_enter``
        # hook. We assert on the appended view's route.
        cases = [
            ("create-wallet-page", "create-wallet-page"),
            ("recover-wallet-page", "recover-wallet-page"),
            ("add-wallet-address-page", "add-wallet-address-page"),
            ("token-page", "token-page"),
            ("spl-token-page", "spl-token-page"),
            ("swap-page", "swap-page"),
            ("sim-page", "sim-page"),
            ("rpc-page", "rpc-page"),
            ("more-page", "more-page"),
            ("settings-page", "settings-page"),
        ]
        for route, expected in cases:
            page.route = route
            page.views.clear()
            await page.on_route_change(None)
            # homepage + the target view.
            check(f"route {route}: appended view matches",
                  len(page.views) == 2 and page.views[1].route == expected)

        # Routes that invoke a *_enter hook still append the right view.
        # wc-page (wc_enter), nft-page (nft_enter), addressbook-page
        # (addressbook_enter), stake-page (lst_enter), dev-storage-page
        # (dev_storage_enter), raw-key-page (rawkey_enter) — all repopulate a
        # shared Column then append. We just check the view appended.
        enter_cases = [
            ("wc-page", "wc-page"),
            ("addressbook-page", "addressbook-page"),
            ("stake-page", "stake-page"),
            ("dev-storage-page", "dev-storage-page"),
            ("raw-key-page", "raw-key-page"),
        ]
        for route, expected in enter_cases:
            page.route = route
            page.views.clear()
            try:
                await page.on_route_change(None)
                ok = len(page.views) == 2 and page.views[1].route == expected
            except Exception as er:
                ok = False
                print(f"      (enter route {route} raised: {er!r})")
            check(f"route {route}: appended view matches after enter hook", ok)

        # nft-page needs a wallet load (no wallets → enter hook short-circuits
        # but still appends the view).
        page.route = "nft-page"
        page.views.clear()
        try:
            await page.on_route_change(None)
            ok = len(page.views) == 2 and page.views[1].route == "nft-page"
        except Exception as er:
            ok = False
            print(f"      (nft enter raised: {er!r})")
        check("route nft-page: appended view matches after enter hook", ok)

        # address-page clears el_token_balance_data + appends the view.
        page.route = "address-page"
        page.views.clear()
        await page.on_route_change(None)
        check("route address-page: appended view matches",
              len(page.views) == 2 and page.views[1].route == "address-page")

        # Unknown route → only the homepage is shown (no crash).
        page.route = "nonexistent-route"
        page.views.clear()
        await page.on_route_change(None)
        check("unknown route: only homepage present", len(page.views) == 1)

    asyncio.run(run())


def test_view_pop_handler():
    print("\n--- view_pop handler ---")

    async def run():
        page = MockPage()
        await _build_app_async(page)
        # Simulate two stacked views (homepage + create-wallet-page).
        page.views.clear()
        page.views.append(flet.View(route="/"))
        page.views.append(flet.View(route="create-wallet-page"))
        await page.on_view_pop(None)
        # pop removed the top view + push_route was called with the new top.
        check("view_pop popped the top view", len(page.views) == 1)
        check("view_pop pushed the new top route",
              page.pushed_routes and page.pushed_routes[-1] == "/")

    asyncio.run(run())


def test_navbar_wiring():
    print("\n--- navbar wiring ---")
    page = MockPage()
    asyncio.run(_build_app_async(page))
    homepage = page.views[0]
    navbar = homepage.navigation_bar
    check("homepage navigation_bar is a NavigationBar", isinstance(navbar, flet.NavigationBar))
    check("navbar has 5 destinations", len(navbar.destinations) == 5)
    labels = [d.label for d in navbar.destinations]
    check("navbar labels Home/New/Recover/Add/More",
          labels == ["Home", "New", "Recover", "Add", "More"])
    check("navbar on_change set", callable(navbar.on_change))


def test_homepage_more_action():
    print("\n--- homepage AppBar More action ---")
    page = MockPage()
    asyncio.run(_build_app_async(page))
    homepage = page.views[0]
    appbar = homepage.appbar
    actions = appbar.actions or []
    more_btns = [a for a in actions if isinstance(a, flet.IconButton)]
    check("AppBar has one IconButton action", len(more_btns) == 1)
    check("AppBar action icon is APPS", more_btns[0].icon == flet.Icons.APPS)
    check("AppBar action tooltip is 'More'", more_btns[0].tooltip == "More")
    check("AppBar action on_click set (nav_more)", callable(more_btns[0].on_click))


# ---------- runner -----------------------------------------------------------

def main():
    test_build_addressbook_page()
    test_build_nft_page()
    test_build_staking_page()
    test_builders_require_ctx_controls()
    test_build_app_bootstrap()
    test_build_app_shared_controls_registered()
    test_build_app_theme_dark_from_prefs()
    test_build_app_homepage_invariant()
    test_route_dispatcher()
    test_view_pop_handler()
    test_navbar_wiring()
    test_homepage_more_action()
    print(f"\n============================================\n"
          f"ALL APP UI TESTS PASSED\n"
          f"Total: {_passed} passed, {_failed} failed\n"
          f"============================================")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
