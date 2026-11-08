"""Slot holds: one visitor at a time per slot, one slot at a time per visitor, no personal data."""
import time

import server

DATA = {"slots": ["2026-11-10T09:00:00", "2026-11-10T12:30:00", "2026-11-11T09:00:00"]}
A, B = "tokenAAAA1", "tokenBBBB2"


def setup_function():
    server._holds.clear()


def test_hold_then_second_visitor_is_refused():
    st, body = server.hold_slot("2026-11-10 09:00", A, "1.1.1.1", DATA)
    assert st == 200 and body["ok"]
    st, body = server.hold_slot("2026-11-10 09:00", B, "2.2.2.2", DATA)
    assert st == 409 and body["reason"] == "held"
    assert server._held_slots(DATA) == ["2026-11-10 09:00"]


def test_same_visitor_may_rehold_and_moving_releases_the_old_slot():
    assert server.hold_slot("2026-11-10 09:00", A, "1.1.1.1", DATA)[0] == 200
    assert server.hold_slot("2026-11-10 09:00", A, "1.1.1.1", DATA)[0] == 200      # reload, same token
    assert server.hold_slot("2026-11-10 12:30", A, "1.1.1.1", DATA)[0] == 200      # clicked another slot
    assert server._held_slots(DATA) == ["2026-11-10 12:30"]                        # the first one is free again
    assert server.hold_slot("2026-11-10 09:00", B, "2.2.2.2", DATA)[0] == 200


def test_release_needs_the_same_token_and_holds_expire():
    server.hold_slot("2026-11-10 09:00", A, "1.1.1.1", DATA)
    assert server.release_slot("2026-11-10 09:00", B)["released"] is False
    assert server.release_slot("2026-11-10 09:00", A)["released"] is True
    server.hold_slot("2026-11-11 09:00", A, "1.1.1.1", DATA)
    server._holds["2026-11-11 09:00"]["until"] = time.time() - 1
    assert server._held_slots(DATA) == []


def test_invalid_input_and_taken_slots():
    assert server.hold_slot("2026-11-10 09:00:00", A, "1.1.1.1", DATA)[0] == 400
    assert server.hold_slot("2026-11-10 09:00", "x", "1.1.1.1", DATA)[0] == 400
    assert server.hold_slot("2026-11-12 09:00", A, "1.1.1.1", DATA) == (409, {"ok": False, "reason": "taken"})
    assert server.hold_slot("2026-11-10 09:00", A, "1.1.1.1", None)[0] == 409


def test_per_address_cap_but_not_behind_a_silent_proxy():
    assert server.hold_slot("2026-11-10 09:00", "tab-one-aa", "3.3.3.3", DATA)[0] == 200
    assert server.hold_slot("2026-11-10 12:30", "tab-two-bb", "3.3.3.3", DATA)[0] == 200
    assert server.hold_slot("2026-11-11 09:00", "tab-three-cc", "3.3.3.3", DATA)[0] == 429
    server._holds.clear()
    for tok, slot in (("t1-aaaaaa", "2026-11-10 09:00"), ("t2-bbbbbb", "2026-11-10 12:30"), ("t3-cccccc", "2026-11-11 09:00")):
        assert server.hold_slot(slot, tok, "127.0.0.1", DATA)[0] == 200    # loopback = unknown client, no cap
