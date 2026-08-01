"""Internationalization - language registry + translation lookup.

Pure UI concern. Persisted in Flet ``shared_preferences`` under ``ui.lang`` (same
pattern as ``ui.experience`` / ``theme_mode``). Never raises.

Design
------
* Keys are stable snake_case English identifiers, NOT the English text itself.
  Reason: changing a source string must not break existing translations.
* Missing key   -> returns the key itself (fail-loud: visible in UI during dev).
* Missing lang  -> falls back to English, then to the key.
* ``**fmt``     -> ``str.format`` interpolation, e.g. ``t("del_ok", lang, key=addr)``.
* Plurals       -> separate :func:`tp` helper (simple RU rule: ``n%10``).
* Debug ``print(...)`` logs are NEVER translated - they are developer output.

Architecture decision: own dict-translator, NOT gettext/Babel. Reason: the APK
build via serious_python (CPython 3.12.9, ``pypi.flet.dev`` index) already has
tight dependency constraints; gettext ``.mo`` bundling is an avoidable risk, and
this module already mirrors the established :mod:`ui.experience` pattern
(constants + async ``get``/``set`` pair). Pure Python, zero dependencies.

Languages: add a language by (1) appending its code to ``LANGS`` and (2) adding
its text to every key (a missing translation silently falls back to English).
"""
from __future__ import annotations

LANG_KEY = "ui.lang"

ENGLISH = "en"
RUSSIAN = "ru"
LANGS = (ENGLISH, RUSSIAN)          # add languages as they are translated (es/de/zh/ar...)
DEFAULT_LANG = ENGLISH
_ALL_LANGS = set(LANGS)

# ---- translations -----------------------------------------------------------
# key -> {lang: text}. Interpolation/pluralization via ``str.format``.
TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- common (used across many modules) ---
    "save": {"en": "Save", "ru": "Сохранить"},
    "cancel": {"en": "Cancel", "ru": "Отмена"},
    "ok": {"en": "OK", "ru": "OK"},
    "copy": {"en": "Copy", "ru": "Копировать"},
    "copied": {"en": "Copied!", "ru": "Скопировано!"},
    "delete": {"en": "Delete", "ru": "Удалить"},
    "close": {"en": "Close", "ru": "Закрыть"},
    "confirm": {"en": "Confirm", "ru": "Подтвердить"},
    "back": {"en": "Back", "ru": "Назад"},
    "error": {"en": "Error", "ru": "Ошибка"},
    "loading": {"en": "Loading…", "ru": "Загрузка…"},
    "retry": {"en": "Retry", "ru": "Повторить"},
    "more": {"en": "More", "ru": "Ещё"},
    "settings": {"en": "Settings", "ru": "Настройки"},
    # --- homepage (app.py) - migrated in Phase 2; keys defined now for parity ---
    "app_title": {"en": "Solana Wallet", "ru": "Кошелёк Solana"},
    "wallets_label": {"en": "Wallets:", "ru": "Кошельки:"},
    "new_wallet": {"en": "New Wallet", "ru": "Новый кошелёк"},
    "recover_wallet": {"en": "Recover Wallet", "ru": "Восстановить кошелёк"},
    "add_wallet_address": {"en": "Add Wallet Address", "ru": "Добавить адрес кошелька"},
    # --- settings (settings.py) ---
    "theme_light": {"en": "Light theme", "ru": "Светлая тема"},
    "theme_dark": {"en": "Dark theme", "ru": "Тёмная тема"},
    "appearance": {"en": "Appearance", "ru": "Оформление"},
    "about": {"en": "About", "ru": "О приложении"},
    "language": {"en": "Language", "ru": "Язык"},
    "experience_level": {"en": "Experience level", "ru": "Уровень интерфейса"},
    # --- devtools (migration of EXISTING Russian hardcodes - Phase 1) ---
    "edit_client_storage": {
        "en": "Edit client_storage:",
        "ru": "Редактирование client_storage:",
    },
    "del_ok": {
        "en": "{key} deleted successfully!",
        "ru": "{key} успешно удалён!",
    },
    "del_err": {
        "en": "An error occurred during deletion!",
        "ru": "Во время удаления произошла ошибка!",
    },
    # --- plural pairs (used by tp() in later phases; defined now so the plural
    #     rule is testable in Phase 1). RU: 1=sg, else plural form. ---
    "spam_hidden_sg": {"en": "{n} spam token hidden", "ru": "{n} спам-токен скрыт"},
    "spam_hidden_pl": {"en": "{n} spam tokens hidden", "ru": "{n} спам-токенов скрыто"},
    # ...keys added as modules are migrated (Phases 2-4)...
}

# ---- human-readable language names for the Settings dropdown -----------------
# lang -> {in_lang: native name}. Whichever language the UI is in, "Russian"
# is shown in Russian.
LANGUAGE_NAMES: dict[str, dict[str, str]] = {
    ENGLISH: {"en": "English", "ru": "Английский"},
    RUSSIAN: {"en": "Russian", "ru": "Русский"},
}


def normalize(lang) -> str:
    """Unknown/empty -> English (fail-safe)."""
    return lang if lang in _ALL_LANGS else DEFAULT_LANG


def available_languages() -> tuple[str, ...]:
    """Languages for which at least a base set of translations exists."""
    return LANGS


def language_display_name(lang: str, in_lang: str = DEFAULT_LANG) -> str:
    """Display name of ``lang`` rendered in ``in_lang`` (for the dropdown).

    ``lang`` is looked up as-is so an unknown code surfaces verbatim (e.g.
    ``"de"``); only the rendering language ``in_lang`` is normalized to a valid
    value so a stray value still produces English text.
    """
    in_lang = normalize(in_lang)
    return LANGUAGE_NAMES.get(lang, {}).get(in_lang) or lang


def t(msg_key: str, lang: str | None = None, **fmt) -> str:
    """Translate ``msg_key`` to ``lang`` (default English).

    The lookup key parameter is named ``msg_key`` (not ``key``) so the common
    placeholder name ``key`` stays available for interpolation — e.g.
    ``t("del_ok", lang, key=addr)`` (here ``key`` is the deleted storage key,
    a template ``{key}`` placeholder, not the translation key).

    Missing key   -> returns the key (fail-loud: visible during dev).
    Missing lang  -> falls back to English, then to the key.
    ``**fmt``     -> ``str.format`` interpolation.
    """
    lang = normalize(lang)
    entry = TRANSLATIONS.get(msg_key)
    if entry is None:
        return msg_key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or msg_key
    return text.format(**fmt) if fmt else text


def tp(key_plural: str, key_singular: str, n: int, lang: str | None = None, **fmt) -> str:
    """Plural-aware translate.

    Simple RU rule: ``n%10==1 and n%100!=11`` -> singular, otherwise plural.
    EN: always the plural form (as today). Add rules for other languages as
    they are introduced.
    Example: ``tp("spam_hidden_pl", "spam_hidden_sg", n, ctx.lang)``.

    ``n`` is injected into the format kwargs (overridden if the caller passes
    it explicitly), so templates can use a ``{n}`` placeholder.
    """
    lang = normalize(lang)
    use = dict(fmt)
    use.setdefault("n", n)
    if lang == RUSSIAN:
        if n % 10 == 1 and n % 100 != 11:
            return t(key_singular, lang, **use)
    return t(key_plural, lang, **use)


async def get_lang(page) -> str:
    """Read persisted language (default English). Never raises."""
    try:
        if await page.shared_preferences.contains_key(LANG_KEY):
            return normalize(await page.shared_preferences.get(LANG_KEY))
    except Exception:
        pass
    return DEFAULT_LANG


async def set_lang(page, lang) -> str:
    """Persist ``lang`` (normalized). Returns the normalized value."""
    lang = normalize(lang)
    try:
        await page.shared_preferences.set(LANG_KEY, lang)
    except Exception:
        pass
    return lang
