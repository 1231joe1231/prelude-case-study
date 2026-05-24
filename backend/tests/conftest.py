"""Eval fixtures: rebuild the SQLite DB from backend/input/golden/ before tests.

Tests run against the hand-designed 10-lead golden set (see
backend/scripts/build_golden.py). Each scenario exercises a specific path in
the ranking pipeline so failures point at concrete bugs.

INPUT_VERSION is set in-process before importing the app modules so
`get_input_dir()` resolves to backend/input/golden/. A separate temp SQLite
file is used so test runs don't clobber the dev DB.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Set BEFORE importing any app module that reads env at import time.
os.environ["INPUT_VERSION"] = "golden"
# Redirect SQLite to a test-scoped path so the dev DB is left alone.
TEST_DB_PATH = Path(__file__).resolve().parent / "_test.db"
os.environ["TEST_DB_PATH"] = str(TEST_DB_PATH)


@pytest.fixture(scope="session", autouse=True)
def _build_golden_db():
    """Drop + create + ingest the golden CSVs into a clean test DB."""
    # Late imports — env vars must be set first
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import db as db_module
    from app.db import Base
    from app.ingest import ingest_all
    from app.ranking.persona import clear_cache as clear_persona

    # Point the app's engine at the test DB
    test_engine = create_engine(
        f"sqlite:///{TEST_DB_PATH}",
        connect_args={"check_same_thread": False},
    )
    test_session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Monkey-patch the module-level engine + SessionLocal so all code paths
    # that import them see the test DB.
    db_module.engine = test_engine
    db_module.SessionLocal = test_session

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    s = test_session()
    try:
        ingest_all(s)
        clear_persona()
    finally:
        s.close()

    yield

    # Cleanup
    test_engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture
def db_session():
    """Per-test SQLAlchemy session against the golden DB."""
    from app.db import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
