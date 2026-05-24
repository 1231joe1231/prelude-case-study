import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load backend/.env before any module that reads env vars (rationale.py reads
# ANTHROPIC_API_KEY at module import to choose LLM vs fallback path).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .db import Base, SessionLocal, engine
from .ingest import ingest_all
from .ranking.cache import clear_cache as clear_rationale_cache
from .ranking.persona import clear_cache as clear_persona_cache
from .ranking.trace import clear_events, clear_traces, emit
from .routers import health, ranking, tables

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On every startup: drop and re-ingest from backend/input/*.csv
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    clear_events()
    session = SessionLocal()
    try:
        counts = ingest_all(session)
        clear_persona_cache()
        clear_rationale_cache()
        clear_traces()
        log.info("startup ingest complete: %s", counts)
        emit("ingest", f"Ingested {counts.get('leads', 0)} leads / {counts.get('personnel', 0)} personnel / {counts.get('competitors', 0)} competitors", **counts)
        emit("cache_cleared", "Rationale cache + traces cleared (post-ingest)")
    finally:
        session.close()
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if has_key:
        log.info("rationale: ANTHROPIC_API_KEY loaded — LLM path active")
    else:
        log.info("rationale: ANTHROPIC_API_KEY not set — deterministic fallback")
    emit(
        "ingest",
        f"Startup complete (LLM={'active' if has_key else 'disabled, fallback only'})",
        anthropic_key_present=has_key,
    )
    yield


app = FastAPI(title="Prelude Case Study API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(tables.router, prefix="/api")
app.include_router(ranking.router, prefix="/api")
app.include_router(ranking.ranking_router, prefix="/api")
