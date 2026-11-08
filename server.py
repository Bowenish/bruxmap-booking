#!/usr/bin/env python3
"""Live booking page server for the owner's VPS.

Serves `site/index.html` and `/slots.json`; the slot list is recomputed from the owner's
published busy-only ICS feed on request (cached CACHE_SECONDS, default 30; a stale copy is served at once and refreshed in the
background, so a visitor never waits for Exchange), so a participant always sees the
calendar as it is now. Same rules as compute_slots.py — this file only adds
the HTTP layer. Holds no participant data.

Environment:
  ICS_URL            secret busy-only ICS link from Outlook "Publish a calendar"  (required)
  ICS_FILE           local .ics instead of ICS_URL (tests)
  CACHE_SECONDS      cache lifetime, default 30
  REQUIRE_HEARTBEAT  1 (default) = booking buttons only when the Power Automate heartbeat
                     (a 1-min Free event written every 15 min) is fresh; 0 = never lock

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
REQUIRE_HEARTBEAT = os.environ.get("REQUIRE_HEARTBEAT", "1") == "1"   # fail closed unless told otherwise
VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip() if (HERE / "VERSION").exists() else "dev"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_lock = threading.Lock()           # guards the cache dict
_fetch_lock = threading.Lock()     # one feed fetch at a time
_cache: dict = {"t": 0.0, "data": None, "last_error": None, "last_error_t": None, "refreshing": False}
_NO_STORE = {"Cache-Control": "no-store"}


def _refresh() -> None:
    """Fetch the feed and recompute; on failure keep the last good copy and remember the error."""
    with _fetch_lock:
        with _lock:
            if _cache["data"] is not None and time.time() - _cache["t"] < CACHE_SECONDS:
                _cache["refreshing"] = False
                return  # someone else refreshed while we waited
        try:
            ics = cs.load_ics(os.environ.get("ICS_URL"), os.environ.get("ICS_FILE"))
            data = cs.compute(ics, dt.datetime.now(cs.TZ), require_heartbeat=REQUIRE_HEARTBEAT)
            data["system"]["version"] = VERSION
        except Exception as exc:
            with _lock:
                _cache["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
                _cache["last_error_t"] = time.time()
                _cache["refreshing"] = False
            raise
        with _lock:
            _cache["t"], _cache["data"], _cache["last_error"], _cache["refreshing"] = time.time(), data, None, False


def _fresh() -> tuple[dict, bool]:
    """Return (data, cached). A visitor never waits for the feed when any copy exists:
    stale copies are served at once and refreshed in a background thread."""
    with _lock:
        data, age = _cache["data"], time.time() - _cache["t"]
        if data is not None:
            if age >= CACHE_SECONDS and not _cache["refreshing"]:
                _cache["refreshing"] = True
                threading.Thread(target=_safe_refresh, daemon=True).start()
            return data, True
    _refresh()                       # first load after start: fetch synchronously
    with _lock:
        if _cache["data"] is None:
            raise RuntimeError("feed unavailable")
        return _cache["data"], False


def _safe_refresh() -> None:
    try:
        _refresh()
    except Exception:
        pass


@app.get("/slots.json")
def slots() -> JSONResponse:
    try:
        data, cached = _fresh()
    except Exception:
        return JSONResponse({"error": "calendar feed unavailable"}, status_code=503, headers=_NO_STORE)
    return JSONResponse(data, headers={**_NO_STORE, "X-Cache": "hit" if cached else "miss"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(SITE / "index.html", headers=_NO_STORE)


@app.get("/healthz")
def healthz() -> dict:
    """ok = the service answers; stale = the feed has failed since the last good read."""
    sysinfo = (_cache["data"] or {}).get("system", {})
    return {
        "ok": True,
        "version": VERSION,
        "booking_open": sysinfo.get("booking_open"),
        "heartbeat_age_min": sysinfo.get("heartbeat_age_min"),
        "heartbeat_required": REQUIRE_HEARTBEAT,
        "cache_age_s": round(time.time() - _cache["t"]) if _cache["data"] else None,
        "refreshing": _cache["refreshing"],
        "stale": _cache["last_error"] is not None,
        "last_error": _cache["last_error"],
        "last_error_age_s": round(time.time() - _cache["last_error_t"]) if _cache["last_error_t"] else None,
    }
