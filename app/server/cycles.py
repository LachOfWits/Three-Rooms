"""Saved cycles — a real live run, frozen and replayable (PENDING-JUDGE §4).

Why this exists: a judge opening the repository has no API key. Without a
saved cycle they see an empty app, which is the worst possible first
impression of a system whose entire point is what the agents say. With one,
they see genuine Claude output immediately — no key, no wait, no cost — and
the key only becomes necessary if they want to run their own.

What is saved is REAL output. The manifest records the model, the effort, the
date and the source count, so nothing is claimed that is not true; mock
output is refused outright rather than quietly passed off as live.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.server import db

CYCLES_DIR = config.PROJECT_ROOT / "saved_cycles"

# The cycle currently loaded, if any. The posts on screen were produced by
# THAT run, so the mode badge must describe the content — showing "MOCK"
# over real live output because this process happens to lack a key is
# simply false.
_ACTIVE: dict | None = None


def active_cycle() -> dict | None:
    return _ACTIVE

# Tables captured, in insert order (parents before children).
TABLES = ("runs", "stage_events", "agents", "posts", "tool_calls",
          "gates", "notifications")


def _rows(conn, table: str) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    except Exception:
        return []          # a table this build does not have


def _slug(label: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in label.strip()]
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "cycle"


def save_cycle(label: str, *, mode: str, model: str | None,
               effort: str | None, note: str = "",
               overwrite: bool = False) -> dict:
    """Freeze the current database and research notes under a label.

    Refuses to save a mock cycle: a saved cycle is shown to judges as real
    Claude output, so passing off templated prose would be a lie told by the
    software rather than by anyone in particular.
    """
    if mode != "live":
        raise ValueError(
            "only a live cycle can be saved — a saved cycle is presented as "
            "real model output, and this run had no API key")
    conn = db.get_db()
    slug = _slug(label)
    out = CYCLES_DIR / slug
    if out.exists() and not overwrite:
        # A saved cycle is what every judge sees. Overwriting one by
        # accident — a stray save while poking at the app — would replace it
        # with whatever happens to be in the database at that moment.
        raise FileExistsError(
            f"a saved cycle named '{slug}' already exists. Pass "
            "overwrite=true to replace it deliberately, or use a new label.")
    if out.exists():
        shutil.rmtree(out)
    (out / "research").mkdir(parents=True)

    data = {t: _rows(conn, t) for t in TABLES}
    (out / "data.json").write_text(
        json.dumps(data, indent=1, default=str), encoding="utf-8")

    research_src = config.PROJECT_ROOT / "outputs" / "research"
    notes = []
    if research_src.exists():
        for f in sorted(research_src.glob("*.levels.json")):
            shutil.copy(f, out / "research" / f.name)
        for f in sorted(research_src.glob("*.md")):
            shutil.copy(f, out / "research" / f.name)
            notes.append(f.name)

    published = [p for p in data.get("posts", [])
                 if p.get("status") == "published"]
    sources = set()
    for p in data.get("posts", []):
        raw = p.get("web_sources_json")
        if isinstance(raw, str) and raw:
            try:
                sources.update(json.loads(raw))
            except json.JSONDecodeError:
                pass

    manifest = {
        "label": label,
        "slug": slug,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model": model,
        "effort": effort,
        "note": note,
        "counts": {
            "posts_published": len(published),
            "posts_suppressed": sum(1 for p in data.get("posts", [])
                                    if p.get("status") == "suppressed"),
            "tool_calls": len(data.get("tool_calls", [])),
            "runs": len(data.get("runs", [])),
            "research_notes": len(notes),
            "web_sources": len(sources),
        },
        "research_notes": notes,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def list_cycles() -> list[dict]:
    if not CYCLES_DIR.exists():
        return []
    out = []
    for d in sorted(CYCLES_DIR.iterdir()):
        m = d / "manifest.json"
        if d.is_dir() and m.exists():
            try:
                out.append(json.loads(m.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return sorted(out, key=lambda m: m.get("saved_at", ""), reverse=True)


def load_cycle(slug: str) -> dict:
    """Restore a saved cycle over the current database.

    Destructive to the DATABASE by design — loading a cycle means viewing
    that cycle, and a half-merged database showing two runs' posts side by
    side would be worse than either.

    The saved cycle itself is never written by this, and nothing a viewer
    then does — comments, replies, approving a gate — reaches it either:
    those land in the database, which is disposable. The files under
    saved_cycles/ change only on a deliberate, explicitly-overwriting save.
    """
    src = CYCLES_DIR / _slug(slug)
    manifest_path = src / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no saved cycle '{slug}'")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = json.loads((src / "data.json").read_text(encoding="utf-8"))

    conn = db.get_db()
    for t in reversed(TABLES):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    for t in TABLES:
        rows = data.get(t) or []
        if not rows:
            continue
        cols = {r["name"] for r in
                conn.execute(f"PRAGMA table_info({t})").fetchall()}
        for row in rows:
            keep = {k: v for k, v in row.items() if k in cols}
            if not keep:
                continue
            conn.execute(
                f"INSERT OR REPLACE INTO {t} ({','.join(keep)}) VALUES "
                f"({','.join('?' * len(keep))})", list(keep.values()))
    conn.commit()

    research_dst = config.PROJECT_ROOT / "outputs" / "research"
    research_dst.mkdir(parents=True, exist_ok=True)
    for f in sorted((src / "research").glob("*.levels.json")):
        shutil.copy(f, research_dst / f.name)
    for f in sorted((src / "research").glob("*.md")):
        shutil.copy(f, research_dst / f.name)
    global _ACTIVE
    _ACTIVE = manifest
    return manifest
