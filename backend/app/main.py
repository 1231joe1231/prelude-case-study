import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, SessionLocal, engine
from .ingest import ingest_all
from .routers import health, tables

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On every startup: drop and re-ingest from backend/input/*.csv
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        counts = ingest_all(session)
        log.info("startup ingest complete: %s", counts)
    finally:
        session.close()
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
