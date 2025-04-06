from datetime import datetime
import time
import flet

from solana.create_wallet import create_solana_wallet
from solana.balance import get_sol_spl_balance, get_sol_balance
from solana.transfer_sol import transfer_sol_token, get_min_sol_balance
from solana.validators import is_valid_amount, is_valid_wallet_address, is_valid_private_key, is_valid_wallet_seed_phrase

# LAMPORT_TO_SOL_RATIO = 10 ** 9

def main(page: flet.Page):
    page.scroll = flet.ScrollMode.AUTO
    page.title = "Solana Wallet Generator"
    page.vertical_alignment = flet.MainAxisAlignment.CENTER
    page.horizontal_alignment = flet.CrossAxisAlignment.CENTER
    page.bgcolor = 'white'
    page.padding = flet.padding.only(top=50, left=10, right=10)
    # page.scroll = flet.ScrollMode.AUTO
    # page.theme_mode = flet.ThemeMode.LIGHT

    if page.client_storage.contains_key("theme_mode"):
        if page.client_storage.get("theme_mode") == 'LIGHT':
            page.theme_mode = flet.ThemeMode.LIGHT
        elif page.client_storage.get("theme_mode") == 'DARK':
            page.theme_mode = flet.ThemeMode.DARK
    else:
        page.theme_mode = flet.ThemeMode.LIGHT
        page.client_storage.set("theme_mode", "LIGHT")

    input_wallet_name = flet.TextField(label="Wallet Name", min_lines=1, max_lines=1, max_length=50)
    input_wallet_description = flet.TextField(label="Wallet description", min_lines=2, max_lines=5, max_length=200)

    txt_wallet_name = flet.TextField()
    txt_wallet_description = flet.TextField()
    # txt_wallet_name = flet.Text()
    # txt_wallet_description = flet.Text()
    txt_wallet_address = flet.Text(selectable=True)
    txt_private_key = flet.Text(selectable=True)
    txt_secret_key_base58 = flet.Text(selectable=True)
    txt_public_key = flet.Text(selectable=True)
    txt_words = flet.Text(selectable=True)
    # txt_seed = flet.Text()
    txt_error = flet.Text(selectable=True)
    txt_wallet_created = flet.Text(selectable=True)

    input_recover_wallet_name = flet.TextField(label="Wallet Name", min_lines=1, max_lines=1, max_length=50)
    input_recover_wallet_description = flet.TextField(label="Wallet description", min_lines=2, max_lines=5, max_length=200)
    input_recover_wallet_secret = flet.TextField(label="Wallet Secret Words (12/24) or Secret Key base58 (length=88)", min_lines=2, max_lines=5, max_length=200)

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

    input_add_address_wallet_name = flet.TextField(label="Wallet Name", min_lines=1, max_lines=1, max_length=50)
    input_add_address_wallet_description = flet.TextField(label="Wallet description", min_lines=2, max_lines=5, max_length=200)
    input_add_wallet_address = flet.TextField(label="Add Wallet Address (base58) ", min_lines=2, max_lines=5, max_length=200)

    txt_add_address_wallet_name = flet.TextField()
    txt_add_address_wallet_description = flet.TextField()
    txt_add_address_wallet_address = flet.Text(selectable=True)
    # txt_recover_public_key = flet.Text(selectable=True)
    txt_add_address_error = flet.Text(selectable=True)
    txt_add_address_wallet_created = flet.Text(selectable=True)

    def get_storage_data(prefix=''):
        data_list = []
        keys = page.client_storage.get_keys(prefix)
        print(f'keys: {keys}')
        for key in keys:
            data_list.append(page.client_storage.get(key))
        print(f'data_list: {data_list}')
        return data_list

    def get_wallets_cards():
        wallets = get_storage_data(prefix="wallet.")
        print(f'wallets: {wallets}')
        lv = flet.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)
        for wallet in wallets:
            lv.controls.append(
                flet.Card(
                    content=flet.Container(
                        content=flet.Column(
                            [
                                flet.Text(
                                    "Wallet Name: ",
                                    size=16,
                                    font_family="Georgia",
                                    # weight=flet.FontWeight.BOLD,
                                    text_align=flet.TextAlign.RIGHT,
                                    spans=[
                                        flet.TextSpan(f'{wallet['name']}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                                    ],
                                ),
                                flet.Text(
                                    "Wallet Description: ",
                                    size=16,
                                    font_family="Georgia",
                                    # weight=flet.FontWeight.BOLD,
                                    text_align=flet.TextAlign.RIGHT,
                                    spans=[
                                        flet.TextSpan(f'{wallet['description']}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                                    ],
                                ),
                                flet.Text(
                                    "Address: ",
                                    size=16,
                                    font_family="Georgia",
                                    # weight=flet.FontWeight.BOLD,
                                    selectable=True,
                                    spans=[
                                        flet.TextSpan(f'{wallet['address_base58']}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                                    ]
                                ),
                                flet.Divider(thickness=1),
                                flet.Row(
                                    [
                                        flet.ElevatedButton(
                                            text="Show More",
                                            on_click=go_to_address_page,
                                            data=wallet,
                                        ),
                                        # flet.Text("Real Network", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                                    ],
                                    alignment=flet.MainAxisAlignment.START,
                                ),
                                # flet.Column([]),
                            ]
                        ),
                        width=400,
                        padding=10,
                    )
                )
            )
        return lv

    el_address_page = flet.Column()
    el_token_balance_data = flet.Column()

    def go_to_address_page(e):
        print(f'****** go_to_address_page e.control.data: {e.control.data}')
        wallet = e.control.data
        el_address_page.controls = [
            flet.Row(
                [
                    flet.Text(
                        "Wallet Name: ",
                        size=16,
                        font_family="Georgia",
                        text_align=flet.TextAlign.RIGHT,
                        spans=[
                            flet.TextSpan(f'{wallet["name"]}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                        ],
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        "Wallet Description: ",
                        size=16,
                        font_family="Georgia",
                        text_align=flet.TextAlign.RIGHT,
                        spans=[
                            flet.TextSpan(f'{wallet["description"]}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                        ],
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        "",
                        font_family="Georgia",
                        selectable=True,
                        text_align=flet.TextAlign.RIGHT,
                        spans=[
                            flet.TextSpan('Created: ', flet.TextStyle(size=16)),
                            flet.TextSpan(f'{wallet["created"]}', flet.TextStyle(size=12, weight=flet.FontWeight.BOLD,)),
                        ]
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        'Address: ',
                        size=16,
                        text_align=flet.TextAlign.RIGHT,
                        font_family="Georgia",
                    ),
                ]
            ),
            flet.Row(
                [
                    flet.Text(
                        f'{wallet["address_base58"]}',
                        size=12,
                        font_family="Georgia",
                        weight=flet.FontWeight.BOLD,
                        text_align=flet.TextAlign.RIGHT,
                        selectable=True,
                    ),
                ]
            ),
            flet.Divider(thickness=2),
            flet.Row([flet.Text("Solana Networks:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),]),
            flet.Row(
                [
                    flet.Column(
                        [
                            flet.Checkbox(label="mainnet-beta (real network)", value=True),
                            flet.Checkbox(label="testnet (not a real network)", value=False),
                            flet.Checkbox(label="devnet (not a real network)", value=False),
                        ]
                    ),
                ],
                alignment=flet.MainAxisAlignment.START,
            ),
            flet.Row(
                [
                    flet.ElevatedButton(
                        text="Show Balance",
                        on_click=get_balance_button_click,
                        # data=wallet['address_base58'],
                        data=wallet,
                    ),
                ],
                alignment=flet.MainAxisAlignment.END,
            ),
            el_token_balance_data,
        ]
        page.go("address-page")

    def get_balance_button_click(e):
        try:
            wallet = e.control.data
            print(f'****** address >> get_balance_button_click: {wallet}')
            el_token_balance_data.controls.clear()
            page.update()
            networks = []       # ["mainnet-beta", "testnet", "devnet"]
            if e.control.parent.parent.controls[-3].controls[0].controls[0].value:
                # networks.append("mainnet-beta")
                networks.append("https://api.mainnet-beta.solana.com")
            if e.control.parent.parent.controls[-3].controls[0].controls[1].value:
                # networks.append("testnet")
                networks.append("https://api.testnet.solana.com")
            if e.control.parent.parent.controls[-3].controls[0].controls[2].value:
                # networks.append("devnet")
                networks.append("https://api.devnet.solana.com")
            print(f'networks: {networks}')
            e.control.disabled = True   # блокируем кнопку
            el_token_balance_data.controls.append(
                flet.Row([flet.ProgressRing(), flet.Text("PLEASE WAIT")], alignment=flet.MainAxisAlignment.CENTER)
            )
            page.update()
            tmp_balance_result = []
            start = datetime.now()
            result = get_sol_spl_balance(wallet['address_base58'], networks)
            print(f'****** result: {result}')
            for i, r in enumerate(result):
                tmp_balance_spl = []
                for spl_token in r['spl']:
                    token_symbol = ''
                    if 'symbol_metaplex' in spl_token:
                        token_symbol += f'{spl_token['symbol_metaplex']} (symbol_metaplex) '
                    if 'symbol_2022' in spl_token:
                        token_symbol += f'{spl_token['symbol_2022']} (symbol_2022)'
                    tmp_balance_spl.extend(
                        [
                            flet.Row(
                                [
                                    flet.ElevatedButton(
                                        text="Transfer this token",
                                        # on_click=get_balance_detail_button_click,
                                        data={
                                            # 'wallet_address': e.control.data,
                                            'wallet_address': wallet['address_base58'],
                                            'network': r['network'],
                                            'spl_amount': spl_token['amount'],
                                            'symbol': token_symbol,
                                            'sol_amount': r['sol'],
                                            'raw_data': spl_token,
                                            'wallet_data': wallet,
                                        },
                                        # disabled=False if (r['sol'] and spl_token['amount']) else True,
                                        disabled=True,
                                    ),
                                    flet.Text(
                                        value='',
                                        spans=[
                                            flet.TextSpan(f'{spl_token['amount']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                            flet.TextSpan(f' {token_symbol}', flet.TextStyle(size=16)),
                                        ]
                                    ),
                                ],
                            ),
                            flet.Row(
                                scroll=flet.ScrollMode.AUTO,
                                controls=[
                                    flet.Text(
                                        value=f'{spl_token}',
                                        size=12,
                                    ),
                                ],
                            ),
                        ]
                    )
                tmp_balance_result.extend(
                    [
                        flet.Row(
                            [
                                flet.Text(
                                    value='',
                                    spans=[flet.TextSpan(f'Network: {r['network']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD))]
                                ),
                            ],
                        ),
                        flet.Row(
                            [
                                flet.ElevatedButton(
                                    text="Transfer this token",
                                    on_click=go_to_token_page_button_click,
                                    data={
                                        # 'wallet_address': e.control.data,
                                        'wallet_address': wallet['address_base58'],
                                        'network': r['network'],
                                        'sol_amount': r['sol'],
                                        'symbol': 'SOL',
                                        'wallet_data': wallet,
                                    },
                                    disabled=False if r['sol'] else True,
                                ),
                                flet.Text(
                                    value='',
                                    spans=[
                                        flet.TextSpan(f'{r['sol']}', flet.TextStyle(size=16, weight=flet.FontWeight.BOLD)),
                                        flet.TextSpan(' SOL', flet.TextStyle(size=16)),
                                    ]
                                ),
                            ],
                        ),
                        *tmp_balance_spl,
                    ]
                )
                if i < len(result) - 1:     # добавляем разделяющую линию после каждого результата кроме последнего
                    tmp_balance_result.append(flet.Divider(thickness=1))
            el_token_balance_data.controls.clear()
            el_token_balance_data.controls.extend([flet.Divider(thickness=3), *tmp_balance_result])
            e.control.disabled = False  # разблокируем кнопку
            print(f'time: {datetime.now() - start} sec')
            page.open(
                flet.AlertDialog(
                    title=flet.Text(f"Balance for {wallet['address_base58']} received successfully!"),
                )
            )
        except Exception as er:
            print(f'Error get_balance_button_click: {er}')
            page.open(
                flet.AlertDialog(
                    title=flet.Text("Error get_balance_button_click!"),
                )
            )
        finally:
            page.update()

    el_token_page = flet.Column()

    def go_to_token_page_button_click(e):
        print(f'****** go_to_token_page_button_click >> e.control.data: {e.control.data}')
        data = e.control.data
        el_token_page.controls.clear()
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
                        flet.TextField(label="Enter the recipient's address", min_lines=1, max_lines=1, max_length=100)
                    ],
                ),
                flet.Row(
                    [
                        flet.ElevatedButton(
                            text="Transfer SOL",
                            on_click=transfer_sol_button_click,
                            data=data,
                        ),
                    ],
                ),
                flet.Column(),
            ]
        )
        if not data['wallet_data']['private_key_hex']:
            el_token_page.controls.insert(
                5,
                flet.Row(
                    [
                        flet.TextField(label="Enter Secret (12/24 Words or Private Key)", min_lines=1, max_lines=1, max_length=100)
                    ],
                )
            )
        page.go("token-page")

    def transfer_sol_button_click(e):
        data = e.control.data
        # print(f'****** transfer_sol_button_click >> e.control.data: {data}')
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

        if not data['wallet_data']['private_key_hex']:
            input_secret = e.control.parent.parent.controls[5].controls[0].value.strip()
            if is_valid_wallet_seed_phrase(input_secret):
                # преобразовать секретные слова 12/24 в приватный ключ в hex формате
                for attempt in range(10):
                    words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error = create_solana_wallet(secret=input_secret)
                    if wallet_address_base58 == data['wallet_data']['address_base58']:
                        data['wallet_data']['private_key_hex'] = private_key_hex
                        break
                    elif error:
                        alert_dialog_text = f"Error after: {attempt} attempts to get private key from secret words: {input_secret}! Error Msg: {error}"
                else:
                    alert_dialog_text = f'Failed to get private key after: {attempt} attempts from secret words: {input_secret}'
            elif is_valid_private_key(input_secret):
                if len(input_secret) == 64:
                    data['wallet_data']['private_key_hex'] = input_secret
            else:
                alert_dialog_text = "Error Secret!"

        if data['wallet_data']['private_key_hex']:
            recipient_address = e.control.parent.parent.controls[4].controls[0].value
            # print(f'**** recipient: {recipient_address}')
            if is_valid_wallet_address(recipient_address):

                transfer_sol_amount = e.control.parent.parent.controls[3].controls[0].value
                # print(f'**** transfer_sol_button_click >> SOL: {transfer_sol_amount}')
                if is_valid_amount(transfer_sol_amount):
                    transfer_sol_amount = float(transfer_sol_amount)

                    min_sol_balance = get_min_sol_balance(data['network'])
                    # print(f'**** min_sol_balance: {min_sol_balance}')
                    if min_sol_balance is None:
                        min_sol_balance = 0

                    if (transfer_sol_amount > 0) and (transfer_sol_amount < data['sol_amount'] - min_sol_balance):
                        result = transfer_sol_token(
                            sender_address=data['wallet_data']['address_base58'],
                            sender_private_key=data['wallet_data']['private_key_hex'],
                            recipient_address=recipient_address,
                            amount=transfer_sol_amount,
                            network=data['network']
                        )
                        # print(f'****** RESULT: {result}')

                        if 'result' in result:
                            sol_balance_after = get_sol_balance(address=data['wallet_data']['address_base58'], network=data['network'])
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
        page.open(
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


    def generate_new_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        value['name'] = txt_wallet_name.value
        value['description'] = txt_wallet_description.value
        value['address_base58'] = txt_wallet_address.value
        value['private_key_hex'] = txt_private_key.value
        value['public_key_hex'] = txt_public_key.value
        value['words'] = txt_words.value
        value['secret_key_base58'] = txt_secret_key_base58.value

        print(f'generate_new_wallet_storage >> key: {key}')
        print(f'generate_new_wallet_storage >> value: {value}')

        page.client_storage.set(key, value)

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


    def recover_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        value['name'] = txt_recover_wallet_name.value
        value['description'] = txt_recover_wallet_description.value
        value['address_base58'] = txt_recover_wallet_address.value
        value['private_key_hex'] = txt_recover_private_key.value
        value['public_key_hex'] = txt_recover_public_key.value
        value['words'] = txt_recover_words.value
        value['secret_key_base58'] = txt_recover_secret_key_base58.value

        print(f'recover_wallet_storage >> key: {key}')
        print(f'recover_wallet_storage >> value: {value}')

        page.client_storage.set(key, value)

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


    def add_address_solana_wallet_card_save_button_clicked(e):
        key = f"wallet.{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        value = {}
        value['created'] = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        value['name'] = txt_add_address_wallet_name.value
        value['description'] = txt_add_address_wallet_description.value
        value['address_base58'] = txt_add_address_wallet_address.value
        value['private_key_hex'] = ''
        value['public_key_hex'] = ''
        value['words'] = ''
        value['secret_key_base58'] = ''

        print(f'add_address_wallet_storage >> key: {key}')
        print(f'add_address_wallet_storage >> value: {value}')

        page.client_storage.set(key, value)

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

    def generate_new_solana_wallet_card_clear_button_clicked(e):
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

    def recover_solana_wallet_card_clear_button_clicked(e):
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

    def add_address_solana_wallet_card_clear_button_clicked(e):
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
                    # flet.Text("Seed (Hex):", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    # txt_seed,
                    flet.Row(
                        [
                            generate_new_solana_wallet_card_save_button,
                            flet.TextButton("Copy"),
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
                            flet.TextButton("Copy"),
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
                            flet.TextButton("Copy"),
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

    error_generate_new_solana_wallet_card = flet.Card(
        content=flet.Container(
            content=flet.Column(
                [
                    flet.Text("Error:", size=16, font_family="Georgia", weight=flet.FontWeight.BOLD),
                    txt_error
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
                    txt_recover_error
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
                    txt_recover_error
                ]
            ),
            width=400,
            padding=10,
        )
    )

    def generate_new_solana_wallet_button(e):
        if generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(generate_new_solana_wallet_card)
        if error_generate_new_solana_wallet_card in create_wallet_page.controls:
            create_wallet_page.controls.remove(error_generate_new_solana_wallet_card)
        words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error = create_solana_wallet()
        if error:
            txt_error.value = error
            create_wallet_page.controls.append(error_generate_new_solana_wallet_card)
        else:
            txt_wallet_created.value = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            txt_wallet_name.value = input_wallet_name.value.strip()
            txt_wallet_description.value = input_wallet_description.value.strip()
            txt_wallet_address.value = wallet_address_base58
            txt_private_key.value = private_key_hex
            txt_public_key.value = public_key_hex
            txt_words.value = words
            # txt_seed.value = seed_hex
            txt_secret_key_base58.value = secret_key_base58
            create_wallet_page.controls.append(generate_new_solana_wallet_card)
        page.update()

    def recover_solana_wallet_button(e):
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

    def add_address_solana_wallet_button(e):
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

    def theme_changed(e):
        page.theme_mode = flet.ThemeMode.DARK if page.theme_mode == flet.ThemeMode.LIGHT else flet.ThemeMode.LIGHT
        theme_control.label = "Light theme" if page.theme_mode == flet.ThemeMode.LIGHT else "Dark theme"
        if page.theme_mode == flet.ThemeMode.LIGHT:
            page.client_storage.set("theme_mode", "LIGHT")
        else:
            page.client_storage.set("theme_mode", "DARK")
        page.update()

    theme_control = flet.Switch(label="Light theme", on_change=theme_changed)

    def dev_tools_storage_list():
        lv = flet.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)
        for i, key in enumerate(page.client_storage.get_keys('')):
            val = page.client_storage.get(key)
            lv.controls.append(
                flet.Row(
                    scroll=flet.ScrollMode.AUTO,
                    controls=[
                        flet.ElevatedButton(text="Delete", on_click=storage_delete_button_click, data=key),
                        flet.Text(f"{i+1}. {key}: {val}", max_lines=2),
                    ]
                )
            )
        return lv

    def storage_delete_button_click(e):
        try:
            page.client_storage.remove(e.control.data)
            page.open(
                flet.AlertDialog(
                    title=flet.Text(f"{e.control.data} успешно удалён!"),
                )
            )
        except Exception as er:
            print(f'Error deleted data from client_storage: {er}')
            page.open(
                flet.AlertDialog(
                    title=flet.Text("Во время удаления произошла ошибка!"),
                )
            )
        page.update()

    def clear_client_storage():
        for key in page.client_storage.get_keys(''):
            page.client_storage.remove(key)

    def selected_drawer(e):
        print(f'e.control.selected_index: {e.control.selected_index}')
        if e.control.selected_index == 0:
            # page.go('settings-page')
            pass
        elif e.control.selected_index == 3:
            page.go('dev-storage-page')
        elif e.control.selected_index == 4:
            clear_client_storage()

    drawer = flet.NavigationDrawer(
        # on_dismiss=handle_dismissal,
        on_change=selected_drawer,
        controls=[
            flet.Container(height=20),
            theme_control,
            flet.NavigationDrawerDestination(
                label="Item 1",
                icon=flet.Icons.DOOR_BACK_DOOR_OUTLINED,
                selected_icon=flet.Icon(flet.Icons.DOOR_BACK_DOOR),
            ),
            flet.Divider(thickness=2),
            flet.NavigationDrawerDestination(
                icon=flet.Icon(flet.Icons.MAIL_OUTLINED),
                label="Item 2",
                selected_icon=flet.Icons.MAIL,
            ),
            flet.NavigationDrawerDestination(
                icon=flet.Icon(flet.Icons.PHONE_OUTLINED),
                label="Item 3",
                selected_icon=flet.Icons.PHONE,
            ),
            flet.Divider(thickness=2),
            flet.Text("DevTools:", size=20, text_align=flet.TextAlign.CENTER),
            flet.NavigationDrawerDestination(
                label="Storage",
                icon=flet.Icons.STORAGE,
                selected_icon=flet.Icon(flet.Icons.STORAGE_OUTLINED),
            ),
            flet.NavigationDrawerDestination(
                label="Clear Storage",
                icon=flet.Icons.DEVELOPER_MODE,
                selected_icon=flet.Icon(flet.Icons.DEVELOPER_MODE_OUTLINED),
            ),
        ],
    )

    def selected_navbar(e):
        print(f'e.control.selected_index: {e.control.selected_index}')
        if e.control.selected_index == 1:
            page.go("create-wallet-page")
        elif e.control.selected_index == 2:
            page.go("recover-wallet-page")
        elif e.control.selected_index == 3:
            page.go("add-wallet-address-page")
        else:
            page.go("/")

    navbar = flet.NavigationBar(
        on_change = selected_navbar,
        destinations=[
            flet.NavigationDrawerDestination(
                label="Menu",
                icon=flet.Icon(flet.Icons.GRID_VIEW_ROUNDED),
            ),
            flet.NavigationDrawerDestination(
                label="New",
                icon=flet.Icon(flet.Icons.CODE),
            ),
            flet.NavigationDrawerDestination(
                label="Recover",
                icon=flet.Icon(flet.Icons.ROCKET_LAUNCH_OUTLINED),
            ),
            flet.NavigationDrawerDestination(
                label="Add",
                icon=flet.Icon(flet.Icons.CODE),
            ),
            flet.NavigationDrawerDestination(
                label="Exit",
                icon=flet.Icon(flet.Icons.CANCEL),
            ),
       ]
    )

    def route_change(route):
        page.views.clear()
        page.views.append(homepage)
        if page.route == "create-wallet-page":
            page.views.append(create_wallet_page)
        elif page.route == "recover-wallet-page":
            page.views.append(recover_wallet_page)
        elif page.route == "add-wallet-address-page":
            page.views.append(add_wallet_address_page)
        elif page.route == "dev-storage-page":
            page.views.append(dev_storage_page)
        elif page.route == "address-page":
            el_token_balance_data.controls.clear()
            page.views.append(address_page)
        elif page.route == "token-page":
            page.views.append(token_page)
        # else:
        #     page.views.append(homepage)
        page.update()

    def view_pop(view):
        print(f'########### start >> page.views >> len={len(page.views)}, page.views: {page.views}')
        page.views.pop()
        print(f'########### after pop() >> page.views >> len={len(page.views)}, page.views: {page.views}')
        top_view = page.views[-1]
        page.go(top_view.route)

    recover_wallet_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            padding=5,
            content=flet.Column(controls=[flet.Image(src="recover.png"), flet.Text('Recover Wallet', size=12)])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=lambda _:page.go("recover-wallet-page")
    )

    add_wallet_address_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            padding=5,
            content=flet.Column(controls=[flet.Image(src="add.png"), flet.Text('Add Wallet Address', size=12)])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=lambda _:page.go("add-wallet-address-page")
    )

    create_wallet_button = flet.OutlinedButton(
        height=100,
        width=100,
        content=flet.Container(
            padding=5,
            content=flet.Column(controls=[flet.Image(src="create.png"), flet.Text('New Wallet')])
        ),
        style=flet.ButtonStyle(shape=flet.RoundedRectangleBorder(radius=10)),
        on_click=lambda _:page.go('create-wallet-page')
    )

    button_group_1 = flet.Row(
        width=page.width,
        alignment=flet.MainAxisAlignment.SPACE_EVENLY,
        controls=[
            create_wallet_button,
            recover_wallet_button,
            add_wallet_address_button,
        ]
    )

    homepage = flet.View(
        route="/",
        appbar=flet.AppBar(
            bgcolor="#1da1f2",
            color="white",
            title=flet.Text("HomePage"),
        ),
        navigation_bar=navbar,
        drawer=drawer,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            # flet.Image(src="solana.jpg", width=page.width, height=200, fit=flet.ImageFit.FILL),
            flet.Text('Solana', size=30, font_family="Georgia", weight=flet.FontWeight.BOLD),
            button_group_1,
            flet.Text('Wallets:', size=30, font_family="Georgia", weight=flet.FontWeight.BOLD),
            get_wallets_cards(),
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
            # flet.Text('Recover wallet', size=30, font_family="Georgia"),
            flet.Row([flet.Text('Recover wallet', size=30, font_family="Georgia")], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_recover_wallet_secret], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(text='Recover Wallet', width=200, height=40, on_click=recover_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ]
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
                    flet.OutlinedButton(text='Add Wallet Address', width=200, height=40, on_click=add_address_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ]
    )

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
            # generate_new_solana_wallet_card,
            flet.Row([input_wallet_name], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row([input_wallet_description], alignment=flet.MainAxisAlignment.CENTER),
            flet.Row(
                [
                    flet.OutlinedButton(text='Create New Wallet', width=200, height=40, on_click=generate_new_solana_wallet_button)
                ],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
        ]
    )

    dev_storage_page = flet.View(
        route="dev-storage-page",
        appbar=flet.AppBar(
            title=flet.Text("DevTools: Storage"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text(value='Редактирование client_storage:', size=20),
            dev_tools_storage_list(),
        ]
    )

    address_page = flet.View(
        route="address-page",
        appbar=flet.AppBar(
            title=flet.Text("Address Page"),
            color="white",
            bgcolor="cyan",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=view_pop),
        ),
        navigation_bar=navbar,
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text('Information:', size=30, font_family="Georgia"),
            el_address_page,
        ]
    )

    token_page = flet.View(
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
            el_token_page,
        ]
    )

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    page.go(page.route)
    page.update()


flet.app(target=main)