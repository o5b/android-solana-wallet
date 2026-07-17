"""Pure formatting helpers shared across the UI layer.

These functions build no flet controls and take no :class:`~ui.context.AppContext`
— they are plain string/value transforms. Keeping them out of ``main.py`` lets
several extracted ``ui/components`` modules (address book, NFT gallery, transfer
screens, wallet cards) share one canonical implementation instead of each
re-declaring its own inline copy of the same helper.
"""

from __future__ import annotations


def short_addr(addr: str, head: int = 6, tail: int = 6) -> str:
    """Truncate a long base58 address to ``head…tail`` for compact display.

    Empty input returns ``""``; short enough addresses are returned unchanged
    (so the ellipsis never eats more than it shows).
    """
    if not addr:
        return ""
    if len(addr) <= head + tail + 1:
        return addr
    return f"{addr[:head]}…{addr[-tail:]}"
