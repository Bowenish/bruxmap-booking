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
  SYNC_AFTER_SECONDS copies older than this are refreshed before answering, default 120
  HOLD_SECONDS       how long a slot is reserved while a visitor fills the form, default 600
  HOLD_PER_IP        concurrent holds allowed per client address, default 5
  HOLD_FORM_SECONDS  how long a slot stays reserved after the visitor reached the form, default 1800
  HOLD_MAX_SECONDS   no hold lives longer than this, renewals included, default 3600
  HOLDS_FILE         where holds survive a restart (default holds.json next to this file; "" = memory only)
  POLL_SECONDS       how often an open page re-reads the slots, default 10
  REQUIRE_HEARTBEAT  1 (default) = booking buttons only when the Power Automate heartbeat
                     (a 1-min Free event written every 15 min) is fresh; 0 = never lock

Run (behind nginx):  uvicorn server:app --host 127.0.0.1 --port 4010
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import socket
import threading
import time

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

import compute_slots as cs

try:  # MX lookup; falls back to an A/AAAA lookup when dnspython is missing
    import dns.resolver  # type: ignore
except Exception:  # pragma: no cover
    dns = None

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE / "site"
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "30"))
SYNC_AFTER_SECONDS = int(os.environ.get("SYNC_AFTER_SECONDS", "120"))   # older than this: refresh before answering
HOLD_SECONDS = int(os.environ.get("HOLD_SECONDS", "600"))        # a slot stays reserved this long while someone fills the form
HOLD_PER_IP = int(os.environ.get("HOLD_PER_IP", "5"))            # concurrent holds per client address (campus NAT: several visitors share one)
HOLD_FORM_SECONDS = int(os.environ.get("HOLD_FORM_SECONDS", "1800"))   # once the visitor reached the form: covers Flow B + the feed's publish lag
HOLD_MAX_SECONDS = int(os.environ.get("HOLD_MAX_SECONDS", "3600"))     # hard ceiling per hold, renewals included
HOLDS_FILE = os.environ.get("HOLDS_FILE", str(HERE / "holds.json"))    # holds survive pm2 restart / deploy; "" = memory only
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "10"))               # the page re-reads slots.json this often while visible
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
    """Return (data, cached). Fresh copy (< CACHE_SECONDS): served. Slightly stale copy
    (< SYNC_AFTER_SECONDS): served at once, refreshed in a background thread. Older than that
    (quiet period, nobody visited): refreshed synchronously first - bounded by the feed timeout -
    so the first visitor never sees hours-old slots or a false pause; the old copy is the fallback."""
    with _lock:
        data, age = _cache["data"], time.time() - _cache["t"]
        if data is not None and age < CACHE_SECONDS:
            return data, True
        if data is not None and age < SYNC_AFTER_SECONDS:
            if not _cache["refreshing"]:
                _cache["refreshing"] = True
                threading.Thread(target=_safe_refresh, daemon=True).start()
            return data, True
    try:
        _refresh()
    except Exception:
        with _lock:
            if _cache["data"] is None:
                raise
            return _cache["data"], True   # feed down: last good copy (healthz shows last_error)
    with _lock:
        return _cache["data"], False


def _safe_refresh() -> None:
    try:
        _refresh()
    except Exception:
        pass


# ---- slot holds: "someone is filling the form for this slot right now" -------------------------
# slot key -> {until, token, ip, created, stage}. The token is a random id the browser makes up; the
# address is kept only to cap holds per visitor; stage is "dialog" (typing name/email, renewed by the
# page every 90 s) or "form" (reached the Microsoft Form: longer tail so the slot stays hidden until
# Flow B's event shows up in the feed). Mirrored to HOLDS_FILE after every change so a restart keeps
# them. No names, no emails.
_holds: dict = {}
_holds_error: str | None = None
_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_LOCAL_IPS = {"", "127.0.0.1", "::1"}


def _save_holds() -> None:          # caller holds _lock; never raises (a full disk must not block bookings)
    global _holds_error
    if not HOLDS_FILE:
        return
    try:
        tmp = HOLDS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_holds, fh)
        os.replace(tmp, HOLDS_FILE)
        _holds_error = None
    except Exception as exc:
        _holds_error = f"{type(exc).__name__}: {exc}"[:200]


def _load_holds() -> int:
    """Restore the holds the previous process wrote (pm2 restart, deploy). Expired or malformed
    entries are dropped. Returns how many were restored."""
    global _holds_error
    if not HOLDS_FILE or not os.path.exists(HOLDS_FILE):
        return 0
    try:
        raw = json.loads(pathlib.Path(HOLDS_FILE).read_text(encoding="utf-8"))
    except Exception as exc:
        _holds_error = f"{type(exc).__name__}: {exc}"[:200]
        return 0
    now, n = time.time(), 0
    with _lock:
        for slot, v in (raw.items() if isinstance(raw, dict) else []):
            try:
                until = float(v["until"])
                if not _SLOT_RE.match(str(slot)) or not _TOKEN_RE.match(str(v["token"])) or until <= now:
                    continue
                _holds[str(slot)] = {"until": until, "token": str(v["token"]), "ip": str(v.get("ip", "")),
                                     "created": float(v.get("created", until - HOLD_SECONDS)),
                                     "stage": "form" if v.get("stage") == "form" else "dialog"}
                n += 1
            except Exception:
                continue
    return n


def _purge_holds(now: float | None = None) -> None:      # caller holds _lock
    now = time.time() if now is None else now
    for k in [k for k, v in _holds.items() if v["until"] <= now]:
        del _holds[k]


def _free_keys(data: dict | None) -> set:
    return {s[:10] + " " + s[11:16] for s in (data or {}).get("slots", [])}


def _held_slots(data: dict | None) -> list:
    """Slot keys currently held, limited to slots the feed still shows as free."""
    with _lock:
        _purge_holds()
        free = _free_keys(data)
        return sorted(k for k in _holds if k in free)


def hold_slot(slot, token, ip: str, data: dict | None, stage: str = "dialog") -> tuple[int, dict]:
    """Place, renew or upgrade a hold. Same token + same slot = renewal (the expiry moves, `created`
    does not). stage "form" = the visitor has reached the Microsoft Form: the slot then stays hidden
    for HOLD_FORM_SECONDS even if the tab is closed, long enough for Flow B's event to reach the
    feed. Nothing outlives HOLD_MAX_SECONDS."""
    slot, token = str(slot).strip(), str(token).strip()
    stage = "form" if stage == "form" else "dialog"
    if not _SLOT_RE.match(slot) or not _TOKEN_RE.match(token):
        return 400, {"ok": False, "reason": "invalid"}
    with _lock:
        _purge_holds()
        if slot not in _free_keys(data):
            return 409, {"ok": False, "reason": "taken"}
        cur = _holds.get(slot)
        if cur and cur["token"] != token:
            return 409, {"ok": False, "reason": "held", "until": cur["until"]}
        now = time.time()
        created = cur.get("created", now) if cur else now
        if now - created > HOLD_MAX_SECONDS:
            del _holds[slot]
            _save_holds()
            return 409, {"ok": False, "reason": "expired"}
        if ip not in _LOCAL_IPS and sum(1 for v in _holds.values() if v["ip"] == ip and v["token"] != token) >= HOLD_PER_IP:
            return 429, {"ok": False, "reason": "limit"}
        for k in [k for k, v in _holds.items() if v["token"] == token and k != slot]:
            del _holds[k]                      # one hold per visitor: a new slot releases the old one
        seconds = HOLD_FORM_SECONDS if stage == "form" else HOLD_SECONDS
        until = min(now + seconds, created + HOLD_MAX_SECONDS)
        _holds[slot] = {"until": until, "token": token, "ip": ip, "created": created, "stage": stage}
        _save_holds()
        return 200, {"ok": True, "until": until, "hold_seconds": seconds, "stage": stage}


def release_slot(slot, token) -> dict:
    slot, token = str(slot).strip(), str(token).strip()
    with _lock:
        cur = _holds.get(slot)
        if cur and cur["token"] == token:
            del _holds[slot]
            _save_holds()
            return {"ok": True, "released": True}
    return {"ok": True, "released": False}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()      # right-most entry = the one our own proxy appended
    return request.client.host if request.client else ""


@app.post("/hold")
def hold(request: Request, payload: dict = Body(...)) -> JSONResponse:
    try:
        data, _ = _fresh()
    except Exception:
        return JSONResponse({"ok": False, "reason": "unavailable"}, status_code=503, headers=_NO_STORE)
    status, body = hold_slot(payload.get("slot", ""), payload.get("token", ""), _client_ip(request), data, str(payload.get("stage", "dialog")))
    return JSONResponse(body, status_code=status, headers=_NO_STORE)


@app.post("/unhold")
def unhold(payload: dict = Body(...)) -> JSONResponse:
    return JSONResponse(release_slot(payload.get("slot", ""), payload.get("token", "")), headers=_NO_STORE)


@app.get("/slots.json")
def slots() -> JSONResponse:
    try:
        data, cached = _fresh()
    except Exception:
        return JSONResponse({"error": "calendar feed unavailable"}, status_code=503, headers=_NO_STORE)
    body = dict(data)
    body["held"] = _held_slots(data)
    body["hold_seconds"] = HOLD_SECONDS
    body["poll_seconds"] = POLL_SECONDS
    return JSONResponse(body, headers={**_NO_STORE, "X-Cache": "hit" if cached else "miss"})


_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_dom_cache: dict = {}


def _domain_accepts_mail(domain: str) -> tuple[bool, str]:
    """True when the domain has MX records (or at least resolves). Only the domain is looked up."""
    if dns is not None:
        try:
            if dns.resolver.resolve(domain, "MX", lifetime=4):
                return True, "ok"
        except dns.resolver.NXDOMAIN:
            return False, "no-mx"
        except Exception:  # NoAnswer, timeout, ...: fall back below
            pass
    try:
        socket.getaddrinfo(domain, 25)
        return True, "ok-a"
    except socket.gaierror:
        return False, "no-mx"
    except Exception:
        return True, "unknown"


@app.post("/check-domain")
def check_domain(payload: dict = Body(...)) -> JSONResponse:
    """Page helper: {"domain": "example.com"} -> {"ok": bool, "reason": ...}. Cached 10 min; the
    local part of the address never reaches the server."""
    domain = str(payload.get("domain", "")).strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(domain):
        return JSONResponse({"ok": False, "reason": "invalid"}, headers=_NO_STORE)
    hit = _dom_cache.get(domain)
    if hit and time.time() - hit[0] < 600:
        ok, reason = hit[1], hit[2]
    else:
        ok, reason = _domain_accepts_mail(domain)
        if len(_dom_cache) > 2000:
            _dom_cache.clear()
        _dom_cache[domain] = (time.time(), ok, reason)
    return JSONResponse({"ok": ok, "reason": reason}, headers=_NO_STORE)


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
        "holds": len(_held_slots(_cache["data"])),
        "holds_persisted": bool(HOLDS_FILE) and _holds_error is None,
        "holds_error": _holds_error,
        "cache_age_s": round(time.time() - _cache["t"]) if _cache["data"] else None,
        "refreshing": _cache["refreshing"],
        "stale": _cache["last_error"] is not None,
        "last_error": _cache["last_error"],
        "last_error_age_s": round(time.time() - _cache["last_error_t"]) if _cache["last_error_t"] else None,
    }


_load_holds()   # on start: take over the holds of the previous process (pm2 restart, deploy)
