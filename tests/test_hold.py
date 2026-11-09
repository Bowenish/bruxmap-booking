"""Slot holds: one visitor at a time per slot, one slot at a time per visitor, no personal data.
2026-11-09.1 adds renewal, the form-stage tail, a hard ceiling and restart persistence."""
import json
import time

import server

DATA = {"slots": ["2026-11-10T09:00:00", "2026-11-10T12:30:00", "2026-11-11T09:00:00", "2026-11-11T12:30:00",
                  "2026-11-12T09:00:00", "2026-11-12T12:30:00", "2026-11-13T09:00:00"]}
A, B = "tokenAAAA1", "tokenBBBB2"
S1, S2, S3 = "2026-11-10 09:00", "2026-11-10 12:30", "2026-11-11 09:00"


def setup_function():
    server._holds.clear()
    server._holds_error = None


def test_hold_then_second_visitor_is_refused():
    st, body = server.hold_slot(S1, A, "1.1.1.1", DATA)
    assert st == 200 and body["ok"] and body["stage"] == "dialog" and body["hold_seconds"] == server.HOLD_SECONDS
    st, body = server.hold_slot(S1, B, "2.2.2.2", DATA)
    assert st == 409 and body["reason"] == "held"
    assert server._held_slots(DATA) == [S1]


def test_same_visitor_may_rehold_and_moving_releases_the_old_slot():
    assert server.hold_slot(S1, A, "1.1.1.1", DATA)[0] == 200
    assert server.hold_slot(S1, A, "1.1.1.1", DATA)[0] == 200      # reload, same token
    assert server.hold_slot(S2, A, "1.1.1.1", DATA)[0] == 200      # clicked another slot
    assert server._held_slots(DATA) == [S2]                        # the first one is free again
    assert server.hold_slot(S1, B, "2.2.2.2", DATA)[0] == 200


def test_release_needs_the_same_token_and_holds_expire():
    server.hold_slot(S1, A, "1.1.1.1", DATA)
    assert server.release_slot(S1, B)["released"] is False
    assert server.release_slot(S1, A)["released"] is True
    server.hold_slot(S3, A, "1.1.1.1", DATA)
    server._holds[S3]["until"] = time.time() - 1
    assert server._held_slots(DATA) == []


def test_invalid_input_and_taken_slots():
    assert server.hold_slot("2026-11-10 09:00:00", A, "1.1.1.1", DATA)[0] == 400
    assert server.hold_slot(S1, "x", "1.1.1.1", DATA)[0] == 400
    assert server.hold_slot("2026-11-20 09:00", A, "1.1.1.1", DATA) == (409, {"ok": False, "reason": "taken"})
    assert server.hold_slot(S1, A, "1.1.1.1", None)[0] == 409


def test_per_address_cap_but_not_behind_a_silent_proxy():
    slots = [s[:10] + " " + s[11:16] for s in DATA["slots"]]
    for i in range(server.HOLD_PER_IP):                                            # 5 visitors behind one campus address
        assert server.hold_slot(slots[i], f"tab-{i}-aaaaaa", "3.3.3.3", DATA)[0] == 200
    assert server.hold_slot(slots[server.HOLD_PER_IP], "tab-x-aaaaaa", "3.3.3.3", DATA)[0] == 429
    assert server.hold_slot(slots[0], "tab-0-aaaaaa", "3.3.3.3", DATA)[0] == 200   # renewing an existing hold is not capped
    server._holds.clear()
    for i in range(server.HOLD_PER_IP + 1):
        assert server.hold_slot(slots[i], f"loop-{i}-aaaa", "127.0.0.1", DATA)[0] == 200    # loopback = unknown client, no cap


def test_renewal_keeps_created_and_the_form_stage_extends_the_tail():
    assert server.hold_slot(S1, A, "1.1.1.1", DATA)[0] == 200
    created = server._holds[S1]["created"]
    server._holds[S1]["until"] -= 100                                          # pretend 100 s passed
    st, body = server.hold_slot(S1, A, "1.1.1.1", DATA)                        # the page renews every 90 s
    assert st == 200 and body["stage"] == "dialog" and body["hold_seconds"] == server.HOLD_SECONDS
    assert server._holds[S1]["created"] == created
    assert server._holds[S1]["until"] >= time.time() + server.HOLD_SECONDS - 2
    st, body = server.hold_slot(S1, A, "1.1.1.1", DATA, stage="form")          # Continue pressed: long tail
    assert st == 200 and body["stage"] == "form" and body["hold_seconds"] == server.HOLD_FORM_SECONDS
    assert server._holds[S1]["until"] >= time.time() + server.HOLD_FORM_SECONDS - 2
    assert server._holds[S1]["created"] == created
    assert server.hold_slot(S1, B, "2.2.2.2", DATA)[0] == 409                 # still hidden from the next visitor
    assert server.hold_slot(S1, A, "1.1.1.1", DATA, stage="nonsense")[1]["stage"] == "dialog"


def test_no_hold_outlives_the_ceiling():
    server.hold_slot(S1, A, "1.1.1.1", DATA)
    server._holds[S1]["created"] = time.time() - server.HOLD_MAX_SECONDS - 1
    assert server.hold_slot(S1, A, "1.1.1.1", DATA) == (409, {"ok": False, "reason": "expired"})
    assert S1 not in server._holds
    assert server.hold_slot(S1, B, "2.2.2.2", DATA)[0] == 200                 # free for the next visitor
    server._holds[S1]["created"] = time.time() - server.HOLD_MAX_SECONDS + 60  # 1 min of ceiling left
    st, body = server.hold_slot(S1, B, "2.2.2.2", DATA, stage="form")
    assert st == 200 and body["until"] <= time.time() + 61                     # the form tail is clipped at the ceiling


def test_holds_survive_a_restart(tmp_path, monkeypatch):
    path = tmp_path / "holds.json"
    monkeypatch.setattr(server, "HOLDS_FILE", str(path))
    server.hold_slot(S1, A, "1.1.1.1", DATA)
    server.hold_slot(S2, B, "2.2.2.2", DATA, stage="form")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {S1, S2} and saved[S2]["stage"] == "form" and "created" in saved[S1]
    saved[S1]["until"] = time.time() - 1                                       # expired while the process was down
    saved["garbage"] = {"until": "soon"}
    path.write_text(json.dumps(saved), encoding="utf-8")
    server._holds.clear()
    assert server._load_holds() == 1                                           # the live one comes back, the rest is dropped
    assert list(server._holds) == [S2] and server._holds[S2]["stage"] == "form" and server._holds[S2]["token"] == B
    assert server.hold_slot(S2, A, "1.1.1.1", DATA)[0] == 409                 # and it still protects the slot
    server.release_slot(S2, B)
    assert json.loads(path.read_text(encoding="utf-8")) == {}                  # release writes through


def test_a_damaged_or_unwritable_holds_file_never_blocks_bookings(tmp_path, monkeypatch):
    path = tmp_path / "holds.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(server, "HOLDS_FILE", str(path))
    assert server._load_holds() == 0 and server._holds_error
    assert server.hold_slot(S1, A, "1.1.1.1", DATA)[0] == 200                 # and the next save repairs the file
    assert server._holds_error is None and S1 in json.loads(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(server, "HOLDS_FILE", str(tmp_path / "no_such_dir" / "holds.json"))
    assert server.hold_slot(S2, A, "1.1.1.1", DATA)[0] == 200                 # unwritable path: hold still placed
    assert server._holds_error
    monkeypatch.setattr(server, "HOLDS_FILE", "")
    assert server._load_holds() == 0                                           # memory-only mode is a no-op


def test_healthz_and_slots_report_the_new_fields():
    assert server.healthz()["holds_persisted"] is False                      # tests run with HOLDS_FILE=""
    assert server.healthz()["holds_error"] is None
    server._cache["data"], server._cache["t"] = dict(DATA, system={}), time.time()
    body = json.loads(server.slots().body)
    assert body["poll_seconds"] == server.POLL_SECONDS and body["held"] == [] and body["hold_seconds"] == server.HOLD_SECONDS
