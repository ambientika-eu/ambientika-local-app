"""
FastAPI-Router des Free-Cooling-Wächters: /api/freecooling

  GET  /api/freecooling              Status: Konfiguration, Außenwert, Geräte
  GET  /api/freecooling/config       Konfiguration
  PUT  /api/freecooling/config       Konfiguration ändern (Teil-Update), wird gespeichert
  GET  /api/freecooling/log          Umschalt-Protokoll (neueste zuerst)
  GET  /api/freecooling/log.csv      Protokoll als CSV
  POST /api/freecooling/outdoor      Außenwert melden ({"temperature": 21.4, "humidity": 60,
                                     "kind": "sensor"|"weather", "source": "..."})
  POST /api/freecooling/evaluate     Bewertung sofort ausführen
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .store import log_to_csv


class ConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    devices: Optional[List[str]] = None
    t_in_min: Optional[float] = None
    t_out_min: Optional[float] = None
    delta_on: Optional[float] = None
    delta_off: Optional[float] = None
    min_hold_s: Optional[int] = None
    outdoor_ttl_s: Optional[int] = None
    cool_mode: Optional[str] = None
    fan_day: Optional[str] = None
    fan_night: Optional[str] = None
    night_hours: Optional[str] = None
    rest_mode: Optional[str] = None
    block_smart: Optional[bool] = None
    override_pause_s: Optional[int] = None
    confirm_grace_s: Optional[int] = None
    use_avg_temperature: Optional[bool] = None


class OutdoorInput(BaseModel):
    temperature: float
    humidity: Optional[float] = None
    kind: str = "sensor"
    source: Optional[str] = None


def make_freecooling_router(controller) -> APIRouter:
    router = APIRouter(prefix="/api/freecooling", tags=["Free Cooling"])

    @router.get("")
    async def get_status() -> Dict[str, Any]:
        return controller.status()

    @router.get("/config")
    async def get_config() -> Dict[str, Any]:
        return controller.config.as_dict()

    @router.put("/config")
    async def put_config(update: ConfigUpdate) -> Dict[str, Any]:
        data = {k: v for k, v in update.model_dump().items() if v is not None}
        try:
            cfg = controller.update_config(data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return cfg.as_dict()

    @router.get("/log")
    async def get_log(serial: Optional[str] = None,
                      start: Optional[str] = Query(None, description="ISO-8601 UTC, inklusive"),
                      end: Optional[str] = Query(None, description="ISO-8601 UTC, inklusive"),
                      limit: int = 200) -> List[Dict[str, Any]]:
        return controller.store.query_log(serial=serial, start=start, end=end, limit=limit)

    @router.get("/log.csv", response_class=PlainTextResponse)
    async def get_log_csv(serial: Optional[str] = None, start: Optional[str] = None,
                          end: Optional[str] = None):
        rows = controller.store.query_log(serial=serial, start=start, end=end,
                                          limit=None, newest_first=False)
        return PlainTextResponse(
            log_to_csv(rows), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=ambientika_freecooling.csv"},
        )

    @router.post("/outdoor")
    async def post_outdoor(inp: OutdoorInput) -> Dict[str, Any]:
        kind = (inp.kind or "sensor").strip().lower()
        if kind not in ("sensor", "weather"):
            raise HTTPException(status_code=400, detail="kind: sensor oder weather")
        r = controller.on_outdoor(kind, inp.temperature, inp.humidity,
                                  source=inp.source or f"http:{kind}")
        if r is None:
            raise HTTPException(status_code=400, detail="Außenwert nicht plausibel (-50..60 °C)")
        return {"status": "ok", "reading": r.as_dict(), "outdoor": controller.outdoor.as_dict()}

    @router.post("/evaluate")
    async def evaluate() -> Dict[str, Any]:
        events = controller.tick()
        return {"status": "ok", "events": events, "state": controller.status()}

    return router
