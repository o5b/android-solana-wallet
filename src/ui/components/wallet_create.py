"""Create / Recover / Add-wallet-address pages (Phase 7 Group 6a).

Lifted verbatim out of ``main()``: the three wallet-entry screens (Create New
Wallet, Recover Wallet, Add Wallet Address) along with their form fields, save /
clear / copy handlers, the seed-phrase backup quiz, the success and error
cards, and the three ``flet.View`` definitions. The block is self-contained —
the only external state it needs is the live ``page`` (via ``ctx``), the shared
view chrome (``ctx.controls["view_pop"]`` / ``["navbar"]``), and
``ctx.encrypt_for_storage`` (mirrors the legacy closure).

The form TextFields / Text objects are created **once** at bootstrap and bound
into the views; their values persist across navigations (legacy behaviour — see
AGENTS.md "Form fields are global objects").

The three Views are returned to ``main.py`` which appends them in
``route_change`` exactly as before. There is no per-visit rebuild (no "enter"
hook): the screens hold their state across navigations by design.
"""

import json
import random
from datetime import datetime

import flet

from solana.create_wallet import create_solana_wallet
from solana.security import WATCH_ONLY_FIELD


async def build_wallet_pages(ctx) -> tuple:
    """Build the three wallet-entry Views once at bootstrap.

    Returns ``(create_wallet_page, recover_wallet_page,
    add_wallet_address_page)``. Each View binds its own form fields + handlers
    (closures capturing the locally-created fields), so the three pages are
    fully independent of each other after this returns.
    """
    page = ctx.page
    view_pop = ctx.controls["view_pop"]
    navbar = ctx.controls["navbar"]

    # ============================== Form inputs ==============================
    input_wallet_name = flet.TextField(
        label="Wallet Name", min_lines=1, max_lines=1, max_length=50,
    )
    input_wallet_description = flet.TextField(
        label="Wallet description", min_lines=2, max_lines=5, max_length=200,
    )

    input_recover_wallet_name = flet.TextField(
        label="Wallet Name", min_lines=1, max_lines=1, max_length=50,
    )
    input_recover_wallet_description = flet.TextField(
        label="Wallet description", min_lines=2, max_lines=5, max_length=200,
    )
    input_recover_wallet_secret = flet.TextField(
        label="Wallet Secret Words (12/24) or Secret Key base58 (length=88)",
        min_lines=2, max_lines=5, max_length=200,
    )

    input_add_address_wallet_name = flet.TextField(
        label="Wallet Name", min_lines=1, max_lines=1, max_length=50,
    )
    input_add_address_wallet_description = flet.TextField(
        label="Wallet description", min_lines=2, max_lines=5, max_length=200,
    )
    input_add_wallet_address = flet.TextField(
        label="Add Wallet Address (base58) ", min_lines=2, max_lines=5, max_length=200,
    )

    # ====================== Display-only result fields ======================
    # Filled after wallet creation/recovery. Their values persist across
    # navigations (legacy "global objects" behaviour).
    txt_wallet_name = flet.TextField()
    txt_wallet_description = flet.TextField()
    txt_wallet_address = flet.Text(selectable=True)
    txt_private_key = flet.Text(selectable=True)
    txt_secret_key_base58 = flet.Text(selectable=True)
    txt_public_key = flet.Text(selectable=True)
    txt_words = flet.Text(selectable=True)
    txt_error = flet.Text(selectable=True)
    txt_wallet_created = flet.Text(selectable=True)

    txt_recover_wallet_name = flet.TextField()
    txt_recover_wallet_description = flet.TextField()
    txt_recover_wallet_address = flet.Text(selectable=True)
    txt_recover_private_key = flet.Text(selectable=True)
    txt_recover_secret_key_base58 = flet.Text(selectable=True)
    txt_recover_public_key = flet.Text(selectable=True)
    txt_recover_words = flet.Text(selectable=True)
    txt_recover_error = flet.Text(selectable=True)
    txt_recover_wallet_created = flet.Text(selectable=True)
    txt_recover_wallet_secret = flet.Text(selectable=True)

    txt_add_address_wallet_name = flet.TextField()
    txt_add_address_wallet_description = flet.TextField()
    txt_add_address_wallet_address = flet.Text(selectable=True)
    txt_add_address_error = flet.Text(selectable=True)
    txt_add_address_wallet_created = flet.Text(selectable=True)

    # ============================ Copy helper ===============================
    async def copy_wallet_data_click(e, mode):
        data_to_copy = {}
        if mode == 'create':
            data_to_copy = {
                'created': txt_wallet_created.value,
                'name': txt_wallet_name.value,
                'description': txt_wallet_description.value,
                'address_base58': txt_wallet_address.value,
                'private_key_hex': txt_private_key.value,
                'public_key_hex': txt_public_key.value,
                'words': txt_words.value,
                'secret_key_base58': txt_secret_key_base58.value,
            }
        elif mode == 'recover':
            data_to_copy = {
                'created': txt_recover_wallet_created.value,
                'name': txt_recover_wallet_name.value,
                'description': txt_recover_wallet_description.value,
                'address_base58': txt_recover_wallet_address.value,
                'private_key_hex': txt_recover_private_key.value,
                'public_key_hex': txt_recover_public_key.value,
                'words': txt_recover_words.value,
                'secret_key_base58': txt_recover_secret_key_base58.value,
            }
        elif mode == 'add':
            data_to_copy = {
                'created': txt_add_address_wallet_created.value,
                'name': txt_add_address_wallet_name.value,
                'description': txt_add_address_wallet_description.value,
                'address_base58': txt_add_address_wallet_address.value,
            }
        await page.clipboard.set(json.dumps(data_to_copy, indent=2))
        page.show_dialog(flet.AlertDialog(title=flet.Text("Data copied to clipboard!")))

    # ========================= Save / clear handlers ========================
    async def generate_new_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        value['name'] = txt_wallet_name.value
        value['description'] = txt_wallet_description.value
        value['address_base58'] = txt_wallet_address.value
        value['private_key_hex'] = txt_private_key.value
        value['public_key_hex'] = txt_public_key.value
        value['words'] = txt_words.value
        value['secret_key_base58'] = txt_secret_key_base58.value

        print(f'generate_new_wallet_storage >> key: {key}')
        print(f'generate_new_wallet_storage >> value: {value}')

        await page.shared_preferences.set(key, json.dumps(ctx.encrypt_for_storage(value)))

        txt_error.value = ''
        txt_wallet_created.value = ''
        txt_wallet_name.value = ''
        txt_wallet_description.value = ''
        txt_wallet_address.value = ''
        txt_private_key.value = ''
        txt_public_key.value = ''
        txt_words.value = ''
        txt_secret_key_base58.value = ''
        create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        page.update()

    generate_new_solana_wallet_card_save_button = flet.TextButton(
        "Save",
        on_click=generate_new_solana_wallet_card_save_button_clicked,
        data=0,
    )

    async def recover_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        value['name'] = txt_recover_wallet_name.value
        value['description'] = txt_recover_wallet_description.value
        value['address_base58'] = txt_recover_wallet_address.value
        value['private_key_hex'] = txt_recover_private_key.value
        value['public_key_hex'] = txt_recover_public_key.value
        value['words'] = txt_recover_words.value
        value['secret_key_base58'] = txt_recover_secret_key_base58.value

        print(f'recover_wallet_storage >> key: {key}')
        print(f'recover_wallet_storage >> value: {value}')

        await page.shared_preferences.set(key, json.dumps(ctx.encrypt_for_storage(value)))

        txt_recover_error.value = ''
        txt_recover_wallet_created.value = ''
        txt_recover_wallet_name.value = ''
        txt_recover_wallet_description.value = ''
        txt_recover_wallet_address.value = ''
        txt_recover_private_key.value = ''
        txt_recover_public_key.value = ''
        txt_recover_words.value = ''
        txt_recover_secret_key_base58.value = ''
        txt_recover_wallet_secret.value = ''
        recover_wallet_page.controls.remove(recover_solana_wallet_card)
        page.update()

    recover_solana_wallet_card_save_button = flet.TextButton(
        "Save",
        on_click=recover_solana_wallet_card_save_button_clicked,
        data=0,
    )

    async def add_address_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        value['name'] = txt_add_address_wallet_name.value
        value['description'] = txt_add_address_wallet_description.value
        value['address_base58'] = txt_add_address_wallet_address.value
        value['private_key_hex'] = ''
        value['public_key_hex'] = ''
        value['words'] = ''
        value['secret_key_base58'] = ''
        value[WATCH_ONLY_FIELD] = True

        print(f'add_address_wallet_storage >> key: {key}')
        print(f'add_address_wallet_storage >> value: {value}')

        await page.shared_preferences.set(key, json.dumps(ctx.encrypt_for_storage(value)))

        txt_add_address_error.value = ''
        txt_add_address_wallet_created.value = ''
        txt_add_address_wallet_name.value = ''
        txt_add_address_wallet_description.value = ''
        txt_add_address_wallet_address.value = ''
        add_wallet_address_page.controls.remove(add_address_solana_wallet_card)
        page.update()

    add_address_solana_wallet_card_save_button = flet.TextButton(
        "Save",
        on_click=add_address_solana_wallet_card_save_button_clicked,
        data=0,
    )

    async def generate_new_solana_wallet_card_clear_button_clicked(e):
        txt_error.value = ''
        txt_wallet_created.value = ''
        txt_wallet_name.value = ''
        txt_wallet_description.value = ''
        txt_wallet_address.value = ''
        txt_private_key.value = ''
        txt_public_key.value = ''
        txt_words.value = ''
        txt_secret_key_base58.value = ''
        if generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        if error_generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(error_generate_new_solana_wallet_card)
        page.update()

    generate_new_solana_wallet_card_clear_button = flet.TextButton(
        "Clear",
        on_click=generate_new_solana_wallet_card_clear_button_clicked,
        data=0,
    )

    async def recover_solana_wallet_card_clear_button_clicked(e):
        txt_recover_error.value = ''
        txt_recover_wallet_created.value = ''
        txt_recover_wallet_name.value = ''
        txt_recover_wallet_description.value = ''
        txt_recover_wallet_address.value = ''
        txt_recover_private_key.value = ''
        txt_recover_public_key.value = ''
        txt_recover_words.value = ''
        txt_recover_secret_key_base58.value = ''
        txt_recover_wallet_secret.value = ''
        if recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(recover_solana_wallet_card)
        if error_recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(error_recover_solana_wallet_card)
        page.update()

    recover_solana_wallet_card_clear_button = flet.TextButton(
        "Clear",
        on_click=recover_solana_wallet_card_clear_button_clicked,
        data=0,
    )

    async def add_address_solana_wallet_card_clear_button_clicked(e):
        txt_add_address_error.value = ''
        txt_add_address_wallet_created.value = ''
        txt_add_address_wallet_name.value = ''
        txt_add_address_wallet_description.value = ''
        txt_add_address_wallet_address.value = ''
        if add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(add_address_solana_wallet_card)
        if error_add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(error_add_address_solana_wallet_card)
        page.update()

    add_address_solana_wallet_card_clear_button = flet.TextButton(
        "Clear",
        on_click=add_address_solana_wallet_card_clear_button_clicked,
        data=0,
    )

    # ============================ Card definitions ==========================
    generate_new_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Created:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_created,
                    flet.Text("Wallet Name:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_name,
                    flet.Text("Wallet Description:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_description,
                    flet.Text("Wallet Address (Base58, size 44):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_wallet_address,
                    flet.Text("Secret Key (Base58, size 88, e.g. Phantom):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_secret_key_base58,
                    flet.Text("Private Key (Hex, size 64):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_private_key,
                    flet.Text("Public Key (Hex):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_public_key,
                    flet.Text("Mnemonic Words (12/24 words):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_words,
                    flet.Row(
                        [
                            generate_new_solana_wallet_card_save_button,
                            flet.TextButton("Copy", on_click=lambda e: page.run_task(copy_wallet_data_click, e, 'create')),
                            generate_new_solana_wallet_card_clear_button,
                        ],
                        alignment=flet.MainAxisAlignment.END,
                    ),
                ]
            ),
            width=400,
            padding=10,
        )
    )

    recover_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Created:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_created,
                    flet.Text("Wallet Name:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_name,
                    flet.Text("Wallet Description:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_description,
                    flet.Text("Wallet Address (Base58, size 44):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_wallet_address,
                    flet.Text("Secret Key (Base58, size 88, e.g. Phantom):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_secret_key_base58,
                    flet.Text("Private Key (Hex, size 64):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_private_key,
                    flet.Text("Public Key (Hex):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_public_key,
                    flet.Text("Mnemonic Words (12/24 words):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_recover_words,
                    flet.Row(
                        [
                            recover_solana_wallet_card_save_button,
                            flet.TextButton("Copy", on_click=lambda e: page.run_task(copy_wallet_data_click, e, 'recover')),
                            recover_solana_wallet_card_clear_button,
                        ],
                        alignment=flet.MainAxisAlignment.END,
                    ),
                ]
            ),
            width=400,
            padding=10,
        )
    )

    add_address_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Created:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_created,
                    flet.Text("Wallet Name:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_name,
                    flet.Text("Wallet Description:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_description,
                    flet.Text("Wallet Address (Base58, size 44):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD, selectable=True),
                    txt_add_address_wallet_address,
                    flet.Row(
                        [
                            add_address_solana_wallet_card_save_button,
                            flet.TextButton("Copy", on_click=lambda e: page.run_task(copy_wallet_data_click, e, 'add')),
                            add_address_solana_wallet_card_clear_button,
                        ],
                        alignment=flet.MainAxisAlignment.END,
                    ),
                ]
            ),
            width=400,
            padding=10,
        )
    )

    # ============================ Error cards ===============================
    error_generate_new_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_error,
                ]
            ),
            width=400,
            padding=10,
        )
    )

    error_recover_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_recover_error,
                ]
            ),
            width=400,
            padding=10,
        )
    )

    error_add_address_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_add_address_error,
                ]
            ),
            width=400,
            padding=10,
        )
    )

    # ===================== Create / Recover / Add ===========================
    async def generate_new_solana_wallet_button(e):
        if generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        if error_generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(error_generate_new_solana_wallet_card)
        words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error = create_solana_wallet()
        if error:
            txt_error.value = error
            create_wallet_page.controls.append(error_generate_new_solana_wallet_card)
            page.update()
            return

        txt_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        txt_wallet_name.value = input_wallet_name.value.strip()
        txt_wallet_description.value = input_wallet_description.value.strip()
        txt_wallet_address.value = wallet_address_base58
        txt_private_key.value = private_key_hex
        txt_public_key.value = public_key_hex
        txt_words.value = words
        txt_secret_key_base58.value = secret_key_base58
        page.update()

        # Backup verification: reveal the seed, then quiz the user on it
        # before allowing them to see the full secret card / save it.
        words_list = words.split()
        reveal_dlg = None  # assigned by show_reveal(); closed over by start_quiz

        async def start_quiz(ev):
            nonlocal reveal_dlg
            if reveal_dlg is not None:
                reveal_dlg.open = False
            page.update()

            positions = sorted(random.sample(range(len(words_list)), min(2, len(words_list))))
            fields = []
            quiz_rows = [
                flet.Text("Confirm your recovery phrase by entering the requested words.", size=12),
            ]
            for pos in positions:
                tf = flet.TextField(label=f"Word #{pos + 1}", min_lines=1, max_lines=1, max_length=30)
                fields.append((pos, tf))
                quiz_rows.append(tf)
            quiz_err = flet.Text("", color="red")

            async def verify(inner):
                ok = True
                for pos, tf in fields:
                    if (tf.value or "").strip().lower() != words_list[pos].lower():
                        ok = False
                if ok:
                    quiz_dlg.open = False
                    page.update()
                    create_wallet_page.controls.append(generate_new_solana_wallet_card)
                    page.update()
                else:
                    quiz_err.value = "One or more words are incorrect. Check your spelling and try again."
                    page.update()

            async def reveal_again(inner):
                quiz_dlg.open = False
                page.update()
                await show_reveal()

            quiz_dlg = flet.AlertDialog(
                modal=True,
                title=flet.Text("Verify your backup"),
                content=flet.Column(quiz_rows + [quiz_err], tight=True),
                actions=[
                    flet.TextButton("Show words again", on_click=reveal_again),
                    flet.ElevatedButton("Verify", on_click=verify),
                ],
                actions_alignment=flet.MainAxisAlignment.END,
            )
            page.show_dialog(quiz_dlg)

        async def show_reveal():
            nonlocal reveal_dlg
            reveal_rows = [
                flet.Text(
                    "These 12 words are the ONLY way to recover this wallet. "
                    "Write them down and store them safely. No one can recover them for you.",
                    size=12, color="red",
                ),
                flet.Text(words, selectable=True, size=14, weight=flet.FontWeight.BOLD),
                flet.Text("You will be asked to confirm them on the next screen.", size=12),
            ]
            reveal_dlg = flet.AlertDialog(
                modal=True,
                title=flet.Text("Your recovery phrase"),
                content=flet.Column(reveal_rows, tight=True),
                actions=[flet.ElevatedButton("I've written it down", on_click=start_quiz)],
                actions_alignment=flet.MainAxisAlignment.END,
            )
            page.show_dialog(reveal_dlg)

        await show_reveal()

    async def recover_solana_wallet_button(e):
        if recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(recover_solana_wallet_card)
        if error_recover_solana_wallet_card in recover_wallet_page.controls:
            recover_wallet_page.controls.remove(error_recover_solana_wallet_card)

        if input_recover_wallet_secret.value:
            words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_recover_wallet_secret.value.strip())
        else:
            error = 'Input the secret'

        if error:
            txt_recover_error.value = error
            recover_wallet_page.controls.append(error_recover_solana_wallet_card)
        else:
            txt_recover_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            txt_recover_wallet_name.value = input_recover_wallet_name.value.strip()
            txt_recover_wallet_description.value = input_recover_wallet_description.value.strip()
            txt_recover_wallet_secret.value = input_recover_wallet_secret.value.strip()
            txt_recover_wallet_address.value = wallet_address_base58
            txt_recover_private_key.value = private_key_hex
            txt_recover_public_key.value = public_key_hex
            txt_recover_words.value = words
            txt_recover_secret_key_base58.value = secret_key_base58
            recover_wallet_page.controls.append(recover_solana_wallet_card)
        page.update()

    async def add_address_solana_wallet_button(e):
        if add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(add_address_solana_wallet_card)
        if error_add_address_solana_wallet_card in add_wallet_address_page.controls:
            add_wallet_address_page.controls.remove(error_add_address_solana_wallet_card)

        error = ''
        if not input_add_wallet_address.value.strip():
            error = 'Input the wallet address'

        if error:
            txt_add_address_error.value = error
            add_wallet_address_page.controls.append(error_add_address_solana_wallet_card)
        else:
            txt_add_address_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            txt_add_address_wallet_name.value = input_add_address_wallet_name.value.strip()
            txt_add_address_wallet_description.value = input_add_address_wallet_description.value.strip()
            txt_add_address_wallet_address.value = input_add_wallet_address.value.strip()
            add_wallet_address_page.controls.append(add_address_solana_wallet_card)
        page.update()

    # ============================== View defs ===============================
    create_wallet_page = flet.View(
        route="create-wallet-page",
        appbar=flet.AppBar(
            title=flet.Text("Create New Wallet Page"),
            color="white",
            bgcolor="teal",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Row([flet.Text('Create New Wallet', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(content=flet.Text('Create New Wallet'), width=200, height=40, on_click=generate_new_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ],
    )

    recover_wallet_page = flet.View(
        route="recover-wallet-page",
        appbar=flet.AppBar(
            title=flet.Text("Recover Wallet Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Row([flet.Text('Recover wallet', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_secret], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(content=flet.Text('Recover Wallet'), width=200, height=40, on_click=recover_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ],
    )

    add_wallet_address_page = flet.View(
        route="add-wallet-address-page",
        appbar=flet.AppBar(
            title=flet.Text("Add Wallet Address Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Row([flet.Text('Add wallet address', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_add_address_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_add_address_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_add_wallet_address], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(content=flet.Text('Add Wallet Address'), width=200, height=40, on_click=add_address_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ],
    )

    return create_wallet_page, recover_wallet_page, add_wallet_address_page
