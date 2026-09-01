"""App configuration (SPEC-APP section 1).

Limits are configuration and are displayed in the UI (the governor is part of
the demo). Secrets are NEVER held here: `.env` (ANTHROPIC_API_KEY etc.) is
loaded only inside `app/agents/runtime.py`. This module reads plain process
environment variables with safe defaults so the server works with no `.env`
at all (AGENT_MODE then defaults to `mock`).
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The venv interpreter used for engine subprocesses (falls back to whatever
# interpreter is running the server, e.g. on non-Windows checkouts).
_VENV_PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(_VENV_PY) if _VENV_PY.exists() else None  # None -> sys.executable

# Paths (env-overridable so tests can point at temp dirs).
DB_PATH = os.environ.get("APP_DB_PATH", str(PROJECT_ROOT / "app.sqlite"))
# Root the engine bridge lays run directories out under, as
# <root>/<YYYY_MM>/vN/{inputs,esg,pricing} (PENDING-BATCH2 section 1). The
# flat `outputs/runs/<integer id>/` layout is gone: no integer run id
# appears in a path. Env-overridable so tests get a temp root.
RUNS_DIR = os.environ.get("APP_RUNS_DIR", str(PROJECT_ROOT / "outputs"))
ASSUMPTIONS_DIR = PROJECT_ROOT / "assumptions"
BOOK_PATH = PROJECT_ROOT / "book" / "positions.json"
LIABILITIES_PATH = PROJECT_ROOT / "book" / "liabilities.json"
STATIC_DIR = PROJECT_ROOT / "app" / "static"
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"
GROUND_TRUTH_PATH = SCENARIOS_DIR / "seeded" / "ground_truth.yaml"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Compute governor (SPEC-APP section 1). Displayed by GET /api/config.
MAX_TOOL_CALLS_PER_POST = int(os.environ.get("MAX_TOOL_CALLS_PER_POST", "12"))
# Research and desk agents run search -> fetch -> read cycles toward a ~20
# source target; the checker budget starves them.
MAX_TOOL_CALLS_RESEARCH = int(os.environ.get("MAX_TOOL_CALLS_RESEARCH", "40"))
MAX_REPLIES_PER_THREAD = int(os.environ.get("MAX_REPLIES_PER_THREAD", "6"))
MAX_POSTS_PER_PASS = int(os.environ.get("MAX_POSTS_PER_PASS", "25"))

# Fresh snapshots (SPEC-APP section E): default forward walk of the
# market-data window, in business days, capped at the last available date.
SNAPSHOT_STEP_BUSINESS_DAYS = int(
    os.environ.get("SNAPSHOT_STEP_BUSINESS_DAYS", "5"))

# Server bind.
HOST = "127.0.0.1"
PORT = 8600

DEFAULT_SEED = 20260831
DEFAULT_SIMS = 50000


def engine_pace_seconds() -> float:
    """Artificial delay between stage events so a seconds-fast model run is
    watchable (documented, not hidden). Read per-call so tests can zero it."""
    try:
        return float(os.environ.get("ENGINE_PACE_SECONDS", "2"))
    except ValueError:
        return 2.0


def agent_mode() -> str:
    """`mock` (default) | `live`. Single source of truth: delegate to the
    agents runtime (the only .env reader), so the mode the API/UI reports
    can never lag the mode the agents actually run in (audit finding #13).
    Falls back to the plain process environment when app.agents is absent."""
    try:
        from app.agents import runtime  # noqa: PLC0415 (guarded, lazy)
        return runtime.agent_mode()
    except Exception:
        mode = os.environ.get("AGENT_MODE", "mock").strip().lower()
        return mode if mode in ("mock", "live") else "mock"


def anthropic_model() -> str:
    try:
        from app.agents import runtime  # noqa: PLC0415 (guarded, lazy)
        return runtime.anthropic_model()
    except Exception:
        return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def public_config() -> dict:
    """What GET /api/config surfaces — limits + mode, no secrets."""
    return {
        "agent_mode": agent_mode(),
        "anthropic_model": anthropic_model() if agent_mode() == "live" else None,
        "limits": {
            "MAX_TOOL_CALLS_PER_POST": MAX_TOOL_CALLS_PER_POST,
            "MAX_TOOL_CALLS_RESEARCH": MAX_TOOL_CALLS_RESEARCH,
            "MAX_REPLIES_PER_THREAD": MAX_REPLIES_PER_THREAD,
            "MAX_POSTS_PER_PASS": MAX_POSTS_PER_PASS,
            "ENGINE_PACE_SECONDS": engine_pace_seconds(),
        },
        "engine": {
            "default_seed": DEFAULT_SEED,
            "default_sims": DEFAULT_SIMS,
        },
    }
