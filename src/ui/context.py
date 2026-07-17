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
