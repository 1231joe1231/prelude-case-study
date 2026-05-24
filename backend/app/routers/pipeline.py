"""Pipeline orchestration endpoints.

  GET  /api/pipeline/status              live state + LLM progress
  POST /api/pipeline/switch_input        body: {version}  → re-ingest from that subdir
  POST /api/pipeline/run                 body: {limit}    → score + LLM-rationalize top-N
  POST /api/pipeline/reset_cache         clear rationale cache + traces

The frontend's Pipeline page is the only UI that triggers /run. Default
behavior is: backend starts → ingests → idle. The LLM never runs until the
operator clicks the button.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import Base, SessionLocal, engine, get_db
from ..ingest import ingest_all
from ..pipeline import selection, state as pstate
from ..ranking.cache import clear_cache as clear_rationale_cache, get_or_kick
from ..ranking.features import extract_all
from ..ranking.persona import clear_cache as clear_persona_cache, get_persona
from ..ranking.score import score_lead
from ..ranking.trace import clear_events, clear_traces, emit

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

VALID_VERSIONS = {"real", "golden"}


@router.get("/status")
def get_status() -> dict:
    return pstate.get_state().to_dict()


class SwitchInputBody(BaseModel):
    version: Literal["real", "golden"]


@router.post("/switch_input")
async def switch_input(body: SwitchInputBody, background: BackgroundTasks) -> dict:
    """Re-ingest from backend/input/{version}/. Drops DB, recreates schema,
    runs ingest_all in a background task so the request returns immediately
    and the frontend can poll /status."""
    if body.version not in VALID_VERSIONS:
        raise HTTPException(400, f"version must be one of {sorted(VALID_VERSIONS)}")

    async with pstate.run_lock():
        if pstate.get_state().stage in {"ingesting", "llm_batching"}:
            raise HTTPException(409, "pipeline busy; wait for it to complete")

    background.add_task(_reingest, body.version)
    return {"started": True, "version": body.version}


def _reingest(version: str) -> None:
    """Sync reingest worker. Runs in BackgroundTasks (own thread)."""
    os.environ["INPUT_VERSION"] = version
    pstate.set_input_version(version)
    pstate.set_stage("ingesting")
    try:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        clear_persona_cache()
        clear_rationale_cache()
        clear_traces()
        selection.clear()  # operator selections are tied to the previous dataset
        # Don't clear events — keep history across switches so the Trace tab
        # shows the switch event too.
        session = SessionLocal()
        try:
            counts = ingest_all(session)
        finally:
            session.close()
        pstate.set_ingest_counts(counts)
        emit("ingest", f"Switched to {version!r}: {counts.get('leads', 0)} leads / "
             f"{counts.get('personnel', 0)} personnel / {counts.get('competitors', 0)} competitors",
             version=version, **counts)
        pstate.set_stage("ingested")
    except Exception as e:
        emit("ingest", f"Re-ingest FAILED on version={version!r}",
             error=f"{type(e).__name__}: {e}")
        pstate.set_stage("error", error=f"{type(e).__name__}: {e}")
        raise


class RunBody(BaseModel):
    limit: int = Field(50, ge=1, le=500)


@router.post("/run")
async def run_pipeline(body: RunBody) -> dict:
    """Trigger the full ranking pipeline: persona → features → score → LLM batch
    over the top-N leads. Returns immediately; poll /status for progress.

    Refuses to start if another run is already in flight.
    """
    if pstate.run_lock().locked():
        raise HTTPException(409, "pipeline already running")

    asyncio.create_task(_run_pipeline_async(body.limit))
    return {"started": True, "limit": body.limit}


async def _run_pipeline_async(limit: int) -> None:
    """Compute top-N then fan out LLM rationales. Persona + feature extraction
    + scoring run inline (sub-100ms on 121 leads) so they don't get their own
    stage — they're effectively part of preparing the batch. The only stage
    the operator notices is `llm_batching`."""
    async with pstate.run_lock():
        started = time.time()
        emit("ranking_request", f"Pipeline run kicked off (limit={limit})",
             limit=limit, source="pipeline_endpoint")
        try:
            session = SessionLocal()
            try:
                persona = get_persona(session)
                all_features = extract_all(session, persona)
                scored = [(f, *score_lead(f)) for f in all_features]
                scored.sort(key=lambda x: x[1], reverse=True)
                pstate.set_scored_count(len(scored))
                top = scored[:limit]
            finally:
                session.close()

            # begin_llm_batch flips stage → 'llm_batching' and resets counters.
            pstate.begin_llm_batch(total=len(top), started_at=started)
            # get_or_kick seeds pending placeholders + spawns the background
            # batch worker. cache._run_batch ticks pstate per completed lead
            # and calls pstate.finish() at the end.
            await get_or_kick([(f, c) for f, _, c in top])
            # If no items needed running (all already cached), _run_batch never
            # fires — mark complete here so frontend stops polling.
            if pstate.get_state().llm_total == 0:
                pstate.finish(finished_at=time.time())
        except Exception as e:
            pstate.finish(finished_at=time.time(), error=f"{type(e).__name__}: {e}")
            raise


@router.post("/reset_cache")
async def reset_cache() -> dict:
    """Clear rationale cache + traces. Next /run regenerates everything."""
    clear_rationale_cache()
    clear_traces()
    pstate.set_stage("ingested" if pstate.get_state().ingest_counts else "idle",
                     llm_total=0, llm_done=0, llm_failed=0,
                     last_started_at=None, last_finished_at=None, error=None)
    emit("cache_cleared", "Rationale cache + traces cleared by /pipeline/reset_cache")
    return {"ok": True}
