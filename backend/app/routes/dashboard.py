"""Dashboard endpoints: real lake measurements from the AI service.

None of these fall back to mock data. If the AI service is down they answer
503 and the frontend shows "Data unavailable".
"""
import ast
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import agent_client

router = APIRouter(tags=["dashboard"])


def _bbox(wb):
    return ast.literal_eval(wb.bbox_geojson) if wb.bbox_geojson else None


def _get_wb(db, waterbody_id):
    wb = db.query(models.Waterbody).filter(models.Waterbody.id == waterbody_id).first()
    if wb is None:
        raise HTTPException(404, f"Waterbody '{waterbody_id}' not found")
    return wb


def waterbody_index(db):
    """[(waterbody, bbox)] for point-in-box matching, smallest box first."""
    items = []
    for wb in db.query(models.Waterbody).all():
        bbox = _bbox(wb)
        if bbox and len(bbox) == 4:
            items.append((wb, bbox))
    items.sort(key=lambda i: (i[1][2] - i[1][0]) * (i[1][3] - i[1][1]))
    return items


def find_wb(index, lon, lat):
    for wb, b in index:
        if b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
            return wb
    return None


@router.get("/api/waterbodies/{waterbody_id}/series")
async def lake_series(waterbody_id: str, start: Optional[str] = None,
                      end: Optional[str] = None, db: Session = Depends(get_db)):
    wb = _get_wb(db, waterbody_id)
    data = await agent_client.call_real(
        "/agent/series", {"bbox": _bbox(wb), "from": start, "to": end})
    data["waterbody_id"] = waterbody_id
    return data


@router.get("/api/waterbodies/{waterbody_id}/seasonality")
async def lake_seasonality(waterbody_id: str, db: Session = Depends(get_db)):
    wb = _get_wb(db, waterbody_id)
    data = await agent_client.call_real("/agent/seasonality", {"bbox": _bbox(wb)})
    data["waterbody_id"] = waterbody_id
    return data


SLOW = 180.0   # seconds: SHAP and first-time archive downloads


@router.post("/api/waterbodies/{waterbody_id}/xai")
async def lake_xai(waterbody_id: str, payload: dict = None,
                   db: Session = Depends(get_db)):
    """payload (all optional): date, scene, class (water|vegetation|bare_land|
    built_up), methods (occlusion, gradcam, shap)."""
    wb = _get_wb(db, waterbody_id)
    payload = payload or {}
    data = await agent_client.call_real(
        "/agent/xai", {"bbox": _bbox(wb), "date": payload.get("date"),
                       "scene": payload.get("scene"), "class": payload.get("class"),
                       "methods": payload.get("methods")}, timeout=SLOW)
    data["waterbody_id"] = waterbody_id
    return data


@router.get("/api/waterbodies/{waterbody_id}/imagery")
async def lake_imagery(waterbody_id: str, db: Session = Depends(get_db)):
    """One real satellite image per year (which date, how much water)."""
    wb = _get_wb(db, waterbody_id)
    data = await agent_client.call_real("/agent/imagery", {"bbox": _bbox(wb)})
    data["waterbody_id"] = waterbody_id
    return data


@router.post("/api/waterbodies/{waterbody_id}/imagery/render")
async def lake_imagery_render(waterbody_id: str, payload: dict,
                              db: Session = Depends(get_db)):
    """Pictures + re-measurement for one image (payload: item_id)."""
    wb = _get_wb(db, waterbody_id)
    return await agent_client.call_real(
        "/agent/imagery/render", {"bbox": _bbox(wb), "item_id": payload.get("item_id")},
        timeout=SLOW)


@router.get("/api/waterbodies/{waterbody_id}/accuracy")
async def lake_accuracy(waterbody_id: str, db: Session = Depends(get_db)):
    wb = _get_wb(db, waterbody_id)
    data = await agent_client.call_real("/agent/accuracy", {"bbox": _bbox(wb)}, timeout=SLOW)
    data["waterbody_id"] = waterbody_id
    return data


@router.get("/api/waterbodies/{waterbody_id}/events")
async def lake_events(waterbody_id: str, db: Session = Depends(get_db)):
    """Changes in open water that the rain that fell cannot explain."""
    wb = _get_wb(db, waterbody_id)
    data = await agent_client.call_real("/agent/events", {"bbox": _bbox(wb)}, timeout=SLOW)
    data["waterbody_id"] = waterbody_id
    return data


@router.get("/api/events")
async def city_events(limit: Optional[int] = None, db: Session = Depends(get_db)):
    data = await agent_client.call_real("/agent/events", {"limit": limit}, timeout=SLOW)
    ids, index = {}, waterbody_index(db)
    for feature in (await agent_client.call_real("/agent/geojson", {}))["features"]:
        p = feature["properties"]
        wb = find_wb(index, p["lon"], p["lat"])
        if wb:
            ids[p["lake_id"]] = wb.id
    for e in data["events"]:
        e["waterbody_id"] = ids.get(e["lake_id"])
    return data


@router.get("/api/waterbodies/{waterbody_id}/landcover")
async def lake_landcover(waterbody_id: str, db: Session = Depends(get_db)):
    """Land cover in the lake and its shoreline buffers over the years (slow the first time)."""
    wb = _get_wb(db, waterbody_id)
    data = await agent_client.call_real("/agent/landcover_rings", {"bbox": _bbox(wb)}, timeout=600.0)
    data["waterbody_id"] = waterbody_id
    return data


@router.post("/api/chat")
async def chat(payload: dict, db: Session = Depends(get_db)):
    """Ask AquaRevive. Adds the website page to open for more detail."""
    data = await agent_client.call_real("/agent/chat", payload, timeout=SLOW)
    link = data.get("link")
    if link:
        geo = await agent_client.call_real("/agent/geojson", {})
        feature = next((f["properties"] for f in geo["features"]
                        if f["properties"]["lake_id"] == link["lake_id"]), None)
        wb = find_wb(waterbody_index(db), feature["lon"], feature["lat"]) if feature else None
        link["url"] = (f"/lake/{wb.id}" + (f"?tab={link['tab']}" if link.get("tab") else "")) if wb else None
    return data


@router.get("/api/accuracy")
async def accuracy_overall():
    return await agent_client.call_real("/agent/accuracy", {})


@router.get("/api/lookup")
async def lookup(lat: float, lon: float, radius_m: float = 1000,
                 db: Session = Depends(get_db)):
    data = await agent_client.call_real(
        "/agent/lookup", {"lat": lat, "lon": lon, "radius_m": radius_m})
    if data.get("found"):
        lake = data["lake"]
        b = lake["bbox"]
        wb = find_wb(waterbody_index(db), (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        data["waterbody_id"] = wb.id if wb else None
    return data


@router.get("/api/summary")
async def summary():
    return await agent_client.call_real("/agent/summary", {})


@router.get("/api/waterbodies.geojson")
async def lakes_geojson(db: Session = Depends(get_db)):
    data = await agent_client.call_real("/agent/geojson", {})
    index = waterbody_index(db)
    for feature in data["features"]:
        p = feature["properties"]
        wb = find_wb(index, p["lon"], p["lat"])
        p["id"] = wb.id if wb else None
        if wb and wb.name:
            p["name"] = p["name"] or wb.name
    return data


async def lake_info_by_wb(db):
    """waterbody id -> newest lake measurement (empty dict if AI is down)."""
    try:
        data = await agent_client.call_real("/agent/geojson", {})
    except HTTPException:
        return {}
    index = waterbody_index(db)
    info = {}
    for feature in data["features"]:
        p = feature["properties"]
        wb = find_wb(index, p["lon"], p["lat"])
        if wb is not None and wb.id not in info:
            info[wb.id] = p
    return info


def apply_info(item, p):
    """Fill the Bible 7.1 shape with real values where we have them."""
    if not p:
        return item
    item["latest_capture_date"] = p.get("date")
    item["change_type"] = p.get("status")
    item["open_water_ha"] = p.get("open_water_ha")
    item["outline_ha"] = p.get("outline_ha")
    item["deficit_fraction"] = p.get("deficit_fraction")
    return item
