# AGENTS.md

Instructions for AI agents (Kilo). Read this first to start fast in a new session.

## What this is

Experimental Solana wallet in **Python + Flet** (Flutter web/desktop), targeting Android.
The blockchain layer in `src/solana/` is **hand-rolled** (no `solana-py`/`solders`).
All UI + business logic lives in one monolithic `src/main.py` (~2000 lines, nested closures).
Wallets are stored in Flet `shared_preferences` (browser localStorage in web mode),
under keys `wallet.<timestamp>`, as JSON. Secrets (`private_key_hex`, `words`,
`secret_key_base58`) are **encrypted at rest** with a PIN-derived key once a PIN is
set (see Security below); the PIN itself is never stored. `address_base58` and
`public_key_hex` stay plaintext so the wallet list can render without unlocking.

## Layout

- `src/main.py` — UI, route handlers, all click callbacks (the monolith being
  incrementally extracted into `src/ui/` — see "Phase 7" session entry below)
- `src/ui/` — UI package being grown out of `main.py` (Phase 7 refactor):
  - `experience.py` — Simple/Pro/Developer mode registry (`feature()` matrix,
    `get_experience`/`set_experience`). The single source of truth for which
    features each mode may see.
  - `context.py` — `AppContext` dataclass: shared app state (`page`, `session`,
    PIN constants, `controls` registry) passed as the first arg to every
    extracted `ui/` function. Wraps the live `main()` objects **by reference**.
  - `components/` — reusable control builders, each taking `ctx` (e.g.
    `priority_fee.py`). Grows one group at a time.
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
  - `swap.py` — Jupiter Swap API V2 (quote + assembled V0 tx; **mainnet-only**)
  - `prices.py` — USD price feeds via Jupiter Price API V3 (`get_prices`,
    `enrich_balance_result_with_prices`, `fmt_usd`/`fmt_change`)
  - `spam_filter.py` — **spam / scam token filter** (balance screen). Classifies each
    `get_sol_spl_balance` token as `spam`/`suspicious`/`clean` via curated
    `KNOWN_GOOD_MINTS`/`KNOWN_SPAM_MINTS` registries, symbol **impersonation**, suspicious
    text (URL/bait words), and on-chain `mintAuthority`/`freezeAuthority` risk
    (`get_mint_authorities` batches all mints of a network in one `getMultipleAccountsInfo`).
    `enrich_balance_result_with_spam_filter` mutates tokens in place (`token['spam']`) and
    runs AFTER `prices.py` so a real Jupiter `usd_price` (liquidity) downgrades an isolated
    open-mint hit to clean. Never raises.
  - `nft.py` — **NFT gallery data layer**: `get_nfts(address, networks)` collects an
    address's NFTs by reusing `get_sol_spl_balance(..., include_transfer_cost=False,
    include_image_bytes=False)` (fast — skips per-mint priority-fee RPC + raw image
    downloads), filtering `decimals==0 && amount>=1` (same heuristic as Phantom/Solflare).
    `_normalize_image_url` rewrites `ipfs://`→HTTPS gateway; `_normalize_attributes`
    flattens the Metaplex traits array; `_build_nft` normalizes each NFT into
    `{mint, network, amount, decimals, program_id, owner, name, symbol, collection,
    image, uri, description, attributes, external_url}`. Images are returned as URLs.
  - `compute_budget.py` — ComputeBudget program ix builders for **priority fees**:
    `set_compute_unit_limit` (disc 2) / `set_compute_unit_price` (disc 3) +
    `priority_fee_instructions(price, cu_limit)` (→ `[]` when price=0 = no fee)
  - `versioned_transaction.py` — V0 (versioned) tx signing/serialization for swaps
  - `wallet_standard.py` — **dApp signing capability layer** (transport-agnostic):
    `sign_message`/`verify_message` (ed25519, wallet-adapter `signMessage` — refuses
    to sign a payload that parses as a transaction message, so it can't be abused as
    a tx-signing oracle), `sign_transaction`/`sign_and_send_transaction` (sign
    legacy/V0 tx; enforce fee-payer==signer, single-signer, and refuse unknown
    programs unless `allow_unknown_programs=True`), `preview_transaction` (fee payer
    / programs / signers summary, ALT-safe), and SIWS (`SIWSPayload` model with
    newline-control-char validation + address-must-equal-signer binding +
    `format_siws_message` + `sign_in_with_solana`). `KNOWN_PROGRAMS` is the canonical
    program registry; `describe_program()` annotates previews and
    `validate_program_registries()` asserts `swap.ALLOWED_PROGRAM_ID ⊆ KNOWN_PROGRAMS`.
    Reuses `versioned_transaction`, `keypair`, `swap.send_raw_transaction`,
    `transfer_sol.confirm_transaction`. No transport yet (WC2 is the next step).
  - `simulation.py` — **transaction simulation & preview** (anti-phishing):
    `analyze_transaction(tx_b64, network, signer_pubkey=)` runs `simulateTransaction`
    (`sigVerify=false`, `replaceRecentBlockhash=true` — safe on unsigned dApp txs) +
    `getFeeForMessage`, returns `{status, error, compute_units, fee_lamports,
    fee_sol, fee_payer, programs, unknown_programs, sol_changes, token_changes,
    logs, warnings}`. `sol_changes`/`token_changes` are real per-account deltas
    (who pays/receives, incl. token drain) — the core of phishing detection.
    Also exposes `get_fee_for_message`/`get_fee_for_message_bytes` +
    `simulate_transaction_raw`. `analyze_transaction` runs fee + simulation as one
    pooled, parallelized pair of RPC calls, and **degrades** to
    `status="simulation_failed"` (still returning the static preview + fee) instead
    of raising on a `simulateTransaction` error (incl. public-RPC 429 — `_rpc`
    backs off like `confirm_transaction`).
  - `security.py` — PIN key derivation (scrypt) + Fernet secret encryption, PIN
    verification, wallet encrypt/decrypt + migration of legacy plaintext records
  - `validators.py`, `keypair.py`, `publickey.py`, `transaction.py`, `commitment.py`, ...
  - `transfer_spl.py` — OLD sync version (unused; do not edit)
  - **WalletConnect v2** (dApp transport, hand-rolled — no WC JS SDK):
    - `wc2_crypto.py` — relay-interop-critical crypto: X25519 ECDH +
      HKDF-SHA256(salt=zeros32) → symKey, `topic=sha256(hexbytes(symKey))`,
      ChaCha20-Poly1305 AEAD, and the `base64pad([type]‖…)` envelope
      (type 0=direct / 1=x25519 / 2=plain). Plus `parse_pairing_uri` and the
      EdDSA relay-auth JWT (`did:key:z…` issuer, seconds-based iat/exp).
      **Every byte reverse-engineered** from `@walletconnect/utils` 2.23.10 +
      `relay-auth` 1.1.0 + `time` 1.0.2 to be bit-compatible with real dApps.
    - `wc2_relay.py` — async WebSocket relay client (`wss://relay.walletconnect.com`)
      speaking the IRN JSON-RPC (`irn_subscribe/publish/subscription`), with JWT
      auth, JSON-RPC request/response correlation, and auto-reconnect+resubscribe.
    - `walletconnect.py` — `WalletConnectClient` sign client (responder): pair →
      `wc_sessionPropose` (1100) → approve (gen X25519, derive session topic =
      `hashKey(deriveSymKey(myPriv,proposerPub))`, send propose-response 1101 +
      settle 1102) → route `wc_sessionRequest` (1108) through
      `wallet_standard` + `simulation` → respond (1109). Maps `solana_signTransaction`
      / `solana_signAndSendTransaction` / `solana_signMessage` / `solana_signIn`.
      CAIP-2 chains in `SOLANA_CHAINS`. Never holds wallet keys: resolves a signer
      per-account via the injected `signer_resolver` (secrets stay encrypted at rest).
- `src/assets/` — images
- `tests/` — headless/integration tests (run with `PYTHONPATH=src venv/bin/python tests/<file>.py`):
  `test_wc2_integration.py` (mock-relay WC2 protocol), `test_burn_close.py`
  (burn/close instruction encoding + readonly devnet + opt-in destructive), and
  `test_history_csv.py` (offline CSV formatting coverage)
- `venv/` — project venv (use it for every python/flet command)
- `devnet-wallets.txt` — test private keys (**gitignored**, see below)

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
4. **Flet `Dropdown` popups cannot be opened** via Playwright (mouse, keyboard, or DOM
   events). To change a persisted setting (e.g. experience mode), set it via
   `localStorage` and open a **new tab**. See playbook §6–§9.
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

> Note: `get_sol_balance` without `commitment` can return a stale slot immediately after a
> transfer. It now uses `{"commitment": "confirmed"}`, so use that (or verify via the
> signature status above) rather than trusting an instant post-transfer balance.

### Transfer via UI (slower, fragile)

Add wallet (Recover with private key, or Add address) → **Show More** → tick **devnet** →
**Show Balance** → **Transfer this token** → enter amount + recipient → **Transfer SOL**.
If the wallet was added without a private key, an "Enter Secret" field appears on the transfer page.

## Before/after code changes

- Syntax check (no test suite exists):
  ```bash
  venv/bin/python -c "import py_compile; py_compile.compile('src/solana/<file>.py', doraise=True)"
  ```
- After touching transfer/balance code, smoke-test with a real devnet transfer (W1→W2)
  and confirm the function returns a `result` key (not an `error`).

## Known issues & recent fixes (as of last session)

- **FIXED** `confirm_transaction` (`src/solana/transfer_sol.py`): used to raise on public-RPC
  `429` while waiting for `finalized`, reporting a false error even though the transfer had
  succeeded. Now: default commitment `Confirmed`, `429` → backoff + retry, returns last-seen
  status on timeout if the tx was ever observed.
- **FIXED** `get_sol_balance` (`src/solana/balance.py`): now passes `{"commitment": "confirmed"}`
  so the balance is fresh right after a transfer.
- **OPEN** `get_balance_button_click` (`src/main.py`, `except` block ~654): no
  `e.control.disabled = False` → "Show Balance" button stays disabled forever after an error.
- **OPEN** `calculate_total_transfer_cost` (`src/solana/balance.py` ~308): hardcodes
  `rent_fee = 2039280` and `will_create_ata = True` → overestimates cost and can disable valid transfers.
- **OPEN** "Show Balance" on an address holding many tokens (e.g. a mint treasury) can throw
  mid-processing (heavy sequential RPC for metadata + images + priority fees).
- **OPEN** `get_spl_balances` and `get_account_info` in `balance.py` still omit `commitment`
  (same staleness issue as the old `get_sol_balance`).

### Session 2026-07-11

- **FIXED** `get_balance_button_click` (`src/main.py`, `except`/`finally` blocks): now resets
  `e.control.disabled = False` on error and in `finally`, so "Show Balance" no longer stays
  disabled forever after an error. Error text is also displayed in `el_token_balance_data`.
- **FIXED** `calculate_total_transfer_cost` (`src/solana/balance.py`): no longer hardcodes
  `rent_fee = 2039280` / `will_create_ata = True`. When no recipient is given (balance-display
  path), `rent_fee = 0` and `will_create_ata = False`. When `recipient_pubkey` + `program_id`
  are provided, it checks the recipient's ATA via `get_account_info` and only adds rent if the
  ATA does not exist.
- **FIXED** `get_spl_balances` and `get_account_info` (`src/solana/balance.py`): now pass
  `{"commitment": "confirmed"}` in RPC params (same fix as `get_sol_balance`).
- **FIXED** `get_sol_spl_balance` (`src/solana/balance.py`): per-token processing wrapped in
  try/except so a single token's metadata/RPC failure no longer kills the entire balance fetch.
  Failed tokens are still returned with an `error` key.
- **NEW** QR code generation (`generate_qr_base64` in `src/main.py`, uses `qrcode` + `Pillow`).
  QR codes are shown: inline on the address page (160px), in a "Show QR Code" dialog (280px),
  and in the Wallet Info dialog (140px). A "Copy Address" button is also on the address page.

### Session 2026-07-12 (Security)

- **NEW** `src/solana/security.py`: PIN-based key derivation (scrypt N=2^14) + Fernet
  symmetric encryption of wallet secrets. Only a salt + encrypted verifier token are
  stored (`security.pin_salt`, `security.pin_verifier`); the PIN is never persisted.
- **NEW** PIN gate (`src/main.py`): on first launch a "Set up a PIN" modal forces a PIN;
  subsequent sessions show an "Enter PIN" unlock modal. `session["key"]` (Fernet key) is
  held in memory only while unlocked. "Forgot PIN?" wipes the PIN + all wallets (encrypted
  secrets are unrecoverable without the PIN — same model as Phantom/other wallets).
- **NEW** Secrets encrypted at rest: when a PIN exists, new wallets are encrypted on save
  (`encrypt_for_storage`), and existing legacy plaintext wallets are migrated to ciphertext
  on first PIN setup AND defensively on every successful unlock (`migrate_plaintext_wallets`).
  Records carry `secrets_encrypted: true`; `address_base58`/`public_key_hex` stay plaintext
  so the wallet list renders without unlocking. Decrypt-on-demand via `get_wallet_private_key`
  / `decrypt_for_display` (used by transfer/SPL/swap flows + Wallet Info). Watch-only
  wallets (empty secrets) are handled gracefully.
- **NEW** Auto-lock: `auto_lock_watcher` (asyncio task) locks the app after
  `AUTO_LOCK_SECONDS` (300s) of inactivity; `reset_activity()` is called on route changes.
- **NEW** Seed-phrase backup quiz: `generate_new_solana_wallet_button` now shows a reveal
  dialog (the 12 words + warning) then a quiz asking for 2 random word positions before the
  secret card / Save becomes available ("Show words again" returns to the reveal).
- **NEW** Watch-only wallets: "Add Wallet Address" sets `watch_only: true` (no private key);
  the wallet card shows an orange "Watch-only (no private key)" badge. Transfers/swaps on a
  watch-only wallet still prompt for a one-time secret (existing behaviour).

### Session 2026-07-12 (dApp signing)

- **NEW** `src/solana/wallet_standard.py`: transport-agnostic dApp signing capability layer
  (foundation for WalletConnect / Solana Wallet Standard). Functions:
  - `sign_message(pk_hex, msg)` / `verify_message(addr, msg, sig)` — ed25519, wallet-adapter
    `signMessage` parity (str→UTF-8, raw bytes supported). Returns base58 + hex sigs.
  - `sign_transaction(pk_hex, tx_b64)` — signs a serialized legacy/V0 tx **without** broadcasting
    (reuses `versioned_transaction.sign_base64`); enforces single-signer + fee-payer==signer.
  - `sign_and_send_transaction(pk_hex, tx_b64, network, confirm=True)` — sign + broadcast
    (`swap.send_raw_transaction`) + optional confirm (`transfer_sol.confirm_transaction`).
  - `preview_transaction(tx_b64)` — `{version, fee_payer, accounts, required_signatures,
    programs, unknown_programs}` for the future tx-simulation/anti-phishing UI.
  - SIWS: `SIWSPayload` (pydantic, snake_case + camelCase aliases), `format_siws_message`
    (canonical plaintext), `sign_in_with_solana(pk_hex, payload)`.
  - `KNOWN_PROGRAMS` registry + `describe_program()` annotate previews.
  - Verified headless (sign/verify round-trip, SIWS, sign_transaction on a crafted legacy tx,
    fee-payer mismatch rejected) **and** end-to-end on devnet (W1→W1, sig confirmed `err=None`).
  - **No transport yet** — the next step is WalletConnect v2 (QR pairing + relay) calling these.

### Session 2026-07-12 (transaction simulation)

- **NEW** `src/solana/simulation.py`: anti-phishing transaction simulation. `analyze_transaction`
  runs `simulateTransaction` (unsigned-safe: `sigVerify=false`, `replaceRecentBlockhash=true`) +
  `getFeeForMessage`, and returns real per-account SOL/Token deltas, compute units, fee, predicted
  status/error, and warnings (spend outflow, token drain, recipient receives SOL, unverified
  programs, predicted failure). Verified end-to-end on devnet: a 0.001 SOL W1→W2 transfer correctly
  reports W1 `-0.001005` (incl. 5000-lamport fee) / W2 `+0.001`, compute=150, status=ok; an
  over-balance transfer correctly reports `InstructionError [0, {Custom: 1}]` + "Transaction will
  FAIL". This is the layer that should be called before any dApp `sign_transaction`/`sign_and_send`
  to show the user what they are really approving.

### Session 2026-07-12 (dApp-signing hardening after code review)

- **FIXED** `sign_message` oracle: now refuses to sign a payload that parses as a valid
  transaction message (strict bounds-checked detector `_looks_like_tx_message`), so it can't be
  abused as a tx-signing bypass. Ordinary text/SIWS plaintext still signs.
- **FIXED** SIWS safety: `SIWSPayload` rejects `\n`/`\r` in line-oriented fields (format-injection
  guard), and `sign_in_with_solana` refuses unless `payload.address == signer pubkey`.
- **FIXED** `preview_transaction` no longer crashes on V0+ALT txs (program-id decode now wrapped,
  ALT-safe), so `sign_transaction` is too.
- **FIXED** `sign_transaction`/`sign_and_send_transaction` refuse unknown programs by default
  (`allow_unknown_programs=False`), parity with `swap.swap`'s allowlist; the returned result now
  includes `unknown_programs`.
- **FIXED** `analyze_transaction` degrades to `status="simulation_failed"` instead of raising on a
  `simulateTransaction` error; `_rpc` now backs off on HTTP/JSON-RPC 429 and accepts a shared
  `httpx.AsyncClient`. Fee + simulation run as one pooled, parallel pair of calls.
- **NEW** `get_fee_for_message_bytes` (reuse already-decoded message), `validate_program_registries()`
  (asserts `swap.ALLOWED_PROGRAM_ID ⊆ KNOWN_PROGRAMS`).
- **REMOVED** dead `KNOWN_PROGRAMS` entry with a leading space.

### Session 2026-07-12 (WalletConnect v2)

- **NEW** `src/solana/wc2_crypto.py`: relay-interop crypto. X25519 + HKDF-SHA256
  (salt=zeros32, info=∅) → 32-byte symKey; `topic=sha256(hexbytes(symKey)).hex()`;
  ChaCha20-Poly1305 AEAD (12-byte iv, 16-byte tag); envelope =
  `base64pad([type]‖payload)` (0=direct `type‖iv(12)‖sealed`, 1=x25519
  `type‖senderPub(32)‖iv(12)‖sealed`, 2=plain). EdDSA relay-auth JWT with
  `did:key:z<base58(0xed01‖pub)>` issuer, seconds iat/exp. `parse_pairing_uri`.
- **NEW** `src/solana/wc2_relay.py`: `RelayClient` — async WebSocket to
  `wss://relay.walletconnect.com`, IRN JSON-RPC (`irn_subscribe/publish/
  subscription/unsubscribe`), JWT auth, request/response correlation, auto-reconnect
  + resubscribe. The relay requires a registered projectId (HTTP 403 `invalid key`
  otherwise).
- **NEW** `src/solana/walletconnect.py`: `WalletConnectClient` (responder). pair →
  `wc_sessionPropose`(1100) → `approve()` generates X25519 key, derives
  `sessionTopic=hashKey(deriveSymKey(myPriv,proposerPub))`, sends propose-response
  (1101, `responderPublicKey`) + `wc_sessionSettle`(1102). Routes
  `wc_sessionRequest`(1108) → `wallet_standard` + `simulation` → respond (1109).
  Maps `solana_signTransaction` / `solana_signAndSendTransaction` /
  `solana_signMessage` / `solana_signIn` (result key `signature` = base58; signTransaction
  also returns `signedTransaction`). CAIP-2 chains in `SOLANA_CHAINS`. Never holds
  wallet keys (signer_resolver). Tags verified against `@walletconnect/sign-client`
  2.23.10 (propose=1100/1101, settle=1102/1103, request=1108/1109, delete=1112/1113).
- **NEW** UI in `src/main.py`: "Connect dApp (WalletConnect v2)" button on the
  homepage → `wc-page`: projectId input (stored in `shared_preferences` under
  `wc.project_id`), `wc:` URI paste + Connect, active-sessions list with Disconnect.
  Identity seed persisted under `wc.identity_seed`. Incoming proposals show a
  dialog (dApp metadata + required chains/methods + account picker → Approve/Reject);
  incoming requests show the simulation preview (programs, fee, SOL/token deltas,
  warnings) → Approve & Sign / Reject.
- **VERIFIED** crypto self-tests (X25519 ECDH symmetry, envelope round-trips, did:key
  multicodec `K36`, EdDSA JWT), a live relay connect attempt (WS+JWT correct —
  relay returns 403 only for a placeholder projectId), and a full **mock-relay**
  integration test (`test_wc2_integration.py`: dApp proposer ↔ wallet responder,
  real envelopes, session topic derived identically by both sides, signMessage +
  signTransaction round-trips with verified signatures, disconnect).
- **TO USE LIVE**: register a free projectId at https://cloud.walletconnect.com,
  paste it on the `wc-page` → Save, then paste a dApp's `wc:` URI → Connect.
  Run `PYTHONPATH=src venv/bin/python tests/test_wc2_integration.py` for the offline
  protocol test.

### Session 2026-07-16 (USD portfolio + prices)

- **NEW** `src/solana/prices.py`: USD price feeds via **Jupiter Price API V3**
  (`https://api.jup.ag/price/v3`, free, no key). `get_prices(mints)` returns
  `{mint: {"usd", "change_24h"}}` (unknown/illiquid mints are simply omitted;
  never raises — returns partial/empty map). Batches mints in chunks of 50 to
  keep the URL short. `enrich_balance_result_with_prices(result)` attaches USD
  price/value to a `get_sol_spl_balance` result **mainnet entries only** (native
  SOL is priced via the wrapped-SOL mint `So111…12`; devnet/testnet holdings have
  no real value and are left unpriced). Adds `sol_price`/`sol_usd`/`sol_change_24h`
  + per-network `total_usd` and per-token `usd_price`/`usd_value`/`change_24h`;
  returns `{total_usd, priced, tokens, mainnet}`. `fmt_usd`/`fmt_change` helpers.
- **NEW** UI in `src/main.py` `get_balance_button_click`: after `get_sol_spl_balance`,
  calls `enrich_balance_result_with_prices` (wrapped in try/except so a price fetch
  failure never breaks balance display). Renders a green **"Portfolio value $X"**
  banner above the per-network results, and appends `$value (+/-x%)` (green/red) to
  each SOL row and SPL token row. Zero-amount tokens and unpriced mints show nothing.
- **FIXED** latent `flet.colors.X` → `flet.Colors.X` (capital C): flet 0.82.2 dropped
  the lowercase `colors` alias; the pre-existing `flet.colors.RED` in the balance
  error handler was a latent crash. All balance-screen color references now use
  `flet.Colors`. (`flet.border_radius`/`flet.padding`/`flet.margin` lowercase are
  still valid in this version.)
- **VERIFIED** headless: `get_prices` on real mainnet mints (SOL $76, USDC $1.00,
  MEW $0.000354, TNSR $0.03; PENGU/SEND/test mint correctly omitted), and
  `enrich_balance_result_with_prices` on a devnet+mainnet mix (devnet ignored,
  mainnet SOL+USDC priced, total computed, zero-amount token skipped).

### Session 2026-07-16 (Burn / Close token accounts)

- **NEW** burn/close core in `src/solana/spl_token.py`: `burn_instruction`
  (InstructionType BURN=8 → `[8]‖amount_u64`), `close_account_instruction`
  (CLOSE_ACCOUNT=9 → single byte, no args; needs 0 balance), `get_ata_raw_amount`
  (reads the exact on-chain base-unit amount straight from `ACCOUNT_LAYOUT` so a
  full burn has zero float drift), `burn_token` (partial), `close_token_account`
  (refund rent, fails if balance≠0), `burn_and_close_token_account` (burns the
  full fetched balance + closes the ATA in one tx — the "return rent" flow).
  Token Program vs Token-2022 resolved from the mint's owner via
  `get_token_program_id` (same path as the existing SPL transfer). Private
  `_sign_send_confirm`/`_resolve_program_id`/`_coerce_program_id` helpers.
- **NEW** `tests/` folder: moved `test_wc2_integration.py` here and added
  `test_burn_close.py` (offline instruction encoding + readonly devnet
  `get_ata_raw_amount` + full burn+close tx ASSEMBLY+SIGNING without submit +
  opt-in `RUN_DESTRUCTIVE=1` real burn+close). Run with
  `PYTHONPATH=src venv/bin/python tests/<file>.py`.
- **NEW** UI in `src/main.py` SPL token page (`go_to_spl_token_page_button_click`):
  "Burn" (partial, uses the amount field) and "Burn All & Close Account"
  (destructive, modal confirm → burns full on-chain balance + closes ATA → rent
  refund). Buttons carry explicit control refs (`amount_tf`/`secret_tf`/`status`)
  in `data` instead of positional index reads, so the new controls don't shift
  the existing transfer handler's indices. New `resolve_signing_key(data, secret_control)`
  helper centralizes private-key resolution (stored key, else seed/private-key
  from the secret field) — reused by both burn actions.
- **VERIFIED** headless: BURN/CLOSE instruction bytes (exact), ATA determinism,
  `get_ata_raw_amount` on W1's real Token-2022 holding (45 base units, 9 dp), and
  a fully assembled+signed burn+close tx (252-byte wire, not submitted).

### Session 2026-07-16 (Priority fee slider)

- **NEW** `src/solana/compute_budget.py`: the ComputeBudget program
  (`ComputeBudget111111111111111111111111111111`) instruction builders —
  `set_compute_unit_limit(units)` (discriminant 2 → `[2]‖u32 LE`) and
  `set_compute_unit_price(micro_lamports)` (discriminant 3 → `[3]‖u64 LE`),
  both account-less. `priority_fee_instructions(micro_lamports, cu_limit=None)`
  returns `[limit, price]` (or `[]` when the price is 0/None — i.e. the default
  "Auto"/no-priority-fee path is byte-identical to before). Priority fee charged
  = (CUs *consumed*) × µLamports / 1e6; the unit *limit* is only a scheduling cap.
- **NEW** `get_priority_fee_levels(mint|str, network)` in `balance.py`: percentile-based
  Low (p25) / Medium (p50) / High (p85) / max levels from
  `getRecentPrioritizationFees`, with safe non-zero fallbacks when the RPC fails.
  Refactored the shared fetch into `_fetch_recent_prioritization_fees`; the legacy
  `get_priority_fees` (max) is preserved for `calculate_total_transfer_cost`. Both
  now accept `str` (raw address) too.
- **CHANGED** `transfer_sol_token` (`transfer_sol.py`) and `transfer_spl_token` /
  `burn_token` / `close_token_account` / `burn_and_close_token_account` (`spl_token.py`)
  gained optional `priority_fee` (µLamports) + `cu_limit` params. When
  `priority_fee`>0 the ComputeBudget instructions are prepended to the tx (limit
  first, then price). Defaults leave existing behavior unchanged (no priority fee).
- **NEW** UI in `src/main.py`: `make_priority_fee_block(network, account_for_fees,
  cu_limit)` builds an Auto/Low/Medium/High/Custom selector (preset buttons +
  custom Slider + µLamports field + live SOL estimate) and returns a state object
  whose `get()` yields the chosen µLamports. Injected into both the SOL transfer
  page (cu_limit=2000, fees sampled on the sender) and the SPL transfer page
  (cu_limit=80000, fees sampled on the mint). `_pf_from_data(data)` reads the
  selection and is passed through `transfer_sol_token` / `transfer_spl_token` /
  the two burn actions. The selection is carried in each button's `data` dict
  (`pf_state`/`cu_limit`), not positional indices, so the existing field-index
  reads (amount/recipient/secret) are untouched.
- **VERIFIED** offline: `tests/test_priority_fee.py` (exact CB instruction bytes,
  auto=none / active=[limit,price] / no-limit=[price], SOL tx assembly). End-to-end
  on devnet: a W1→W2 0.001 SOL transfer with `priority_fee=5000, cu_limit=2000`
  landed `err=None`; on-chain fee = **5010 lamports** = 5000 base + 10 priority
  (2000×5000/1e6), with both ComputeBudget instructions present and correctly
  ordered before the System transfer.

### Session 2026-07-16 (NFT gallery)

- **NEW** `src/solana/nft.py`: NFT gallery data layer. `get_nfts(address, networks)`
  reuses `get_sol_spl_balance(..., include_transfer_cost=False, include_image_bytes=False)`
  for speed (no per-mint priority-fee RPC, no raw image downloads) and filters NFTs by
  `decimals==0 && amount>=1` (`is_nft_token` — same heuristic as Phantom/Solflare).
  `_build_nft` normalizes each holding into
  `{mint, network, amount, decimals, program_id, owner, name, symbol, collection, image,
  uri, description, attributes, external_url}`. `_normalize_image_url` rewrites
  `ipfs://`/`ipfs://ipfs/` → `https://ipfs.io/ipfs/` (Arweave/https pass through);
  `_normalize_attributes` flattens the Metaplex traits array (incl. nested-object values);
  `_collection_name` resolves a label from metadata JSON or on-chain symbol. Never raises
  — a failing network/token is skipped; hard balance-fetch failure returns `[]`.
- **CHANGED** `get_sol_spl_balance` (`balance.py`): gained optional `include_transfer_cost`
  (default `True`) and `include_image_bytes` (default `True`) flags. When `False`, the
  per-token `calculate_total_transfer_cost` priority-fee RPC call and the
  `get_spl_token_image` raw-byte download are skipped respectively. Existing callers
  (balance screen) are unchanged; the NFT gallery passes both as `False`.
- **NEW** UI in `src/main.py`: "NFT Gallery" button on the homepage → `nft-page`.
  `nft_enter()` builds a wallet `Dropdown` + mainnet/testnet/devnet checkboxes + "Load
  NFTs" button → `get_nfts` → `Row(wrap=True)` grid of clickable tiles (thumbnail +
  name + collection/network tag). `nft_detail_click` opens a modal dialog (image,
  collection, network+amount, copyable mint, Attributes traits, description) with a
  "Send NFT" action. Sending reuses the existing SPL transfer flow: `_open_spl_token_page`
  was extracted from `go_to_spl_token_page_button_click` (so it can be called with a
  data dict directly), and the SPL page now honors `data['nft_prefill_amount']` to
  prefill the amount field ("1" for an NFT). Watch-only wallets get the usual one-time
  secret field. The existing transfer/burn/priority-fee machinery is reused unchanged.
- **VERIFIED** headless: `get_nfts` on W1 finds the real devnet NFT `SuperNFT7`
  (Token-2022, decimals 0, amount 1) with correct name/symbol/image-URL/attributes; the
  `is_nft_token`/`_build_nft`/IPFS/attribute helpers pass offline unit checks. UI smoke
  test (Playwright): gallery page renders, Load finds the NFT, detail dialog shows all
  metadata (name/collection/network/amount/mint/attributes), and "Send NFT" navigates to
  the SPL transfer page with amount prefilled to "1".

### Session 2026-07-17 (Liquid Staking)

- **NEW** `src/solana/liquid_staking.py`: curated, anti-phishing mainnet registry for
  `JitoSOL`, `mSOL`, `bSOL`, and `jupSOL`. `stake_sol` / `unstake_sol` wrap the existing
  Jupiter flow for `SOL -> LST` / `LST -> SOL`; `get_stake_quote` supplies expected and
  minimum output plus the SOL-per-LST rate; `get_lst_positions` aggregates the wallet's
  classic-SPL and Token-2022 token accounts, then prices recognized LSTs in USD. The
  module strictly requires mainnet-beta, exact base-unit amounts (no float rounding), and
  slippage in `1..500` bps.
- **NEW** UI in `src/main.py`: the homepage's **Liquid Staking** button opens
  `stake-page`, where a wallet and LST can be selected, a quote acquired, and stake or
  unstake executed. Positions can be refreshed and each position provides an amount field
  for unstaking. The quote is invalidated when the amount, LST, or slippage changes.
- **HARDENED** `swap.swap`: Jupiter can choose a different DEX route for each `/order`
  request. An order containing a program outside `ALLOWED_PROGRAM_IDS` is never signed;
  the wallet fetches up to three fresh orders looking for an allowlisted route, then
  refuses if none is safe. Jupiter's HTTP-200 application errors (such as insufficient
  funds with an empty transaction) now produce an actionable error instead of a signing
  failure.
- **NEW** `tests/test_liquid_staking.py`: offline coverage for the curated registry,
  exact amount and slippage guards, quote/stake/unstake adapters, LST positions, Jupiter
  application errors, and the safe-route retry behavior. Run with
  `PYTHONPATH=src venv/bin/python tests/test_liquid_staking.py`.
- **VERIFIED mainnet**: full `0.01 SOL -> LST -> SOL` round-trips completed for all four
  curated LSTs: JitoSOL, mSOL, bSOL, and jupSOL. Every stake, unstake, and empty-ATA
  close transaction reached `confirmed` with `err=None`. Final verification found no LST
  positions and no remaining ATA for any of the four mints. The four complete tests cost
  about `0.000169586 SOL` total after returning ATA rent.

### Session 2026-07-17 (CSV Transaction History Export)

- **NEW** `src/solana/history_csv.py`: `transaction_history_to_csv()` formats existing
  `transaction_history.get_transaction_history()` records as RFC-compatible CSV. It
  emits a SOL-only row when no SPL balance changed, or one row per SPL change otherwise,
  preserving network, UTC ISO-8601 block time, signature, transaction type/status,
  SOL delta, fee, token mint/symbol/delta, slot, version, and compute units.
- **NEW** UI in `src/main.py`: after history loads for one or more selected networks,
  **Save History as CSV** opens Flet `FilePicker.save_file()` with a timestamped
  `solana-history-YYYY-MM-DD_HH-MM-SS.csv` default name and a `.csv` extension filter.
  The data is saved as `utf-8-sig` so Microsoft Excel recognizes UTF-8 correctly.
  The button is omitted when no transaction history was loaded.
- **VERIFIED** offline: `PYTHONPATH=src venv/bin/python tests/test_history_csv.py`,
  Python syntax compilation, and `git diff --check` all pass.

### Session 2026-07-17 (Solana Name Service resolution)

- **NEW** `src/solana/sns.py`: safe read-only `.sol` resolver. It validates and
  normalizes top-level names, derives the name-record PDA using the canonical SNS
  program (`namesLPneVptA9Z5rqUDD9tMTWEJwofgaYwp8cawRkX`) and `.sol` root-domain
  seed, obtains it with `getAccountInfo`, validates the account owner, and returns
  the configured wallet owner. Unregistered, malformed, non-SNS, and zero-owner
  records raise `SNSResolutionError`; SNS lookup is intentionally mainnet-only.
- **NEW** UI in `src/main.py`: SOL and SPL transfer recipient fields accept a
  base58 address or `name.sol`. The resolved address is displayed before transfer,
  passed through the existing address-poisoning gate, and then used as the actual
  recipient; the entered name remains visible in the field.
- **NEW** `tests/test_sns.py`: offline coverage for normalization, PDA derivation
  against the official SDK result, account decoding, wrong-program/truncated-data
  rejection, and zero-owner rejection. Run with
  `PYTHONPATH=src venv/bin/python tests/test_sns.py`.
- **VERIFIED**: `test_sns.py` (11 checks), `test_address_check.py` (35 checks),
  syntax compilation of `src/main.py` and `src/solana/sns.py`, and `git diff --check`.

### Session 2026-07-17 (Spam / scam token filter)

- **NEW** `src/solana/spam_filter.py`: balance-screen spam detection. Classifies each
  `get_sol_spl_balance` token record as `spam` / `suspicious` / `clean` via three layers:
  (1) curated registries — `KNOWN_GOOD_MINTS`/`KNOWN_GOOD_SYMBOLS` (verified canonical
  mints: SOL, USDC, USDT, JUP, BONK, WIF, JTO, JitoSOL — never flagged) and an empty
  `KNOWN_SPAM_MINTS` blacklist; (2) heuristics — symbol **impersonation** (name/symbol
  matches a known symbol but mint differs, incl. `"USDC <bait>"`-style names),
  **suspicious text** (URL fragments `.com`/`.io`/`http`, bait words `claim`/`airdrop`/
  `visit`/`free`), and **on-chain authority risk** (`mintAuthority` present on an unpriced
  unknown token = infinite-supply rug; `freezeAuthority` while impersonating = strong scam
  signal); (3) **liquidity signal** — a token carrying a real Jupiter `usd_price` (set by
  `prices.py`) downgrades an isolated open-mint-authority hit to clean. `get_mint_authorities`
  batch-fetches `{mintAuthority, freezeAuthority, supply}` for all mints of a network in one
  `getMultipleAccountsInfo` call (chunks of 100). `enrich_balance_result_with_spam_filter`
  mutates each token in place (`token['spam'] = {flag, severity, reasons}`) and runs AFTER
  pricing so the liquidity signal is available. Never raises / never blocks balance display.
- **NEW** UI in `src/main.py` (`get_balance_button_click`): runs spam enrichment right after
  price enrichment. Confirmed-**spam** tokens are hidden behind a per-network red
  "N spam token(s) hidden — click to show" toggle (rows live in a `visible=False` column,
  toggle carries the column ref in `data` so no per-loop closure state). **Suspicious**
  tokens stay visible but get an inline orange warning badge with the detection reasons.
  A red "Spam filter: N spam hidden / N suspicious" summary banner appears at the top when
  anything is flagged. The SPL token row builder was extracted into a local
  `_build_spl_token_controls(spl_token)` helper so spam/suspicious/normal all render
  identically; the existing transfer/burn/priority-fee handlers are untouched (they read
  named fields from `data`, and the extra `spam` key is harmless).
- **NEW** `tests/test_spam_filter.py` (31 checks): impersonation, suspicious-text,
  authority-risk heuristics; `classify_token` severity rules (known-good short-circuit,
  blacklist→spam, impersonation/url→spam, bait/open-mint→suspicious, priced→clean);
  `enrich_balance_result_with_spam_filter` summary; `is_hidden_spam`/`is_suspicious`;
  and graceful degradation (`get_mint_authorities` returns `{}` instead of raising on a
  dead endpoint). Run with `PYTHONPATH=src venv/bin/python tests/test_spam_filter.py`.
- **VERIFIED**: `test_spam_filter.py` (31 checks), `test_history_csv.py`, `test_sns.py`,
  syntax compilation of `src/main.py` and `src/solana/spam_filter.py`, a headless
  enrichment run on W1 devnet (real tokens: clean as expected, no crash), and a synthetic
  mixed spam/clean scenario (fake-USDC→hidden, `"claim at x.com"`→hidden,
  `"Free airdrop"`→suspicious, real USDC & priced token→clean).

### Session 2026-07-17 (UI reorganization — Phases 1–2)

Start of a **tiered-UI redesign** (Simple / Pro / Developer experience levels). Full plan
and per-phase progress live in `info/17-06-2026_UI.md` (design) and `info/17-07-2026_UI_progress.md`
(hands-off / what-remains). Phases 1–2 (cleanup + More hub) are **DONE and committed**
(`a1417c1`); Phases 3–7 remain. All changes are in `src/main.py` (UI only — `solana/` untouched).

- **REMOVED** the whole `NavigationDrawer` (junk "Item 1/2" placeholders, the `selected_drawer`
  handler, duplicate nav surface). Address Book + DevTools moved into the More hub.
- **REMOVED** the destructive navbar "Exit" item (`page.window.destroy()` — was killing the
  window). Navbar is now `Home | New | Recover | Add | More`; "Menu"→"Home"; fixed the
  duplicated `CODE` icon for New/Add (New=`ADD`, Add=`LINK`); added `selected_icon` variants.
- **NEW** `more-page` route (More hub): groups features into `WEB3 & DeFi` (Connect dApp /
  NFT Gallery / Liquid Staking) / `Tools` (Address Book / Settings) / `Developer` (Storage
  inspector `dev` badge / Clear all storage `danger` badge). Built by the `_hub_item(icon,
  title, subtitle, on_click, badge="")` helper (Card with ink tap, icon+title+subtitle+chevron).
- **NEW** `settings-page` route: hosts the `theme_control` switch (moved out of the drawer) +
  an About block. A "Experience level (coming soon)" note reserves the spot for the Phase-3
  Simple/Pro/Developer selector.
- **NEW** homepage is cleaner: the three feature buttons (`connect_dapp_button` /
  `nft_gallery_button` / `liquid_staking_button`) were removed from home and relocated to the
  hub; AppBar relabeled `HomePage`→`Solana Wallet` and gained a `More` action icon.
- **SAFETY** `clear_client_storage()` (wipes ALL wallets + PIN + contacts + WC pairing) was
  previously **silent/irreversible** (drawer item called it directly). Now wrapped in
  `clear_storage_click` → a confirmation `AlertDialog` ("Clear everything" button, red) →
  `_do_clear_storage`. The async confirm runs via `asyncio.create_task(_do_clear_storage(dlg))`
  in a sync lambda.
- **FIXED** latent bug: `theme_control` label now reflects the actual loaded theme at startup
  (was always hardcoded "Light theme").
- Key symbols/lines in current `src/main.py`: `nav_addressbook`/`nav_dev_storage`/`clear_storage_click`
  /`_do_clear_storage` (~3773), `selected_navbar`+`navbar` (~4181), `nav_wc`/`nav_nft`/`nav_stake`
  /`nav_more`/`nav_settings` + `_hub_item` (~4148), `route_change` more/settings branches (~4222),
  homepage (~4282), `more_page` (~4555), `settings_page` (~4595).
- **INVARIANT preserved**: `route_change` does `homepage.controls[-1] = await get_wallets_cards()`
  — after removing the 3 button rows, `get_wallets_cards()` is still the last homepage control.
- **VERIFIED** end-to-end via Playwright (web mode, PIN `1234`): clean homepage, More hub renders
  3 sections + badges, Settings theme toggle flips Light↔Dark with synced label, Clear-storage
  confirm dialog works (Cancel safe), Address Book reachable via hub (was drawer-only). 0 console
  errors. Code review (security / business-logic / dead-code tracks) = APPROVE, no findings.

### Session 2026-07-17 (UI reorganization — Phase 3: Experience registry)

Tiered-UI redesign Phase 3 of the plan in `info/17-06-2026_UI.md` / `info/17-07-2026_UI_progress.md`.
Introduces a persisted **Simple / Pro / Developer** mode that gates feature visibility.
UI + a small new `src/ui/` package; the `solana/` business layer is untouched. Committed (`b3b709e`).

- **NEW** `src/ui/__init__.py` + `src/ui/experience.py`: pure-logic experience registry. The
  feature matrix (`_MATRIX`) is the single source of truth for which features each mode may see:
  `spl_tokens/nft/swap/staking/burn_close/walletconnect` → {Pro, Dev};
  `devtools/raw_export/custom_rpc/csv_export/sim_detail` → {Dev}; any unlisted feature
  (send/receive/history/addressbook/settings/wallet create/recover/add, theme) → all modes.
  `feature(name, mode)` **fails open to all modes on an unknown feature key** (so a typo can never
  hide e.g. Send). `normalize`/`label`/`description`, async `get_experience`/`set_experience`
  (shared_preferences key `ui.experience`, default `"simple"`, never raise), and
  `has_seen_dev_warning`/`mark_dev_warning_seen` (key `ui.dev_warning_seen`). Top-level package
  `ui` imports fine because `src/` is on the path (same as `solana`/`construct`).
- **NEW** Settings selector (`src/main.py`): the "Experience level (coming soon)" note in
  `settings_page` is replaced with a `Dropdown` (Simple/Pro/Developer) + a dynamic description
  `Text`. Controls `experience_dd`/`experience_desc` are defined next to `theme_control`;
  `on_select` → `experience_changed` → `_apply_experience` (persist + update description +
  `page.update()`). `settings_enter()` reads the persisted mode into the selector and is called by
  `route_change` for `settings-page` (mirrors `wc_enter_page`/`nft_enter`).
- **NEW** Developer warning: first switch **into** Developer (prev≠Developer, not seen) opens a
  modal `AlertDialog` ("Enable Developer mode?" + destructive-tool text) — same pattern as
  `clear_storage_click`. **Cancel** reverts the dropdown to the previously persisted mode;
  **Enable Developer** marks `dev_warning_seen` + applies. Repeat switches skip the warning.
- **GOTCHA** (flet 0.82.2): `Dropdown` uses **`on_select`**, not `on_change`
  (`inspect.signature` confirms). Also a `Dropdown` merged inside a `Card` with a sibling `Text`
  becomes one a11y button — in Playwright, to open the overlay dispatch a real
  `PointerEvent('pointerdown'/'pointerup')` on `flt-glass-pane` at the dropdown's top-row coords,
  then click the option button in the snapshot.
- **INVARIANT preserved**: `homepage.controls[-1]` still the wallets list; `_apply_experience`
  doesn't touch homepage. `settings_enter` defined before `route_change(None)`.
- **VERIFIED** headless (matrix, get/set round-trip with a mock `page`, normalize, never-raises)
  **and** end-to-end via Playwright (PIN `1234`): selector renders & defaults to Simple; selecting
  Pro persists `ui.experience="pro"` + updates description; re-entering Settings reads it back;
  first Developer pick shows the warning (Cancel reverts, Enable persists + marks seen); second
  Developer pick skips the warning. 0 console errors. `git diff --check` clean.

### Session 2026-07-17 (UI reorganization — Phases 4–5 + review fixes)

Tiered-UI redesign Phases 4–5 of the plan in `info/17-06-2026_UI.md` /
`info/17-07-2026_UI_progress.md`. UI-only (`src/main.py` + `src/ui/experience.py`);
the `solana/` business layer is untouched. Phases 4–5 are DONE in the working tree
(not yet committed); the Phase-5 review findings are fixed and ready to commit.

- **Phase 4 (hub filtering)**: `more_page` → async `more_enter()` builder reads the
  experience mode and only appends gated `_hub_item(...)` calls. Mapping: Connect dApp
  → `walletconnect`, NFT Gallery → `nft`, Liquid Staking → `staking`, Storage inspector
  + Clear all storage → `devtools`; Address Book + Settings always visible. Empty
  sections are omitted entirely (Simple mode shows only Tools). `route_change` calls
  `await more_enter()` for `more-page`.
- **Phase 5 (progressive disclosure)**: same screens, different detail per mode. 5 new
  feature keys in `experience.py` `_MATRIX`: `priority_fee` (Pro/Dev),
  `priority_fee_custom` (Dev), `history_detail` (Pro/Dev), `history_tech` (Dev),
  `balance_raw` (Dev); reused `spl_tokens`/`csv_export`/`sim_detail`. Per screen:
  - Balance (`get_balance_button_click`): Simple = SOL + USD only (SPL + spam filter
    hidden; spam enrichment skipped); Pro/Dev = + SPL + spam filter; Dev = + raw token
    dump + "Inspect on Solscan".
  - Priority fee (`make_priority_fee_block`): Simple = hidden → `priority_fee=0` (Auto,
    byte-identical to before); Pro = Auto/Low/Med/High presets; Dev = + Custom slider +
    µLamports field + percentile readout.
  - History (`get_history_button_click`): Simple = header + inline status only; Pro =
    + expandable Signature/"Status | Fee"; Dev = + Slot/Version/CU + logs + CSV button.
  - Token detail (`_build_spl_token_detail`, extracted): Pro = friendly summary; Dev =
    raw key/value dump + Solscan link (arrow button `data` gained `'network'`).
  - WC request (`on_wc_request` / `_wc_render_preview(..., show_program_ids=)`): Pro =
    unknown programs shown as a count; Dev = + full sim logs + raw session/request JSON.
- **Phase 5 review fixes** (from `/review uncommitted`, all in `src/main.py`):
  - **FIXED (CRITICAL)** `on_wc_request` Dev raw-JSON dump leaked the live WC2 relay
    `symkey` (the ChaCha20-Poly1305 session key — would let anyone decrypt/forge relay
    messages; the session topic is public). Now serializes a scrubbed copy
    (`{k: v for k, v in session.items() if k != "symkey"}`) before `json.dumps`; the
    other session fields (peer, accounts, public X25519 keys) are dApp-known and kept.
  - **FIXED (WARNING)** Simple-mode "Portfolio value" banner over-counted: it summed
    SPL `usd_value` the user can't see (SPL rows are hidden in Simple). Banner now uses
    a SOL-only subtotal (`sum(nr.get('sol_usd') or 0.0 ...)`) in Simple; the
    "(no priced tokens)" note is suppressed in Simple. Pro/Dev unchanged.
  - **FIXED (SUGGESTION)** Simple mode wasted the slow per-token SPL fetch: now reads
    the mode first and passes `include_transfer_cost=show_spl, include_image_bytes=show_spl`
    to `get_sol_spl_balance` (the NFT-gallery fast path). SOL USD pricing still works via
    the wrapped-SOL mint.
  - **FIXED (SUGGESTION)** redundant `get_experience(page)` await in the Dev block of
    `on_wc_request` → reuses the already-fetched `_wc_mode`.
- **INVARIANT preserved**: `homepage.controls[-1]` still the wallets list; hidden
  priority-fee → `priority_fee=0` (Auto), identical wire bytes to before; existing
  `data`-dict contracts (amount/recipient/secret/pf_state) untouched.
- **VERIFIED**: syntax (`py_compile` on `src/main.py` + `src/ui/experience.py`),
  `git diff --check` clean, and a headless check of the SOL-only banner subtotal
  (SOL-only 73.68 vs full SOL+SPL 173.66 on a synthetic mainnet+devnet result; devnet
  correctly excluded). The Phase-5 Playwright 3-mode smoke (PIN `1234`, W1 watch-only
  on devnet) was run in the Phase-5 session; the mainnet-priced Simple banner is
  verified headlessly (devnet isn't priced). Phases 4–5 committed (`5ca3c5a` + `0971359`).

### Session 2026-07-17 (UI reorganization — Phase 6: Developer layer)

Tiered-UI redesign Phase 6 of the plan in `info/17-06-2026_UI.md` /
`info/17-07-2026_UI_progress.md`. UI-only (`src/main.py`); the `solana/` business layer
is untouched — all three new pages reuse existing functions. Phase 6 is DONE in the
working tree (not yet committed).

- **Goal**: gather dev-only tools into the More hub's **Developer** section. Adds three
  new pages, each gated by an already-present matrix key (no new keys): Simulation
  inspector (`sim_detail`), Raw RPC inspector (`custom_rpc`), Export raw keys
  (`raw_export`). CSV export (`csv_export`) stays on the history page (Phase 5).
- **Imports** (`src/main.py`): `from solana.simulation import analyze_transaction` +
  `import httpx` (first direct httpx use in main.py; already a project dependency).
- **NEW Simulation inspector** (`sim-page`, gated `sim_detail`): paste base64 tx + network
  dropdown (mainnet/testnet/devnet) + optional signer pubkey → **Analyze** runs
  `analyze_transaction` (read-only, unsigned-safe: `sigVerify=false`,
  `replaceRecentBlockhash=true`) and renders status / fee / message version / fee payer /
  account count / compute units / programs / ⚠ unverified programs / per-account SOL Δ /
  token Δ / ⚠ warnings / coloured simulation logs / **Copy raw JSON**. try/except →
  "analyze failed: …". Read-only — never signs/broadcasts.
- **NEW Raw RPC inspector** (`rpc-page`, gated `custom_rpc`): endpoint source dropdown
  (mainnet/testnet/devnet presets + **custom RPC URL** field) + commitment selector
  (processed/confirmed/finalized) + method selector (`getBalance` /
  `getAccountInfo` / `getTransaction` / `getSignaturesForAddress` /
  `getLatestBlockhash`) + input field → **Run** posts a direct JSON-RPC request via
  `httpx.AsyncClient` and renders pretty-printed response (selectable + Copy). Read-only
  methods only; errors degrade to a red "RPC failed: …" row.
- **NEW Export raw keys** (`raw-key-page`, gated `raw_export`): red **DANGER** banner +
  one Card per wallet with **Reveal**/**Copy** for `private_key_hex` /
  `secret_key_base58` / `words` / `public_key_hex`. Decrypts on demand via
  `decrypt_for_display` (needs the app unlocked — same PIN gate as Wallet Info);
  watch-only wallets only expose the public key (the 3 secret rows show
  "(watch-only wallet — no private key)"). `rawkey_enter()` (async, mirrors `nft_enter`)
  rebuilds the wallet list each visit.
- **Hub wiring**: `more_enter()` Developer section refactored from a single
  `if feature("devtools")` block into a `dev_items = []` list assembled from per-tool
  `feature(...)` checks (`devtools`/`sim_detail`/`custom_rpc`/`raw_export`/`devtools`);
  the whole section (header + divider + items) is omitted when `dev_items` is empty. New
  nav handlers `nav_sim`/`nav_rpc`/`nav_rawkey`; new `route_change` branches for
  `sim-page`/`rpc-page`/`raw-key-page`.
- **INVARIANT preserved**: `homepage.controls[-1]` still the wallets list; new pages
  defined before `route_change(None)`; no `solana/` change; hub `dev_items` refactor is
  additive (existing items/data contracts untouched).
- **Key symbols** (`src/main.py`): `el_rawkey_page`; `sim_analyze_click`/`sim_page`/
  `sim_net_dd`/`sim_tx_ta`; `rpc_run_click`/`rpc_page`/`rpc_source_dd`/`_rpc_endpoint()`;
  `rawkey_reveal_click`/`rawkey_copy_click`/`rawkey_enter`/`raw_key_page`;
  `nav_sim`/`nav_rpc`/`nav_rawkey`; `more_enter` Developer `dev_items` list.
- **VERIFIED**: syntax (`py_compile` on `src/main.py` + `src/ui/experience.py`),
  `git diff --check` clean. Headless: feature gating (`devtools`/`sim_detail`/
  `custom_rpc`/`raw_export`/`csv_export` all True only in Developer); `analyze_transaction`
  end-to-end on a real signed devnet tx (status `ok`, System Program + Compute Budget,
  W1 −0.001005 / W2 +0.001, CU 450); raw `getBalance` on W1 devnet (4.04 SOL). End-to-end
  via Playwright (Developer mode, PIN `1234`, W1 watch-only): More hub shows all 5 dev
  tools; RPC inspector → structured output (no error); sim inspector → 10 rows + logs +
  Copy raw JSON (empty-input error path also OK); raw-key page → watch-only exposes only
  the public key; Simple mode hides the whole Developer section. 0 console errors.
- **PLAYWRIGHT NOTES** (added to `info/ui-testing-playbook.md`): (1) the Flet `Dropdown`
  overlay **is** openable via paired `pointerdown`/`pointerup` on `flt-glass-pane` with
  an explicit `pointerId: 1` at the trigger centre (coords from a `boxes:true` snapshot)
  — §6.1 has the copy-paste recipe; the earlier "not automatable" claim was incomplete.
  (2) CanvasKit draws `Text` on the canvas — `browser_find`/DOM text walks return nothing
  for output text, so assert on the rendered control set or re-run the logic headlessly
  (§12).

### Session 2026-07-17 (UI reorganization — Phase 7: `main.py` → `ui/` package)

Tiered-UI redesign Phase 7 — the **refactor** phase (highest risk). `main.py` was
~5390 lines / ~150 nested closures inside one `async def main(page)`, all implicitly
capturing `page`, the in-memory `session` dict (holds the PIN-derived Fernet key),
PIN constants and a few shared flet controls. Phase 7 extracts those closures into
`src/ui/` one group at a time. Plan + detailed per-group handoff live in
`info/17-06-2026_UI.md` (design) and `info/17-07-2026_UI_progress.md` (progress).
**`info/` is gitignored** — those notes survive across sessions on this machine but
are NOT in git; **this AGENTS.md entry is the durable migration reference**.

Strategy chosen (with the user): **foundation + incremental pilot**, passing an
**`AppContext` object** as the first arg to every extracted function (highest-risk
phase → small, independently-committable steps; never a big-bang). The pilot landed
in the working tree, passed `/review uncommitted` (APPROVE WITH SUGGESTIONS — one
unused import, fixed), **NOT yet committed**.

**Migration contract (applies to every future group):**
- Every extracted function takes `ctx: AppContext` as its first arg (replaces
  implicit closure capture of `page` / `session`).
- `ctx.page` / `ctx.session` are the **same live objects** `main()` created —
  `AppContext` wraps by reference, never copies, so legacy closures and extracted
  modules share one source of truth during the incremental migration.
- Use `ctx.safe_update()` instead of the inline `try: page.update() except: pass`.
- Pure helpers (no `page`/`session` need) take explicit args and need no `ctx`.
- A group is "done" when its closures are deleted from `main.py` and its call sites
  pass `ctx`. Keep `main.py` as bootstrap + route orchestrator.
- `solana/` business layer is never touched.

**What shipped (working tree):**
- **NEW** `src/ui/context.py` — `AppContext` dataclass: `page`, `session`,
  `pin_salt_key`/`pin_verifier_key`/`auto_lock_seconds`, `controls` registry, plus
  accessors `is_unlocked()` / `reset_activity()` / `safe_update()`.
- **NEW** `src/ui/components/__init__.py` + `src/ui/components/priority_fee.py` —
  the **pilot**: `make_priority_fee_block(ctx, network, account_for_fees, cu_limit)`
  + pure `pf_from_data(data)`. Lifted verbatim from `main.py` (`page`→`ctx.page`,
  `try/except page.update()`→`ctx.safe_update()`).
- **CHANGED** `src/main.py`: imports `AppContext` + the module; removed the now-unused
  `get_priority_fee_levels` import; builds `ctx = AppContext(page=page, session=session,
  ...)` right after `session`; deleted both closures; updated the 2
  `make_priority_fee_block(...)` call sites to pass `ctx` (the 4 `_pf_from_data(data)`
  sites use an alias import — unchanged). Net: 5406 → 5269 lines.

**INVARIANTS preserved:** `homepage.controls[-1]` still the wallets list; Simple-mode
hidden priority fee still yields `priority_fee=0` (Auto, byte-identical wire bytes);
existing `data`-dict contracts (`pf_state`/`cu_limit`/amount/recipient/secret) untouched.

**Key symbols** (`src/main.py` — re-grep, line numbers drift): `ctx = AppContext(...)`
(~L141, right after `session`); the one-line migration marker (~L246); the import
block `from ui.context import AppContext` / `from ui.components.priority_fee import ...`
(~L69).

**VERIFIED:** `py_compile` on all 3 files; headless runtime build of
`make_priority_fee_block(ctx,…)` in all 3 modes (Simple→hidden/0, Pro→4 presets,
Dev→+Custom; `ctx.safe_update()` routes to `page.update()` and swallows its errors);
Playwright (PIN `1234`): boots clean, 0 console errors, unlocks, homepage renders.
`git diff --check` clean.

**flet headless-testing gotchas** (for future groups; full notes in
`info/ui-testing-playbook.md` §13): (1) `flet.Page.update` is NOT a coroutine
(`inspect.iscoroutinefunction` → False) — calling it un-awaited is correct (matches
legacy). (2) `ElevatedButton("text")` stores the label in `.content`, **not** `.text`,
in flet 0.82.2. (3) flet registers event handlers in an internal registry, so
`control.on_click` reads as `None` outside a live session — **headless tests can only
assert control *construction*, not click wiring**; verify clicks via Playwright or by
re-running the underlying `solana/` function. (4) Reusable headless recipe: mock
`page` with async `shared_preferences` + sync `update()`, monkeypatch the slow RPC,
assert on the built control structure.

**Next groups** (suggested order, each = one commit, lowest coupling first):
1. ~~Address book (`ab_*`, poisoning gate, contact picker)~~ — **DONE** (see "Phase 7 —
   Group 1: Address book" below). 2. ~~Dev tools (sim/rpc/rawkey pages)~~ — **DONE** (see
   "Phase 7 — Group 2: Dev tools" below, committed `651581a`). 3. ~~NFT gallery + Liquid
   staking enter pages~~ — **DONE** (see "Phase 7 — Group 3" below, committed `8e2e309`).
   4. WalletConnect (`_wc_*`/`on_wc_*`) — **← NEXT**. 5. Transfer screens (SOL/SPL,
   burn/close). 6. Wallet cards + views (`homepage`/`more_page`/`settings_page`/
   `route_change`) — the orchestrators, done last.

#### Phase 7 — Group 3: NFT gallery + Liquid staking (`enter`-style rebuilders)
UI-only extraction (committed `8e2e309`). Lifted the NFT gallery
(`_nft_network_tag`/`_nft_tile`/`nft_detail_click`/`nft_enter`, ~230 ln) and
Liquid staking (`lst_enter` + positions/quote/stake/unstake, ~205 ln) out of
`main()` into `src/ui/components/nft.py` + `src/ui/components/staking.py`.
`main.py` 4539→4104. Plus two shared-helpers improvements: wallet-key accessors
on `AppContext` and a canonical wallet-record loader in `ui/wallets.py`.
- **NEW** `src/ui/wallets.py` — `load_wallets(ctx)`: JSON-decode each `wallet.<key>`
  from `ctx.page.shared_preferences`, keep dict records with an `address_base58`,
  never raises (→ `[]`). Replaces devtools' `_load_wallets` + the would-be
  duplicates in nft/staking (DRY). `addressbook._gather_known_addresses` keeps its
  own read (different `{address,label}` output shape).
- **NEW** `src/ui/components/nft.py` — `_network_tag` (was `_nft_network_tag`),
  `_nft_tile(nft, wallet, on_click)` (pure builder), `nft_enter(ctx, open_spl_page)`.
  Wallets via `load_wallets`. The detail dialog's **"Send NFT"** action builds the
  same `spl_data` dict `main.py` built inline and calls `await open_spl_page(...)`,
  where `open_spl_page` is **`main.py`'s `_open_spl_token_page` closure injected as
  an explicit arg** (the SPL page migrates with the transfer group; this module
  never imports from `main.py`). `_nft_tile`'s `on_click` is the nested
  `_detail_click` closure that captures `open_spl_page`.
- **NEW** `src/ui/components/staking.py` — `lst_enter(ctx)`. Self-contained (quote /
  stake / unstake / refresh-positions render inline into `ctx.controls
  ["el_lst_page"]`; no outbound navigation). Stake/unstake sign with the wallet key
  via **`ctx.get_wallet_private_key`/`ctx.has_wallet_private_key`**. Local `_MAINNET`
  constant. Imports LST funcs + `LST_TOKENS`/`MAX_SLIPPAGE_BPS` from
  `solana.liquid_staking`, `fmt_usd`/`is_valid_amount`.
- **`AppContext`** (`ui/context.py`) gained `get_wallet_private_key(wallet)` /
  `has_wallet_private_key(wallet)` methods (mirror of the legacy `main()` closures:
  `""` while locked, else `get_secret(wallet, "private_key_hex", session["key"])`).
  Added `from solana.security import get_secret` at module top.
- **`devtools.py`** refactored: deleted its local `_load_wallets`, imports
  `load_wallets` from `ui.wallets` (DRY — the read was identical).
- **`main.py`**: imports `nft_enter`/`lst_enter`; **removed** now-unused
  `from solana.nft import get_nfts`, the whole `from solana.liquid_staking import
  (...)` block, and `InvalidOperation` from the decimal import (`Decimal`/
  `ROUND_HALF_UP` stay — transfer amount calc; `fmt_usd`/`fmt_change` stay —
  balance). Registers `ctx.controls["el_nft_page"]`/`["el_lst_page"]` right after
  the two `flet.Column()` holders (the views still bind those objects). Deleted the
  440-line NFT+LST block → one migration-marker comment. `route_change`:
  `nft-page`→`await nft_enter(ctx, _open_spl_token_page)`;
  `stake-page`→`await lst_enter(ctx)`.
- **Kept in main.py** (later groups): `_open_spl_token_page` (transfer group — now
  injected into nft_enter, still called by `go_to_spl_token_page_button_click`),
  `MAINNET_RPC` (swap), the legacy `get_wallet_private_key`/`has_wallet_private_key`
  closures (used by ~15 main.py sites in groups 4/5/6), `nft_page`/`stake_page`
  `flet.View` defs, `more_enter`'s hub items.
- **Migration-contract additions (apply to every future group)**:
  - **Wallet-key resolution → `ctx.get_wallet_private_key`/
    `ctx.has_wallet_private_key`.** State-derived signer-key access (unlock + Fernet)
    is now an `AppContext` method. Extracted modules use `ctx.*`; the legacy closures
    stay in `main.py` until their call sites migrate.
  - **Wallet-record load → `ui.wallets.load_wallets(ctx)`.** The canonical loader;
    don't re-inline the `wallet.` read.
  - **Outbound navigation to a not-yet-migrated page → inject as a callback arg.**
    When an extracted module needs to navigate to a page still in `main.py`, pass
    the closure as an explicit argument (`nft_enter(ctx, open_spl_page)`) instead of
    importing from `main.py`. The data-dict passed to the callback must be
    byte-identical to what `main.py` built inline.
- **INVARIANTS preserved**: `homepage.controls[-1]` still the wallets list;
  `nft_page`/`stake_page` bind the same `el_*_page` objects; the `spl_data` dict
  the NFT "Send NFT" action passes is byte-identical to before (SPL page
  field-index reads untouched); `solana/` untouched; views built once at bootstrap
  → `route_change` branches only gain `ctx` (+ callback); no per-session mutable
  state in this group → no `ctx.session` migration.
- **Key symbols** (re-grep): `src/ui/wallets.py:load_wallets`;
  `src/ui/components/nft.py:_network_tag`/`_nft_tile`/`nft_enter`;
  `src/ui/components/staking.py:lst_enter`; `src/ui/context.py:
  get_wallet_private_key`/`has_wallet_private_key`; `src/main.py`: import `nft_enter`/
  `lst_enter` (~L84), `ctx.controls["el_nft_page"]`/`["el_lst_page"]` (~L557),
  migration marker (~L2088), route_change nft/stake branches (~L3579/L3585).
- **VERIFIED**: `py_compile` all 7 files; `git diff --check` clean; existing offline
  suites green (`test_address_check`/`test_sns`/`test_history_csv`/`test_spam_filter`).
  Headless (28 checks): `load_wallets` (2 wallets + junk-filter); `ctx` wallet-key
  accessors (unlocked True/False, locked→`""`); `nft._network_tag`;
  `_nft_tile` (TextButton + data contract); `nft_enter` structure (7 controls,
  wallet Dropdown + Load NFTs button, empty-wallets branch); `lst_enter` structure
  (11 controls, LST Dropdown = 4 options + JitoSOL default, buttons, empty-wallets
  branch); signatures. End-to-end via Playwright (Pro mode via JSON-encoded
  localStorage + new tab; PIN `1234`; W1 watch-only): app **boots clean, 0 console
  errors**; NFT Gallery page renders; Liquid Staking page renders; clicking
  **Refresh Positions** runs the extracted handler end-to-end (real `lst_positions`
  mainnet RPC → "No liquid-staking positions yet for this wallet."), 0 errors.


#### Phase 7 — Group 2: Dev tools (`sim` / `rpc` / `raw-key` pages)
UI-only extraction (committed `651581a`). Lifted the three Developer-layer
pages (Phase 6) out of `main()` into `src/ui/components/devtools.py`. `main.py` 4926→4539.
- **NEW** `src/ui/components/devtools.py` — `build_sim_page(ctx)`/`build_rpc_page(ctx)`/
  `build_rawkey_page(ctx)` (each returns a `flet.View`), `rawkey_enter(ctx)` (async,
  rebuilds `ctx.controls["el_rawkey_page"]` — mirrors Group 1's `addressbook_enter`),
  plus helpers `_sim_row` (shared labelled row for both inspectors), `_ENDPOINTS`
  (local mainnet/testnet/devnet URLs — no dep on main.py's `MAINNET_RPC`), `_load_wallets`
  (reads `wallet.` records straight from `ctx.page.shared_preferences`, JSON-decode +
  filter — no dep on `get_storage_data`), `_decrypt` (replaces the `decrypt_for_display`
  closure: passthrough when locked, else `decrypt_wallet_secrets`). The 3 Views' AppBar
  back button + navigation bar read `ctx.controls["view_pop"]` / `["navbar"]`.
- **`main.py`**: imports the 4 functions; **removed** `from solana.simulation import
  analyze_transaction` + `import httpx` (both only ever lived in the dev block — now in
  the module); registers `ctx.controls["el_rawkey_page"]` / `["navbar"]` / `["view_pop"]`
  (right after each definition) so the extracted builders can wire view chrome; replaced
  the ~400-line dev block with `sim_page = build_sim_page(ctx)` / `rpc_page` /
  `raw_key_page` (built once at bootstrap — `route_change` is untouched); `await
  rawkey_enter(ctx)` in the `raw-key-page` branch.
- **Migration-contract note (applies to every future view-builder group)**: shared view
  chrome now lives in `ctx.controls` — `view_pop` (the back-nav handler) and `navbar`
  (the shared NavigationBar) are registered there during bootstrap, so extracted view
  builders wire them via `ctx.controls["view_pop"]` / `["navbar"]` without an explicit
  arg. The three dev views are built once at bootstrap at the same code location as
  before, so `route_change`'s `sim-page`/`rpc-page`/`raw-key-page` branches stay
  byte-unchanged.
- **Kept in main.py** (later groups): `nav_sim`/`nav_rpc`/`nav_rawkey` (nav-handler
  group), `more_enter`'s `dev_items` + `_hub_item` (orchestrator group), `MAINNET_RPC`
  (still used by swap/staking), `decrypt_for_display` (still used by Wallet Info dialog).
- **INVARIANTS preserved**: `homepage.controls[-1]` still the wallets list; the 3 dev
  Views built once at the same location; `raw_key_page` binds the same `el_rawkey_page`
  that `rawkey_enter(ctx)` rebuilds; `solana/` untouched; existing `data`-dict contracts
  untouched; no per-session state in this group (sim/rpc outputs are transient, rawkey
  reveal is stateless) → no `ctx.session` migration needed.
- **VERIFIED**: `py_compile` all 3 files; `git diff --check` clean; headless (19+ checks:
  helpers, `_load_wallets` junk-filtering, `_decrypt` real encrypt/decrypt round-trip,
  all 3 builders produce correct routes/controls/navbar wiring, `rawkey_enter` danger
  banner + wallet cards + watch-only rows); Playwright (PIN `1234`, fresh instance): app
  **boots clean, 0 console errors**, PIN setup + unlock + homepage + More hub (Simple =
  Tools-only, WEB3/Developer absent) + Settings all render; **bootstrap logs prove
  `build_sim_page`/`build_rpc_page` ran during live startup** (the `devtools.py` `ElevatedButton`
  deprecation warnings only fire if the builders executed). The live *visual* render
  behind the Developer-mode wall wasn't captured — that `Dropdown` is a CanvasKit control
  Playwright can't drive reliably (pointer/mouse hits the merged description node, not the
  trigger; new-tab localStorage trips a Flet type-cast error) — playbook §6/§12 fragility,
  not a code defect; headless build is byte-identical in structure to the pre-extraction
  code.

#### Phase 7 — Group 1: Address book (`ab_*`, poisoning gate)
UI-only extraction (committed). Lifted the whole address-book + address-poisoning
block (~370 lines) out of `main()` into `src/ui/components/addressbook.py`.
- **NEW** `src/ui/formatting.py` — pure `short_addr(addr, head=6, tail=6)` (was an
  inline closure `_short_addr` in main.py; used by address book, NFT page, and
  wallet-card dropdowns — shared now).
- **NEW** `src/ui/components/addressbook.py` — `ab_load/save/add/delete`,
  `_gather_known_addresses`, `make_poisoning_banner`/`update_poisoning_banner`,
  `open_contact_picker`/`open_save_contact_dialog`, `maybe_block_for_poisoning`,
  `addressbook_enter`. All ctx-first where they need `page`/`session`.
  `_gather_known_addresses` reads the user's wallets directly from
  `ctx.page.shared_preferences` under the `"wallet."` prefix (so no dependency on
  main.py's `get_storage_data` closure). `make_poisoning_banner` is a pure builder
  (no ctx). `resolve_recipient_input` (SNS helper) was **kept in main.py** — it
  migrates with the transfer-screens group.
- **`AppContext`** gained `close_dialog(dlg)` (=`dlg.open=False` + `safe_update()`)
  — the old `_close_dlg` closure was shared by address-book dialogs AND the
  dev-warning / clear-storage dialogs; all 4 non-AB call sites now call
  `ctx.close_dialog(dlg)`.
- **`main.py`** (5269→4926): imports `make_poisoning_banner`/`update_poisoning_banner`/
  `open_contact_picker`/`open_save_contact_dialog`/`maybe_block_for_poisoning as
  _maybe_block_for_poisoning`/`addressbook_enter` + `short_addr as _short_addr`;
  registers `ctx.controls["el_address_book"]` on the live `el_address_book` Column
  (the `addressbook_page` view still binds that same object); updated all call sites
  (2 transfer pages + 4 dialog sites) to pass `ctx` / use `ctx.close_dialog`; removed
  the now-dead `check_address_poisoning` import.
- **Review fix (from `/review uncommitted`)**: the per-session poisoning-confirm
  allowlist MUST live in `ctx.session["_poisoning_confirmed"]` (a `set`), NOT at
  module level. Reason: `main()` is invoked **per connected client in web mode**, so
  the original closure `_poisoning_confirmed` was per-session; a module-level set is
  per-process and would bleed one client's confirmation across sessions. Verified
  headless: session A confirms → proceeds; fresh session B blocks again.
  **General rule for all future groups: any per-session mutable state that was a
  closure in `main()` goes to `ctx.session`, never module-level.**
- **INVARIANTS preserved**: `homepage.controls[-1]` still the wallets list;
  `addressbook_page` binds the same `el_address_book`; `solana/` untouched;
  existing `data`-dict contracts (pf_state/cu_limit/amount/recipient/secret) untouched.
- **VERIFIED**: `py_compile` all 4 files; headless (38 checks: short_addr, storage
  round-trip, ab_add validation, _gather_known_addresses, banner green/grey/hidden,
  gate clean/risky/already-confirmed, picker/save dialogs, addressbook_enter rebuild,
  close_dialog, + per-session isolation); Playwright (PIN `1234`): 0 console errors,
  Address Book renders + add/persist contact to `flutter.addressbook.contacts`;
  `tests/test_address_check.py` 35/35.

## Security reminders

- Private keys and mnemonics are stored **encrypted at rest** (Fernet) once a PIN is set;
  they are decrypted into memory only while the app is unlocked. Never log, print, or copy
  them unless explicitly required for a test.
- The PIN is never stored; only a salt + encrypted verifier token are. Losing the PIN makes
  encrypted secrets unrecoverable (the only option is "Forgot PIN?" → wipe all wallets).
- `devnet-wallets.txt` and `mainnet-wallets.txt` are gitignored. Keep all keys out of the
  repo and use a mainnet key only after the user explicitly authorizes a real transaction.
- Devnet wallets hold no real value; `mainnet-wallets.txt` contains real keys and must
  never be logged, printed, or copied into source files.
