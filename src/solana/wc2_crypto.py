"""WalletConnect v2 cryptography layer (relay-interop-critical).

Every byte here was reverse-engineered from the reference JS implementation
(``@walletconnect/utils`` 2.23.10 + ``@walletconnect/relay-auth`` 1.1.0 +
``@walletconnect/time`` 1.0.2) so that a hand-rolled Python wallet can talk to
real WalletConnect dApps without any JS SDK.

Wire format (verified against the bundled ``Ie`` / ``ne`` envelope helpers):

    symKey  = HKDF-SHA256(ikm=X25519-ECDH(priv, peerPub),
                          salt=zero(32), info=b"", dkLen=32)
    topic   = sha256(hex2bytes(symKey)).hex()
    sealed  = ChaCha20-Poly1305(symKey, iv=12 random).encrypt(utf8(json))
              -> ciphertext || 16-byte tag
    envelope(type 0, direct)   = base64pad( [0x00] || iv(12)  || sealed )
    envelope(type 1, x25519)   = base64pad( [0x01] || senderPub(32)
                                            || iv(12) || sealed )
    envelope(type 2, plaintext)= base64pad( [0x02] || data )

``base64pad`` == standard base64 (``+``/``/``/``=``).  The relay ``message``
field is exactly one of these envelope strings.

All key material is hex-encoded 32-byte strings (WC convention).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import os
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_RAW = serialization.Encoding.Raw
_RAW_PRIV = serialization.PrivateFormat.Raw
_RAW_PUB = serialization.PublicFormat.Raw
_NO_ENC = serialization.NoEncryption()

# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------
# WC uses a *standard* base64 (with padding) for relay envelopes and a
# *url-safe* base64 (no padding) for JWT segments.
import base64 as _b64


def _b64pad(b: bytes) -> str:
    """Standard base64 (with ``+/=``) — WC ``EncodingType base64pad``."""
    return _b64.b64encode(b).decode("ascii")


def _unb64pad(s: str) -> bytes:
    # WC envelopes are always standard base64; be lenient about padding.
    pad = (-len(s)) % 4
    return _b64.b64decode(s + ("=" * pad))


def _b64url(b: bytes) -> str:
    """URL-safe base64 without padding — JWT segment encoding."""
    return _b64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _hex(b: bytes) -> str:
    return b.hex()


def _unhex(s: str) -> bytes:
    return bytes.fromhex(s)


# ---------------------------------------------------------------------------
# Symmetric keys & key agreement
# ---------------------------------------------------------------------------
_HKDF_SALT = b"\x00" * 32  # noble: salt defaults to HashLen zero bytes


def generate_symkey() -> str:
    """A random 32-byte symmetric key as hex (used for pairings)."""
    return _hex(os.urandom(32))


def x25519_generate_keypair() -> Tuple[str, str]:
    """Generate an X25519 keypair, returns ``(private_hex, public_hex)``."""
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=_RAW,
        format=_RAW_PRIV,
        encryption_algorithm=_NO_ENC,
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=_RAW,
        format=_RAW_PUB,
    )
    return _hex(priv_bytes), _hex(pub_bytes)


def x25519_public_from_private(private_hex: str) -> str:
    priv = X25519PrivateKey.from_private_bytes(_unhex(private_hex))
    pub_bytes = priv.public_key().public_bytes(
        encoding=_RAW,
        format=_RAW_PUB,
    )
    return _hex(pub_bytes)


def derive_symkey(private_hex: str, peer_public_hex: str) -> str:
    """X25519 ECDH + HKDF-SHA256(salt=zeros32, info=∅) → 32-byte symKey hex.

    Matches ``@walletconnect/utils`` ``deriveSymKey``: the shared secret is the
    raw X25519 scalar-mult output (no hashing by the curve), then HKDF derives
    the 32-byte symmetric key.
    """
    priv = X25519PrivateKey.from_private_bytes(_unhex(private_hex))
    peer = X25519PublicKey.from_public_bytes(_unhex(peer_public_hex))
    shared = priv.exchange(peer)  # 32 raw bytes
    sym = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=b"",
    ).derive(shared)
    return _hex(sym)


def hash_key(symkey_hex: str) -> str:
    """Topic for a symKey = ``sha256(hex2bytes(symKey)).hex()``."""
    return _hash_bytes(_unhex(symkey_hex))


def _hash_bytes(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def hash_message(message: str) -> str:
    """``sha256(utf8(message)).hex()`` (WC ``hashMessage``)."""
    return _hash_bytes(message.encode("utf-8"))


# ---------------------------------------------------------------------------
# AEAD encrypt / decrypt (ChaCha20-Poly1305)
# ---------------------------------------------------------------------------
_AEAD_IV_LEN = 12


def _aead_encrypt(symkey_hex: str, iv: bytes, plaintext: bytes) -> bytes:
    aead = ChaCha20Poly1305(_unhex(symkey_hex))
    # cryptography appends the 16-byte Poly1305 tag to the ciphertext — exactly
    # what noble's chacha20poly1305 does.
    return aead.encrypt(iv, plaintext, None)


def _aead_decrypt(symkey_hex: str, iv: bytes, sealed: bytes) -> bytes:
    aead = ChaCha20Poly1305(_unhex(symkey_hex))
    return aead.decrypt(iv, sealed, None)


# Envelope type bytes (WC ``TYPE_0``/``TYPE_1``/``TYPE_2``).
TYPE_0 = 0  # direct symmetric encryption
TYPE_1 = 1  # X25519 ephemeral sender key (carried in envelope)
TYPE_2 = 2  # plaintext (no encryption)


def encrypt_envelope(
    message: str,
    symkey_hex: str,
    *,
    enc_type: int = TYPE_0,
    sender_public_hex: Optional[str] = None,
) -> str:
    """Encrypt a message string and return the base64pad envelope.

    For ``enc_type=1`` (X25519) ``sender_public_hex`` MUST be provided; the
    receiver derives the shared symKey from its own private key + this sender
    public key. Used for the very first message on a topic where only an ECDH
    agreement exists (the WC ``TypeOneParams`` flow).

    For the normal session/pairing traffic we always use ``enc_type=0`` (the
    symKey for the topic is already shared).
    """
    iv = os.urandom(_AEAD_IV_LEN)
    sealed = _aead_encrypt(symkey_hex, iv, message.encode("utf-8"))

    if enc_type == TYPE_1:
        if not sender_public_hex:
            raise ValueError("type-1 envelope requires sender_public_hex")
        body = bytes([TYPE_1]) + _unhex(sender_public_hex) + iv + sealed
    elif enc_type == TYPE_0:
        body = bytes([TYPE_0]) + iv + sealed
    elif enc_type == TYPE_2:
        body = bytes([TYPE_2]) + message.encode("utf-8")
    else:
        raise ValueError(f"unknown envelope type {enc_type}")
    return _b64pad(body)


def decrypt_envelope(envelope: str, symkey_hex: str) -> str:
    """Decrypt a base64pad envelope produced by :func:`encrypt_envelope`.

    Returns the recovered UTF-8 message string.
    """
    raw = _unb64pad(envelope)
    if not raw:
        raise ValueError("empty envelope")
    etype = raw[0]
    if etype == TYPE_0:
        iv = raw[1 : 1 + _AEAD_IV_LEN]
        sealed = raw[1 + _AEAD_IV_LEN :]
        plaintext = _aead_decrypt(symkey_hex, iv, sealed)
    elif etype == TYPE_1:
        # type || senderPub(32) || iv(12) || sealed
        sender_pub = raw[1:33]
        iv = raw[33 : 33 + _AEAD_IV_LEN]
        sealed = raw[33 + _AEAD_IV_LEN :]
        # Type-1 messages are encrypted under an ECDH-derived key. The caller
        # must supply the matching private key via the symkey_hex slot only when
        # it *is* that key; for incoming proposals we handle ECDH at a higher
        # layer. Here we attempt direct decrypt for the common (type-0) path.
        del sender_pub
        plaintext = _aead_decrypt(symkey_hex, iv, sealed)
    elif etype == TYPE_2:
        plaintext = raw[1:]
    else:
        raise ValueError(f"unknown envelope type byte {etype}")
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# JSON-RPC payload convenience (encrypt(json.dumps(payload)) / decrypt+json.loads)
# ---------------------------------------------------------------------------
def encode_payload(payload: Dict[str, Any], symkey_hex: str, **kw: Any) -> str:
    """Serialise a JSON-RPC payload to JSON, then encrypt it."""
    return encrypt_envelope(json.dumps(payload, separators=(",", ":")), symkey_hex, **kw)


def decode_payload(envelope: str, symkey_hex: str) -> Dict[str, Any]:
    """Decrypt an envelope and parse the JSON-RPC payload inside."""
    return json.loads(decrypt_envelope(envelope, symkey_hex))


# ---------------------------------------------------------------------------
# Pairing URI parsing
# ---------------------------------------------------------------------------
def parse_pairing_uri(uri: str) -> Dict[str, Any]:
    """Parse a ``wc:`` pairing URI.

    Format (WC2):
        ``wc:<topic>@2?relay-protocol=<p>&relay-data=<d>&symKey=<hex>&...``

    Returns ``{topic, version, relay_protocol, relay_data?, symkey, methods?}``.
    """
    uri = uri.strip()
    if not uri.startswith("wc:"):
        raise ValueError("not a WalletConnect pairing URI (missing 'wc:' prefix)")
    body = uri[len("wc:") :]
    topic, _, rest = body.partition("@")
    version = 2
    if rest:
        ver_part, _, query = rest.partition("?")
        if ver_part.isdigit():
            version = int(ver_part)
    else:
        query = ""
    params: Dict[str, str] = {}
    if query:
        for pair in query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
    if "symKey" not in params:
        raise ValueError("pairing URI missing symKey")
    if not topic:
        raise ValueError("pairing URI missing topic")
    return {
        "topic": topic,
        "version": version,
        "relay_protocol": params.get("relay-protocol", "irn"),
        "relay_data": params.get("relay-data"),
        "symkey": params["symKey"],
        "methods": params.get("methods"),
    }


def derive_pairing_topic(symkey_hex: str) -> str:
    """Recompute the pairing topic from its symKey (== ``hash_key``).

    The dApp sets ``topic = hashKey(symKey)``; we verify/derive the same.
    """
    return hash_key(symkey_hex)


# ---------------------------------------------------------------------------
# Relay-auth JWT (EdDSA) — minimal did:key issuer, no third-party dep
# ---------------------------------------------------------------------------
# multicodec prefix for an Ed25519 public key: varint 0xed 0x01.
# base58btc("\xed\x01") == "K36"  (verified against @walletconnect/relay-auth).
_MULTICODEC_ED25519 = b"\xed\x01"


def did_key_from_ed25519_public(public_key_32: bytes) -> str:
    """``did:key:z<base58btc(0xed01 || pubKey)>`` — the WC client id."""
    import base58

    return "did:key:z" + base58.b58encode(_MULTICODEC_ED25519 + public_key_32).decode("ascii")


def _ed25519_sign(signing_input: bytes, seed_32: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(seed_32).sign(signing_input)


def sign_relay_jwt(
    ed25519_seed: bytes,
    relay_url: str,
    *,
    sub: Optional[str] = None,
    ttl_seconds: int = 86400,
) -> str:
    """Sign an EdDSA relay-auth JWT.

    Payload (matches ``@walletconnect/relay-auth`` ``signJWT``):
        ``{iss, sub, aud, iat, exp}`` with ``iss`` a did:key derived from the
        Ed25519 public key. ``iat``/``exp`` are **seconds** (WC
        ``fromMiliseconds`` floors ms/1000).

    The relay URL is the ``aud``; ``sub`` is a random session id when omitted.
    """
    import uuid

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if len(ed25519_seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    pub = Ed25519PrivateKey.from_private_bytes(ed25519_seed).public_key().public_bytes(
        encoding=_RAW,
        format=_RAW_PUB,
    )
    header = {"alg": "EdDSA", "typ": "JWT"}
    import time

    iat = int(time.time())
    payload = {
        "iss": did_key_from_ed25519_public(pub),
        "sub": sub or str(uuid.uuid4()),
        "aud": relay_url,
        "iat": iat,
        "exp": iat + ttl_seconds,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    ).encode("ascii")
    sig = _ed25519_sign(signing_input, ed25519_seed)
    return signing_input.decode("ascii") + "." + _b64url(sig)


def client_id_from_seed(ed25519_seed: bytes) -> str:
    """The persistent ``clientId`` (did:key) for a 32-byte ed25519 seed."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pub = Ed25519PrivateKey.from_private_bytes(ed25519_seed).public_key().public_bytes(
        encoding=_RAW,
        format=_RAW_PUB,
    )
    return did_key_from_ed25519_public(pub)


__all__ = [
    "TYPE_0",
    "TYPE_1",
    "TYPE_2",
    "generate_symkey",
    "x25519_generate_keypair",
    "x25519_public_from_private",
    "derive_symkey",
    "hash_key",
    "hash_message",
    "encrypt_envelope",
    "decrypt_envelope",
    "encode_payload",
    "decode_payload",
    "parse_pairing_uri",
    "derive_pairing_topic",
    "did_key_from_ed25519_public",
    "sign_relay_jwt",
    "client_id_from_seed",
]
