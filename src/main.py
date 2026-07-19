import flet

# Phase 7 refactor (tiered-UI redesign) — COMPLETE.
#
# ``main()`` used to be a ~5.4k-line monolith (~150 nested closures) holding
# every screen, handler and piece of routing state. Phase 7 incrementally
# extracted those closures into the ``ui/`` package one cohesive group at a
# time:
#
#   ui/context.py           — AppContext (shared page/session/controls state)
#   ui/security_gate.py     — PIN gate, encrypted secrets, auto-lock (Group 6c)
#   ui/experience.py        — Simple / Pro / Developer mode registry
#   ui/formatting.py        — shared pure helpers (short_addr)
#   ui/qr.py                — QR rendering (no flet dep)
#   ui/wallets.py           — canonical wallet-record loader
#   ui/components/          — one module per screen:
#     priority_fee / addressbook / devtools / nft / staking / walletconnect /
#     transfer / wallet_create / swap / more / settings / balance
#
# Group 6g (this commit) is the final orchestrator step: every remaining piece
# of bootstrap + routing plumbing (page config, the AppContext, the shared
# Columns, the navbar, the back-nav handler, the route dispatcher, the
# homepage View, the bootstrap sequence) moved into ``ui/app.py``'s
# ``build_app(page)``. ``main.py`` is now a one-line entry point.
#
# Migration contract for future work: any new screen belongs in its own
# ``ui/components/*.py`` module with a ``build_*_page(ctx)`` view builder +
# optional ``*_enter(ctx)`` repopulation hook, plus one
# ``elif page.route == ...`` branch in ``ui/app.py``'s ``route_change``.

from ui.app import build_app


async def main(page: flet.Page):
    """App entry point. Delegates everything to the orchestrator."""
    await build_app(page)


flet.run(main)
