from typing import List, Union, Tuple, Dict, Any, Literal
import httpx
import base58
import asyncio
import time

from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from solana.commitment import COMMITMENT_RANKS, Commitment, Confirmed

async def get_blockhash(network):
    blockhash = None
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getLatestBlockhash",
        "params": [{"commitment": "finalized"}]
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)

    print(f'get_blockhash >> response: {response}')
    print(f'******* get_blockhash >> response.json(): {response.json()}')

    if response.status_code == 200:
        blockhash = response.json().get("result", {}).get("value", {}).get('blockhash', '')
        print(f'*** get_blockhash >> blockhash: {blockhash}')

    return blockhash

async def confirm_transaction(tx_sig: str, network: str, commitment: Commitment = Confirmed, sleep_seconds: float = 0.5, timeout_seconds: float = 60) -> Dict:
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

    deadline = time.time() + timeout_seconds
    max_429_retries = 10
    retry_429_count = 0
    last_resp = None
    last_seen_status = None

    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as er:
                print(f'confirm_transaction >> network error: {er}, retrying...')
                await asyncio.sleep(min(sleep_seconds * 4, 5))
                continue

            # Public RPC rate-limit: back off and retry instead of failing.
            if response.status_code == 429:
                retry_429_count += 1
                if retry_429_count > max_429_retries:
                    raise Exception(f"Rate limited (429) after {retry_429_count} retries while confirming transaction {tx_sig}")
                retry_after = float(response.headers.get("retry-after") or min(2 ** retry_429_count, 20))
                print(f'confirm_transaction >> 429 Too Many Requests, backing off for {retry_after}s (attempt {retry_429_count}/{max_429_retries})')
                await asyncio.sleep(retry_after)
                continue

            resp = response.json()
            maybe_rpc_error = resp.get("error")
            if maybe_rpc_error is not None:
                # JSON-RPC 429 is the same rate-limit encoded at the protocol level.
                err_code = maybe_rpc_error.get("code") if isinstance(maybe_rpc_error, dict) else None
                if err_code == 429:
                    retry_429_count += 1
                    if retry_429_count > max_429_retries:
                        raise Exception(maybe_rpc_error)
                    backoff = min(2 ** retry_429_count, 20)
                    print(f'confirm_transaction >> RPC error 429, backing off for {backoff}s (attempt {retry_429_count}/{max_429_retries})')
                    await asyncio.sleep(backoff)
                    continue
                raise Exception(maybe_rpc_error)

            last_resp = resp
            resp_value = resp["result"]["value"][0]
            if resp_value is not None:
                confirmation_status = resp_value.get("confirmationStatus")
                last_seen_status = confirmation_status
                if confirmation_status is not None:
                    confirmation_rank = COMMITMENT_RANKS[confirmation_status]
                    commitment_rank = COMMITMENT_RANKS[commitment]
                    if confirmation_rank >= commitment_rank:
                        print(f'confirm_transaction >> success: {confirmation_status}')
                        break
            await asyncio.sleep(sleep_seconds)
        else:
            # Timed out. The transaction may still have been accepted by the
            # cluster (any status observed): return the last response instead
            # of raising so the caller knows the tx was at least submitted.
            # Only raise if we never observed the transaction at all.
            if last_resp is not None and last_seen_status is not None:
                print(f'confirm_transaction >> timeout reached, but tx was last seen as "{last_seen_status}"; returning last status')
                return last_resp
            raise Exception(f"Unable to confirm transaction {tx_sig}")

    return last_resp if last_resp is not None else resp

async def transfer_sol_token(
    sender_address: str,
    sender_private_key: str,
    recipient_address: str,
    amount: float,
    network: str
) -> dict:
    try:
        sender_kp = Keypair.from_seed(bytes.fromhex(sender_private_key))

        txn = Transaction()
        txn.recent_blockhash = await get_blockhash(network)
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

        url = network
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [base58.b58encode(txn.serialize()).decode("utf-8")]
        }
        print(f'payload: {payload}')

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)

        print(f'response sendTransaction: {response}')

        if response.status_code == 200:
            response_json = response.json()
            print(f'******* sendTransaction response.json(): {response_json}')
            if 'result' in response_json:
                resp_confirm_transaction = await confirm_transaction(tx_sig=response_json['result'], network=network)
                print(f'******* transfer_sol_token >> resp_confirm_transaction: {resp_confirm_transaction}')
                return resp_confirm_transaction
            return response_json
        return {'error': f'Response status code: {response.status_code}'}

    except Exception as error:
        print(f"Failed transfer sol token: {error}")
        return {'error': str(error)}

async def get_min_sol_balance(network: str) -> float | None:
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMinimumBalanceForRentExemption",
        "params": [50]
    }
    try:
        async with httpx.AsyncClient() as client:
            for attempt in range(5):
                try:
                    response = await client.post(url, headers=headers, json=payload)
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
                    await asyncio.sleep(10)
            else:
                print("Failed to get min_sol_balance after 5 attempts.")
                return None
    except Exception as e:
        print(f"Failed to get min_sol_balance: {e}")
        return None
