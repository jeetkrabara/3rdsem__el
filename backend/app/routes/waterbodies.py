import ast
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/waterbodies", tags=["waterbodies"])


def waterbody_to_dict(wb: models.Waterbody) -> dict:
    """Matches the Bible v2 Chapter 7.1 response shape."""
    return {
        "id": wb.id,
        "name": wb.name,
        "bbox": ast.literal_eval(wb.bbox_geojson) if wb.bbox_geojson else None,
        "ward": wb.ward,
        "latest_capture_date": None,
        "scores": {
            "water_health": None,
            "vegetation_health": None,
            "degradation_risk": None,
            "restoration_urgency": None,
            "data_reliability": None,
            "recovery_trend": None,
        },
        "segmentation": {
            "water_pct": None,
            "vegetation_pct": None,
            "bare_land_pct": None,
            "built_up_pct": None,
        },
        "change_point": None,
        "new_structures_since_baseline": None,
        "change_type": None,
        "confidence": None,
    }


@router.get("")
async def list_waterbodies(db: Session = Depends(get_db)):
    from .dashboard import apply_info, lake_info_by_wb
    waterbodies = db.query(models.Waterbody).all()
    info = await lake_info_by_wb(db)
    return [apply_info(waterbody_to_dict(wb), info.get(wb.id)) for wb in waterbodies]


@router.post("")
def create_waterbody(payload: schemas.WaterbodyCreate, db: Session = Depends(get_db)):
    wb_id = f"wb_{uuid.uuid4().hex[:8]}"
    wb = models.Waterbody(
        id=wb_id,
        name=payload.name,
        bbox_geojson=str(payload.bbox),
        ward=payload.ward,
        region=payload.region,
    )
    db.add(wb)
    db.commit()
    db.refresh(wb)
    return waterbody_to_dict(wb)


@router.get("/{waterbody_id}")
async def get_waterbody(waterbody_id: str, db: Session = Depends(get_db)):
    from .dashboard import apply_info, lake_info_by_wb
    wb = db.query(models.Waterbody).filter(models.Waterbody.id == waterbody_id).first()
    if wb is None:
        raise HTTPException(status_code=404, detail=f"Waterbody '{waterbody_id}' not found")
    info = await lake_info_by_wb(db)
    return apply_info(waterbody_to_dict(wb), info.get(wb.id))


@router.get("/{waterbody_id}/timeline")
def get_waterbody_timeline(waterbody_id: str, db: Session = Depends(get_db)):
    wb = db.query(models.Waterbody).filter(models.Waterbody.id == waterbody_id).first()
    if wb is None:
        raise HTTPException(status_code=404, detail=f"Waterbody '{waterbody_id}' not found")

    captures = (
        db.query(models.ImageryCapture)
        .filter(models.ImageryCapture.waterbody_id == waterbody_id)
        .order_by(models.ImageryCapture.date)
        .all()
    )
    change_point = (
        db.query(models.ChangePoint)
        .filter(models.ChangePoint.waterbody_id == waterbody_id)
        .first()
    )

    return {
        "waterbody_id": waterbody_id,
        "captures": [
            {
                "capture_id": c.id,
                "date": c.date.isoformat() if c.date else None,
                "season": c.season,
                "cloud_cover_pct": c.cloud_cover_pct,
            }
            for c in captures
        ],
        "change_point": (
            {
                "year": change_point.change_point_year,
                "before_mean": change_point.before_mean,
                "after_mean": change_point.after_mean,
                "narrative": change_point.narrative,
            }
            if change_point
            else None
        ),
    }


@router.post("/{waterbody_id}/analyze")
async def analyze_waterbody(waterbody_id: str, payload: dict = None, db: Session = Depends(get_db)):
    """
    Real analysis of this lake from the AI service (no mock data): land cover,
    seasonal anomaly, change point, cause hypotheses and the live priority
    rank. 'date' (optional) picks the measurement nearest to it.
    """
    from ..services import agent_client

    wb = db.query(models.Waterbody).filter(models.Waterbody.id == waterbody_id).first()
    if wb is None:
        raise HTTPException(status_code=404, detail=f"Waterbody '{waterbody_id}' not found")

    date = (payload or {}).get("date")
    bbox = ast.literal_eval(wb.bbox_geojson)  # a plain list; never eval()
    base = {"waterbody_id": waterbody_id, "bbox": bbox, "date": date}

    seg_result = await agent_client.call_real("/agent/segment", base)
    anomaly_result = await agent_client.call_real("/agent/anomaly", base)
    changepoint_result = await agent_client.call_real("/agent/changepoint", base)
    cause_result = await agent_client.call_real("/agent/cause", {
        **base, "segmentation": seg_result.get("class_percentages"),
        "anomaly": anomaly_result,
    })
    ranked = await agent_client.call_real("/agent/rank", {})
    lake_id = anomaly_result.get("lake_id")
    rank_item = next((i for i in ranked["items"] if i["lake_id"] == lake_id), None)

    return {
        "waterbody_id": waterbody_id,
        "name": wb.name,
        "bbox": bbox,
        "lake_id": lake_id,
        "segmentation": seg_result,
        "anomaly": anomaly_result,
        "change_point": changepoint_result,
        "cause_hypotheses": cause_result,
        "priority": rank_item,
        "lakes_ranked": sum(1 for i in ranked["items"]
                            if i.get("basis", "water") == (rank_item or {}).get("basis", "water")),
        "priority_note": ranked.get("land_note") if (rank_item or {}).get("basis") == "land"
                         else ranked.get("note"),
    }
