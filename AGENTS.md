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

- `src/main.py` — UI, route handlers, all click callbacks
- `src/solana/` — blockchain logic:
  - `balance.py` — SOL/SPL balances, metadata parsing, transfer-cost estimate
  - `transfer_sol.py` — SOL transfer + `confirm_transaction` (confirmation polling)
  - `spl_token.py` — SPL/Token-2022 transfer, airdrop, ATA helpers
  - `create_wallet.py` — BIP39 + BIP32-ed25519 key derivation, recovery
  - `transaction_history.py` — tx history (parallel fetch)
  - `swap.py` — Jupiter Swap API V2 (quote + assembled V0 tx; **mainnet-only**)
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
- `src/assets/` — images
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

1. **Flet = Flutter web.** After `navigate`, the page only shows an "Enable accessibility"
   button. The button is outside the viewport (direct click times out). Enable semantics
   via JS once:
   ```js
   () => { const el = document.querySelector('flt-semantics-placeholder'); if (el) el.click(); }
   ```
   Then use `playwright_browser_snapshot` / click.
2. **Fill text fields with `playwright_browser_type`, not `fill`/`fill_form`.** Flutter
   textboxes ignore DOM `fill` for some fields (notably the address field). To replace an
   existing value: click the field → `Ctrl+A` → type.
3. The `NavigationDrawer` may open on first load → click **"Dismiss"**.
4. Form fields are **global objects** — values persist across page navigations. Clear/replace
   before reuse.
5. Network selection is via 3 checkboxes (mainnet-beta/testnet/devnet) on the address page.

## Test devnet wallets

Private keys live in `devnet-wallets.txt` (**gitignored**). Public addresses:

- **W1**: `AuPjPzHABDxeug5fidMcsNz6Aqwm3Amk9NcutqjDirWz` (SOL + Token-2022)
- **W2**: `EcjMVbJnNni4maBotAgtFnTqhkKkPrgGkoNtzL2MpBKr` (SOL + Token-2022)

Common Token-2022 mint they both hold: `Ejxf4ZKJnyCbgHdEAkWhaR7qjGvT7vpMYxiAeWyLG62b` (9 decimals).

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

## Security reminders

- Private keys and mnemonics are stored **encrypted at rest** (Fernet) once a PIN is set;
  they are decrypted into memory only while the app is unlocked. Never log, print, or copy
  them unless explicitly required for a test.
- The PIN is never stored; only a salt + encrypted verifier token are. Losing the PIN makes
  encrypted secrets unrecoverable (the only option is "Forgot PIN?" → wipe all wallets).
- `devnet-wallets.txt` is gitignored. Keep any real keys out of the repo.
- These wallets hold **devnet** funds only (no real value).
