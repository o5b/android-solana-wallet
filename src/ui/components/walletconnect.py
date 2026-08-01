"""WalletConnect v2 page + dApp signing handlers (extracted from ``main.py``).

Owns the WalletConnect v2 (WC2) responder UI assembled across the 2026-07-12
"WalletConnect v2" and 2026-07-17 "Phase 5" sessions: pair with a dApp via a
``wc:`` URI, render incoming session proposals + signing requests (with the
anti-phishing simulation preview), and list/disconnect active sessions. The
relay/protocol/crypto layer lives in :mod:`solana.walletconnect` (and
:mod:`solana.wc2_relay` / :mod:`solana.wc2_crypto`); this module is purely the
Flet UI + the callbacks the :class:`~solana.walletconnect.WalletConnectClient`
fires back into.

Coupling
--------
* **Per-session state.** The live :class:`WalletConnectClient` owns a relay
  WebSocket + ChaCha20-Poly1305 symkeys, so it MUST be per-session (web mode
  starts one ``main()`` per connected client). It lives in
  ``ctx.session["_wc_state"]`` (a ``{"client": ...}`` dict), NOT at module
  level — mirrors the Group-1 rule that any per-session mutable state goes to
  ``ctx.session``.
* **Wallet-key resolution.** ``_resolve_signer`` decrypts the signer key on
  demand via ``ctx.get_wallet_private_key`` (the ``AppContext`` accessor added
  in Group 3) and reads wallet records via :func:`ui.wallets.load_wallets`.
  Watch-only wallets resolve to ``None``.
* **Shared view chrome** (back button + navbar) come from ``ctx.controls``
  (``view_pop`` / ``navbar``), registered by ``main()`` during bootstrap —
  same pattern as the devtools view builders.
* **Long-lived WC controls** (URI input, projectId input, status text,
  sessions list) are created once by :func:`build_wc_page` and registered in
  ``ctx.controls`` so the module-level handlers can reach them without being
  nested inside the view builder (keeps them unit-testable).

The 4 ``WalletConnectClient`` callbacks (``signer_resolver`` / ``on_proposal``
/ ``on_request`` / ``on_session``) are wired as thin lambdas that forward
``ctx`` into the module-level coroutines below.
"""

import json
import os

import flet

from solana.security import WATCH_ONLY_FIELD
from solana.walletconnect import WalletConnectClient
from ui.context import AppContext
from ui.experience import feature, get_experience
from ui.wallets import load_wallets

WC_PROJECT_ID_KEY = "wc.project_id"
WC_IDENTITY_KEY = "wc.identity_seed"


# ---------------------------------------------------------------------------
# per-session state + persistent prefs
# ---------------------------------------------------------------------------


def _wc_state(ctx: AppContext) -> dict:
    """Lazy per-session ``{"client": WalletConnectClient | None}`` holder.

    The client owns a live relay WebSocket + session symkeys, so it is strictly
    per-session. Stored in ``ctx.session`` (not module level) so it never bleeds
    across connected clients in web mode.
    """
    st = ctx.session.get("_wc_state")
    if st is None:
        st = {"client": None}
        ctx.session["_wc_state"] = st
    return st


async def _get_project_id(ctx: AppContext) -> str | None:
    sp = ctx.page.shared_preferences
    if await sp.contains_key(WC_PROJECT_ID_KEY):
        v = await sp.get(WC_PROJECT_ID_KEY)
        if v:
            return v
    return None


async def _get_identity_seed(ctx: AppContext) -> bytes:
    sp = ctx.page.shared_preferences
    if await sp.contains_key(WC_IDENTITY_KEY):
        v = await sp.get(WC_IDENTITY_KEY)
        try:
            return bytes.fromhex(v)
        except Exception:
            pass
    seed = os.urandom(32)
    await sp.set(WC_IDENTITY_KEY, seed.hex())
    return seed


async def _resolve_signer(ctx: AppContext, account_b58: str | None) -> str | None:
    """Resolve a signer private-key hex for an on-chain account.

    Returns ``None`` for a falsy input, an unknown account, or a watch-only
    wallet; returns ``""`` (empty) while the app is locked or when an encrypted
    secret fails to decrypt. Callers treat both the same way (``if not priv:``),
    matching the legacy ``_wc_resolve_signer`` closure: the WalletConnectClient
    must never hold wallet keys itself, it asks us per request.
    """
    if not account_b58:
        return None
    wallets = await load_wallets(ctx)
    for w in wallets:
        if w.get("address_base58") == account_b58:
            if w.get(WATCH_ONLY_FIELD):
                return None
            try:
                return ctx.get_wallet_private_key(w)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def _dapp_name(obj: dict) -> str:
    md = obj.get("peerMetadata") or obj.get("proposer", {}).get("metadata") or {}
    return md.get("name") or md.get("url") or "dApp"


def _render_preview(ctx: AppContext, preview: dict, show_program_ids: bool = False) -> str:
    """Render a simulation preview dict as the multi-line text shown in the
    request dialog. ``show_program_ids`` (Developer mode) expands the
    unverified-programs list instead of collapsing it to a count."""
    method = preview.get("method")
    lines = [ctx.t("sim_method", val=method), ctx.t("sim_chain", val=preview.get('chain_id'))]
    decoded = preview.get("decoded") or {}
    if decoded.get("programs"):
        lines.append(ctx.t("sim_programs", val=", ".join(decoded["programs"])))
    if decoded.get("unknown_programs"):
        if show_program_ids:
            lines.append(ctx.t("sim_unverified_list", progs=", ".join(decoded["unknown_programs"])))
        else:
            lines.append(ctx.tp("unverified_prog_pl", "unverified_prog_sg", len(decoded['unknown_programs'])))
    sim = preview.get("simulation") or {}
    if sim:
        lines.append(ctx.t("sim_pred_status", val=sim.get("status")))
        if sim.get("fee_sol") is not None:
            lines.append(ctx.t("sim_fee", fee=sim.get("fee_sol")))
        for ch in (sim.get("sol_changes") or [])[:8]:
            acct = str(ch.get("account", ""))
            lines.append(f"SOL Δ {acct[:10]}…: {ch.get('delta_sol', 0):+.9f}")
        for ch in (sim.get("token_changes") or [])[:8]:
            acct = str(ch.get("account", ""))
            lines.append(
                f"Token Δ {acct[:10]}…: {ch.get('delta_amount', '?')} ({ch.get('mint', '')[:8]}…)"
            )
        for w in sim.get("warnings") or []:
            lines.append("⚠ " + w)
    if preview.get("message_utf8") is not None:
        lines.append(ctx.t("sim_message", val=preview["message_utf8"]))
    if preview.get("preview_error"):
        lines.append(ctx.t("sim_preview_error", val=preview["preview_error"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# active-sessions list
# ---------------------------------------------------------------------------


def _make_disconnect_handler(ctx: AppContext, topic: str):
    async def handler(e):
        client = _wc_state(ctx)["client"]
        if client:
            await client.disconnect_session(topic)
        await _refresh_sessions(ctx)

    return handler


async def _refresh_sessions(ctx: AppContext) -> None:
    wc_sessions_list = ctx.controls["wc_sessions_list"]
    wc_sessions_list.controls.clear()
    client = _wc_state(ctx)["client"]
    if client is None or not client.list_sessions():
        wc_sessions_list.controls.append(flet.Text(ctx.t("wc_no_sessions")))
    else:
        for s in client.list_sessions():
            peer_md = s.get("peerMetadata", {}) or {}
            acct_short = ", ".join(a.split(":")[-1] for a in s.get("accounts", []))
            wc_sessions_list.controls.append(
                flet.Card(
                    content=flet.Container(
                        content=flet.Column(
                            [
                                flet.Text(_dapp_name(s), size=16, weight=flet.FontWeight.BOLD),
                                flet.Text(peer_md.get("url") or "", size=11, selectable=True),
                                flet.Text(ctx.t("wc_accounts", accts=acct_short), size=11, selectable=True),
                                flet.Row(
                                    [flet.OutlinedButton(ctx.t("wc_disconnect"), on_click=_make_disconnect_handler(ctx, s["topic"]))]
                                ),
                            ]
                        ),
                        padding=10,
                        width=360,
                    )
                )
            )
    ctx.safe_update()


# ---------------------------------------------------------------------------
# WalletConnectClient callbacks (proposal / request / session)
# ---------------------------------------------------------------------------


async def _on_proposal(ctx: AppContext, proposal: dict) -> None:
    page = ctx.page
    wallets = await load_wallets(ctx)
    addrs = [w.get("address_base58") for w in wallets if w.get("address_base58")]
    if not addrs:
        page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_no_wallets"))))
        return
    dd = flet.Dropdown(
        label=ctx.t("wc_account_to_connect"),
        options=[flet.dropdown.Option(a) for a in addrs],
        value=addrs[0],
        width=320,
    )
    req_ns = proposal.get("requiredNamespaces", {}) or {}
    chains: list = []
    methods: list = []
    for ns in req_ns.values():
        chains += (ns or {}).get("chains", []) or []
        methods += (ns or {}).get("methods", []) or []
    meta = (proposal.get("proposer", {}) or {}).get("metadata", {}) or {}

    async def do_approve(e):
        dlg_p.open = False
        ctx.safe_update()
        client = _wc_state(ctx)["client"]
        try:
            topic = await client.approve(proposal["id"], accounts=[dd.value])
            page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_session_approved", topic=topic[:8]))))
        except Exception as ex:
            page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_approve_failed", err=ex))))

    async def do_reject(e):
        dlg_p.open = False
        ctx.safe_update()
        client = _wc_state(ctx)["client"]
        if client:
            await client.reject(proposal["id"])

    dlg_p = flet.AlertDialog(
        title=flet.Text(ctx.t("wc_connect_to", name=meta.get('name', 'dApp'))),
        content=flet.Column(
            [
                flet.Text(meta.get("url") or "", size=11, selectable=True),
                flet.Text((meta.get("description") or "")[:160], size=11),
                flet.Text(ctx.t("wc_chains", chains=", ".join(chains)), size=12),
                flet.Text(ctx.t("wc_methods", methods=", ".join(methods)), size=12),
                dd,
            ],
            scroll=flet.ScrollMode.AUTO,
            height=280,
        ),
        actions=[
            flet.TextButton(ctx.t("reject"), on_click=do_reject),
            flet.ElevatedButton(ctx.t("approve"), on_click=do_approve),
        ],
        actions_alignment=flet.MainAxisAlignment.END,
    )
    page.show_dialog(dlg_p)


async def _on_request(ctx: AppContext, session: dict, request: dict, preview: dict) -> None:
    page = ctx.page
    rid = request["id"]
    method = request.get("method")
    accounts = [a.split(":")[-1] for a in session.get("accounts", [])]
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    target = params.get("pubkey") if params else None
    if not target and accounts:
        target = accounts[0]
    _wc_mode = await get_experience(page)
    preview_text = _render_preview(ctx, preview, show_program_ids=feature("sim_detail", _wc_mode))

    async def do_approve(e):
        dlg_r.open = False
        ctx.safe_update()
        client = _wc_state(ctx)["client"]
        priv = await _resolve_signer(ctx, target)
        if not priv:
            page.show_dialog(
                flet.AlertDialog(title=flet.Text(ctx.t("wc_no_priv_key", target=target or '?')))
            )
            await client.reject_request(rid)
            return
        try:
            await client.approve_request(rid, priv)
            page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_signed_sent"))))
        except Exception as ex:
            page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_sign_failed", err=ex))))
            await client.reject_request(rid)

    async def do_reject(e):
        dlg_r.open = False
        ctx.safe_update()
        client = _wc_state(ctx)["client"]
        if client:
            await client.reject_request(rid)

    sim = preview.get("simulation") or {}
    sim_fail = sim.get("status") == "error"
    content_controls = [
        flet.Text(ctx.t("wc_account", acct=target or '?'), size=11, selectable=True),
        flet.Text(preview_text, selectable=True, size=12),
    ]
    if sim_fail:
        content_controls.append(
            flet.Text(
                ctx.t("wc_sim_fail_block"),
                color="red",
                size=12,
            )
        )
    # Developer-only (Phase 5): full simulation logs + raw session/request JSON.
    # Reuse _wc_mode (already awaited above) — avoids two extra shared_preferences round-trips.
    if feature("sim_detail", _wc_mode):
        sim_logs = sim.get("logs") or []
        if sim_logs:
            content_controls.append(flet.Text("Simulation logs:", size=11, weight=flet.FontWeight.BOLD))
            log_controls = []
            for log in sim_logs:
                log_color = "red" if ("failed" in str(log).lower() or "error" in str(log).lower()) else "grey"
                log_controls.append(flet.Text(f"• {log}", size=10, color=log_color, selectable=True))
            content_controls.append(
                flet.Container(
                    content=flet.Column(log_controls, spacing=1, scroll=flet.ScrollMode.AUTO),
                    height=120,
                    padding=5,
                    border=flet.border.all(1, "black12"),
                    border_radius=5,
                )
            )
        # SECURITY: scrub relay keying material before dumping. The live
        # WC2 `symkey` (ChaCha20-Poly1305 relay session key) would let
        # anyone decrypt + forge relay messages for this session — its
        # topic is public. The other session fields (peer, accounts,
        # namespaces, public X25519 keys) are dApp-known and safe to show.
        scrubbed_session = {k: v for k, v in session.items() if k != "symkey"}
        try:
            raw_json = json.dumps(
                {"session": scrubbed_session, "request": request, "simulation": sim},
                indent=2,
                default=str,
            )
        except Exception:
            raw_json = "raw JSON unavailable"
        content_controls.append(flet.Text("Raw session/request JSON:", size=11, weight=flet.FontWeight.BOLD))
        content_controls.append(
            flet.Container(
                content=flet.Column(
                    [flet.Text(raw_json, size=9, selectable=True, color=flet.Colors.GREY_700)],
                    spacing=1,
                    scroll=flet.ScrollMode.AUTO,
                ),
                height=160,
                padding=5,
                border=flet.border.all(1, "black12"),
                border_radius=5,
            )
        )
    dlg_r = flet.AlertDialog(
        title=flet.Text(ctx.t("wc_dapp_request", method=method)),
        content=flet.Column(
            content_controls,
            scroll=flet.ScrollMode.AUTO,
            height=380,
        ),
        actions=[
            flet.TextButton(ctx.t("reject"), on_click=do_reject),
            flet.ElevatedButton(ctx.t("approve_sign"), on_click=do_approve, disabled=sim_fail),
        ],
        actions_alignment=flet.MainAxisAlignment.END,
    )
    page.show_dialog(dlg_r)


async def _on_session(ctx: AppContext, event: str, session: dict) -> None:
    await _refresh_sessions(ctx)


# ---------------------------------------------------------------------------
# client lifecycle + static button handlers
# ---------------------------------------------------------------------------


async def _ensure_client(ctx: AppContext) -> WalletConnectClient | None:
    """Get-or-create the per-session WalletConnectClient.

    Reads the projectId from ``shared_preferences`` (falling back to the input
    field), loads/generates the identity seed, constructs the client with this
    module's callbacks, and starts the relay. Returns ``None`` when no
    projectId is configured.
    """
    page = ctx.page
    st = _wc_state(ctx)
    if st["client"] is not None:
        return st["client"]
    pid = await _get_project_id(ctx)
    if not pid:
        pid = (ctx.controls["wc_pid_input"].value or "").strip()
        if pid:
            await page.shared_preferences.set(WC_PROJECT_ID_KEY, pid)
    if not pid:
        return None
    seed = await _get_identity_seed(ctx)
    client = WalletConnectClient(
        pid,
        seed,
        signer_resolver=lambda acct: _resolve_signer(ctx, acct),
        on_proposal=lambda proposal: _on_proposal(ctx, proposal),
        on_request=lambda sess, req, prev: _on_request(ctx, sess, req, prev),
        on_session=lambda evt, sess: _on_session(ctx, evt, sess),
    )
    await client.start()
    st["client"] = client
    ctx.controls["wc_status_text"].value = ctx.t("wc_status_ready", cid=client.client_id[:18])
    ctx.safe_update()
    return client


async def wc_connect_click(ctx: AppContext, e) -> None:
    client = await _ensure_client(ctx)
    if client is None:
        ctx.page.show_dialog(
            flet.AlertDialog(
                title=flet.Text(ctx.t("wc_enter_projectid"))
            )
        )
        return
    uri = (ctx.controls["wc_uri_input"].value or "").strip()
    if not uri.startswith("wc:"):
        ctx.page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_invalid_uri"))))
        return
    try:
        await client.pair(uri)
        ctx.controls["wc_status_text"].value = ctx.t("wc_status_pairing")
        ctx.safe_update()
    except Exception as ex:
        ctx.page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_pair_failed", err=ex))))


async def wc_save_pid_click(ctx: AppContext, e) -> None:
    pid = (ctx.controls["wc_pid_input"].value or "").strip()
    if pid:
        await ctx.page.shared_preferences.set(WC_PROJECT_ID_KEY, pid)
        ctx.page.show_dialog(flet.AlertDialog(title=flet.Text(ctx.t("wc_pid_saved"))))


# ---------------------------------------------------------------------------
# view builder + enter hook
# ---------------------------------------------------------------------------


def build_wc_page(ctx: AppContext) -> flet.View:
    """Build the WalletConnect v2 page View (called once at bootstrap).

    Creates the four long-lived WC controls (URI input, projectId input,
    status text, sessions list) and registers them in ``ctx.controls`` so the
    module-level handlers (:func:`_ensure_client` / :func:`wc_connect_click` /
    :func:`wc_enter` / :func:`_refresh_sessions`) can reach them without being
    nested here. The shared view chrome (back button / navbar) is wired from
    ``ctx.controls`` (registered by ``main()``), matching the devtools view
    builders.
    """
    wc_uri_input = flet.TextField(label=ctx.t("wc_uri_label"), width=340, multiline=True, max_lines=3)
    wc_pid_input = flet.TextField(label="WalletConnect projectId", width=300)
    wc_status_text = flet.Text(ctx.t("wc_status_idle"), size=12, selectable=True)
    wc_sessions_list = flet.Column(spacing=8)
    ctx.controls["wc_uri_input"] = wc_uri_input
    ctx.controls["wc_pid_input"] = wc_pid_input
    ctx.controls["wc_status_text"] = wc_status_text
    ctx.controls["wc_sessions_list"] = wc_sessions_list

    async def _connect(e):
        await wc_connect_click(ctx, e)

    async def _save_pid(e):
        await wc_save_pid_click(ctx, e)

    return flet.View(
        route="wc-page",
        appbar=flet.AppBar(
            title=flet.Text(ctx.t("wc_appbar_title")),
            color="white",
            bgcolor="#8b5cf6",
            leading=flet.IconButton(icon=flet.Icons.ARROW_BACK, on_click=ctx.controls["view_pop"]),
        ),
        navigation_bar=ctx.controls["navbar"],
        horizontal_alignment=flet.CrossAxisAlignment.CENTER,
        scroll=flet.ScrollMode.AUTO,
        controls=[
            flet.Text(ctx.t("wc_connect_heading"), size=26, font_family="Georgia", weight=flet.FontWeight.BOLD),
            flet.Text(
                ctx.t("wc_steps"),
                size=11,
                text_align=flet.TextAlign.CENTER,
            ),
            flet.Row(
                [wc_pid_input, flet.ElevatedButton(ctx.t("wc_save_pid"), on_click=_save_pid)],
                alignment=flet.MainAxisAlignment.CENTER,
            ),
            flet.Divider(),
            flet.Column(
                [wc_uri_input,
                 flet.ElevatedButton(ctx.t("wc_connect_btn"), on_click=_connect, icon=flet.Icons.LINK, width=200)],
                horizontal_alignment=flet.CrossAxisAlignment.CENTER,
            ),
            wc_status_text,
            flet.Divider(),
            flet.Text(ctx.t("wc_active_sessions"), size=16, weight=flet.FontWeight.BOLD),
            wc_sessions_list,
        ],
    )


async def wc_enter(ctx: AppContext) -> None:
    """Refresh the WC page on entry: reload saved projectId, refresh the
    active-sessions list, and ensure the relay client is started."""
    pid = await _get_project_id(ctx)
    ctx.controls["wc_pid_input"].value = pid or ""
    await _refresh_sessions(ctx)
    await _ensure_client(ctx)
