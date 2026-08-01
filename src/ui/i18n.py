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
    # --- homepage navbar (app.py) - Phase 2 ---
    "nav_home": {"en": "Home", "ru": "Главная"},
    "nav_new": {"en": "New", "ru": "Новый"},
    "nav_recover": {"en": "Recover", "ru": "Восстановить"},
    "nav_add": {"en": "Add", "ru": "Добавить"},
    # --- balance.py (wallet cards, address page, balance/history) - Phase 2 ---
    "wallet_name_label": {"en": "Wallet Name: ", "ru": "Имя кошелька: "},
    "wallet_desc_label": {"en": "Wallet Description: ", "ru": "Описание кошелька: "},
    "address_label": {"en": "Address: ", "ru": "Адрес: "},
    "created_label": {"en": "Created: ", "ru": "Создан: "},
    "watch_only_badge": {
        "en": "Watch-only (no private key)",
        "ru": "Только просмотр (без приватного ключа)",
    },
    "watch_only_tag": {"en": "  (watch-only)", "ru": "  (только просмотр)"},
    "show_more": {"en": "Show More", "ru": "Показать ещё"},
    "field_name": {"en": "Name", "ru": "Название"},
    "field_description": {"en": "Description", "ru": "Описание"},
    "wallet_info": {"en": "Wallet Info", "ru": "Информация о кошельке"},
    "receive_sol": {"en": "Receive SOL", "ru": "Получить SOL"},
    "delete_wallet": {"en": "Delete Wallet", "ru": "Удалить кошелёк"},
    "copy_address": {"en": "Copy Address", "ru": "Копировать адрес"},
    "copy_all_data": {"en": "Copy All Data", "ru": "Копировать все данные"},
    "show_qr_code": {"en": "Show QR Code", "ru": "Показать QR-код"},
    "solana_networks": {"en": "Solana Networks:", "ru": "Сети Solana:"},
    "net_mainnet_beta": {
        "en": "mainnet-beta (real network)",
        "ru": "mainnet-beta (реальная сеть)",
    },
    "net_testnet": {"en": "testnet (not a real network)", "ru": "testnet (не реальная сеть)"},
    "net_devnet": {"en": "devnet (not a real network)", "ru": "devnet (не реальная сеть)"},
    "show_history": {"en": "Show History", "ru": "Показать историю"},
    "show_balance": {"en": "Show Balance", "ru": "Показать баланс"},
    "information": {"en": "Information:", "ru": "Информация:"},
    "address_page_title": {"en": "Address Page", "ru": "Страница адреса"},
    "wallet_deleted_ok": {"en": "Wallet deleted successfully!", "ru": "Кошелёк успешно удалён!"},
    "loading_history": {"en": "LOADING HISTORY...", "ru": "ЗАГРУЗКА ИСТОРИИ..."},
    "please_wait": {"en": "PLEASE WAIT", "ru": "ПОДОЖДИТЕ"},
    "transfer_this_token": {"en": "Transfer this token", "ru": "Перевести этот токен"},
    "swap_btn": {"en": "Swap", "ru": "Обмен"},
    "request_airdrop_1": {"en": "Request Airdrop 1 SOL", "ru": "Запросить airdrop 1 SOL"},
    "network_label": {"en": "Network: {net}", "ru": "Сеть: {net}"},
    "error_prefix": {"en": "Error: {err}", "ru": "Ошибка: {err}"},
    "no_transactions": {"en": "No transactions found.", "ru": "Транзакции не найдены."},
    "err_loading_history": {"en": "Error loading history!", "ru": "Ошибка загрузки истории!"},
    "err_balance": {"en": "Error loading balance!", "ru": "Ошибка загрузки баланса!"},
    "logs_label": {"en": "Logs:", "ru": "Логи:"},
    "no_logs": {"en": "No logs available", "ru": "Логи недоступны"},
    "signature_label": {"en": "Signature: {sig}", "ru": "Подпись: {sig}"},
    "history_status_fee": {
        "en": "Status: {status} | Fee: {fee} SOL",
        "ru": "Статус: {status} | Комиссия: {fee} SOL",
    },
    "slot_version_cu": {
        "en": "Slot: {slot} | Version: {version} | CU Consumed: {cu}",
        "ru": "Слот: {slot} | Версия: {version} | Потреблено CU: {cu}",
    },
    "status_success": {"en": "Success", "ru": "Успешно"},
    "status_failed": {"en": "Failed", "ru": "Не удалось"},
    "unknown": {"en": "Unknown", "ru": "Неизвестно"},
    "csv_saved": {"en": "CSV saved", "ru": "CSV сохранён"},
    "csv_saved_to": {
        "en": "Transaction history saved to:\n{path}",
        "ru": "История транзакций сохранена в:\n{path}",
    },
    "save_history_csv": {"en": "Save History as CSV", "ru": "Сохранить историю как CSV"},
    "save_history_csv_title": {
        "en": "Save transaction history CSV",
        "ru": "Сохранить историю транзакций CSV",
    },
    "balance_received_ok": {
        "en": "Balance for {addr} received successfully!",
        "ru": "Баланс для {addr} успешно получен!",
    },
    "suspicious_label": {"en": "Suspicious: {reasons}", "ru": "Подозрительно: {reasons}"},
    "flagged_risky": {"en": "flagged as risky", "ru": "помечен как рисковый"},
    "portfolio_value": {"en": "Portfolio value  ", "ru": "Стоимость портфеля  "},
    "no_priced_tokens": {"en": " (no priced tokens)", "ru": " (нет токенов с ценой)"},
    "spam_filter_summary": {"en": "Spam filter: {summary}", "ru": "Спам-фильтр: {summary}"},
    "spam_hidden_click_sg": {
        "en": "{n} spam token hidden — click to show",
        "ru": "{n} спам-токен скрыт — нажмите, чтобы показать",
    },
    "spam_hidden_click_pl": {
        "en": "{n} spam tokens hidden — click to show",
        "ru": "{n} спам-токенов скрыто — нажмите, чтобы показать",
    },
    "spam_count_sg": {"en": "{n} spam hidden", "ru": "{n} спам скрыт"},
    "spam_count_pl": {"en": "{n} spam hidden", "ru": "{n} спама скрыто"},
    "suspicious_count_sg": {"en": "{n} suspicious", "ru": "{n} подозрительный"},
    "suspicious_count_pl": {"en": "{n} suspicious", "ru": "{n} подозрительных"},
    "info_address": {"en": "Address: {val}", "ru": "Адрес: {val}"},
    "info_created": {"en": "Created: {val}", "ru": "Создан: {val}"},
    "info_private_key": {"en": "Private Key: {val}", "ru": "Приватный ключ: {val}"},
    "info_public_key": {"en": "Public Key: {val}", "ru": "Публичный ключ: {val}"},
    "info_words": {"en": "Words: {val}", "ru": "Слова: {val}"},
    "info_secret_key": {
        "en": "Secret Key (base58): {val}",
        "ru": "Секретный ключ (base58): {val}",
    },
    # --- more.py (hub sections + clear-storage dialog) - Phase 2 ---
    "tools": {"en": "Tools", "ru": "Инструменты"},
    "web3_defi": {"en": "WEB3 & DeFi", "ru": "WEB3 и DeFi"},
    "developer": {"en": "Developer", "ru": "Разработчик"},
    "clear_all_storage_q": {"en": "Clear ALL local storage?", "ru": "Очистить ВСЁ хранилище?"},
    "clear_all_storage_desc": {
        "en": "This permanently deletes every wallet, the PIN, contacts and "
              "WalletConnect pairing. Encrypted secrets cannot be recovered.",
        "ru": "Это безвозвратно удалит все кошельки, PIN-код, контакты и "
              "пару WalletConnect. Зашифрованные секреты восстановить нельзя.",
    },
    "clear_everything": {"en": "Clear everything", "ru": "Очистить всё"},
    "storage_cleared": {"en": "All local storage cleared.", "ru": "Всё хранилище очищено."},
    "badge_danger": {"en": "danger", "ru": "опасно"},
    "hub_connect_dapp": {"en": "Connect dApp", "ru": "Подключить dApp"},
    "hub_connect_dapp_desc": {
        "en": "Pair with a dApp via WalletConnect v2 and sign requests.",
        "ru": "Подключиться к dApp через WalletConnect v2 и подписывать запросы.",
    },
    "hub_nft": {"en": "NFT Gallery", "ru": "Галерея NFT"},
    "hub_nft_desc": {
        "en": "Browse and send your non-fungible tokens.",
        "ru": "Просматривайте и отправляйте ваши NFT.",
    },
    "hub_staking": {"en": "Liquid Staking", "ru": "Ликвидный стейкинг"},
    "hub_staking_desc": {
        "en": "Stake SOL into JitoSOL / mSOL / bSOL / jupSOL.",
        "ru": "Стейкать SOL в JitoSOL / mSOL / bSOL / jupSOL.",
    },
    "hub_address_book": {"en": "Address Book", "ru": "Адресная книга"},
    "hub_address_book_desc": {
        "en": "Saved recipients with address-poisoning protection.",
        "ru": "Сохранённые получатели с защитой от отравления адресов.",
    },
    "hub_settings_desc": {
        "en": "Theme, security and app preferences.",
        "ru": "Тема, безопасность и настройки приложения.",
    },
    "hub_storage_inspector": {"en": "Storage inspector", "ru": "Инспектор хранилища"},
    "hub_storage_inspector_desc": {
        "en": "View and edit raw shared_preferences keys.",
        "ru": "Просмотр и правка ключей shared_preferences.",
    },
    "hub_sim_inspector": {"en": "Simulation inspector", "ru": "Инспектор симуляции"},
    "hub_sim_inspector_desc": {
        "en": "Run the anti-phishing simulation on a pasted transaction.",
        "ru": "Запустить антифишинг-симуляцию на вставленной транзакции.",
    },
    "hub_rpc_inspector": {"en": "Raw RPC inspector", "ru": "Инспектор сырых RPC"},
    "hub_rpc_inspector_desc": {
        "en": "Run read-only JSON-RPC calls against any endpoint.",
        "ru": "Выполнять read-only JSON-RPC запросы к любой ноде.",
    },
    "hub_export_keys": {"en": "Export raw keys", "ru": "Экспорт сырых ключей"},
    "hub_export_keys_desc": {
        "en": "Reveal & copy a wallet's private key / mnemonic. DANGEROUS.",
        "ru": "Показать и скопировать приватный ключ / мнемонику. ОПАСНО.",
    },
    "hub_clear_storage": {"en": "Clear all storage", "ru": "Очистить хранилище"},
    "hub_clear_storage_desc": {
        "en": "Wipe every wallet, PIN and pairing. Irreversible.",
        "ru": "Стереть все кошельки, PIN и привязки. Безвозвратно.",
    },
    # ...keys added as modules are migrated (Phases 3-5)...
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
