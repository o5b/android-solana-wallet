# AGENTS.md

Instructions for AI agents (Kilo). Read this first to start fast in a new session.

## What this is

Experimental Solana wallet in **Python + Flet** (Flutter web/desktop), targeting Android.
The blockchain layer in `src/solana/` is **hand-rolled** (no `solana-py`/`solders`).
The app is **fully modular**: `src/main.py` is a 39-line entry point (`build_app`), and
all UI + bootstrap live in `src/ui/` (extracted in a completed Phase-7 refactor).
Wallets are stored in Flet `shared_preferences` (browser localStorage in web mode),
under keys `wallet.<timestamp>`, as JSON. Secrets (`private_key_hex`, `words`,
`secret_key_base58`) are **encrypted at rest** with a PIN-derived key once a PIN is
set (see Security below); the PIN itself is never stored. `address_base58` and
`public_key_hex` stay plaintext so the wallet list can render without unlocking.

## Layout

- `src/main.py` — 39-line entry point: `async def main(page): await build_app(page)` +
  `flet.run(main)`. All real logic is in `src/ui/`.
- `src/ui/` — the UI package:
  - `app.py` — `build_app(page)`: bootstrap (page config, `AppContext`, shared controls,
    `NavigationBar`, homepage View), the per-route View builders (one `build_*_page(ctx)`
    per screen), the `route_change` dispatcher, `view_pop` back-nav, and the bootstrap
    tail (`auto_lock_watcher` task + `refresh_lock_state` PIN gate).
  - `context.py` — `AppContext` dataclass: shared app state (`page`, `session`,
    `lang`, PIN constants, `controls` registry) passed as the first arg to every
    `ui/` function. Wraps the live `main()` objects **by reference**. Accessors:
    `is_unlocked()`, `reset_activity()`, `safe_update()`, `close_dialog()`,
    `get_wallet_private_key()`, `has_wallet_private_key()`,
    `decrypt_for_display()`, `encrypt_for_storage()`, `t()`/`tp()` (i18n).
  - `experience.py` — Simple/Pro/Developer mode registry (`feature()` matrix,
    `get_experience`/`set_experience`). The single source of truth for which
    features each mode may see.
  - `i18n.py` — switchable **English + Русский** translation layer
    (`t()`/`tp()` plurals, `get_lang`/`set_lang`). Pure Python, 0 dependencies.
  - `security_gate.py` — PIN gate (setup/unlock modals), auto-lock watcher,
    plaintext-wallet migration, `clear_client_storage` (wipe).
  - `wallets.py` — `load_wallets(ctx)`: canonical wallet-record loader.
  - `formatting.py` — `short_addr(addr)` helper.
  - `qr.py` — `generate_qr_base64()` (pure, no flet dep).
  - `components/` — reusable screen/control builders, each taking `ctx`:
    - `priority_fee.py` — Auto/Low/Med/High/Custom priority-fee selector block.
    - `addressbook.py` — contacts + address-poisoning gate.
    - `balance.py` — wallet cards (homepage), address page, balance + history screens.
    - `transfer.py` — SOL/SPL transfer pages, burn/close.
    - `swap.py` — Jupiter swap page.
    - `staking.py` — Liquid staking page.
    - `nft.py` — NFT gallery page.
    - `walletconnect.py` — WalletConnect v2 responder UI.
    - `wallet_create.py` — create/recover/add-wallet pages.
    - `settings.py` — Settings page (theme, experience mode, language).
    - `more.py` — More hub (feature-gated navigation).
    - `devtools.py` — Developer tools (simulation/RPC/raw-key/storage inspectors).
- `src/solana/` — blockchain logic:
  - `balance.py` — SOL/SPL balances, metadata parsing, transfer-cost estimate.
    `get_sol_spl_balance(address, networks, include_transfer_cost=True,
    include_image_bytes=True)` — the two optional flags skip the slow per-token
    paths (priority-fee `calculate_total_transfer_cost` RPC + raw image-byte
    download) that the NFT gallery (`nft.get_nfts`) doesn't need.
  - `transfer_sol.py` — SOL transfer + `confirm_transaction` (confirmation polling)
  - `spl_token.py` — SPL/Token-2022 transfer, airdrop, ATA helpers, **burn/close**:
    `burn_instruction`/`close_account_instruction` (InstructionType BURN=8 /
    CLOSE_ACCOUNT=9), `get_ata_raw_amount` (reads exact on-chain balance so a full
    burn has no float drift), `burn_token` (partial), `close_token_account`
    (rent refund, requires 0 balance), `burn_and_close_token_account` (burns full
    balance + closes the ATA in one tx — the "return rent" flow)
  - `create_wallet.py` — BIP39 + BIP32-ed25519 key derivation, recovery
  - `transaction_history.py` — tx history (parallel fetch)
  - `history_csv.py` — CSV formatter for normalized transaction history. Produces one
    row per SOL-only transaction or per SPL token delta, with network, UTC timestamp,
    signature, status, SOL change, fee, token data, slot/version/CU fields.
  - `swap.py` — Jupiter Swap API V2 (quote + assembled V0 tx; **mainnet-only**).
    `swap()` retries up to 3 orders looking for an `ALLOWED_PROGRAM_IDS`-safe route.
  - `liquid_staking.py` — curated mainnet LST registry (JitoSOL/mSOL/bSOL/jupSOL);
    `stake_sol`/`unstake_sol`/`get_stake_quote`/`get_lst_positions`.
  - `prices.py` — USD price feeds via Jupiter Price API V3 (`get_prices`,
    `enrich_balance_result_with_prices`, `fmt_usd`/`fmt_change`)
  - `spam_filter.py` — **spam / scam token filter** (balance screen). Classifies each
    `get_sol_spl_balance` token as `spam`/`suspicious`/`clean` via curated
    `KNOWN_GOOD_MINTS`/`KNOWN_SPAM_MINTS` registries, symbol **impersonation**, suspicious
    text (URL/bait words), and on-chain `mintAuthority`/`freezeAuthority` risk.
    `enrich_balance_result_with_spam_filter` mutates tokens in place and runs AFTER
    `prices.py` so a real Jupiter `usd_price` (liquidity) downgrades an isolated
    open-mint hit to clean. Never raises.
  - `nft.py` — **NFT gallery data layer**: `get_nfts(address, networks)` collects an
    address's NFTs by reusing `get_sol_spl_balance(..., include_transfer_cost=False,
    include_image_bytes=False)` (fast), filtering `decimals==0 && amount>=1` (same
    heuristic as Phantom/Solflare).
  - `compute_budget.py` — ComputeBudget program ix builders for **priority fees**:
    `set_compute_unit_limit` (disc 2) / `set_compute_unit_price` (disc 3) +
    `priority_fee_instructions(price, cu_limit)` (→ `[]` when price=0 = no fee)
  - `versioned_transaction.py` — V0 (versioned) tx signing/serialization for swaps
  - `wallet_standard.py` — **dApp signing capability layer** (transport-agnostic):
    `sign_message`/`verify_message` (ed25519 — refuses to sign a payload that parses as
    a transaction message, so it can't be abused as a tx-signing oracle),
    `sign_transaction`/`sign_and_send_transaction` (enforce fee-payer==signer,
    single-signer, refuse unknown programs unless `allow_unknown_programs=True`),
    `preview_transaction` (ALT-safe), and SIWS (`SIWSPayload` plain class +
    `sign_in_with_solana`). `KNOWN_PROGRAMS` is the canonical program registry.
  - `simulation.py` — **transaction simulation & preview** (anti-phishing):
    `analyze_transaction(tx_b64, network, signer_pubkey=)` runs unsigned-safe
    `simulateTransaction` + `getFeeForMessage`, returns real per-account SOL/token
    deltas, compute units, fee, status/error, warnings. Degrades to
    `status="simulation_failed"` instead of raising. Called before any dApp sign.
  - `security.py` — PIN key derivation (scrypt) + Fernet secret encryption, PIN
    verification, wallet encrypt/decrypt + migration of legacy plaintext records
  - `sns.py` — Solana Name Service `.sol` resolver (mainnet-only, read-only)
  - `validators.py`, `keypair.py`, `publickey.py`, `transaction.py`, `commitment.py`, ...
  - `transfer_spl.py` — OLD sync version (unused; do not edit)
  - **WalletConnect v2** (dApp transport, hand-rolled — no WC JS SDK):
    - `wc2_crypto.py` — relay-interop-critical crypto: X25519 ECDH +
      HKDF-SHA256 → symKey, ChaCha20-Poly1305 AEAD, envelope encoding,
      `parse_pairing_uri`, EdDSA relay-auth JWT. Bit-compatible with
      `@walletconnect/utils` 2.23.10.
    - `wc2_relay.py` — async WebSocket relay client (`wss://relay.walletconnect.com`)
      speaking IRN JSON-RPC, with JWT auth + auto-reconnect.
    - `walletconnect.py` — `WalletConnectClient` sign client (responder): pair →
      `wc_sessionPropose` → approve → settle → route `wc_sessionRequest` through
      `wallet_standard` + `simulation` → respond. Maps `solana_signTransaction` /
      `solana_signAndSendTransaction` / `solana_signMessage` / `solana_signIn`.
      Never holds wallet keys (resolves a signer per-account via `signer_resolver`).
- `src/assets/` — images
- `tests/` — headless/integration tests (run with
  `PYTHONPATH=src venv/bin/python tests/<file>.py`). 18 suites, ~600 checks:
  `test_app_ui`, `test_balance_ui`, `test_transfer_ui`, `test_security_gate_ui`,
  `test_settings_ui`, `test_more_ui`, `test_dev_storage_ui`, `test_wallet_create_ui`,
  `test_swap_ui`, `test_i18n`, `test_address_check`, `test_sns`, `test_history_csv`,
  `test_spam_filter`, `test_priority_fee`, `test_burn_close`, `test_liquid_staking`,
  `test_wc2_integration`.
- `venv/` — project venv (use it for every python/flet command)
- `devnet-wallets.txt` / `mainnet-wallets.txt` — test private keys (**gitignored**, see below)

## Networks (Solana RPC)

- mainnet-beta: `https://api.mainnet-beta.solana.com`
- testnet:      `https://api.testnet.solana.com`
- devnet:       `https://api.devnet.solana`  ← **use devnet for any transfer/test**

Public RPCs are heavily rate-limited (HTTP 429, `retry-after`). Public devnet/testnet
faucets (`requestAirdrop`) frequently return `"Internal error"` — do not depend on them
to fund fresh wallets; reuse the funded devnet wallets in `devnet-wallets.txt`.

## Run the app

Web mode (needed for Playwright testing):

```bash
venv/bin/flet run --web --port 8550 src/main.py
```

Start it with the `background_process` tool and a readiness probe on port `8550`.
Desktop GUI also works when `DISPLAY` is set.

## Headless testing (fast — no UI)

Import app functions directly. Two rules: **workdir must be `src/`** and **use the
absolute path to the venv python** (`<repo>/venv/bin/python`).

```python
from solana.balance import get_sol_balance, get_sol_spl_balance
from solana.transfer_sol import transfer_sol_token, confirm_transaction
from solana.spl_token import transfer_spl_token, request_airdrop
```

Prefer headless for balance checks and transfers — it exercises the same code paths
as the UI without the brittleness of Flutter automation.

## Playwright UI testing — gotchas

> **Read [`info/ui-testing-playbook.md`](info/ui-testing-playbook.md) FIRST** — it is the
> consolidated, battle-tested reference for every recurring pitfall (PIN field
> concatenation, shared_preferences JSON-encoding + cache, Dropdown overlay, navigation).
> The notes below are the short version.

1. **Flet = Flutter web.** After `navigate`, the page only shows an "Enable accessibility"
   button. The button is outside the viewport (direct click times out). Enable semantics
   via JS once:
   ```js
   () => { const el = document.querySelector('flt-semantics-placeholder'); if (el) el.click(); }
   ```
   Then use `playwright_browser_snapshot` / click.
2. **Fill text fields with `keyboard.type`, not `fill`/`fill_form`.** Flutter textboxes
   ignore DOM `fill` and values **concatenate** if you fill twice. To replace an existing
   value: click the field → `Ctrl+A` → `keyboard.type`. See playbook §3–§4.
3. **PIN fields** are the #1 trap: filling "Create PIN" then "Confirm PIN" with `fill`
   merges both into one field → "PINs do not match." Use the per-field keyboard pattern
   in playbook §3. The test PIN is **`1234`**.
4. **Flet `Dropdown` popups** can be opened via paired `pointerdown`/`pointerup` on
   `flt-glass-pane` with an explicit `pointerId: 1` at the trigger centre (coords from a
   `boxes:true` snapshot). See playbook §6.1. Alternatively, to change a persisted setting
   (e.g. experience mode), set it via `localStorage` and open a **new tab**.
5. **shared_preferences values are JSON-encoded** in web localStorage: the app stores
   `"pro"` as `'"pro"'` (inner quotes). A raw `setItem('...', 'pro')` is silently
   ignored. See playbook §7.
6. **The shared_preferences in-memory cache survives same-tab reloads.** After changing
   `localStorage`, open a **new tab** (`browser_tabs` → `close` → `new`) —
   `location.reload()` / `page.goto()` do NOT rebuild the cache. See playbook §7.
7. Navigate via the **AppBar "More" action button** (top-right icon), not the navbar
   "More" tab (unreliable). See playbook §5.
8. Form fields are **global objects** — values persist across page navigations. Clear/replace
   before reuse.
9. Network selection is via 3 checkboxes (mainnet-beta/testnet/devnet) on the address page.
10. **CanvasKit draws `Text` on the canvas** — `browser_find`/DOM text walks return nothing
    for output text, so assert on the rendered control set or re-run the logic headlessly.
    To assert e.g. a swap succeeded, either re-fetch the on-chain balance headlessly after
    the click, or read the `print`-flushed debug line in the background-process server log.
11. **Auto-lock kills long UI tests.** `AUTO_LOCK_SECONDS = 300` (in `ui/app.py`) means after
    5 min of no `route_change` the app locks and click handlers silently stop. For long
    tests, bump it or navigate frequently to reset activity.

## Test devnet wallets

Private keys live in `devnet-wallets.txt` (**gitignored**). Public addresses:

- **W1**: `AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz` (SOL + Token-2022)
- **W2**: `EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr` (SOL + Token-2022)

Common Token-2022 mint they both hold: `Ejxf4ZKJnyCbgHdEAkWhaR7qjGvT7vpMYxiAeWyLG62b` (9 decimals).

## App unlock PIN

The running app's PIN-gate PIN is **`1234`**. Use it to unlock the app (e.g. for
Playwright UI smoke tests) without asking the user. The PIN is never stored by
the app itself (only a scrypt salt + encrypted verifier are); this note exists
only for local testing convenience.

## Ready-to-run operations

All snippets below: run with `workdir = <repo>/src` and interpreter `<repo>/venv/bin/python`.

### Check balance (SOL) of a wallet

```python
import asyncio
from solana.balance import get_sol_balance
NET = "https://api.devnet.solana.com"
async def main():
    print(await get_sol_balance("AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz", NET))
asyncio.run(main())
```

### Transfer SOL on devnet (W1 → W2)

```python
import asyncio
from solana.transfer_sol import transfer_sol_token
from solana.balance import get_sol_balance
W1, W1P = "AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz", "<W1_PRIV from devnet-wallets.txt>"
W2 = "EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr"
NET = "https://api.devnet.solana.com"
async def main():
    res = await transfer_sol_token(W1, W1P, W2, 0.1, NET)
    print("SUCCESS" if isinstance(res, dict) and "result" in res else res)
asyncio.run(main())
```

### Verify a submitted transaction landed (after any transfer)

```python
import httpx
SIG = "<signature from sendTransaction result>"
NET = "https://api.devnet.solana.com"
r = httpx.post(NET, json={"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses",
                          "params":[[SIG],{"searchTransactionHistory":True}]})
v = r.json()["result"]["value"][0]
print(v["confirmationStatus"], v["err"])  # e.g. "finalized", None
```

> Note: `get_sol_balance` uses `{"commitment": "confirmed"}`, so it's fresh right after
> a transfer. You can also verify via the signature status above.

### Real mainnet swap (Jupiter Swap API V2)

`mainnet-wallets.txt` has 2 funded mainnet wallets: W1
(`FFde1VgK4kLJanDoBitXw3GAJ3UqWVRA8pd815Pwk5T4`, ~0.054 SOL) and W2
(`HdNLnrxGJvHEXYYf2YLBv3mNSpxchN2FDs5EGHa115Q3`). Both have SOL + USDC.

Headless is faster + more reliable than the UI for a real swap:

```python
from solana.swap import swap as jup_swap
res = await jup_swap(input_mint, output_mint, amount_base_units,
                     signer_address, private_key_hex,
                     slippage_bps=100, network='https://api.mainnet-beta.solana.com')
# res['signature'], res['outAmount'], res['confirmation']['result']['value'][0]
```

`amount` is in **base units** (lamports for SOL, micro-USDC for USDC),
NOT in SOL — `get_quote` will HTTP-400 "amount cannot be parsed" if you pass a float.
Use `int(round(sol * 1e9))` / `int(round(usdc * 1e6))`.

UI swap path: balance screen SOL row → "Swap" → `swap-page`. The handler chain is
`on_go_to_swap_page` (in `balance.py`, adapter for `go_to_swap_page_click` in
`swap.py`) → builds `get_quote_button_click` + `swap_button_click` as nested closures.
Quote is cached and refuses to swap if inputs changed since quote.

### Transfer via UI (slower, fragile)

Add wallet (Recover with private key, or Add address) → **Show More** → tick **devnet** →
**Show Balance** → **Transfer this token** → enter amount + recipient → **Transfer SOL**.
If the wallet was added without a private key, an "Enter Secret" field appears on the transfer page.

## Before/after code changes

- Syntax check:
  ```bash
  venv/bin/python -c "import py_compile; py_compile.compile('src/solana/<file>.py', doraise=True)"
  ```
- Run the relevant offline test suites (`PYTHONPATH=src venv/bin/python tests/<file>.py`).
- After touching transfer/balance code, smoke-test with a real devnet transfer (W1→W2)
  and confirm the function returns a `result` key (not an `error`).

## UI architecture & conventions

The app was refactored from a ~5400-line monolithic `main.py` into the modular
`src/ui/` package (Phase 7, complete). Follow these conventions when editing or
adding UI code:

### AppContext migration contract

Every `ui/` function takes `ctx: AppContext` as its first arg (replaces implicit
closure capture of `page` / `session`):
- `ctx.page` / `ctx.session` are the **same live objects** `build_app` created —
  `AppContext` wraps by reference, never copies.
- Use `ctx.safe_update()` instead of the inline `try: page.update() except: pass`.
- Pure helpers (no `page`/`session` need) take explicit args and need no `ctx`.
- `solana/` business layer is **never touched** by UI changes.

### Per-session state

- **Per-session mutable state → `ctx.session`, NEVER module-level.** `build_app` is
  invoked **per connected client in web mode**, so module-level state would bleed
  across sessions. The in-memory `session` dict holds the PIN-derived Fernet key,
  `last_activity`, `_poisoning_confirmed` set, `_wc_state`, etc.
- **No module-level mutable state** in any `ui/` module.

### Async handlers

- **Async `(ctx, e)` module handlers wired from `build_app` → named `async def`
  adapter closures, NEVER `lambda`.** flet 0.82.2's `__fire_event` only awaits
  handlers that pass `inspect.iscoroutinefunction`; a plain
  `lambda ev: async_fn(ctx, ev)` is a sync lambda returning a coroutine → flet calls
  it, drops the coroutine, and the handler silently never runs (only signalled by a
  `RuntimeWarning: coroutine ... was never awaited`). Always wrap with
  `async def on_X(e): await fn(ctx, e)` defined inside `build_app` so it captures `ctx`.
  (`functools.partial(async_fn, ctx)` also works, but named adapters are the convention.)

### Shared view chrome & routes

- Shared view chrome (`view_pop` back-nav, `navbar`) + all `el_*` Column holders live
  in `ctx.controls["..."]`, registered during `build_app` bootstrap.
- **Adding a new route**: (a) a `build_*_page(ctx)` in the module that owns the screen;
  (b) one `elif page.route == ...` branch in `build_app`'s `route_change`.
- **Rebuild-on-enter pattern**: pages with dynamic content define an async
  `*_enter(ctx)` hook called by the `route_change` branch to repopulate the shared
  `el_*` Column on each visit.

### Wallet-key / secret access

- Wallet-key resolution → `ctx.get_wallet_private_key(wallet)` /
  `ctx.has_wallet_private_key(wallet)` (`""` while locked).
- Record decryption for display → `ctx.decrypt_for_display(wallet)`.
- Record encryption on save → `ctx.encrypt_for_storage(value)`.
- Wallet-record load → `ui.wallets.load_wallets(ctx)`.
- PIN gate / wipe / migration → `ui.security_gate` module functions
  (`refresh_lock_state`, `lock_app`, `clear_client_storage`,
  `migrate_plaintext_wallets`). Never reach into `solana.security` primitives directly
  from UI code.

### Invariant

`homepage.controls[-1]` is the wallets ListView — `route_change` refreshes it on every
navigation via `get_wallets_cards(ctx)`. Preserve this when editing the homepage.

### i18n conventions

The app supports **English + Русский** via `src/ui/i18n.py`. Follow these rules when
adding or changing user-facing text:

1. **Translation → `ctx.t(msg_key, **fmt)`** (never hardcode user-facing text in a
   `flet.Text/Button(...)`). `msg_key` is a stable snake_case id, NOT the English text.
2. **The lookup-key param is named `msg_key`, not `key`** — keeps `key` free as a
   template placeholder (e.g. `ctx.t("del_ok", key=storage_key)`).
3. **Plurals → `ctx.tp(key_plural, key_singular, n)`** (RU rule: `n%10==1 and n%100!=11`
   → singular, else plural; EN always plural).
4. **NEVER translate**: routes (`"-page"`), `shared_preferences` keys (`"ui."`/`wc.`),
   icon/enum names, debug `print(...)` logs, addresses/sigs, ISO-8601 dates, currency
   formatting (`fmt_usd`/`fmt_change` in `solana/prices.py`), `short_addr(...)`.
5. **Live switch → update `ctx.lang` + call the route's `*_enter(ctx)` rebuild hook**
   (every page already has one).

### flet headless-testing gotchas

1. `flet.Page.update` is NOT a coroutine (`inspect.iscoroutinefunction` → False) —
   calling it un-awaited is correct.
2. `ElevatedButton("text")` / `TextButton("text")` store the label in `.content` as a
   plain `str`, NOT in `.text` (unset), NOT in a `flet.Text` control (only when you
   explicitly pass `content=flet.Text(...)`). Headless button-lookup helpers must check
   `isinstance(c.content, str)` first.
3. flet registers event handlers in an internal registry, so `control.on_click` reads
   as `None` outside a live session — **headless tests can only assert control
   *construction*, not click wiring**; verify clicks via Playwright or by re-running
   the underlying `solana/` function.
4. Reusable headless recipe: mock `page` with async `shared_preferences` + sync
   `update()`, monkeypatch the slow RPC, assert on the built control structure.

### Experience modes

`ui/experience.py` gates feature visibility across **Simple / Pro / Developer** modes.
`feature(name, mode)` **fails open to all modes** on an unknown feature key (so a typo
can never hide e.g. Send). The More hub, balance/history detail, priority-fee controls,
and dev tools are all gated through it. Mode is persisted under `ui.experience`.

## Android APK build + release signing

Build + sign the app as a real Android APK (no `solana-py`/`solders`). Dependencies are
in `pyproject.toml [project.dependencies]` (the source of truth — NOT repo-root
`requirements.txt`).

### Build toolchain (already provisioned on this machine — `flet build apk` finds them)

- Flutter **3.41.4** → `/home/oleg/flutter/3.41.4/bin/flutter`
- JDK **17.0.13+11** → `/home/oleg/java/17.0.13+11` (flet sets `--jdk-dir` to it)
- Android SDK → `/home/oleg/Android/sdk` (use this, not `~/.android` which is empty).
  System `java` is JDK 21 — fine for `keytool`, but Gradle uses JDK 17.

### How flet packages Python for Android

`serious_python` downloads a standalone CPython 3.12.9, installs requirements against an
**extra PyPI index `https://pypi.flet.dev`** (pre-built Android wheels), and bundles
`app/app.zip`. The pins must match what `pypi.flet.dev` serves for `cp312 android_24_*`:
- `pillow==12.2.0` (not 12.3.0 — PyPI-only), `websockets==16.0` (not 16.1),
  `cryptography==43.0.1`, `PyNaCl==1.5.0`.
- Check available versions before bumping a pin:
  `curl -s https://pypi.flet.dev/<pkg>/ | grep -oE '<pkg>-[0-9][^.]*'`.
- `requires-python = >=3.12` (matches embedded CPython 3.12.9).

### Release signing — use `apksigner`, NOT flet's `--android-signing-*` flags

flet's built-in signing passes creds to Gradle via env vars and a long-lived Gradle
daemon can read a stale password → build fails with `keystore password was incorrect`.
The reliable workflow is build (debug-signed) → `zipalign` → `apksigner sign`:

```bash
STOREPASS=<password>
# keystore created once (gitignored): release.keystore, alias=solana
#   creds live in android-signing.txt (also gitignored)
keytool -genkeypair -v -keystore release.keystore -alias solana \
  -keyalg RSA -keysize 2048 -validity 9125 \
  -storepass "$STOREPASS" -keypass "$STOREPASS" \
  -dname "CN=Solana Wallet, O=SolanaWallet, C=RU"
flet build apk -v
BT=/home/oleg/Android/sdk/build-tools/34.0.0
"$BT/zipalign" -f -p 4 build/apk/solana_wallet_v3.apk build/apk/aligned.apk
"$BT/apksigner" sign --ks release.keystore --ks-key-alias solana \
  --ks-pass "pass:$STOREPASS" --key-pass "pass:$STOREPASS" \
  --out build/apk/solana_wallet_v3-release.apk build/apk/aligned.apk
"$BT/apksigner" verify --verbose build/apk/solana_wallet_v3-release.apk
```

### Output + install

- Fat APK ≈87 MB (all 4 ABIs); for a ~25–30 MB phone APK use `--split-per-abi` + install
  **arm64-v8a**. First build ≈ 17 min; incremental ≈ 1–2 min.
- **Play Protect blocks debug-signed APKs** ("App not installed"). Release-sign with
  `apksigner` (above). If still blocked, disable "Scan apps with Play Protect"
  (Play Store → profile → Play Protect → gear) or tap "More details → Install anyway".
- **Keystore hygiene**: `release.keystore` + `android-signing.txt` are gitignored —
  never commit. Losing the keystore means a new signature → can't update the same
  package id (users must uninstall+reinstall).

## Security

- Private keys and mnemonics are stored **encrypted at rest** (Fernet) once a PIN is set;
  they are decrypted into memory only while the app is unlocked. Never log, print, or copy
  them unless explicitly required for a test.
- The PIN is never stored; only a salt + encrypted verifier token are. Losing the PIN makes
  encrypted secrets unrecoverable (the only option is "Forgot PIN?" → wipe all wallets).
- `devnet-wallets.txt` and `mainnet-wallets.txt` are gitignored. Keep all keys out of the
  repo and use a mainnet key only after the user explicitly authorizes a real transaction.
- Devnet wallets hold no real value; `mainnet-wallets.txt` contains real keys and must
  never be logged, printed, or copied into source files.
