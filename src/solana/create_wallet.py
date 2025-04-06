import struct
from typing import Tuple
import mnemonic
import hashlib
import hmac
import base58
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519

# Константы
SOLANA_DERIVATION_PATH = "m/44'/501'/0'/0'"
HARDENED_OFFSET = 0x80000000
SOLANA_SEED = b"ed25519 seed"


# Функция для HMAC-SHA512
def hmac_sha512(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha512).digest()


# Функция для вычисления мастер-ключа из seed
def master_key_from_seed(seed: bytes) -> Tuple[bytes, bytes]:
    I = hmac_sha512(SOLANA_SEED, seed)
    return I[:32], I[32:]


# Функция для деривации дочернего ключа
def derive_child_key(parent_key: bytes, parent_chain_code: bytes, index: int) -> Tuple[bytes, bytes]:
    data = struct.pack('>B', 0) + parent_key + struct.pack('>I', index | HARDENED_OFFSET)
    I = hmac_sha512(parent_chain_code, data)
    return I[:32], I[32:]


# Функция для разбиения derivation_path
def parse_derivation_path(path: str):
    return [int(i[:-1]) for i in path.split("/")[1:] if "'" in i]


# Основная функция
def derive_private_key(seed: bytes, derivation_path: str) -> bytes:
    master_private_key, master_chain_code = master_key_from_seed(seed)
    private_key, chain_code = master_private_key, master_chain_code

    for index in parse_derivation_path(derivation_path):
        private_key, chain_code = derive_child_key(private_key, chain_code, index)

    return private_key

# def derive_solana_key(seed: bytes, derivation_path: str) -> bytes:
#     """Деривация ключа для Solana по пути m/44'/501'/0'/0'"""
#     # Начальный seed для HMAC-SHA512
#     master_key = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
#     private_key = master_key[:32]  # Первые 32 байта - приватный ключ

#     # Разбиваем путь деривации
#     path_parts = derivation_path.split('/')
#     if path_parts[0] == 'm':
#         path_parts = path_parts[1:]

#     current_key = private_key
#     for part in path_parts:
#         hardened = part.endswith("'")
#         index = int(part.rstrip("'")) + 0x80000000 if hardened else int(part)

#         # Создаем данные для HMAC
#         data = b"\x00" + current_key + index.to_bytes(4, byteorder='big')
#         derived = hmac.new(b"ed25519 seed", data, hashlib.sha512).digest()
#         current_key = derived[:32]

#     return current_key


def get_public_key(private_key: bytes) -> bytes:
    """Генерация публичного ключа из приватного ключа с использованием Ed25519"""
    private_key_obj = ed25519.Ed25519PrivateKey.from_private_bytes(private_key)
    public_key = private_key_obj.public_key().public_bytes_raw()
    return public_key


def create_solana_wallet(secret=None):
    words = ''
    wallet_address_base58 = ''
    secret_key_base58 = ''
    private_key_hex = ''
    public_key_hex = ''
    # seed_hex = ''
    error = ''
    error_text = 'Error create new solana wallet.'
    try:
        # solana_derivation_path = "m/44'/501'/0'/0'"
        mnemo = mnemonic.Mnemonic("english")
        if secret:
            secret_list = secret.split()
            print(f'secret_list: {secret_list}, len: {len(secret_list)}, type: {type(secret_list)}')
            if (len(secret_list) == 12) or (len(secret_list) == 24):    # secret words 12/24
                words = secret
                error_text = f'Error create solana wallet from secret words: {words}.'
            elif (len(secret_list) == 1):
                print(f'len(secret_list[0]): {len(secret_list[0])}')
                if len(secret_list[0]) == 64:   # private_key hex
                    private_key_hex = secret_list[0]
                    try:
                        private_key_bytes = bytes.fromhex(private_key_hex)
                        public_key_bytes = get_public_key(private_key_bytes)
                        secret_key_bytes = private_key_bytes + public_key_bytes
                        secret_key_base58 = base58.b58encode(secret_key_bytes).decode()
                        wallet_address_base58 = base58.b58encode(public_key_bytes).decode('ascii')
                        # private_key_hex = private_key_bytes.hex()
                        public_key_hex = public_key_bytes.hex()
                    except Exception as er:
                        error = f'Error create solana wallet from private_key_hex: {private_key_hex}. Error msg: {er}'
                    finally:
                        return words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error
                elif len(secret_list[0]) == 88:   # secret_key base58
                    secret_key_base58 = secret_list[0]
                    try:
                        secret_key_bytes = base58.b58decode(secret_key_base58) # secret_key_bytes=private_key_bytes + public_key_bytes
                        private_key_bytes = secret_key_bytes[:32]
                        public_key_bytes = secret_key_bytes[32:]
                        if public_key_bytes != get_public_key(private_key_bytes):
                            error = f'Error create solana wallet from secret: {secret_key_base58}. Bad secret_key_base58!'
                            return words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error
                        wallet_address_base58 = base58.b58encode(public_key_bytes).decode('ascii')
                        private_key_hex = private_key_bytes.hex()
                        public_key_hex = public_key_bytes.hex()
                    except Exception as er:
                        error = f'Error create solana wallet from secret_key_base58: {secret_key_base58}. Error msg: {er}'
                    finally:
                        return words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error
                else:
                    error = f'Error create solana wallet from secret: {secret}. Bad length secret={len(secret_list[0])}'
                    return words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error
            else:
                error = f'Error create solana wallet from secret: "{secret}". Bad secret.'
                return words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error
        else:
            words = mnemo.generate(strength=128)  # 12 слов

        seed_bytes = mnemo.to_seed(words, passphrase="")

        # Деривация приватного ключа
        private_key_bytes = derive_private_key(seed_bytes, SOLANA_DERIVATION_PATH)
        public_key_bytes = get_public_key(private_key_bytes)
        secret_key_bytes = private_key_bytes + public_key_bytes
        secret_key_base58 = base58.b58encode(secret_key_bytes).decode()
        # Кодирование публичного ключа в base58 для адреса Solana
        wallet_address_base58 = base58.b58encode(public_key_bytes).decode('ascii')
        private_key_hex = private_key_bytes.hex()
        public_key_hex = public_key_bytes.hex()
    except Exception as er:
        error = f'{error_text} Error msg: {er}'
    finally:
        return words, wallet_address_base58, secret_key_base58, private_key_hex, public_key_hex, error