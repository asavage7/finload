"""Routes for the stopgap analysis-data export/import (see
services/feature_transfer.py). Delete this file and its one line in main.py
to remove the feature.

! Caution: This file is mostly AI Generated.
It will likely be removed soon anyway.
"""
from fastapi import APIRouter, Body, HTTPException

from core import state
from services.feature_transfer import export_features, import_features

router = APIRouter()


@router.post("/api/features/export")
def export_track_features(data: dict = Body(...)):
    path = (data.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        count = export_features(path)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"exported": count}


@router.post("/api/features/import")
def import_track_features(data: dict = Body(...)):
    path = (data.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        result = import_features(path, state.db)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
