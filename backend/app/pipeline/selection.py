"""Operator outreach-selection store (demo-scope, in-memory).

Brief asks for a "checkbox to mark a lead as selected for outreach" + a sketch
of how operator feedback closes the loop. We capture the selection bit per
lead in a process-local set so the checkbox state survives tab switches and
page reloads within a single backend session. Cleared on re-ingest and on
backend restart (brief: "Persistence beyond what you need to demo the flow"
is out of scope).

Future closing-the-loop work would persist (lead_id, feature_snapshot,
selected_at) so a downstream job can fit weights from confirmed picks.
"""
from __future__ import annotations

import threading

_selected: set[str] = set()
_lock = threading.Lock()


def set_selected(lead_id: str, selected: bool) -> None:
    with _lock:
        if selected:
            _selected.add(lead_id)
        else:
            _selected.discard(lead_id)


def is_selected(lead_id: str) -> bool:
    with _lock:
        return lead_id in _selected


def all_selected() -> set[str]:
    with _lock:
        return set(_selected)


def clear() -> None:
    with _lock:
        _selected.clear()
