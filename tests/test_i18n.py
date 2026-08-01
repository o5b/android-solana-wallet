"""Headless tests for the i18n foundation (Phase 1).

Run:
    PYTHONPATH=src venv/bin/python tests/test_i18n.py

Covers (per the Phase-1 plan §6.1):
  * ``t()`` contracts: en/ru lookup, missing-key fail-loud, lang fallback to
    English, default-lang (None) -> English, ``str.format`` interpolation.
  * ``tp()`` plural rule: RU singular vs plural, EN always plural.
  * ``normalize`` / ``available_languages`` / ``language_display_name``.
  * ``get_lang`` / ``set_lang`` round-trip + never-raises on corrupt storage.
  * ``ctx.t`` / ``ctx.tp`` bound to ``ctx.lang`` (the in-memory cache).
  * Completeness: every key in TRANSLATIONS has both ``en`` and ``ru`` entries.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flet

from ui import i18n
from ui.context import AppContext
from ui.i18n import (
    DEFAULT_LANG,
    ENGLISH,
    LANGS,
    LANG_KEY,
    RUSSIAN,
    TRANSLATIONS,
    available_languages,
    get_lang,
    language_display_name,
    normalize,
    set_lang,
    t,
    tp,
)


# ---------- mock shared_preferences -----------------------------------------

class MockSP:
    def __init__(self, values=None):
        self._values = dict(values or {})

    async def contains_key(self, k):
        return k in self._values

    async def get(self, k):
        return self._values.get(k)

    async def set(self, k, v):
        self._values[k] = v


class BoomSP(MockSP):
    async def contains_key(self, k):
        raise RuntimeError("simulated storage failure")


# ---------- runner ----------------------------------------------------------

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


# ============================ t() contracts =================================

def test_t_basic_lookup():
    check("t(save,en)=Save", t("save", "en") == "Save")
    check("t(save,ru)=Сохранить", t("save", "ru") == "Сохранить")
    check("t(ok,en)=OK", t("ok", "en") == "OK")
    check("t(cancel,ru)=Отмена", t("cancel", "ru") == "Отмена")


def test_t_missing_key_returns_key():
    check("missing key -> key itself", t("nope_not_a_key", "en") == "nope_not_a_key")
    check("missing key ignores fmt", t("nope", "en", x=1) == "nope")


def test_t_unknown_lang_falls_back_to_en():
    check("unknown lang -> en", t("save", "fr") == "Save")
    check("garbage lang -> en", t("save", "xxx") == "Save")


def test_t_none_lang_is_default():
    check("None lang -> default(en)", t("save", None) == "Save")
    check("t() no lang arg -> en", t("save") == "Save")


def test_t_interpolation():
    check("del_ok en interp",
          t("del_ok", "en", key="abc") == "abc deleted successfully!")
    check("del_ok ru interp",
          t("del_ok", "ru", key="abc") == "abc успешно удалён!")
    # Interpolation only runs when fmt provided; plain call returns the template.
    check("del_ok ru no fmt keeps placeholder",
          t("del_ok", "ru") == "{key} успешно удалён!")


# ============================ tp() plural rule ==============================

def test_tp_ru_singular():
    # n%10==1 and n%100!=11 -> singular
    check("tp ru n=1 -> sg", tp("spam_hidden_pl", "spam_hidden_sg", 1, "ru") == "1 спам-токен скрыт")
    check("tp ru n=21 -> sg", tp("spam_hidden_pl", "spam_hidden_sg", 21, "ru") == "21 спам-токен скрыт")
    check("tp ru n=101 -> sg", tp("spam_hidden_pl", "spam_hidden_sg", 101, "ru") == "101 спам-токен скрыт")


def test_tp_ru_plural():
    check("tp ru n=2 -> pl", tp("spam_hidden_pl", "spam_hidden_sg", 2, "ru") == "2 спам-токенов скрыто")
    check("tp ru n=5 -> pl", tp("spam_hidden_pl", "spam_hidden_sg", 5, "ru") == "5 спам-токенов скрыто")
    check("tp ru n=11 -> pl (exception)", tp("spam_hidden_pl", "spam_hidden_sg", 11, "ru") == "11 спам-токенов скрыто")


def test_tp_en_always_plural():
    check("tp en n=1 -> pl", tp("spam_hidden_pl", "spam_hidden_sg", 1, "en") == "1 spam tokens hidden")
    check("tp en n=5 -> pl", tp("spam_hidden_pl", "spam_hidden_sg", 5, "en") == "5 spam tokens hidden")


def test_tp_none_lang_defaults_en_plural():
    check("tp None lang -> en plural", tp("spam_hidden_pl", "spam_hidden_sg", 1) == "1 spam tokens hidden")


# ============================ normalize / display ===========================

def test_normalize():
    check("normalize(en)", normalize("en") == "en")
    check("normalize(ru)", normalize("ru") == "ru")
    check("normalize(unknown)->default", normalize("de") == DEFAULT_LANG)
    check("normalize(None)->default", normalize(None) == DEFAULT_LANG)
    check("normalize('')->default", normalize("") == DEFAULT_LANG)


def test_available_languages():
    langs = available_languages()
    check("available_languages returns LANGS", langs == LANGS)
    check("langs are en+ru", set(langs) == {ENGLISH, RUSSIAN})


def test_language_display_name():
    check("ru in ru -> Русский", language_display_name("ru", "ru") == "Русский")
    check("ru in en -> Russian", language_display_name("ru", "en") == "Russian")
    check("en in ru -> Английский", language_display_name("en", "ru") == "Английский")
    check("en in en -> English", language_display_name("en", "en") == "English")
    check("unknown lang -> code", language_display_name("de", "en") == "de")
    check("unknown in_lang -> en render", language_display_name("ru", "de") == "Russian")


# ============================ get_lang / set_lang ===========================

def test_get_lang_default_when_missing():
    page = type("P", (), {"shared_preferences": MockSP()})()
    check("get_lang missing -> en", asyncio.run(get_lang(page)) == ENGLISH)


def test_set_get_lang_round_trip():
    page = type("P", (), {"shared_preferences": MockSP()})()
    norm = asyncio.run(set_lang(page, "ru"))
    check("set_lang returns normalized", norm == "ru")
    check("get_lang reads back ru", asyncio.run(get_lang(page)) == "ru")
    check("LANG_KEY persisted", page.shared_preferences._values[LANG_KEY] == "ru")


def test_set_lang_normalizes_unknown():
    page = type("P", (), {"shared_preferences": MockSP()})()
    norm = asyncio.run(set_lang(page, "klingon"))
    check("set_lang unknown -> en", norm == ENGLISH)


def test_get_lang_never_raises_on_corrupt():
    page = type("P", (), {"shared_preferences": BoomSP()})()
    # Must not raise; falls back to default.
    check("get_lang corrupt -> en", asyncio.run(get_lang(page)) == ENGLISH)


# ============================ ctx.t / ctx.tp ================================

def test_ctx_t_uses_lang_cache():
    page = type("P", (), {})()
    page.shared_preferences = MockSP()
    page.theme_mode = flet.ThemeMode.LIGHT
    ctx = AppContext(page=page, session={})
    check("ctx default lang en", ctx.lang == ENGLISH)
    check("ctx.t en save", ctx.t("save") == "Save")
    ctx.lang = RUSSIAN
    check("ctx.t ru save", ctx.t("save") == "Сохранить")
    ctx.lang = ENGLISH
    check("ctx.t back to en", ctx.t("save") == "Save")


def test_ctx_t_interpolation():
    page = type("P", (), {})()
    page.shared_preferences = MockSP()
    ctx = AppContext(page=page, session={})
    check("ctx.t del_ok en", ctx.t("del_ok", key="K1") == "K1 deleted successfully!")
    ctx.lang = RUSSIAN
    check("ctx.t del_ok ru", ctx.t("del_ok", key="K1") == "K1 успешно удалён!")


def test_ctx_t_missing_key():
    page = type("P", (), {})()
    page.shared_preferences = MockSP()
    ctx = AppContext(page=page, session={})
    check("ctx.t missing -> key", ctx.t("does_not_exist") == "does_not_exist")


def test_ctx_tp_plural():
    page = type("P", (), {})()
    page.shared_preferences = MockSP()
    ctx = AppContext(page=page, session={})
    ctx.lang = RUSSIAN
    check("ctx.tp ru sg", ctx.tp("spam_hidden_pl", "spam_hidden_sg", 1) == "1 спам-токен скрыт")
    check("ctx.tp ru pl", ctx.tp("spam_hidden_pl", "spam_hidden_sg", 5) == "5 спам-токенов скрыто")
    ctx.lang = ENGLISH
    check("ctx.tp en pl", ctx.tp("spam_hidden_pl", "spam_hidden_sg", 1) == "1 spam tokens hidden")


# ============================ completeness ==================================

def test_translations_have_en_and_ru():
    missing_en = [k for k, langs in TRANSLATIONS.items() if "en" not in langs]
    missing_ru = [k for k, langs in TRANSLATIONS.items() if "ru" not in langs]
    check("no key missing en", missing_en == [])
    check("no key missing ru", missing_ru == [])


def test_language_names_complete():
    # Every language must have a display name in every language.
    for lang in LANGS:
        names = i18n.LANGUAGE_NAMES.get(lang, {})
        for in_lang in LANGS:
            check(f"name of {lang} in {in_lang} present",
                  bool(names.get(in_lang)))


# ============================ runner ========================================

def main():
    tests = [
        test_t_basic_lookup,
        test_t_missing_key_returns_key,
        test_t_unknown_lang_falls_back_to_en,
        test_t_none_lang_is_default,
        test_t_interpolation,
        test_tp_ru_singular,
        test_tp_ru_plural,
        test_tp_en_always_plural,
        test_tp_none_lang_defaults_en_plural,
        test_normalize,
        test_available_languages,
        test_language_display_name,
        test_get_lang_default_when_missing,
        test_set_get_lang_round_trip,
        test_set_lang_normalizes_unknown,
        test_get_lang_never_raises_on_corrupt,
        test_ctx_t_uses_lang_cache,
        test_ctx_t_interpolation,
        test_ctx_t_missing_key,
        test_ctx_tp_plural,
        test_translations_have_en_and_ru,
        test_language_names_complete,
    ]
    for fn in tests:
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\n============================================")
    print(f"{'ALL I18N TESTS PASSED' if _failed == 0 else f'{_failed} FAILED'}")
    print(f"Total: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
