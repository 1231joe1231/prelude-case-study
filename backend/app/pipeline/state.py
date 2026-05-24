"""Pipeline state machine + progress tracker.

Process-local singleton: the Pipeline page polls GET /api/pipeline/status to
render a live view of which stage the backend is in. Mutations happen from
the ingest path, the persona/features/scoring path, and the LLM cache
worker — all funnel through `set_stage()` / `tick_llm()` so the frontend
sees one consistent state.

Stages (linear, frontend renders them as steps):

  idle               nothing running
  ingesting          re-reading CSVs into SQLite (a few seconds)
  ingested           CSVs in DB; deterministic scoring is ready instantly
  llm_batching       generating rationales for top-N (progress = done / total)
  complete           cache fully populated; ready to serve
  error              any stage raised; check `error` field

Persona inference, feature extraction, and scoring are deliberately NOT
separate stages — they run inline in /api/leads/ranked GETs and take
sub-100ms on 121 leads. Surfacing them as visible steps would be theater.

`input_version` and `ingest_counts` are kept in the same state object so the
frontend has a single source of truth for what's loaded + what stage.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict, dataclass, field
from typing import Literal

# Honest stage set: only operations that take human-noticeable time are
# surfaced. Persona inference + feature extraction + scoring all run inline
# in /api/leads/ranked GETs (sub-100ms on 121 leads) so they're not separate
# stages — they're effectively free side-effects of reading SQLite.
Stage = Literal[
    "idle",          # process just started; no ingest yet
    "ingesting",     # re-reading CSVs into SQLite (a few seconds)
    "ingested",      # data loaded; deterministic scoring is ready instantly
    "llm_batching",  # generating rationales for top-N (the only real work)
    "complete",      # cache populated; ready to serve
    "error",         # check `error` field
]


@dataclass
class PipelineState:
    stage: Stage = "idle"
    input_version: str = "real"
    ingest_counts: dict[str, int] = field(default_factory=dict)
    persona_hs_codes: list[str] = field(default_factory=list)
    scored_count: int = 0           # leads that completed feature+score
    llm_total: int = 0              # batch size
    llm_done: int = 0               # completed in current batch
    llm_failed: int = 0             # rationales that fell back
    last_started_at: float | None = None
    last_finished_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_state = PipelineState()
_lock = threading.Lock()


def get_state() -> PipelineState:
    """Snapshot copy — safe to serialize without race with mutators."""
    with _lock:
        return PipelineState(**asdict(_state))


def set_stage(stage: Stage, **patch) -> None:
    """Atomically set the stage + any other fields. Pass error='...' to set
    stage='error' implicitly."""
    with _lock:
        _state.stage = stage
        for k, v in patch.items():
            setattr(_state, k, v)


def set_input_version(version: str) -> None:
    with _lock:
        _state.input_version = version


def set_ingest_counts(counts: dict[str, int]) -> None:
    with _lock:
        _state.ingest_counts = dict(counts)


def set_persona(hs_codes: list[str]) -> None:
    with _lock:
        _state.persona_hs_codes = list(hs_codes)


def set_scored_count(n: int) -> None:
    with _lock:
        _state.scored_count = n


def begin_llm_batch(total: int, started_at: float) -> None:
    with _lock:
        _state.stage = "llm_batching"
        _state.llm_total = total
        _state.llm_done = 0
        _state.llm_failed = 0
        _state.last_started_at = started_at
        _state.last_finished_at = None
        _state.error = None


def tick_llm(source: str) -> None:
    """One per completed lead. source ∈ {llm, fallback_*}."""
    with _lock:
        _state.llm_done += 1
        if not source.startswith("llm"):
            _state.llm_failed += 1


def finish(*, finished_at: float, error: str | None = None) -> None:
    with _lock:
        _state.stage = "error" if error else "complete"
        _state.last_finished_at = finished_at
        _state.error = error


def reset() -> None:
    """Called by the lifespan on startup, before any ingest, so state is
    deterministic across reloads."""
    global _state
    with _lock:
        _state = PipelineState()


# Async lock for serializing pipeline runs — never run two batches at once.
_run_lock = asyncio.Lock()


def run_lock() -> asyncio.Lock:
    return _run_lock
