"""PIN-based key derivation and secret encryption for stored wallets.

Sensitive material (private keys, seed words, secret keys) is encrypted at
rest with a symmetric key derived from the user's PIN via scrypt.  Only a
random salt and an encrypted verifier token are persisted; the PIN itself
is never stored.

Backward compatibility: legacy wallet records (created before this module
existed) keep their secrets in plaintext.  Such records lack the
``secrets_encrypted`` marker and are migrated to ciphertext the first time
a PIN is set up.
"""
import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# scrypt cost parameters.  ~128 * N * r bytes of memory and a few hundred
# milliseconds on commodity hardware -- enough to deter brute force while
# staying responsive for a single unlock action.
_SCRYPT_N = 1 << 14  # 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32

# Marker stored inside a wallet record so encrypted records can be told
# apart from legacy plaintext ones.
WALLET_ENCRYPTED_FIELD = "secrets_encrypted"
# A wallet with no stored private key (address-only).
WATCH_ONLY_FIELD = "watch_only"

# The fields of a wallet record that hold secret material.
SECRET_FIELDS = ("private_key_hex", "words", "secret_key_base58")

# Known plaintext encrypted into the verifier token.  Decrypting the stored
# verifier with the candidate key and matching this value confirms the PIN.
_VERIFIER_PLAINTEXT = "solana-wallet-pin-ok"

# Minimum acceptable PIN length.
MIN_PIN_LENGTH = 4


class PinError(Exception):
    """Raised on PIN verification / setup problems."""


def make_salt() -> bytes:
    """Return a fresh 16-byte random salt."""
    return os.urandom(16)


def derive_key(pin: str, salt: bytes) -> bytes:
    """Derive a 32-byte url-safe-base64 Fernet key from ``pin`` + ``salt``."""
    kdf = Scrypt(
        salt=salt,
        length=_KEY_LEN,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    )
    raw = kdf.derive(pin.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def make_verifier(key: bytes) -> str:
    """Create a verifier token that proves knowledge of ``key``."""
    return _encrypt(_VERIFIER_PLAINTEXT, key)


def verify_pin(pin: str, salt: bytes, verifier: str) -> bool:
    """Return True if ``pin`` decrypts ``verifier`` to the known plaintext."""
    if not pin or not verifier:
        return False
    try:
        return decrypt_secret(verifier, derive_key(pin, salt)) == _VERIFIER_PLAINTEXT
    except InvalidToken:
        return False
    except Exception:
        return False


def validate_pin(pin: str) -> bool:
    """Return True if ``pin`` meets the minimum strength requirements."""
    return bool(pin) and pin.isdigit() and len(pin) >= MIN_PIN_LENGTH


# --- salt (de)serialization for shared_preferences storage ---

def encode_salt(salt: bytes) -> str:
    return base64.b64encode(salt).decode("ascii")


def decode_salt(salt_str: str) -> bytes:
    return base64.b64decode(salt_str.encode("ascii"))


# --- encrypt / decrypt individual secrets ---

def _encrypt(plaintext: str, key: bytes) -> str:
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, key: bytes) -> str:
    """Decrypt a single Fernet token.  Raises ``InvalidToken`` on failure."""
    if not token:
        return ""
    return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")


def encrypt_wallet_secrets(wallet: dict, key: bytes) -> dict:
    """Return a copy of ``wallet`` with its secret fields encrypted.

    Non-secret fields (name, description, address, public key, ...) are
    left untouched so the wallet list can still be rendered without the key.
    """
    out = dict(wallet)
    for field in SECRET_FIELDS:
        out[field] = _encrypt(str(wallet.get(field, "")), key)
    out[WALLET_ENCRYPTED_FIELD] = True
    return out


def decrypt_wallet_secrets(wallet: dict, key: bytes) -> dict:
    """Return a copy of ``wallet`` with its secret fields decrypted.

    Legacy plaintext records (no ``secrets_encrypted`` marker) are returned
    unchanged.  Empty secret fields stay empty (e.g. watch-only wallets).
    """
    out = dict(wallet)
    if not wallet.get(WALLET_ENCRYPTED_FIELD):
        return out
    for field in SECRET_FIELDS:
        token = wallet.get(field, "")
        if not token:
            out[field] = ""
            continue
        try:
            out[field] = decrypt_secret(token, key)
        except Exception:
            out[field] = ""
    return out


def get_secret(wallet: dict, field: str, key: bytes) -> str:
    """Return the plaintext value of a single secret ``field`` of ``wallet``.

    Returns ``""`` for missing / watch-only fields.  Legacy (unencrypted)
    records return their stored value as-is.
    """
    if not wallet.get(WALLET_ENCRYPTED_FIELD):
        return str(wallet.get(field, ""))
    token = wallet.get(field, "")
    if not token:
        return ""
    try:
        return decrypt_secret(token, key)
    except Exception:
        return ""
