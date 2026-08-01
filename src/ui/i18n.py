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
    # --- security_gate.py (PIN setup/unlock dialogs) - Phase 3 ---
    "pin_create": {"en": "Create a PIN ({n}+ digits)", "ru": "Создайте PIN-код ({n}+ цифр)"},
    "pin_confirm": {"en": "Confirm PIN", "ru": "Подтвердите PIN-код"},
    "pin_too_short": {"en": "PIN must be {n}+ digits.", "ru": "PIN-код должен быть из {n}+ цифр."},
    "pin_mismatch": {"en": "PINs do not match.", "ru": "PIN-коды не совпадают."},
    "pin_setup_title": {"en": "Set up a PIN", "ru": "Настройка PIN-кода"},
    "pin_setup_desc": {
        "en": "This PIN encrypts your private keys at rest and unlocks the app. "
              "Do not forget it: lost PINs cannot be recovered.",
        "ru": "Этот PIN-код шифрует ваши приватные ключи и разблокирует приложение. "
              "Не забывайте его: утраченный PIN-код восстановить нельзя.",
    },
    "pin_set_btn": {"en": "Set PIN", "ru": "Задать PIN-код"},
    "pin_enter": {"en": "Enter PIN", "ru": "Введите PIN-код"},
    "pin_incorrect": {"en": "Incorrect PIN.", "ru": "Неверный PIN-код."},
    "pin_unlock": {"en": "Unlock", "ru": "Разблокировать"},
    "pin_forgot": {"en": "Forgot PIN?", "ru": "Забыли PIN-код?"},
    "reset_everything_q": {"en": "Reset everything?", "ru": "Сбросить всё?"},
    "reset_everything_desc": {
        "en": "This will permanently delete the PIN and ALL stored wallets "
              "(their encrypted keys become unrecoverable). Only continue "
              "if you have your seed phrases backed up.",
        "ru": "Это безвозвратно удалит PIN-код и ВСЕ сохранённые кошельки "
              "(их зашифрованные ключи станет невозможно восстановить). "
              "Продолжайте, только если у вас есть резервные копии сид-фраз.",
    },
    "reset_wipe": {"en": "Reset & Wipe", "ru": "Сбросить и стереть"},
    # --- addressbook.py (contacts + poisoning gate) - Phase 3 ---
    "ab_empty": {"en": "Your address book is empty.", "ru": "Ваша адресная книга пуста."},
    "ab_empty_hint": {
        "en": "Add a contact on the Address Book page first.",
        "ru": "Сначала добавьте контакт на странице «Адресная книга».",
    },
    "no_name": {"en": "(no name)", "ru": "(без имени)"},
    "ab_pick_contact": {"en": "Pick a contact", "ru": "Выберите контакт"},
    "contact_name": {"en": "Contact name", "ru": "Имя контакта"},
    "ab_save_title": {"en": "Save to address book", "ru": "Сохранить в адресную книгу"},
    "ab_solana_address": {"en": "Solana address (base58)", "ru": "Адрес Solana (base58)"},
    "ab_note": {"en": "Note (optional)", "ru": "Заметка (необязательно)"},
    "ab_add_contact": {"en": "Add contact", "ru": "Добавить контакт"},
    "ab_add_contact_btn": {"en": "Add Contact", "ru": "Добавить контакт"},
    "ab_protection_hint": {
        "en": "Transfers warn you when a recipient looks like a saved contact "
              "but isn't an exact match (address-poisoning protection).",
        "ru": "Переводы предупреждают, когда получатель похож на сохранённый "
              "контакт, но не совпадает точно (защита от отравления адресов).",
    },
    "ab_no_contacts": {"en": "No contacts yet.", "ru": "Пока нет контактов."},
    "ab_contacts_count": {"en": "Contacts ({n}):", "ru": "Контакты ({n}):"},
    "ab_copied": {"en": "Copied {name}.", "ru": "Скопировано: {name}."},
    "ab_err_name": {"en": "Contact name is required.", "ru": "Укажите имя контакта."},
    "ab_err_address": {"en": "Address is required.", "ru": "Укажите адрес."},
    "ab_err_invalid_addr": {
        "en": "Not a valid Solana address: {addr}",
        "ru": "Недопустимый адрес Solana: {addr}",
    },
    "ab_err_duplicate": {
        "en": "This address is already in your address book.",
        "ru": "Этот адрес уже есть в адресной книге.",
    },
    "ab_saved": {"en": "Saved contact '{name}'.", "ru": "Контакт «{name}» сохранён."},
    "my_wallet": {"en": "My Wallet", "ru": "Мой кошелёк"},
    "saved_address": {"en": "saved address", "ru": "сохранённый адрес"},
    "poison_known": {"en": "Known contact: {name}", "ru": "Известный контакт: {name}"},
    "poison_danger": {
        "en": "DANGER — possible address poisoning: {reasons}",
        "ru": "ОПАСНО — возможное отравление адресов: {reasons}",
    },
    "poison_caution": {"en": "Caution: {reasons}", "ru": "Внимание: {reasons}"},
    "poison_not_in_book": {
        "en": "This address is not in your address book. Double-check it carefully.",
        "ru": "Этого адреса нет в вашей адресной книге. Тщательно перепроверьте его.",
    },
    "poison_recipient_warn": {
        "en": "This recipient may not be who you think it is.",
        "ru": "Получатель может быть не тем, за кого себя выдаёт.",
    },
    "poison_entered": {"en": "Entered: {val}", "ru": "Введено: {val}"},
    "poison_explain": {
        "en": "Address-poisoning scams send tiny amounts from a look-alike address "
              "hoping you copy it by mistake. Continue ONLY if you have verified "
              "the recipient out of band.",
        "ru": "При отравлении адресов мошенники отправляют крошечные суммы с похожего "
              "адреса, надеясь, что вы скопируете его по ошибке. Продолжайте, ТОЛЬКО "
              "если вы проверили получателя другим способом.",
    },
    "poison_proceed": {"en": "I'm sure — proceed", "ru": "Я уверен — продолжить"},
    "poison_suspicious_title": {"en": "Suspicious recipient", "ru": "Подозрительный получатель"},
    # --- wallet_create.py (create/recover/add pages + seed quiz) - Phase 3 ---
    "wallet_name_field": {"en": "Wallet Name", "ru": "Название кошелька"},
    "wallet_desc_field": {"en": "Wallet description", "ru": "Описание кошелька"},
    "recover_secret_field": {
        "en": "Wallet Secret Words (12/24) or Secret Key base58 (length=88)",
        "ru": "Секретные слова кошелька (12/24) или секретный ключ base58 (длина=88)",
    },
    "add_address_field": {"en": "Add Wallet Address (base58) ", "ru": "Добавить адрес кошелька (base58) "},
    "clear": {"en": "Clear", "ru": "Очистить"},
    "copied_to_clipboard": {
        "en": "Data copied to clipboard!",
        "ru": "Данные скопированы в буфер обмена!",
    },
    "card_created": {"en": "Created:", "ru": "Создан:"},
    "card_name": {"en": "Wallet Name:", "ru": "Название кошелька:"},
    "card_desc": {"en": "Wallet Description:", "ru": "Описание кошелька:"},
    "card_address": {
        "en": "Wallet Address (Base58, size 44):",
        "ru": "Адрес кошелька (Base58, размер 44):",
    },
    "card_secret_key": {
        "en": "Secret Key (Base58, size 88, e.g. Phantom):",
        "ru": "Секретный ключ (Base58, размер 88, напр. Phantom):",
    },
    "card_private_key": {"en": "Private Key (Hex, size 64):", "ru": "Приватный ключ (Hex, размер 64):"},
    "card_public_key": {"en": "Public Key (Hex):", "ru": "Публичный ключ (Hex):"},
    "card_words": {"en": "Mnemonic Words (12/24 words):", "ru": "Мнемонические слова (12/24 слова):"},
    "error_colon": {"en": "Error:", "ru": "Ошибка:"},
    "quiz_intro": {
        "en": "Confirm your recovery phrase by entering the requested words.",
        "ru": "Подтвердите фразу восстановления, введя запрошенные слова.",
    },
    "quiz_word": {"en": "Word #{n}", "ru": "Слово #{n}"},
    "quiz_wrong": {
        "en": "One or more words are incorrect. Check your spelling and try again.",
        "ru": "Одно или несколько слов неверны. Проверьте написание и попробуйте снова.",
    },
    "verify_backup_title": {"en": "Verify your backup", "ru": "Проверьте резервную копию"},
    "quiz_show_again": {"en": "Show words again", "ru": "Показать слова снова"},
    "verify_btn": {"en": "Verify", "ru": "Проверить"},
    "reveal_warning": {
        "en": "These 12 words are the ONLY way to recover this wallet. "
              "Write them down and store them safely. No one can recover them for you.",
        "ru": "Эти 12 слов — единственный способ восстановить этот кошелёк. "
              "Запишите их и храните в надёжном месте. Никто не сможет восстановить их за вас.",
    },
    "reveal_next": {
        "en": "You will be asked to confirm them on the next screen.",
        "ru": "На следующем экране вас попросят их подтвердить.",
    },
    "reveal_title": {"en": "Your recovery phrase", "ru": "Ваша фраза восстановления"},
    "reveal_written": {"en": "I've written it down", "ru": "Я записал их"},
    "create_wallet_page_title": {"en": "Create New Wallet Page", "ru": "Страница создания кошелька"},
    "create_new_wallet": {"en": "Create New Wallet", "ru": "Создать новый кошелёк"},
    "recover_wallet_page_title": {"en": "Recover Wallet Page", "ru": "Страница восстановления"},
    "recover_wallet_header": {"en": "Recover wallet", "ru": "Восстановить кошелёк"},
    "add_wallet_page_title": {"en": "Add Wallet Address Page", "ru": "Страница добавления адреса"},
    "add_wallet_header": {"en": "Add wallet address", "ru": "Добавить адрес кошелька"},
    "err_input_secret": {"en": "Input the secret", "ru": "Введите секрет"},
    "err_input_address": {"en": "Input the wallet address", "ru": "Введите адрес кошелька"},
    # --- transfer.py (SOL/SPL transfer, burn/close) - Phase 3 ---
    "amount_field": {"en": "Input the amount", "ru": "Введите количество"},
    "amount_sol_field": {"en": "Input the amount of SOL", "ru": "Введите количество SOL"},
    "recipient_field": {"en": "Recipient address or name.sol", "ru": "Адрес получателя или name.sol"},
    "secret_field": {
        "en": "Enter Secret (12/24 Words or Private Key)",
        "ru": "Введите секрет (12/24 слова или приватный ключ)",
    },
    "tooltip_pick_contact": {"en": "Pick from address book", "ru": "Выбрать из адресной книги"},
    "tooltip_save_contact": {"en": "Save recipient as contact", "ru": "Сохранить получателя как контакт"},
    "inspect_solscan": {"en": "Inspect on Solscan", "ru": "Проверить в Solscan"},
    "transfer_token_btn": {"en": "Transfer Token", "ru": "Перевести токен"},
    "transfer_sol_btn": {"en": "Transfer SOL", "ru": "Перевести SOL"},
    "burn_btn": {"en": "Burn", "ru": "Сжечь"},
    "burn_all_close_btn": {"en": "Burn All & Close Account", "ru": "Сжечь всё и закрыть аккаунт"},
    "burn_close_btn": {"en": "Burn & Close", "ru": "Сжечь и закрыть"},
    "burn_close_hint": {
        "en": "Burn destroys tokens. Close Account also refunds the rent SOL (~0.002) to your wallet.",
        "ru": "Сжигание уничтожает токены. Закрытие аккаунта также возвращает арендную плату SOL (~0,002) в ваш кошелёк.",
    },
    "burn_close_q": {"en": "Burn all and close account?", "ru": "Сжечь всё и закрыть аккаунт?"},
    "burn_close_confirm": {
        "en": "This will DESTROY your entire balance of {symbol} and close the "
              "token account, refunding the rent (~0.002 SOL) to your wallet. "
              "This action cannot be undone.",
        "ru": "Это УНИЧТОЖИТ весь ваш баланс {symbol} и закроет токен-аккаунт, "
              "вернув арендную плату (~0,002 SOL) в ваш кошелёк. "
              "Это действие нельзя отменить.",
    },
    "burning": {"en": "BURNING...", "ru": "СЖИГАНИЕ..."},
    "burning_closing": {"en": "BURNING & CLOSING...", "ru": "СЖИГАНИЕ И ЗАКРЫТИЕ..."},
    "token_page_title": {"en": "Token Page", "ru": "Страница токена"},
    "spl_transfer_title": {"en": "SPL Token Transfer", "ru": "Перевод SPL-токена"},
    "transfer_spl_title": {"en": "Transfer SPL Token", "ru": "Перевод SPL-токена"},
    "transfer_sol_info": {"en": "Transfer sol info:", "ru": "Информация о переводе SOL:"},
    "err_drop_down_btn": {
        "en": "Error spl_token_arrow_drop_down_button_click!",
        "ru": "Ошибка spl_token_arrow_drop_down_button_click!",
    },
    "err_drop_up_btn": {
        "en": "Error spl_token_arrow_drop_up_button_click!",
        "ru": "Ошибка spl_token_arrow_drop_up_button_click!",
    },
    # transfer TextSpan labels
    "lbl_network": {"en": "Network: ", "ru": "Сеть: "},
    "lbl_from_address": {"en": "From Address: ", "ru": "Адрес отправителя: "},
    "lbl_token": {"en": "Token: ", "ru": "Токен: "},
    "lbl_amount": {"en": "Amount: ", "ru": "Количество: "},
    "lbl_info_msg": {"en": "Information message: ", "ru": "Информационное сообщение: "},
    "lbl_from": {"en": "From: ", "ru": "От: "},
    "lbl_to": {"en": "To: ", "ru": "Кому: "},
    "lbl_transfer": {"en": "Transfer: ", "ru": "Перевод: "},
    "lbl_balance_before": {"en": "Balance before: ", "ru": "Баланс до: "},
    "lbl_balance_after": {"en": "Balance after: ", "ru": "Баланс после: "},
    # transfer result / validation messages
    "key_required": {
        "en": "Private key is required (unlock the wallet or enter the secret).",
        "ru": "Требуется приватный ключ (разблокируйте кошелёк или введите секрет).",
    },
    "key_error": {"en": "Error getting private key: {err}", "ru": "Ошибка получения приватного ключа: {err}"},
    "key_seed_failed": {
        "en": "Failed to get private key from seed phrase.",
        "ru": "Не удалось получить приватный ключ из сид-фразы.",
    },
    "invalid_secret": {"en": "Invalid secret.", "ru": "Недопустимый секрет."},
    "no_key_generic": {
        "en": "Could not proceed. Private key is missing or invalid.",
        "ru": "Не удалось продолжить. Приватный ключ отсутствует или недействителен.",
    },
    "no_key_transfer": {
        "en": "Could not proceed with transfer. Private key is missing or invalid.",
        "ru": "Не удалось выполнить перевод. Приватный ключ отсутствует или недействителен.",
    },
    "spl_transfer_ok": {
        "en": "Transfer of {amount} {symbol} was successful!",
        "ru": "Перевод {amount} {symbol} выполнен успешно!",
    },
    "transfer_error": {"en": "Transfer Error: {err}", "ru": "Ошибка перевода: {err}"},
    "transfer_failed": {
        "en": "Transfer failed for an unknown reason.",
        "ru": "Перевод не выполнен по неизвестной причине.",
    },
    "invalid_amount": {"en": "Invalid transfer amount.", "ru": "Недопустимая сумма перевода."},
    "invalid_amount_format": {"en": "Invalid amount format.", "ru": "Недопустимый формат суммы."},
    "invalid_recipient": {"en": "Invalid recipient address.", "ru": "Недопустимый адрес получателя."},
    "invalid_burn_amount": {"en": "Invalid burn amount.", "ru": "Недопустимая сумма сжигания."},
    "burn_ok": {
        "en": "Burn of {amount} {symbol} was successful!",
        "ru": "Сжигание {amount} {symbol} выполнено успешно!",
    },
    "burn_error": {"en": "Burn Error: {err}", "ru": "Ошибка сжигания: {err}"},
    "burn_failed": {
        "en": "Burn failed for an unknown reason.",
        "ru": "Сжигание не выполнено по неизвестной причине.",
    },
    "burn_close_ok": {
        "en": "All {symbol} burned and the token account was closed. "
              "Rent SOL has been refunded to {addr}.",
        "ru": "Все {symbol} сожжены, а токен-аккаунт закрыт. "
              "Арендная плата SOL возвращена на {addr}.",
    },
    "burn_close_error": {"en": "Burn & Close Error: {err}", "ru": "Ошибка сжигания и закрытия: {err}"},
    "burn_close_failed": {
        "en": "Burn & Close failed for an unknown reason.",
        "ru": "Сжигание и закрытие не выполнены по неизвестной причине.",
    },
    "sol_transfer_fee": {"en": "Transfer fee: {fee} SOL", "ru": "Комиссия за перевод: {fee} SOL"},
    "sol_transfer_ok": {
        "en": "Transfer of {amount} SOL was Successfully!",
        "ru": "Перевод {amount} SOL выполнен успешно!",
    },
    "sol_transfer_error": {
        "en": "Error during Transfer. Error Msg: {err}",
        "ru": "Ошибка при переводе. Сообщение об ошибке: {err}",
    },
    "sol_transfer_error_bare": {"en": "Error during Transfer!", "ru": "Ошибка при переводе!"},
    "sol_error_result": {"en": "Error Result: {result}", "ru": "Ошибка результата: {result}"},
    "sol_insufficient": {
        "en": "Not enough SOL balance for transfer.",
        "ru": "Недостаточно SOL для перевода.",
    },
    "sol_invalid_amount": {
        "en": "The amount of SOL={amount} is not valid. Please enter the correct number.",
        "ru": "Сумма SOL={amount} недопустима. Введите корректное число.",
    },
    "sol_invalid_recipient": {
        "en": "The recipient wallet address: {addr} is not valid. Please enter the correct recipient wallet address.",
        "ru": "Адрес получателя: {addr} недопустим. Введите корректный адрес получателя.",
    },
    "sol_key_error_attempts": {
        "en": "Error after: {attempts} attempts to get private key from secret words! Error Msg: {err}",
        "ru": "Ошибка после: {attempts} попыток получить приватный ключ из секретных слов! Сообщение: {err}",
    },
    "sol_key_failed": {
        "en": "Failed to get private key after: {attempts} attempts from secret words",
        "ru": "Не удалось получить приватный ключ после: {attempts} попыток из секретных слов",
    },
    "sol_error_secret": {"en": "Error Secret!", "ru": "Ошибка секрета!"},
    "airdrop_no_result": {
        "en": "Not Result request airdrop sol for wallet: {addr}",
        "ru": "Нет результата запроса airdrop SOL для кошелька: {addr}",
    },
    "airdrop_result": {
        "en": "The result airdrop SOL for wallet address: {addr}: {result}",
        "ru": "Результат запроса airdrop SOL для адреса: {addr}: {result}",
    },
    # ...keys added as modules are migrated (Phases 4-5)...
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
