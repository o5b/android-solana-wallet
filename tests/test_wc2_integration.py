"""End-to-end WC2 protocol test with an in-memory mock relay.

Validates the full handshake + solana_* request/response without needing a
real relay projectId. A simulated dApp (proposer) and the WalletConnectClient
(responder) exchange real, crypto-correct envelopes.
"""
import asyncio
import base64
import json
import os
import sys

from solana import wc2_crypto as crypto
from solana import walletconnect as wc
from solana.keypair import Keypair

# ---------------------------------------------------------------------------
# Mock relay: in-memory pub/sub mirroring the IRN relay surface.
# ---------------------------------------------------------------------------
class MockRelay:
    def __init__(self):
        self.subs = {}  # topic -> set[client_id]
        self.clients = {}  # client_id -> on_event
        self.messages = {}  # topic -> list[str] (retained for late subscribers)
        self.connected = True
        self.on_event = None
        self._cid = 0

    def new_client(self):
        self._cid += 1
        cid = self._cid
        return _Adapter(self, cid)

    async def start(self): pass
    async def stop(self): pass
    async def wait_connected(self, timeout=None): pass

    def _subscribe(self, cid, topic):
        self.subs.setdefault(topic, set()).add(cid)
        # Replay retained messages to the late subscriber (real relay retains msgs for TTL).
        cb = self.clients.get(cid)
        if cb:
            for msg in self.messages.get(topic, []):
                asyncio.create_task(cb("message", {"topic": topic, "message": msg}))

    def _unsubscribe(self, cid, topic):
        self.subs.get(topic, set()).discard(cid)

    def _publish(self, topic, message):
        self.messages.setdefault(topic, []).append(message)
        for cid in list(self.subs.get(topic, set())):
            cb = self.clients.get(cid)
            if cb:
                asyncio.create_task(cb("message", {"topic": topic, "message": message}))


class _Adapter:
    """Per-client view of the mock relay — matches RelayClient's used surface."""
    def __init__(self, hub, cid):
        self._hub = hub
        self._cid = cid
        self.on_event = None
        hub.clients[cid] = self._dispatch

    async def _dispatch(self, event, data):
        if self.on_event:
            await self.on_event(event, data)

    @property
    def connected(self): return self._hub.connected

    async def start(self): pass
    async def stop(self): pass
    async def wait_connected(self, timeout=None): pass

    async def subscribe(self, topic):
        self._hub._subscribe(self._cid, topic)
        return f"sub-{self._cid}-{topic[:4]}"

    async def unsubscribe(self, topic):
        self._hub._unsubscribe(self._cid, topic)

    async def publish(self, topic, message, *, tag, ttl=2592000, prompt=False):
        # Simulate async relay delivery.
        loop = asyncio.get_event_loop()
        loop.call_soon(self._hub._publish, topic, message)


# ---------------------------------------------------------------------------
# A simulated dApp (proposer).
# ---------------------------------------------------------------------------
class FakeDapp:
    def __init__(self, hub):
        self.relay = hub.new_client()
        self.relay.on_event = self._on_event
        self.pairing_symkey = crypto.generate_symkey()
        self.pairing_topic = crypto.hash_key(self.pairing_symkey)
        self.privA, self.pubA = crypto.x25519_generate_keypair()
        self.received = []
        self.propose_response = None
        self.session_symkey = None
        self.session_topic = None
        self._settle_id = None

    @property
    def uri(self):
        return f"wc:{self.pairing_topic}@2?relay-protocol=irn&symKey={self.pairing_symkey}"

    async def _on_event(self, event, data):
        if event != "message":
            return
        topic = data["topic"]
        # decrypt
        if topic == self.pairing_topic:
            sym = self.pairing_symkey
        elif topic == self.session_topic:
            sym = self.session_symkey
        else:
            return
        try:
            payload = crypto.decode_payload(data["message"], sym)
        except Exception as e:
            self.received.append(("decrypt_fail", repr(e)))
            return
        self.received.append(payload)
        method = payload.get("method")
        if "result" in payload and not method:
            # propose response
            self.propose_response = payload.get("result")
            rp = self.propose_response or {}
            resp_pub = rp.get("responderPublicKey")
            if resp_pub:
                self.session_symkey = crypto.derive_symkey(self.privA, resp_pub)
                self.session_topic = crypto.hash_key(self.session_symkey)
                await self.relay.subscribe(self.session_topic)

    async def send_propose(self):
        await self.relay.subscribe(self.pairing_topic)
        propose = {
            "id": 9001,
            "jsonrpc": "2.0",
            "method": "wc_sessionPropose",
            "params": {
                "relays": [{"protocol": "irn"}],
                "requiredNamespaces": {
                    "solana": {
                        "chains": [wc.DEFAULT_CHAIN],
                        "methods": ["solana_signTransaction", "solana_signMessage"],
                        "events": [],
                    }
                },
                "optionalNamespaces": {},
                "proposer": {"publicKey": self.pubA, "metadata": {"name": "Test dApp"}},
            },
        }
        env = crypto.encode_payload(propose, self.pairing_symkey)
        await self.relay.publish(self.pairing_topic, env, tag=wc.TAG_SESSION_PROPOSE, ttl=wc.TTL_PROPOSE)

    async def send_request(self, method, params, chain_id, rid):
        req = {
            "id": rid,
            "jsonrpc": "2.0",
            "method": "wc_sessionRequest",
            "params": {"request": {"method": method, "params": params, "chainId": chain_id}, "chainId": chain_id},
        }
        env = crypto.encode_payload(req, self.session_symkey)
        await self.relay.publish(self.session_topic, env, tag=wc.TAG_SESSION_REQUEST, ttl=wc.TTL_REQUEST)

    def find_response(self, rid):
        for p in self.received:
            if p.get("id") == rid and "result" in p:
                return p["result"]
            if p.get("id") == rid and "error" in p:
                return p
        return None


# ---------------------------------------------------------------------------
# Test driver
# ---------------------------------------------------------------------------
async def main():
    hub = MockRelay()
    wallet_adapter = hub.new_client()

    # Wallet (responder) with a real signer.
    seed = os.urandom(32)
    kp = Keypair.from_seed(seed)
    addr = str(kp.public_key)
    priv_hex = seed.hex()

    proposals = []
    requests = []
    sessions = []

    async def on_proposal(p):
        proposals.append(p)

    async def on_request(session, req, preview):
        requests.append((req, preview))

    async def on_session(ev, session):
        sessions.append((ev, session))

    wallet = wc.WalletConnectClient(
        project_id="test",
        identity_seed=os.urandom(32),
        metadata={"name": "Test Wallet"},
        relay=wallet_adapter,
        on_proposal=on_proposal,
        on_request=on_request,
        on_session=on_session,
    )

    dapp = FakeDapp(hub)

    # 1. Pair + propose.
    await wallet.pair(dapp.uri)
    await dapp.send_propose()
    await asyncio.sleep(0.2)
    assert proposals, "wallet never received proposal"
    prop = proposals[0]
    print("1. proposal received from:", prop["proposer"].get("metadata", {}).get("name"))
    assert prop["id"] == 9001

    # 2. Approve.
    session_topic = await wallet.approve(prop["id"], accounts=[addr])
    await asyncio.sleep(0.2)
    assert dapp.propose_response, "dapp never got propose response"
    rp = dapp.propose_response["responderPublicKey"]
    assert dapp.session_topic == session_topic, (dapp.session_topic, session_topic)
    print("2. session settled; topic derived identically by both sides:",
          dapp.session_topic == session_topic)
    # dapp has the settle in its received list
    settle = [p for p in dapp.received if p.get("method") == "wc_sessionSettle"]
    assert settle, "dapp never received settle"
    print("   settle namespaces keys:", list(settle[0]["params"]["namespaces"].keys()))
    assert sessions and sessions[0][0] == "approved"

    # 3. solana_signMessage
    msg = b"hello wc2"
    params = {"pubkey": addr, "message": base64.b64encode(msg).decode()}
    rid = 7001
    await dapp.send_request("solana_signMessage", params, wc.DEFAULT_CHAIN, rid)
    await asyncio.sleep(0.2)
    assert requests, "wallet never got sessionRequest"
    req0, preview0 = requests[-1]
    assert req0["id"] == rid
    print("3. signMessage request received; preview message =", preview0.get("message_utf8"))
    # Approve with the real signer.
    await wallet.approve_request(rid, priv_hex)
    await asyncio.sleep(0.2)
    resp = dapp.find_response(rid)
    assert isinstance(resp, dict) and "signature" in resp, resp
    # Verify the returned signature.
    from solana.wallet_standard import verify_message
    assert verify_message(addr, msg, resp["signature"]), "signature failed verify"
    print("   signMessage response signature verified OK")

    requests.clear()

    # 4. solana_signTransaction (offline-built unsigned System transfer tx).
    # Stub the live simulation so the test stays fast/offline.
    async def _fake_simulate(_tx, _net, *, signer_pubkey=None):
        return {"status": "sim_skipped_in_test"}
    wc._safe_simulate = _fake_simulate

    tx_b64 = await _build_unsigned_tx(None, addr, addr, 0.001, wc.chain_to_rpc(wc.DEFAULT_CHAIN))
    params = {"transaction": tx_b64}
    rid = 7002
    await dapp.send_request("solana_signTransaction", params, wc.DEFAULT_CHAIN, rid)
    await asyncio.sleep(0.2)
    req1, preview1 = requests[-1]
    assert req1["id"] == rid
    print("4. signTransaction request received; preview programs =", preview1.get("decoded", {}).get("programs"))
    print("   simulation status:", (preview1.get("simulation") or {}).get("status"))
    await wallet.approve_request(rid, priv_hex)
    await asyncio.sleep(0.2)
    resp2 = dapp.find_response(rid)
    assert isinstance(resp2, dict) and "signature" in resp2, resp2
    assert "signedTransaction" in resp2
    print("   signTransaction response signature:", resp2["signature"][:16], "...")
    # Verify signature is the fee-payer sig of the returned signed tx.
    raw = base64.b64decode(resp2["signedTransaction"])
    import base58
    first_sig = base58.b58encode(raw[1:65]).decode()
    assert first_sig == resp2["signature"], "returned signature != first sig of signed tx"
    print("   signedTransaction first-sig matches returned signature OK")

    # 5. disconnect
    await wallet.disconnect_session(session_topic)
    await asyncio.sleep(0.2)
    deleted = [s for s in sessions if s[0] == "deleted"]
    assert deleted
    print("5. session disconnect handled")

    print()
    print("ALL WC2 INTEGRATION TESTS PASSED")


async def _build_unsigned_tx(_unused, src, dst, amount, network):
    """Build an unsigned legacy System-transfer wire tx (offline, dummy blockhash)."""
    import base64 as b64
    from solana.transaction import Transaction
    from solana.system_program import transfer, TransferParams
    from solana.publickey import PublicKey

    src_pub = PublicKey(src)
    tx = Transaction(fee_payer=src_pub, recent_blockhash="11111111111111111111111111111111")
    tx.add(transfer(TransferParams(from_pubkey=src_pub, to_pubkey=PublicKey(dst), lamports=int(amount * 1e9))))
    msg_bytes = tx.serialize_message()
    # Wire format: [compact-u16 sig_count=1][64-byte zero placeholder][message]
    unsigned_wire = bytes([1]) + bytes(64) + msg_bytes
    return b64.b64encode(unsigned_wire).decode()


if __name__ == "__main__":
    asyncio.run(main())
