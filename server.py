#!/usr/bin/env python3
"""Live booking page server for the owner's VPS.

Serves `site/index.html` and `/slots.json`; the slot list is recomputed from the owner's
published busy-only ICS feed on request (cached CACHE_SECONDS, default 30), so a participant
always sees the calendar as it is now. Same rules as compute_slots.py — this file only adds
the HTTP layer. Holds no participant data.

Environment:
  ICS_URL        secret busy-only ICS link from Outlook "Publish a calendar"  (required)
  ICS_FILE       local .ics instead of ICS_URL (tests)
  CACHE_SECONDS  cache lifetime, default 30

Run (behind nginx):  uvicorn server:app --host 127.0.0.1 --port 4010
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import threading
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

import compute_slots as cs

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE / "site"
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "30"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_lock = threading.Lock()          # held for the whole refresh: concurrent visitors share one fetch
_cache: dict = {"t": 0.0, "data": None, "last_error": None, "last_error_t": None}
_NO_STORE = {"Cache-Control": "no-store"}


def _fresh() -> tuple[dict, bool]:
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["t"] < CACHE_SECONDS:
            return _cache["data"], True
        try:
            ics = cs.load_ics(os.environ.get("ICS_URL"), os.environ.get("ICS_FILE"))
            data = cs.compute(ics, dt.datetime.now(cs.TZ))
        except Exception as exc:  # feed unreachable or unparsable: remember it, keep the last good copy
            _cache["last_error"], _cache["last_error_t"] = f"{type(exc).__name__}: {exc}"[:200], time.time()
            raise
        _cache["t"], _cache["data"], _cache["last_error"] = time.time(), data, None
        return data, False


@app.get("/slots.json")
def slots() -> JSONResponse:
    try:
        data, cached = _fresh()
    except Exception:
        if _cache["data"] is None:
            return JSONResponse({"error": "calendar feed unavailable"}, status_code=503, headers=_NO_STORE)
        data, cached = _cache["data"], True
    return JSONResponse(data, headers={**_NO_STORE, "X-Cache": "hit" if cached else "miss"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(SITE / "index.html", headers=_NO_STORE)


@app.get("/healthz")
def healthz() -> dict:
    """ok = the service answers; stale = the feed has failed since the last good read."""
    return {
        "ok": True,
        "cache_age_s": round(time.time() - _cache["t"]) if _cache["data"] else None,
        "stale": _cache["last_error"] is not None,
        "last_error": _cache["last_error"],
        "last_error_age_s": round(time.time() - _cache["last_error_t"]) if _cache["last_error_t"] else None,
    }
