#!/usr/bin/env python3
"""Compute the free BRUXMAP session slots from a published Outlook busy/free ICS feed.

Rules (identical to the Power Automate slot-finder verified on 2026-11-07, see
ethics/projects/bruxmap_healthtech_pilot/recruitment/booking_system.md):
  * campaign window CAMPAIGN_START..CAMPAIGN_END inclusive, every day of the week;
  * start times START_TIMES, Europe/Copenhagen, session length DURATION;
  * a slot is dropped when any calendar event whose X-MICROSOFT-CDO-BUSYSTATUS is BUSY
    overlaps it (tentative, free, out-of-office and working-elsewhere never block);
  * a slot is dropped when it starts less than LEAD before "now" (this also removes
    past days once the campaign is running).

Usage:
  ICS_URL=https://... python compute_slots.py [out.json]
  python compute_slots.py --ics path/to/calendar.ics [--now 2026-11-07T21:20] [out.json]

The output JSON holds only date-times: no titles, no participant data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

TZ = ZoneInfo("Europe/Copenhagen")
CAMPAIGN_START = dt.date(2026, 11, 9)
CAMPAIGN_END = dt.date(2026, 12, 5)          # inclusive
START_TIMES = [(9, 0), (12, 30), (15, 0)]
DURATION = dt.timedelta(minutes=150)
LEAD = dt.timedelta(hours=24)   # owner's rule 2026-11-08 (was 48 h)
BLOCKING_STATUS = {"BUSY"}


def load_ics(url: str | None, path: str | None) -> bytes:
    if path:
        with open(path, "rb") as fh:
            return fh.read()
    if not url:
        sys.exit("give --ics <file> or set ICS_URL")
    req = urllib.request.Request(url, headers={"User-Agent": "bruxmap-slots/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def _aware(value, end_of_all_day: bool = False) -> dt.datetime:
    """DTSTART/DTEND value -> aware datetime in TZ. All-day dates become local midnight."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ)
        return value.astimezone(TZ)
    # a date (all-day event): DTEND of an all-day event is already the next day in iCalendar
    return dt.datetime.combine(value, dt.time(0, 0), tzinfo=TZ)


def busy_intervals(ics_bytes: bytes, window_start: dt.datetime, window_end: dt.datetime):
    cal = icalendar.Calendar.from_ical(ics_bytes)
    out = []
    for ev in recurring_ical_events.of(cal).between(window_start, window_end):
        status = str(ev.get("X-MICROSOFT-CDO-BUSYSTATUS", "")).upper()
        if status not in BLOCKING_STATUS:
            continue
        start = _aware(ev.decoded("DTSTART"))
        if "DTEND" in ev:
            end = _aware(ev.decoded("DTEND"))
        elif "DURATION" in ev:
            end = start + ev.decoded("DURATION")
        else:
            end = start + dt.timedelta(days=1) if not isinstance(ev.decoded("DTSTART"), dt.datetime) else start
        out.append((start, end))
    return out


def compute(ics_bytes: bytes, now: dt.datetime) -> dict:
    window_start = dt.datetime.combine(CAMPAIGN_START, dt.time(0, 0), tzinfo=TZ)
    window_end = dt.datetime.combine(CAMPAIGN_END + dt.timedelta(days=1), dt.time(0, 0), tzinfo=TZ)
    busy = busy_intervals(ics_bytes, window_start, window_end)
    earliest = now + LEAD
    slots: list[str] = []
    day = CAMPAIGN_START
    while day <= CAMPAIGN_END:
        for hh, mm in START_TIMES:
            s = dt.datetime.combine(day, dt.time(hh, mm), tzinfo=TZ)
            e = s + DURATION
            if s < earliest:
                continue
            if any(b_s < e and b_e > s for b_s, b_e in busy):
                continue
            slots.append(s.strftime("%Y-%m-%dT%H:%M:%S"))
        day += dt.timedelta(days=1)
    by_day: dict[str, list[str]] = {}
    for s in slots:
        by_day.setdefault(s[:10], []).append(s[11:16])
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "timezone": "Europe/Copenhagen",
        "campaign": {"start": CAMPAIGN_START.isoformat(), "end": CAMPAIGN_END.isoformat()},
        "start_times": [f"{h:02d}:{m:02d}" for h, m in START_TIMES],
        "duration_minutes": int(DURATION.total_seconds() // 60),
        "lead_hours": int(LEAD.total_seconds() // 3600),
        "slots": slots,
        "by_day": by_day,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="public/slots.json")
    ap.add_argument("--ics", help="local .ics file instead of ICS_URL")
    ap.add_argument("--now", help="override 'now' (local Copenhagen time, e.g. 2026-11-07T21:20) for tests")
    a = ap.parse_args()
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else dt.datetime.now(TZ)
    data = compute(load_ics(os.environ.get("ICS_URL"), a.ics), now)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"{len(data['slots'])} free slots written to {a.out}")


if __name__ == "__main__":
    main()
