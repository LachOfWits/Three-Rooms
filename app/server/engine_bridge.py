"""Engine bridge: the ONLY place the app executes the deterministic engine.

- `create_run(...)` records a queued run row, resolves/derives its input
  files (never touching `assumptions/`, `book/` or the committed outputs),
  and returns the row.
- `execute_run(run_id)` runs `python -m engine.run` as a subprocess into the
  run's `pricing/` directory with `--stage-log`, tails the stage log into
  `stage_events` + SSE, and calls the agents narrator per event (guarded —
  the server works without app.agents present). Pacing per
  ENGINE_PACE_SECONDS makes the seconds-fast run watchable.

Run layout (PENDING-BATCH2 section 1) — month, version, stage:

    outputs/2026_03/v1/
      inputs/         manifest.json (+ any derived assumptions YAML)
      stage_log.jsonl
      esg/            assumptions_used.yaml, sim_factors.npy, sim_index.json
      pricing/        valuation.json, var_standalone_positions.csv,
                      var_standalone_factors.json, var_aggregate.json,
                      sim_pnl_positions.npy, sim_surplus.npy,
                      sim_pnl_sample.csv

`engine/run.py` is untouched by this: it writes wherever `--out` points, so
the bridge points it at `vN/pricing/` and then MOVES the ESG-stage artefacts
across into `vN/esg/` and copies the assumptions the run actually used in as
`assumptions_used.yaml`. Nothing is duplicated — the two stages partition the
run's files.

`runs.out_dir` records the `pricing/` directory (the priced results are what
every dashboard endpoint reads); `run_root()` walks back up to `vN/` and
`artifact_path()` finds a named artefact on whichever side of the run holds
it, falling back to a flat directory for temp/sensitivity/fixture runs.

The integer `runs.id` stays internal to the database: no path and no API
response identifies a run by it. The identity a human sees is the LABEL
(`2603_v1`) and the directory (`outputs/2026_03/v1`), both derived by
`run_identity()` and attached to every run row this module returns.

Rerun-with-adjustments writes a derived assumptions YAML under the run's own
`inputs/` (parent's assumptions + overrides) and records lineage via
`parent_run_id` + `adjustments_json` + the manifest.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

from app import config
from app.server import db
from app.server.events import broker

STAGE_LOG_NAME = "stage_log.jsonl"
MANIFEST_NAME = "manifest.json"
INPUTS_DIR_NAME = "inputs"

# --- run directory layout (PENDING-BATCH2 section 1) -----------------------

ESG_STAGE = "esg"
PRICING_STAGE = "pricing"
STAGE_DIRS = (ESG_STAGE, PRICING_STAGE)

ASSUMPTIONS_USED_NAME = "assumptions_used.yaml"

# The partition. Everything `engine/run.py` writes belongs to exactly one
# side; `assumptions_used.yaml` is placed by the bridge, not the engine.
ESG_ARTEFACTS = (ASSUMPTIONS_USED_NAME, "sim_factors.npy", "sim_index.json")
PRICING_ARTEFACTS = ("valuation.json", "var_standalone_positions.csv",
                     "var_standalone_factors.json", "var_aggregate.json",
                     "sim_pnl_positions.npy", "sim_surplus.npy",
                     "sim_pnl_sample.csv")

_VERSION_DIR_RE = re.compile(r"^v(\d+)$")


def outputs_root() -> Path:
    """Root the month directories are laid out under. `config.RUNS_DIR`
    (env `APP_RUNS_DIR`) so tests can point the whole layout at a temp dir;
    `outputs/` in the real app."""
    return Path(config.RUNS_DIR)


def month_dir_name(asof) -> str:
    """'2026-03-31' or '2026-03' -> '2026_03' (the month directory)."""
    return str(asof)[:7].replace("-", "_")


def run_label(asof, version: int) -> str:
    """'2026-03-31', 1 -> '2603_v1' — the identity a human reads."""
    return f"{str(asof)[2:4]}{str(asof)[5:7]}_v{int(version)}"


def version_dir(asof, version: int) -> Path:
    return outputs_root() / month_dir_name(asof) / f"v{int(version)}"


def run_root(out_dir) -> Path:
    """A run's `vN/` directory, given any of `vN/`, `vN/esg`, `vN/pricing`."""
    p = Path(str(out_dir))
    return p.parent if p.name in STAGE_DIRS else p


def parse_run_dir(out_dir) -> tuple[str | None, int | None, Path | None]:
    """(month key 'YYYY-MM', version, `vN/` path) for a directory in the
    layout; (None, None, None) for a flat/legacy/temp directory."""
    if not out_dir:
        return (None, None, None)
    root = run_root(out_dir)
    m = _VERSION_DIR_RE.match(root.name)
    if not m:
        return (None, None, None)
    month = root.parent.name.replace("_", "-")
    if not re.match(r"^\d{4}-\d{2}$", month):
        return (None, None, None)
    return (month, int(m.group(1)), root)


def stage_dir(out_dir, stage: str) -> Path:
    return run_root(out_dir) / stage


def manifest_path(out_dir) -> Path:
    """The run manifest lives at the run ROOT — it describes the whole run,
    not one stage. Falls back to `<out_dir>/inputs/manifest.json` for a flat
    directory that already carries one (legacy/temp runs)."""
    root = run_root(out_dir) / INPUTS_DIR_NAME / MANIFEST_NAME
    if root.exists():
        return root
    flat = Path(str(out_dir)) / INPUTS_DIR_NAME / MANIFEST_NAME
    return flat if flat.exists() else root


def stage_log_path(out_dir) -> Path:
    return run_root(out_dir) / STAGE_LOG_NAME


def artifact_path(out_dir, filename: str) -> Path:
    """Locate one engine artefact for a run, whichever stage holds it.

    Accepts the run root or either stage directory. Filenames are unique
    across the two stages, so first-existing wins. A flat directory (the
    temp dirs `run_sensitivity` writes, test fixtures, any pre-restructure
    run) still resolves, which is why the flat candidate is tried too. When
    nothing exists the flat path is returned so callers' "no such file"
    messages name the directory they asked about."""
    name = Path(str(filename)).name          # never a traversal
    d = Path(str(out_dir))
    root = run_root(d)
    for cand in (d / name, root / PRICING_STAGE / name,
                 root / ESG_STAGE / name):
        if cand.exists():
            return cand
    return d / name


def attribution_dir_name(prev_month, prev_version, curr_month,
                         curr_version) -> str:
    """'attr_2026_02_v1__2026_03_v1' — both ends named, so a reader never
    has to look up which run an attribution was computed from."""
    return (f"attr_{month_dir_name(prev_month)}_v{int(prev_version)}"
            f"__{month_dir_name(curr_month)}_v{int(curr_version)}")


def attribution_dir_names(prev_run: dict, curr_run: dict) -> list[str]:
    """Directory names to look for this pair's attribution under, most
    specific first: the exact pair of runs, then the two months' BASE runs
    (v1 -> v1). The fallback is what an ad-hoc or seeded run has to compare
    against — nobody commits an attribution for a run that was invented at
    the console — and a caller that must not quote a different book pair
    still checks the attribution's own input hashes before using it."""
    p, c = run_identity(prev_run), run_identity(curr_run)
    exact = attribution_dir_name(p.get("month"), p.get("version") or 1,
                                 c.get("month"), c.get("version") or 1)
    base = attribution_dir_name(p.get("month"), 1, c.get("month"), 1)
    return [exact] if exact == base else [exact, base]


def attribution_dir_for_runs(prev_run: dict, curr_run: dict) -> Path:
    """The committed pair-attribution directory for two run rows: the exact
    pair when it exists on disk, else the months' base pair."""
    names = attribution_dir_names(prev_run, curr_run)
    for n in names:
        if (config.OUTPUTS_DIR / n / "attribution.json").exists():
            return config.OUTPUTS_DIR / n
    return config.OUTPUTS_DIR / names[0]


def _db_version(run: dict) -> int:
    """Fallback version for a run row whose out_dir is NOT in the layout —
    a seeded or ad-hoc run pointing at `scenarios/`, or a row left over from
    the old flat `outputs/runs/<id>/`.

    It must not collide with a run that genuinely owns a `vN` directory for
    that month: the label is the identity the picker shows, and two runs
    labelled `2603_v1` would be indistinguishable there. So the versions
    already claimed by layout runs are skipped, and what is left is handed
    out to the non-layout rows in id order."""
    try:
        rows = db.get_db().execute(
            "SELECT id, out_dir FROM runs WHERE substr(asof, 1, 7) = ? "
            "ORDER BY id", (str(run.get("asof"))[:7],)).fetchall()
    except Exception:
        return 1
    taken = set()
    for r in rows:
        v = parse_run_dir(r["out_dir"])[1]
        if v is not None:
            taken.add(v)
    nxt = 1
    for r in rows:
        if parse_run_dir(r["out_dir"])[1] is not None:
            continue
        while nxt in taken:
            nxt += 1
        if r["id"] == run.get("id"):
            return nxt
        taken.add(nxt)
    return 1


def _rel(p: Path) -> str:
    """Project-relative posix path when possible (what the UI shows)."""
    try:
        return Path(p).resolve().relative_to(
            config.PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return Path(p).as_posix()


def run_identity(run: dict) -> dict:
    """Label + directories for a run row. This is the identity that leaves
    the server: the integer id never appears in a path or a label."""
    if not run:
        return {}
    month, version, root = parse_run_dir(run.get("out_dir"))
    if month is None:
        month = str(run.get("asof"))[:7]
        version = _db_version(run)
        root = Path(run["out_dir"]) if run.get("out_dir") else None
    ident = {"label": run_label(month + "-01", version),
             "version": version, "month": month}
    if root is not None:
        ident["run_dir"] = _rel(root)
        ident["esg_dir"] = _rel(root / ESG_STAGE)
        ident["pricing_dir"] = _rel(root / PRICING_STAGE)
    return ident


def decorate_run(run: dict | None) -> dict | None:
    """Attach `label`/`version`/`month`/`*_dir` to a run row in place."""
    if run is None:
        return None
    run.update(run_identity(run))
    return run


def next_version(asof) -> int:
    """Next free version for a month: one past the highest version already
    on disk OR already recorded in the database. Disk is consulted so a
    fresh database can never overwrite a committed run directory."""
    highest = 0
    month_dir = outputs_root() / month_dir_name(asof)
    if month_dir.is_dir():
        for child in month_dir.iterdir():
            m = _VERSION_DIR_RE.match(child.name)
            if child.is_dir() and m:
                highest = max(highest, int(m.group(1)))
    try:
        rows = db.get_db().execute(
            "SELECT out_dir FROM runs WHERE substr(asof, 1, 7) = ?",
            (str(asof)[:7],)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        _, v, _ = parse_run_dir(r["out_dir"])
        if v:
            highest = max(highest, v)
    return highest + 1

# Live engine subprocesses, keyed by run id (PENDING-ROSTER J: Stop run).
# `_STOP_REQUESTED` also covers the window between create_run and Popen, so a
# stop issued while the run is still queued is honoured rather than lost.
_PROCS: dict[int, subprocess.Popen] = {}
_STOP_REQUESTED: set[int] = set()
_PROC_LOCK = threading.Lock()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _agents_api():
    """Guarded import — server must run before app/agents exists."""
    try:
        from app.agents import api as agents_api  # noqa: PLC0415
        return agents_api
    except Exception:
        return None


def _python_exe() -> str:
    return config.PYTHON_EXE or sys.executable


def _month_key(asof: str) -> str:
    """'2026-03-31' or '2026-03' -> '2026-03' (assumptions file naming)."""
    return str(asof)[:7]


def get_run(run_id: int) -> dict | None:
    cur = db.get_db().execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    return decorate_run(cur.fetchone())


def _manifest_path(out_dir: str) -> Path:
    return manifest_path(out_dir)


def read_manifest(out_dir: str) -> dict | None:
    p = manifest_path(out_dir)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# --- adjustments -> derived assumptions YAML -------------------------------

def _flatten_leaves(prefix: list[str], obj: dict, out: list) -> None:
    for k, v in obj.items():
        if isinstance(v, dict):
            _flatten_leaves(prefix + [str(k)], v, out)
        else:
            out.append((prefix + [str(k)], v))


def _resolve_key(node: dict, seg: str):
    """Prefer an existing key: exact string, else int (YAML tenor keys are
    ints), else float; fall back to the string segment."""
    if seg in node:
        return seg
    for caster in (int, float):
        try:
            cast = caster(seg)
        except ValueError:
            continue
        if cast in node:
            return cast
    return seg

def apply_adjustments(doc: dict, adjustments: dict) -> list[dict]:
    """Apply overrides in-place. Accepts nested dicts and/or dotted paths
    (e.g. {"vols.gbp_swap.10": 0.0073}). Returns a change log."""
    leaves: list = []
    for k, v in adjustments.items():
        segs = str(k).split(".")
        if isinstance(v, dict):
            _flatten_leaves(segs, v, leaves)
        else:
            leaves.append((segs, v))
    changes = []
    for segs, value in leaves:
        node = doc
        for seg in segs[:-1]:
            key = _resolve_key(node, seg)
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]
        leaf = _resolve_key(node, segs[-1])
        changes.append({"path": ".".join(segs), "old": node.get(leaf),
                        "new": value})
        node[leaf] = value
    return changes


def _parent_assumptions_path(parent: dict) -> str:
    """Assumptions file the parent run actually used."""
    man = read_manifest(parent["out_dir"]) if parent.get("out_dir") else None
    if man and man.get("assumptions_path"):
        return man["assumptions_path"]
    # fall back to the engine's own record, then to the committed file
    if parent.get("out_dir"):
        val = Path(parent["out_dir"]) / "valuation.json"
        if val.exists():
            with open(val, "r", encoding="utf-8") as f:
                meta = json.load(f).get("meta", {})
            if meta.get("assumptions_path"):
                return meta["assumptions_path"]
    return str(config.ASSUMPTIONS_DIR / f"{_month_key(parent['asof'])}.yaml")


def _parent_liabilities_path(parent: dict) -> str:
    """Liability cohorts the parent run actually used — a rerun inherits
    them for exactly the reason it inherits the book (approving an
    assumptions gate must not silently swap the reserve file too)."""
    man = read_manifest(parent["out_dir"]) if parent.get("out_dir") else None
    if man and man.get("liabilities_path"):
        return man["liabilities_path"]
    return str(config.LIABILITIES_PATH)


def _parent_book_path(parent: dict) -> str:
    """Book file the parent run actually used — a rerun must inherit it, or
    approving an assumptions gate would silently also swap a seeded book
    back to the committed one (a change no human approved)."""
    man = read_manifest(parent["out_dir"]) if parent.get("out_dir") else None
    if man and man.get("book_path"):
        return man["book_path"]
    if parent.get("out_dir"):
        val = Path(parent["out_dir"]) / "valuation.json"
        if val.exists():
            with open(val, "r", encoding="utf-8") as f:
                meta = json.load(f).get("meta", {})
            if meta.get("book_path"):
                return meta["book_path"]
    return str(config.BOOK_PATH)


# --- run creation ----------------------------------------------------------

def create_run(asof: str | None, kind: str = "base",
               parent_run_id: int | None = None,
               seeded_assumptions_path: str | None = None,
               seeded_book_path: str | None = None,
               seeded_liabilities_path: str | None = None,
               adjustments_json: dict | str | None = None,
               sims: int | None = None, seed: int | None = None) -> dict:
    """Insert a queued run row with resolved inputs; return the row.

    Never modifies assumptions/, book/, data/ — seeded inputs are read from
    the caller-given paths, derived assumptions are written under the run's
    own out dir.
    """
    conn = db.get_db()
    if isinstance(adjustments_json, str):
        adjustments = json.loads(adjustments_json) if adjustments_json else None
    else:
        adjustments = adjustments_json

    parent = get_run(parent_run_id) if parent_run_id else None
    if kind == "rerun" and parent is None:
        raise ValueError("rerun requires a valid parent_run_id")
    if asof is None:
        if parent is None:
            raise ValueError("asof is required for a base run")
        asof = parent["asof"]
    if parent is not None:
        # A rerun differs from its parent ONLY by the approved adjustments:
        # inherit seed/sims unless explicitly overridden.
        if sims is None:
            sims = parent["sims"]
        if seed is None:
            seed = parent["seed"]

    cur = conn.execute(
        "INSERT INTO runs (asof, kind, parent_run_id, seed, sims, status, "
        "adjustments_json, started_at) VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL)",
        (asof, kind, parent_run_id,
         seed if seed is not None else config.DEFAULT_SEED,
         sims if sims is not None else config.DEFAULT_SIMS,
         json.dumps(adjustments) if adjustments else None))
    run_id = cur.lastrowid

    # outputs/<YYYY_MM>/vN/{inputs,esg,pricing}. The DB records the pricing
    # directory as `out_dir` (the priced results are what the dashboard
    # endpoints read); the run root and the ESG side hang off it.
    version = next_version(asof)
    root = version_dir(asof, version)
    out_dir = root / PRICING_STAGE
    (root / INPUTS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (root / ESG_STAGE).mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve inputs.
    if adjustments and parent is not None:
        src = _parent_assumptions_path(parent)
        with open(src, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        changes = apply_adjustments(doc, adjustments)
        assumptions_path = str(root / INPUTS_DIR_NAME /
                               f"assumptions_{_month_key(asof)}_derived.yaml")
        with open(assumptions_path, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        derived_from = src
    else:
        assumptions_path = (seeded_assumptions_path or
                            str(config.ASSUMPTIONS_DIR /
                                f"{_month_key(asof)}.yaml"))
        changes, derived_from = None, None
    if not Path(assumptions_path).exists():
        conn.execute("UPDATE runs SET status = 'failed', finished_at = ? "
                     "WHERE id = ?", (_now(), run_id))
        conn.commit()
        raise FileNotFoundError(f"assumptions file not found: {assumptions_path}")

    book_path = seeded_book_path or (
        _parent_book_path(parent) if parent is not None
        else str(config.BOOK_PATH))
    # The liability cohorts are an input like the book: a month-end pair
    # that carries written business changes BOTH sides (SPEC section 7 —
    # `liabilities_2026-03.json` scales the cohorts with the same period's
    # new business), and @warden's market/flows/decision split reads the
    # reserve flow off exactly this file. Defaulted, inherited by a rerun,
    # overridable per run.
    liabilities_path = seeded_liabilities_path or (
        _parent_liabilities_path(parent) if parent is not None
        else str(config.LIABILITIES_PATH))

    # A run is seeded when any RESOLVED input traces to scenarios/seeded/ —
    # including a gate rerun that inherits its parent's seeded book, or
    # derives corrected assumptions FROM a seeded file. (Bug found in the
    # final integration demo: the D1-gate rerun still priced the seeded D2
    # book but reported seeded=false, so the frontend's base-run picker
    # would have selected it as the clean base run.)
    def _under_seeded(p) -> bool:
        if not p:
            return False
        try:
            seeded_root = (config.SCENARIOS_DIR / "seeded").resolve()
            return Path(str(p)).resolve().is_relative_to(seeded_root)
        except (OSError, ValueError):
            return False

    seeded = any(_under_seeded(p) for p in (
        seeded_assumptions_path, seeded_book_path, seeded_liabilities_path,
        assumptions_path, derived_from, book_path, liabilities_path))

    manifest = {
        "run_id": run_id, "asof": asof, "kind": kind,
        "label": run_label(asof, version), "version": version,
        "run_dir": _rel(root),
        "parent_run_id": parent_run_id,
        "assumptions_path": str(assumptions_path),
        "assumptions_derived_from": derived_from,
        "adjustment_changes": changes,
        "book_path": str(book_path),
        "liabilities_path": str(liabilities_path),
        "seeded": seeded,
        "created_at": _now(),
    }
    with open(root / INPUTS_DIR_NAME / MANIFEST_NAME, "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    conn.execute("UPDATE runs SET out_dir = ? WHERE id = ?",
                 (str(out_dir), run_id))
    conn.commit()
    return get_run(run_id)


# --- execution -------------------------------------------------------------

def _record_stage_event(run_id: int, ev: dict) -> dict:
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO stage_events (run_id, stage, status, detail_json, ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, ev.get("stage"), ev.get("status"),
         json.dumps({k: v for k, v in ev.items()
                     if k not in ("stage", "status", "ts")}) or None,
         ev.get("ts") or _now()))
    conn.commit()
    row = conn.execute("SELECT * FROM stage_events WHERE id = ?",
                       (cur.lastrowid,)).fetchone()
    return row


def _emit_stage_event(run_id: int, ev: dict, agents_api) -> None:
    row = _record_stage_event(run_id, ev)
    broker.publish("stage", row, run_id=run_id)
    if agents_api is not None:
        try:
            post_id = agents_api.stage_narrator_post(run_id, dict(row))
        except Exception:
            post_id = None
        if post_id:
            notify_posts([post_id])


def notify_posts(post_ids: list[int]) -> None:
    """Broadcast new-post notifications to every open SSE stream."""
    if not post_ids:
        return
    conn = db.get_db()
    for pid in post_ids:
        post = conn.execute(
            "SELECT id, room, run_id, type, status, author_label, parent_id "
            "FROM posts WHERE id = ?", (pid,)).fetchone()
        if post:
            broker.publish("post", post, run_id=None)


def notify_notifications(post_ids: list[int]) -> None:
    """Broadcast any `notifications` rows created against these post ids
    over the existing SSE stream (SPEC-APP F: "delivered live over the
    existing SSE stream, which already carries new-post events"). Call
    after any agents_api entry point that may have created notifications
    (a reply, a mention, a gate proposal, a suppression)."""
    if not post_ids:
        return
    conn = db.get_db()
    marks = ",".join("?" * len(post_ids))
    for row in conn.execute(
            f"SELECT * FROM notifications WHERE post_id IN ({marks})",
            list(post_ids)).fetchall():
        broker.publish("notification", row, run_id=None)


def create_and_notify(kind: str, post_id: int | None = None,
                      thread_root_id: int | None = None,
                      room: int | None = None,
                      agent_id: int | None = None) -> dict:
    """Insert one notifications row and broadcast it immediately — used for
    events the SERVER itself originates (e.g. 'snapshot_ready', which has
    no single post to key off of the way a reply or a gate does)."""
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO notifications (kind, post_id, thread_root_id, room, "
        "agent_id, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (kind, post_id, thread_root_id, room, agent_id))
    conn.commit()
    row = conn.execute("SELECT * FROM notifications WHERE id = ?",
                       (cur.lastrowid,)).fetchone()
    broker.publish("notification", row, run_id=None)
    return row


def request_stop(run_id: int) -> dict:
    """Terminate the engine subprocess for an in-flight run and mark the run
    `stopped`, keeping the partial stage events (PENDING-ROSTER J). Distinct
    from the gate flow: stop ABANDONS, a gate corrects and relaunches with
    lineage. Returns the run row. Idempotent; a finished run is untouched."""
    conn = db.get_db()
    run = get_run(run_id)
    if run is None:
        raise ValueError(f"no such run: {run_id}")
    if run["status"] not in ("queued", "running"):
        return run
    with _PROC_LOCK:
        _STOP_REQUESTED.add(run_id)
        proc = _PROCS.get(run_id)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
        # execute_run's own loop observes the exit and writes the final
        # status; wait briefly so the caller sees it, then fall back to a
        # direct write if that thread is not the one running this row
        # (e.g. the process was never ours).
        for _ in range(60):
            time.sleep(0.05)
            row = get_run(run_id)
            if row["status"] not in ("queued", "running"):
                return row
        try:
            proc.kill()
        except OSError:
            pass
        for _ in range(40):
            time.sleep(0.05)
            row = get_run(run_id)
            if row["status"] not in ("queued", "running"):
                return row
    conn.execute("UPDATE runs SET status = 'stopped', finished_at = ? "
                 "WHERE id = ? AND status IN ('queued', 'running')",
                 (_now(), run_id))
    conn.commit()
    row = get_run(run_id)
    broker.publish("run_status", row, run_id=run_id)
    return row


def place_stage_artefacts(out_dir, assumptions_path: str | None = None) -> dict:
    """Split what the engine wrote into the two stage directories.

    `engine/run.py` is deliberately unchanged — it writes every artefact into
    the single `--out` directory, which the bridge points at `vN/pricing/`.
    This moves the ESG-stage artefacts across into `vN/esg/` and copies the
    assumptions the run actually used in beside them as
    `assumptions_used.yaml`, so the ESG side is self-describing: the inputs
    the shocks came from and the shocks themselves.

    Idempotent, and a no-op for a flat directory that has no `vN/` root
    (temp sensitivity runs keep the engine's own single-directory shape).
    Returns {"moved": [...], "assumptions_used": path|None}.
    """
    root = run_root(out_dir)
    if _VERSION_DIR_RE.match(root.name) is None:
        return {"moved": [], "assumptions_used": None}
    esg = root / ESG_STAGE
    esg.mkdir(parents=True, exist_ok=True)
    src_dir = Path(str(out_dir))
    moved = []
    for name in ESG_ARTEFACTS:
        if name == ASSUMPTIONS_USED_NAME:
            continue                      # placed below, not moved
        src = src_dir / name
        if src.exists():
            shutil.move(str(src), str(esg / name))
            moved.append(name)
    used = None
    if assumptions_path and Path(assumptions_path).exists():
        used = esg / ASSUMPTIONS_USED_NAME
        shutil.copyfile(assumptions_path, used)
    return {"moved": moved, "assumptions_used": str(used) if used else None}


def execute_run(run_id: int) -> dict:
    """Run the engine subprocess for a queued run; blocks until finished.

    Intended to be called from a FastAPI background task (worker thread).
    """
    conn = db.get_db()
    run = get_run(run_id)
    if run is None:
        raise ValueError(f"no such run: {run_id}")
    manifest = read_manifest(run["out_dir"]) or {}
    agents_api = _agents_api()
    pace = config.engine_pace_seconds()

    with _PROC_LOCK:
        if run_id in _STOP_REQUESTED:
            # Stopped while still queued: never start the subprocess.
            _STOP_REQUESTED.discard(run_id)
            conn.execute("UPDATE runs SET status = 'stopped', finished_at = ? "
                         "WHERE id = ?", (_now(), run_id))
            conn.commit()
            row = get_run(run_id)
            broker.publish("run_status", row, run_id=run_id)
            return row

    conn.execute("UPDATE runs SET status = 'running', started_at = ? "
                 "WHERE id = ?", (_now(), run_id))
    conn.commit()
    broker.publish("run_status", get_run(run_id), run_id=run_id)

    stage_log = stage_log_path(run["out_dir"])
    stage_log.parent.mkdir(parents=True, exist_ok=True)
    if stage_log.exists():
        stage_log.unlink()

    assumptions_used = manifest.get(
        "assumptions_path",
        str(config.ASSUMPTIONS_DIR / f"{_month_key(run['asof'])}.yaml"))
    cmd = [
        _python_exe(), "-m", "engine.run",
        "--assumptions", assumptions_used,
        "--book", manifest.get("book_path", str(config.BOOK_PATH)),
        "--liabilities", manifest.get("liabilities_path",
                                      str(config.LIABILITIES_PATH)),
        "--out", str(run["out_dir"]),
        "--sims", str(run["sims"] or config.DEFAULT_SIMS),
        "--seed", str(run["seed"] or config.DEFAULT_SEED),
        "--stage-log", str(stage_log),
    ]
    proc = subprocess.Popen(cmd, cwd=str(config.PROJECT_ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    with _PROC_LOCK:
        _PROCS[run_id] = proc

    state = {"last_stage": "setup", "emitted": 0, "buf": "", "fh": None}

    def _drain() -> int:
        """Read any new complete lines from the stage log; emit each."""
        if state["fh"] is None:
            if not stage_log.exists():
                return 0
            state["fh"] = open(stage_log, "r", encoding="utf-8")
        state["buf"] += state["fh"].read()
        n = 0
        while "\n" in state["buf"]:
            line, state["buf"] = state["buf"].split("\n", 1)
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if state["emitted"] and pace > 0:
                time.sleep(pace)  # documented watchability pacing
            state["last_stage"] = ev.get("stage", state["last_stage"])
            _emit_stage_event(run_id, ev, agents_api)
            state["emitted"] += 1
            n += 1
        return n

    try:
        while proc.poll() is None:
            if _drain() == 0:
                time.sleep(0.05)
        while _drain() > 0:  # final drain after process exit
            pass
    finally:
        if state["fh"] is not None:
            state["fh"].close()

    stdout, stderr = proc.communicate()
    with _PROC_LOCK:
        _PROCS.pop(run_id, None)
        stopped = run_id in _STOP_REQUESTED
        _STOP_REQUESTED.discard(run_id)
    ok = proc.returncode == 0 and not stopped
    if ok:
        # Lay the run out across esg/ and pricing/ (PENDING-BATCH2 section 1).
        # Only on success: a failed or stopped run keeps whatever partial
        # files the engine left exactly where it left them.
        try:
            place_stage_artefacts(run["out_dir"], assumptions_used)
        except OSError as e:                       # pragma: no cover - IO
            _emit_stage_event(run_id, {
                "stage": "validation", "status": "failed",
                "detail": f"could not lay out run stages: {e}",
            }, agents_api)
            ok = False
    if stopped:
        # Abandoned deliberately: keep every stage event already recorded
        # (the record of what happened is itself useful) and say so.
        _emit_stage_event(run_id, {
            "stage": state["last_stage"], "status": "failed",
            "stopped": True,
            "detail": "run stopped by a human; engine subprocess terminated",
        }, agents_api)
    elif not ok:
        _emit_stage_event(run_id, {
            "stage": state["last_stage"], "status": "failed",
            "returncode": proc.returncode,
            "stderr": (stderr or "")[-2000:],
        }, agents_api)
    conn.execute("UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
                 ("stopped" if stopped else "done" if ok else "failed",
                  _now(), run_id))
    conn.commit()
    row = get_run(run_id)
    broker.publish("run_status", row, run_id=run_id)
    return row
