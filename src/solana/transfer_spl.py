from typing import Dict
import base58
import base64
import requests
import pprint

from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.transaction import Transaction, TransactionInstruction, AccountMeta
from solana.spl_token import get_token_program_id, get_associated_token_address, create_associated_token_account, TransferCheckedParams, transfer_checked, get_token_account
from solana.transfer_sol import confirm_transaction, get_blockhash



def transfer_spl_token(
    sender_address: str,
    sender_private_key: str,
    recipient_address: str,
    mint_address: str,
    amount: float,
    decimals: int,
    network: str,
    program_id: str
) -> dict:
    try:
        sender_pubkey = PublicKey(sender_address)
        recipient_pubkey = PublicKey(recipient_address)
        mint_pubkey = PublicKey(mint_address)
        sender_keypair = Keypair.from_seed(bytes.fromhex(sender_private_key))

        token_program_id_str = get_token_program_id(mint=mint_address, network=network)

        if token_program_id_str:
            program_id_pubkey = PublicKey(token_program_id_str)
        else:
            raise Exception("Failed to get_token_program_id")

        # print(f'***** token_program_id_str: {token_program_id_str}')
        # print(f'***** program_id:           {program_id_pubkey}')
        # print(f'***** get_token_program_id: {get_token_program_id(mint_address_str)}')

        # Get the sender's associated token account
        sender_token_account = get_associated_token_address(
            owner=sender_pubkey,
            mint=mint_pubkey,
            token_program_id=program_id_pubkey
        )
        print(f'***** sender_associated_token_account: {sender_token_account}')

        recipient_token_account = get_token_account(
            owner=recipient_pubkey,
            mint=mint_pubkey,
            program_id=program_id_pubkey,
            network=network
        )

        instruction_create_associated_token_account = None

        if recipient_token_account is None:
            # Get or create the associated token account for the recipient
            recipient_token_account = get_associated_token_address(
                owner=recipient_pubkey,
                mint=mint_pubkey,
                token_program_id=program_id_pubkey
            )

            instruction_create_associated_token_account = create_associated_token_account(
                    payer=sender_pubkey,
                    owner=recipient_pubkey,
                    mint=mint_pubkey,
                    token_program_id=program_id_pubkey,
                )


        print(f'***** recipient associated token account: {recipient_token_account}')

        params = TransferCheckedParams(
            program_id=program_id_pubkey,
            source=sender_token_account,
            mint=mint_pubkey,
            dest=recipient_token_account,
            owner=sender_pubkey,
            amount=int(float(amount) * (10 ** int(decimals))),
            decimals=decimals,
            signers=[sender_pubkey],
        )
        print(f'params: {params}')

        instruction_transfer = transfer_checked(params)

        latest_blockhash = get_blockhash(network)
        print(f'latest_blockhash: {latest_blockhash}')

        txn = Transaction(recent_blockhash=latest_blockhash)

        if instruction_create_associated_token_account:
            txn.add(instruction_create_associated_token_account)

        txn.add(instruction_transfer)
        txn.sign(sender_keypair)

        pprint.pp(f' instruction_create_associated_token_account: {instruction_create_associated_token_account}')
        pprint.pp(f' instruction_transfer: {instruction_transfer}')

        print(f'txn.serialize: {txn.serialize()}')
        print(f'txn.serialize base64: {base64.b64encode(txn.serialize()).decode("utf-8")}')

        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                base58.b58encode(txn.serialize()).decode("utf-8")
                # {"skipPreflight": True}
            ]
        }
        print(f'payload: {payload}')

        print("\n--- Sending Transaction ---")
        response = requests.post(url, headers=headers, json=payload)
        print(f'response sendTransaction: {response}')

        if response.status_code == 200:
            response_json = response.json()
            print('***********response_json:')
            pprint.pp(response_json)
            if 'result' in response_json:
                resp_confirm_transaction = confirm_transaction(
                    tx_sig=response_json['result'],
                    network=network,
                )
                print(f'resp_confirm_transaction: {resp_confirm_transaction}')
                return resp_confirm_transaction
            return response_json
        return {'error': f'Response status code: {response.status_code}'}

    except Exception as error:
        print(f"Failed to transfer spl token: {error}")
        return {'error': str(error)}
