"""AI service (port 8001). The backend calls these endpoints.

Run from the project root:
    uvicorn ai_agent.app.main:app --port 8001
Inputs it needs (made by the scripts): lake outlines, the lake time series,
and (optional) predictions_6band/*.tif. See lake_analytics.py for limits.
"""

import math

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import imagery
from . import lake_analytics as la

MODEL_VERSION = "lake-measure-v1 + unet6band-worldcover"
app = FastAPI(title="Satellite AI Service")
imagery.WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files/web", StaticFiles(directory=str(imagery.WEB_DIR)), name="web")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(payload):
    """Accept either bbox or lat/lon (+ radius_m, default 1000 m)."""
    payload = dict(payload or {})
    if not payload.get("bbox") and payload.get("lat") is not None \
            and payload.get("lon") is not None:
        lat, lon = float(payload["lat"]), float(payload["lon"])
        radius = float(payload.get("radius_m") or 1000)
        dlat = radius / 111_320.0
        dlon = radius / (111_320.0 * math.cos(math.radians(lat)))
        payload["bbox"] = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
    return payload


def _context(payload):
    """(lake, measurement row, date) for a request from the backend."""
    when = la.parse_date(payload.get("date"))
    lake = la.match_lake(payload.get("bbox"), payload.get("lake_id"))
    row = la.pick_measurement(lake["id"], when, 10_000) if lake else None
    return lake, row, when


@app.get("/health")
def health():
    info = la.status()
    return {"status": "running",
            "model_ready": bool(info["lakes_file"] and info["series_file"]),
            **info}


@app.post("/agent/segment")
def segment(payload: dict):
    payload = _norm(payload)
    lake, row, when = _context(payload)
    cover = la.landcover_percentages(payload.get("bbox"), when)
    if lake is None and cover is None:
        raise HTTPException(404, "No known lake in this bbox and no "
                                 "land-cover prediction for it.")
    close = la.pick_measurement(lake["id"], when) if lake else None
    return {
        "capture_id": payload.get("capture_id")
        or (f"{lake['id']}_{close['date']}" if close else "cap_none"),
        "class_percentages": cover["class_percentages"] if cover else None,
        "land_cover": cover,
        "lake": la.lake_summary(lake, close),
        "mask_path": None,
        "model_version": MODEL_VERSION,
        "note": "class_percentages come from the land-cover model "
                "(about 86% accurate on held-out WorldCover pixels, weaker "
                "for water precision and bare land); the lake block is the "
                "per-lake open-water measurement. Either can be null when "
                "no scene is near the requested date.",
    }


@app.post("/agent/anomaly")
def anomaly(payload: dict):
    payload = _norm(payload)
    lake, _, when = _context(payload)
    if lake is None:
        raise HTTPException(404, "No known lake in this bbox.")
    return {"lake_id": lake["id"], **la.anomaly(lake["id"], when)}


@app.post("/agent/changepoint")
def changepoint(payload: dict):
    payload = _norm(payload)
    lake = la.match_lake(payload.get("bbox"), payload.get("lake_id"))
    if lake is None:
        # the backend sends only waterbody_id here; fall back to a bbox if given
        raise HTTPException(404, "Send bbox or lake_id so the lake can be found.")
    return {"lake_id": lake["id"], **la.changepoint(lake["id"])}


@app.post("/agent/structures")
def structures(payload: dict):
    return {"new_structures": None,
            "note": "Not implemented: the model has no building/road class "
                    "and no building footprint data is loaded."}


@app.post("/agent/cause")
def cause(payload: dict):
    payload = _norm(payload)
    lake, row, _ = _context(payload)
    return la.cause(payload.get("segmentation"), payload.get("anomaly"),
                    la.lake_summary(lake, row))


@app.post("/agent/priority")
def priority(payload: dict):
    return la.priority(
        payload.get("severity", 0), payload.get("persistence", 50),
        payload.get("ecological_importance", 50),
        payload.get("population_impact", 50))


@app.post("/agent/xai")
def xai(payload: dict):
    payload = _norm(payload)
    lake, row, when = _context(payload)
    bbox = payload.get("bbox")
    out = {"lake_measurement": None, "model_explanation": None}
    if row is not None:
        out["lake_measurement"] = {
            "rule": "open water = MNDWI above the threshold AND near-infrared "
                    "below 0.20, counted inside the lake outline",
            "date": row["date"], "source": row["source"],
            "threshold": _num(row.get("threshold")),
            "threshold_method": row.get("threshold_method"),
            "usable_fraction": _num(row.get("valid_frac")),
            "open_water_ha": row["_water"],
            "image_provider_water_class_ha": _num(row.get("scl_water_ha")),
            "water_just_outside_outline_ha": _num(row.get("ring_water_ha")),
            "note": "Compare open_water_ha with the provider's own water "
                    "class; a big gap means check the quality picture.",
        }
    if bbox:
        from . import xai as xai_module
        methods = payload.get("methods") or ["occlusion", "gradcam"]
        try:
            out["available_scenes"] = xai_module.available_scenes()
            result = xai_module.explain(
                bbox, when, tuple(methods),
                lake_geom=lake["geom"] if lake else None,
                scene_name=payload.get("scene"),
                class_name=payload.get("class") or "water")
            out["model_explanation"] = result.get("occlusion")
            out["prediction"] = result.get("prediction")
            out["gradcam"] = result.get("gradcam")
            out["shap"] = result.get("shap")
            out["scene"] = result["scene"]
            out["scene_date"] = result["scene_date"]
            out["region"] = result["region"]
            out["class"] = result["class"]
        except xai_module.XaiUnavailable as error:
            out["model_explanation"] = {"unavailable": str(error)}
        except ImportError as error:
            out["model_explanation"] = {"unavailable": str(error)}
    if out["lake_measurement"] is None and out["model_explanation"] is None:
        raise HTTPException(404, "Nothing to explain: no lake measurement "
                                 "and no bbox.")
    return out


# ------------------------------------------------------- imagery / accuracy
@app.post("/agent/imagery")
def imagery_years(payload: dict):
    """One representative real image per year (clearest pre-monsoon date)."""
    lake, _ = _lake_or_404(payload)
    return {"lake_id": lake["id"], "name": lake["name"],
            "outline_ha": lake["area_ha"], "years": imagery.year_picks(lake["id"])}


@app.post("/agent/imagery/render")
def imagery_render(payload: dict):
    """Fetch one image (cached after the first time) and re-measure the lake on it."""
    lake, payload = _lake_or_404(payload)
    if not payload.get("item_id"):
        raise HTTPException(422, "item_id is required (from /agent/imagery).")
    try:
        return imagery.render(lake["id"], payload["item_id"])
    except imagery.ImageryError as error:
        raise HTTPException(424, str(error))


@app.post("/agent/accuracy")
def accuracy_view(payload: dict = None):
    """Model vs WorldCover and measurement vs independent products;
    per lake when a bbox or lake_id is given."""
    from . import accuracy
    payload = _norm(payload)
    lake = None
    if payload.get("bbox") or payload.get("lake_id"):
        lake, payload = _lake_or_404(payload)
    return accuracy.accuracy(lake)


@app.post("/agent/events")
def change_events(payload: dict = None):
    """Rain-inconsistent change events: one lake (bbox / lake_id) or the whole city."""
    from . import events, rainfall
    payload = _norm(payload)
    try:
        if payload.get("bbox") or payload.get("lake_id"):
            lake, _ = _lake_or_404(payload)
            return events.lake_events(lake)
        return events.city_events(limit=payload.get("limit"))
    except rainfall.RainfallUnavailable as error:
        raise HTTPException(424, str(error))


@app.post("/agent/chat")
def chat_view(payload: dict):
    """Ask AquaRevive: payload = {message, context: {lake_ids, topic}, history: [{role, content}]}."""
    from . import chat
    return chat.answer(payload.get("message"), payload.get("context"), payload.get("history"))


# ------------------------------------------------------------------ dashboard
def _lake_or_404(payload):
    payload = _norm(payload)
    lake = la.match_lake(payload.get("bbox"), payload.get("lake_id"))
    if lake is None:
        raise HTTPException(404, "No known lake for this bbox or lake_id.")
    return lake, payload


@app.post("/agent/series")
def series(payload: dict):
    lake, payload = _lake_or_404(payload)
    return la.series(lake["id"], la.parse_date(payload.get("from")),
                     la.parse_date(payload.get("to")))


@app.post("/agent/seasonality")
def seasonality(payload: dict):
    lake, _ = _lake_or_404(payload)
    return la.seasonality(lake["id"])


@app.post("/agent/summary")
def summary(payload: dict = None):
    # live status is read fresh each time; the rest is cached until the CSV changes
    return {**la.summary(), "live": la.live_status()}


@app.post("/agent/geojson")
def geojson(payload: dict = None):
    return la.geojson()


@app.post("/agent/rank")
def rank(payload: dict = None):
    return la.rank()


@app.post("/agent/lookup")
def lookup(payload: dict):
    if payload.get("lat") is None or payload.get("lon") is None:
        raise HTTPException(422, "lat and lon are required.")
    radius = float(payload.get("radius_m") or 1000)
    lake, distance_km = la.nearest_lake(float(payload["lat"]),
                                        float(payload["lon"]))
    if lake is None:
        return {"found": False, "note": "No lake within 5 km."}
    inside = distance_km * 1000 <= radius
    return {"found": True, "within_radius": inside,
            "distance_m": round(distance_km * 1000),
            "lake": lake_status_view(lake)}


def lake_status_view(lake):
    return {"lake_id": lake["id"], "name": lake["name"],
            "outline_ha": lake["area_ha"],
            "bbox": [round(v, 5) for v in lake["geom"].bounds],
            **la.lake_status(lake)}
