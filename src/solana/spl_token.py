import base58
import base64
import httpx
import asyncio
import time
from typing import List, Dict, Any, NamedTuple
import pprint

from solana.balance import get_account_info
from solana.publickey import PublicKey
from solana.transaction import Transaction, TransactionInstruction, AccountMeta
from solana.layouts import INSTRUCTIONS_LAYOUT, InstructionType
from solana.commitment import Commitment, Finalized
from solana.transfer_sol import confirm_transaction, get_blockhash

# Program IDs
TOKEN_PROGRAM_ID = PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM_ID = PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ASSOCIATED_TOKEN_PROGRAM_ID = PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM_ID = PublicKey("11111111111111111111111111111111")
SYS_PROGRAM_ID: PublicKey = PublicKey("11111111111111111111111111111111")
SYSVAR_RENT_PUBKEY: PublicKey = PublicKey("SysvarRent111111111111111111111111111111111")


async def get_token_program_id(mint: str, network: str) -> str | None:
    try:
        for attempt in range(5):
            try:
                response = await get_account_info(address=mint, network=network)
                break
            except Exception as e:
                print(f"Error when get_token_program_id: {e}. Attempt {attempt + 1} out of 5.")
                await asyncio.sleep(10)
        else:
            raise Exception("Failed to get_token_program_id after 5 attempts.")

        if response and 'owner' in response:
            return response['owner']
        return None
    except Exception as error:
        print(f"Failed to get_token_program_id: {error}")
        return None

def get_associated_token_address(owner: PublicKey, mint: PublicKey, token_program_id: PublicKey = TOKEN_PROGRAM_ID) -> PublicKey:
    if token_program_id not in [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID]:
        raise ValueError("token_program_id must be one of TOKEN_PROGRAM_ID or TOKEN_2022_PROGRAM_ID.")
    key, _ = PublicKey.find_program_address(
        seeds=[bytes(owner), bytes(token_program_id), bytes(mint)],
        program_id=ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return key

def create_associated_token_account(
    payer: PublicKey, owner: PublicKey, mint: PublicKey, token_program_id: PublicKey = TOKEN_PROGRAM_ID
) -> TransactionInstruction:
    if token_program_id not in [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID]:
        raise ValueError("token_program_id must be one of TOKEN_PROGRAM_ID or TOKEN_2022_PROGRAM_ID.")
    associated_token_address = get_associated_token_address(owner, mint, token_program_id)
    return TransactionInstruction(
        keys=[
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=associated_token_address, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner, is_signer=False, is_writable=False),
            AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYS_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=token_program_id, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYSVAR_RENT_PUBKEY, is_signer=False, is_writable=False),
        ],
        program_id=ASSOCIATED_TOKEN_PROGRAM_ID,
        data=bytes(0),
    )

class TransferCheckedParams(NamedTuple):
    program_id: PublicKey
    source: PublicKey
    mint: PublicKey
    dest: PublicKey
    owner: PublicKey
    amount: int
    decimals: int
    signers: List[PublicKey] = []

def __add_signers(keys: List[AccountMeta], owner: PublicKey, signers: List[PublicKey]) -> None:
    if signers:
        keys.append(AccountMeta(pubkey=owner, is_signer=False, is_writable=False))
        for signer in signers:
            keys.append(AccountMeta(pubkey=signer, is_signer=True, is_writable=False))
    else:
        keys.append(AccountMeta(pubkey=owner, is_signer=True, is_writable=False))

def transfer_checked(params: TransferCheckedParams) -> TransactionInstruction:
    data = INSTRUCTIONS_LAYOUT.build(
        {
            "instruction_type": InstructionType.TRANSFER2,
            "args": {"amount": params.amount, "decimals": params.decimals},
        }
    )
    keys = [
        AccountMeta(pubkey=params.source, is_signer=False, is_writable=True),
        AccountMeta(pubkey=params.mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=params.dest, is_signer=False, is_writable=True),
    ]
    __add_signers(keys, params.owner, params.signers)
    return TransactionInstruction(keys=keys, program_id=params.program_id, data=data)

async def get_token_account(owner: PublicKey, mint: PublicKey, program_id: PublicKey, network: str) -> PublicKey | None:
    try:
        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                f"{owner}",
                {"programId": f"{program_id}"},
                {"commitment": "finalized", "encoding": "jsonParsed"}
            ]
        }
        print(f'payload: {payload}')
        print("\n--- Sending getTokenAccountsByOwner ---")

        async with httpx.AsyncClient() as client:
            for attempt in range(5):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    break
                except Exception as e:
                    print(f"Error get_token_account for the owner: {owner}. Error msg.: {e}. Attempt {attempt + 1} out of 5.")
                    await asyncio.sleep(3)
            else:
                raise Exception(f"Failed to get_token_account: {mint} from owner: {owner} after 5 attempts.")

        print(f'response getTokenAccountsByOwner: {response}')

        if response.status_code == 200:
            response_json = response.json()
            print('***********response_json:')
            pprint.pp(response_json)
            if response_json and "result" in response_json:
                if "value" in response_json["result"]:
                    accounts = response_json["result"]["value"]
                    for account in accounts:
                        try:
                            account_mint = account["account"]["data"]["parsed"]["info"]["mint"]
                            if account_mint == f"{mint}":
                                print(f"account pubkey: {account['pubkey']}")
                                return PublicKey(account["pubkey"])
                        except KeyError:
                            continue
        else:
            print(f"Failed to get_token_account: {mint} from owner: {owner}.")
        return None

    except Exception as error:
        print(f"Failed to get_token_account: {error}")
        return None

async def request_airdrop(pubkey: PublicKey, lamports: int, network: str, commitment: Commitment = Finalized):
    print(f'****** request_airdrop >> commitment: {commitment}')
    try:
        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "requestAirdrop",
            "params": [
                f"{pubkey}",
                lamports,
                {"commitment": commitment}
            ]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)

        print(f'response requestAirdrop: {response}')

        if response.status_code == 200:
            response_json = response.json()
            if response_json and "result" in response_json and response_json["result"]:
                try:
                    confirm_tx_res = await confirm_transaction(tx_sig=response_json["result"], network=network)
                    print(f'confirm_transaction: {confirm_tx_res}')
                except Exception as er:
                    print(f"Failed to confirm_transaction: {er}")
                return response_json["result"]
            elif "error" in response_json:
                return f'Error: {response_json["error"]}'
            else:
                return response_json

        elif response.status_code == 429:
            response_json = response.json()
            if response_json and "error" in response_json:
                return f'Error: {response_json["error"]}, retry-after: {response.headers.get("retry-after", "N/A")} seconds.'
            elif 'retry-after' in response.headers:
                return f'Error: Too Many Requests, retry-after: {response.headers["retry-after"]} seconds.'

        return None
    except Exception as error:
        print(f"Failed request_airdrop: {error}")
        return f"Failed request_airdrop: {error}"

from solana.keypair import Keypair

async def transfer_spl_token(
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

        token_program_id_str = await get_token_program_id(mint=mint_address, network=network)

        if token_program_id_str:
            program_id_pubkey = PublicKey(token_program_id_str)
        else:
            raise Exception("Failed to get_token_program_id")

        sender_token_account = get_associated_token_address(
            owner=sender_pubkey,
            mint=mint_pubkey,
            token_program_id=program_id_pubkey
        )
        print(f'***** sender_associated_token_account: {sender_token_account}')

        recipient_token_account = await get_token_account(
            owner=recipient_pubkey,
            mint=mint_pubkey,
            program_id=program_id_pubkey,
            network=network
        )

        instruction_create_associated_token_account = None

        if recipient_token_account is None:
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

        instruction_transfer = transfer_checked(params)
        latest_blockhash = await get_blockhash(network)

        txn = Transaction(recent_blockhash=latest_blockhash)
        if instruction_create_associated_token_account:
            txn.add(instruction_create_associated_token_account)
        txn.add(instruction_transfer)
        txn.sign(sender_keypair)

        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [base58.b58encode(txn.serialize()).decode("utf-8")]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)

        print(f'response sendTransaction: {response}')

        if response.status_code == 200:
            response_json = response.json()
            if 'result' in response_json:
                resp_confirm_transaction = await confirm_transaction(
                    tx_sig=response_json['result'],
                    network=network,
                )
                return resp_confirm_transaction
            return response_json
        return {'error': f'Response status code: {response.status_code}'}

    except Exception as error:
        print(f"Failed to transfer spl token: {error}")
        return {'error': str(error)}
