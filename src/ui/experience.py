"""Experience-level registry - Simple / Pro / Developer feature gating.

Pure UI concern. The feature matrix here is the single source of truth for which
features each experience level may see; the views (main.py) consult it via
``feature(name, mode)`` instead of hardcoding visibility.

Persisted in Flet ``shared_preferences`` under ``ui.experience`` (same pattern as
``theme_mode`` / ``wc.project_id``). Defaults to ``simple``.
"""
from __future__ import annotations

from ui.i18n import DEFAULT_LANG, t as _t

# ---- storage keys -----------------------------------------------------------
EXPERIENCE_KEY = "ui.experience"
DEV_WARNING_SEEN_KEY = "ui.dev_warning_seen"

# ---- modes ------------------------------------------------------------------
SIMPLE = "simple"
PRO = "pro"
DEVELOPER = "developer"

MODES = (SIMPLE, PRO, DEVELOPER)
DEFAULT_MODE = SIMPLE
_ALL_MODES = {SIMPLE, PRO, DEVELOPER}

# ---- feature matrix ---------------------------------------------------------
# feature name -> set of modes allowed to see/use it.
# Any feature NOT listed here is visible in ALL modes (receive, send, history,
# address book, settings, wallet create/recover/add, theme...).
_MATRIX: dict[str, set[str]] = {
    "spl_tokens": {PRO, DEVELOPER},
    "nft": {PRO, DEVELOPER},
    "swap": {PRO, DEVELOPER},
    "staking": {PRO, DEVELOPER},
    "burn_close": {PRO, DEVELOPER},
    "walletconnect": {PRO, DEVELOPER},
    "devtools": {DEVELOPER},
    "raw_export": {DEVELOPER},
    "custom_rpc": {DEVELOPER},
    "csv_export": {DEVELOPER},
    "sim_detail": {DEVELOPER},
    # Phase 5 — progressive disclosure inside existing screens.
    "priority_fee": {PRO, DEVELOPER},        # priority-fee selector on transfer pages
    "priority_fee_custom": {DEVELOPER},      # Custom slider + µLamports field + percentiles
    "history_detail": {PRO, DEVELOPER},      # expandable fee/signature details in history
    "history_tech": {DEVELOPER},             # slot/version/CU/logs rows in history details
    "balance_raw": {DEVELOPER},              # raw mint/program_id dump + explorer link on tokens
}

# human labels / descriptions used by the Settings selector. The text lives in
# :mod:`ui.i18n` (``exp_*`` / ``exp_*_desc`` keys) so it switches with the UI
# language; LABELS/DESCRIPTIONS stay as the canonical English fallback for any
# caller that needs the untranslated form (e.g. data dumps / logs).
LABELS = {
    SIMPLE: "Simple",
    PRO: "Pro",
    DEVELOPER: "Developer",
}

DESCRIPTIONS = {
    SIMPLE: "Send & receive SOL and view basic activity. Advanced WEB3 tools stay hidden.",
    PRO: "Everything in Simple, plus SPL tokens, NFTs, swaps, liquid staking and WalletConnect.",
    DEVELOPER: (
        "Everything in Pro, plus raw developer tools (storage inspector, CSV export, "
        "simulation details). Intended for power users."
    ),
}

# i18n translation keys backing label()/description() (their ``en`` values match
# LABELS/DESCRIPTIONS byte-for-byte, so a no-``lang`` call reproduces the legacy
# English text exactly).
_LABEL_KEYS = {SIMPLE: "exp_simple", PRO: "exp_pro", DEVELOPER: "exp_developer"}
_DESC_KEYS = {SIMPLE: "exp_simple_desc", PRO: "exp_pro_desc", DEVELOPER: "exp_developer_desc"}


def normalize(mode) -> str:
    """Return a valid mode string; unknown/empty -> Simple."""
    return mode if mode in MODES else DEFAULT_MODE


def label(mode, lang: str | None = None) -> str:
    """Localized mode label (``lang=None`` -> English, the legacy default)."""
    return _t(_LABEL_KEYS[normalize(mode)], lang or DEFAULT_LANG)


def description(mode, lang: str | None = None) -> str:
    """Localized mode description (``lang=None`` -> English, the legacy default)."""
    return _t(_DESC_KEYS[normalize(mode)], lang or DEFAULT_LANG)


def feature(name, mode) -> bool:
    """True if ``mode`` may access feature ``name``.

    Unknown feature names default to ALL modes (fail-open for core flows), so a
    typo in a feature key can never accidentally hide e.g. the Send button.
    """
    return normalize(mode) in _MATRIX.get(name, _ALL_MODES)


async def get_experience(page) -> str:
    """Read persisted mode (default Simple). Never raises."""
    try:
        if await page.shared_preferences.contains_key(EXPERIENCE_KEY):
            return normalize(await page.shared_preferences.get(EXPERIENCE_KEY))
    except Exception:
        pass
    return DEFAULT_MODE


async def set_experience(page, mode) -> str:
    """Persist ``mode`` (normalized). Returns the normalized value."""
    mode = normalize(mode)
    try:
        await page.shared_preferences.set(EXPERIENCE_KEY, mode)
    except Exception:
        pass
    return mode


async def has_seen_dev_warning(page) -> bool:
    try:
        return bool(await page.shared_preferences.contains_key(DEV_WARNING_SEEN_KEY))
    except Exception:
        return False


async def mark_dev_warning_seen(page) -> None:
    try:
        await page.shared_preferences.set(DEV_WARNING_SEEN_KEY, "1")
    except Exception:
        pass
