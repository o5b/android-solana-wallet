"""Headless tests for the extracted DevTools Storage inspector
(Phase 7 Group 6f — moved out of ``main.py`` into ``ui/components/devtools.py``).

Run:
    PYTHONPATH=src venv/bin/python tests/test_dev_storage_ui.py

Covers:
  * ``build_dev_storage_page(ctx)`` → ``flet.View`` with route
    ``dev-storage-page`` + the AppBar + navigation_bar wired from
    ``ctx.controls["view_pop"]`` / ``["navbar"]``; binds the shared
    ``el_dev_storage_page`` column.
  * ``dev_storage_enter(ctx)`` rebuilds the column on each visit:
      - lists every ``shared_preferences`` key/value pair as a row
        (Delete button + indexed ``"i. key: value"`` Text)
      - JSON-decodes string values when possible (objects / arrays / numbers /
        bools) and leaves non-JSON strings untouched
      - handles an empty store (one header ListView row, no entries)
      - clears the previous contents on re-entry (no duplicate rows after a
        second enter)
  * ``_dev_storage_delete_click(ctx, key)``:
      - success path: removes the key from ``shared_preferences`` + shows the
        ``ctx.t("del_ok", key=...)`` dialog (now localized — Phase 1 i18n; the
        legacy Russian text is the ``ru`` translation) + rebuilds the list
      - error path: ``shared_preferences.remove`` raising → prints + shows the
        ``ctx.t("del_err")`` dialog; key is NOT removed
      - dialog strings are localized via :mod:`ui.i18n` (default test lang =
        English; the Russian wording lives in the ``ru`` translation entry).

Click wiring can't be asserted headlessly (flet registers handlers in an
internal registry — see ``info/ui-testing-playbook.md`` §13), so the Delete
button's lambda is inspected for shape (default-arg key capture +
``asyncio.create_task`` target) but the actual delete path is exercised by
calling ``_dev_storage_delete_click`` directly.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

from ui.context import AppContext
from ui.components import devtools as devtools_mod


# ---------- mock page --------------------------------------------------------

class MockSP:
    """Mock shared_preferences that supports get_keys / get / set / remove."""

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
        # Mimic real shared_preferences: raise KeyError on a missing key.
        if k not in self._values:
            raise KeyError(k)
        del self._values[k]


class MockPage:
    def __init__(self, sp=None):
        self.shared_preferences = sp or MockSP()
        self.update_calls = 0
        self.dialogs_shown = []

    def update(self):
        self.update_calls += 1  # sync, like real flet (NOT a coroutine)

    def show_dialog(self, dlg):
        self.dialogs_shown.append(dlg)


def make_ctx(page=None):
    page = page or MockPage()
    ctx = AppContext(page=page, session={})
    ctx.controls["view_pop"] = lambda e: None
    ctx.controls["navbar"] = flet.NavigationBar()
    # The shared column holder main() would create + register at bootstrap.
    ctx.controls["el_dev_storage_page"] = flet.Column()
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


def find_all(root, pred):
    """Walk any control tree (View, ListView, Row, …) and return matches."""
    out = []
    children = getattr(root, "controls", None) or []
    for c in children:
        _walk(c, pred, out)
    return out


# ============================ build_dev_storage_page ========================

def test_build_returns_view():
    ctx = make_ctx()
    view = devtools_mod.build_dev_storage_page(ctx)
    check("returns flet.View", isinstance(view, flet.View))
    check("route is dev-storage-page", view.route == "dev-storage-page")
    check("appbar present", view.appbar is not None)
    check("navbar wired from ctx", view.navigation_bar is ctx.controls["navbar"])


def test_build_appbar_title_and_color():
    ctx = make_ctx()
    view = devtools_mod.build_dev_storage_page(ctx)
    # AppBar leading back button wired from view_pop
    lead = view.appbar.leading
    check("leading is IconButton", isinstance(lead, flet.IconButton))
    check("leading icon is ARROW_BACK", lead.icon == flet.Icons.ARROW_BACK)
    # AppBar color/bgcolor preserved byte-identically
    check("appbar color is white", view.appbar.color == "white")
    check("appbar bgcolor is cyan", view.appbar.bgcolor == "cyan")


def test_build_binds_shared_column():
    ctx = make_ctx()
    view = devtools_mod.build_dev_storage_page(ctx)
    # The header Text + the shared el_dev_storage_page column are in controls.
    texts = [c for c in view.controls if isinstance(c, flet.Text)]
    check("header text present",
          any(t.value == ctx.t("edit_client_storage") for t in texts))
    check("el_dev_storage_page bound in view",
          ctx.controls["el_dev_storage_page"] in view.controls)


def test_build_horizontal_alignment_and_scroll():
    ctx = make_ctx()
    view = devtools_mod.build_dev_storage_page(ctx)
    check("horizontal_alignment is CENTER",
          view.horizontal_alignment == flet.CrossAxisAlignment.CENTER)
    check("scroll is AUTO", view.scroll == flet.ScrollMode.AUTO)


# ============================ dev_storage_enter =============================

def test_enter_lists_all_keys():
    page = MockPage(sp=MockSP({
        "wallet.1": '{"name": "W1"}',
        "ui.experience": '"pro"',
        "theme_mode": "LIGHT",
    }))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    check("list container is ListView", isinstance(lv, flet.ListView))
    # One row per key.
    rows = [c for c in lv.controls if isinstance(c, flet.Row)]
    check("3 rows for 3 keys", len(rows) == 3)
    # Each row has exactly one Delete button + one Text label.
    for i, row in enumerate(rows):
        btns = [c for c in row.controls if isinstance(c, flet.ElevatedButton)]
        texts = [c for c in row.controls if isinstance(c, flet.Text)]
        check(f"row {i+1} has 1 Delete button", len(btns) == 1)
        check(f"row {i+1} button content is 'Delete'",
              getattr(btns[0], "content", None) == "Delete")
        check(f"row {i+1} has 1 Text label", len(texts) == 1)
        # The label is "i. key: value" (1-indexed).
        check(f"row {i+1} label starts with index",
              texts[0].value.startswith(f"{i+1}. "))


def test_enter_json_decodes_string_values():
    """JSON-shaped string values are decoded (so dict/list/etc. render as their
    Python repr, not raw JSON). Non-JSON strings are left untouched."""
    page = MockPage(sp=MockSP({
        "wallet.1": '{"name": "W1"}',          # JSON object  → dict
        "ui.experience": '"pro"',               # JSON string → str (still 'pro')
        "a_num": "42",                          # JSON number → int
        "a_bool": "true",                       # JSON bool   → True
        "plain": "not json at all",             # non-JSON    → unchanged
    }))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    labels = {c.value for c in find_all(lv, lambda c: isinstance(c, flet.Text))}
    # Each label is "i. key: value" — match by substring.
    check("JSON object decoded to dict repr",
          any("wallet.1:" in l and "{'name': 'W1'}" in l for l in labels))
    check("JSON string decoded (inner quotes stripped)",
          any("ui.experience: pro" in l for l in labels))
    check("JSON number decoded to int",
          any("a_num: 42" in l for l in labels))
    check("JSON bool decoded to True",
          any("a_bool: True" in l for l in labels))
    check("non-JSON left byte-identical",
          any("plain: not json at all" in l for l in labels))


def test_enter_empty_store():
    page = MockPage(sp=MockSP({}))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    rows = [c for c in lv.controls if isinstance(c, flet.Row)]
    check("empty store → 0 rows", len(rows) == 0)


def test_enter_rebuild_clears_previous_contents():
    """dev_storage_enter must clear el_dev_storage_page on each visit — no
    duplicate rows after a second enter."""
    page = MockPage(sp=MockSP({"a": "1", "b": "2"}))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    rows = [c for c in lv.controls if isinstance(c, flet.Row)]
    check("second enter → still 2 rows (no duplication)", len(rows) == 2)
    # Only one ListView lives in the column.
    lvs = [c for c in ctx.controls["el_dev_storage_page"].controls
           if isinstance(c, flet.ListView)]
    check("only 1 ListView in column", len(lvs) == 1)


def test_enter_calls_page_update():
    page = MockPage(sp=MockSP({"a": "1"}))
    ctx = make_ctx(page)
    before = page.update_calls
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    check("page.update() called at least once", page.update_calls > before)


def test_enter_delete_button_data_carries_key():
    """Each Delete button carries the key in ``data`` (defense in depth — the
    lambda also captures it via a default arg, but the legacy handler relied on
    ``data``)."""
    page = MockPage(sp=MockSP({"key.one": "1", "key.two": "2"}))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    btns = [c for c in find_all(lv, lambda c: isinstance(c, flet.ElevatedButton))
            if getattr(c, "content", None) == "Delete"]
    check("2 delete buttons", len(btns) == 2)
    data_keys = {b.data for b in btns}
    check("data carries the actual keys", data_keys == {"key.one", "key.two"})


def test_enter_delete_button_lambda_captures_key():
    """The Delete button's on_click is a sync lambda that captures the key via
    a default arg and calls asyncio.create_task(_dev_storage_delete_click(...)).
    flet only awaits coroutine-function handlers, so a sync lambda returning a
    Task is the right shape (the Task actually does the async work)."""
    page = MockPage(sp=MockSP({"k": "v"}))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    btn = next(c for c in find_all(lv, lambda c: isinstance(c, flet.ElevatedButton))
               if getattr(c, "content", None) == "Delete")
    h = btn.on_click
    check("on_click is set", h is not None)
    # Default-arg capture: inspecting __defaults__ exposes the bound 'k' value.
    check("on_click has default-arg capture",
          hasattr(h, "__defaults__") and "k" in h.__defaults__)


# ============================ _dev_storage_delete_click =====================

def test_delete_success_removes_and_dialog_and_refreshes():
    page = MockPage(sp=MockSP({"wallet.1": "1", "wallet.2": "2"}))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    before = len(page.shared_preferences._values)
    asyncio.run(devtools_mod._dev_storage_delete_click(ctx, "wallet.1"))
    # Key removed from storage
    check("key removed from storage",
          "wallet.1" not in page.shared_preferences._values)
    check("other keys untouched",
          "wallet.2" in page.shared_preferences._values)
    # Success dialog shown (localized: default lang = English).
    check("exactly 1 dialog shown", len(page.dialogs_shown) == 1)
    dlg = page.dialogs_shown[0]
    check("dialog is AlertDialog", isinstance(dlg, flet.AlertDialog))
    # AlertDialog title Text value is the localized delete-success string
    # (ctx default lang = English -> "wallet.1 deleted successfully!").
    title_val = dlg.title.value if isinstance(dlg.title, flet.Text) else None
    check("success dialog text localized",
          title_val == ctx.t("del_ok", key="wallet.1"))
    # List refreshed (latent-bug-fix): only 1 row remains
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    rows = [c for c in lv.controls if isinstance(c, flet.Row)]
    check("list refreshed after delete (1 row left)", len(rows) == 1)


def test_delete_error_dialog_and_key_preserved(capsys=None):
    """If shared_preferences.remove raises, the error dialog is shown with the
    byte-identical Russian text and the key is NOT removed."""
    class BoomSP(MockSP):
        async def remove(self, k):
            raise RuntimeError("simulated RPC failure")

    page = MockPage(sp=BoomSP({"wallet.1": "1"}))
    ctx = make_ctx(page)
    asyncio.run(devtools_mod.dev_storage_enter(ctx))
    # Capture stdout to assert the print() side-effect (matches legacy closure)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        asyncio.run(devtools_mod._dev_storage_delete_click(ctx, "wallet.1"))
    check("error printed to stdout",
          "Error deleted data from shared_preferences" in buf.getvalue())
    # Key NOT removed (the raise short-circuits the deletion)
    check("key preserved on error", "wallet.1" in page.shared_preferences._values)
    # Error dialog (localized: default lang = English).
    check("exactly 1 dialog shown", len(page.dialogs_shown) == 1)
    dlg = page.dialogs_shown[0]
    title_val = dlg.title.value if isinstance(dlg.title, flet.Text) else None
    check("error dialog text localized",
          title_val == ctx.t("del_err"))
    # List refreshed (re-enter still runs after error so user sees current state)
    lv = ctx.controls["el_dev_storage_page"].controls[0]
    rows = [c for c in lv.controls if isinstance(c, flet.Row)]
    check("list still has 1 row after error-path refresh", len(rows) == 1)


# ============================ runner ========================================

def main():
    tests = [
        test_build_returns_view,
        test_build_appbar_title_and_color,
        test_build_binds_shared_column,
        test_build_horizontal_alignment_and_scroll,
        test_enter_lists_all_keys,
        test_enter_json_decodes_string_values,
        test_enter_empty_store,
        test_enter_rebuild_clears_previous_contents,
        test_enter_calls_page_update,
        test_enter_delete_button_data_carries_key,
        test_enter_delete_button_lambda_captures_key,
        test_delete_success_removes_and_dialog_and_refreshes,
        test_delete_error_dialog_and_key_preserved,
    ]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        t()
    print(f"\n============================================")
    print(f"{'ALL DEV STORAGE UI TESTS PASSED' if _failed == 0 else f'{_failed} FAILED'}")
    print(f"Total: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
