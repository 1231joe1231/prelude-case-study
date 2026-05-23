from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Competitor, Lead, Personnel

router = APIRouter(prefix="/tables", tags=["tables"])


def _to_dict(row: Any) -> dict:
    d = {k: v for k, v in row.__dict__.items() if not k.startswith("_sa_")}
    return d


@router.get("/leads")
def list_leads(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Lead)).all()
    return [_to_dict(r) for r in rows]


@router.get("/personnel")
def list_personnel(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Personnel)).all()
    return [_to_dict(r) for r in rows]


@router.get("/competitors")
def list_competitors(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Competitor)).all()
    return [_to_dict(r) for r in rows]
