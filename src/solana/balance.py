from datetime import datetime
import time
import pprint
import requests
import json
import base64
import struct
import base58

from .publickey import PublicKey

def get_sol_balance(address, network):
    # url = f"https://api.{network}.solana.com"
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [address]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        # print(f"*** get_sol_balances >> Headers: {response.headers}")
        result = response.json().get("result", {}).get("value", 0)
        return result / 1_000_000_000  # Баланс в SOL
    else:
        error_message = response.text
        # raise Exception(f"Error get_sol_balance: {error_message}")
        print(f"Error get_sol_balance: {error_message} \nHeaders: {response.headers}")
    return None

def get_spl_balances(address, network, program_id):
    # url = f"https://api.{network}.solana.com"
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            address,
            {"programId": program_id},
            {"encoding": "jsonParsed"}
        ]
    }
    retry_after = 1
    while retry_after >= 1:
        time.sleep(retry_after)
        response = requests.post(url, headers=headers, json=payload)
        # print(f"*** get_spl_balances >> Headers: {response.headers}")
        retry_after = int(response.headers.get('retry-after', '0'))
        print(f'get_spl_balances >> response.headers >> retry_after={retry_after}')
    if response.status_code == 200:
        result = response.json().get("result", {}).get("value", [])
        tokens = {}
        for account in result:
            token_info = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            mint = token_info.get("mint")
            amount = int(token_info.get("tokenAmount", {}).get("amount", 0))
            decimals = int(token_info.get("tokenAmount", {}).get("decimals", 0))
            owner = token_info.get("owner", "Unknown")
            tokens[mint] = {
                "amount": amount / (10 ** decimals) if decimals else amount,
                "owner": owner,
            }
        return tokens
    else:
        error_message = response.text
        print(f"Error get_spl_balance: {error_message} \nHeaders: {response.headers}")
        """
        Error get_spl_balance:  {"jsonrpc":"2.0","error":{"code": 429,"message":"Too many requests for a specific RPC call"}, "id": 1 }

        Headers: {'access-control-max-age': '86400', 'content-length': '107', 'content-type': 'application/json', 'cache-control': 'no-cache', 'access-control-allow-origin': '*', 'access-control-allow-methods': 'POST, GET, OPTIONS', 'retry-after': '10', 'x-rpc-node': 'sxb1', 'x-ratelimit-tier': 'free', 'x-ratelimit-method-limit': '2', 'x-ratelimit-method-remaining': '-4', 'x-ratelimit-rps-limit': '100', 'x-ratelimit-rps-remaining': '91', 'x-ratelimit-endpoint-limit': 'unlimited', 'x-ratelimit-endpoint-remaining': '-2010', 'x-ratelimit-conn-limit': '40', 'x-ratelimit-conn-remaining': '39', 'x-ratelimit-connrate-limit': '40', 'x-ratelimit-connrate-remaining': '39', 'x-ratelimit-pubsub-limit': '5', 'x-ratelimit-pubsub-remaining': '5', 'connection': 'close'}
        """
        # response.headers={'retry-after': '10'}
        # When you get a 429 http status, the status usually comes with a response header Retry-After. This response header gives you the amount of time to delay until the next API call.
        # If you follow the information given back in the RPC API response headers, you should be able to abide by the given rate limits.
        """The answer is Solana imposes limits:
            X-Ratelimit-Conn-Limit: The maximum number of concurrent connections allowed.
            Example: 40 X-Ratelimit-Conn-Remaining: Remaining concurrent connections in the current window.
            Example: 38 X-Ratelimit-Method-Limit: The limit on requests for a specific method.
            Example: 10 X-Ratelimit-Method-Remaining: Remaining requests for the specific method.
            Example: 8 X-Ratelimit-Rps-Limit: The limit on requests per second.
            Example: 100 X-Ratelimit-Rps-Remaining: Remaining requests in the current second.
            Example: 97 X-Ratelimit-Tier: Indicates the user's rate limit tier (e.g., free, premium).
        """
    return None

def get_account_info(address, network):
    # url = f"https://api.{network}.solana.com"
    url = network
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            address,
            {
                "encoding": "base64"
            }
        ]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        # print(f"*** get_account_info >> Headers: {response.headers}")
        return response.json().get("result", {}).get("value", [])
    else:
        error_message = response.text
        print(f"Error get_account_info: {error_message} \nHeaders: {response.headers}")
    return None

def decode_metadata(data: list[str, str]) -> str | None:
    if len(data) == 2:
        if data[1] == 'base64':
            return base64.b64decode(data[0])
        elif data[1] == 'base58':
            return base58.b58decode(data[0])
    return None

def get_metadata_pda(mint_address: str) -> str:
    # Metaplex Token Metadata Program ID
    METAPLEX_METADATA_PROGRAM_ID = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
    mint_pubkey = PublicKey(mint_address)
    program_id_pubkey = PublicKey(METAPLEX_METADATA_PROGRAM_ID)
    seeds = [
        b"metadata",
        bytes(program_id_pubkey),
        bytes(mint_pubkey)
    ]
    metadata_pda, _ = PublicKey.find_program_address(seeds, program_id_pubkey)
    return str(metadata_pda)

def parse_metadata_2022_program_id(data: bytes):
    metadata = {
        # "mint_authority": "",
        # "freeze_authority": "",
        "update_authority": "",
        "mint": "",
        "name": "",
        "symbol": "",
        "uri": ""
    }

    offset = 0

    # Пропускаем первые 4 байта (версия или флаг)
    #offset += 4

    if len(data) > 314:
        # Пропускаем первые 238 байт
        offset += 238

        # Декодируем update_authority (32 байта)
        update_authority = base58.b58encode(data[offset:offset + 32]).decode()
        offset += 32

        # Декодируем mint (32 байта)
        mint = base58.b58encode(data[offset:offset + 32]).decode()
        offset += 32

        # Декодируем длину имени (4 байта) и само имя
        name_length = struct.unpack_from("<I", data, offset)[0]
        print(f'name_length: {name_length}')
        offset += 4
        name = data[offset:offset + name_length].decode()
        offset += name_length

        # Декодируем длину символа (4 байта) и сам символ
        symbol_length = struct.unpack_from("<I", data, offset)[0]
        print(f'symbol_length: {symbol_length}')
        offset += 4
        symbol = data[offset:offset + symbol_length].decode()
        offset += symbol_length

        # Декодируем длину URI (4 байта) и сам URI
        uri_length = struct.unpack_from("<I", data, offset)[0]
        print(f'uri_length: {uri_length}')
        offset += 4
        uri = data[offset:offset + uri_length].decode()
        offset += uri_length

        metadata["update_authority"] = update_authority
        metadata["mint"] = mint
        metadata["name"] = name
        metadata["symbol"] = symbol
        metadata["uri"] = uri

    # else:
    #     # Пропускаем первые 4 байта (версия или флаг)
    #     offset += 4

    #     # Декодируем mint_authority (32 байта)
    #     mint_authority = base58.b58encode(data[offset:offset + 32]).decode()
    #     offset += 32

    #     # TODO: непонятные данные, ex.: b'\xb2\xde;\x8f\x06\xf7\xbb\x98\x06\x01'
    #     offset += 10

    #     # Пропускаем следующие 4 байта (версия или флаг)
    #     offset += 4

    #     freeze_authority = base58.b58encode(data[offset:offset + 32]).decode()
    #     offset += 32

    #     metadata["mint_authority"] = mint_authority
    #     metadata["freeze_authority"] = freeze_authority

    return metadata

def parse_metadata_metaplex(data: bytes):
    metadata = {
        # "mint_authority": "",
        # "freeze_authority": "",
        "update_authority": "",
        "mint": "",
        "name": "",
        "symbol": "",
        "uri": ""
    }

    offset = 0

    if len(data) == 679:
        # Пропускаем первый байт (версия или флаг)
        offset += 1

        # Декодируем update_authority (32 байта)
        update_authority = base58.b58encode(data[offset:offset + 32]).decode()
        offset += 32

        # Декодируем mint (32 байта)
        mint = base58.b58encode(data[offset:offset + 32]).decode()
        offset += 32

        # Декодируем длину имени (4 байта) и само имя
        name_length = struct.unpack_from("<I", data, offset)[0]
        print(f'name_length: {name_length}')
        offset += 4
        # name = data[offset:offset + name_length].decode()
        name = data[offset:offset + name_length].replace(b'\x00', b'').decode()
        offset += name_length

        # Декодируем длину символа (4 байта) и сам символ
        symbol_length = struct.unpack_from("<I", data, offset)[0]
        print(f'symbol_length: {symbol_length}')
        offset += 4
        # symbol = data[offset:offset + symbol_length].decode()
        symbol = data[offset:offset + symbol_length].replace(b'\x00', b'').decode()
        offset += symbol_length

        # Декодируем длину URI (4 байта) и сам URI
        uri_length = struct.unpack_from("<I", data, offset)[0]
        print(f'uri_length: {uri_length}')
        offset += 4
        # uri = data[offset:offset + uri_length].decode()
        uri = data[offset:offset + uri_length].replace(b'\x00', b'').decode()
        offset += uri_length

        metadata["update_authority"] = update_authority
        metadata["mint"] = mint
        metadata["name"] = name
        metadata["symbol"] = symbol
        metadata["uri"] = uri

    return metadata

def get_sol_spl_balance(address: str, networks: list) -> list:
    result = []
    TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # Standard SPL Token Program
    TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # Token 2022 Program

    for network in networks:
        network_result = {
            'address': address,
            # 'network': f"https://api.{network}.solana.com",
            'network': network,
            'sol': 0,
            'spl': []
        }
        print(f"\n******************* Сеть: {network} *******************")
        sol_balance = get_sol_balance(address, network)
        if sol_balance is not None:
            print(f"Баланс SOL: {sol_balance:.9f}")
            network_result['sol'] = sol_balance
        else:
            print("Ошибка при получении баланса SOL")

        for program_id in [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID]:
            spl_balances = get_spl_balances(address, network, program_id)
            if spl_balances is not None:

                if program_id == TOKEN_PROGRAM_ID:
                    print(f"\n********* SPL токены (TOKEN_PROGRAM_ID: {program_id}):")
                elif program_id == TOKEN_2022_PROGRAM_ID:
                    print(f"\n********* SPL токены (TOKEN_2022_PROGRAM_ID: {program_id}):")
                print(f'***** spl_balances: {spl_balances}')

                for mint, data in spl_balances.items():
                    print(f"\n    ({mint}): {data['amount']} (Owner: {data['owner']})")
                    if mint:
                        token_2022_program_id_metadata = None
                        token_metaplex_metadata = None
                        token_data = {}
                        if program_id == TOKEN_2022_PROGRAM_ID:
                            response_account_info = get_account_info(mint, network)
                            print(f'response_account_info:{response_account_info}')
                            if response_account_info and ('data' in response_account_info):
                                decode_metadata_bytes = decode_metadata(response_account_info['data'])
                                print(f'decode_metadata_bytes 2022_program_id: {decode_metadata_bytes}')
                                if decode_metadata_bytes:
                                    token_2022_program_id_metadata = parse_metadata_2022_program_id(decode_metadata_bytes)
                                    print(f'Token metadata (TOKEN_2022_PROGRAM_ID): {token_2022_program_id_metadata}')
                                    if token_2022_program_id_metadata:
                                        print(f"    Name: {token_2022_program_id_metadata['name']}")
                                        print(f"    Symbol: {token_2022_program_id_metadata['symbol']}")
                                        print(f"    JSON URL: {token_2022_program_id_metadata['uri']}")
                                        print(f"    Update authority: {token_2022_program_id_metadata['update_authority']}")
                                        print(f"    Mint: {token_2022_program_id_metadata['mint']}")
                                        print(f'    Amount: {data['amount']}')

                        # METAPLEX
                        metadata_pda_address = get_metadata_pda(mint_address=mint)
                        print(f'    Metadata PDA address: {metadata_pda_address}')
                        if metadata_pda_address:
                            response_pda_account_info = get_account_info(metadata_pda_address, network)
                            print(f'        response_pda_account_info: {response_pda_account_info}')
                            if response_pda_account_info and ('data' in response_pda_account_info):
                                decode_metadata_bytes = decode_metadata(response_pda_account_info['data'])
                                print(f'decode_pda_metadata_bytes: {decode_metadata_bytes}')
                                token_metaplex_metadata = parse_metadata_metaplex(decode_metadata_bytes)
                                print(f'Token metadata (TOKEN_PROGRAM_ID - METAPLEX): {token_metaplex_metadata}')
                                if token_metaplex_metadata:
                                    print(f"    Name: {token_metaplex_metadata['name']}")
                                    print(f"    Symbol: {token_metaplex_metadata['symbol']}")
                                    print(f"    JSON URL: {token_metaplex_metadata['uri']}")
                                    print(f"    Update authority: {token_metaplex_metadata['update_authority']}")
                                    print(f"    Mint: {token_metaplex_metadata['mint']}")
                                    print(f'    Amount: {data['amount']}')

                        token_data['program_id'] = program_id
                        token_data['mint'] = mint
                        token_data['amount'] = data['amount']
                        token_data['owner'] = data['owner']

                        if token_2022_program_id_metadata:
                            token_data['name_2022'] = token_2022_program_id_metadata['name']
                            token_data['symbol_2022'] = token_2022_program_id_metadata['symbol']
                            token_data['uri_2022'] = token_2022_program_id_metadata['uri']
                            token_data['update_authority_2022'] = token_2022_program_id_metadata['update_authority']

                        if token_metaplex_metadata:
                            token_data['name_metaplex'] = token_metaplex_metadata['name']
                            token_data['symbol_metaplex'] = token_metaplex_metadata['symbol']
                            token_data['uri_metaplex'] = token_metaplex_metadata['uri']
                            token_data['update_authority_metaplex'] = token_metaplex_metadata['update_authority']
                            token_data['metadata_pda_address'] = metadata_pda_address

                        network_result['spl'].append(token_data)

            else:
                print(f"Ошибка при получении SPL токенов (Program ID: {program_id})")
        result.append(network_result)
    return result
