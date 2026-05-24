"""Per-lead pipeline trace + global event log.

Two separate concerns live here:

  Trace      = the full audit narrative for ONE lead's rationale generation
               (stages 1-6: features, scoring, prompt, LLM response, factuality,
               outcome). Stored in-memory keyed by lead_id, lazily fetched by
               GET /api/leads/{id}/trace when the operator expands a row.

  Events     = a ring buffer of cross-lead system events (ingest, persona
               inferred, batch dispatched, factuality failure). Powers the
               global Trace tab. Bounded so memory stays flat over time.

Both are process-local and cleared on restart. The brief weights auditability;
this module is the surface the operator (or grader) inspects to defend any
single ranking decision and to see system-level behavior.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventKind = Literal[
    "ingest",
    "persona_inferred",
    "ranking_request",
    "batch_dispatched",
    "batch_complete",
    "llm_call",
    "factuality_fail",
    "fallback",
    "cache_cleared",
]


@dataclass
class LLMCallTrace:
    """Captured from a single Anthropic API call."""
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    raw_response_text: str | None = None
    error: str | None = None  # set iff the call raised


@dataclass
class FactualityCheck:
    ok: bool
    cited_hs_codes: list[str] = field(default_factory=list)
    cited_ports: list[str] = field(default_factory=list)
    cited_competitors: list[str] = field(default_factory=list)
    cited_titles: list[str] = field(default_factory=list)
    invalid_hs_codes: list[str] = field(default_factory=list)
    has_anchor: bool = False
    reason: str | None = None  # human-readable why-rejected


@dataclass
class RationaleTrace:
    """Full audit trail for one lead's rationale.

    Populated by the rationale layer; read by GET /api/leads/{id}/trace.
    """
    lead_id: str
    generated_at: float
    # Stage inputs — exact strings sent to the LLM (full, not truncated)
    user_payload: str                       # per-lead user message
    system_prompt: str                      # full cached system prompt
    # Stage 4: LLM call (None if API key absent → went straight to fallback)
    llm: LLMCallTrace | None = None
    # Stage 5: factuality (None if no LLM ran)
    factuality: FactualityCheck | None = None
    # Stage 6: final outcome
    final_source: str = ""                  # one of: llm, fallback_no_key, fallback_error, fallback_factuality
    final_text: str = ""
    fallback_text: str | None = None        # populated whenever a fallback ran (for compare)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    """One entry in the global event ring buffer."""
    seq: int
    ts: float
    kind: EventKind
    summary: str               # one-line human description
    payload: dict[str, Any] = field(default_factory=dict)


# ---------- per-lead trace store ----------

_traces: dict[str, RationaleTrace] = {}


def put_trace(trace: RationaleTrace) -> None:
    _traces[trace.lead_id] = trace


def get_trace(lead_id: str) -> RationaleTrace | None:
    return _traces.get(lead_id)


def clear_traces() -> None:
    _traces.clear()


def trace_count() -> int:
    return len(_traces)


# ---------- global event ring buffer ----------

EVENT_BUFFER_MAX = 500

_events: deque[Event] = deque(maxlen=EVENT_BUFFER_MAX)
_seq = 0


def emit(kind: EventKind, summary: str, **payload: Any) -> Event:
    global _seq
    _seq += 1
    ev = Event(seq=_seq, ts=time.time(), kind=kind, summary=summary, payload=payload)
    _events.append(ev)
    return ev


def events_since(since_seq: int = 0, limit: int = EVENT_BUFFER_MAX) -> list[Event]:
    out = [e for e in _events if e.seq > since_seq]
    return out[-limit:]


def clear_events() -> None:
    _events.clear()
