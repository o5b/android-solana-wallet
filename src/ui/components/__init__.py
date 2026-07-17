"""Reusable UI component builders extracted from ``main.py``.

Each module exposes functions that take an :class:`ui.context.AppContext` as
their first argument. Components are stateless builders (they construct flet
controls + return small state dicts); view orchestration lives in
``ui/views``.
"""
