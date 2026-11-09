"""Shared test setup: the tests must never touch the real holds.json next to server.py, so
persistence is switched off before `server` is imported; the persistence tests point
`server.HOLDS_FILE` at a temporary file themselves."""
import os
import pathlib
import sys

os.environ.setdefault("HOLDS_FILE", "")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
