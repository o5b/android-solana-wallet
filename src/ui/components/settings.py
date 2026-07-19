"""Settings page (extracted from ``main.py`` — Phase 7 Group 6d).

Owns the Settings screen assembled in Phase 3 of the tiered-UI redesign:

* the **theme switch** (``Light theme`` / ``Dark theme``) persisted in
  ``shared_preferences`` under ``theme_mode``;
* the **Experience level** selector (Simple / Pro / Developer) — the single
  source of truth for the ``feature()`` matrix in :mod:`ui.experience`. The
  first switch **into** Developer shows a destructive-tool warning dialog
  (``modal=True`` so a barrier-click or Escape can't dismiss it without an
  action — otherwise the dropdown would show "Developer" while storage stayed
  the prior mode, a self-healing desync).

The two long-lived controls (``theme_control`` / ``experience_dd`` /
``experience_desc``) are created once at bootstrap by
:func:`build_settings_page` and registered in ``ctx.controls`` so the module-
level :func:`settings_enter` hook can read them back without being nested in
the builder.

Every function that needs ``page``/``session`` takes an :class:`AppContext` as
its first argument (Phase 7 migration contract). The module never reaches back
into ``main.py``.
"""

import asyncio

import flet

from ui.context import AppContext
from ui.experience import (
    DEVELOPER,
    MODES,
    SIMPLE,
    description as experience_description,
    label as experience_label,
    get_experience,
    has_seen_dev_warning,
    mark_dev_warning_seen,
    set_experience,
)


def build_settings_page(ctx: AppContext) -> flet.View:
    """Build the Settings page View (called once at bootstrap).

    Creates the theme switch and the experience-level Dropdown + description
    Text, registers them in ``ctx.controls`` (so :func:`settings_enter` can
    re-read the persisted mode on each visit), and wires the embedded
    ``theme_changed`` / ``experience_changed`` handlers as closures.

    The AppBar back button and the navigation bar are read from
    ``ctx.controls`` ("view_pop" / "navbar"), which ``main()`` registers during
    bootstrap.
    """
    page = ctx.page
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]

    # ---- Theme switch ------------------------------------------------------
    async def theme_changed(e):
        page.theme_mode = (
            flet.ThemeMode.DARK
            if page.theme_mode == flet.ThemeMode.LIGHT
            else flet.ThemeMode.LIGHT
        )
        theme_control.label = (
            "Light theme"
            if page.theme_mode == flet.ThemeMode.LIGHT
            else "Dark theme"
        )
        if page.theme_mode == flet.ThemeMode.LIGHT:
            await page.shared_preferences.set("theme_mode", "LIGHT")
        else:
            await page.shared_preferences.set("theme_mode", "DARK")
        page.update()

    theme_control = flet.Switch(
        label="Light theme"
        if page.theme_mode == flet.ThemeMode.LIGHT
        else "Dark theme",
        on_change=theme_changed,
    )

    # ---- Experience level (Simple / Pro / Developer) -----------------------
    async def _apply_experience(mode: str) -> None:
        mode = await set_experience(page, mode)
        experience_dd.value = mode
        experience_desc.value = experience_description(mode)
        page.update()

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
                flet.TextButton(
                    "Cancel",
                    on_click=lambda ev: _cancel_dev_warning(dlg, prev_mode),
                ),
                flet.TextButton(
                    "Enable Developer",
                    style=flet.ButtonStyle(color=flet.Colors.RED),
                    on_click=lambda ev: asyncio.create_task(
                        _confirm_dev_warning(dlg, new_mode)
                    ),
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

    async def _on_experience_select(e):
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

    experience_dd = flet.Dropdown(
        label="Experience level",
        options=[
            flet.dropdown.Option(key=m, text=experience_label(m)) for m in MODES
        ],
        value=SIMPLE,
        dense=True,
        on_select=_on_experience_select,
    )
    experience_desc = flet.Text(
        experience_description(SIMPLE), size=11, color=flet.Colors.GREY_700,
    )

    # Register the long-lived controls so settings_enter can read them back.
    ctx.controls["theme_control"] = theme_control
    ctx.controls["experience_dd"] = experience_dd
    ctx.controls["experience_desc"] = experience_desc

    return flet.View(
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


async def settings_enter(ctx: AppContext) -> None:
    """Read the persisted experience level into the Settings selector.

    Mirrors the ``enter`` hooks (``wc_enter`` / ``rawkey_enter`` /
    ``addressbook_enter``): called from ``route_change`` on each visit to the
    settings page so the dropdown reflects the current storage state (which may
    have changed since bootstrap, e.g. after a Dev-mode warning revert).
    """
    page = ctx.page
    mode = await get_experience(page)
    ctx.controls["experience_dd"].value = mode
    ctx.controls["experience_desc"].value = experience_description(mode)
