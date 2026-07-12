"""WalletConnect v2 relay client (async WebSocket transport).

Speaks the IRN JSON-RPC protocol over a WebSocket to ``wss://relay.walletconnect.com``:

    irn_subscribe   {topic}                  -> subscriptionId
    irn_unsubscribe {id, topic}
    irn_publish     {topic, message, ttl, tag, prompt?}
    irn_subscription (server push)           {id, data:{topic, message, publishedAt}}

Authentication is an EdDSA JWT (``wc2_crypto.sign_relay_jwt``) carrying a
``did:key`` ``iss``; the relay identifies us by that client id.

This layer is deliberately crypto-agnostic: it only shuffles opaque envelope
strings between topics. Encryption/decryption + the session state machine live
in :mod:`solana.walletconnect`.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Set
import asyncio
import json
import logging

import websockets
from websockets.exceptions import ConnectionClosed

from solana.wc2_crypto import sign_relay_jwt

log = logging.getLogger("wc2.relay")

DEFAULT_RELAY_URL = "wss://relay.walletconnect.com"
DEFAULT_PUBLISH_TTL = 2592000  # 30 days, max relay TTL

# Callback shape: async fn(event_type: str, data: dict) -> None
#   "message"  -> {"topic","message","publishedAt"}
#   "status"   -> {"connected": bool}
EventHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]


class RelayError(RuntimeError):
    def __init__(self, rpc_error: Dict[str, Any]):
        super().__init__(f"relay rpc error: {rpc_error}")
        self.rpc_error = rpc_error


class RelayClient:
    """Persistent, auto-reconnecting IRN relay transport."""

    def __init__(
        self,
        project_id: str,
        identity_seed: bytes,
        *,
        relay_url: str = DEFAULT_RELAY_URL,
        on_event: Optional[EventHandler] = None,
    ) -> None:
        if len(identity_seed) != 32:
            raise ValueError("identity_seed must be 32 bytes (ed25519)")
        self.project_id = project_id
        self.identity_seed = identity_seed
        self.relay_url = relay_url
        self.on_event: EventHandler = on_event or _noop_event
        self._ws: Optional[Any] = None
        self._send_lock = asyncio.Lock()
        self._next_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._subs: Dict[str, str] = {}          # topic -> subscription id
        self._want_topics: Set[str] = set()      # topics to (re)subscribe
        self._run_task: Optional[asyncio.Task] = None
        self._stop = False
        self._connected = asyncio.Event()
        self._serve_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ life cycle
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def start(self) -> None:
        if self._run_task and not self._run_task.done():
            return
        self._stop = False
        self._run_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._serve_task:
            self._serve_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass
        self._connected.clear()
        await self.on_event("status", {"connected": False})

    async def wait_connected(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    # ------------------------------------------------------------------ run loop
    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                await self._open_and_serve()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("relay connection dropped: %r", e)
                self._connected.clear()
                await self.on_event("status", {"connected": False})
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _open_and_serve(self) -> None:
        jwt = sign_relay_jwt(self.identity_seed, self.relay_url)
        url = f"{self.relay_url}?auth={jwt}&projectId={self.project_id}"
        log.info("opening relay ws %s (projectId=%s)", self.relay_url, _redact(self.project_id))
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=2**22,
            open_timeout=20,
        ) as ws:
            self._ws = ws
            self._connected.set()
            await self.on_event("status", {"connected": True})
            # Re-establish subscriptions after (re)connect.
            topics = list(self._want_topics)
            for topic in topics:
                try:
                    await self._send_subscribe(topic)
                except Exception as e:  # noqa: BLE001
                    log.warning("resubscribe %s failed: %r", _redact(topic), e)
            # Serve incoming traffic.
            self._serve_task = asyncio.current_task()
            async for raw in ws:
                if self._stop:
                    break
                await self._handle_frame(raw)

    # ------------------------------------------------------------------ framing
    async def _handle_frame(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            log.debug("ignoring non-json frame")
            return
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        if method == "irn_subscription":
            await self._on_subscription(msg)
            return
        # Response to one of our requests.
        rid = msg.get("id")
        if rid is not None and ("result" in msg or "error" in msg):
            fut = self._pending.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        log.debug("unhandled relay frame: %s", method)

    async def _on_subscription(self, msg: Dict[str, Any]) -> None:
        params = msg.get("params") or {}
        data = params.get("data") or {}
        topic = data.get("topic")
        message = data.get("message")
        published_at = data.get("publishedAt")
        # Acknowledge receipt so the relay stops redelivering.
        await self._reply_ack(msg.get("id"))
        if topic and message is not None:
            try:
                await self.on_event("message", {
                    "topic": topic,
                    "message": message,
                    "publishedAt": published_at,
                })
            except Exception as e:  # noqa: BLE001
                log.exception("on_event(message) handler raised: %r", e)

    async def _reply_ack(self, rid: Optional[int]) -> None:
        if rid is None or self._ws is None:
            return
        try:
            async with self._send_lock:
                await self._ws.send(json.dumps({"id": rid, "jsonrpc": "2.0", "result": True}))
        except ConnectionClosed:
            pass

    # ------------------------------------------------------------------ requests
    async def _send_raw(self, frame: Dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("relay not connected")
        async with self._send_lock:
            await self._ws.send(json.dumps(frame))

    async def _request(self, method: str, params: Dict[str, Any], *, timeout: float = 30.0) -> Any:
        if self._ws is None:
            raise RuntimeError("relay not connected")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        frame = {"id": rid, "jsonrpc": "2.0", "method": method, "params": params}
        try:
            await self._send_raw(frame)
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._pending.pop(rid, None)
            raise
        if "error" in resp:
            raise RelayError(resp["error"])
        return resp.get("result")

    # ------------------------------------------------------------------ IRN API
    async def subscribe(self, topic: str) -> str:
        self._want_topics.add(topic)
        sub_id = await self._send_subscribe(topic)
        self._subs[topic] = sub_id
        log.debug("subscribed to %s -> %s", _redact(topic), _redact(sub_id))
        return sub_id

    async def _send_subscribe(self, topic: str) -> str:
        return await self._request("irn_subscribe", {"topic": topic})

    async def unsubscribe(self, topic: str) -> None:
        self._want_topics.discard(topic)
        sub_id = self._subs.pop(topic, None)
        if sub_id is None:
            return
        try:
            await self._request("irn_unsubscribe", {"id": sub_id, "topic": topic})
        except Exception as e:  # noqa: BLE001
            log.debug("unsubscribe %s failed (ignored): %r", _redact(topic), e)

    async def publish(
        self,
        topic: str,
        message: str,
        *,
        tag: int,
        ttl: int = DEFAULT_PUBLISH_TTL,
        prompt: bool = False,
    ) -> None:
        await self._request("irn_publish", {
            "topic": topic,
            "message": message,
            "ttl": ttl,
            "tag": tag,
            "prompt": prompt,
        })


def _redact(s: str) -> str:
    if not isinstance(s, str) or len(s) <= 12:
        return "***"
    return s[:6] + "…" + s[-4:]


async def _noop_event(_event: str, _data: Dict[str, Any]) -> None:
    return None


__all__ = ["RelayClient", "RelayError", "DEFAULT_RELAY_URL", "DEFAULT_PUBLISH_TTL"]
