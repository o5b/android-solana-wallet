"""Priority-fee selector component (extracted from ``main.py``).

Builds a Low / Medium / High / Custom priority-fee selector with progressive
disclosure by experience mode:

* **Simple**    -> the block is hidden entirely and the state always yields 0
                   (Auto / no priority fee), so the wire bytes are identical to
                   the pre-existing default in
                   :func:`solana.compute_budget.priority_fee_instructions`.
* **Pro**       -> Auto / Low / Medium / High presets only.
* **Developer** -> + Custom slider + µLamports field + percentile readout from
                   :func:`solana.balance.get_priority_fee_levels`.

The component never raises: a failed fee-levels fetch falls back to safe
defaults.
"""

import flet

from solana.balance import get_priority_fee_levels
from ui.context import AppContext
from ui.experience import feature, get_experience


async def make_priority_fee_block(
    ctx: AppContext,
    network: str,
    account_for_fees: str,
    cu_limit: int,
) -> tuple[flet.Column, dict]:
    """Build a Low / Medium / High / Custom priority-fee selector.

    Fetches recent prioritization-fee levels for ``account_for_fees`` (the
    sender for SOL transfers, the mint for SPL transfers) and returns
    ``(ui_column, state)`` where ``state['get']()`` yields the chosen
    micro-lamports-per-CU price (0 = Auto / no priority fee).
    """
    page = ctx.page
    # Simple mode: no selector, always Auto (priority_fee=0). Returning an
    # invisible empty column keeps the callers untouched (they still append
    # `pf_block` and read `pf_state`); pf_from_data then yields None.
    mode = await get_experience(page)
    if not feature("priority_fee", mode):
        state = {'micro_lamports': 0, 'get': lambda: 0}
        return flet.Column([], visible=False), state
    allow_custom = feature("priority_fee_custom", mode)

    try:
        levels = await get_priority_fee_levels(account_for_fees, network)
    except Exception as er:
        print(f'priority fee levels fetch failed, using defaults: {er}')
        levels = {'low': 1_000, 'medium': 5_000, 'high': 25_000, 'max': 25_000}

    state = {'micro_lamports': 0}

    def _sol(ul: int) -> str:
        return f"{(cu_limit * ul) / 1_000_000 / 1_000_000_000:.9f}"

    estimate_txt = flet.Text(size=12, color=flet.Colors.GREY_700, selectable=True)
    slider_max = max(levels['max'] * 2, 10_000)
    slider = flet.Slider(min=0, max=slider_max, divisions=200, label="{value}", visible=False)
    custom_tf = flet.TextField(
        label="µLamports / CU", value="0", width=150, min_lines=1, max_lines=1,
        max_length=12, visible=False, keyboard_type=flet.KeyboardType.NUMBER,
    )

    def _refresh():
        ul = state['micro_lamports']
        if ul <= 0:
            estimate_txt.value = ctx.t("pf_estimate_auto")
        else:
            estimate_txt.value = ctx.t("pf_estimate_amount", ul=ul, sol=_sol(ul))
        ctx.safe_update()

    def _set(ul: int):
        state['micro_lamports'] = max(0, int(ul))
        slider.value = state['micro_lamports']
        custom_tf.value = str(state['micro_lamports'])
        _refresh()

    def _preset(ul: int):
        def _h(_e):
            slider.visible = False
            custom_tf.visible = False
            _set(ul)
            ctx.safe_update()
        return _h

    def _custom(_e):
        slider.visible = True
        custom_tf.visible = True
        ctx.safe_update()

    def _on_slide(_e):
        try:
            ul = int(float(slider.value or 0))
        except Exception:
            ul = 0
        custom_tf.value = str(ul)
        state['micro_lamports'] = max(0, ul)
        _refresh()

    def _on_custom_change(_e):
        try:
            ul = int(float(custom_tf.value or 0))
        except Exception:
            ul = 0
        ul = max(0, min(ul, int(slider_max)))
        slider.value = ul
        state['micro_lamports'] = ul
        _refresh()

    slider.on_change = _on_slide
    custom_tf.on_change = _on_custom_change

    preset_buttons = [
        flet.ElevatedButton(ctx.t("pf_auto"), on_click=_preset(0)),
        flet.ElevatedButton(ctx.t("pf_low"), on_click=_preset(levels['low'])),
        flet.ElevatedButton(ctx.t("pf_medium"), on_click=_preset(levels['medium'])),
        flet.ElevatedButton(ctx.t("pf_high"), on_click=_preset(levels['high'])),
    ]
    if allow_custom:
        preset_buttons.append(flet.ElevatedButton(ctx.t("pf_custom"), on_click=_custom))

    presets = flet.Row(preset_buttons, wrap=True)

    block_controls = [
        flet.Text(ctx.t("pf_title"), size=13, weight=flet.FontWeight.BOLD),
        presets,
    ]
    if allow_custom:
        # Developer-only: percentile readout from get_priority_fee_levels.
        block_controls.append(
            flet.Text(
                f"Recent fees (µLamports/CU): low {levels['low']:,} · "
                f"med {levels['medium']:,} · high {levels['high']:,} · max {levels['max']:,}",
                size=11, color=flet.Colors.GREY_600, selectable=True,
            )
        )
        block_controls.append(flet.Row([slider]))
        block_controls.append(flet.Row([custom_tf]))
    block_controls.append(estimate_txt)

    block = flet.Column(block_controls)
    _refresh()
    state['get'] = lambda: state['micro_lamports']
    return block, state


def pf_from_data(data: dict) -> int | None:
    """Read the chosen priority fee (micro-lamports) from a button's data, or None.

    Pure helper (no context needed): the priority-fee ``state`` dict is carried
    in the transfer button's ``data`` under the ``pf_state`` key.
    """
    pf_state = (data or {}).get('pf_state') or {}
    val = pf_state.get('get', lambda: 0)() if callable(pf_state.get('get')) else pf_state.get('micro_lamports', 0)
    val = int(val or 0)
    return val or None
