"""Extra rule tests for the heartbeat / booking_open logic (copied into tests/ by the patch step)."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import compute_slots as cs  # noqa: E402

TZ = cs.TZ
NOW = dt.datetime(2026, 11, 8, 18, 0, tzinfo=TZ)


def _ev(uid, start, end, status):
    return (f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Romance Standard Time:{start}\n"
            f"DTEND;TZID=Romance Standard Time:{end}\nSUMMARY:Free\nX-MICROSOFT-CDO-BUSYSTATUS:{status}\nEND:VEVENT\n")


def _ics(*events):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:test\n" + "".join(events) + "END:VCALENDAR\n").encode()


def test_fresh_heartbeat_opens_booking():
    ics = _ics(_ev("hb", "20261108T175000", "20261108T175100", "FREE"))     # 10 min old, 1 min long
    d = cs.compute(ics, NOW, require_heartbeat=True)
    assert d["system"]["booking_open"] is True
    assert 9.9 <= d["system"]["heartbeat_age_min"] <= 10.1


def test_old_or_missing_heartbeat_closes_booking():
    old = _ics(_ev("hb", "20261108T170000", "20261108T170100", "FREE"))     # 60 min old
    assert cs.compute(old, NOW, require_heartbeat=True)["system"]["booking_open"] is False
    assert cs.compute(_ics(), NOW, require_heartbeat=True)["system"]["booking_open"] is False
    assert cs.compute(_ics(), NOW, require_heartbeat=False)["system"]["booking_open"] is True


def test_other_free_events_are_not_heartbeats():
    ics = _ics(_ev("coffee", "20261108T175000", "20261108T180500", "FREE"),  # 15 min, not a heartbeat
               _ev("busy1", "20261108T175500", "20261108T175600", "BUSY"))   # 1 min but Busy
    d = cs.compute(ics, NOW, require_heartbeat=True)
    assert d["system"]["heartbeat_age_min"] is None and d["system"]["booking_open"] is False
