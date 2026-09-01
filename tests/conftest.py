"""Test-wide isolation.

The one that matters: engine runs must never land in the committed
`outputs/` tree. `APP_RUNS_DIR` was previously set by whichever test module
happened to import first, so running a subset — or a differently ordered
full suite — wrote real run directories into `outputs/`, overwriting a
committed run and leaving strays. That has bitten twice.

Setting it here fixes it for every invocation, subset or not, and before any
app module reads config.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must happen before `app.config` is imported by anything.
if not os.environ.get("APP_RUNS_DIR"):
    os.environ["APP_RUNS_DIR"] = str(
        Path(tempfile.mkdtemp(prefix="threerooms_test_runs_")) / "outputs")

# Same problem, same fix, for RESEARCH notes. Tests call generate_note()
# without an out_dir, which writes to the real outputs/research/ — so every
# suite run replaced live, web-researched notes with mock ones computed
# offline. That is what kept resurrecting "Mock mode: no web access" in the
# Research tab hours after the live research had been run.
if not os.environ.get("APP_RESEARCH_DIR"):
    os.environ["APP_RESEARCH_DIR"] = str(
        Path(tempfile.mkdtemp(prefix="threerooms_test_research_")))

# Tests never spend money: mock unless a test explicitly opts into live.
os.environ.setdefault("AGENT_MODE", "mock")
# No artificial pacing in tests.
os.environ.setdefault("ENGINE_PACE_SECONDS", "0")
