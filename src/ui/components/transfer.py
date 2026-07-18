"""Transfer screens (extracted from ``main.py``).

Owns the SOL and SPL transfer pages + their handlers:

* **SOL transfer page** (``token-page``) — built by :func:`go_to_token_page_click`
  / :func:`build_token_page`. Native SOL transfer via
  :func:`solana.transfer_sol.transfer_sol_token`, plus a devnet/testnet
  **airdrop** button (:func:`request_airdrop_click`).
* **SPL token transfer page** (``spl-token-page``) — built by
  :func:`open_spl_token_page` / :func:`build_spl_token_page`. SPL / Token-2022
  transfer (:func:`transfer_spl_click`), partial burn (:func:`burn_spl_click`),
  and **burn-all-and-close** (:func:`burn_and_close_click` — destroys the full
  on-chain balance + closes the ATA, refunding rent).
* **Token-detail expander** on the balance screen
  (:func:`spl_token_arrow_drop_down_click` /
  :func:`spl_token_arrow_drop_up_click` / :func:`_build_spl_token_detail`) —
  Pro shows a friendly summary; Developer adds a raw key/value dump + a
  "Inspect on Solscan" link (Phase 5 progressive disclosure).

Coupling
--------
Signer keys are resolved via ``ctx.get_wallet_private_key`` /
``ctx.has_wallet_private_key`` (the :class:`~ui.context.AppContext` accessors
that mirror the legacy closures — ``""`` while locked, else decrypt on demand).
The recipient ``.sol`` name is resolved via :func:`resolve_recipient_input`
(local helper over :mod:`solana.sns`). The address-poisoning banner + blocking
gate are reused from :mod:`ui.components.addressbook`. The priority-fee block +
``pf_from_data`` reader are reused from :mod:`ui.components.priority_fee`.

Outbound navigation: :func:`open_spl_token_page` is exposed as a coroutine
``(ctx, data)`` so the NFT gallery (in :mod:`ui.components.nft`) can call it
from its "Send NFT" action. It is injected into ``nft_enter`` as a callback
(Phase-7 Group 3 contract for navigation into a not-yet-migrated page; this
module owns the SPL page now so the callback is just the function itself,
adapted to ``(ctx, data)``).

Shared view chrome (AppBar back button, navbar) and the page-holder Columns
(``el_token_page`` / ``el_spl_token_page``) are read from ``ctx.controls`` —
``main()`` registers them during bootstrap, same pattern as Groups 2/3.

INVARIANTS preserved
--------------------
* The exact ``el_token_page`` / ``el_spl_token_page`` control order is preserved
  byte-for-byte — the transfer handlers' positional reads
  (``e.control.parent.parent.controls[N]``) depend on it. The secret TextField
  is inserted at index 5 (SOL) / 6 (SPL) when the wallet has no stored key,
  matching the legacy behaviour.
* The ``data`` dict contract passed to the handlers is unchanged
  (``wallet_address`` / ``network`` / ``spl_amount`` / ``sol_amount`` /
  ``symbol`` / ``raw_data`` / ``wallet_data`` + optional ``nft_prefill_amount``)
  so balance-screen and NFT-gallery call sites need no edits.
* ``solana/`` business layer is never touched (pure reuse).
* No per-session mutable state lives in this module — the poisoning
  confirmation allowlist is in ``ctx.session["_poisoning_confirmed"]``
  (Group 1's rule).
"""

import flet

from solana.balance import get_sol_balance
from solana.create_wallet import create_solana_wallet
from solana.prices import fmt_usd
from solana.sns import SNSResolutionError, resolve_sns_name
from solana.spl_token import (
    burn_and_close_token_account,
    burn_token,
    request_airdrop,
    transfer_spl_token,
)
from solana.transfer_sol import get_min_sol_balance, transfer_sol_token
from solana.validators import (
    is_valid_amount,
    is_valid_private_key,
    is_valid_wallet_address,
    is_valid_wallet_seed_phrase,
)
from ui.components.addressbook import (
    make_poisoning_banner,
    maybe_block_for_poisoning,
    open_contact_picker,
    open_save_contact_dialog,
    update_poisoning_banner,
)
from ui.components.priority_fee import make_priority_fee_block, pf_from_data
from ui.context import AppContext
from ui.experience import feature, get_experience


# ============================ shared helpers ================================

async def resolve_recipient_input(recipient_raw: str, network: str) -> tuple[str, str | None]:
    """Resolve a ``.sol`` recipient name to its wallet address when necessary.

    Returns ``(address, message_or_None)``. A bare base58 address passes through
    unchanged (``message=None``); a ``name.sol`` input is resolved via
    :func:`solana.sns.resolve_sns_name` and the resolution is surfaced to the
    user via the returned message (displayed in the transfer page's SNS status
    line). Raises ``ValueError`` on a resolution failure.
    """
    entered = (recipient_raw or "").strip()
    if not entered.lower().endswith(".sol"):
        return entered, None
    try:
        address = await resolve_sns_name(entered, network)
    except SNSResolutionError as err:
        raise ValueError(str(err)) from err
    return address, f"{entered} resolved to {address}"


def resolve_signing_key(ctx: AppContext, data: dict, secret_control=None) -> tuple[str, str]:
    """Resolve a private key hex for a token/burn action.

    Returns ``(private_key_hex, error_message)``. Tries the stored (decrypted)
    key first via ``ctx.get_wallet_private_key``, then falls back to the secret
    TextField (12/24 words or raw hex private key) entered on the page. Mirrors
    the legacy ``main()`` closure: the seed-phrase path loops up to 10 times
    deriving a keypair and matching against the wallet's address.
    """
    pk = ctx.get_wallet_private_key(data.get('wallet_data') or {})
    if pk:
        return pk, ''
    input_secret = ''
    if secret_control is not None:
        try:
            input_secret = (secret_control.value or '').strip()
        except Exception:
            input_secret = ''
    if not input_secret:
        return '', "Private key is required (unlock the wallet or enter the secret)."
    wallet_data = data['wallet_data']
    if is_valid_wallet_seed_phrase(input_secret):
        for attempt in range(10):
            words, wallet_address_base58, secret_key_base58, new_private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
            if wallet_address_base58 == wallet_data['address_base58']:
                return new_private_key_hex, ''
            if error:
                return '', f"Error getting private key: {error}"
        return '', "Failed to get private key from seed phrase."
    if is_valid_private_key(input_secret) and len(input_secret) == 64:
        return input_secret, ''
    return '', "Invalid secret."


# ====================== token detail expander (balance) =====================

async def _build_spl_token_detail(ctx: AppContext, data: dict) -> list:
    """Token detail rows for the arrow-drop-down expander on the balance screen.

    Progressive disclosure (Phase 5):
    * **Pro**       -> friendly summary (symbol, amount, USD, mint short, program).
    * **Developer** -> full raw key/value dump + a Solscan explorer link.
    """
    page = ctx.page
    mode = await get_experience(page)
    rows: list = []
    if feature("balance_raw", mode):
        spl_token_data_text = ''
        for k, v in data.items():
            spl_token_data_text = spl_token_data_text + f'{k}: {v}\n'
        rows.append(flet.Text(value=spl_token_data_text, selectable=True))
        mint = str(data.get('mint') or '')
        if mint:
            net = str(data.get('network') or '')
            cluster = ''
            if 'devnet' in net:
                cluster = '?cluster=devnet'
            elif 'testnet' in net:
                cluster = '?cluster=testnet'
            url = f"https://solscan.io/token/{mint}{cluster}"
            rows.append(
                flet.Row(
                    [
                        flet.ElevatedButton(
                            content=flet.Text("Inspect on Solscan"),
                            icon=flet.Icons.OPEN_IN_NEW,
                            on_click=lambda _e, u=url: page.launch_url(u),
                        ),
                    ],
                )
            )
        return rows
    # Pro: friendly summary only.
    symbol = ''
    if data.get('symbol_metaplex'):
        symbol += f"{data['symbol_metaplex']} "
    if data.get('symbol_2022'):
        symbol += f"{data['symbol_2022']}"
    symbol = (symbol or data.get('name_2022') or '').strip() or '(unknown)'
    mint = str(data.get('mint') or '')
    mint_short = f"{mint[:6]}…{mint[-4:]}" if len(mint) >= 12 else mint
    program = str(data.get('program_id') or '')
    program_tag = 'Token-2022' if program.startswith('Tokenz') else ('Token' if program.startswith('Token') else program[:12])
    usd_line = ''
    if data.get('usd_value') is not None:
        usd_line = f"USD value: {fmt_usd(data['usd_value'])}"
        if data.get('usd_price') is not None:
            usd_line += f"  (price {fmt_usd(data['usd_price'])})"
    summary = (
        f"Token: {symbol}\n"
        f"Amount: {data.get('amount')}\n"
        f"Decimals: {data.get('decimals')}\n"
        f"Mint: {mint_short}\n"
        f"Program: {program_tag}\n"
    )
    if usd_line:
        summary += f"{usd_line}\n"
    rows.append(flet.Text(value=summary, selectable=True))
    return rows


async def spl_token_arrow_drop_down_click(ctx: AppContext, e):
    """Expand a token's detail panel (arrow points down -> show detail)."""
    page = ctx.page
    try:
        data = e.control.data
        print(f'****** spl_token_arrow_drop_down_button_click >> data: {data}')
        print(f"*** data len: {len(data)}")
        print(f"*** e.control.parent.parent: {e.control.parent.parent}")
        e.control.parent.parent.controls.clear()
        e.control.parent.parent.controls.append(
            flet.Row(
                [
                    flet.TextButton(
                        content=flet.Row(
                            [
                                flet.Icon(flet.Icons.ARROW_DROP_UP, size=50),
                            ],
                        ),
                        on_click=lambda ev: spl_token_arrow_drop_up_click(ctx, ev),
                        data=data,
                    ),
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        )
        detail_controls = await _build_spl_token_detail(ctx, data)
        e.control.parent.parent.controls.extend(detail_controls)
    except Exception as er:
        print(f'Error spl_token_arrow_drop_down_button_click: {er}')
        page.show_dialog(
            flet.AlertDialog(
                title=flet.Text("Error spl_token_arrow_drop_down_button_click!"),
            )
        )
    finally:
        page.update()


async def spl_token_arrow_drop_up_click(ctx: AppContext, e):
    """Collapse a token's detail panel back to the arrow-down state."""
    page = ctx.page
    try:
        data = e.control.data
        e.control.parent.parent.controls.clear()
        e.control.parent.parent.controls.append(
            flet.Row(
                [
                    flet.TextButton(
                        content=flet.Row(
                            [
                                flet.Icon(flet.Icons.ARROW_DROP_DOWN, size=50),
                            ],
                        ),
                        on_click=lambda ev: spl_token_arrow_drop_down_click(ctx, ev),
                        data=data,
                    ),
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        )
    except Exception as er:
        print(f'Error spl_token_arrow_drop_up_button_click: {er}')
        page.show_dialog(
            flet.AlertDialog(
                title=flet.Text("Error spl_token_arrow_drop_up_button_click!"),
            )
        )
    finally:
        page.update()


# =========================== SPL transfer page ==============================

async def go_to_spl_token_page_click(ctx: AppContext, e):
    """Entry from the balance screen's 'Transfer this token' (SPL) button."""
    print(f'****** go_to_spl_token_page_button_click >> e.control.data: {e.control.data}')
    await open_spl_token_page(ctx, e.control.data)


async def open_spl_token_page(ctx: AppContext, data):
    """Build the SPL token transfer page into ``ctx.controls["el_spl_token_page"]``.

    Called either from the balance screen (via :func:`go_to_spl_token_page_click`)
    or from the NFT gallery's 'Send NFT' action (injected as a callback into
    ``nft_enter``). The ``data`` dict shape is identical in both cases.
    """
    page = ctx.page
    el_spl_token_page = ctx.controls["el_spl_token_page"]
    el_spl_token_page.controls.clear()
    amount_tf = flet.TextField(
        label="Input the amount",
        value=("1" if data.get('nft_prefill_amount') is not None else None),
        min_lines=1, max_lines=1, max_length=20,
    )
    recipient_tf = flet.TextField(label="Recipient address or name.sol", min_lines=1, max_lines=1, max_length=100, expand=True)
    sns_status = flet.Text(size=11, selectable=True, color=flet.Colors.BLUE_700)
    secret_tf = flet.TextField(label="Enter Secret (12/24 Words or Private Key)", min_lines=1, max_lines=1, max_length=100)
    burn_status = flet.Column()
    pf_block, pf_state = await make_priority_fee_block(ctx, data['network'], data['raw_data']['mint'], cu_limit=80000)

    poisoning_banner = make_poisoning_banner()

    async def _on_recipient_change(ev):
        await update_poisoning_banner(ctx, poisoning_banner, recipient_tf.value or "")

    recipient_tf.on_change = _on_recipient_change

    async def _pick_contact(addr, name):
        recipient_tf.value = addr
        try:
            recipient_tf.update()
        except Exception:
            pass
        await update_poisoning_banner(ctx, poisoning_banner, addr)

    async def _open_picker(ev):
        await open_contact_picker(ctx, _pick_contact)

    async def _save_contact(ev):
        await open_save_contact_dialog(ctx, (recipient_tf.value or "").strip())

    burn_data = {
        **data,
        'amount_tf': amount_tf,
        'secret_tf': secret_tf,
        'status': burn_status,
        'pf_state': pf_state,
        'cu_limit': 80000,
    }
    close_data = {
        **data,
        'secret_tf': secret_tf,
        'status': burn_status,
        'pf_state': pf_state,
        'cu_limit': 80000,
    }
    transfer_data = {**data, 'pf_state': pf_state, 'cu_limit': 80000,
                     'recipient_tf': recipient_tf, 'poisoning_banner': poisoning_banner,
                     'sns_status': sns_status}
    burn_section = flet.Column(
        [
            flet.Row(
                [
                    flet.ElevatedButton(
                        content=flet.Text("Burn"),
                        on_click=lambda ev: burn_spl_click(ctx, ev),
                        data=burn_data,
                        disabled=False if (data['spl_amount'] and data['spl_amount'] > 0) else True,
                    ),
                    flet.ElevatedButton(
                        content=flet.Text("Burn All & Close Account"),
                        on_click=lambda ev: burn_and_close_click(ctx, ev),
                        data=close_data,
                    ),
                ],
            ),
            flet.Text(
                value="Burn destroys tokens. Close Account also refunds the rent SOL (~0.002) to your wallet.",
                size=11,
                color=flet.Colors.GREY_600,
            ),
            burn_status,
        ]
    )
    el_spl_token_page.controls.extend(
        [
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Network: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data["network"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('From Address: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data["wallet_address"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Token: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data["symbol"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Amount: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data["spl_amount"]} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row([amount_tf]),
            flet.Row(
                [
                    recipient_tf,
                    flet.IconButton(
                        icon=flet.Icons.CONTACTS_OUTLINED,
                        tooltip="Pick from address book",
                        on_click=_open_picker,
                    ),
                    flet.IconButton(
                        icon=flet.Icons.PERSON_ADD_ALT_OUTLINED,
                        tooltip="Save recipient as contact",
                        on_click=_save_contact,
                    ),
                ],
            ),
            flet.Row([poisoning_banner]),
            flet.Row([sns_status]),
            burn_section,
            pf_block,
            flet.Row(
                [
                    flet.ElevatedButton(
                        content=flet.Text("Transfer Token"),
                        on_click=lambda ev: transfer_spl_click(ctx, ev),
                        data=transfer_data,
                    ),
                ],
            ),
            flet.Column(),
        ]
    )
    if not ctx.has_wallet_private_key(data['wallet_data']):
         el_spl_token_page.controls.insert(
            6,
            flet.Row([secret_tf])
        )
    await page.push_route("spl-token-page")


async def transfer_spl_click(ctx: AppContext, e):
    """Transfer SPL tokens from the SPL transfer page."""
    page = ctx.page
    data = e.control.data
    recipient_input = (data.get('recipient_tf').value or "").strip() if data.get('recipient_tf') else ""
    try:
        recipient_address, resolution_message = await resolve_recipient_input(recipient_input, data['network'])
    except ValueError as err:
        page.show_dialog(flet.AlertDialog(title=flet.Text(str(err))))
        return
    if resolution_message:
        data['sns_status'].value = resolution_message
        page.update()
    # Address-poisoning gate (before disabling the button).
    _rtf = data.get('recipient_tf')
    if _rtf is not None and not await maybe_block_for_poisoning(
            ctx, recipient_address, lambda: transfer_spl_click(ctx, e)):
        return
    e.control.disabled = True
    e.control.parent.parent.controls[-1].controls.clear()
    e.control.parent.parent.controls[-1].controls.append(
        flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
    )
    page.update()

    alert_dialog_text = ''
    private_key_hex = ctx.get_wallet_private_key(data['wallet_data'])

    if not private_key_hex:
        input_secret = e.control.parent.parent.controls[6].controls[0].value.strip()
        if is_valid_wallet_seed_phrase(input_secret):
            for attempt in range(10):
                words, wallet_address_base58, secret_key_base58, new_private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
                if wallet_address_base58 == data['wallet_data']['address_base58']:
                    private_key_hex = new_private_key_hex
                    break
                elif error:
                    alert_dialog_text = f"Error getting private key: {error}"
            else:
                alert_dialog_text = "Failed to get private key from seed phrase."
        elif is_valid_private_key(input_secret):
             if len(input_secret) == 64:
                private_key_hex = input_secret
        else:
            alert_dialog_text = "Invalid secret."

    if private_key_hex:
        if is_valid_wallet_address(recipient_address):
            transfer_amount_str = e.control.parent.parent.controls[4].controls[0].value
            if is_valid_amount(transfer_amount_str):
                transfer_amount = float(transfer_amount_str)
                if 0 < transfer_amount <= data['spl_amount']:
                    print("------ DEBUG: transfer_spl_token call ------")
                    print(f"sender_address: {data['wallet_address']}")
                    print(f"recipient_address: {recipient_address}")
                    print(f"mint_address: {data['raw_data']['mint']}")
                    print(f"amount: {transfer_amount}")
                    print(f"decimals: {data['raw_data']['decimals']}")
                    print(f"network: {data['network']}")
                    print("------------------------------------------")
                    result = await transfer_spl_token(
                        sender_address=data['wallet_address'],
                        sender_private_key=private_key_hex,
                        recipient_address=recipient_address,
                        mint_address=data['raw_data']['mint'],
                        amount=transfer_amount,
                        decimals=data['raw_data']['decimals'],
                        network=data['network'],
                        program_id=data['raw_data']['program_id'],
                        priority_fee=pf_from_data(data),
                        cu_limit=data.get('cu_limit', 80000),
                    )
                    if 'result' in result:
                        alert_dialog_text = f"Transfer of {transfer_amount} {data['symbol']} was successful!"
                    elif 'error' in result:
                        alert_dialog_text = f"Transfer Error: {result['error']}"
                    else:
                        alert_dialog_text = "Transfer failed for an unknown reason."
                else:
                    alert_dialog_text = "Invalid transfer amount."
            else:
                alert_dialog_text = "Invalid amount format."
        else:
            alert_dialog_text = "Invalid recipient address."

    if not alert_dialog_text:
         alert_dialog_text = "Could not proceed with transfer. Private key is missing or invalid."

    page.show_dialog(flet.AlertDialog(title=flet.Text(alert_dialog_text)))
    e.control.parent.parent.controls[-1].controls.clear()
    e.control.disabled = False
    page.update()


async def burn_spl_click(ctx: AppContext, e):
    """Partial burn of an SPL token from the SPL transfer page."""
    page = ctx.page
    data = e.control.data
    status = data.get('status')
    e.control.disabled = True
    if status is not None:
        status.controls.clear()
        status.controls.append(
            flet.Row([flet.ProgressRing(), flet.Text("BURNING...")], alignment=flet.MainAxisAlignment.CENTER)
        )
    page.update()

    alert_dialog_text = ''
    private_key_hex, key_err = resolve_signing_key(ctx, data, data.get('secret_tf'))

    if private_key_hex:
        amount_tf = data.get('amount_tf')
        amount_str = ''
        if amount_tf is not None:
            try:
                amount_str = (amount_tf.value or '').strip()
            except Exception:
                amount_str = ''
        if is_valid_amount(amount_str):
            amount = float(amount_str)
            if 0 < amount <= data['spl_amount']:
                result = await burn_token(
                    owner_address=data['wallet_address'],
                    owner_private_key=private_key_hex,
                    mint_address=data['raw_data']['mint'],
                    amount=amount,
                    decimals=data['raw_data']['decimals'],
                    network=data['network'],
                    program_id=data['raw_data'].get('program_id'),
                    priority_fee=pf_from_data(data),
                    cu_limit=data.get('cu_limit', 80000),
                )
                if 'result' in result:
                    alert_dialog_text = f"Burn of {amount} {data['symbol']} was successful!"
                elif 'error' in result:
                    alert_dialog_text = f"Burn Error: {result['error']}"
                else:
                    alert_dialog_text = "Burn failed for an unknown reason."
            else:
                alert_dialog_text = "Invalid burn amount."
        else:
            alert_dialog_text = "Invalid amount format."
    else:
        alert_dialog_text = key_err or "Could not proceed. Private key is missing or invalid."

    page.show_dialog(flet.AlertDialog(title=flet.Text(alert_dialog_text)))
    e.control.disabled = False
    if status is not None:
        status.controls.clear()
    page.update()


async def burn_and_close_click(ctx: AppContext, e):
    """Burn the full SPL balance + close the ATA, refunding rent."""
    page = ctx.page
    data = e.control.data

    async def _execute(ev):
        dlg.open = False
        page.update()
        status = data.get('status')
        ev.control.disabled = True
        if status is not None:
            status.controls.clear()
            status.controls.append(
                flet.Row([flet.ProgressRing(), flet.Text("BURNING & CLOSING...")], alignment=flet.MainAxisAlignment.CENTER)
            )
        page.update()

        alert_dialog_text = ''
        private_key_hex, key_err = resolve_signing_key(ctx, data, data.get('secret_tf'))

        if private_key_hex:
            result = await burn_and_close_token_account(
                owner_address=data['wallet_address'],
                owner_private_key=private_key_hex,
                mint_address=data['raw_data']['mint'],
                network=data['network'],
                program_id=data['raw_data'].get('program_id'),
                priority_fee=pf_from_data(data),
                cu_limit=data.get('cu_limit', 80000),
            )
            if 'result' in result:
                alert_dialog_text = (
                    f"All {data['symbol']} burned and the token account was closed. "
                    f"Rent SOL has been refunded to {data['wallet_address']}."
                )
            elif 'error' in result:
                alert_dialog_text = f"Burn & Close Error: {result['error']}"
            else:
                alert_dialog_text = "Burn & Close failed for an unknown reason."
        else:
            alert_dialog_text = key_err or "Could not proceed. Private key is missing or invalid."

        page.show_dialog(flet.AlertDialog(title=flet.Text(alert_dialog_text)))
        if status is not None:
            status.controls.clear()
        page.update()

    def _cancel(ev):
        dlg.open = False
        page.update()

    dlg = flet.AlertDialog(
        modal=True,
        title=flet.Text("Burn all and close account?"),
        content=flet.Text(
            f"This will DESTROY your entire balance of {data['symbol']} and close the "
            f"token account, refunding the rent (~0.002 SOL) to your wallet. "
            f"This action cannot be undone.",
            selectable=True,
        ),
        actions=[
            flet.TextButton("Cancel", on_click=_cancel),
            flet.ElevatedButton("Burn & Close", on_click=_execute, bgcolor=flet.Colors.RED, color=flet.Colors.WHITE),
        ],
    )
    page.show_dialog(dlg)


# ============================ SOL transfer page =============================

async def go_to_token_page_click(ctx: AppContext, e):
    """Entry from the balance screen's 'Transfer this token' (SOL) button.

    Builds the SOL transfer page into ``ctx.controls["el_token_page"]``.
    """
    page = ctx.page
    print(f'****** go_to_token_page_button_click >> e.control.data: {e.control.data}')
    data = e.control.data
    el_token_page = ctx.controls["el_token_page"]
    el_token_page.controls.clear()
    pf_block, pf_state = await make_priority_fee_block(ctx, data['network'], data['wallet_address'], cu_limit=2000)

    recipient_tf = flet.TextField(
        label="Recipient address or name.sol", min_lines=1, max_lines=1, max_length=100, expand=True,
    )
    sns_status = flet.Text(size=11, selectable=True, color=flet.Colors.BLUE_700)
    poisoning_banner = make_poisoning_banner()

    async def _on_recipient_change(ev):
        await update_poisoning_banner(ctx, poisoning_banner, recipient_tf.value or "")

    recipient_tf.on_change = _on_recipient_change

    async def _pick_contact(addr, name):
        recipient_tf.value = addr
        try:
            recipient_tf.update()
        except Exception:
            pass
        await update_poisoning_banner(ctx, poisoning_banner, addr)

    async def _open_picker(ev):
        await open_contact_picker(ctx, _pick_contact)

    async def _save_contact(ev):
        await open_save_contact_dialog(ctx, (recipient_tf.value or "").strip())

    transfer_data = {
        **data, 'pf_state': pf_state, 'cu_limit': 2000,
        'recipient_tf': recipient_tf, 'poisoning_banner': poisoning_banner,
        'sns_status': sns_status,
    }
    el_token_page.controls.extend(
        [
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Network: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data['network']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Address: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data['wallet_address']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Amount: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data['sol_amount']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            flet.TextSpan('SOL', flet.TextStyle(size=16)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.TextField(label="Input the amount of SOL", min_lines=1, max_lines=1, max_length=20)
                ],
            ),
            flet.Row(
                [
                    recipient_tf,
                    flet.IconButton(
                        icon=flet.Icons.CONTACTS_OUTLINED,
                        tooltip="Pick from address book",
                        on_click=_open_picker,
                    ),
                    flet.IconButton(
                        icon=flet.Icons.PERSON_ADD_ALT_OUTLINED,
                        tooltip="Save recipient as contact",
                        on_click=_save_contact,
                    ),
                ],
            ),
            flet.Row([poisoning_banner]),
            flet.Row([sns_status]),
            pf_block,
            flet.Row(
                [
                    flet.ElevatedButton(
                        content=flet.Text("Transfer SOL"),
                        on_click=lambda ev: transfer_sol_click(ctx, ev),
                        data=transfer_data,
                    ),
                ],
            ),
            flet.Column(),
        ]
    )
    if not ctx.has_wallet_private_key(data['wallet_data']):
        el_token_page.controls.insert(
            5,
            flet.Row(
                [
                    flet.TextField(label="Enter Secret (12/24 Words or Private Key)", min_lines=1, max_lines=1, max_length=100)
                ],
            )
        )
    await page.push_route("token-page")


async def transfer_sol_click(ctx: AppContext, e):
    """Transfer native SOL from the SOL transfer page."""
    page = ctx.page
    data = e.control.data
    recipient_input = (data.get('recipient_tf').value or "").strip() if data.get('recipient_tf') else ""
    try:
        resolved_recipient, resolution_message = await resolve_recipient_input(recipient_input, data['network'])
    except ValueError as err:
        page.show_dialog(flet.AlertDialog(title=flet.Text(str(err))))
        return
    if resolution_message:
        data['sns_status'].value = resolution_message
        data['sns_status'].color = flet.Colors.BLUE_700
        page.update()
    # print(f'****** transfer_sol_button_click >> e.control.data: {data}')
    # Address-poisoning gate (before disabling the button). On confirm it
    # re-invokes this same handler; the address is then whitelisted for the session.
    _rtf = data.get('recipient_tf')
    if _rtf is not None and not await maybe_block_for_poisoning(
            ctx, resolved_recipient, lambda: transfer_sol_click(ctx, e)):
        return
    e.control.disabled = True  # блокируем кнопку
    e.control.parent.parent.controls[-1].controls.clear()
    e.control.parent.parent.controls[-1].controls.append(
        flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
    )
    page.update()
    result_transfer_txt = ''
    sol_balance_after = ''
    alert_dialog_text = ''
    transfer_sol_amount = ''
    recipient_address = ''

    private_key_hex = ctx.get_wallet_private_key(data['wallet_data'])

    if not private_key_hex:
        input_secret = e.control.parent.parent.controls[5].controls[0].value.strip()
        if is_valid_wallet_seed_phrase(input_secret):
            # преобразовать секретные слова 12/24 в приватный ключ в hex формате
            for attempt in range(10):
                words, wallet_address_base58, secret_key_base58, new_private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
                if wallet_address_base58 == data['wallet_data']['address_base58']:
                    private_key_hex = new_private_key_hex
                    break
                elif error:
                    alert_dialog_text = f"Error after: {attempt} attempts to get private key from secret words: {input_secret}! Error Msg: {error}"
            else:
                alert_dialog_text = f'Failed to get private key after: {attempt} attempts from secret words: {input_secret}'
        elif is_valid_private_key(input_secret):
            if len(input_secret) == 64:
                private_key_hex = input_secret
        else:
            alert_dialog_text = "Error Secret!"

    if private_key_hex:
        recipient_address = resolved_recipient
        # print(f'**** recipient: {recipient_address}')
        if is_valid_wallet_address(recipient_address):

            transfer_sol_amount = e.control.parent.parent.controls[3].controls[0].value
            # print(f'**** transfer_sol_button_click >> SOL: {transfer_sol_amount}')
            if is_valid_amount(transfer_sol_amount):
                transfer_sol_amount = float(transfer_sol_amount)

                min_sol_balance = await get_min_sol_balance(data['network'])
                # print(f'**** min_sol_balance: {min_sol_balance}')
                if min_sol_balance is None:
                    min_sol_balance = 0

                if (transfer_sol_amount > 0) and (transfer_sol_amount < data['sol_amount'] - min_sol_balance):
                    result = await transfer_sol_token(
                        sender_address=data['wallet_data']['address_base58'],
                        sender_private_key=private_key_hex,
                        recipient_address=recipient_address,
                        amount=transfer_sol_amount,
                        network=data['network'],
                        priority_fee=pf_from_data(data),
                        cu_limit=data.get('cu_limit', 2000),
                    )
                    # print(f'****** RESULT: {result}')

                    if 'result' in result:
                        sol_balance_after = await get_sol_balance(address=data['wallet_data']['address_base58'], network=data['network'])
                        if sol_balance_after:
                            e.control.parent.parent.controls[2].controls[0].spans=[
                                flet.TextSpan('Amount: ', flet.TextStyle(size=16)),
                                flet.TextSpan(f'{sol_balance_after} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                flet.TextSpan('SOL', flet.TextStyle(size=16)),
                            ]
                            transfer_fee = data['sol_amount'] - sol_balance_after - transfer_sol_amount
                            result_transfer_txt = f"Transfer fee: {transfer_fee:.9f} SOL"
                        alert_dialog_text = f"Transfer of {transfer_sol_amount} SOL was Successfully!"
                    elif 'error' in result:
                        alert_dialog_text = f"Error during Transfer. Error Msg: {result['error']}"
                    elif not result:
                        alert_dialog_text = "Error during Transfer!"
                    else:
                        alert_dialog_text = f"Error Result: {result}"
                else:
                    alert_dialog_text = "Not enough SOL balance for transfer."
            else:
                alert_dialog_text = f"The amount of SOL={transfer_sol_amount} is not valid. Please enter the correct number."
        else:
            alert_dialog_text = f"The recipient wallet address: {recipient_address} is not valid. Please enter the correct recipient wallet address."
    page.show_dialog(
        flet.AlertDialog(
            title=flet.Text(alert_dialog_text),
        )
    )
    e.control.parent.parent.controls[3].controls[0].value = ''
    e.control.parent.parent.controls[-1].controls.clear()
    e.control.parent.parent.controls[-1].controls.extend(
        [
            flet.Divider(thickness=3),
            flet.Row(
                [
                    flet.Text(value='Transfer sol info:', size=14),
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Information message: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{alert_dialog_text}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
                scroll=flet.ScrollMode.AUTO,
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('From: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data['wallet_data']['address_base58']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('To: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{recipient_address}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Transfer: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{transfer_sol_amount} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            flet.TextSpan('SOL', flet.TextStyle(size=16)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Balance before: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{data['sol_amount']} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            flet.TextSpan('SOL', flet.TextStyle(size=16)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value='',
                        selectable=True,
                        spans=[
                            flet.TextSpan('Balance after: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{sol_balance_after} ', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                            flet.TextSpan('SOL', flet.TextStyle(size=16)),
                        ]
                    ),
                ],
            ),
            flet.Row(
                [
                    flet.Text(
                        value=result_transfer_txt,
                        size=14,
                        selectable=True,
                    ),
                ],
            ),
        ]
    )
    e.control.disabled = False  # разблокируем кнопку
    page.update()


async def request_airdrop_click(ctx: AppContext, e):
    """Request a 1 SOL devnet/testnet airdrop (balance screen button)."""
    page = ctx.page
    data = e.control.data
    print(f'****** request_airdrop_sol_button_click >> e.control.data: {data}')
    e.control.disabled = True  # блокируем кнопку
    e.control.parent.parent.controls[-1].controls.clear()
    e.control.parent.parent.controls[-1].controls.append(
        flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
    )
    page.update()
    result_transfer_txt = ''
    sol_balance_after = ''
    alert_dialog_text = f"Not Result request airdrop sol for wallet: {data['wallet_address']}"
    transfer_sol_amount = ''
    recipient_address = ''

    if is_valid_wallet_address(data['wallet_address']):
        result = await request_airdrop(pubkey=data['wallet_address'], lamports=1_000_000_000, network=data['network'])

        alert_dialog_text = f"The result airdrop SOL for wallet address: {data['wallet_address']}: {result}"

    page.show_dialog(
        flet.AlertDialog(
            title=flet.Text(alert_dialog_text),
        )
    )

    # e.control.parent.parent.controls[3].controls[0].value = ''
    e.control.parent.parent.controls[-1].controls.clear()

    e.control.disabled = False  # разблокируем кнопку
    page.update()


# ============================== view builders ==============================

def build_token_page(ctx: AppContext) -> flet.View:
    """Build the SOL transfer page View (binds ``ctx.controls["el_token_page"]``)."""
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    return flet.View(
        route="token-page",
        appbar=flet.AppBar(
            title=flet.Text("Token Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Information:', size=30, font_family="Georgia"),
            ctx.controls["el_token_page"],
        ]
    )


def build_spl_token_page(ctx: AppContext) -> flet.View:
    """Build the SPL token transfer page View (binds ``ctx.controls["el_spl_token_page"]``)."""
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]
    return flet.View(
        route="spl-token-page",
        appbar=flet.AppBar(
            title=flet.Text("SPL Token Transfer"),
            color="white",
            bgcolor="purple",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Transfer SPL Token', size=30, font_family="Georgia"),
            ctx.controls["el_spl_token_page"],
        ]
    )
