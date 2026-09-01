"""Tool registry (SPEC-APP section 4) — the agents' only reach into the world.

All tools are read-only except `run_sensitivity` (engine rerun into a temp
dir) and `propose_rerun` (creates a pending gate; never executes anything).
Every call is recorded in `tool_calls` with JSON-able args and results.

Access guards:
- File reads are confined to outputs/, assumptions/, book/, data/processed/
  and scenarios/{reference,seeded} non-ground-truth files.
- `scenarios/seeded/ground_truth.yaml` is NEVER readable through any tool
  (SPEC-APP section 2: defects are found by the checks, or not at all).

Budget: a ToolSession enforces MAX_TOOL_CALLS_PER_POST per post and at most
2 `run_sensitivity` invocations per post.
"""

from __future__ import annotations

import datetime
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from app import config
from app.server import db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
ASSUMPTIONS_DIR = PROJECT_ROOT / "assumptions"
BOOK_DIR = PROJECT_ROOT / "book"
DATA_DIR = PROJECT_ROOT / "data" / "processed"
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"

SENSITIVITY_SIMS = 5000
MAX_SENSITIVITY_PER_POST = 2
MAX_DELTA_NORMAL_PER_POST = 2
MAX_SERIES_ROWS = 600

DATA_SERIES = {"gbp_swap", "gbp_gilt", "ust", "credit_oas", "equity", "fx"}
# percent-quoted series get /100 in recompute_vol; equity/fx are levels
PERCENT_SERIES = {"gbp_swap", "gbp_gilt", "ust", "credit_oas"}


# A model reasonably guesses a parameter name — `asof` for `asof_or_run`,
# `month` for `asof`. Failing the whole post over a near-miss is a bad
# trade: the intent is unambiguous, and a live agent that crashes on a
# synonym looks broken rather than careful. Aliases are one-way and never
# overwrite a correctly-named argument.
_ARG_ALIASES = {
    "asof_or_run": ("asof", "month", "run", "run_id", "as_of"),
    "asof": ("month", "as_of", "date"),
    "filename": ("file", "name", "path"),
    "path": ("file", "filename"),
    "series": ("name", "series_name"),
    "column": ("col", "field"),
    "window_days": ("window", "days"),
    "run_a": ("run", "run_id", "asof"),
    "run_b": ("prev", "prev_run", "compare_to"),
    "shocks": ("shock", "shock_json", "moves"),
    "where": ("filter", "condition", "query"),
}


def _canonical_args(tool: str, args: dict) -> dict:
    spec = next((t for t in TOOL_SPECS if t["name"] == tool), None)
    if not spec:
        return args
    props = set((spec.get("input_schema") or {}).get("properties") or {})
    out = dict(args)
    for canon, aliases in _ARG_ALIASES.items():
        if canon in props and canon not in out:
            for a in aliases:
                if a in out:
                    out[canon] = out.pop(a)
                    break
    # drop anything the tool does not accept rather than TypeError on it
    return {k: v for k, v in out.items() if k in props} if props else out


class ToolError(Exception):
    """A tool refused or failed; JSON-able message."""


class ToolLimitError(ToolError):
    """Per-post tool budget exhausted."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _guard_path(p: Path) -> Path:
    p = p.resolve()
    if p.name == "ground_truth.yaml" or "ground_truth" in p.name:
        raise ToolError("access to ground truth is forbidden for agents")
    allowed = [OUTPUTS_DIR, ASSUMPTIONS_DIR, BOOK_DIR, DATA_DIR,
               SCENARIOS_DIR / "reference", SCENARIOS_DIR / "seeded",
               Path(tempfile.gettempdir())]
    # Path.is_relative_to, not str.startswith: a sibling directory such as
    # outputs_evil/ must NOT pass the outputs/ prefix (security audit note).
    if not any(p == a.resolve() or p.is_relative_to(a.resolve())
               for a in allowed):
        raise ToolError(f"path outside the agent-readable areas: {p}")
    return p


# --------------------------------------------------------------------------
# run / path resolution
# --------------------------------------------------------------------------

def _get_run(run_id: int) -> dict:
    row = db.get_db().execute("SELECT * FROM runs WHERE id = ?",
                              (int(run_id),)).fetchone()
    if row is None:
        raise ToolError(f"no such run: {run_id}")
    return row


def _is_run_ref(x) -> bool:
    return isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit())


def resolve_out_dir(asof_or_run) -> Path:
    """Run id -> that run's out_dir; 'YYYY-MM'/'YYYY-MM-DD' -> the committed
    month's base run `outputs/<YYYY_MM>/v1/pricing/`; '<YYYY_MM>/vN' or
    '<YYYY_MM>_vN' -> that version; 'attr_A__B' -> the attribution dir.

    (PENDING-BATCH2 section 1 — month/version/stage. `v1` is the month's
    base run by construction, so a bare month key still means "the committed
    run for that month"; `artifact_path` finds a named file on whichever
    side of the run holds it.)"""
    if _is_run_ref(asof_or_run):
        run = _get_run(int(asof_or_run))
        if not run["out_dir"]:
            raise ToolError(f"run {run['id']} has no out_dir yet")
        # A stored out_dir may be an ABSOLUTE path from the machine that
        # produced the run — a saved cycle carries them verbatim, so a judge
        # cloning the repo would get C:\Users\<someone-else>\... and nothing
        # would resolve. Re-root anything under an "outputs" segment onto
        # THIS checkout's OUTPUTS_DIR; absolute paths that already exist are
        # left alone.
        d = Path(run["out_dir"])
        try:
            return _guard_path(d)          # recorded path is valid here
        except ToolError:
            # It is not: the run was produced on another machine, so the
            # path is absolute and points outside this checkout. Re-root it
            # onto THIS checkout's outputs tree and try again.
            parts = d.parts
            if "outputs" in parts:
                tail = parts[parts.index("outputs") + 1:]
                if tail:
                    return _guard_path(Path(OUTPUTS_DIR).joinpath(*tail))
            raise
    key = str(asof_or_run).strip().replace("\\", "/").strip("/")
    if key.startswith("attr_"):
        return _guard_path(OUTPUTS_DIR / key)
    m = re.match(r"^(\d{4})[-_](\d{2})(?:[/_]v(\d+))?$", key)
    if m:
        return _guard_path(OUTPUTS_DIR / f"{m.group(1)}_{m.group(2)}"
                           / f"v{m.group(3) or 1}" / "pricing")
    return _guard_path(OUTPUTS_DIR / f"{key[:4]}_{key[5:7]}" / "v1" / "pricing")


def out_dir_name(d) -> str:
    """A readable name for an out dir: '2026_03/v1' inside the month/version
    layout (naming the RUN, not the stage subdirectory it happened to be
    resolved to), else the directory's own name — attribution dirs and the
    temp dirs run_sensitivity writes."""
    from app.server import engine_bridge  # noqa: PLC0415 (leaf; no cycle)
    month, _version, root = engine_bridge.parse_run_dir(d)
    if month is None:
        return Path(str(d)).name
    return f"{root.parent.name}/{root.name}"


def run_input_paths(run: dict) -> dict:
    """The input files a run actually used: inputs/manifest.json when the
    app created the run, else the engine's own valuation.json metadata,
    else the committed defaults for the run's month."""
    out_dir = Path(run["out_dir"]) if run.get("out_dir") else None
    res = {
        "assumptions_path": str(ASSUMPTIONS_DIR / f"{str(run['asof'])[:7]}.yaml"),
        "book_path": str(BOOK_DIR / "positions.json"),
        "liabilities_path": str(BOOK_DIR / "liabilities.json"),
        "seeded": False,
    }
    if out_dir is None:
        return res
    from app.server import engine_bridge  # noqa: PLC0415 (leaf; no cycle)
    man_p = engine_bridge.manifest_path(out_dir)   # run root, not stage dir
    if man_p.exists():
        with open(man_p, "r", encoding="utf-8") as f:
            man = json.load(f)
        for k in ("assumptions_path", "book_path", "liabilities_path"):
            if man.get(k):
                res[k] = man[k]
        res["seeded"] = bool(man.get("seeded"))
        return res
    val_p = out_dir / "valuation.json"
    if val_p.exists():
        with open(val_p, "r", encoding="utf-8") as f:
            meta = json.load(f).get("meta", {})
        for src, dst in (("assumptions_path", "assumptions_path"),
                         ("book_path", "book_path"),
                         ("liabilities_path", "liabilities_path")):
            if meta.get(src):
                res[dst] = meta[src]
        res["seeded"] = any("seeded" in str(res[k]) for k in
                            ("assumptions_path", "book_path"))
    return res


def _abspath(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (PROJECT_ROOT / q)


def _maybe_num(s: str):
    try:
        return float(s)
    except (TypeError, ValueError):
        return s


# --------------------------------------------------------------------------
# the tools
# --------------------------------------------------------------------------

def read_output(asof_or_run, filename: str) -> dict:
    """JSONs/CSVs (and .md) from a run's out dir or a committed outputs dir.
    Resolves across the run's esg/ and pricing/ stages."""
    from app.server import engine_bridge  # noqa: PLC0415 (leaf; no cycle)
    d = resolve_out_dir(asof_or_run)
    name = Path(str(filename)).name  # no traversal
    p = _guard_path(engine_bridge.artifact_path(d, name))
    if p.suffix not in (".json", ".csv", ".md"):
        raise ToolError(f"read_output serves .json/.csv/.md only, not {name}")
    if not p.exists():
        raise ToolError(f"no such output file: {out_dir_name(d)}/{name}")
    if p.suffix == ".json":
        with open(p, "r", encoding="utf-8") as f:
            return {"dir": out_dir_name(d), "file": name, "data": json.load(f)}
    if p.suffix == ".csv":
        with open(p, "r", encoding="utf-8") as f:
            import csv as _csv
            rows = [{k: _maybe_num(v) for k, v in r.items()}
                    for r in _csv.DictReader(f)]
        return {"dir": out_dir_name(d), "file": name, "n_rows": len(rows), "rows": rows}
    return {"dir": out_dir_name(d), "file": name, "text": p.read_text(encoding="utf-8")}


def read_assumptions(asof_or_run) -> dict:
    """Assumptions YAML for a month key, or the file a given run actually
    used (seeded/derived files resolve through the run's manifest)."""
    if _is_run_ref(asof_or_run):
        run = _get_run(int(asof_or_run))
        p = _abspath(run_input_paths(run)["assumptions_path"])
    else:
        p = ASSUMPTIONS_DIR / f"{str(asof_or_run)[:7]}.yaml"
    p = _guard_path(p)
    if not p.exists():
        raise ToolError(f"assumptions file not found: {p.name}")
    with open(p, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    doc.get("meta", {}).pop("sources", None)  # provenance prose, not data
    return {"file": p.name, "data": doc}


def read_book(asof_or_run=None) -> dict:
    """The position file — the committed book by default, or the book a
    given run actually used (seeded books resolve through the manifest)."""
    if asof_or_run is not None and _is_run_ref(asof_or_run):
        p = _abspath(run_input_paths(_get_run(int(asof_or_run)))["book_path"])
    else:
        p = BOOK_DIR / "positions.json"
    p = _guard_path(p)
    with open(p, "r", encoding="utf-8") as f:
        return {"file": p.name, "data": json.load(f)}


def read_liabilities() -> dict:
    p = _guard_path(BOOK_DIR / "liabilities.json")
    with open(p, "r", encoding="utf-8") as f:
        return {"file": p.name, "data": json.load(f)}


def read_data_series(series: str, start: str | None = None,
                     end: str | None = None) -> dict:
    """Daily rows from data/processed/<series>.csv between ISO dates."""
    if series not in DATA_SERIES:
        raise ToolError(f"unknown series {series!r}; one of {sorted(DATA_SERIES)}")
    p = _guard_path(DATA_DIR / f"{series}.csv")
    df = pd.read_csv(p)
    if start:
        df = df[df["date"] >= str(start)]
    if end:
        df = df[df["date"] <= str(end)]
    truncated = len(df) > MAX_SERIES_ROWS
    if truncated:
        df = df.tail(MAX_SERIES_ROWS)
    rows = df.to_dict(orient="records")
    return {"series": series, "columns": list(df.columns), "start": start,
            "end": end, "n_rows": len(rows), "truncated": truncated,
            "rows": rows}


def recompute_vol(series: str, column: str, asof: str,
                  window_days: int = 504) -> dict:
    """Deterministic vol recalculation matching calibration/calibrate.py:
    levels up to the last business day <= asof; percent -> decimal for
    rate/spread series; daily changes = absolute diffs (rates/spreads) or
    proportional returns (equity/fx); stdev(ddof=1) over the trailing
    `window_days` changes, annualized x sqrt(252), rounded to 6dp."""
    if series not in DATA_SERIES:
        raise ToolError(f"unknown series {series!r}; one of {sorted(DATA_SERIES)}")
    p = _guard_path(DATA_DIR / f"{series}.csv")
    df = pd.read_csv(p, parse_dates=["date"], index_col="date")
    if column not in df.columns:
        raise ToolError(f"{series}.csv has no column {column!r} "
                        f"(columns: {list(df.columns)})")
    s = df[column]
    if series in PERCENT_SERIES:
        s = s / 100.0
    upto = s.loc[:pd.Timestamp(str(asof))]
    if upto.empty:
        raise ToolError(f"no data on or before {asof}")
    window_days = int(window_days)
    tail = upto.tail(window_days + 1)
    if len(tail) < window_days + 1:
        raise ToolError(f"only {len(tail) - 1} daily changes available <= "
                        f"{asof}; need {window_days}")
    if series in PERCENT_SERIES:
        changes = tail.diff().iloc[1:]
        change_kind = "absolute daily changes of decimal levels"
    else:
        changes = tail.pct_change().iloc[1:]
        change_kind = "proportional daily returns"
    vol = float(changes.std(ddof=1) * math.sqrt(252.0))
    return {
        "series": series, "column": column, "asof": str(asof),
        "asof_used": str(tail.index[-1].date()),
        "window_days": window_days,
        "window_start": str(changes.index[0].date()),
        "window_end": str(changes.index[-1].date()),
        "change_kind": change_kind,
        "annualization": "sqrt(252)",
        "vol_annualized": round(vol, 6),
        "vol_annualized_raw": vol,
    }


_OPS = {
    "eq": lambda d, tol_ok: tol_ok,
    "approx": lambda d, tol_ok: tol_ok,
    "ne": lambda d, tol_ok: not tol_ok,
    "lt": lambda d, tol_ok: d < 0,
    "le": lambda d, tol_ok: d < 0 or tol_ok,
    "gt": lambda d, tol_ok: d > 0,
    "ge": lambda d, tol_ok: d > 0 or tol_ok,
}


def verify_claim(left, op: str, right, tol: float = 0.005) -> dict:
    """Numeric comparison; also the derivation vehicle — the result carries
    difference / rel_diff / ratio so derived figures quoted in posts can be
    bound to this call."""
    if op not in _OPS:
        raise ToolError(f"unknown op {op!r}; one of {sorted(_OPS)}")
    left_f, right_f = float(left), float(right)
    diff = left_f - right_f
    rel = abs(diff) / max(abs(right_f), 1e-12)
    tol_ok = abs(diff) <= float(tol) * max(abs(right_f), 1e-12)
    return {
        "left": left_f, "right": right_f, "op": op, "tol": float(tol),
        "passed": bool(_OPS[op](diff, tol_ok)),
        "difference": diff,
        "abs_difference": abs(diff),
        "rel_diff": rel,
        "rel_diff_pct": rel * 100.0,
        "ratio": (left_f / right_f) if right_f != 0 else None,
    }


def read_research(asof, agent: str = "focused",
                  data_through: str | None = None) -> dict:
    """The research note for a month-end (SPEC-APP 5.1), READ from disk. `agent`: "focused" (default; factor-block
    research from data/processed/*.csv, never assumptions/ or engine
    outputs — an assumptions-vs-research mismatch is evidence of an error
    between them) or "wide-eye" (mock stub — wider risks need live web
    search). Accepts 'YYYY-MM', an ISO date, or a run id (a run's research
    is its month's — seeded inputs cannot touch it). `data_through`
    advances the window past month-end for a fresh snapshot (SPEC-APP E)
    without persisting the note. Returns the markdown and the underlying
    stats so every figure an agent quotes from the note binds to this
    call. Available in rooms 1 and 3."""
    from app.agents import research  # leaf module; no circular import
    key = str(asof).strip()
    if _is_run_ref(key):
        key = str(_get_run(int(key))["asof"])[:7]
    # Compute to a SCRATCH directory, never over the real note. This used
    # to call generate_note() with its default out_dir, which rewrites
    # outputs/research/<month>_<agent>.md — so every agent that merely READ
    # the research silently replaced a live, web-researched note with a
    # freshly computed one carrying no web context. Reading a document must
    # not author it.
    import tempfile  # noqa: PLC0415
    try:
        note = research.generate_note(
            key, agent=agent, data_through=data_through,
            out_dir=Path(tempfile.mkdtemp(prefix="read_research_")))
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(f"read_research: {e}")

    # Prefer what the research stage actually published: that note carries
    # the web research, which a recomputation here cannot reproduce.
    if data_through is None:
        published = research.RESEARCH_DIR / f"{note['month'].replace('-', '_')}_{agent}.md"
        if published.is_file():
            try:
                note = dict(note)
                note["markdown"] = published.read_text(encoding="utf-8")
                note["path"] = str(published)
                # Reload the independently-sourced levels from the sidecar the
                # research stage wrote — the recomputed note above has no web
                # context, so without this the room-1 pass sees no levels to
                # compare and cannot bind to them.
                side = published.parent / (published.name + ".levels.json")
                if side.is_file():
                    data = json.loads(side.read_text(encoding="utf-8"))
                    note["stats"] = dict(note["stats"])
                    meta = dict(note["stats"].get("meta") or {})
                    meta["independent_levels"] = data.get(
                        "independent_levels") or {}
                    meta["independent_unsourced"] = data.get(
                        "independent_unsourced") or []
                    note["stats"]["meta"] = meta
            except (OSError, ValueError):
                pass
    return {"file": Path(note["path"]).name if note["path"] else None,
            "agent": note["stats"]["meta"].get("agent", "focused"),
            "month": note["month"], "asof": note["asof"],
            "prev_asof": note["prev_asof"], "markdown": note["markdown"],
            "stats": note["stats"]}


def read_agent_posts(room: int, handle: str, run_id=None,
                     snapshot_id=None) -> dict:
    """Another agent's published posts for the active run/snapshot
    (SPEC-APP section H — agents reading other agents). Each returned
    claim retains its ORIGINAL tool_call_id: a citing agent binds its own
    claim to that same id, so provenance chains to the executed tool call,
    never to "another agent said so." Context posts (the @wide-eye
    quarantine) come back with an empty claims list by construction, so
    the quarantine cannot be laundered through this tool (rule 2 of H)."""
    conn = db.get_db()
    handle = str(handle).strip()
    if not handle.startswith("@"):
        handle = "@" + handle
    agent_row = conn.execute("SELECT id FROM agents WHERE handle = ?",
                             (handle,)).fetchone()
    if agent_row is None:
        raise ToolError(f"no such agent: {handle}")
    q = ("SELECT * FROM posts WHERE agent_id = ? AND room = ? AND "
         "status = 'published' AND type IN ('origin', 'expansion')")
    args = [agent_row["id"], int(room)]
    if snapshot_id is not None:
        q += " AND snapshot_id = ?"
        args.append(int(snapshot_id))
    elif run_id is not None:
        q += " AND run_id = ?"
        args.append(int(run_id))
    q += " ORDER BY id"
    rows = conn.execute(q, args).fetchall()
    posts = [{"id": r["id"], "type": r["type"], "body_md": r["body_md"],
             "significance": r.get("significance"),
             "claims": (json.loads(r["claims_json"])
                        if r["claims_json"] else []),
             "created_at": r["created_at"]}
             for r in rows]
    return {"room": int(room), "handle": handle, "n_posts": len(posts),
            "posts": posts}


def read_reference(filename: str) -> dict:
    """Bundled reference/scenario documents agents are sanctioned to read:
    scenarios/reference/* and scenarios/seeded/ *.md / *.csv / *.json /
    *.yaml inputs — NEVER ground truth (guarded)."""
    name = Path(str(filename)).name
    for base in (SCENARIOS_DIR / "reference", SCENARIOS_DIR / "seeded"):
        p = base / name
        if p.exists():
            p = _guard_path(p)
            if p.suffix == ".csv":
                with open(p, "r", encoding="utf-8") as f:
                    import csv as _csv
                    rows = [{k: _maybe_num(v) for k, v in r.items()}
                            for r in _csv.DictReader(f)]
                return {"file": name, "n_rows": len(rows), "rows": rows}
            if p.suffix == ".json":
                with open(p, "r", encoding="utf-8") as f:
                    return {"file": name, "data": json.load(f)}
            if p.suffix in (".yaml", ".yml"):
                with open(p, "r", encoding="utf-8") as f:
                    return {"file": name, "data": yaml.safe_load(f)}
            return {"file": name, "text": p.read_text(encoding="utf-8")}
    raise ToolError(f"no such reference file: {name}")


def _delta_inputs(ref) -> dict:
    """Resolve one delta_normal argument (run id, 'YYYY-MM', or an outputs
    key such as '2026_03/v2') to loaded engine inputs + the out dir the
    simulated VaR is read from."""
    key = str(ref).strip()
    if _is_run_ref(key):
        run = _get_run(int(key))
        paths = run_input_paths(run)
        a_p = _guard_path(_abspath(paths["assumptions_path"]))
        b_p = _guard_path(_abspath(paths["book_path"]))
        out_dir = resolve_out_dir(int(key))
        label = f"run {run['id']} (asof {str(run['asof'])[:7]})"
    else:
        a_p = _guard_path(ASSUMPTIONS_DIR / f"{key[:7]}.yaml")
        b_p = _guard_path(BOOK_DIR / "positions.json")
        out_dir = resolve_out_dir(key)
        label = key
    if not a_p.exists():
        raise ToolError(f"assumptions file not found: {a_p.name}")
    if not b_p.exists():
        raise ToolError(f"book file not found: {b_p.name}")
    with open(a_p, "r", encoding="utf-8") as f:
        assumptions = yaml.safe_load(f)
    with open(b_p, "r", encoding="utf-8") as f:
        book = json.load(f)
    with open(_guard_path(BOOK_DIR / "liabilities.json"), "r",
              encoding="utf-8") as f:
        liabilities = json.load(f)
    simulated = None
    agg_p = out_dir / "var_aggregate.json"
    if agg_p.exists():
        with open(agg_p, "r", encoding="utf-8") as f:
            simulated = float(json.load(f)["aggregate_var_gbp"])
    return {"assumptions": assumptions, "positions": book["positions"],
            "liabilities": liabilities,
            "ref_index_levels": book.get("ref_index_levels"),
            "simulated_var_gbp": simulated, "label": label,
            "assumptions_file": a_p.name, "book_file": b_p.name}


def _delta_side(inp: dict) -> dict:
    """single_run analytics + the (w, vols, corr) triple for pair steps."""
    from app.agents import delta_normal as _dn  # leaf module (engine only)
    from engine import esg as _esg  # deterministic engine code, read-only

    w, base = _dn.factor_exposures(inp["assumptions"], inp["positions"],
                                   inp["liabilities"],
                                   inp["ref_index_levels"])
    vols = _esg.vol_vector(inp["assumptions"])
    corr = _esg.correlation_matrix(inp["assumptions"])
    single = _dn.analytics(w, vols, corr)
    single["base_surplus_gbp"] = base
    sim = inp["simulated_var_gbp"]
    single["simulated_var_gbp"] = sim
    if sim:
        gap = single["aggregate_var_gbp"] - sim
        single["approximation_gap_gbp"] = gap
        single["approximation_gap_pct"] = gap / sim * 100.0
    else:
        single["approximation_gap_gbp"] = None
        single["approximation_gap_pct"] = None
    single["exposures_gbp_per_unit_shock"] = {
        _esg.FACTOR_ORDER[i]: float(w[i]) for i in range(_esg.N_FACTORS)}
    return {"single": single, "w": w, "vols": vols, "corr": corr}


def delta_normal(run_a, run_b=None) -> dict:
    """Deterministic analytic helper (SPEC-APP section 4): bump-delta factor
    exposures, closed-form aggregate 2.576*sqrt(w'Sigma w), Euler component
    VaR (summing to the total), diversification benefit, and the
    approximation gap vs the run's simulated VaR. With run_b: the delta-VaR
    split into exposure / vol / correlation movements by sequential
    substitution, naming the largest-moving correlation cells. Engine code,
    no simulation. Bounded: max 2 per post (enforced by ToolSession)."""
    from app.agents import delta_normal as _dn  # noqa: PLC0415

    inp_a = _delta_inputs(run_a)
    side_a = _delta_side(inp_a)
    out = {"run_a": inp_a["label"],
           "inputs_a": {"assumptions": inp_a["assumptions_file"],
                        "book": inp_a["book_file"]},
           "z": _dn.Z_995,
           "a": side_a["single"]}
    if run_b is not None:
        inp_b = _delta_inputs(run_b)
        side_b = _delta_side(inp_b)
        out["run_b"] = inp_b["label"]
        out["inputs_b"] = {"assumptions": inp_b["assumptions_file"],
                           "book": inp_b["book_file"]}
        out["b"] = side_b["single"]
        out["pair"] = _dn.pair_decomposition(
            {"w": side_a["w"], "vols": side_a["vols"],
             "corr": side_a["corr"]},
            {"w": side_b["w"], "vols": side_b["vols"],
             "corr": side_b["corr"]})
        sim_a = side_a["single"]["simulated_var_gbp"]
        sim_b = side_b["single"]["simulated_var_gbp"]
        if sim_a and sim_b:
            out["pair"]["simulated_delta_var_gbp"] = sim_b - sim_a
    return out


# --- adjustments (dotted paths and/or nested dicts) ------------------------

def _flatten(prefix: list[str], obj: dict, out: list) -> None:
    for k, v in obj.items():
        if isinstance(v, dict):
            _flatten(prefix + [str(k)], v, out)
        else:
            out.append((prefix + [str(k)], v))


def _resolve_key(node: dict, seg: str):
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
    leaves: list = []
    for k, v in (adjustments or {}).items():
        segs = str(k).split(".")
        if isinstance(v, dict):
            _flatten(segs, v, leaves)
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


def _engine_headlines(out_dir: Path) -> dict:
    with open(out_dir / "var_aggregate.json", "r", encoding="utf-8") as f:
        agg = json.load(f)
    with open(out_dir / "var_standalone_factors.json", "r",
              encoding="utf-8") as f:
        blocks = json.load(f)["blocks"]
    with open(out_dir / "valuation.json", "r", encoding="utf-8") as f:
        val = json.load(f)
    return {"aggregate_var_gbp": agg["aggregate_var_gbp"],
            "sum_standalone_blocks_gbp": agg["sum_standalone_blocks_gbp"],
            "blocks": blocks,
            "asset_total_gbp": val["asset_total_gbp"],
            "liability_pv_gbp": val["liability_pv_gbp"],
            "surplus_gbp": val["surplus_gbp"]}


def _run_engine(assumptions: Path, book: Path, liabilities: Path,
                out_dir: Path, sims: int, seed: int) -> None:
    exe = config.PYTHON_EXE or sys.executable
    cmd = [exe, "-m", "engine.run",
           "--assumptions", str(assumptions), "--book", str(book),
           "--liabilities", str(liabilities), "--out", str(out_dir),
           "--sims", str(sims), "--seed", str(seed)]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True,
                          text=True, timeout=300)
    if proc.returncode != 0:
        raise ToolError("engine sensitivity run failed: "
                        + (proc.stderr or "")[-500:])


def run_sensitivity(asof, shock_json) -> dict:
    """Engine rerun in a temp dir with a shocked input, sims=5000 — a
    deterministic engine output, not an AI number. Runs base AND shocked at
    the same sims/seed so deltas are apples-to-apples. Bounded to 2 per
    post (enforced by ToolSession)."""
    if isinstance(shock_json, str):
        shock = json.loads(shock_json)
    else:
        shock = dict(shock_json or {})
    if not shock:
        raise ToolError("shock_json must contain at least one adjustment")
    if _is_run_ref(asof):
        run = _get_run(int(asof))
        base_assumptions = _abspath(run_input_paths(run)["assumptions_path"])
        book = _abspath(run_input_paths(run)["book_path"])
    else:
        base_assumptions = ASSUMPTIONS_DIR / f"{str(asof)[:7]}.yaml"
        book = BOOK_DIR / "positions.json"
    base_assumptions = _guard_path(base_assumptions)
    if not base_assumptions.exists():
        raise ToolError(f"assumptions file not found: {base_assumptions.name}")
    liabilities = BOOK_DIR / "liabilities.json"

    tmp = Path(tempfile.mkdtemp(prefix="sensitivity_"))
    with open(base_assumptions, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    changes = apply_adjustments(doc, shock)
    shocked_yaml = tmp / "assumptions_shocked.yaml"
    with open(shocked_yaml, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(doc, f, sort_keys=False)

    seed = config.DEFAULT_SEED
    _run_engine(base_assumptions, book, liabilities, tmp / "base",
                SENSITIVITY_SIMS, seed)
    _run_engine(shocked_yaml, book, liabilities, tmp / "shocked",
                SENSITIVITY_SIMS, seed)
    base = _engine_headlines(tmp / "base")
    shocked = _engine_headlines(tmp / "shocked")
    deltas = {
        "aggregate_var_gbp": shocked["aggregate_var_gbp"] - base["aggregate_var_gbp"],
        "surplus_gbp": shocked["surplus_gbp"] - base["surplus_gbp"],
        "blocks": {k: shocked["blocks"][k] - base["blocks"][k]
                   for k in base["blocks"]},
    }
    return {"asof": str(asof), "sims": SENSITIVITY_SIMS, "seed": seed,
            "base_assumptions": base_assumptions.name,
            "shock": changes, "base": base, "shocked": shocked,
            "deltas": deltas, "out_dir": str(tmp)}


def propose_rerun(asof, adjustments_json, rationale: str,
                  run_id: int | None = None) -> dict:
    """Creates a PENDING gate. Never executes anything — the agent proposes,
    a named human disposes (SPEC-APP section 0.3)."""
    conn = db.get_db()
    if isinstance(adjustments_json, str):
        adjustments = json.loads(adjustments_json)
    else:
        adjustments = dict(adjustments_json or {})
    if not adjustments:
        raise ToolError("adjustments_json must contain at least one override")
    if run_id is None:
        row = conn.execute(
            "SELECT id FROM runs WHERE asof LIKE ? ORDER BY id DESC LIMIT 1",
            (str(asof)[:7] + "%",)).fetchone()
        if row is None:
            raise ToolError(f"no run found for asof {asof} to attach the gate to")
        run_id = row["id"]
    else:
        _get_run(int(run_id))
    cur = conn.execute(
        "INSERT INTO gates (run_id, adjustments_json, rationale, status) "
        "VALUES (?, ?, ?, 'pending')",
        (int(run_id), json.dumps(adjustments), str(rationale)))
    conn.commit()
    return {"gate_id": cur.lastrowid, "run_id": int(run_id),
            "status": "pending", "adjustments": adjustments,
            "rationale": str(rationale),
            "note": "pending human approval in the UI; nothing has run"}


# ==========================================================================
# Scenario tools (PENDING-ROSTER sections M and M.1)
#
# Four tools over the retained simulation (SPEC section 4 / PENDING-ROSTER
# section L) and the deterministic pricer:
#
#   read_scenario   one scenario at a loss rank (rank 250 of 50,000 = the
#                   99.5th percentile = *the* VaR scenario) or a raw index
#   tail_analysis   what happens across the worst n scenarios
#   price_scenario  deterministic repricing at factor movements you specify
#                   (no simulation — one revaluation, exact, milliseconds)
#   query_scenarios filter the saved 50,000 by factor/outcome conditions and
#                   return conditional statistics (reverse stress testing)
#
# Everything here is engine code (engine.esg / engine.pricing / engine.var)
# applied to files on disk, so a number quoted from one of these results is
# an engine number and binds like any other tool call.
# ==========================================================================

VAR_LEVEL = 0.995
DEFAULT_TAIL_N = 250            # the worst 250 of 50,000 = the 0.5% tail
MAX_TAIL_N = 5000               # bound on how much tail a single call reads
MAX_MATCH_ROWS_FOR_POSITIONS = 5000   # position stats sample cap
TOP_LOSERS = 10                 # positions listed in a scenario read-out
TOP_K_FREQ = 5                  # "top-5 losers" frequency (PENDING section M)
BUMP_BP = 1e-4                  # 1bp, for duration / exposure summaries
BUMP_PROP = 1e-2                # 1%, for equity and FX exposures

# Curve/rating/index names in SPEC section 2 order (mirrors engine.esg so the
# tool layer can talk about factors without importing at module scope).
_TENORS = (2, 5, 10, 20)
_CURVES = ("gbp_swap", "gbp_gilt", "ust")
_RATINGS = ("AA", "A", "BBB", "HY", "CCC")
_INDICES = ("FTSE100", "SP500", "SX5E")

# Blocks a shock key may address as a whole (SPEC section 4 block names plus
# the raw curve names).
_SHOCK_BLOCKS = {
    "gbp_swap": [f"gbp_swap_{t}" for t in _TENORS],
    "gbp_gilt": [f"gbp_gilt_{t}" for t in _TENORS],
    "ust": [f"ust_{t}" for t in _TENORS],
    "ir_gbp": [f"gbp_swap_{t}" for t in _TENORS]
              + [f"gbp_gilt_{t}" for t in _TENORS],
    "ir_usd": [f"ust_{t}" for t in _TENORS],
    "spread": [f"spread_{r}" for r in _RATINGS],
    "credit": [f"spread_{r}" for r in _RATINGS],
    "equity": [f"eq_{i}" for i in _INDICES],
    "fx": ["fx_GBPUSD"],
}


def _factor_order() -> list:
    from engine import esg as _esg  # noqa: PLC0415 (engine, read-only)
    return list(_esg.FACTOR_ORDER)


def _canonical_factor(name: str) -> str:
    """Accept the SPEC names plus the friendlier spellings agents reach for
    ('equity_FTSE100' -> 'eq_FTSE100', 'gbp_swap_10y' -> 'gbp_swap_10')."""
    key = str(name).strip()
    order = _factor_order()
    if key in order:
        return key
    if key.startswith("equity_") and "eq_" + key[7:] in order:
        return "eq_" + key[7:]
    if key.endswith("y") and key[:-1] in order:
        return key[:-1]
    if key.startswith("fx") and "fx_GBPUSD" in order and key in (
            "fx", "fx_gbpusd", "FX", "GBPUSD", "fx_GBPUSD"):
        return "fx_GBPUSD"
    return key


def _factor_meta(assumptions: dict) -> list:
    """Per-factor base level, annual vol and how its shock applies."""
    from engine import esg as _esg  # noqa: PLC0415

    base = _esg.base_state(assumptions)
    vols = _esg.vol_vector(assumptions)
    out = []
    for i, name in enumerate(_esg.FACTOR_ORDER):
        if i < 12:
            curve = _CURVES[i // 4]
            level = float(base["curves"][curve][i % 4])
            kind, unit = "additive", "decimal rate"
        elif i < 17:
            level = float(base["spreads"][i - 12])
            kind, unit = "additive_floored", "decimal spread"
        elif i < 20:
            level = float(base["equity"][i - 17])
            kind, unit = "proportional", "index level"
        else:
            level = float(base["fx"])
            kind, unit = "proportional", "GBPUSD"
        out.append({"factor": name, "base_level": level,
                    "vol_annual": float(vols[i]), "shock_kind": kind,
                    "unit": unit})
    return out


def _shocked_level(meta: dict, z: float) -> float:
    if meta["shock_kind"] == "proportional":
        return float(meta["base_level"] * (1.0 + z))
    lvl = float(meta["base_level"] + z)
    if meta["shock_kind"] == "additive_floored":
        lvl = max(lvl, 0.0)
    return lvl


def _factor_rows(meta: list, shock: "np.ndarray") -> list:
    rows = []
    for i, m in enumerate(meta):
        z = float(shock[i])
        vol = float(m["vol_annual"])
        rows.append({
            "factor": m["factor"], "shock": z,
            "base_level": float(m["base_level"]),
            "shocked_level": _shocked_level(m, z),
            "vol_annual": vol,
            "shock_in_vols": (z / vol) if vol else None,
            "shock_kind": m["shock_kind"], "unit": m["unit"],
        })
    return rows


# --- input resolution ------------------------------------------------------

def _named_book_path(name: str, default: Path) -> Path:
    """A book/liabilities file by name: book/ first, then the sanctioned
    scenarios/seeded/ inputs. Never anything else (guarded)."""
    if name is None:
        return _guard_path(default)
    base = Path(str(name)).name
    for root in (BOOK_DIR, SCENARIOS_DIR / "seeded"):
        p = root / base
        if p.exists():
            return _guard_path(p)
    raise ToolError(f"no such book/liabilities file: {base} "
                    f"(looked in book/ and scenarios/seeded/)")


def _scenario_inputs(asof_or_run, book=None, liabilities=None) -> dict:
    """Assumptions + positions + liability cohorts for a month key or run id.

    `book` / `liabilities` optionally name a different file (by name, e.g.
    'positions_2026-03.json') — that is how a month-end PAIR with a book and
    liability change is repriced on one side's market state."""
    key = str(asof_or_run).strip()
    if _is_run_ref(key):
        run = _get_run(int(key))
        paths = run_input_paths(run)
        a_p = _guard_path(_abspath(paths["assumptions_path"]))
        b_p = _named_book_path(book, _abspath(paths["book_path"]))
        l_p = _named_book_path(liabilities,
                               _abspath(paths["liabilities_path"]))
        label = f"run {run['id']} (asof {str(run['asof'])[:7]})"
    else:
        a_p = _guard_path(ASSUMPTIONS_DIR / f"{key[:7]}.yaml")
        b_p = _named_book_path(book, BOOK_DIR / "positions.json")
        l_p = _named_book_path(liabilities, BOOK_DIR / "liabilities.json")
        label = key
    if not a_p.exists():
        raise ToolError(f"assumptions file not found: {a_p.name}")
    with open(a_p, "r", encoding="utf-8") as f:
        assumptions = yaml.safe_load(f)
    with open(b_p, "r", encoding="utf-8") as f:
        bk = json.load(f)
    with open(l_p, "r", encoding="utf-8") as f:
        liabs = json.load(f)
    return {"assumptions": assumptions, "book": bk,
            "positions": bk["positions"],
            "ref_index_levels": bk.get("ref_index_levels"),
            "liabilities": liabs, "label": label,
            "assumptions_file": a_p.name, "book_file": b_p.name,
            "liabilities_file": l_p.name}


def _load_sims(asof_or_run) -> tuple:
    """(out_dir, sim_index, surplus, factors, position P&L) — memory-mapped.

    The arrays straddle the two stages: `sim_factors.npy` / `sim_index.json`
    are ESG-stage artefacts, the P&L arrays are pricing-stage, so every path
    goes through `artifact_path` rather than a bare join."""
    from app.server import engine_bridge  # noqa: PLC0415 (leaf; no cycle)
    d = resolve_out_dir(asof_or_run)
    _art = lambda n: _guard_path(engine_bridge.artifact_path(d, n))  # noqa: E731
    idx_p = _art("sim_index.json")
    if not idx_p.exists():
        raise ToolError(
            f"no saved simulation arrays for {out_dir_name(d)} "
            "(sim_index.json missing) — the run was made with "
            "--no-save-sims, or predates simulation retention")
    with open(idx_p, "r", encoding="utf-8") as f:
        idx = json.load(f)
    try:
        surplus = np.load(str(_art("sim_surplus.npy")), mmap_mode="r")
        factors = np.load(str(_art("sim_factors.npy")), mmap_mode="r")
        pnl = np.load(str(_art("sim_pnl_positions.npy")), mmap_mode="r")
    except (OSError, ValueError) as e:
        raise ToolError(f"simulation arrays for {out_dir_name(d)} are unreadable: {e}")
    return d, idx, surplus, factors, pnl


def _reported_var(out_dir: Path) -> float | None:
    p = out_dir / "var_aggregate.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return float(json.load(f)["aggregate_var_gbp"])


def _position_names(inp: dict, columns: list) -> dict:
    """id -> (name, type) for the P&L columns, when the book matches."""
    meta = {p["id"]: p for p in inp["positions"]}
    return {c: meta.get(c) for c in columns}


def _mahalanobis(assumptions: dict, shock: "np.ndarray") -> dict:
    """How unusual this joint draw is UNDER THE CALIBRATION ITSELF:
    d^2 = z' C^-1 z with z the shock in vol units; d^2 ~ chi^2(21) if the
    correlation matrix generated it. The percentile is the joint-plausibility
    read @vlad needs — a VaR scenario that only reproduces the loss by
    combining draws the VCV says are jointly rare is a model finding."""
    from engine import esg as _esg  # noqa: PLC0415

    vols = _esg.vol_vector(assumptions)
    corr = _esg.correlation_matrix(assumptions)
    z = np.asarray(shock, dtype=float) / np.where(vols > 0, vols, 1.0)
    try:
        d2 = float(z @ np.linalg.solve(corr, z))
    except np.linalg.LinAlgError:
        return {"available": False}
    df = int(_esg.N_FACTORS)
    try:
        from scipy.stats import chi2  # noqa: PLC0415
        pct = float(chi2.cdf(max(d2, 0.0), df) * 100.0)
    except Exception:  # scipy absent: report the statistic without the CDF
        pct = None
    return {"available": True, "mahalanobis_d2": d2,
            "mahalanobis_d": float(np.sqrt(max(d2, 0.0))),
            "degrees_of_freedom": df,
            "chi2_expected_d2": float(df),
            "chi2_percentile": pct,
            "note": "d^2 = z'C^-1 z, z = shock / annual vol; under the "
                    "calibrated correlation d^2 ~ chi^2(21), mean 21."}


# --- read_scenario ---------------------------------------------------------

def read_scenario(run, rank=None, index=None) -> dict:
    """The scenario at a given LOSS RANK (rank 1 = worst; rank 250 of 50,000
    = the 99.5th percentile, i.e. *the* VaR scenario) or at a raw simulation
    index. Returns the 21 factor draws with their shocked levels and size in
    vol units, the per-position P&L, the surplus P&L, the position ranking by
    loss, spread-floor incidence, and a joint-plausibility statistic for the
    draw under the calibrated correlation."""
    d, idx, surplus, factors, pnl = _load_sims(run)
    n_sims = int(surplus.shape[0])
    if index is not None:
        i = int(index)
        if not 0 <= i < n_sims:
            raise ToolError(f"index {i} outside 0..{n_sims - 1}")
        loss_rank = int((np.asarray(surplus) < float(surplus[i])).sum()) + 1
    else:
        r = int(rank) if rank is not None else max(
            1, int(round((1.0 - VAR_LEVEL) * n_sims)))
        if not 1 <= r <= n_sims:
            raise ToolError(f"rank {r} outside 1..{n_sims}")
        order = np.argsort(np.asarray(surplus, dtype=float), kind="stable")
        i, loss_rank = int(order[r - 1]), r

    inp = _scenario_inputs(run)
    meta = _factor_meta(inp["assumptions"])
    shock = np.asarray(factors[i], dtype=float)
    frows = _factor_rows(meta, shock)

    columns = list(idx.get("pnl_columns") or [])
    row = np.asarray(pnl[i], dtype=float)
    names = _position_names(inp, columns)
    contributions = []
    for c, v in zip(columns, row):
        p = names.get(c)
        contributions.append({
            "id": c,
            "name": (p or {}).get("name") if p else
                    ("liabilities (all cohorts)" if c == "LIABILITIES"
                     else None),
            "type": (p or {}).get("type") if p else
                    ("liabilities" if c == "LIABILITIES" else None),
            "pnl_gbp": float(v)})
    ranked = sorted(contributions, key=lambda r_: r_["pnl_gbp"])

    floored = [f["factor"] for f in frows
               if f["shock_kind"] == "additive_floored"
               and f["base_level"] + f["shock"] < 0.0]
    surplus_pnl = float(surplus[i])
    reported = _reported_var(d)
    out = {
        "run": str(run), "out_dir": out_dir_name(d), "n_sims": n_sims,
        "index": i, "loss_rank": loss_rank,
        "loss_percentile": float(loss_rank) / n_sims,
        "is_var_scenario": loss_rank == max(
            1, int(round((1.0 - VAR_LEVEL) * n_sims))),
        "surplus_pnl_gbp": surplus_pnl,
        "loss_gbp": -surplus_pnl,
        "reported_aggregate_var_gbp": reported,
        "loss_vs_reported_var_gbp": (
            (-surplus_pnl) - reported if reported is not None else None),
        "assumptions_file": inp["assumptions_file"],
        "book_file": inp["book_file"],
        "factors": frows,
        "largest_draws_in_vols": sorted(
            [{"factor": f["factor"], "shock": f["shock"],
              "shock_in_vols": f["shock_in_vols"]}
             for f in frows if f["shock_in_vols"] is not None],
            key=lambda f: -abs(f["shock_in_vols"]))[:5],
        "positions_by_loss": ranked[:TOP_LOSERS],
        "positions_by_gain": ranked[::-1][:TOP_LOSERS],
        "position_pnl_gbp": {c["id"]: c["pnl_gbp"] for c in contributions},
        "liability_pnl_gbp": float(
            dict(zip(columns, row)).get("LIABILITIES", 0.0)),
        "asset_pnl_gbp": float(sum(
            v for c, v in zip(columns, row) if c != "LIABILITIES")),
        "spread_floor_bound": floored,
        "spread_floor_incidence": len(floored),
        "joint_plausibility": _mahalanobis(inp["assumptions"], shock),
    }
    return out


# --- tail_analysis ---------------------------------------------------------

def tail_analysis(run, quantile: float = VAR_LEVEL, n=None) -> dict:
    """Across the worst n scenarios (default: the tail beyond `quantile`):
    mean position contributions, factor draw distributions, how often each
    position lands in the top-5 losers, and spread-floor incidence."""
    d, idx, surplus, factors, pnl = _load_sims(run)
    n_sims = int(surplus.shape[0])
    q = float(quantile)
    if not 0.5 <= q < 1.0:
        raise ToolError(f"quantile must be in [0.5, 1.0), got {q}")
    n_tail = int(n) if n is not None else max(
        1, int(round((1.0 - q) * n_sims)))
    if n_tail < 1 or n_tail > min(n_sims, MAX_TAIL_N):
        raise ToolError(f"n must be 1..{min(n_sims, MAX_TAIL_N)}, got {n_tail}")

    s = np.asarray(surplus, dtype=float)
    order = np.argsort(s, kind="stable")[:n_tail]
    tail_pnl = np.asarray(pnl[np.sort(order)], dtype=float)
    tail_shocks = np.asarray(factors[np.sort(order)], dtype=float)
    tail_surplus = s[order]

    inp = _scenario_inputs(run)
    meta = _factor_meta(inp["assumptions"])
    columns = list(idx.get("pnl_columns") or [])
    names = _position_names(inp, columns)

    mean_contrib = tail_pnl.mean(axis=0)
    total_mean = float(mean_contrib.sum())
    contributions = []
    for c, v in zip(columns, mean_contrib):
        p = names.get(c)
        contributions.append({
            "id": c, "name": (p or {}).get("name") if p else None,
            "mean_pnl_gbp": float(v),
            "share_of_mean_tail_loss": (float(v) / total_mean
                                        if total_mean else None)})
    contributions.sort(key=lambda r_: r_["mean_pnl_gbp"])

    # top-5 loser frequency
    k = min(TOP_K_FREQ, tail_pnl.shape[1])
    worst_k = np.argpartition(tail_pnl, k - 1, axis=1)[:, :k]
    counts = np.bincount(worst_k.ravel(), minlength=len(columns))
    freq = sorted(
        ({"id": columns[j], "top5_count": int(counts[j]),
          "top5_frequency": float(counts[j]) / n_tail}
         for j in range(len(columns))),
        key=lambda r_: -r_["top5_count"])

    fac_stats = []
    for i, m in enumerate(meta):
        col = tail_shocks[:, i]
        vol = float(m["vol_annual"])
        fac_stats.append({
            "factor": m["factor"],
            "mean_shock": float(col.mean()),
            "mean_shock_in_vols": (float(col.mean()) / vol) if vol else None,
            "std_shock": float(col.std(ddof=1)) if n_tail > 1 else 0.0,
            "min_shock": float(col.min()), "max_shock": float(col.max()),
            "p5_shock": float(np.percentile(col, 5)),
            "p95_shock": float(np.percentile(col, 95)),
            "mean_shocked_level": _shocked_level(m, float(col.mean())),
            "vol_annual": vol, "shock_kind": m["shock_kind"],
        })

    per_rating = {}
    any_hit = np.zeros(n_tail, dtype=bool)
    for i, m in enumerate(meta):
        if m["shock_kind"] != "additive_floored":
            continue
        hit = (m["base_level"] + tail_shocks[:, i]) < 0.0
        per_rating[m["factor"]] = int(hit.sum())
        any_hit |= hit

    reported = _reported_var(d)
    return {
        "run": str(run), "out_dir": out_dir_name(d), "n_sims": n_sims,
        "quantile": q, "n_tail": n_tail,
        "tail_threshold_gbp": float(-tail_surplus.max()),
        "mean_tail_loss_gbp": float(-tail_surplus.mean()),
        "worst_loss_gbp": float(-tail_surplus.min()),
        "expected_shortfall_gbp": float(-tail_surplus.mean()),
        "reported_aggregate_var_gbp": reported,
        "assumptions_file": inp["assumptions_file"],
        "mean_position_contributions": contributions,
        "largest_mean_contributors": contributions[:TOP_LOSERS],
        "top5_loser_frequency": freq[:TOP_LOSERS],
        "factor_distributions": fac_stats,
        "factor_draws_by_mean_vols": sorted(
            [f for f in fac_stats if f["mean_shock_in_vols"] is not None],
            key=lambda f: -abs(f["mean_shock_in_vols"]))[:5],
        "spread_floor": {
            "scenarios_with_any_floor_bound": int(any_hit.sum()),
            "incidence_rate": float(any_hit.sum()) / n_tail,
            "per_rating_counts": per_rating,
        },
    }


# --- price_scenario --------------------------------------------------------

def _parse_shocks(shocks) -> tuple:
    """(shock vector (21,), expansion log). Absolute moves for rates and
    spreads, proportional for equity and FX; any subset, unspecified factors
    held at base."""
    if isinstance(shocks, str):
        shocks = json.loads(shocks)
    shocks = dict(shocks or {})
    order = _factor_order()
    pos = {name: i for i, name in enumerate(order)}
    vec = np.zeros(len(order))
    applied = {}
    for key, value in shocks.items():
        try:
            z = float(value)
        except (TypeError, ValueError):
            raise ToolError(f"shock for {key!r} must be a number, got "
                            f"{value!r}")
        canon = _canonical_factor(key)
        if canon in pos:
            targets = [canon]
        elif str(key).strip() in _SHOCK_BLOCKS:
            targets = _SHOCK_BLOCKS[str(key).strip()]
        else:
            raise ToolError(
                f"unknown shock key {key!r}; use a factor name "
                f"({', '.join(order[:4])}, ... spread_HY, eq_FTSE100, "
                f"equity_FTSE100, fx_GBPUSD) or a block name "
                f"({', '.join(sorted(_SHOCK_BLOCKS))})")
        for t in targets:
            vec[pos[t]] += z
        applied[str(key)] = {"value": z, "factors": targets}
    return vec, applied


def _sleeve(p: dict) -> str:
    if p.get("asset_class") == "private_credit":
        return "private_credit"
    return str(p.get("type", "other"))


def market_delta_shocks(base_assumptions: dict, to_assumptions: dict) -> dict:
    """A whole month's market move expressed as a factor-shock dict:
    absolute for rates and spread levels, proportional for equity and FX
    (SPEC section 2). Applying it to the base month-end reproduces the other
    month-end's market state exactly."""
    sh = {}
    for curve in _CURVES:
        for t in _TENORS:
            sh[f"{curve}_{t}"] = (float(to_assumptions["curves"][curve][t])
                                  - float(base_assumptions["curves"][curve][t]))
    for r in _RATINGS:
        sh[f"spread_{r}"] = (float(to_assumptions["spreads"][r])
                             - float(base_assumptions["spreads"][r]))
    for i in _INDICES:
        sh[f"eq_{i}"] = (float(to_assumptions["equity"][i])
                         / float(base_assumptions["equity"][i])) - 1.0
    sh["fx_GBPUSD"] = (float(to_assumptions["fx"]["GBPUSD"])
                       / float(base_assumptions["fx"]["GBPUSD"])) - 1.0
    return sh


def price_scenario(asof, shocks=None, book=None, liabilities=None,
                   to_asof=None) -> dict:
    """Deterministic repricing at factor movements you specify — NO
    simulation, one revaluation, exact and instant (PENDING-ROSTER M.1).

    `shocks` takes absolute moves for rates/spreads and proportional moves
    for equity/FX, any subset; a block name ('gbp_swap', 'spread', 'equity')
    moves every factor in that block. `to_asof` names another month-end (or
    run) and shocks the whole market state to it — 'reprice THIS book at
    THAT month's curves, spreads, levels and FX', which is the market leg of
    a month-on-month balance-sheet reconciliation; explicit `shocks` are
    applied on top. Returns position-level values and P&L, per-cohort
    liability PVs, asset total / liability PV / surplus with the base and
    the delta, sleeve totals, effective durations (assets, fixed income,
    liabilities and the GAP) and per-block exposure summaries split into
    assets and liabilities."""
    from engine import esg as _esg, pricing as _pricing  # noqa: PLC0415

    inp = _scenario_inputs(asof, book=book, liabilities=liabilities)
    if to_asof is not None:
        target = _scenario_inputs(to_asof)
        merged = market_delta_shocks(inp["assumptions"], target["assumptions"])
        for k, v in dict(shocks or {}).items():
            canon = _canonical_factor(k)
            merged[canon] = merged.get(canon, 0.0) + float(v)
        shocks = merged
    vec, applied = _parse_shocks(shocks)
    meta = _factor_meta(inp["assumptions"])
    order = _factor_order()
    pos_of = {name: i for i, name in enumerate(order)}

    # One vectorized revaluation set: [base, scenario, +/- bumps per block].
    bump_blocks = [("gbp_swap", BUMP_BP), ("gbp_gilt", BUMP_BP),
                   ("ust", BUMP_BP), ("spread", BUMP_BP),
                   ("equity", BUMP_PROP), ("fx", BUMP_PROP)]
    rows = [np.zeros(len(order)), vec]
    for name, h in bump_blocks:
        for sign in (1.0, -1.0):
            r = np.zeros(len(order))
            for f in _SHOCK_BLOCKS[name]:
                r[pos_of[f]] = sign * h
            rows.append(r)
    mat = np.vstack(rows)

    state0 = _esg.base_state(inp["assumptions"])
    sim = _esg.apply_shocks(state0, mat)
    vals = _pricing.value_positions(inp["positions"], sim,
                                    inp["ref_index_levels"])   # (K, n_pos)
    cohorts, cohort_pvs = _pricing.pv_liability_cohorts(inp["liabilities"],
                                                        sim)   # (K, n_coh)
    assets = vals.sum(axis=1)
    liabs = cohort_pvs.sum(axis=1)
    surplus_v = assets - liabs

    base_v, scen_v = vals[0], vals[1]
    positions_out, sleeves = [], {}
    for p, b, s in zip(inp["positions"], base_v, scen_v):
        rec = {"id": p["id"], "name": p.get("name"), "type": p["type"],
               "currency": p["currency"],
               "asset_class": p.get("asset_class"),
               "strategy": p.get("strategy"), "rating": p.get("rating"),
               "base_value_gbp": float(b), "value_gbp": float(s),
               "pnl_gbp": float(s - b)}
        positions_out.append(rec)
        sl = sleeves.setdefault(_sleeve(p), {
            "n_positions": 0, "base_value_gbp": 0.0, "value_gbp": 0.0,
            "pnl_gbp": 0.0})
        sl["n_positions"] += 1
        sl["base_value_gbp"] += float(b)
        sl["value_gbp"] += float(s)
        sl["pnl_gbp"] += float(s - b)

    # --- durations (effective, +/-1bp on the instrument's own curve) -------
    curve_bump_row = {}
    k = 2
    for name, h in bump_blocks:
        curve_bump_row[name] = (k, k + 1, h)
        k += 2

    def _dur(up, dn, base):
        return (float((dn - up) / (2.0 * BUMP_BP * base))
                if base else 0.0)

    pos_dur = []
    for j, p in enumerate(inp["positions"]):
        if p["type"] in ("govt_bond", "corp_bond"):
            curve = p.get("curve")
            ui, di, _ = curve_bump_row[curve]
            pos_dur.append(_dur(vals[ui, j], vals[di, j], base_v[j]))
        else:
            pos_dur.append(0.0)
    pos_dur = np.array(pos_dur)
    for rec, dv in zip(positions_out, pos_dur):
        rec["effective_duration_years"] = float(dv)

    is_bond = np.array([p["type"] in ("govt_bond", "corp_bond")
                        for p in inp["positions"]])
    is_pc = np.array([p.get("asset_class") == "private_credit"
                      for p in inp["positions"]])

    def _wavg(mask):
        w = base_v[mask]
        return float((pos_dur[mask] * w).sum() / w.sum()) if w.sum() else 0.0

    cohort_out, coh_dur = [], []
    for j, c in enumerate(cohorts):
        ui, di, _ = curve_bump_row[c["curve"]]
        dv = _dur(cohort_pvs[ui, j], cohort_pvs[di, j], cohort_pvs[0, j])
        coh_dur.append(dv)
        cohort_out.append({
            "id": c["id"], "class": c["class"], "currency": c["currency"],
            "curve": c["curve"],
            "base_pv_gbp": float(cohort_pvs[0, j]),
            "pv_gbp": float(cohort_pvs[1, j]),
            "pnl_gbp": float(cohort_pvs[1, j] - cohort_pvs[0, j]),
            "effective_duration_years": float(dv),
            "n_cashflows": len(c["cashflows"]),
            "last_cashflow_year": float(max(float(x["t"])
                                            for x in c["cashflows"])),
        })
    coh_dur = np.array(coh_dur)
    liab_dur = (float((coh_dur * cohort_pvs[0]).sum() / cohort_pvs[0].sum())
                if cohort_pvs[0].sum() else 0.0)
    d_fi = _wavg(is_bond)
    d_fi_ex_pc = _wavg(is_bond & ~is_pc)
    d_all = float((pos_dur * base_v).sum() / base_v.sum()) if base_v.sum() \
        else 0.0

    # --- exposure summaries per block -------------------------------------
    exposures = {}
    for name, h in bump_blocks:
        ui, di, _ = curve_bump_row[name]
        unit = "per_1bp" if h == BUMP_BP else "per_1pct_move"
        exposures[name] = {
            "unit": unit,
            "assets_gbp": float((assets[ui] - assets[di]) / 2.0),
            "liabilities_gbp": float((liabs[ui] - liabs[di]) / 2.0),
            "surplus_gbp": float((surplus_v[ui] - surplus_v[di]) / 2.0),
        }
    exposures["ir_gbp"] = {
        "unit": "per_1bp",
        "assets_gbp": exposures["gbp_swap"]["assets_gbp"]
        + exposures["gbp_gilt"]["assets_gbp"],
        "liabilities_gbp": exposures["gbp_swap"]["liabilities_gbp"]
        + exposures["gbp_gilt"]["liabilities_gbp"],
        "surplus_gbp": exposures["gbp_swap"]["surplus_gbp"]
        + exposures["gbp_gilt"]["surplus_gbp"],
    }
    exposures["ir_usd"] = dict(exposures["ust"])
    for name, ex in exposures.items():
        tot = abs(ex["assets_gbp"]) + abs(ex["liabilities_gbp"])
        ex["liability_share_of_gross"] = (abs(ex["liabilities_gbp"]) / tot
                                          if tot else None)

    return {
        "asof": str(asof), "label": inp["label"],
        "to_asof": str(to_asof) if to_asof is not None else None,
        "assumptions_file": inp["assumptions_file"],
        "book_file": inp["book_file"],
        "liabilities_file": inp["liabilities_file"],
        "deterministic": True, "simulation": False,
        "shocks_requested": {k_: v_["value"] for k_, v_ in applied.items()},
        "shocks_expanded": applied,
        "factors": _factor_rows(meta, vec),
        "base": {"asset_total_gbp": float(assets[0]),
                 "liability_pv_gbp": float(liabs[0]),
                 "surplus_gbp": float(surplus_v[0])},
        "shocked": {"asset_total_gbp": float(assets[1]),
                    "liability_pv_gbp": float(liabs[1]),
                    "surplus_gbp": float(surplus_v[1])},
        "delta": {"asset_total_gbp": float(assets[1] - assets[0]),
                  "liability_pv_gbp": float(liabs[1] - liabs[0]),
                  "surplus_gbp": float(surplus_v[1] - surplus_v[0])},
        "positions": positions_out,
        "liability_cohorts": cohort_out,
        "by_sleeve": sleeves,
        "durations": {
            "assets_fixed_income_years": d_fi,
            "assets_fixed_income_ex_private_credit_years": d_fi_ex_pc,
            "assets_all_years": d_all,
            "liabilities_years": liab_dur,
            "duration_gap_years": d_fi - liab_dur,
            "duration_gap_all_assets_years": d_all - liab_dur,
            "note": "effective durations by +/-1bp repricing on each "
                    "instrument's own discount curve; non-bond assets carry "
                    "zero rate duration. The gap is fixed-income assets "
                    "minus liabilities.",
        },
        "exposures": exposures,
    }


# --- query_scenarios -------------------------------------------------------

_QUERY_OPS = {
    "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b, ">": lambda a, b: a > b,
}
_QUERY_OUTCOMES = ("surplus_pnl", "asset_pnl", "liability_pnl")
_ATOM_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|==|!=|<|>)\s*([-+0-9.eE]+)\s*$")


def _parse_where(where: str, order: list) -> list:
    """OR-groups of AND-conditions. Deliberately a tiny grammar, parsed —
    never eval'd: `name op number`, joined by ' and ' / ' or '."""
    text = str(where or "").strip()
    if not text:
        raise ToolError("where must be a condition such as "
                        "\"equity_FTSE100 < -0.20\" or "
                        "\"surplus_pnl < -150e6\"")
    groups = []
    for or_part in re.split(r"\s+or\s+", text, flags=re.IGNORECASE):
        atoms = []
        for and_part in re.split(r"\s+and\s+", or_part, flags=re.IGNORECASE):
            m = _ATOM_RE.match(and_part)
            if not m:
                raise ToolError(
                    f"cannot parse condition {and_part.strip()!r}; the "
                    "grammar is `name op number` (op in <, <=, >, >=, ==, "
                    "!=), joined by ' and ' / ' or '")
            name, op, num = m.group(1), m.group(2), m.group(3)
            canon = _canonical_factor(name)
            if canon not in order and name not in _QUERY_OUTCOMES:
                raise ToolError(
                    f"unknown variable {name!r}; use a factor name "
                    f"({', '.join(order[:3])}, ... spread_HY, "
                    f"equity_FTSE100, fx_GBPUSD) or an outcome "
                    f"({', '.join(_QUERY_OUTCOMES)})")
            try:
                value = float(num)
            except ValueError:
                raise ToolError(f"cannot read {num!r} as a number")
            atoms.append({"variable": canon if canon in order else name,
                          "op": op, "value": value})
        groups.append(atoms)
    return groups


def query_scenarios(run, where: str, stats=None) -> dict:
    """Filter the saved simulation by factor or outcome conditions and return
    conditional statistics (PENDING-ROSTER M.1).

      query_scenarios(run, "equity_FTSE100 < -0.20")
      query_scenarios(run, "surplus_pnl < -150e6")     # reverse stress test

    Answers two questions nothing else reaches: what the OTHER factors did in
    the scenarios where one of them broke (joint plausibility of the
    calibrated correlation), and what would have to happen for the balance
    sheet to break (reverse stress testing)."""
    d, idx, surplus, factors, pnl = _load_sims(run)
    order = _factor_order()
    groups = _parse_where(where, order)
    n_sims = int(surplus.shape[0])
    want = set(stats) if stats else {"surplus", "factors", "positions"}

    s = np.asarray(surplus, dtype=float)
    columns = list(idx.get("pnl_columns") or [])
    liab_col = columns.index("LIABILITIES") if "LIABILITIES" in columns \
        else None
    liab = (np.asarray(pnl[:, liab_col], dtype=float)
            if liab_col is not None else np.zeros(n_sims))
    series = {"surplus_pnl": s, "liability_pnl": liab, "asset_pnl": s - liab}
    fac = np.asarray(factors, dtype=float)

    mask = np.zeros(n_sims, dtype=bool)
    for atoms in groups:
        m = np.ones(n_sims, dtype=bool)
        for a in atoms:
            col = (series[a["variable"]] if a["variable"] in series
                   else fac[:, order.index(a["variable"])])
            m &= _QUERY_OPS[a["op"]](col, a["value"])
        mask |= m
    n_match = int(mask.sum())

    out = {
        "run": str(run), "out_dir": out_dir_name(d), "n_sims": n_sims,
        "where": str(where),
        "conditions": [[dict(a) for a in g] for g in groups],
        "n_matching": n_match,
        "match_rate": float(n_match) / n_sims,
        "implied_return_period_years": (float(n_sims) / n_match
                                        if n_match else None),
    }
    if n_match == 0:
        out["note"] = ("no simulated scenario satisfies this condition — "
                       "the calibration does not produce it at this "
                       "simulation count")
        return out

    ms = s[mask]
    if "surplus" in want:
        out["surplus_pnl_gbp"] = {
            "mean": float(ms.mean()),
            "std": float(ms.std(ddof=1)) if n_match > 1 else 0.0,
            "min": float(ms.min()), "max": float(ms.max()),
            "p1": float(np.percentile(ms, 1)),
            "p5": float(np.percentile(ms, 5)),
            "median": float(np.median(ms)),
            "p95": float(np.percentile(ms, 95)),
            "mean_loss_gbp": float(-ms.mean()),
            "worst_loss_gbp": float(-ms.min()),
            "unconditional_mean": float(s.mean()),
        }
        out["asset_pnl_gbp_mean"] = float(series["asset_pnl"][mask].mean())
        out["liability_pnl_gbp_mean"] = float(liab[mask].mean())

    if "factors" in want:
        inp = _scenario_inputs(run)
        meta = _factor_meta(inp["assumptions"])
        sub = fac[mask]
        rows = []
        for i, m_ in enumerate(meta):
            vol = float(m_["vol_annual"])
            mean = float(sub[:, i].mean())
            uncond = float(fac[:, i].mean())
            rows.append({
                "factor": m_["factor"], "mean_shock": mean,
                "mean_shock_in_vols": (mean / vol) if vol else None,
                "unconditional_mean_shock": uncond,
                "mean_shocked_level": _shocked_level(m_, mean),
                "p5_shock": float(np.percentile(sub[:, i], 5)),
                "p95_shock": float(np.percentile(sub[:, i], 95)),
                "vol_annual": vol, "shock_kind": m_["shock_kind"]})
        out["assumptions_file"] = inp["assumptions_file"]
        out["factor_conditional_means"] = rows
        out["largest_conditional_draws"] = sorted(
            [r_ for r_ in rows if r_["mean_shock_in_vols"] is not None],
            key=lambda r_: -abs(r_["mean_shock_in_vols"]))[:5]

    if "positions" in want and columns:
        sel = np.flatnonzero(mask)
        capped = len(sel) > MAX_MATCH_ROWS_FOR_POSITIONS
        if capped:
            sel = sel[:MAX_MATCH_ROWS_FOR_POSITIONS]
        mean_contrib = np.asarray(pnl[sel], dtype=float).mean(axis=0)
        contribs = sorted(
            ({"id": c, "mean_pnl_gbp": float(v)}
             for c, v in zip(columns, mean_contrib)),
            key=lambda r_: r_["mean_pnl_gbp"])
        out["position_contributions_sample_n"] = int(len(sel))
        out["position_contributions_capped"] = bool(capped)
        out["largest_mean_contributors"] = contribs[:TOP_LOSERS]
    return out


def fetch_market_level(factor: str, asof) -> dict:
    """Yahoo Finance close for one market factor at a month-end.

    The INDEPENDENT leg of the input check: this reaches the level over the
    public internet, by a completely different route than the calibration
    pipeline took, so agreement with `assumptions/` is real corroboration
    rather than one source agreeing with itself.
    """
    from app.agents import yahoo  # noqa: PLC0415  (leaf; network-touching)
    try:
        return yahoo.close_on(str(factor).strip().lower(), str(asof)[:10])
    except yahoo.YahooError as e:
        raise ToolError(f"fetch_market_level: {e}")


REGISTRY = {
    "fetch_market_level": fetch_market_level,
    "read_output": read_output,
    "read_assumptions": read_assumptions,
    "read_book": read_book,
    "read_liabilities": read_liabilities,
    "read_data_series": read_data_series,
    "recompute_vol": recompute_vol,
    "verify_claim": verify_claim,
    "read_research": read_research,
    "read_agent_posts": read_agent_posts,
    "read_reference": read_reference,
    "delta_normal": delta_normal,
    "read_scenario": read_scenario,
    "tail_analysis": tail_analysis,
    "price_scenario": price_scenario,
    "query_scenarios": query_scenarios,
    "run_sensitivity": run_sensitivity,
    "propose_rerun": propose_rerun,
}

# Anthropic tool definitions for the live agentic loop (runtime.py).

_BROWSE_DIRS = ("assumptions", "book", "data/processed", "data",
                "outputs", "scenarios/reference", "calibration")
_BROWSE_SUFFIX = {".csv", ".json", ".yaml", ".yml", ".md", ".txt"}


def list_files(subdir: str = "") -> dict:
    """List readable files in the model folders, so an agent can discover what
    exists rather than guessing filenames."""
    out = []
    for d in _BROWSE_DIRS:
        if subdir and not d.startswith(str(subdir).strip("/")):
            continue
        base = PROJECT_ROOT / d
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.suffix.lower() in _BROWSE_SUFFIX:
                if "ground_truth" in f.name:
                    continue  # never discoverable
                out.append({"path": str(f.relative_to(PROJECT_ROOT)
                                        ).replace("\\", "/"),
                            "bytes": f.stat().st_size})
    return {"n_files": len(out), "files": out[:400]}


def read_file(path: str, max_rows: int = 200) -> dict:
    """Read any data/config file under the model folders by relative path.
    CSVs return parsed rows (head/tail when long); YAML/JSON return objects;
    text returns content. Ground truth is never readable."""
    rel = str(path).strip().replace("\\", "/").lstrip("/")
    if "ground_truth" in rel:
        raise ToolError("ground truth is not readable by agents")
    if not any(rel.startswith(d) for d in _BROWSE_DIRS):
        raise ToolError(f"path outside the readable model folders: {rel}")
    p = _guard_path(PROJECT_ROOT / rel)
    if not p.exists():
        raise ToolError(f"no such file: {rel}")
    if p.suffix.lower() == ".csv":
        import csv as _csv
        with open(p, "r", encoding="utf-8") as f:
            rows = [{k: _maybe_num(v) for k, v in r.items()}
                    for r in _csv.DictReader(f)]
        n = len(rows)
        if n > max_rows:
            head, tail = rows[: max_rows // 2], rows[-(max_rows // 2):]
            return {"file": rel, "n_rows": n, "truncated": True,
                    "head": head, "tail": tail}
        return {"file": rel, "n_rows": n, "rows": rows}
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml as _yaml
        with open(p, "r", encoding="utf-8") as f:
            return {"file": rel, "content": _yaml.safe_load(f)}
    if p.suffix.lower() == ".json":
        import json as _json
        with open(p, "r", encoding="utf-8") as f:
            return {"file": rel, "content": _json.load(f)}
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return {"file": rel, "text": f.read()[:40000]}

TOOL_SPECS = [
    {"name": "list_files",
     "description": "List the data and config files you can read across the "
                    "model folders (assumptions, book, data/processed, "
                    "outputs, scenarios/reference). Use this to discover what "
                    "exists before guessing a filename.",
     "input_schema": {"type": "object", "properties": {
         "subdir": {"type": "string"}}, "required": []}},
    {"name": "read_file",
     "description": "Read any data/config file under the model folders by "
                    "relative path (e.g. 'data/processed/gbp_gilt.csv', "
                    "'book/positions.json', "
                    "'outputs/2026_03/v1/pricing/valuation.json', "
                    "'book/README.md'). CSVs parse to rows; YAML/JSON to "
                    "objects. Use list_files first if unsure.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "max_rows": {"type": "integer"}},
         "required": ["path"]}},
    {"name": "read_output",
     "description": "Read a JSON/CSV/MD file from a run's output directory. "
                    "asof_or_run: a run id, a month key 'YYYY-MM' (the "
                    "month's base run, v1), an explicit version "
                    "'2026_03/v2', or a pair attribution directory "
                    "'attr_2026_02_v1__2026_03_v1'. Files from both the esg "
                    "and pricing sides of a run resolve by name.",
     "input_schema": {"type": "object", "properties": {
         "asof_or_run": {"type": "string"}, "filename": {"type": "string"}},
         "required": ["asof_or_run", "filename"]}},
    {"name": "read_assumptions",
     "description": "Read the assumptions YAML for a month key or the file a "
                    "given run id actually used (seeded runs resolve to their "
                    "seeded file).",
     "input_schema": {"type": "object", "properties": {
         "asof_or_run": {"type": "string"}}, "required": ["asof_or_run"]}},
    {"name": "read_book",
     "description": "Read the position file (pass a run id to get the book "
                    "that run actually used).",
     "input_schema": {"type": "object", "properties": {
         "asof_or_run": {"type": "string"}}}},
    {"name": "read_liabilities",
     "description": "Read the liability cashflow file.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_data_series",
     "description": "Daily rows from a processed market-data series "
                    "(gbp_swap|gbp_gilt|ust|credit_oas|equity|fx) between "
                    "ISO dates.",
     "input_schema": {"type": "object", "properties": {
         "series": {"type": "string"}, "start": {"type": "string"},
         "end": {"type": "string"}}, "required": ["series"]}},
    {"name": "recompute_vol",
     "description": "Deterministically recompute an annualized vol from "
                    "source data using the calibration methodology (stdev of "
                    "daily changes over window_days, x sqrt(252)).",
     "input_schema": {"type": "object", "properties": {
         "series": {"type": "string"}, "column": {"type": "string"},
         "asof": {"type": "string"}, "window_days": {"type": "integer"}},
         "required": ["series", "column", "asof"]}},
    {"name": "verify_claim",
     "description": "Numeric comparison (eq|ne|lt|le|gt|ge|approx) with "
                    "relative tolerance; result carries difference/rel_diff/"
                    "ratio for citing derived figures.",
     "input_schema": {"type": "object", "properties": {
         "left": {"type": "number"}, "op": {"type": "string"},
         "right": {"type": "number"}, "tol": {"type": "number"}},
         "required": ["left", "op", "right"]}},
    {"name": "fetch_market_level",
     "description": "Yahoo Finance closing level for one market input at a "
                    "month-end, fetched live over the internet — the "
                    "INDEPENDENT source for the input check. factor is one "
                    "of: ftse100, sp500, sx5e, gbpusd, ust_10y. asof is "
                    "'YYYY-MM-DD' (or 'YYYY-MM'); the last trading day on "
                    "or before it is used. Returns the value in the same "
                    "unit as the assumptions file, the exact date it came "
                    "from, and a source_url. Use this to source levels "
                    "rather than searching the web for them.",
     "input_schema": {"type": "object", "properties": {
         "factor": {"type": "string",
                    "enum": ["ftse100", "sp500", "sx5e", "gbpusd",
                             "ust_10y"]},
         "asof": {"type": "string"}},
         "required": ["factor", "asof"]}},
    {"name": "read_research",
     "description": "The research note for a month-end (asof 'YYYY-MM', ISO "
                    "date, or run id). agent='focused' (default): factor-"
                    "block research computed from data/processed/*.csv only, "
                    "independent of assumptions and engine outputs. "
                    "agent='wide-eye': the wider-risk report (themes around "
                    "the factor set, web-researched). data_through advances "
                    "the window past month-end for a fresh snapshot. Serves "
                    "the published note; result carries the markdown and "
                    "per-factor stats.",
     "input_schema": {"type": "object", "properties": {
         "asof": {"type": "string"}, "agent": {"type": "string"},
         "data_through": {"type": "string"}}, "required": ["asof"]}},
    {"name": "read_agent_posts",
     "description": "Another agent's published posts for the active "
                    "run/snapshot (SPEC-APP section H). Each claim keeps its "
                    "ORIGINAL tool_call_id — cite that id directly, never "
                    "restate the number as your own fresh claim, so "
                    "provenance chains to the executed tool call, not to "
                    "\"another agent said so.\"",
     "input_schema": {"type": "object", "properties": {
         "room": {"type": "integer"}, "handle": {"type": "string"},
         "run_id": {"type": "string"}, "snapshot_id": {"type": "string"}},
         "required": ["room", "handle"]}},
    {"name": "read_reference",
     "description": "Read a bundled reference document (e.g. "
                    "ratings_ref.csv, draft_report_D3.md). Ground truth is "
                    "not accessible.",
     "input_schema": {"type": "object", "properties": {
         "filename": {"type": "string"}}, "required": ["filename"]}},
    {"name": "delta_normal",
     "description": "Deterministic delta-normal analytic cross-check of a "
                    "run (engine code, no simulation): bump-delta factor "
                    "exposures, closed-form aggregate 2.576*sqrt(w'Sigma w), "
                    "Euler component VaR per factor/block summing to the "
                    "total, diversification benefit, approximation gap vs "
                    "the run's simulated VaR. Pass run_b to split the "
                    "delta-VaR into exposure/vol/correlation movements with "
                    "the largest-moving correlation cells named. Max 2 per "
                    "post.",
     "input_schema": {"type": "object", "properties": {
         "run_a": {"type": "string"}, "run_b": {"type": "string"}},
         "required": ["run_a"]}},
    {"name": "read_scenario",
     "description": "Drill into ONE saved simulated scenario: pass a loss "
                    "rank (rank 1 = worst; rank 250 of 50,000 = the 99.5th "
                    "percentile, i.e. THE VaR scenario — the default) or a "
                    "raw index. Returns the 21 factor draws with shocked "
                    "levels and size in annual vols, per-position P&L, the "
                    "position ranking by loss, spread-floor incidence, and "
                    "a joint-plausibility statistic (Mahalanobis d^2 vs "
                    "chi^2(21)) for whether the draw is coherent under the "
                    "calibrated correlation.",
     "input_schema": {"type": "object", "properties": {
         "run": {"type": "string"}, "rank": {"type": "integer"},
         "index": {"type": "integer"}}, "required": ["run"]}},
    {"name": "tail_analysis",
     "description": "Across the worst n saved scenarios (default: the tail "
                    "beyond `quantile`): mean position contributions, "
                    "factor draw distributions, how often each position is "
                    "in the top-5 losers, expected shortfall and "
                    "spread-floor incidence.",
     "input_schema": {"type": "object", "properties": {
         "run": {"type": "string"}, "quantile": {"type": "number"},
         "n": {"type": "integer"}}, "required": ["run"]}},
    {"name": "price_scenario",
     "description": "Deterministic repricing at factor movements YOU "
                    "specify — no simulation, one exact revaluation, "
                    "milliseconds. shocks: absolute moves for rates and "
                    "spreads, proportional for equity/FX, any subset "
                    "(unspecified factors held); a block name (gbp_swap, "
                    "ir_gbp, spread, equity, fx) moves the whole block, "
                    "e.g. {\"gbp_swap\": 0.01, \"spread_HY\": 0.03, "
                    "\"equity_FTSE100\": -0.20}. Returns position values "
                    "and P&L, per-cohort liability PVs, asset total / "
                    "liability PV / surplus (base, shocked, delta), sleeve "
                    "totals, effective durations including the "
                    "asset/liability duration GAP, and per-block exposure "
                    "split into assets and liabilities. Optional book / "
                    "liabilities name a different position or cohort file "
                    "(e.g. positions_2026-03.json) to price a book change "
                    "on one side's market state; optional to_asof shocks "
                    "the whole market state to another month-end ('this "
                    "book at that month's market'), which is the market "
                    "leg of a month-on-month reconciliation. Cheap: not "
                    "bounded per post.",
     "input_schema": {"type": "object", "properties": {
         "asof": {"type": "string"}, "shocks": {"type": "object"},
         "book": {"type": "string"}, "liabilities": {"type": "string"},
         "to_asof": {"type": "string"}},
         "required": ["asof"]}},
    {"name": "query_scenarios",
     "description": "Filter the saved simulation by factor or outcome "
                    "conditions and return conditional statistics. Grammar: "
                    "`name op number` (op in <, <=, >, >=, ==, !=) joined "
                    "by ' and ' / ' or '; names are factors "
                    "(equity_FTSE100, spread_HY, gbp_swap_10, fx_GBPUSD) or "
                    "outcomes (surplus_pnl, asset_pnl, liability_pnl). "
                    "\"equity_FTSE100 < -0.20\" asks what credit did when "
                    "equities broke (joint plausibility); "
                    "\"surplus_pnl < -150e6\" is reverse stress testing — "
                    "what would have to happen for the balance sheet to "
                    "break.",
     "input_schema": {"type": "object", "properties": {
         "run": {"type": "string"}, "where": {"type": "string"},
         "stats": {"type": "array", "items": {"type": "string"}}},
         "required": ["run", "where"]}},
    {"name": "run_sensitivity",
     "description": "Engine rerun in a temp dir with a shocked input "
                    "(sims=5000, base and shocked at the same seed; returns "
                    "headline deltas). Max 2 per post.",
     "input_schema": {"type": "object", "properties": {
         "asof": {"type": "string"}, "shock_json": {"type": "object"}},
         "required": ["asof", "shock_json"]}},
    {"name": "propose_rerun",
     "description": "Create a PENDING human gate proposing a corrected "
                    "rerun. Never executes anything.",
     "input_schema": {"type": "object", "properties": {
         "asof": {"type": "string"}, "adjustments_json": {"type": "object"},
         "rationale": {"type": "string"}}, "required": [
         "asof", "adjustments_json", "rationale"]}},
]


class ToolSession:
    """Executes registry tools, records each call in tool_calls (post_id is
    bound after the post lands), and enforces the per-post budget."""

    def __init__(self, run_id: int | None = None,
                 max_calls: int | None = None,
                 snapshot_id: int | None = None,
                 data_through: str | None = None,
                 prev_run_id: int | None = None):
        self.run_id = run_id
        self.prev_run_id = prev_run_id   # the pass's comparison run
        self.snapshot_id = snapshot_id  # fresh-snapshot pass (SPEC-APP E)
        self.data_through = data_through
        self.max_calls = (max_calls if max_calls is not None
                          else config.MAX_TOOL_CALLS_PER_POST)
        self.tool_call_ids: list[int] = []
        self.gate_ids: list[int] = []
        self.calls = 0
        self.sensitivity_calls = 0
        self.delta_normal_calls = 0

    def _pin_to_active_run(self, args: dict) -> dict:
        """Rewrite a bare 'YYYY-MM' naming the ACTIVE run's month into that
        run's id, so a pass reads the run it is actually about."""
        if not self.run_id:
            return args
        try:
            run = _get_run(int(self.run_id))
            month = str(run["asof"])[:7]
        except Exception:
            return args
        # Only meaningful when the active run really is outputs/<month>/vN.
        # Runs registered elsewhere (test fixtures, ad-hoc directories) have
        # no version to compare against, and guessing one there blocks
        # perfectly legitimate reads.
        active_ver = None
        try:
            d = Path(run["out_dir"]).resolve()
            if (d.parent.parent.name == month.replace("-", "_")
                    and d.parent.name.startswith("v")
                    and d.parent.parent.parent == Path(OUTPUTS_DIR).resolve()):
                active_ver = d.parent.name
        except Exception:
            active_ver = None
        month_key = month.replace("-", "_")                        # "2026_03"
        # The pass's OWN comparison run is selected too — a pair may sit in
        # the same month (a rerun against a seeded input is exactly that),
        # and the desk has to be able to read both sides of it.
        allowed = {active_ver}
        if self.prev_run_id:
            try:
                pd = Path(_get_run(int(self.prev_run_id))["out_dir"])
                # ...only when it is the SAME month. The comparison run is
                # usually the month before, and its version name ("v1") must
                # not silently authorise v1 of THIS month.
                if pd.parent.parent.name == month_key:
                    allowed.add(pd.parent.name)
            except Exception:
                pass

        for key in ("asof_or_run", "asof"):
            v = args.get(key)
            if not isinstance(v, str):
                continue
            v = v.strip()
            if v == month and active_ver:
                # Only for a canonical outputs/<month>/vN run. A run held
                # anywhere else (a seeded preview, a fixture) is not "the
                # month's outputs", and redirecting a month lookup into it
                # sends the reader at files it was never meant to hold.
                args[key] = str(self.run_id)          # bare month -> this run
                continue
            # An UNSELECTED version of the active month is not readable.
            # Two runs of the same month tell different stories, and an
            # agent that reads the one nobody selected reports a month that
            # did not happen — which is exactly what occurred.
            m = re.match(r"^(\d{4})[-_](\d{2})[/_]v(\d+)$", v)
            if m and f"{m.group(1)}_{m.group(2)}" == month_key                     and active_ver and f"v{m.group(3)}" not in allowed:
                raise ToolError(
                    f"{v} is not the selected run for {month}: this pass is "
                    f"on {month_key}/{active_ver}. A different version of the "
                    "same month is a different story; read the selected run "
                    "(or pass the run id).")
            if v.startswith("attr_") and month_key in v and active_ver                     and not any(v.endswith(f"{month_key}_{a}")
                                for a in allowed):
                raise ToolError(
                    f"{v} compares a different run of {month}: this pass is "
                    f"on {month_key}/{active_ver}. Read the attribution "
                    f"ending {month_key}_{active_ver}.")
        return args

    def call(self, tool: str, **args):
        """Execute one tool; returns (tool_call_id, result). Failures are
        recorded too (result carries an 'error' key) and re-raised."""
        if tool not in REGISTRY:
            raise ToolError(f"unknown tool: {tool}")
        args = _canonical_args(tool, args)
        if self.calls >= self.max_calls:
            raise ToolLimitError(
                f"per-post tool budget exhausted "
                f"({self.max_calls} = MAX_TOOL_CALLS_PER_POST)")
        if tool == "run_sensitivity":
            if self.sensitivity_calls >= MAX_SENSITIVITY_PER_POST:
                raise ToolLimitError(
                    f"run_sensitivity is bounded to "
                    f"{MAX_SENSITIVITY_PER_POST} calls per post")
            self.sensitivity_calls += 1
        if tool == "delta_normal":
            if self.delta_normal_calls >= MAX_DELTA_NORMAL_PER_POST:
                raise ToolLimitError(
                    f"delta_normal is bounded to "
                    f"{MAX_DELTA_NORMAL_PER_POST} calls per post")
            self.delta_normal_calls += 1
        # A BARE MONTH means "that month's v1" by construction, which is
        # wrong whenever the active run is not v1: every agent in a pass on
        # 2026_03/v2 that asked for "2026-03" silently read v1 instead, and
        # @warden reported "premium nil" for the month whose entire story
        # was a £25m premium. When the month named IS the active run's
        # month, resolve it to the active run. Recorded as the run id, so
        # the tool_calls row shows exactly which run was read.
        args = self._pin_to_active_run(args)
        if tool == "propose_rerun" and "run_id" not in args:
            args["run_id"] = self.run_id
        if tool == "read_agent_posts":
            args.setdefault("run_id", self.run_id)
            args.setdefault("snapshot_id", self.snapshot_id)
        if tool == "read_research" and self.data_through is not None:
            args.setdefault("data_through", self.data_through)
        self.calls += 1
        conn = db.get_db()
        try:
            result = REGISTRY[tool](**args)
        except ToolError as e:
            cur = conn.execute(
                "INSERT INTO tool_calls (post_id, tool, args_json, "
                "result_json, ts) VALUES (NULL, ?, ?, ?, ?)",
                (tool, json.dumps(args, default=str),
                 json.dumps({"error": str(e)}), _now()))
            conn.commit()
            self.tool_call_ids.append(cur.lastrowid)
            raise
        artifact = None
        if isinstance(result, dict):
            artifact = result.get("out_dir")
        cur = conn.execute(
            "INSERT INTO tool_calls (post_id, tool, args_json, result_json, "
            "artifact_path, ts) VALUES (NULL, ?, ?, ?, ?, ?)",
            (tool, json.dumps(args, default=str),
             json.dumps(result, default=str), artifact, _now()))
        conn.commit()
        tc_id = cur.lastrowid
        self.tool_call_ids.append(tc_id)
        if tool == "propose_rerun":
            self.gate_ids.append(result["gate_id"])
        return tc_id, result

    def bind_post(self, post_id: int) -> None:
        """Attach this session's tool calls (and any proposed gates) to the
        post they were executed for."""
        conn = db.get_db()
        for tc in self.tool_call_ids:
            conn.execute("UPDATE tool_calls SET post_id = ? WHERE id = ?",
                         (post_id, tc))
        for g in self.gate_ids:
            conn.execute("UPDATE gates SET proposed_by_post_id = ? "
                         "WHERE id = ?", (post_id, g))
        conn.commit()


def fetch_result_json(tool_call_id) -> str | None:
    """Citation-binding lookup: the recorded result_json for a tool call."""
    if tool_call_id is None:
        return None
    row = db.get_db().execute(
        "SELECT result_json FROM tool_calls WHERE id = ?",
        (int(tool_call_id),)).fetchone()
    return row["result_json"] if row else None
