from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Competitor,
    CompetitorAttribute,
    Lead,
    LeadAttribute,
    Personnel,
    Shipment,
)

router = APIRouter(prefix="/tables", tags=["tables"])


def _to_dict(row: Any) -> dict:
    return {k: v for k, v in row.__dict__.items() if not k.startswith("_sa_")}


def _list(db: Session, model) -> list[dict]:
    return [_to_dict(r) for r in db.scalars(select(model)).all()]


@router.get("/leads")
def list_leads(db: Session = Depends(get_db)) -> list[dict]:
    return _list(db, Lead)


@router.get("/personnel")
def list_personnel(db: Session = Depends(get_db)) -> list[dict]:
    return _list(db, Personnel)


@router.get("/competitors")
def list_competitors(db: Session = Depends(get_db)) -> list[dict]:
    return _list(db, Competitor)


@router.get("/lead_attributes")
def list_lead_attributes(db: Session = Depends(get_db)) -> list[dict]:
    return _list(db, LeadAttribute)


@router.get("/competitor_attributes")
def list_competitor_attributes(db: Session = Depends(get_db)) -> list[dict]:
    return _list(db, CompetitorAttribute)


@router.get("/shipments")
def list_shipments(db: Session = Depends(get_db)) -> list[dict]:
    return _list(db, Shipment)
