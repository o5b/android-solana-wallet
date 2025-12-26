# import os
# import sys
import base58
import requests
import time
# import traceback
from typing import List, Optional, Union, cast, Dict, Any, NamedTuple
import pprint

# from .publickey import PublicKey
from solana.balance import get_account_info
# from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.transaction import Transaction, TransactionInstruction, AccountMeta
from solana.layouts import INSTRUCTIONS_LAYOUT, InstructionType
# from solana.message import Message
from solana.types import TokenAccountOpts

# Program IDs
TOKEN_PROGRAM_ID = PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM_ID = PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")

ASSOCIATED_TOKEN_PROGRAM_ID = PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM_ID = PublicKey("11111111111111111111111111111111")

SYS_PROGRAM_ID: PublicKey = PublicKey("11111111111111111111111111111111")
"""Public key that identifies the System program."""

SYSVAR_RENT_PUBKEY: PublicKey = PublicKey("SysvarRent111111111111111111111111111111111")
"""Public key of the synthetic account that serves the network fee resource consumption."""


def get_token_program_id(mint: str, network: str) -> str | None:
    """ Get a Token Program (owner) from the token.
        Ex.: TOKEN_PROGRAM_ID or TOKEN_2022_PROGRAM_ID

        Returns:
            The public key of Token Program.
    """
    try:

        for attempt in range(5):
            try:
                response = get_account_info(address=mint, network=network)
                break
            except Exception as e:
                print(f"Error when get_token_program_id: {e}. Attempt {attempt + 1} out of 5.")
                time.sleep(10)
        else:
            raise Exception("Failed to get_token_program_id after 5 attempts.")

        # print(f'response: {response}')

        if response:
            if 'owner' in response:
                return response['owner']
        return None

    except Exception as error:
        # detailed_error_traceback = traceback.format_exc()
        print(f"Failed to get_token_program_id: {error}")
        return None


def get_associated_token_address(owner: PublicKey, mint: PublicKey, token_program_id: PublicKey = TOKEN_PROGRAM_ID) -> PublicKey:
    """Derives the associated token address for the given wallet address and token mint.

    Args:
        owner (Pubkey): Owner's wallet address.
        mint (Pubkey): The token mint address.
        token_program_id (Pubkey, optional): The token program ID. Must be either `spl.token.constants.TOKEN_PROGRAM_ID`
            or `spl.token.constants.TOKEN_2022_PROGRAM_ID` (default is `TOKEN_PROGRAM_ID`).

    Returns:
        The public key of the derived associated token address.

    Raises:
        ValueError: If an invalid `token_program_id` is provided.
    """
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
    """Creates a transaction instruction to create an associated token account.

    Args:
        payer (Pubkey): Payer's wallet address.
        owner (Pubkey): Owner's wallet address.
        mint (Pubkey): The token mint address.
        token_program_id (Pubkey, optional): The token program ID. Must be either `spl.token.constants.TOKEN_PROGRAM_ID`
            or `spl.token.constants.TOKEN_2022_PROGRAM_ID` (default is `TOKEN_PROGRAM_ID`).

    Returns:
        The instruction to create the associated token account.

    Raises:
        ValueError: If an invalid `token_program_id` is provided.
    """
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
            # AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYSVAR_RENT_PUBKEY, is_signer=False, is_writable=False),
        ],
        program_id=ASSOCIATED_TOKEN_PROGRAM_ID,
        data=bytes(0),
    )


class TransferCheckedParams(NamedTuple):
    """TransferChecked token transaction params."""

    program_id: PublicKey
    """SPL Token program account."""
    source: PublicKey
    """Source account."""
    mint: PublicKey
    """Public key of the minter account."""
    dest: PublicKey
    """Destination account."""
    owner: PublicKey
    """Owner of the source account."""
    amount: int
    """Number of tokens to transfer."""
    decimals: int
    """Amount decimals."""
    signers: List[PublicKey] = []
    """Signing accounts if `owner` is a multiSig."""


def __add_signers(keys: List[AccountMeta], owner: PublicKey, signers: List[PublicKey]) -> None:
    if signers:
        keys.append(AccountMeta(pubkey=owner, is_signer=False, is_writable=False))
        for signer in signers:
            keys.append(AccountMeta(pubkey=signer, is_signer=True, is_writable=False))
    else:
        keys.append(AccountMeta(pubkey=owner, is_signer=True, is_writable=False))


def transfer_checked(params: TransferCheckedParams) -> TransactionInstruction:
    """This instruction differs from `transfer` in that the token mint and decimals value is asserted by the caller.

    Example:

        >>> dest, mint, owner, source, token = PublicKey(1), PublicKey(2), PublicKey(3), PublicKey(4), PublicKey(5)
        >>> params = TransferCheckedParams(
        ...     amount=1000,
        ...     decimals=6,
        ...     dest=dest,
        ...     mint=mint,
        ...     owner=owner,
        ...     program_id=token,
        ...     source=source,
        ... )
        >>> type(transfer_checked(params))
        <class 'solana.transaction.TransactionInstruction'>

    Returns:
        The transfer-checked instruction.
    """
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

    # return TransactionInstruction(accounts=keys, program_id=params.program_id, data=data)
    return TransactionInstruction(keys=keys, program_id=params.program_id, data=data)


def get_token_account(owner: PublicKey, mint: PublicKey, program_id: PublicKey, network: str) -> PublicKey | None:
    """ Get an associated token account if it exists.

        Returns:
            The public key of associated token account.
    """
    try:
        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                f"{owner}",
                {
                    "programId": f"{program_id}"
                },
                {
                    "commitment": "finalized",
                    "encoding": "jsonParsed"
                }
            ]
        }
        print(f'payload: {payload}')

        print("\n--- Sending getTokenAccountsByOwner ---")
        response = requests.post(url, headers=headers, json=payload)
        print(f'response getTokenAccountsByOwner: {response}')

        print("\n--- Sending getTokenAccountsByOwner ---")
        for attempt in range(5):
            try:
                # response = await client.get_token_accounts_by_owner(owner=owner, opts=TokenAccountOpts(mint=mint))
                response = requests.post(url, headers=headers, json=payload)
                break
            except Exception as e:
                print(f"Error get_token_account for the owner: {owner}. Error msg.: {e}. Attempt {attempt + 1} out of 5.")
                time.sleep(3)
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
                        if "account" in account:
                            if "data" in  account["account"]:
                                if "parsed" in account["account"]["data"]:
                                    if "info" in account["account"]["data"]["parsed"]:
                                        if "mint" in account["account"]["data"]["parsed"]["info"]:
                                            if account["account"]["data"]["parsed"]["info"]["mint"] == f"{mint}":
                                                print(f"account pubkey: {account['pubkey']}")
                                                return PublicKey(account["pubkey"])
        else:
            print(f"Failed to get_token_account: {mint} from owner: {owner}.")
        return None

    except Exception as error:
        print(f"Failed to get_token_account: {error}")
        return None
