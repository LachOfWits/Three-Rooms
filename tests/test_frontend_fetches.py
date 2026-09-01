"""The frontend/backend fetch contract for PENDING-BATCH2.

Every `/api/...` the browser asks for must be a route this server actually
serves. That was NOT true when this file was written: `app/static/app.js`
probed three guessed agent-profile endpoints (`/api/agents/{room}/{id}/
profile` and friends) while the server served `/api/agents/{handle}/profile`,
so the §5 slide-out silently fell back to its degraded path on every click —
no persona grants from the server, no tool-call total, replies missing from
the post list. Nothing failed loudly, which is exactly why it survived.

These tests are deliberately about the CONTRACT, not about rendering:

  1. The primary profile endpoint the client probes first is a real route.
  2. The endpoint cache is an INDEX into the probe list, never a resolved
     path — caching the path meant agent #2 onwards fetched agent #1's URL.
  3. Every non-parameterised `/api/...` literal in app/static/*.js matches a
     registered route template.

AGENT_MODE is pinned to mock; nothing here makes an API call or an engine
subprocess.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_fetch_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"

import pytest

from app import config
from app.server.main import app

STATIC = Path(config.STATIC_DIR)
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


def _routes() -> list[tuple[str, frozenset]]:
    out = []
    for r in app.routes:
        p = getattr(r, "path", "")
        if p.startswith("/api"):
            out.append((p, frozenset(getattr(r, "methods", None) or {"GET"})))
    return out


def _route_templates() -> set[str]:
    return {p for p, _ in _routes()}


def _template_matcher(template: str) -> re.Pattern:
    """`/api/agents/{handle}/profile` -> a regex matching one concrete path."""
    parts = []
    for seg in template.strip("/").split("/"):
        parts.append(r"[^/]+" if seg.startswith("{") else re.escape(seg))
    return re.compile(r"^/" + "/".join(parts) + r"$")


def _matches_a_route(path: str, method: str | None = None) -> bool:
    """Method-aware: `/api/agents/4/activity` has the same SHAPE as the
    PATCH route `/api/agents/{room}/{agent_id}`, so shape alone would call
    a GET that 405s 'served'."""
    for t, methods in _routes():
        if _template_matcher(t).match(path) and (
                method is None or method.upper() in methods):
            return True
    return False


# --- 1. the profile endpoint the client probes first is real ---------------

def test_profile_endpoints_declared_in_probe_order():
    block = re.search(r"const PROFILE_ENDPOINTS = \[(.*?)\n\];", APP_JS, re.S)
    assert block, "PROFILE_ENDPOINTS is gone from app.js"
    # one entry per arrow function, however it is line-wrapped
    entries = [e for e in re.split(r"\(a\)\s*=>", block.group(1))[1:]]
    assert entries, "PROFILE_ENDPOINTS is empty"
    # The FIRST probe must be the handle-based route the server serves; a
    # guessed shape may only ever be a fallback behind it.
    first = " ".join(entries[0].split())
    assert "/api/agents/" in first and "a.handle" in first, first
    assert "/profile" in first, first


def test_primary_profile_endpoint_is_a_registered_route():
    assert "/api/agents/{handle}/profile" in _route_templates()
    assert _matches_a_route("/api/agents/focused/profile")


def test_guessed_profile_endpoints_are_still_unserved():
    """Documents WHY they are fallbacks: they 404. If one is ever added,
    this test fails and the ordering above should be reconsidered."""
    for p in ("/api/agents/1/4/profile", "/api/agents/1/4/activity",
              "/api/agents/4/activity"):
        assert not _matches_a_route(p, "GET"), \
            f"{p} now answers a GET — revisit probe order"


# --- 2. the endpoint cache is an index, not a resolved path ----------------

def test_profile_endpoint_cache_is_an_index_not_a_path():
    """A resolved path is per-agent. Caching the string made every agent
    after the first fetch the first agent's URL."""
    assert "profilePath" not in APP_JS, \
        "profilePath caches a resolved per-agent path — cache the probe index"
    assert "S.profileEp" in APP_JS
    body = re.search(r"async function loadAgentActivity\(a\) \{(.*?)\n\}",
                     APP_JS, re.S)
    assert body, "loadAgentActivity is gone"
    assert "PROFILE_ENDPOINTS[i](a)" in body.group(1)


# --- 3. every literal /api/ path the frontend fetches is a real route ------

def _literal_api_paths() -> set[str]:
    """`/api/...` string literals with no JS interpolation in them — the
    ones that can be checked without executing the file."""
    out = set()
    for js in sorted(STATIC.glob("*.js")):
        text = js.read_text(encoding="utf-8")
        for m in re.finditer(r'"(/api/[A-Za-z0-9_/{}-]*)"', text):
            raw = m.group(1)
            # a literal ending in "/" is a PREFIX the code concatenates an id
            # onto ("/api/agents/" + r) — not a path on its own
            if raw.endswith("/"):
                continue
            p = raw.split("?")[0]
            if p and "{" not in p:
                out.add(p)
    return out


def test_literal_api_paths_all_resolve():
    unresolved = sorted(p for p in _literal_api_paths()
                        if not _matches_a_route(p))
    assert not unresolved, f"frontend fetches unserved paths: {unresolved}"


@pytest.mark.parametrize("path", [
    "/api/config", "/api/runs", "/api/gates", "/api/scorecard",
    "/api/notifications", "/api/notifications/read_all",
    "/api/research", "/api/research/reports", "/api/research/run",
    "/api/rooms/1/feed", "/api/rooms/3/snapshot", "/api/rooms/3/snapshots",
    "/api/rooms/2/refresh", "/api/rooms/2/posts",
    "/api/agents/1", "/api/agents/2/7",
    "/api/runs/3", "/api/runs/3/stop", "/api/runs/3/scenario",
    "/api/runs/3/events", "/api/dashboard/3",
    "/api/notifications/4/read", "/api/gates/2/approve", "/api/gates/2/reject",
])
def test_each_frontend_fetch_shape_is_served(path):
    assert _matches_a_route(path), f"no route serves {path}"


# --- 4. §1: the month pickers offer only the committed months -------------

def test_month_picker_is_restricted_to_the_committed_months():
    """§1: only 2026_02 and 2026_03 exist. The other YAMLs stay on disk but
    must not be offered — `availableMonths()` seeds from COMMITTED_MONTHS and
    adds back only months that actually have a run."""
    m = re.search(r"const COMMITTED_MONTHS = \[(.*?)\];", APP_JS, re.S)
    assert m, "COMMITTED_MONTHS is gone from app.js"
    months = sorted(re.findall(r'"(\d{4}-\d{2})"', m.group(1)))
    assert months == ["2026-02", "2026-03"], months
    fn = re.search(r"function availableMonths\(\) \{(.*?)\n\}", APP_JS, re.S)
    assert fn, "availableMonths is gone"
    assert "COMMITTED_MONTHS" in fn.group(1)
    assert "MONTH_ASOF" not in fn.group(1), \
        "availableMonths must not offer every month in the SPEC table"
