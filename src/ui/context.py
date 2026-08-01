"""Shared application context for the ``ui/`` package.

``main.py`` is a single ``async def main(page)`` closure (~5.4k lines, ~150
nested closures) that implicitly capture ``page``, the in-memory ``session``
dict, PIN constants and a handful of shared flet controls. Phase 7 of the
tiered-UI redesign extracts those closures into ``ui/`` submodules; each
extracted function takes an :class:`AppContext` as its first argument instead
of relying on lexical closure capture.

Design rules
------------
* An ``AppContext`` **wraps** the live objects created in ``main()``, it does
  not duplicate them: ``ctx.session`` is the *same* dict object as the one
  ``main()`` mutates, so legacy closures and extracted modules share one source
  of truth while the migration is incremental.
* Only the state that more than one module needs lives here. Pure helpers stay
  in their own module and take explicit arguments.
* Extracted modules must never reach back into ``main.py``; they depend only on
  ``solana/`` business logic, ``ui.experience`` and ``ctx``.
"""

from dataclasses import dataclass, field

import flet

from solana.security import decrypt_wallet_secrets, encrypt_wallet_secrets, get_secret
from ui.i18n import DEFAULT_LANG, t as _translate, tp as _translate_plural


@dataclass
class AppContext:
    """Bundle of shared app state passed to every ``ui/`` module function.

    Attributes
    ----------
    page:
        The live ``flet.Page``. Extracted modules use ``ctx.page`` for
        ``page.update()`` / route navigation / ``shared_preferences``.
    session:
        In-memory unlock state dict (``unlocked`` / ``key`` / ``last_activity``
        / ``lock_dialog``). Mutated in place by both legacy closures and
        extracted modules. Held by reference, never copied.
    pin_salt_key / pin_verifier_key / auto_lock_seconds:
        Security constants kept in one place so the security module and
        ``main()`` agree on the ``shared_preferences`` key names.
    controls:
        Lazy registry for shared flet controls that more than one view/handler
        references (e.g. ``theme_control``, ``experience_dd``, file pickers).
        Populated by ``main()`` during bootstrap; modules read via
        ``ctx.controls["name"]``.
    """

    page: flet.Page
    session: dict
    pin_salt_key: str = "security.pin_salt"
    pin_verifier_key: str = "security.pin_verifier"
    auto_lock_seconds: int = 300
    controls: dict = field(default_factory=dict)
    lang: str = DEFAULT_LANG  # UI language cache (read at bootstrap / on switch)

    # -- convenience accessors (mirror the helpers main.py closures used) ------

    def is_unlocked(self) -> bool:
        """True when the session key is available (PIN unlocked)."""
        return bool(self.session.get("unlocked")) and self.session.get("key") is not None

    def reset_activity(self) -> None:
        """Mark now as the most recent user activity (postpones auto-lock)."""
        import time

        self.session["last_activity"] = time.time()

    def safe_update(self) -> None:
        """``page.update()`` that swallows the "control not in tree" errors
        raised when a handler fires for a control that has been navigated away
        from. Mirrors the ``try/except`` pattern used throughout ``main.py``."""
        try:
            self.page.update()
        except Exception:
            pass

    def close_dialog(self, dlg) -> None:
        """Close a dialog control and refresh the page.

        Mirrors the legacy ``_close_dlg(dlg)`` closure: ``dlg.open = False`` then
        a fault-tolerant ``page.update()``. Shared by the address-book dialogs and
        the dev-warning / clear-storage dialogs still living in ``main.py``.
        """
        dlg.open = False
        self.safe_update()

    # -- wallet secrets -------------------------------------------------------

    def get_wallet_private_key(self, wallet: dict) -> str:
        """Plaintext ``private_key_hex`` for a wallet ('' if watch-only / locked).

        Mirrors the legacy ``get_wallet_private_key`` closure in ``main.py``:
        returns ``""`` while the app is locked (no in-memory Fernet key), else
        decrypts the secret on demand via :func:`solana.security.get_secret`.
        Widely used by the transfer / swap / liquid-staking / WalletConnect
        flows; lives on the context so every extracted ``ui/`` module can reach
        the signer key without reaching back into ``main.py``.
        """
        if not self.is_unlocked():
            return ""
        return get_secret(wallet, "private_key_hex", self.session["key"])

    def has_wallet_private_key(self, wallet: dict) -> bool:
        """True when a usable (decrypted) private key is available for `wallet`."""
        return bool(self.get_wallet_private_key(wallet))

    def encrypt_for_storage(self, value: dict) -> dict:
        """Encrypt a wallet record's secrets before persistence.

        Mirrors the legacy ``encrypt_for_storage`` closure in ``main.py``: when
        the session is unlocked (a PIN is active) secrets are encrypted with the
        in-memory Fernet key; otherwise the record is returned unchanged and is
        migrated to ciphertext later once a PIN is established.
        """
        if self.is_unlocked():
            return encrypt_wallet_secrets(value, self.session["key"])
        return value

    def decrypt_for_display(self, wallet: dict) -> dict:
        """Wallet dict with secrets decrypted (for the Wallet Info dialog).

        Mirrors the legacy ``decrypt_for_display`` closure: returns the record
        unchanged while the app is locked, otherwise decrypts every secret
        field in place via :func:`solana.security.decrypt_wallet_secrets`.
        Used by the Wallet Info dialog (Group 6e — still in ``main.py``).
        """
        if not self.is_unlocked():
            return wallet
        return decrypt_wallet_secrets(wallet, self.session["key"])

    # -- internationalization --------------------------------------------------

    def t(self, msg_key: str, **fmt) -> str:
        """Translate ``msg_key`` using the session language + interpolation.

        ``ctx.lang`` is a cache read once at bootstrap (see :func:`ui.app.
        build_app`) and updated when the user picks a language in Settings, so
        call sites never need to thread ``page`` through. Mirrors
        :func:`ui.i18n.t` with the session language bound.

        The lookup-key param is ``msg_key`` (not ``key``) so the common
        placeholder name ``key`` stays free for interpolation, e.g.
        ``ctx.t("del_ok", key=storage_key)``.
        """
        return _translate(msg_key, self.lang, **fmt)

    def tp(self, key_plural: str, key_singular: str, n: int, **fmt) -> str:
        """Plural-aware translate using the session language.

        Mirrors :func:`ui.i18n.tp` (RU: ``n%10==1 and n%100!=11`` -> singular).
        """
        return _translate_plural(key_plural, key_singular, n, self.lang, **fmt)
