"""Saved cycles (PENDING-JUDGE §4).

A judge with no API key sees exactly what this module restores, so it is
worth holding to: it must refuse to save mock output as if it were live,
it must round-trip a cycle faithfully, and its manifest must not overstate
what the cycle contains.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.server import cycles, db


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(cycles, "CYCLES_DIR", tmp_path / "saved_cycles")
    c = db.init_db(tmp_path / "cycle.sqlite")
    c.execute("INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
              "VALUES ('2026-03','base','done','/tmp/x',1,2)")
    c.execute("INSERT INTO agents (room, handle, name, focus, "
              "persona_prompt, builtin) VALUES (1,'@t','T','f','p',1)")
    c.execute("INSERT INTO posts (room, agent_id, author_label, type, "
              "body_md, status, web_sources_json, created_at) VALUES "
              "(1,1,'@t','origin','body','published',?,'now')",
              (json.dumps(["https://a.example", "https://b.example"]),))
    c.execute("INSERT INTO posts (room, agent_id, author_label, type, "
              "body_md, status, suppression_reason, web_sources_json, "
              "created_at) VALUES "
              "(1,1,'@t','origin','bad','suppressed','unbound',?,'now')",
              (json.dumps(["https://b.example"]),))
    c.commit()
    return c


def test_mock_output_cannot_be_saved_as_a_cycle(conn):
    """A saved cycle is presented to judges as real model output. Saving a
    mock run would make the software itself the thing telling the lie."""
    with pytest.raises(ValueError, match="only a live cycle"):
        cycles.save_cycle("m", mode="mock", model="claude-opus-5",
                          effort="low")


def test_manifest_counts_what_is_actually_there(conn):
    m = cycles.save_cycle("March 2026", mode="live",
                          model="claude-opus-5", effort="low")
    assert m["slug"] == "march-2026"
    assert m["model"] == "claude-opus-5" and m["effort"] == "low"
    c = m["counts"]
    assert c["posts_published"] == 1
    assert c["posts_suppressed"] == 1      # not hidden from the count
    assert c["runs"] == 1
    # deduplicated across posts: two posts, three references, two URLs
    assert c["web_sources"] == 2


def test_round_trip_restores_the_cycle(conn, tmp_path):
    cycles.save_cycle("rt", mode="live", model="m", effort="low")
    conn.execute("DELETE FROM posts")
    conn.commit()
    assert conn.execute("SELECT count(*) AS n FROM posts").fetchone()["n"] == 0

    cycles.load_cycle("rt")
    rows = conn.execute("SELECT status, web_sources_json FROM posts "
                        "ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["published", "suppressed"]
    assert json.loads(rows[0]["web_sources_json"]) == [
        "https://a.example", "https://b.example"]


def test_listing_is_newest_first_and_survives_a_bad_directory(conn):
    cycles.save_cycle("one", mode="live", model="m", effort="low")
    cycles.save_cycle("two", mode="live", model="m", effort="low")
    junk = cycles.CYCLES_DIR / "not-a-cycle"
    junk.mkdir(parents=True, exist_ok=True)
    (junk / "manifest.json").write_text("{oops", encoding="utf-8")
    listed = [c["slug"] for c in cycles.list_cycles()]
    assert "one" in listed and "two" in listed
    assert "not-a-cycle" not in listed      # unreadable, skipped not fatal


def test_loading_an_unknown_cycle_is_a_clear_error(conn):
    with pytest.raises(FileNotFoundError, match="no saved cycle"):
        cycles.load_cycle("nope")
