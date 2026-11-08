"""Rule tests for compute_slots.compute() on a synthetic busy/free feed.

Run from this folder:  python -m pytest -q
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import compute_slots as cs  # noqa: E402

TZ = cs.TZ


def _event(uid, start, end, status, allday=False):
    if allday:
        return (
            f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;VALUE=DATE:{start}\nDTEND;VALUE=DATE:{end}\n"
            f"SUMMARY:x\nX-MICROSOFT-CDO-BUSYSTATUS:{status}\nX-MICROSOFT-CDO-ALLDAYEVENT:TRUE\nEND:VEVENT\n"
        )
    return (
        f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Romance Standard Time:{start}\n"
        f"DTEND;TZID=Romance Standard Time:{end}\nSUMMARY:x\nX-MICROSOFT-CDO-BUSYSTATUS:{status}\nEND:VEVENT\n"
    )


def _ics(*events):
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:test\n" + "".join(events) + "END:VCALENDAR\n"
    ).encode()


NOW = dt.datetime(2026, 11, 7, 21, 20, tzinfo=TZ)


def test_busy_blocks_and_free_tentative_do_not():
    ics = _ics(
        _event("a", "20261110T133000", "20261110T153000", "BUSY"),       # Tue 10: kills 12:30 and 15:00
        _event("b", "20261111T103000", "20261111T104500", "FREE"),       # Wed 11: Free never blocks
        _event("c", "20261112T123000", "20261112T133000", "TENTATIVE"),  # Thu 12: tentative never blocks
    )
    slots = cs.compute(ics, NOW)["slots"]
    assert "2026-11-10T09:00:00" in slots
    assert "2026-11-10T12:30:00" not in slots and "2026-11-10T15:00:00" not in slots
    assert all(f"2026-11-11T{t}:00" in slots for t in ("09:00", "12:30", "15:00"))
    assert "2026-11-12T12:30:00" in slots


def test_lead_time_and_window():
    slots = cs.compute(_ics(), NOW)["slots"]
    late = cs.compute(_ics(), dt.datetime(2026, 11, 8, 10, 0, tzinfo=TZ))["slots"]
    assert late[0] == "2026-11-09T12:30:00"           # 9 Nov 09:00 is < 24 h from 8 Nov 10:00
    assert slots[0] == "2026-11-09T09:00:00"          # NOW + 24 h = 8 Nov 21:20, so 9 Nov is open
    assert slots[-1] == "2026-12-05T15:00:00"         # last campaign day, every day incl. weekends
    assert len(slots) == 27 * 3                       # 9 Nov .. 5 Dec = 27 days x 3


def test_all_day_busy_removes_whole_day_and_boundary_is_exclusive():
    ics = _ics(
        _event("d", "20261114", "20261115", "BUSY", allday=True),        # Sat 14 all-day
        _event("e", "20261127T090000", "20261127T150000", "BUSY"),       # ends exactly 15:00
    )
    slots = cs.compute(ics, NOW)["slots"]
    assert not any(s.startswith("2026-11-14") for s in slots)
    assert "2026-11-15T09:00:00" in slots                                # next day untouched
    assert "2026-11-27T15:00:00" in slots and "2026-11-27T12:30:00" not in slots


def test_recurring_weekly_event_expands():
    ics = _ics(
        "BEGIN:VEVENT\nUID:r\nDTSTART;TZID=Romance Standard Time:20261105T150000\n"
        "DTEND;TZID=Romance Standard Time:20261105T160000\nRRULE:FREQ=WEEKLY;BYDAY=TH\n"
        "SUMMARY:x\nX-MICROSOFT-CDO-BUSYSTATUS:BUSY\nEND:VEVENT\n"
    )
    slots = cs.compute(ics, NOW)["slots"]
    for day in ("12", "19", "26"):
        assert f"2026-11-{day}T15:00:00" not in slots
        assert f"2026-11-{day}T12:30:00" in slots
