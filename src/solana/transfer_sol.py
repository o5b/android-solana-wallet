from typing import List, Union, Tuple, Dict, NewType, Any, Literal
import requests
# import base64
import base58
import time

from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from solana.commitment import COMMITMENT_RANKS, Commitment, Finalized


def get_blockhash(network):
    blockhash = None
    # url = f"https://api.{network}.solana.com"
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getLatestBlockhash",
        "params": [
            {
                # "commitment": "processed"
                "commitment": "finalized"
            }
        ]
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f'get_blockhash >> response: {response}')
    # print(f'response: {dir(response)}')
    print(f'******* get_blockhash >> response.json(): {response.json()}')

    if response.status_code == 200:
        # print(f"*** Headers: {response.headers}")
        blockhash = response.json().get("result", {}).get("value", {}).get('blockhash', '')
        print(f'*** get_blockhash >> blockhash: {blockhash}')

    return blockhash


def confirm_transaction(tx_sig: str, network: str, commitment: Commitment = Finalized, sleep_seconds: float = 0.5) -> Dict:
    """Confirm the transaction identified by the specified signature.

    Args:
        tx_sig: the transaction signature to confirm.
        commitment: Bank state to query. It can be either "finalized", "confirmed" or "processed".
        sleep_seconds: The number of seconds to sleep when polling the signature status.
    """
    base58_sig = ''
    if isinstance(tx_sig, str):
        base58_sig = base58.b58encode(base58.b58decode(tx_sig.encode("ascii"))).decode("utf-8")
    else:
        base58_sig = base58.b58encode(tx_sig).decode("utf-8")
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [
            [base58_sig],
            {"searchTransactionHistory": False}
        ]
    }
    timeout = time.time() + 30
    while time.time() < timeout:
        print(f'*** TIME: {timeout - time.time()} sec')
        # resp = get_signature_statuses([tx_sig])
        response = requests.post(url, headers=headers, json=payload)
        print(f'******** confirm_transaction >> resp: {response}')
        print(f'******** confirm_transaction >> resp.json(): {response.json()}')
        resp = response.json()
        maybe_rpc_error = resp.get("error")
        if maybe_rpc_error is not None:
            raise Exception(maybe_rpc_error)
        resp_value = resp["result"]["value"][0]
        if resp_value is not None:
            confirmation_status = resp_value["confirmationStatus"]
            confirmation_rank = COMMITMENT_RANKS[confirmation_status]
            commitment_rank = COMMITMENT_RANKS[commitment]
            if confirmation_rank >= commitment_rank:
                break
        time.sleep(sleep_seconds)
    else:
        maybe_rpc_error = resp.get("error")
        if maybe_rpc_error is not None:
            raise Exception(maybe_rpc_error)
        raise Exception(f"Unable to confirm transaction {tx_sig}")
    return resp


def transfer_sol_token(
    sender_address: str,
    sender_private_key: str,
    recipient_address: str,
    amount: float,
    network: str
) -> dict:
    """
        Synchronous function to transfer tokens between wallets.

        Args:
            sender_address (str): Sender's address.
            sender_private_key (str): Sender's private key.
            recipient_address (str): Recipient's address.
            amount (float): Amount of tokens to transfer.

        Raises:
            ValueError: If any of the provided addresses is invalid or the private key is invalid.

        Returns:
            bool: True if the transfer is successful, False otherwise.
    """
    # if not is_valid_wallet_address(sender_address):
    #     raise ValueError("Invalid sender address")

    # if not is_valid_wallet_address(recipient_address):
    #     raise ValueError("Invalid recipient address")

    # if not is_valid_private_key(sender_private_key):
    #     raise ValueError("Invalid sender private key")

    # if not is_valid_amount(amount):
    #     raise ValueError("Invalid amount")

    try:
        sender_kp = Keypair.from_seed(bytes.fromhex(sender_private_key))

        txn = Transaction()
        txn.recent_blockhash = get_blockhash(network)
        txn.fee_payer = PublicKey(sender_address)
        txn.add(
            transfer(
                TransferParams(
                    from_pubkey=PublicKey(sender_address),
                    to_pubkey=PublicKey(recipient_address),
                    lamports=int(float(amount) * 1_000_000_000),
                )
            )
        )
        txn.sign(sender_kp)

        # print(f"base58.b58encode(txn.serialize()).decode('utf-8'): {base58.b58encode(txn.serialize()).decode('utf-8')}")
        # url = f"https://api.{network}.solana.com"

        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                base58.b58encode(txn.serialize()).decode("utf-8")
            ]
        }
        print(f'payload: {payload}')

        response = requests.post(url, headers=headers, json=payload)
        print(f'response sendTransaction: {response}')

        if response.status_code == 200:
            response_json = response.json()
            print(f'******* sendTransaction response.json(): {response_json}')
            if 'result' in response_json:
                resp_confirm_transaction = confirm_transaction(tx_sig=response_json['result'], network=network)
                print(f'******* transfer_sol_token >> resp_confirm_transaction: {resp_confirm_transaction}')
                return resp_confirm_transaction
            return response_json
        return {'error': f'Response status code: {response.status_code}'}

    except Exception as error:
        # detailed_error_traceback = traceback.format_exc()
        print(f"Failed transfer sol token: {error}")
        return {'error': error}
    # finally:
        # await client.close()


def get_min_sol_balance(network: str) -> float | None:
    """
        Retrieves minimum sol balance for a token's transfer.

        Arguments:
        network (str):

        Returns:
        int|None: minimum sol.
    """
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMinimumBalanceForRentExemption",
        "params": [
            50
        ]
    }
    try:
        for attempt in range(5):
            try:
                response = requests.post(url, headers=headers, json=payload)
                print(f'*** response getMinimumBalanceForRentExemption: {response}')
                if response.status_code == 200:
                    response_data = response.json()
                    maybe_rpc_error = response_data.get("error")
                    if maybe_rpc_error is not None:
                        print(f'Error getMinimumBalanceForRentExemption: {maybe_rpc_error}')
                        continue
                    if 'result' in response_data:
                        min_sol_balance = response_data.get('result')
                        return float(min_sol_balance) / 1_000_000_000
            except Exception as e:
                print(f"Error to get min_sol_balance: {e}. Attempt {attempt + 1} out of 5.")
                time.sleep(10)
        else:
            print("Failed to get min_sol_balance after 5 attempts.")
            return None
    except Exception as e:
        print(f"Failed to get min_sol_balance: {e}")
        return None
