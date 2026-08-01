"""Address book + address-poisoning protection (extracted from ``main.py``).

This module owns three cohesive concerns:

1. **Contact storage** — contacts are a single JSON list under one
   :data:`ADDRESSBOOK_KEY` in ``shared_preferences``. Addresses are public
   (exactly like ``wallet.address_base58``), so they stay plaintext — no Fernet.
2. **Address-poisoning detection** — a live warning banner on transfer pages
   (:func:`make_poisoning_banner` / :func:`update_poisoning_banner`) and a
   blocking pre-transfer gate (:func:`maybe_block_for_poisoning`) that compares
   the recipient against the user's own wallets + saved contacts via
   :func:`solana.address_check.check_address_poisoning`.
3. **Contact UI** — the Address Book page (:func:`addressbook_enter`) and the
   contact picker / save-to-contacts dialogs used by the transfer pages.

Every function that needs ``page``/``session`` takes an :class:`AppContext` as
its first argument (Phase 7 migration contract). It never reaches back into
``main.py``: it depends only on ``solana/`` business logic, ``ui.experience``
and ``ctx``. The user's own wallet addresses (for poisoning comparison) are read
directly from ``ctx.page.shared_preferences`` under the ``"wallet."`` prefix so
the module has no dependency on ``main.py``'s ``get_storage_data`` closure.
"""

import asyncio
import json
from datetime import datetime

import flet

from solana.address_check import check_address_poisoning
from solana.validators import is_valid_wallet_address
from ui.context import AppContext
from ui.formatting import short_addr

#: ``shared_preferences`` key under which the contacts JSON list is stored.
ADDRESSBOOK_KEY = "addressbook.contacts"


# ============================ contact storage ===============================

async def ab_load(ctx: AppContext) -> list:
    """Return the saved contacts list (each ``{name, address, note, created_at}``)."""
    page = ctx.page
    if await page.shared_preferences.contains_key(ADDRESSBOOK_KEY):
        raw = await page.shared_preferences.get(ADDRESSBOOK_KEY)
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, TypeError):
                pass
    return []


async def ab_save(ctx: AppContext, contacts: list) -> None:
    await ctx.page.shared_preferences.set(ADDRESSBOOK_KEY, json.dumps(contacts or []))


async def ab_add(ctx: AppContext, name: str, address: str, note: str = "") -> tuple:
    """Add a contact. Returns ``(ok: bool, message: str)``."""
    name = (name or "").strip()
    address = (address or "").strip()
    note = (note or "").strip()
    if not name:
        return False, ctx.t("ab_err_name")
    if not address:
        return False, ctx.t("ab_err_address")
    if not is_valid_wallet_address(address):
        return False, ctx.t("ab_err_invalid_addr", addr=address)
    contacts = await ab_load(ctx)
    for c in contacts:
        if (c.get("address") or "").strip() == address:
            return False, ctx.t("ab_err_duplicate")
    contacts.append({
        "name": name,
        "address": address,
        "note": note,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    await ab_save(ctx, contacts)
    return True, ctx.t("ab_saved", name=name)


async def ab_delete(ctx: AppContext, address: str) -> None:
    address = (address or "").strip()
    contacts = await ab_load(ctx)
    contacts = [c for c in contacts if (c.get("address") or "").strip() != address]
    await ab_save(ctx, contacts)


async def _gather_known_addresses(ctx: AppContext) -> list:
    """Combine address-book contacts + the user's own wallet addresses.

    Used as the comparison set for poisoning detection. Wallets are read
    directly from ``shared_preferences`` (under the ``"wallet."`` prefix) so this
    module has no dependency on ``main.py``'s ``get_storage_data`` closure.
    """
    known = []
    try:
        for c in await ab_load(ctx):
            if c.get("address"):
                known.append({"address": c["address"], "label": c.get("name")})
    except Exception:
        pass
    try:
        keys = await ctx.page.shared_preferences.get_keys("wallet.")
        for key in keys:
            raw = await ctx.page.shared_preferences.get(key)
            try:
                w = json.loads(raw) if isinstance(raw, str) else None
            except (json.JSONDecodeError, TypeError):
                w = None
            if isinstance(w, dict) and w.get("address_base58"):
                known.append({
                    "address": w["address_base58"],
                    "label": w.get("name") or ctx.t("my_wallet"),
                })
    except Exception:
        pass
    return known


# ====================== poisoning banner (live) =============================

def make_poisoning_banner() -> flet.Container:
    """An empty, hidden warning banner updated by :func:`update_poisoning_banner`.

    Pure builder (no ``ctx`` needed) — it only allocates an empty container.
    """
    return flet.Container(
        content=flet.Column([], spacing=2),
        padding=flet.padding.symmetric(horizontal=10, vertical=6),
        border_radius=6,
        visible=False,
    )


async def update_poisoning_banner(ctx: AppContext, banner: flet.Container, address: str) -> bool:
    """Recompute the live poisoning banner. Returns ``True`` if a danger/warning exists."""
    try:
        known = await _gather_known_addresses(ctx)
    except Exception:
        known = []
    res = check_address_poisoning(address or "", known)
    col = banner.content
    col.controls.clear()
    danger = bool(res["has_danger"] or res["hidden_chars"] or res["invalid_chars"])
    warn = bool(res["has_warning"])
    if res["exact"]:
        col.controls.append(flet.Row([
            flet.Icon(flet.Icons.VERIFIED_USER, color=flet.Colors.GREEN_700, size=18),
            flet.Text(ctx.t("poison_known", name=res['exact']['label'] or ctx.t("saved_address")),
                      size=12, color=flet.Colors.GREEN_700, selectable=True),
        ], wrap=True))
        banner.bgcolor = flet.Colors.GREEN_50
    elif danger:
        msgs = []
        for w in res["warnings"]:
            msgs.extend(w["reasons"])
        col.controls.append(flet.Row([
            flet.Icon(flet.Icons.DANGEROUS, color=flet.Colors.RED, size=18),
            flet.Text(ctx.t("poison_danger", reasons=" ".join(msgs)[:260]),
                      size=11, color=flet.Colors.RED_700, selectable=True),
        ], wrap=True))
        banner.bgcolor = flet.Colors.RED_50
    elif warn:
        msgs = []
        for w in res["warnings"]:
            msgs.extend(w["reasons"])
        col.controls.append(flet.Row([
            flet.Icon(flet.Icons.WARNING_AMBER_ROUNDED, color=flet.Colors.ORANGE, size=18),
            flet.Text(ctx.t("poison_caution", reasons=" ".join(msgs)[:260]),
                      size=11, color=flet.Colors.ORANGE_800, selectable=True),
        ], wrap=True))
        banner.bgcolor = flet.Colors.ORANGE_50
    elif (address or "").strip() and res["valid"]:
        col.controls.append(flet.Text(
            ctx.t("poison_not_in_book"),
            size=11, color=flet.Colors.GREY_700, selectable=True))
        banner.bgcolor = None
    else:
        banner.bgcolor = None
    banner.visible = len(col.controls) > 0
    try:
        banner.update()
    except Exception:
        pass
    return danger or warn


# ========================= contact dialogs ==================================

async def open_contact_picker(ctx: AppContext, on_pick) -> None:
    """Show saved contacts; clicking one calls ``await on_pick(address, name)``."""
    page = ctx.page
    contacts = await ab_load(ctx)
    if not contacts:
        page.show_dialog(flet.AlertDialog(
            title=flet.Text(ctx.t("ab_empty")),
            content=flet.Text(ctx.t("ab_empty_hint")),
        ))
        return
    col = flet.Column([], scroll=flet.ScrollMode.AUTO, tight=True, spacing=6)

    async def _pick(ev, a=None, n=None):
        ctx.close_dialog(dlg)
        await on_pick(a, n)

    for c in contacts:
        addr = c.get("address", "")
        name = c.get("name") or ""
        note = c.get("note") or ""
        btn = flet.TextButton(
            content=flet.Row([
                flet.Icon(flet.Icons.CONTACT_PAGE_OUTLINED, size=18, color=flet.Colors.BLUE),
                flet.Column([
                    flet.Text(name if name else ctx.t("no_name"), size=14, weight=flet.FontWeight.BOLD),
                    flet.Text(short_addr(addr), size=10, color=flet.Colors.GREY_700, selectable=True),
                ] + ([flet.Text(note, size=10, color=flet.Colors.GREY_500, selectable=True)] if note else []),
                    spacing=0, tight=True),
            ], wrap=True),
            on_click=(lambda ev, a=addr, n=name: asyncio.create_task(_pick(ev, a, n))),
        )
        col.controls.append(flet.Container(content=btn, width=340, border_radius=6))

    dlg = flet.AlertDialog(
        modal=True,
        title=flet.Text(ctx.t("ab_pick_contact")),
        content=flet.Container(content=col, width=360, height=min(60 + len(contacts) * 64, 420)),
        actions=[flet.TextButton(ctx.t("cancel"), on_click=lambda ev: ctx.close_dialog(dlg))],
    )
    page.show_dialog(dlg)


async def open_save_contact_dialog(ctx: AppContext, address: str) -> None:
    page = ctx.page
    address = (address or "").strip()
    if not address:
        return
    name_tf = flet.TextField(label=ctx.t("contact_name"), autofocus=True, min_lines=1, max_lines=1, max_length=50)

    async def _save(ev):
        nm = (name_tf.value or "").strip()
        if not nm:
            return
        ok, msg = await ab_add(ctx, nm, address, "")
        ctx.close_dialog(dlg)
        page.show_dialog(flet.AlertDialog(title=flet.Text(msg)))
        page.update()

    dlg = flet.AlertDialog(
        modal=True,
        title=flet.Text(ctx.t("ab_save_title")),
        content=flet.Column([
            flet.Text(ctx.t("info_address", val=short_addr(address)), selectable=True, size=12),
            name_tf,
        ], tight=True),
        actions=[
            flet.TextButton(ctx.t("cancel"), on_click=lambda ev: ctx.close_dialog(dlg)),
            flet.ElevatedButton(ctx.t("save"), on_click=_save),
        ],
    )
    page.show_dialog(dlg)


# =================== blocking poisoning gate (transfer) ====================

async def maybe_block_for_poisoning(ctx: AppContext, recipient_raw: str, rerun) -> bool:
    """Blocking address-poisoning gate run before a transfer.

    Returns ``True`` to let the transfer proceed (clean address, or already
    confirmed this session). Returns ``False`` after showing a modal confirm
    dialog; the "Proceed" button marks the address confirmed and re-invokes
    ``rerun()`` (the original transfer handler), so it only prompts once per
    address per session.

    The per-session confirmation allowlist lives in ``ctx.session`` under
    ``"_poisoning_confirmed"`` (a ``set`` of normalized addresses) — never
    persisted, scoped to one app session, matching the original closure's
    lifetime exactly.
    """
    page = ctx.page
    addr = (recipient_raw or "").strip()
    if not addr:
        return True
    try:
        known = await _gather_known_addresses(ctx)
    except Exception:
        known = []
    res = check_address_poisoning(addr, known)
    risky = bool(res["has_danger"] or res["has_warning"]
                 or res["hidden_chars"] or res["invalid_chars"])
    if not risky:
        return True
    confirmed = ctx.session.setdefault("_poisoning_confirmed", set())
    if res["normalized"] and res["normalized"] in confirmed:
        return True

    is_danger = bool(res["has_danger"] or res["hidden_chars"] or res["invalid_chars"])
    lines = []
    for w in res["warnings"]:
        lbl = (w.get("label") + " — ") if w.get("label") else ""
        for reason in w["reasons"]:
            lines.append(f"[{w['severity'].upper()}] {lbl}{reason}")
    content_lines = [
        flet.Text(ctx.t("poison_recipient_warn"), size=13, weight=flet.FontWeight.BOLD),
        flet.Text(ctx.t("poison_entered", val=res['normalized'] or addr), size=11, selectable=True, color=flet.Colors.GREY_800),
        flet.Text("\n".join(lines), size=11, selectable=True,
                  color=flet.Colors.RED_700 if is_danger else flet.Colors.ORANGE_800),
        flet.Text(ctx.t("poison_explain"), size=11, color=flet.Colors.GREY_700),
    ]

    def _cancel(ev):
        ctx.close_dialog(dlg)

    def _proceed(ev):
        ctx.close_dialog(dlg)
        if res["normalized"]:
            confirmed.add(res["normalized"])
        asyncio.create_task(rerun())

    proceed_btn = flet.ElevatedButton(
        ctx.t("poison_proceed"), on_click=_proceed,
        bgcolor=flet.Colors.RED if is_danger else flet.Colors.ORANGE,
        color=flet.Colors.WHITE,
    )
    dlg = flet.AlertDialog(
        modal=True,
        title=flet.Row([
            flet.Icon(flet.Icons.DANGEROUS if is_danger else flet.Icons.WARNING_AMBER_ROUNDED,
                      color=flet.Colors.RED if is_danger else flet.Colors.ORANGE),
            flet.Text(ctx.t("poison_suspicious_title")),
        ]),
        content=flet.Column(content_lines, tight=True, spacing=6),
        actions=[flet.TextButton(ctx.t("cancel"), on_click=_cancel), proceed_btn],
    )
    page.show_dialog(dlg)
    return False


# ============================= address book page ===========================

async def addressbook_enter(ctx: AppContext) -> None:
    """(Re)build the Address Book page contents into ``ctx.controls["el_address_book"]``."""
    page = ctx.page
    el_address_book = ctx.controls["el_address_book"]
    el_address_book.controls.clear()
    name_tf = flet.TextField(label=ctx.t("contact_name"), min_lines=1, max_lines=1, max_length=50)
    addr_tf = flet.TextField(label=ctx.t("ab_solana_address"), min_lines=1, max_lines=2, max_length=100)
    note_tf = flet.TextField(label=ctx.t("ab_note"), min_lines=1, max_lines=2, max_length=200)
    status = flet.Text(selectable=True, size=12)

    async def _add(ev):
        ok, msg = await ab_add(ctx, name_tf.value, addr_tf.value, note_tf.value)
        if ok:
            await addressbook_enter(ctx)
            return
        status.value = msg
        status.color = flet.Colors.RED
        page.update()

    add_form = flet.Column([
        flet.Text(ctx.t("ab_add_contact"), size=18, weight=flet.FontWeight.BOLD),
        flet.Row([name_tf]),
        flet.Row([addr_tf]),
        flet.Row([note_tf]),
        flet.Row([flet.ElevatedButton(ctx.t("ab_add_contact_btn"), on_click=_add), status]),
        flet.Divider(),
        flet.Row([
            flet.Icon(flet.Icons.SHIELD_OUTLINED, color=flet.Colors.GREEN_700),
            flet.Text(ctx.t("ab_protection_hint"), size=11, color=flet.Colors.GREY_700, selectable=True),
        ], wrap=True),
        flet.Divider(),
    ], spacing=8)

    contacts = await ab_load(ctx)
    if not contacts:
        add_form.controls.append(flet.Text(ctx.t("ab_no_contacts"), size=12, color=flet.Colors.GREY_600))
    else:
        add_form.controls.append(flet.Text(ctx.t("ab_contacts_count", n=len(contacts)), size=16, weight=flet.FontWeight.BOLD))
        for c in contacts:
            addr = c.get("address", "")
            name = c.get("name") or ctx.t("no_name")
            note = c.get("note") or ""

            async def _copy(ev, a=addr, n=name):
                await page.clipboard.set(a)
                status.value = ctx.t("ab_copied", name=n)
                status.color = flet.Colors.GREEN
                page.update()

            async def _del(ev, a=addr):
                await ab_delete(ctx, a)
                await addressbook_enter(ctx)

            add_form.controls.append(flet.Card(content=flet.Container(
                padding=10,
                content=flet.Column([
                    flet.Text(name, size=15, weight=flet.FontWeight.BOLD),
                    flet.Text(addr, size=11, selectable=True, color=flet.Colors.GREY_800),
                ] + ([flet.Text(note, size=11, selectable=True, color=flet.Colors.GREY_600)] if note else []) + [
                    flet.Row([
                        flet.OutlinedButton(ctx.t("copy"), on_click=_copy),
                        flet.OutlinedButton(ctx.t("delete"), on_click=_del,
                                            style=flet.ButtonStyle(color=flet.Colors.RED)),
                    ]),
                ], spacing=2),
            )))

    el_address_book.controls.append(add_form)
    page.update()


def build_addressbook_page(ctx: AppContext) -> flet.View:
    """Build the Address Book page (binds the shared ``el_address_book`` column;
    ``addressbook_enter(ctx)`` repopulates it on each visit).

    Extracted from ``main.py`` during Phase 7 Group 6g — mirrors the
    ``build_*_page`` pattern used by the other extracted modules: the View is
    built once at bootstrap, binds the shared Column registered in
    ``ctx.controls["el_address_book"]``, and wires the shared view chrome
    (AppBar back button + navbar) from ``ctx.controls``.
    """
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    return flet.View(
        route="addressbook-page",
        appbar=flet.AppBar(
            title=flet.Text(ctx.t("hub_address_book")),
            color="white",
            bgcolor="#0d9488",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text(ctx.t("hub_address_book"), size=30, font_family="Georgia"),
            ctx.controls["el_address_book"],
        ],
    )
