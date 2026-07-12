"""WalletConnect v2 sign client (Solana wallet / responder).

Glues the :mod:`wc2_crypto` + :mod:`wc2_relay` layers into the session state
machine and routes incoming dApp ``solana_*`` requests through the existing
``wallet_standard`` signing surface and ``simulation`` anti-phishing preview.

Implemented flows (wallet = *responder*):
    pair(uri)                     -> subscribe pairing topic, await proposal
    on wc_sessionPropose (1100)   -> UI callback; approve()/reject()
    approve():                    -> generate X25519 key, derive session topic,
                                     send propose-response (1101) + settle (1102)
    on wc_sessionRequest (1108)   -> simulate + UI callback; approve/reject
                                     -> result (1109) routed to wallet_standard
    on wc_sessionDelete (1112)    -> drop session
    on wc_sessionPing (1114)      -> ack (1115)

The client never holds wallet private keys directly: it asks the host (main.py)
for a signer via the injected ``signer_resolver`` so secrets stay encrypted at
rest until a request is explicitly approved.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional
import asyncio
import base64
import logging
import time
import uuid

import base58

from solana import wc2_crypto as crypto
from solana.wc2_relay import RelayClient, RelayError, DEFAULT_RELAY_URL

log = logging.getLogger("wc2.sign")

# ---------------------------------------------------------------------------
# Constants — verified against @walletconnect/sign-client 2.23.10
# ---------------------------------------------------------------------------
RELAY_PROTOCOL = "irn"

# IRN publish tags for the sign protocol (req / res pairs).
TAG_SESSION_PROPOSE = 1100
TAG_SESSION_PROPOSE_RES = 1101
TAG_SESSION_SETTLE = 1102
TAG_SESSION_SETTLE_RES = 1103
TAG_SESSION_REQUEST = 1108
TAG_SESSION_REQUEST_RES = 1109
TAG_SESSION_DELETE = 1112
TAG_SESSION_DELETE_RES = 1113
TAG_SESSION_PING = 1114
TAG_SESSION_PING_RES = 1115

TTL_SESSION = 604800      # 7 days
TTL_REQUEST = 604800 * 3  # SEVEN_DAYS*3 (relay caps at 30d = 2592000)
TTL_PROPOSE = 604800
TTL_PING = 300

# CAIP-2 Solana chain ids -> RPC URLs.
SOLANA_CHAINS: Dict[str, str] = {
    "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": "https://api.mainnet-beta.solana.com",
    "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1": "https://api.devnet.solana.com",
    "solana:4uhcVJyU9pJkvQyS88uRDiswHXSCkY3z": "https://api.testnet.solana.com",
}
DEFAULT_CHAIN = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

# Methods we advertise.
SOLANA_METHODS = [
    "solana_signTransaction",
    "solana_signAndSendTransaction",
    "solana_signMessage",
    "solana_signIn",
]

# WC SDK error codes (subset used for rejections).
SDK_ERRORS: Dict[str, Dict[str, Any]] = {
    "USER_REJECTED": {"code": 5000, "message": "User rejected the request."},
    "USER_REJECTED_CHAINS": {"code": 5001, "message": "User disapproved requested chains."},
    "UNSUPPORTED_CHAINS": {"code": 5002, "message": "Requested chains are not supported."},
    "UNSUPPORTED_METHODS": {"code": 5003, "message": "Requested methods are not supported."},
    "USER_DISCONNECTED": {"code": 6000, "message": "User disconnected the session."},
}


def chain_to_rpc(chain_id: Optional[str]) -> str:
    if not chain_id:
        return SOLANA_CHAINS[DEFAULT_CHAIN]
    if chain_id in SOLANA_CHAINS:
        return SOLANA_CHAINS[chain_id]
    # Tolerate bare references / "solana:<ref>" variants already covered above.
    return SOLANA_CHAINS[DEFAULT_CHAIN]


# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------
# Proposal: async fn(proposal: dict) -> None
ProposalHandler = Callable[[Dict[str, Any]], Awaitable[None]]
# Session request: async fn(session: dict, request: dict, preview: dict) -> None
#   request = {id, topic, method, params, chain_id}
RequestHandler = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Awaitable[None]]
# Generic session lifecycle: async fn(event: str, session: dict) -> None
SessionHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
# Resolve a wallet private key for an account (base58 pubkey) -> private_key_hex
SignerResolver = Callable[[str], Awaitable[Optional[str]]]


class WalletConnectError(RuntimeError):
    pass


def _jsonrpc_request(rpc_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rpc_id, "jsonrpc": "2.0", "method": method, "params": params}


def _jsonrpc_result(rpc_id: int, result: Any) -> Dict[str, Any]:
    return {"id": rpc_id, "jsonrpc": "2.0", "result": result}


def _jsonrpc_error(rpc_id: int, code: int, message: str) -> Dict[str, Any]:
    return {"id": rpc_id, "jsonrpc": "2.0", "error": {"code": code, "message": message}}


def _next_id() -> int:
    # WC uses a monotonic int derived from Date.now()+random; a UUID-int is unique enough.
    return abs(uuid.uuid4().int >> 64) % (2**31)


class WalletConnectClient:
    """High-level WalletConnect v2 sign client for a Solana wallet."""

    def __init__(
        self,
        project_id: str,
        identity_seed: bytes,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        relay_url: str = DEFAULT_RELAY_URL,
        signer_resolver: Optional[SignerResolver] = None,
        on_proposal: Optional[ProposalHandler] = None,
        on_request: Optional[RequestHandler] = None,
        on_session: Optional[SessionHandler] = None,
        relay: Optional[Any] = None,
    ) -> None:
        if len(identity_seed) != 32:
            raise ValueError("identity_seed must be 32 bytes (ed25519)")
        self.project_id = project_id
        self.identity_seed = identity_seed
        self.metadata = metadata or {
            "name": "Solana Flet Wallet",
            "description": "Hand-rolled Solana wallet",
            "url": "https://example.com",
            "icons": [],
        }
        self.signer_resolver = signer_resolver
        self.on_proposal = on_proposal
        self.on_request = on_request
        self.on_session = on_session

        self.client_id = crypto.client_id_from_seed(identity_seed)

        # Crypto/topic state.
        self._pairings: Dict[str, Dict[str, Any]] = {}   # pairing_topic -> {symkey}
        self._sessions: Dict[str, Dict[str, Any]] = {}    # session_topic -> {symkey, peer, ...}
        self._proposals: Dict[int, Dict[str, Any]] = {}   # rpc id -> proposal
        self._pending_req: Dict[int, Dict[str, Any]] = {} # request id -> {topic, method, params, chain_id}
        self._next_rpc_id = int(time.time() * 1000) % (2**31)

        self.relay: Any = relay if relay is not None else RelayClient(
            project_id,
            identity_seed,
            relay_url=relay_url,
            on_event=self._on_relay_event,
        )
        # Always bind the event callback (injected relays need it too).
        self.relay.on_event = self._on_relay_event  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ startup
    async def start(self) -> None:
        await self.relay.start()

    async def stop(self) -> None:
        await self.relay.stop()

    async def wait_connected(self, timeout: float = 30.0) -> None:
        await self.relay.wait_connected(timeout=timeout)

    @property
    def connected(self) -> bool:
        return self.relay.connected

    def list_sessions(self) -> List[Dict[str, Any]]:
        return [dict(s, topic=t) for t, s in self._sessions.items()]

    # ------------------------------------------------------------------ pairing
    async def pair(self, uri: str) -> str:
        """Connect to a dApp from a ``wc:`` pairing URI.

        Subscribes to the pairing topic and waits (asynchronously, via
        :attr:`on_proposal`) for the dApp's ``wc_sessionPropose``.
        Returns the pairing topic.
        """
        info = crypto.parse_pairing_uri(uri)
        topic = info["topic"]
        symkey = info["symkey"]
        # Prefer the dApp-provided topic (it equals hash_key(symkey)); store the symkey.
        self._pairings[topic] = {"symkey": symkey}
        await self.relay.subscribe(topic)
        log.info("paired on topic %s, awaiting proposal", topic[:10] + "…")
        return topic

    # --------------------------------------------------------------- proposals
    async def approve(
        self,
        proposal_id: int,
        accounts: List[str],
        *,
        methods: Optional[List[str]] = None,
        events: Optional[List[str]] = None,
        chains: Optional[List[str]] = None,
    ) -> str:
        """Approve a session proposal. Returns the new session topic.

        ``accounts`` are CAIP-10 account ids (``solana:<ref>:<addr>``) or bare
        base58 addresses (auto-prefixed with the first requested chain).
        """
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            raise WalletConnectError(f"no pending proposal {proposal_id}")
        params = proposal.get("params", {})
        proposer = params.get("proposer", {})
        proposer_pub = proposer.get("publicKey")
        if not proposer_pub:
            raise WalletConnectError("proposal missing proposer.publicKey")
        pairing_topic = proposal.get("pairingTopic")
        pairing = self._pairings.get(pairing_topic) if pairing_topic else None
        if not pairing:
            raise WalletConnectError("pairing vanished before approve")

        # Resolve namespace constraints from required/optional namespaces.
        req_ns = params.get("requiredNamespaces", {}) or {}
        req_chains = _collect_chains(req_ns) or [DEFAULT_CHAIN]
        if chains is None:
            chains = req_chains
        methods = methods or SOLANA_METHODS
        events = events or []

        # Normalise accounts to CAIP-10.
        caip10 = _to_caip10_accounts(accounts, chains[0] if chains else DEFAULT_CHAIN)
        namespaces = {
            "solana": {
                "accounts": caip10,
                "methods": methods,
                "events": events,
                "chains": chains,
            }
        }
        required_namespaces = _conform_required(req_ns, chains, methods, events)

        # 1. Generate our X25519 responder keypair + derive the session topic.
        my_priv, my_pub = crypto.x25519_generate_keypair()
        session_symkey = crypto.derive_symkey(my_priv, proposer_pub)
        session_topic = crypto.hash_key(session_symkey)
        self._sessions[session_topic] = {
            "symkey": session_symkey,
            "topic": session_topic,
            "peer": proposer,
            "peerMetadata": proposer.get("metadata", {}),
            "pairingTopic": pairing_topic,
            "namespaces": namespaces,
            "accounts": caip10,
            "expiry": int(time.time()) + TTL_SESSION,
            "acknowledged": False,
            "selfPublicKey": my_pub,
            "controller": my_pub,
        }
        await self.relay.subscribe(session_topic)

        # 2. Reply to the sessionPropose request with our responder public key.
        propose_result = {"relay": {"protocol": RELAY_PROTOCOL}, "responderPublicKey": my_pub}
        await self._publish_jsonrpc(
            pairing_topic,
            pairing["symkey"],
            _jsonrpc_result(proposal_id, propose_result),
            tag=TAG_SESSION_PROPOSE_RES,
            ttl=TTL_PROPOSE,
        )

        # 3. Send wc_sessionSettle on the session topic.
        settle_params = {
            "relay": {"protocol": RELAY_PROTOCOL},
            "namespaces": namespaces,
            "requiredNamespaces": required_namespaces,
            "optionalNamespaces": {},
            "sessionProperties": params.get("sessionProperties", {}),
            "controller": {"publicKey": my_pub, "metadata": self.metadata},
            "expiry": int(time.time()) + TTL_SESSION,
        }
        settle_id = self._rpc_id()
        await self._publish_jsonrpc(
            session_topic,
            session_symkey,
            _jsonrpc_request(settle_id, "wc_sessionSettle", settle_params),
            tag=TAG_SESSION_SETTLE,
            ttl=TTL_SESSION,
        )
        self._sessions[session_topic]["acknowledged"] = True
        if self.on_session:
            await self.on_session("approved", self._sessions[session_topic])
        log.info("session settled on topic %s", session_topic[:10] + "…")
        return session_topic

    async def reject(self, proposal_id: int, *, reason: str = "USER_REJECTED") -> None:
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            return
        pairing_topic = proposal.get("pairingTopic")
        pairing = self._pairings.get(pairing_topic) if pairing_topic else None
        err = SDK_ERRORS.get(reason, SDK_ERRORS["USER_REJECTED"])
        if pairing:
            await self._publish_jsonrpc(
                pairing_topic,
                pairing["symkey"],
                _jsonrpc_error(proposal_id, err["code"], err["message"]),
                tag=TAG_SESSION_PROPOSE_RES,
                ttl=TTL_PROPOSE,
            )

    async def disconnect_session(self, session_topic: str, *, reason: str = "USER_DISCONNECTED") -> None:
        session = self._sessions.pop(session_topic, None)
        if session is None:
            return
        err = SDK_ERRORS[reason]
        rid = self._rpc_id()
        try:
            await self._publish_jsonrpc(
                session_topic,
                session["symkey"],
                _jsonrpc_request(rid, "wc_sessionDelete", {"code": err["code"], "message": err["message"]}),
                tag=TAG_SESSION_DELETE,
                ttl=TTL_PING,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("sessionDelete publish failed: %r", e)
        await self.relay.unsubscribe(session_topic)
        if self.on_session:
            await self.on_session("deleted", session)

    # ------------------------------------------------------------- request i/o
    async def respond_request(self, request_id: int, result: Any) -> None:
        req = self._pending_req.pop(request_id, None)
        if req is None:
            raise WalletConnectError(f"no pending request {request_id}")
        session = self._sessions.get(req["topic"])
        if session is None:
            raise WalletConnectError("session for request vanished")
        await self._publish_jsonrpc(
            req["topic"],
            session["symkey"],
            _jsonrpc_result(request_id, result),
            tag=TAG_SESSION_REQUEST_RES,
            ttl=TTL_REQUEST,
        )

    async def reject_request(self, request_id: int, *, reason: str = "USER_REJECTED") -> None:
        req = self._pending_req.pop(request_id, None)
        if req is None:
            return
        session = self._sessions.get(req["topic"])
        if session is None:
            return
        err = SDK_ERRORS.get(reason, SDK_ERRORS["USER_REJECTED"])
        await self._publish_jsonrpc(
            req["topic"],
            session["symkey"],
            _jsonrpc_error(request_id, err["code"], err["message"]),
            tag=TAG_SESSION_REQUEST_RES,
            ttl=TTL_REQUEST,
        )

    async def approve_request(self, request_id: int, signer_private_hex: str) -> None:
        """Sign + respond to a pending ``solana_*`` request with the given key."""
        req = self._pending_req.get(request_id)
        if req is None:
            raise WalletConnectError(f"no pending request {request_id}")
        result = await self._execute_method(req["method"], req["params"], req.get("chain_id"), signer_private_hex)
        await self.respond_request(request_id, result)

    # ------------------------------------------------------------- internals
    def _rpc_id(self) -> int:
        rid = self._next_rpc_id
        self._next_rpc_id += 1
        return rid

    async def _publish_jsonrpc(
        self, topic: str, symkey: str, payload: Dict[str, Any], *, tag: int, ttl: int
    ) -> None:
        envelope = crypto.encode_payload(payload, symkey)
        await self.relay.publish(topic, envelope, tag=tag, ttl=ttl)

    def _symkey_for_topic(self, topic: str) -> Optional[str]:
        if topic in self._pairings:
            return self._pairings[topic]["symkey"]
        if topic in self._sessions:
            return self._sessions[topic]["symkey"]
        return None

    async def _on_relay_event(self, event: str, data: Dict[str, Any]) -> None:
        if event != "message":
            return
        topic = data["topic"]
        symkey = self._symkey_for_topic(topic)
        if symkey is None:
            log.debug("message on unknown topic %s", topic[:8] + "…")
            return
        try:
            payload = crypto.decode_payload(data["message"], symkey)
        except Exception as e:  # noqa: BLE001
            log.warning("failed to decrypt message on %s: %r", topic[:8] + "…", e)
            return
        await self._dispatch(topic, payload)

    async def _dispatch(self, topic: str, payload: Dict[str, Any]) -> None:
        method = payload.get("method")
        rpc_id = payload.get("id")
        if method is not None:
            # It's a JSON-RPC request directed at us.
            if method == "wc_sessionPropose":
                await self._on_propose(topic, payload)
            elif method == "wc_sessionRequest":
                await self._on_session_request(topic, payload)
            elif method == "wc_sessionPing":
                await self._on_ping(topic, rpc_id)
            elif method == "wc_sessionDelete":
                await self._on_delete(topic, rpc_id)
            else:
                log.debug("unhandled method %s", method)
        else:
            # Response to one of our outgoing requests (settle ack, etc.).
            log.debug("received response for id=%s", rpc_id)

    async def _on_propose(self, pairing_topic: str, payload: Dict[str, Any]) -> None:
        params = payload.get("params", {})
        proposal = {
            "id": payload.get("id"),
            "pairingTopic": pairing_topic,
            "params": params,
            "proposer": params.get("proposer", {}),
            "requiredNamespaces": params.get("requiredNamespaces", {}),
            "optionalNamespaces": params.get("optionalNamespaces", {}),
            "relays": params.get("relays", []),
        }
        self._proposals[proposal["id"]] = proposal
        log.info(
            "session proposal from %s",
            (proposal.get("proposer", {}).get("metadata", {}) or {}).get("name", "unknown dApp"),
        )
        if self.on_proposal:
            try:
                await self.on_proposal(proposal)
            except Exception as e:  # noqa: BLE001
                log.exception("on_proposal handler raised: %r", e)

    async def _on_session_request(self, topic: str, payload: Dict[str, Any]) -> None:
        session = self._sessions.get(topic)
        if session is None:
            log.warning("sessionRequest on unknown session %s", topic[:8] + "…")
            return
        params = payload.get("params", {}) or {}
        inner = params.get("request", {}) or {}
        method = inner.get("method")
        inner_params = inner.get("params", {})
        chain_id = params.get("chainId") or inner.get("chainId")
        rpc_id = payload.get("id")
        if method not in SOLANA_METHODS:
            log.warning("rejecting unsupported session method %s", method)
            err = SDK_ERRORS["UNSUPPORTED_METHODS"]
            await self._publish_jsonrpc(
                topic,
                session["symkey"],
                _jsonrpc_error(rpc_id, err["code"], err["message"]),
                tag=TAG_SESSION_REQUEST_RES,
                ttl=TTL_REQUEST,
            )
            return
        request = {
            "id": rpc_id,
            "topic": topic,
            "method": method,
            "params": inner_params,
            "chain_id": chain_id,
        }
        self._pending_req[rpc_id] = request
        preview = await self._build_preview(method, inner_params, chain_id)
        if self.on_request:
            try:
                await self.on_request(session, request, preview)
            except Exception as e:  # noqa: BLE001
                log.exception("on_request handler raised: %r", e)
                # Don't orphan the request: tell the dApp we rejected it.
                try:
                    await self.reject_request(rpc_id, reason="USER_REJECTED")
                except Exception:  # noqa: BLE001
                    self._pending_req.pop(rpc_id, None)

    async def _on_ping(self, topic: str, rpc_id: Optional[int]) -> None:
        session = self._sessions.get(topic)
        if session and rpc_id is not None:
            await self._publish_jsonrpc(
                topic, session["symkey"], _jsonrpc_result(rpc_id, True), tag=TAG_SESSION_PING_RES, ttl=TTL_PING
            )

    async def _on_delete(self, topic: str, rpc_id: Optional[int]) -> None:
        session = self._sessions.pop(topic, None)
        if session is None:
            # Already cleaned up (e.g. relay echo of our own disconnect) —
            # don't fire a duplicate "deleted" callback or ack our own message.
            return
        if rpc_id is not None:
            await self._publish_jsonrpc(
                topic, session["symkey"], _jsonrpc_result(rpc_id, True), tag=TAG_SESSION_DELETE_RES, ttl=TTL_PING
            )
        await self.relay.unsubscribe(topic)
        if self.on_session:
            await self.on_session("deleted", session)

    # -------------------------------------------------- preview / execution
    async def _build_preview(self, method: str, params: Any, chain_id: Optional[str]) -> Dict[str, Any]:
        """Anti-phishing preview for an incoming request (best-effort, never raises)."""
        preview: Dict[str, Any] = {"method": method, "chain_id": chain_id}
        try:
            if method in ("solana_signTransaction", "solana_signAndSendTransaction"):
                tx_b64 = _extract_tx(params)
                if tx_b64:
                    decoded = _safe_preview_transaction(tx_b64)
                    preview["decoded"] = decoded
                    # The fee payer is the account that will sign (enforced by
                    # wallet_standard); label it as "you" in the simulation so
                    # personal outflow/drain warnings fire.
                    signer_pubkey = decoded.get("fee_payer") if isinstance(decoded, dict) else None
                    sim = await _safe_simulate(
                        tx_b64, chain_to_rpc(chain_id), signer_pubkey=signer_pubkey
                    )
                    if sim:
                        preview["simulation"] = sim
            elif method == "solana_signMessage":
                preview["message_utf8"] = _maybe_utf8(_extract_message(params))
            elif method == "solana_signIn":
                preview["siws"] = params if isinstance(params, dict) else None
        except Exception as e:  # noqa: BLE001
            preview["preview_error"] = repr(e)
        return preview

    async def _execute_method(
        self, method: str, params: Any, chain_id: Optional[str], signer_private_hex: str
    ) -> Dict[str, Any]:
        from solana import wallet_standard as ws

        network = chain_to_rpc(chain_id)
        if method == "solana_signMessage":
            msg_b64 = _extract_message(params)
            msg_bytes = base64.b64decode(msg_b64) if msg_b64 else b""
            res = ws.sign_message(signer_private_hex, msg_bytes)
            return {"signature": res["signature"]}
        if method == "solana_signIn":
            payload = params if isinstance(params, dict) else {}
            res = ws.sign_in_with_solana(signer_private_hex, payload)
            return {
                "signature": res["signature"],
                "signedMessage": base64.b64encode(res["message"].encode("utf-8")).decode("ascii"),
                "method": "signIn",
            }
        if method == "solana_signTransaction":
            tx_b64 = _extract_tx(params)
            signed = ws.sign_transaction(
                signer_private_hex, tx_b64, allow_unknown_programs=True
            )
            return {
                "signature": _first_signature_base58(signed["signed_transaction"]),
                "signedTransaction": signed["signed_transaction"],
            }
        if method == "solana_signAndSendTransaction":
            tx_b64 = _extract_tx(params)
            res = await ws.sign_and_send_transaction(
                signer_private_hex, tx_b64, network, allow_unknown_programs=True, confirm=False
            )
            return {"signature": res["signature"]}
        raise WalletConnectError(f"unsupported method {method}")


# ---------------------------------------------------------------------------
# Helpers (parameter extraction, namespace conformance)
# ---------------------------------------------------------------------------
def _extract_tx(params: Any) -> Optional[str]:
    if not isinstance(params, dict):
        return None
    return params.get("transaction") or params.get("txn")


def _extract_message(params: Any) -> Optional[str]:
    if not isinstance(params, dict):
        return None
    return params.get("message")


def _maybe_utf8(b64_msg: Optional[str]) -> Optional[str]:
    if not b64_msg:
        return None
    try:
        raw = base64.b64decode(b64_msg)
        return raw.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def _safe_preview_transaction(tx_b64: str) -> Dict[str, Any]:
    try:
        from solana.wallet_standard import preview_transaction

        return preview_transaction(tx_b64)
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)}


async def _safe_simulate(
    tx_b64: str, network: str, *, signer_pubkey: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    try:
        from solana.simulation import analyze_transaction

        return await analyze_transaction(tx_b64, network, signer_pubkey=signer_pubkey)
    except Exception as e:  # noqa: BLE001
        return {"simulation_failed": repr(e)}


def _first_signature_base58(signed_tx_b64: str) -> str:
    """Extract the first (fee-payer) signature of a signed wire transaction."""
    raw = base64.b64decode(signed_tx_b64)
    count = raw[0]
    if count == 0:
        raise WalletConnectError("signed transaction has no signatures")
    return base58.b58encode(raw[1 : 1 + 64]).decode("ascii")


def _collect_chains(required_namespaces: Dict[str, Any]) -> List[str]:
    chains: List[str] = []
    for ns in required_namespaces.values():
        for c in (ns or {}).get("chains", []) or []:
            if c not in chains:
                chains.append(c)
    return chains


def _to_caip10_accounts(accounts: List[str], default_chain: str) -> List[str]:
    out: List[str] = []
    for acct in accounts:
        if acct.count(":") >= 2:  # already CAIP-10
            out.append(acct)
        else:
            out.append(f"{default_chain}:{acct}")
    return out


def _conform_required(
    required_namespaces: Dict[str, Any], chains: List[str], methods: List[str], events: List[str]
) -> Dict[str, Any]:
    """Echo back required namespaces conformed to the chains we actually serve."""
    out: Dict[str, Any] = {}
    for key, ns in (required_namespaces or {}).items():
        out[key] = {
            "chains": chains,
            "methods": methods,
            "events": events,
        }
    return out


__all__ = [
    "WalletConnectClient",
    "WalletConnectError",
    "SOLANA_CHAINS",
    "DEFAULT_CHAIN",
    "SOLANA_METHODS",
    "SDK_ERRORS",
    "chain_to_rpc",
    "RELAY_PROTOCOL",
]
