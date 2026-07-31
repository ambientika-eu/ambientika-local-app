"""
FastAPI-Router fuer Historie: Abfrage + CSV/JSON-Export ueber die Oberflaeche.

Einbindung in backend/main.py:

    from history.routes import make_history_router
    from history.sampler import HistorySampler

    sampler = HistorySampler(get_devices=lambda: devices,
                             mqtt_client=mqtt_client, mqtt_prefix=MQTT_PREFIX)
    app.include_router(make_history_router(sampler))

    # in der lifespan:  sampler.start()  /  await sampler.stop()
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse, JSONResponse

from . import exporter


def make_history_router(sampler) -> "APIRouter":
    router = APIRouter(prefix="/api/history", tags=["History"])
    store = sampler.store

    @router.get("")
    async def query_history(
        serial: Optional[str] = None,
        start: Optional[str] = Query(None, description="ISO-8601 UTC, inklusive"),
        end: Optional[str] = Query(None, description="ISO-8601 UTC, inklusive"),
        limit: int = 1000,
    ):
        return store.query(serial=serial, start=start, end=end, limit=limit)

    @router.get("/export.csv", response_class=PlainTextResponse)
    async def export_csv(serial: Optional[str] = None,
                         start: Optional[str] = None, end: Optional[str] = None):
        csv_text = exporter.export(store, "csv", serial=serial, start=start, end=end)
        return PlainTextResponse(
            csv_text, media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=ambientika_history.csv"},
        )

    @router.get("/export.json")
    async def export_json(serial: Optional[str] = None,
                          start: Optional[str] = None, end: Optional[str] = None):
        rows = store.query(serial=serial, start=start, end=end)
        return JSONResponse(rows)

    @router.get("/config")
    async def get_config():
        c = sampler.config
        return {
            "interval_seconds": c.interval_seconds,
            "retention_days": c.retention_days,
            "mqtt_publish": c.mqtt_publish,
            "rows": store.count(),
        }

    @router.post("/sample-now")
    async def sample_now():
        n = sampler.sample_once()
        return {"status": "ok", "recorded": n}

    return router
