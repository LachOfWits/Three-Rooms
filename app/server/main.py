"""FastAPI application — routes per SPEC-APP section 7.

Design notes:
- Agent work (room passes, human-post replies, narrator posts) runs in
  FastAPI background tasks via `app.agents.api`; every import of the agents
  package is guarded so this server starts and serves before app/agents
  exists (posts then simply don't get agent responses).
- Dashboard and scorecard endpoints read engine output files directly —
  no AI anywhere in that path.
- The server MAY read scenarios/seeded/ground_truth.yaml (scorecard only);
  agents may not — it is never exposed through any agent-facing route.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.server import db, engine_bridge
from app.server.events import broker

ROOM_NAMES = {1: "1 · Assumption Challenge", 2: "2 · Execution Monitoring",
              3: "3 · Output Challenge"}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _agents_api():
    try:
        from app.agents import api as agents_api  # noqa: PLC0415
        return agents_api
    except Exception:
        return None


def _avatars_mod():
    try:
        from app.agents import avatars  # noqa: PLC0415
        return avatars
    except Exception:
        return None


def _avatar_json_for(p: dict) -> str | None:
    """Persona-declared avatar, else the section 8.1 default rule."""
    if p.get("avatar_json"):
        return p["avatar_json"]
    avatars = _avatars_mod()
    if avatars is None:
        return None
    return json.dumps(avatars.default_avatar(p.get("name"), p.get("handle")))


def seed_builtin_agents() -> int:
    """Insert built-in personas from app.agents.api (idempotent). Returns
    number seeded; 0 when the agents package is not present yet. Every
    builtin is seeded WITH its avatar_json (section 8.1); missing avatars
    fall back to the default-avatar rule. Rows that predate the avatar
    column get theirs backfilled.

    Seeding alone is INSERT OR IGNORE, so it cannot converge a database that
    predates a roster change — retired handles survive and also_posts_in is
    never written. ensure_builtins does both, and is idempotent, so it runs
    first (PENDING-BATCH2 §7, §8, §13)."""
    agents_api = _agents_api()
    if agents_api is None:
        return 0
    conn = db.get_db()
    # Guarded: a stubbed or partial agents module (tests do this) need not
    # expose it, and seeding below still works without convergence.
    converge = getattr(agents_api, "ensure_builtins", None)
    if callable(converge):
        converge(conn)
    n = 0
    try:
        personas = agents_api.builtin_personas()
    except Exception:
        return 0
    for p in personas:
        avatar = _avatar_json_for(p)
        reads_from = (json.dumps(p["reads_from"])
                     if p.get("reads_from") else None)
        reads_on_request = (json.dumps(p["reads_on_request"])
                            if p.get("reads_on_request") else None)
        cur = conn.execute(
            "INSERT OR IGNORE INTO agents "
            "(room, handle, name, focus, persona_prompt, avatar_json, "
            "builtin, outlook, reads_from, reads_on_request) VALUES "
            "(?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (p.get("room"), p.get("handle"), p.get("name"),
             p.get("focus"), p.get("persona_prompt"), avatar,
             p.get("outlook", "internal"), reads_from, reads_on_request))
        n += cur.rowcount
        if cur.rowcount == 0 and avatar:
            conn.execute(
                "UPDATE agents SET avatar_json = ? WHERE handle = ? "
                "AND builtin = 1 AND avatar_json IS NULL",
                (avatar, p.get("handle")))
        if cur.rowcount == 0:
            # SPEC-APP E/H fields (outlook/reads_from) added after some
            # builtins were first seeded: backfill only what is still NULL
            # so a user's own edits in the agent panel are never clobbered.
            conn.execute(
                "UPDATE agents SET reads_from = ?, reads_on_request = ? "
                "WHERE handle = ? AND builtin = 1 AND reads_from IS NULL "
                "AND reads_on_request IS NULL",
                (reads_from, reads_on_request, p.get("handle")))
    # any non-builtin row without an avatar gets the default rule too
    avatars = _avatars_mod()
    if avatars is not None:
        for row in conn.execute("SELECT id, handle, name FROM agents "
                                "WHERE avatar_json IS NULL").fetchall():
            conn.execute(
                "UPDATE agents SET avatar_json = ? WHERE id = ?",
                (json.dumps(avatars.default_avatar(row["name"],
                                                   row["handle"])),
                 row["id"]))
    conn.commit()
    return n


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if not db.is_initialized():
        db.init_db(config.DB_PATH)
    else:
        db.init_db(db.db_path())  # ensure schema on the configured path
    # ensure_builtins retires renamed handles, seeds new personas and
    # writes also_posts_in; INSERT OR IGNORE seeding alone leaves a
    # pre-existing database on the old roster.
    seed_builtin_agents()   # converges the roster, then seeds
    yield


app = FastAPI(title="Three Rooms", docs_url="/api/docs",
              openapi_url="/api/openapi.json", lifespan=_lifespan)


# --- helpers ---------------------------------------------------------------

def _check_room(room: int) -> None:
    if room not in (1, 2, 3):
        raise HTTPException(404, f"no such room: {room}")


def _get_run_or_404(run_id: int) -> dict:
    run = engine_bridge.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"no such run: {run_id}")
    return run


def _reply_count(conn, post_id: int) -> int:
    """Replies under a post — PENDING-BATCH2 §4: "children of type reply".
    Expansions are the post's own working, not conversation, so they do not
    count; a suppressed reply is not shown and so is not counted either."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE parent_id = ? "
        "AND type = 'reply' AND status = 'published'",
        (post_id,)).fetchone()["n"]


def _post_with_details(conn, post: dict) -> dict:
    post = dict(post)
    post["claims"] = json.loads(post["claims_json"]) if post.get("claims_json") else []
    post["reply_count"] = _reply_count(conn, post["id"])
    post["tool_calls"] = conn.execute(
        "SELECT id, tool, args_json, result_json, artifact_path, ts "
        "FROM tool_calls WHERE post_id = ? ORDER BY id", (post["id"],)).fetchall()
    return post


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _run_outputs(run: dict) -> dict:
    """Headline numbers straight from a run's output files."""
    out = Path(run["out_dir"])
    res: dict = {"run": engine_bridge.decorate_run(dict(run))}
    val = out / "valuation.json"
    if val.exists():
        v = _read_json(val)
        res["valuation"] = {
            "asset_total_gbp": v.get("asset_total_gbp"),
            "liability_pv_gbp": v.get("liability_pv_gbp"),
            "surplus_gbp": v.get("surplus_gbp"),
            "meta": v.get("meta"),
        }
    agg = out / "var_aggregate.json"
    if agg.exists():
        res["var_aggregate"] = _read_json(agg)
    blocks = out / "var_standalone_factors.json"
    if blocks.exists():
        res["var_blocks"] = _read_json(blocks)
    pos = out / "var_standalone_positions.csv"
    if pos.exists():
        with open(pos, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["market_value_gbp"] = float(r["market_value_gbp"])
            r["var_99_5_1y_gbp"] = float(r["var_99_5_1y_gbp"])
        rows.sort(key=lambda r: -r["var_99_5_1y_gbp"])
        res["top_positions_by_var"] = rows[:10]
    res["manifest"] = engine_bridge.read_manifest(run["out_dir"])
    return res


def _attribution_for_pair(prev_run: dict, curr_run: dict) -> dict | None:
    """Committed attribution outputs for the pair, if present. The directory
    names BOTH ends (`attr_2026_02_v1__2026_03_v1`, PENDING-BATCH2 section 1),
    so which two runs an attribution came from is readable off the path.
    (Attribution between arbitrary ad-hoc runs is not computed here.)"""
    p = engine_bridge.attribution_dir_for_runs(prev_run, curr_run) / \
        "attribution.json"
    if p.exists():
        return _read_json(p)
    return None


# --- run identity (PENDING-BATCH2 §1 and §6) -------------------------------
#
# "no integer run id appears in a path or in the UI." Every response that
# names a run therefore carries the LABEL (`2603_v1`) and the directory it
# stands for. The integer stays on the row because it is the key the API is
# addressed by — it is never the NAME of anything.

_RUN_REF_FIELDS = ("id", "asof", "kind", "status", "label", "month",
                   "version", "run_dir", "esg_dir", "pricing_dir", "out_dir")


def _run_ref(run_id) -> dict | None:
    """Compact, label-first identity for a run id (None when unknown)."""
    if run_id is None:
        return None
    run = engine_bridge.get_run(int(run_id))
    if run is None:
        return None
    engine_bridge.decorate_run(run)
    return {k: run.get(k) for k in _RUN_REF_FIELDS}


# --- pass activity (PENDING-BATCH2 §3, §4) ---------------------------------
#
# Who is still to post, and who has posted, for the pass currently running in
# a room (and for the research stage). State is deliberately in-process: a
# pass dies with the server, so a "running" flag that survived a restart
# would be a lie. Every transition is pushed over SSE as an `activity` event
# carrying exactly the GET /api/rooms/{room}/activity body, so the UI never
# has to poll.

_ACTIVITY_LOCK = threading.RLock()
_ACTIVITY: dict[str, dict] = {}

_ACTIVITY_POLL_SECONDS = 0.35

_STAGE_LABELS = {"research": "Research", 1: "Room 1", 2: "Room 2",
                 3: "Room 3"}


def _activity_key(stage) -> str:
    return "research" if stage == "research" else f"room:{int(stage)}"


def _agent_mode() -> str:
    try:
        from app.agents import runtime  # noqa: PLC0415
        return runtime.agent_mode()
    except Exception:
        return "mock"


def _room_roster_handles(conn, room: int) -> list[str]:
    """Handles expected to post in one room pass, in run order.

    Mock runs the registered check list (which is where cross-room duties
    live — `@results-validator` is a room-2 agent that posts its draft-report
    review into room 3), plus the user-built personas' stubs. Live drives
    every persona row whose home room is this one, minus `@run-monitor`,
    whose narration is event-driven rather than part of a pass."""
    handles: list[str] = []

    def _add(h):
        if h and h not in handles:
            handles.append(h)

    live = _agent_mode() == "live"
    if not live:
        try:
            from app.agents.checks import ROOM_CHECKS  # noqa: PLC0415
            for handle, _fn in ROOM_CHECKS.get(room, []):
                _add(handle)
        except Exception:
            pass
    if live or not handles:
        # agents_in_room honours also_posts_in, so @focused/@red-team (rooms
        # 1 and 3) and @story (all three) appear in every room they post in.
        from app.agents import api as _api  # noqa: PLC0415
        for r in _api.agents_in_room(conn, room):
            if live and r["handle"] == "@run-monitor":
                continue
            _add(r["handle"])
    else:
        for r in conn.execute(
                "SELECT handle FROM agents WHERE room = ? AND builtin = 0 "
                "ORDER BY id", (room,)).fetchall():
            _add(r["handle"])
    return handles


def _research_roster_handles() -> list[str]:
    try:
        from app.agents import research  # noqa: PLC0415
        return ["@" + a for a in research.AGENTS]
    except Exception:
        return ["@focused", "@wide-eye"]


def _agent_cards(conn, handles: list[str]) -> list[dict]:
    """{handle, name, avatar} per handle — what the pending/typing row and
    the switcher dot need, and nothing else."""
    out = []
    for h in handles:
        row = conn.execute(
            "SELECT handle, name, room, avatar_json FROM agents "
            "WHERE handle = ? ORDER BY builtin DESC, id LIMIT 1",
            (h,)).fetchone()
        avatar = None
        if row and row.get("avatar_json"):
            try:
                avatar = json.loads(row["avatar_json"])
            except (TypeError, ValueError):
                avatar = None
        out.append({"handle": h,
                    "name": (row or {}).get("name") or h.lstrip("@"),
                    "room": (row or {}).get("room"),
                    "avatar": avatar})
    return out


def _blank_activity(key: str) -> dict:
    stage = "research" if key == "research" else int(key.split(":")[1])
    return {"key": key, "stage": stage,
            "room": None if stage == "research" else stage,
            "status": "idle", "expected": [], "done": [], "month": None,
            "run_id": None, "started_at": None, "finished_at": None,
            "error": None, "since_post_id": 0, "started_epoch": 0.0}


def _activity_payload(key: str) -> dict:
    with _ACTIVITY_LOCK:
        st = dict(_ACTIVITY.get(key) or _blank_activity(key))
        st["expected"] = list(st.get("expected") or [])
        st["done"] = list(st.get("done") or [])
    conn = db.get_db()
    if not st["expected"]:
        # idle, or a state from before the roster was resolved: the strip
        # still needs "Run room 1 — 5 agents" before anything has run (§3)
        try:
            st["expected"] = (_research_roster_handles() if key == "research"
                              else _room_roster_handles(conn, st["room"]))
        except Exception:
            st["expected"] = []
    done = st["done"]
    pending = ([h for h in st["expected"] if h not in done]
               if st["status"] == "running" else [])
    return {
        "key": key,
        "stage": st["stage"],
        "stage_label": _STAGE_LABELS.get(st["stage"], str(st["stage"])),
        "room": st["room"],
        "status": st["status"],
        "running": st["status"] == "running",
        "pending": _agent_cards(conn, pending),
        "done": _agent_cards(conn, done),
        "roster": _agent_cards(conn, st["expected"]),
        "expected_count": len(st["expected"]),
        "done_count": len(done),
        "pending_count": len(pending),
        "month": st.get("month"),
        "run": _run_ref(st.get("run_id")),
        "started_at": st.get("started_at"),
        "finished_at": st.get("finished_at"),
        "error": st.get("error"),
    }


def _publish_activity(key: str) -> None:
    try:
        broker.publish("activity", _activity_payload(key), run_id=None)
    except Exception:
        pass


def _max_post_id(conn) -> int:
    row = conn.execute("SELECT MAX(id) AS m FROM posts").fetchone()
    return int(row["m"] or 0)


def _activity_begin(key: str, *, expected: list[str], month=None,
                    run_id=None) -> None:
    conn = db.get_db()
    st = _blank_activity(key)
    st.update({"status": "running", "expected": list(expected), "done": [],
               "month": month, "run_id": run_id, "started_at": _now(),
               "finished_at": None, "error": None,
               "since_post_id": _max_post_id(conn),
               "started_epoch": datetime.datetime.now().timestamp()})
    with _ACTIVITY_LOCK:
        _ACTIVITY[key] = st
    _publish_activity(key)


def _activity_mark_done(key: str, handles: list[str]) -> bool:
    """Record handles that have now posted; True when anything changed."""
    with _ACTIVITY_LOCK:
        st = _ACTIVITY.get(key)
        if st is None:
            return False
        new = [h for h in handles if h not in st["done"]]
        st["done"].extend(new)
    return bool(new)


def _activity_finish(key: str, status: str, error: str | None = None) -> None:
    with _ACTIVITY_LOCK:
        st = _ACTIVITY.get(key)
        if st is None:
            st = _blank_activity(key)
            _ACTIVITY[key] = st
        st["status"] = status
        st["error"] = error
        st["finished_at"] = _now()
    _publish_activity(key)


def _room_posters(key: str) -> list[str]:
    """Handles that have posted into the room since this pass began, in the
    order their posts landed."""
    with _ACTIVITY_LOCK:
        st = _ACTIVITY.get(key)
        if st is None:
            return []
        room, since = st["room"], st["since_post_id"]
    rows = db.get_db().execute(
        "SELECT a.handle AS handle FROM posts p JOIN agents a "
        "ON a.id = p.agent_id WHERE p.room = ? AND p.id > ? ORDER BY p.id",
        (room, since)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["handle"] not in seen:
            seen.add(r["handle"])
            out.append(r["handle"])
    return out


def _research_posters(key: str) -> list[str]:
    """Research agents whose report file has been (re)written by this pass."""
    with _ACTIVITY_LOCK:
        st = _ACTIVITY.get(key)
        if st is None:
            return []
        month, started = st["month"], st["started_epoch"]
    try:
        from app.agents import research  # noqa: PLC0415
    except Exception:
        return []
    out = []
    for agent in research.AGENTS:
        p = research.RESEARCH_DIR / research.note_filename(month, agent)
        try:
            if p.exists() and p.stat().st_mtime >= started - 1.0:
                out.append("@" + agent)
        except OSError:
            continue
    return out


def _activity_watcher(key: str, poller, stop: threading.Event) -> None:
    """Poll the database for arrivals while a pass runs and PUSH each one.
    The server polls so the browser does not have to: agents publish through
    `app.agents`, which this module does not own, so there is no hook to
    hang an event on — but the client still gets a push per arrival."""
    while not stop.wait(_ACTIVITY_POLL_SECONDS):
        with _ACTIVITY_LOCK:
            st = _ACTIVITY.get(key)
            if st is None or st["status"] != "running":
                return
        try:
            found = poller(key)
        except Exception:
            continue
        if _activity_mark_done(key, found):
            _publish_activity(key)


def _run_stage_with_activity(key: str, poller, work) -> str | None:
    """Run one stage's `work()` under a live activity watcher. Returns None
    on success or an error string; never raises — a stage that blows up
    must leave the strip showing `failed`, not a wedged `running`."""
    stop = threading.Event()
    watcher = threading.Thread(target=_activity_watcher,
                               args=(key, poller, stop), daemon=True)
    watcher.start()
    error = None
    try:
        error = work()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    finally:
        stop.set()
        watcher.join(timeout=2.0)
    try:
        _activity_mark_done(key, poller(key))
    except Exception:
        pass
    _activity_finish(key, "failed" if error else "done", error=error)
    return error


@app.get("/api/rooms/{room}/activity")
def room_activity(room: int):
    """PENDING-BATCH2 §4 — who is still to post in this room's pass, and who
    already has. `status` is idle | running | done | failed."""
    _check_room(room)
    return _activity_payload(_activity_key(room))


@app.get("/api/research/activity")
def research_activity():
    """The same shape for the research stage (§3's first strip button)."""
    return _activity_payload("research")


@app.get("/api/activity")
def all_activity():
    """Every stage at once — what the run-control strip and the room
    switcher's activity dot both read in one request."""
    stages = [_activity_payload(_activity_key(s))
              for s in ("research", 1, 2, 3)]
    return {"stages": stages,
            "running": [s["key"] for s in stages if s["status"] == "running"],
            "cycle": _cycle_payload()}


# --- rooms / feed ----------------------------------------------------------

@app.get("/api/rooms/{room}/feed")
def room_feed(room: int, thread: int | None = None):
    _check_room(room)
    conn = db.get_db()
    if thread is not None:
        root = conn.execute("SELECT * FROM posts WHERE id = ? AND room = ?",
                            (thread, room)).fetchone()
        if root is None:
            raise HTTPException(404, f"no such thread: {thread}")
        # full descendant set (expansion children = the working; reply = talk)
        nodes, frontier = [], [thread]
        while frontier:
            marks = ",".join("?" * len(frontier))
            kids = conn.execute(
                f"SELECT * FROM posts WHERE parent_id IN ({marks}) ORDER BY id",
                frontier).fetchall()
            nodes.extend(kids)
            frontier = [k["id"] for k in kids]
        return {"room": room, "room_name": ROOM_NAMES[room],
                "thread": _post_with_details(conn, root),
                "children": [_post_with_details(conn, k) for k in nodes]}

    origins = conn.execute(
        "SELECT * FROM posts WHERE room = ? AND parent_id IS NULL "
        "AND status = 'published' ORDER BY pinned DESC, id DESC",
        (room,)).fetchall()
    for o in origins:
        o["claims"] = json.loads(o["claims_json"]) if o.get("claims_json") else []
        o["reply_count"] = _reply_count(conn, o["id"])
    suppressed = conn.execute(
        "SELECT * FROM posts WHERE room = ? AND status = 'suppressed' "
        "ORDER BY id DESC", (room,)).fetchall()
    for s in suppressed:
        s["claims"] = json.loads(s["claims_json"]) if s.get("claims_json") else []
        s["reply_count"] = _reply_count(conn, s["id"])
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE room = ?", (room,)).fetchone()["n"]
    return {"room": room, "room_name": ROOM_NAMES[room], "posts": origins,
            "suppressed": suppressed,
            "suppression_rate": (len(suppressed) / total) if total else 0.0}


def _system_reply(room: int, parent_id: int, body: str) -> list[int]:
    """Answer a human in their own thread, as the app rather than an agent.

    Silence is the wrong answer to a comment. Without a key the agents
    cannot run, and a comment that simply sits there looks like a bug in
    the app instead of a missing credential."""
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, parent_id, "
        "body_md, status, created_at) VALUES "
        "(?, NULL, 'three-rooms', 'reply', ?, ?, 'published', ?)",
        (room, parent_id, body, _now()))
    conn.commit()
    return [cur.lastrowid]


def _bg_handle_human_post(room: int, post_id: int) -> None:
    agents_api = _agents_api()
    if agents_api is None:
        return
    try:
        from app.agents import runtime as _rt
        if _rt.agent_mode() == "live" and not _rt.have_api_key():
            new_ids = _system_reply(
                room, post_id,
                "**No API key connected.** The agents cannot answer without "
                "one, so nothing has run. Add a key from the operator chip "
                "in the header (top right) and post again — it is held in "
                "memory for this session only and never written to disk. "
                "Everything already on screen is a saved live cycle and "
                "stays readable without a key.")
            engine_bridge.notify_posts(list(new_ids))
            engine_bridge.notify_notifications(list(new_ids))
            return
    except Exception:
        pass
    try:
        new_ids = agents_api.handle_human_post(room, post_id) or []
    except Exception as e:
        # A failure here used to be swallowed whole, leaving the comment
        # unanswered and the reason nowhere.
        logging.getLogger(__name__).warning(
            "human post %s in room %s produced no reply: %s: %s",
            post_id, room, type(e).__name__, " ".join(str(e).split())[:200])
        new_ids = _system_reply(
            room, post_id,
            "**The agents could not answer this.** "
            f"`{type(e).__name__}: {' '.join(str(e).split())[:160]}`")
    engine_bridge.notify_posts(list(new_ids))
    engine_bridge.notify_notifications(list(new_ids))


@app.post("/api/rooms/{room}/posts")
def create_human_post(room: int, payload: dict, background: BackgroundTasks):
    _check_room(room)
    body = (payload or {}).get("body", "").strip()
    if not body:
        raise HTTPException(422, "body is required")
    parent_id = (payload or {}).get("parent_id")
    conn = db.get_db()
    if parent_id is not None:
        parent = conn.execute("SELECT id FROM posts WHERE id = ? AND room = ?",
                              (parent_id, room)).fetchone()
        if parent is None:
            raise HTTPException(404, f"no such parent post: {parent_id}")
    author = (payload or {}).get("author_label") or "you"
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, parent_id, "
        "body_md, status, created_at) VALUES (?, NULL, ?, ?, ?, ?, 'published', ?)",
        (room, author, "reply" if parent_id else "origin", parent_id, body,
         _now()))
    conn.commit()
    post_id = cur.lastrowid
    engine_bridge.notify_posts([post_id])
    # A human post triggers work architecturally identical to a scheduled
    # pass — bounded inside the agents runtime (SPEC-APP section 0.4).
    background.add_task(_bg_handle_human_post, room, post_id)
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return {"post": post}


def _bg_room_pass(room: int, prev_run_id: int | None, curr_run_id: int,
                  seeded: bool) -> str | None:
    """One room pass, with its activity state tracked and pushed (§4).
    Returns None on success or an error string (the cycle runner reads it)."""
    key = _activity_key(room)
    conn = db.get_db()
    _activity_begin(key, expected=_room_roster_handles(conn, room),
                    month=str((engine_bridge.get_run(curr_run_id) or {}
                               ).get("asof") or "")[:7] or None,
                    run_id=curr_run_id)
    new_ids: list[int] = []

    def _work():
        agents_api = _agents_api()
        if agents_api is None:
            return "agents package not available"
        new_ids.extend(agents_api.run_room_pass(
            room, prev_run_id, curr_run_id, seeded) or [])
        return None

    error = _run_stage_with_activity(key, _room_posters, _work)
    engine_bridge.notify_posts(list(new_ids))
    engine_bridge.notify_notifications(list(new_ids))
    return error


def _bg_snapshot(run_id: int, data_through: str | None) -> None:
    agents_api = _agents_api()
    if agents_api is None:
        return
    try:
        result = agents_api.run_snapshot(run_id, data_through=data_through)
    except Exception:
        return
    new_ids = result.get("post_ids") or []
    engine_bridge.notify_posts(new_ids)
    engine_bridge.notify_notifications(new_ids)
    engine_bridge.create_and_notify(
        "snapshot_ready", post_id=(new_ids[0] if new_ids else None), room=3)


@app.post("/api/rooms/3/snapshot")
def create_snapshot(payload: dict, background: BackgroundTasks):
    """SPEC-APP E: create a fresh snapshot, run OUTWARD (+'both') agents'
    checks only, and APPEND the new posts to room 3 — base-pass posts are
    never replaced. `data_through` overrides the default +5-business-day
    walk (capped at the last available date)."""
    payload = payload or {}
    run_id = payload.get("run_id")
    if run_id is None:
        raise HTTPException(422, "run_id is required")
    run = _get_run_or_404(int(run_id))
    if run["status"] != "done":
        raise HTTPException(409, f"run {run_id} is {run['status']}, not done")
    if _agents_api() is None:
        raise HTTPException(503, "agents package not available yet")
    data_through = payload.get("data_through")
    background.add_task(_bg_snapshot, int(run_id), data_through)
    return {"status": "scheduled", "run_id": int(run_id),
            "run": _run_ref(int(run_id))}


@app.get("/api/rooms/3/snapshots")
def list_snapshots(run_id: int):
    """Snapshots for a run, newest first — the room-3 feed groups posts by
    snapshot using this list (SPEC-APP E)."""
    _get_run_or_404(run_id)
    conn = db.get_db()
    return {"run": _run_ref(run_id), "snapshots": conn.execute(
        "SELECT * FROM snapshots WHERE run_id = ? ORDER BY seq DESC",
        (run_id,)).fetchall()}


@app.post("/api/rooms/{room}/refresh")
def refresh_room(room: int, payload: dict, background: BackgroundTasks):
    _check_room(room)
    payload = payload or {}
    pair = payload.get("pair")
    if pair:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            raise HTTPException(422, "pair must be [prev_run_id, curr_run_id]")
        prev_run_id, curr_run_id = int(pair[0]), int(pair[1])
        _get_run_or_404(prev_run_id)
    elif payload.get("run_id") is not None:
        prev_run_id, curr_run_id = None, int(payload["run_id"])
    else:
        raise HTTPException(422, "run_id or pair is required")
    _get_run_or_404(curr_run_id)
    seeded = bool(payload.get("seeded", False))
    if _agents_api() is None:
        raise HTTPException(503, "agents package not available yet")
    background.add_task(_bg_room_pass, room, prev_run_id, curr_run_id, seeded)
    return {"status": "scheduled", "room": room, "prev_run_id": prev_run_id,
            "curr_run_id": curr_run_id, "seeded": seeded,
            # §6: the runs this pass reads, named by label + directory
            "run": _run_ref(curr_run_id), "prev_run": _run_ref(prev_run_id)}


# --- runs ------------------------------------------------------------------

@app.get("/api/runs")
def list_runs():
    conn = db.get_db()
    runs = conn.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
    for r in runs:
        man = engine_bridge.read_manifest(r["out_dir"]) if r["out_dir"] else None
        r["seeded"] = bool(man and man.get("seeded"))
        # PENDING-BATCH2 section 1: the picker reads the LABEL (2603_v1) and
        # the directory, never the integer id — that stays internal to the DB.
        engine_bridge.decorate_run(r)
    return {"runs": runs}


_SEEDED_PATH_ROOTS = (config.SCENARIOS_DIR, config.PROJECT_ROOT / "book")


def _validated_seeded_path(value, label: str) -> str | None:
    """Allowlist for POST /api/runs seeded inputs (security audit 1d): only
    files under scenarios/ or book/ may be named — never ground truth,
    never an arbitrary local file bounced through engine stderr."""
    if value is None:
        return None
    p = Path(str(value))
    p = (p if p.is_absolute() else config.PROJECT_ROOT / p).resolve()
    if "ground_truth" in p.name:
        raise HTTPException(422, f"{label} may not reference ground truth")
    for root in _SEEDED_PATH_ROOTS:
        root = root.resolve()
        if p == root or p.is_relative_to(root):
            if not p.is_file():
                raise HTTPException(422, f"{label} not found: {p.name}")
            return str(p)
    raise HTTPException(
        422, f"{label} must live under scenarios/ or book/ (got {p.name})")


@app.post("/api/runs")
def create_run(payload: dict, background: BackgroundTasks):
    payload = payload or {}
    asof = payload.get("asof")
    if not asof:
        raise HTTPException(422, "asof is required")
    try:
        run = engine_bridge.create_run(
            asof=asof, kind="base",
            seeded_assumptions_path=_validated_seeded_path(
                payload.get("seeded_assumptions"), "seeded_assumptions"),
            seeded_book_path=_validated_seeded_path(
                payload.get("seeded_book"), "seeded_book"),
            seeded_liabilities_path=_validated_seeded_path(
                payload.get("seeded_liabilities"), "seeded_liabilities"),
            sims=payload.get("sims"), seed=payload.get("seed"))
    except FileNotFoundError as e:
        raise HTTPException(422, str(e))
    background.add_task(engine_bridge.execute_run, run["id"])
    return {"run": engine_bridge.decorate_run(run)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    run = _get_run_or_404(run_id)
    conn = db.get_db()
    events = conn.execute(
        "SELECT * FROM stage_events WHERE run_id = ? ORDER BY id",
        (run_id,)).fetchall()
    run["seeded"] = bool((engine_bridge.read_manifest(run["out_dir"]) or {}
                          ).get("seeded")) if run["out_dir"] else False
    engine_bridge.decorate_run(run)
    return {"run": run, "stage_events": events}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: int, payload: dict | None = None):
    """PENDING-ROSTER J — stop an in-flight run. Terminates the engine
    subprocess, marks the run `stopped` and keeps the partial stage events.
    A stopped run stays visible as history but is not a basis for
    attribution or a room pass (enforced in the frontend picker)."""
    _get_run_or_404(run_id)
    try:
        run = engine_bridge.request_stop(run_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    run["seeded"] = bool((engine_bridge.read_manifest(run["out_dir"]) or {}
                          ).get("seeded")) if run["out_dir"] else False
    engine_bridge.decorate_run(run)
    return {"run": run, "stopped_by": (payload or {}).get("stopped_by")}


@app.get("/api/runs/{run_id}/scenario")
def run_scenario(run_id: int, rank: int | None = None,
                 index: int | None = None, percentile: float | None = None):
    """PENDING-ROSTER M/N — the scenario explorer's data: one simulation
    out of the run's retained draws, by loss rank (default: the VaR
    scenario) or raw index. Engine outputs read off disk; no AI in the
    path, exactly like the dashboard endpoints."""
    run = _get_run_or_404(run_id)
    if run["status"] != "done":
        raise HTTPException(409, f"run {run_id} is {run['status']}")
    try:
        from app.agents import tools  # noqa: PLC0415 (guarded, lazy)
    except Exception as e:                     # pragma: no cover - no agents
        raise HTTPException(501, f"scenario tools unavailable: {e}")
    if rank is None and index is None and percentile is not None:
        n = int(run["sims"] or config.DEFAULT_SIMS)
        rank = max(1, int(round((1.0 - float(percentile)) * n)))
    try:
        scenario = tools.read_scenario(str(run_id), rank=rank, index=index)
    except tools.ToolError as e:
        raise HTTPException(422, str(e))
    return {"run": engine_bridge.decorate_run(run), "scenario": scenario}


def _sse_format(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: int, request: Request):
    """SSE: replay of this run's stored stage events, then live stage events
    and run-status changes for the run, plus new-post notifications (all
    rooms) for live feed updates."""
    _get_run_or_404(run_id)

    async def gen():
        sub_id, q = broker.subscribe(run_id=run_id)
        try:
            conn = db.get_db()
            for ev in conn.execute(
                    "SELECT * FROM stage_events WHERE run_id = ? ORDER BY id",
                    (run_id,)).fetchall():
                yield _sse_format("stage", ev)
            yield _sse_format("run_status", engine_bridge.get_run(run_id))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse_format(item["event"], item["data"])
        finally:
            broker.unsubscribe(sub_id)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/events")
async def global_events(request: Request):
    """SSE with no run attached — the stream the app can hold open before
    (and between) runs. Carries the broadcast events: new posts,
    notifications, and the `activity` / `cycle` / `research` stage events
    of PENDING-BATCH2 §3-§4. Opens by replaying the current state of all
    four stages so a client that connects mid-pass is immediately correct
    rather than blank until the next transition."""
    async def gen():
        sub_id, q = broker.subscribe(run_id=None)
        try:
            for stage in ("research", 1, 2, 3):
                yield _sse_format("activity",
                                  _activity_payload(_activity_key(stage)))
            yield _sse_format("cycle", _cycle_payload())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse_format(item["event"], item["data"])
        finally:
            broker.unsubscribe(sub_id)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --- gates -----------------------------------------------------------------

def _decorate_gate(gate: dict) -> dict:
    """A gate names two runs — the one it was raised against and the one an
    approval produced. Both are named by LABEL and directory (§6)."""
    gate = dict(gate)
    gate["run"] = _run_ref(gate.get("run_id"))
    gate["result_run"] = _run_ref(gate.get("result_run_id"))
    return gate


@app.get("/api/gates")
def list_gates(status: str | None = None):
    conn = db.get_db()
    if status:
        gates = conn.execute("SELECT * FROM gates WHERE status = ? "
                             "ORDER BY id DESC", (status,)).fetchall()
    else:
        gates = conn.execute("SELECT * FROM gates ORDER BY id DESC").fetchall()
    return {"gates": [_decorate_gate(g) for g in gates]}


def _get_gate_or_404(gate_id: int) -> dict:
    gate = db.get_db().execute("SELECT * FROM gates WHERE id = ?",
                               (gate_id,)).fetchone()
    if gate is None:
        raise HTTPException(404, f"no such gate: {gate_id}")
    return _decorate_gate(gate)


@app.post("/api/gates/{gate_id}/approve")
def approve_gate(gate_id: int, payload: dict, background: BackgroundTasks):
    """A named human disposes: approval creates + executes the corrected
    rerun (derived assumptions YAML, lineage recorded)."""
    gate = _get_gate_or_404(gate_id)
    if gate["status"] != "pending":
        raise HTTPException(409, f"gate is {gate['status']}, not pending")
    decided_by = ((payload or {}).get("decided_by") or "").strip()
    if not decided_by:
        raise HTTPException(422, "decided_by is required (a named human)")
    conn = db.get_db()
    adjustments = (json.loads(gate["adjustments_json"])
                   if gate["adjustments_json"] else None)
    run = engine_bridge.create_run(
        asof=None, kind="rerun", parent_run_id=gate["run_id"],
        adjustments_json=adjustments)
    conn.execute(
        "UPDATE gates SET status = 'approved', decided_by = ?, decided_at = ?, "
        "result_run_id = ? WHERE id = ?",
        (decided_by, _now(), run["id"], gate_id))
    conn.commit()
    background.add_task(engine_bridge.execute_run, run["id"])
    return {"gate": _get_gate_or_404(gate_id),
            "run": engine_bridge.decorate_run(run)}


@app.post("/api/gates/{gate_id}/reject")
def reject_gate(gate_id: int, payload: dict):
    gate = _get_gate_or_404(gate_id)
    if gate["status"] != "pending":
        raise HTTPException(409, f"gate is {gate['status']}, not pending")
    decided_by = ((payload or {}).get("decided_by") or "").strip()
    if not decided_by:
        raise HTTPException(422, "decided_by is required (a named human)")
    conn = db.get_db()
    conn.execute("UPDATE gates SET status = 'rejected', decided_by = ?, "
                 "decided_at = ? WHERE id = ?", (decided_by, _now(), gate_id))
    conn.commit()
    return {"gate": _get_gate_or_404(gate_id)}


# --- notifications (SPEC-APP section F) -------------------------------------

_NOTIFICATIONS_CAP = 50


def _notification_with_excerpt(conn, n: dict) -> dict:
    n = dict(n)
    n["read"] = n.get("read_at") is not None
    if n.get("post_id") is not None:
        post = conn.execute(
            "SELECT author_label, body_md, room FROM posts WHERE id = ?",
            (n["post_id"],)).fetchone()
        if post:
            n["author_label"] = post["author_label"]
            n["excerpt"] = (post["body_md"] or "")[:160]
            if n.get("room") is None:
                n["room"] = post["room"]
    return n


def _unread_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
    ).fetchone()["n"]


@app.get("/api/notifications")
def list_notifications(unread_only: bool = False,
                       limit: int = _NOTIFICATIONS_CAP):
    """PENDING-BATCH2 §6 — history, not a disappearing act. A read
    notification KEEPS its row and stays in the list, greyed; unread sit
    above read, each half newest-first, and the list is capped at 50."""
    conn = db.get_db()
    limit = max(1, min(int(limit or _NOTIFICATIONS_CAP), _NOTIFICATIONS_CAP))
    q = "SELECT * FROM notifications"
    if unread_only:
        q += " WHERE read_at IS NULL"
    # unread first, then read; newest first within each half
    q += " ORDER BY (read_at IS NOT NULL) ASC, id DESC LIMIT ?"
    rows = [_notification_with_excerpt(conn, n)
            for n in conn.execute(q, (limit,)).fetchall()]
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    return {"notifications": rows, "unread_count": _unread_count(conn),
            "total": total, "cap": _NOTIFICATIONS_CAP,
            "truncated": total > len(rows) and not unread_only}


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int):
    """Sets `read_at`; the row is kept. Re-reading does not move the
    timestamp — when it was first seen is the useful fact."""
    conn = db.get_db()
    row = conn.execute("SELECT * FROM notifications WHERE id = ?",
                       (notification_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no such notification: {notification_id}")
    conn.execute("UPDATE notifications SET read_at = ? WHERE id = ? "
                 "AND read_at IS NULL", (_now(), notification_id))
    conn.commit()
    return {"notification": _notification_with_excerpt(conn, conn.execute(
        "SELECT * FROM notifications WHERE id = ?",
        (notification_id,)).fetchone()),
        "unread_count": _unread_count(conn)}


@app.post("/api/notifications/read_all")
def read_all_notifications():
    """"Mark all read" greys everything; nothing is removed."""
    conn = db.get_db()
    cur = conn.execute(
        "UPDATE notifications SET read_at = ? WHERE read_at IS NULL",
        (_now(),))
    conn.commit()
    return {"status": "ok", "marked_read": cur.rowcount,
            "unread_count": _unread_count(conn)}


# --- dashboards (no AI in this path) ---------------------------------------

@app.get("/api/dashboard/{room}")
def dashboard(room: int, run: int | None = None, pair: str | None = None):
    """Numbers straight from engine output files. `pair=<prev,curr>` are run
    ids; attribution is served from the committed attr outputs when the pair
    maps onto known month-ends."""
    _check_room(room)
    prev_run = curr_run = None
    if pair:
        try:
            prev_id, curr_id = (int(x) for x in pair.split(","))
        except ValueError:
            raise HTTPException(422, "pair must be '<prev_run_id>,<curr_run_id>'")
        prev_run, curr_run = _get_run_or_404(prev_id), _get_run_or_404(curr_id)
    elif run is not None:
        curr_run = _get_run_or_404(run)
    else:
        raise HTTPException(422, "run or pair query parameter is required")

    res: dict = {"room": room, "room_name": ROOM_NAMES[room]}
    res["current"] = _run_outputs(curr_run)
    if prev_run is not None:
        res["previous"] = _run_outputs(prev_run)
        res["attribution"] = _attribution_for_pair(prev_run, curr_run)

    if room == 1:
        # assumption-facing extras: the actual calibration inputs used
        man = engine_bridge.read_manifest(curr_run["out_dir"]) or {}
        apath = man.get("assumptions_path")
        if apath and Path(apath).exists():
            with open(apath, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
            res["assumptions"] = {k: doc.get(k) for k in
                                  ("meta", "curves", "spreads", "equity",
                                   "fx", "vols")}
    elif room == 2:
        conn = db.get_db()
        res["stage_events"] = conn.execute(
            "SELECT * FROM stage_events WHERE run_id = ? ORDER BY id",
            (curr_run["id"],)).fetchall()
    return res


# --- research (SPEC-APP 5.1, PENDING-BATCH2 §2: the stage that runs FIRST) --

def _research_module():
    try:
        from app.agents import research  # noqa: PLC0415
        return research
    except Exception:
        raise HTTPException(503, "research module not available yet")


@app.get("/api/research")
def research_note(asof: str, agent: str = "focused"):
    """One research report for a month-end (`?asof=YYYY-MM` or an ISO date,
    `?agent=focused|wide-eye`) — regenerated deterministically on each
    request from data/processed/*.csv only (never assumptions/ or engine
    outputs), so a refresh always serves a note consistent with the current
    source data. Rendered read-only by the UI; `agent` defaults to
    @focused's note, which is what the tab showed before there were two."""
    # SERVE THE NOTE ON DISK, do not regenerate it. Regenerating produced a
    # fresh mock note on every read (no web context here), which overwrote a
    # live web-researched note and made the tab claim "Mock mode". A note is
    # written once, by the research stage; if there is none, say so rather
    # than manufacturing one.
    if not re.match(r"^\d{4}-\d{2}(-\d{2})?$", str(asof)):
        raise HTTPException(422, "asof must be 'YYYY-MM' or an ISO date")
    research = _research_module()
    month = str(asof)[:7]
    fname = f"{month.replace('-', '_')}_{agent}.md"
    fpath = research.RESEARCH_DIR / fname
    if not fpath.is_file():
        return {"month": month, "asof": asof, "agent": agent,
                "prev_asof": None, "path": None, "file": None,
                "markdown": None, "missing": True}
    return {"month": month, "asof": asof, "agent": agent,
            "prev_asof": None, "path": str(fpath), "file": fname,
            "markdown": fpath.read_text(encoding="utf-8")}


@app.get("/api/research/reports")
def research_reports(asof: str | None = None):
    """Both reports for a month, NEWEST FIRST — agent, month covered, file
    and the time the report was last generated (PENDING-BATCH2 §2, the
    research tab). Content comes from GET /api/research?agent=; this is the
    index. A report that has never been generated is listed with
    `generated_at: null` rather than hidden, so the tab can say so."""
    research = _research_module()
    month = None
    if asof:
        month = str(asof)[:7]
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise HTTPException(422, f"asof must be 'YYYY-MM' or an ISO "
                                     f"date, got {asof!r}")
    out = []
    for agent in research.AGENTS:
        months = [month] if month else sorted(
            {p.name[:7].replace("_", "-")
             for p in research.RESEARCH_DIR.glob(f"*_{agent}.md")},
            reverse=True)
        for m in months:
            path = research.RESEARCH_DIR / research.note_filename(m, agent)
            exists = path.exists()
            out.append({
                "agent": agent, "month": m,
                "file": path.name,
                "path": str(path) if exists else None,
                "generated_at": (datetime.datetime.fromtimestamp(
                    path.stat().st_mtime,
                    datetime.timezone.utc).isoformat() if exists else None),
                "bytes": path.stat().st_size if exists else None,
            })
    out.sort(key=lambda r: (r["month"], r["generated_at"] or ""), reverse=True)
    return {"reports": out, "month": month}


def _bg_research_pass(month: str) -> str | None:
    """The research stage, with the same activity state as a room pass —
    it is a stage of the cycle, not a side errand (§2, §3)."""
    key = "research"
    _activity_begin(key, expected=_research_roster_handles(), month=month)
    box: dict = {}

    def _work():
        agents_api = _agents_api()
        if agents_api is None:
            return "agents package not available"
        box["result"] = agents_api.run_research_pass(month)
        errs = box["result"].get("errors") or []
        if errs and not (box["result"].get("reports") or []):
            return "; ".join(str(e.get("error") or e) for e in errs)
        return None

    error = _run_stage_with_activity(key, _research_posters, _work)
    result = box.get("result") or {"reports": [],
                                   "errors": [{"error": error or "pass failed"}]}
    broker.publish("research", {"month": month,
                                "reports": result.get("reports") or [],
                                "errors": result.get("errors") or []},
                   run_id=None)
    return error


@app.post("/api/research/run")
def run_research(payload: dict, background: BackgroundTasks):
    """PENDING-BATCH2 §2: run the RESEARCH STAGE for a month — the first
    stage of a cycle (research → room 1 → room 2 → room 3). Regenerates both
    reports; the room posts that cite them are written by the room passes
    that follow. `month` accepts 'YYYY-MM', an ISO date or a run id."""
    payload = payload or {}
    month = payload.get("month", payload.get("asof", payload.get("run_id")))
    if month is None:
        raise HTTPException(422, "month is required")
    agents_api = _agents_api()
    if agents_api is None:
        raise HTTPException(503, "agents package not available yet")
    try:
        month = agents_api._resolve_month(month)
    except ValueError as e:
        raise HTTPException(422, str(e))
    background.add_task(_bg_research_pass, month)
    return {"status": "scheduled", "stage": "research", "month": month,
            "cycle": list(agents_api.CYCLE_STAGES)}


# --- run all: the whole cycle, chained (PENDING-BATCH2 §3) -----------------
#
#     research -> room 1 -> room 2 -> room 3
#
# The rooms stay individually runnable in any order — nothing here blocks
# them — but "Run all" is the intended sequence, and every stage transition
# goes out over SSE as a `cycle` event alongside the per-stage `activity`
# events, so the strip can light up without polling.

_CYCLE_LOCK = threading.RLock()
_CYCLE: dict = {"cycle_id": None, "status": "idle", "month": None,
                "stage": None, "stages": [], "run": None, "prev_run": None,
                "started_at": None, "finished_at": None, "error": None}


def _cycle_payload() -> dict:
    with _CYCLE_LOCK:
        state = dict(_CYCLE)
        state["stages"] = [dict(s) for s in state.get("stages") or []]
    return state


def _publish_cycle() -> None:
    try:
        broker.publish("cycle", _cycle_payload(), run_id=None)
    except Exception:
        pass


def _cycle_set(**fields) -> None:
    with _CYCLE_LOCK:
        _CYCLE.update(fields)
    _publish_cycle()


def _cycle_stage_status(stage, status: str, error: str | None = None) -> None:
    with _CYCLE_LOCK:
        for s in _CYCLE["stages"]:
            if s["stage"] == stage:
                s["status"] = status
                s["error"] = error
                s["at"] = _now()
        _CYCLE["stage"] = stage if status == "running" else _CYCLE["stage"]
    _publish_cycle()


def _bg_cycle(cycle_id: str, month: str, prev_run_id: int | None,
              curr_run_id: int, seeded: bool) -> None:
    _cycle_set(status="running", started_at=_now(), finished_at=None,
               error=None)
    failed = None
    for stage in ("research", 1, 2, 3):
        with _CYCLE_LOCK:
            if _CYCLE.get("cycle_id") != cycle_id:
                return  # superseded by a newer cycle
        _cycle_stage_status(stage, "running")
        if stage == "research":
            err = _bg_research_pass(month)
        else:
            err = _bg_room_pass(int(stage), prev_run_id, curr_run_id, seeded)
        _cycle_stage_status(stage, "failed" if err else "done", error=err)
        if err:
            failed = f"{_STAGE_LABELS.get(stage, stage)}: {err}"
            break
    _cycle_set(status="failed" if failed else "done", stage=None,
               finished_at=_now(), error=failed)


def _latest_run_for_month(conn, month: str) -> dict | None:
    """The run a cycle's room passes should read: the newest DONE run for
    the month, falling back to the newest run of any status so a caller
    still gets a clear 409 rather than a silent 'no such month'."""
    done = conn.execute(
        "SELECT * FROM runs WHERE substr(asof, 1, 7) = ? AND status = 'done' "
        "ORDER BY id DESC LIMIT 1", (month,)).fetchone()
    if done is not None:
        return done
    return conn.execute(
        "SELECT * FROM runs WHERE substr(asof, 1, 7) = ? ORDER BY id DESC "
        "LIMIT 1", (month,)).fetchone()


def _prior_month_run(conn, month: str) -> dict | None:
    """The newest done run of the newest earlier month — the `prev` half of
    the pair a month-on-month pass compares against."""
    return conn.execute(
        "SELECT * FROM runs WHERE substr(asof, 1, 7) < ? AND status = 'done' "
        "ORDER BY substr(asof, 1, 7) DESC, id DESC LIMIT 1",
        (month,)).fetchone()


@app.post("/api/cycle/run")
def run_cycle(payload: dict, background: BackgroundTasks):
    """PENDING-BATCH2 §3 "Run all" — chain research -> room 1 -> room 2 ->
    room 3 for a month as one background task.

    `month` accepts 'YYYY-MM', an ISO date or a run id. The run the rooms
    read is the month's newest completed run unless `run_id` names one, and
    the comparison run is the newest completed run of an earlier month
    unless `pair` gives both. The response names both runs by LABEL and
    directory (§6)."""
    payload = payload or {}
    month = payload.get("month", payload.get("asof", payload.get("run_id")))
    if month is None:
        raise HTTPException(422, "month is required")
    agents_api = _agents_api()
    if agents_api is None:
        raise HTTPException(503, "agents package not available yet")
    try:
        month = agents_api._resolve_month(month)
    except ValueError as e:
        raise HTTPException(422, str(e))

    conn = db.get_db()
    pair = payload.get("pair")
    if pair:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            raise HTTPException(422, "pair must be [prev_run_id, curr_run_id]")
        prev_run = _get_run_or_404(int(pair[0]))
        curr_run = _get_run_or_404(int(pair[1]))
    else:
        if payload.get("run_id") is not None:
            curr_run = _get_run_or_404(int(payload["run_id"]))
        else:
            curr_run = _latest_run_for_month(conn, month)
        if curr_run is None:
            raise HTTPException(
                422, f"no run exists for {month} — create a run for that "
                     f"month before running its cycle")
        prev_run = _prior_month_run(conn, str(curr_run["asof"])[:7])
    if curr_run["status"] != "done":
        raise HTTPException(
            409, f"run {engine_bridge.run_identity(curr_run).get('label')} "
                 f"is {curr_run['status']}, not done")

    seeded = bool(payload.get("seeded", False))
    cycle_id = uuid.uuid4().hex[:12]
    stages = [{"stage": s, "label": _STAGE_LABELS.get(s, str(s)),
               "status": "idle", "error": None, "at": None}
              for s in ("research", 1, 2, 3)]
    _cycle_set(cycle_id=cycle_id, status="scheduled", month=month,
               stage=None, stages=stages,
               run=_run_ref(curr_run["id"]),
               prev_run=_run_ref(prev_run["id"]) if prev_run else None,
               started_at=None, finished_at=None, error=None)
    background.add_task(_bg_cycle, cycle_id, month,
                        prev_run["id"] if prev_run else None,
                        curr_run["id"], seeded)
    return {"status": "scheduled", "cycle_id": cycle_id, "month": month,
            "stages": [s["stage"] for s in stages],
            "run": _run_ref(curr_run["id"]),
            "prev_run": _run_ref(prev_run["id"]) if prev_run else None,
            "seeded": seeded}


@app.get("/api/cycle/status")
def cycle_status():
    """Where the chained cycle has got to, plus each stage's own activity."""
    payload = _cycle_payload()
    payload["activity"] = [_activity_payload(_activity_key(s))
                           for s in ("research", 1, 2, 3)]
    return payload


# --- scorecard -------------------------------------------------------------

def _load_ground_truth() -> dict | None:
    p = config.GROUND_TRUTH_PATH
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _defect_matches(defect: dict, text: str) -> bool:
    """Tolerant matching: a published post 'detects' a defect if it names the
    defect id, the seeded field, or the seeded file.

    A defect MAY instead carry an explicit `match_any` token list, which is
    then used on its own. That is how two defects seeded into the SAME file
    stay separable: `positions_D2_D4.json` contains both "D2" and "D4" as
    substrings, so filename matching alone would credit each catch to both
    defects. The direction check would still score them correctly, but the
    reported post ids would be wrong, and a scorecard that misattributes
    evidence is not worth reading."""
    text_l = text.lower()
    explicit = defect.get("match_any")
    if explicit:
        return any(str(t).lower() in text_l for t in explicit if t)
    tokens = []
    for key in ("id", "field"):
        if defect.get(key):
            tokens.append(str(defect[key]).lower())
    if defect.get("file"):
        tokens.append(Path(str(defect["file"])).name.lower())
    return any(t and t in text_l for t in tokens)


_DIRECTION_STEMS = {"overstatement": "overstat",
                    "understatement": "understat"}


def _direction_ok(defect: dict, text: str) -> bool:
    """Direction-aware scoring (ground truth v2): where a defect carries a
    plain direction (D4: overstatement), a correct catch must characterise
    it that way — an overstatement caught as an understatement is a miss."""
    stem = _DIRECTION_STEMS.get(str(defect.get("direction") or "").strip())
    if stem is None:
        return True
    return stem in text.lower()


def _mfc_hits(item: dict, posts: list[dict]) -> tuple[list[int], list[int]]:
    """must_flag_changes matching: the allocation change is surfaced when a
    published agent post declares a material allocation change naming the
    changed sleeve (or the changed book file). Two phrasings are accepted:
    @holdings' original "material allocation change" (room 1, pre-rename)
    and @warden's month-end-summary phrasing, "not flow, it is a decision"
    (room 3 — the duty moved there; PENDING-ROSTER renames). Returns (hit
    ids, ids of hits that mischaracterise the change as an error/defect)."""
    curr_book = Path(str(item.get("curr_book") or "")).name.lower()
    hits, mischaracterised = [], []
    for p in posts:
        text = ((p["body_md"] or "") + " " + (p["claims_json"] or "")).lower()
        if not ("material allocation change" in text
                or "it is a decision" in text):
            continue
        if not ("private credit" in text or "pc sleeve" in text
                or (curr_book and curr_book in text)):
            continue
        hits.append(p["id"])
        if "flag —" in text or "defect" in text or "seeded" in text:
            mischaracterised.append(p["id"])
    return hits, mischaracterised


@app.get("/api/scorecard")
def scorecard():
    conn = db.get_db()
    published = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE status = 'published'"
    ).fetchone()["n"]
    suppressed = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE status = 'suppressed'"
    ).fetchone()["n"]
    total_posts = published + suppressed

    claims_total = claims_bound = 0
    for row in conn.execute(
            "SELECT claims_json FROM posts WHERE status = 'published' "
            "AND claims_json IS NOT NULL").fetchall():
        try:
            claims = json.loads(row["claims_json"]) or []
        except json.JSONDecodeError:
            continue
        claims_total += len(claims)
        claims_bound += sum(1 for c in claims if c.get("tool_call_id"))
    tool_call_count = conn.execute(
        "SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"]
    quiet = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE status = 'published' "
        "AND significance = 'quiet'").fetchone()["n"]

    result: dict = {
        "posts": {"published": published, "suppressed": suppressed,
                  "total": total_posts},
        "suppression_rate": (suppressed / total_posts) if total_posts else 0.0,
        # SPEC-APP G: "a credibility metric... a system that mostly says
        # nothing is a system worth reading when it does." Denominator is
        # published posts only — a suppressed post never had a level.
        "quiet_rate": (quiet / published) if published else 0.0,
        "citations": {"claims_total": claims_total,
                      "claims_bound": claims_bound,
                      "binding_rate": (claims_bound / claims_total)
                                      if claims_total else None,
                      "tool_calls_recorded": tool_call_count},
        "detection": None,
    }

    # Detection vs ground truth — only when a seeded run is active. The
    # SERVER may read ground truth for scoring; agents may not.
    seeded_runs = [r for r in conn.execute(
        "SELECT * FROM runs ORDER BY id DESC").fetchall()
        if r["out_dir"] and (engine_bridge.read_manifest(r["out_dir"]) or {}
                             ).get("seeded")]
    gt = _load_ground_truth()
    if seeded_runs and gt:
        defects = gt.get("defects") or [v for k, v in gt.items()
                                        if isinstance(v, dict)
                                        and str(k).lower().startswith("d")]
        must_not = gt.get("must_not_flag") or []
        if isinstance(must_not, dict):
            must_not = [must_not]
        agent_posts = conn.execute(
            "SELECT id, body_md, claims_json FROM posts WHERE "
            "status = 'published' AND agent_id IS NOT NULL").fetchall()

        must_flag = gt.get("must_flag_changes") or []
        if isinstance(must_flag, dict):
            must_flag = [must_flag]

        per_defect = []
        detected_n = 0
        for d in defects:
            hits = []
            direction_ok = False
            for p in agent_posts:
                text = ((p["body_md"] or "") + " "
                        + (p["claims_json"] or ""))
                if _defect_matches(d, text):
                    hits.append(p["id"])
                    if _direction_ok(d, text):
                        direction_ok = True
            detected = bool(hits) and direction_ok
            per_defect.append({"id": d.get("id"), "field": d.get("field"),
                               "severity": d.get("severity"),
                               "direction": d.get("direction"),
                               "direction_ok": direction_ok if hits else None,
                               "detected": detected, "post_ids": hits})
            detected_n += detected

        # must_flag_changes (ground truth v2): the +15% PC allocation change
        # must be surfaced — with roughly right magnitude — and must NOT be
        # characterised as an error. Scored alongside defect detection.
        per_mfc = []
        mfc_detected_n = 0
        for item in must_flag:
            hits, mischaracterised = _mfc_hits(item, agent_posts)
            detected = bool(hits) and not mischaracterised
            per_mfc.append({"id": item.get("id"), "detected": detected,
                            "post_ids": hits,
                            "mischaracterised_post_ids": mischaracterised})
            mfc_detected_n += detected

        false_flags = []
        for item in must_not:
            hits = [p["id"] for p in agent_posts
                    if _defect_matches(item, p["body_md"] or "")
                    and any(w in (p["body_md"] or "").lower()
                            for w in ("defect", "error", "wrong", "seeded",
                                      "incorrect", "flag"))]
            if hits:
                false_flags.append({"item": item.get("id") or item.get("field"),
                                    "post_ids": hits})
        n_findable = (len(defects) + len(must_flag)) or 1
        found_n = detected_n + mfc_detected_n
        result["detection"] = {
            "seeded_run_ids": [r["id"] for r in seeded_runs],
            "defects": per_defect,
            "must_flag_changes": per_mfc,
            "recall": found_n / n_findable,
            "precision": (found_n / (found_n + len(false_flags)))
                         if (found_n + len(false_flags)) else None,
            "must_not_flag_violations": false_flags,
        }
    return result


# --- agents (builder) ------------------------------------------------------

@app.get("/api/agents/{room}")
def list_agents(room: int):
    """Agents that post in this room — including those scheduled here via
    also_posts_in (@focused and @red-team in rooms 1 and 3, @story in all
    three). Home room alone would omit them from a room they post in
    every pass."""
    _check_room(room)
    from app.agents import api as _api  # noqa: PLC0415
    return {"agents": _api.agents_in_room(db.get_db(), room)}


_OUTLOOKS = ("internal", "outward", "both")


def _reads_from_payload(payload: dict, key: str) -> str | None:
    """A reads_from/reads_on_request list from the payload, JSON-encoded
    for storage (SPEC-APP E, H). None when absent; [] clears it."""
    if key not in payload:
        return None, False
    value = payload.get(key) or []
    if not isinstance(value, list) or not all(isinstance(x, str)
                                              for x in value):
        raise HTTPException(422, f"{key} must be a list of strings")
    return (json.dumps(value) if value else None), True


@app.post("/api/agents/{room}")
def create_agent(room: int, payload: dict):
    _check_room(room)
    payload = payload or {}
    handle = (payload.get("handle") or "").strip()
    if not handle:
        raise HTTPException(422, "handle is required")
    if not handle.startswith("@"):
        handle = "@" + handle
    avatars = _avatars_mod()
    avatar_json = None
    if avatars is not None:
        # section 8.1: the default rule (initials + curated-palette bg +
        # luminance fg) is assigned AT CREATION and stored; a caller-supplied
        # avatar is validated and completed against the same rule.
        avatar_json = avatars.normalize(payload.get("avatar_json"),
                                        payload.get("name"), handle)
    outlook = payload.get("outlook", "internal")
    if outlook not in _OUTLOOKS:
        raise HTTPException(422, f"outlook must be one of {_OUTLOOKS}")
    reads_from, _ = _reads_from_payload(payload, "reads_from")
    reads_on_request, _ = _reads_from_payload(payload, "reads_on_request")
    agents_api = _agents_api()
    if agents_api is not None and reads_from:
        try:
            agents_api.validate_reads_from(
                db.get_db(), handle, json.loads(reads_from))
        except ValueError as e:
            raise HTTPException(422, str(e))
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO agents (room, handle, name, focus, persona_prompt, "
            "avatar_json, builtin, outlook, reads_from, reads_on_request) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (room, handle, payload.get("name"), payload.get("focus"),
             payload.get("persona_prompt"), avatar_json, outlook,
             reads_from, reads_on_request))
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        # handles are globally unique across rooms (SPEC-APP section 3)
        raise HTTPException(409, f"handle {handle} already exists")
    agent = conn.execute("SELECT * FROM agents WHERE id = ?",
                         (cur.lastrowid,)).fetchone()
    return {"agent": agent}


@app.patch("/api/agents/{room}/{agent_id}")
def edit_agent(room: int, agent_id: int, payload: dict):
    """Edit an existing agent (SPEC-APP section 7): name, focus,
    persona_prompt, avatar_json. Builtins are editable too (single-user
    tool); the HANDLE is immutable."""
    _check_room(room)
    payload = payload or {}
    conn = db.get_db()
    agent = conn.execute("SELECT * FROM agents WHERE id = ? AND room = ?",
                         (agent_id, room)).fetchone()
    if agent is None:
        raise HTTPException(404, f"no such agent in room {room}: {agent_id}")
    if "handle" in payload and (payload.get("handle") or "").strip() not in (
            "", agent["handle"]):
        raise HTTPException(422, "handle is immutable")
    updates, params = [], []
    for field in ("name", "focus", "persona_prompt"):
        if field in payload:
            updates.append(f"{field} = ?")
            params.append(payload.get(field))
    if "outlook" in payload:
        outlook = payload.get("outlook")
        if outlook not in _OUTLOOKS:
            raise HTTPException(422, f"outlook must be one of {_OUTLOOKS}")
        updates.append("outlook = ?")
        params.append(outlook)
    for key in ("reads_from", "reads_on_request"):
        if key in payload:
            value, _ = _reads_from_payload(payload, key)
            if key == "reads_from" and value:
                agents_api = _agents_api()
                if agents_api is not None:
                    try:
                        agents_api.validate_reads_from(
                            conn, agent["handle"], json.loads(value))
                    except ValueError as e:
                        raise HTTPException(422, str(e))
            updates.append(f"{key} = ?")
            params.append(value)
    if "avatar_json" in payload:
        avatars = _avatars_mod()
        name = payload.get("name", agent["name"])
        value = payload.get("avatar_json")
        if avatars is not None:
            value = avatars.normalize(value, name, agent["handle"])
        elif isinstance(value, dict):
            value = json.dumps(value)
        updates.append("avatar_json = ?")
        params.append(value)
    if updates:
        params.append(agent_id)
        conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = ?",
                     params)
        conn.commit()
    agent = conn.execute("SELECT * FROM agents WHERE id = ?",
                         (agent_id,)).fetchone()
    return {"agent": agent}


# --- agent profile (PENDING-BATCH2 §5) -------------------------------------

def _json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _grants_for(handle: str) -> dict:
    """What this persona may reach: the shared local tool registry, plus
    Anthropic's server-side web search for the outward-looking agents only
    (`runtime.WEB_SEARCH_HANDLES`). Reported even in mock — it is a property
    of the persona, not of the mode it happens to be running in."""
    tool_names, web = [], False
    try:
        from app.agents import tools  # noqa: PLC0415
        for spec in tools.TOOL_SPECS:
            name = spec.get("name") if isinstance(spec, dict) else None
            if name:
                tool_names.append(name)
    except Exception:
        pass
    try:
        from app.agents import runtime  # noqa: PLC0415
        web = handle in runtime.WEB_SEARCH_HANDLES
    except Exception:
        pass
    return {"tools": tool_names, "web_search": web,
            "tool_count": len(tool_names) + (1 if web else 0)}


def _is_modified_builtin(agent: dict) -> bool:
    """§11: an edited builtin is marked "modified" so a changed prompt is
    never invisible. False when the shipped persona cannot be consulted."""
    if not agent.get("builtin"):
        return False
    agents_api = _agents_api()
    if agents_api is None:
        return False
    try:
        shipped = {p.get("handle"): p for p in agents_api.builtin_personas()}
    except Exception:
        return False
    p = shipped.get(agent["handle"])
    if p is None:
        return False
    return any((agent.get(f) or None) != (p.get(f) or None)
               for f in ("name", "focus", "persona_prompt", "outlook"))


def _thread_root_ids(conn) -> dict[int, int]:
    """post id -> the id of the root of its thread, for every post."""
    parents = {r["id"]: r["parent_id"] for r in
               conn.execute("SELECT id, parent_id FROM posts").fetchall()}
    roots: dict[int, int] = {}

    def _root(pid: int) -> int:
        chain = []
        while pid is not None and pid not in roots:
            chain.append(pid)
            nxt = parents.get(pid)
            if nxt is None:
                roots[pid] = pid
                break
            pid = nxt
        base = roots.get(pid, pid)
        for cid in chain:
            roots[cid] = base
        return base

    for pid in parents:
        _root(pid)
    return roots


@app.get("/api/agents/{handle}/profile")
def agent_profile(handle: str):
    """PENDING-BATCH2 §5 — one agent's page: the persona record (outlook,
    tool grants, web-search grant), its counts, and EVERY post it has made,
    newest first across all rooms and snapshots, each carrying the room and
    the thread it belongs to so the panel can click through. Runs are named
    by LABEL and directory, never by their integer id (§6)."""
    handle = (handle or "").strip()
    if not handle.startswith("@"):
        handle = "@" + handle
    conn = db.get_db()
    agent = conn.execute(
        "SELECT * FROM agents WHERE handle = ? ORDER BY builtin DESC, id "
        "LIMIT 1", (handle,)).fetchone()
    if agent is None:
        raise HTTPException(404, f"no such agent: {handle}")

    agent = dict(agent)
    agent["builtin"] = bool(agent.get("builtin"))
    try:
        agent["avatar"] = (json.loads(agent["avatar_json"])
                           if agent.get("avatar_json") else None)
    except (TypeError, ValueError):
        agent["avatar"] = None
    agent["reads_from"] = _json_list(agent.get("reads_from"))
    agent["reads_on_request"] = _json_list(agent.get("reads_on_request"))
    agent["home_room"] = agent["room"]
    agent["room_name"] = ROOM_NAMES.get(agent["room"])
    agent["modified"] = _is_modified_builtin(agent)

    rows = conn.execute(
        "SELECT * FROM posts WHERE agent_id = ? ORDER BY id DESC",
        (agent["id"],)).fetchall()
    roots = _thread_root_ids(conn) if rows else {}
    run_refs: dict[int, dict | None] = {}
    posts = []
    for p in rows:
        run_id = p.get("run_id")
        if run_id is not None and run_id not in run_refs:
            run_refs[run_id] = _run_ref(run_id)
        posts.append({
            "id": p["id"], "room": p["room"],
            "room_name": ROOM_NAMES.get(p["room"]),
            "type": p["type"], "parent_id": p["parent_id"],
            "thread_id": roots.get(p["id"], p["id"]),
            "snapshot_id": p.get("snapshot_id"),
            "status": p["status"],
            "suppression_reason": p.get("suppression_reason"),
            "significance": p.get("significance"),
            "pinned": bool(p.get("pinned")),
            "body_md": p["body_md"],
            "claims": json.loads(p["claims_json"]) if p.get("claims_json") else [],
            "reply_count": _reply_count(conn, p["id"]),
            "created_at": p["created_at"],
            "run": run_refs.get(run_id) if run_id is not None else None,
            # THE WORKING. The agent's page is where you go to ask "where
            # did that number come from", so the tool calls behind the post
            # belong on it — not one click away in the thread.
            "tool_calls": [
                {"id": t["id"], "name": t["tool"],
                 "args": t["args_json"],
                 "result": (t["result_json"] or "")[:600]}
                for t in conn.execute(
                    "SELECT * FROM tool_calls WHERE post_id = ? ORDER BY id",
                    (p["id"],)).fetchall()],
        })

    def _count(where: str, params=()) -> int:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM posts WHERE agent_id = ? {where}",
            (agent["id"], *params)).fetchone()["n"]

    tool_calls = conn.execute(
        "SELECT COUNT(*) AS n FROM tool_calls tc JOIN posts p "
        "ON p.id = tc.post_id WHERE p.agent_id = ?",
        (agent["id"],)).fetchone()["n"]

    # The agent's SOURCES, deduplicated across everything it has published.
    # For a research agent this list is its provenance: a figure it read on
    # the web stands on these the way an engine figure stands on a tool
    # call, so the reader must be able to open them from its page.
    sources: list[str] = []
    seen = set()
    for r in rows:
        raw = r["web_sources_json"] if "web_sources_json" in r.keys() else None
        if not raw:
            continue
        try:
            for u in json.loads(raw):
                u = str(u).strip()
                if u and u not in seen:
                    seen.add(u)
                    sources.append(u)
        except (TypeError, ValueError):
            continue

    # The agent's own research notes, where it writes them. These are the
    # documents its room posts are drawn from, so they belong on its page
    # next to the sources rather than only in the Research tab.
    notes = []
    try:
        from app.agents import research as _res
        stem = handle.lstrip("@")
        if _res.RESEARCH_DIR.is_dir():
            for f in sorted(_res.RESEARCH_DIR.glob(f"*_{stem}.md"),
                            reverse=True):
                notes.append({"file": f.name, "agent": stem,
                              "month": f.name.split("_")[0] + "-"
                                       + f.name.split("_")[1]})
    except Exception:
        notes = []

    # Sources cited inside the agent's own research notes count too: for a
    # research agent that IS where its references live, and a page that
    # lists the note but not what the note read is only half an answer.
    try:
        import re as _re
        for n in notes:
            f = _res.RESEARCH_DIR / n["file"]
            for u in _re.findall(r"https?://[^\s)\]>\"']+",
                                 f.read_text(encoding="utf-8")):
                u = u.rstrip(".,;")
                if u not in seen:
                    seen.add(u)
                    sources.append(u)
    except Exception:
        pass

    return {
        "agent": agent,
        "grants": _grants_for(handle),
        "sources": sources,
        "research_notes": notes,
        "counts": {
            "published": _count("AND status = 'published'"),
            "suppressed": _count("AND status = 'suppressed'"),
            "quiet": _count("AND status = 'published' "
                            "AND significance = 'quiet'"),
            "tool_calls": tool_calls,
            "posts_total": len(posts),
            "replies": _count("AND type = 'reply'"),
        },
        "posts": posts,
    }


# --- config ----------------------------------------------------------------

# --------------------------------------------------------------------------
# Session settings (PENDING-JUDGE §3) — operator name, and optionally a key
# a judge pastes. The key is held in process memory only.
# --------------------------------------------------------------------------

@app.get("/api/session")
def get_session():
    from app.server import session  # noqa: PLC0415
    from app.agents import runtime  # noqa: PLC0415
    st = session.public_state()
    # key_set is true if the session has one OR .env does: what the UI needs
    # to know is "can this run", not where the key came from.
    env_key = False
    try:
        runtime._api_key()
        env_key = True
    except Exception:
        pass
    st["can_run"] = bool(st["key_set"] or env_key)
    st["models"] = ["claude-opus-5", "claude-sonnet-5", "claude-fable-5",
                    "claude-opus-4-8", "claude-haiku-4-5"]
    st["efforts"] = list(session.VALID_EFFORTS)
    return st


@app.post("/api/session")
def post_session(payload: dict = Body(default={})):
    from app.server import session  # noqa: PLC0415
    session.set_session(
        operator=payload.get("operator"),
        api_key=payload.get("api_key"),
        model=payload.get("model"),
        effort=payload.get("effort"))
    return get_session()


@app.delete("/api/session")
def delete_session():
    from app.server import session  # noqa: PLC0415
    session.clear()
    return get_session()


# --------------------------------------------------------------------------
# Saved cycles (PENDING-JUDGE §4) — a judge with no key must land on real
# output, not an empty app.
# --------------------------------------------------------------------------

@app.get("/api/cycles")
def list_saved_cycles():
    from app.server import cycles  # noqa: PLC0415
    return {"cycles": cycles.list_cycles()}


@app.post("/api/cycles/save")
def save_saved_cycle(payload: dict = Body(default={})):
    from app.agents import runtime  # noqa: PLC0415
    from app.server import cycles  # noqa: PLC0415
    label = (payload.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    try:
        return {"cycle": cycles.save_cycle(
            label, mode=runtime.agent_mode(),
            model=runtime.anthropic_model(),
            effort=runtime.anthropic_effort(),
            note=(payload.get("note") or "").strip(),
            overwrite=bool(payload.get("overwrite")))}
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/cycles/{slug}/load")
def load_saved_cycle(slug: str):
    from app.server import cycles  # noqa: PLC0415
    try:
        return {"cycle": cycles.load_cycle(slug)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/config")
def get_config():
    cfg = config.public_config()
    cfg["agents_available"] = _agents_api() is not None
    # The posts on screen may be a saved LIVE cycle being replayed by a
    # process that has no key. The badge describes the CONTENT, so it must
    # know that — labelling real Claude output "MOCK" because this process
    # could not have produced it is simply false.
    try:
        from app.server import cycles as _cyc
        active = _cyc.active_cycle()
    except Exception:
        active = None
    cfg["active_cycle"] = ({
        "label": active.get("label"), "slug": active.get("slug"),
        "mode": active.get("mode"), "model": active.get("model"),
        "effort": active.get("effort"), "saved_at": active.get("saved_at"),
    } if active else None)
    return cfg


# --- static frontend -------------------------------------------------------

if config.STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True),
              name="static")
else:
    @app.get("/")
    def index_placeholder():
        return {"app": "Three Rooms", "note": "app/static/ not present yet",
                "api": "/api/config"}
