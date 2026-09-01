"""SQLite layer — shared interface for server and agents packages.

Exposes exactly:
  init_db(path) -> sqlite3.Connection   (creates schema, idempotent)
  get_db()      -> sqlite3.Connection   (per-thread connection, dict rows)

Schema exactly per SPEC-APP section 3. This module has NO dependency on
app.agents (builtin-agent seeding happens in server startup, guarded).
Connections are per-thread (FastAPI background tasks run in worker threads);
WAL journaling + busy timeout keep concurrent reader/writer behaviour sane.
"""

from __future__ import annotations

import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  asof            TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('base', 'rerun')),
  parent_run_id   INTEGER REFERENCES runs(id),
  seed            INTEGER,
  sims            INTEGER,
  status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'failed',
                                      'stopped')),
  out_dir         TEXT,
  adjustments_json TEXT,
  started_at      TEXT,
  finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS stage_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES runs(id),
  stage       TEXT NOT NULL CHECK (stage IN ('setup', 'esg', 'pricing', 'validation')),
  status      TEXT NOT NULL CHECK (status IN ('started', 'done', 'failed')),
  detail_json TEXT,
  ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  room              INTEGER NOT NULL CHECK (room IN (1, 2, 3)),
  handle            TEXT NOT NULL,
  name              TEXT,
  focus             TEXT,
  persona_prompt    TEXT,
  avatar_json       TEXT,
  builtin           INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
  outlook           TEXT NOT NULL DEFAULT 'internal'
                      CHECK (outlook IN ('internal', 'outward', 'both')),
  reads_from        TEXT,   -- JSON list of handles / "room:N" wildcards
  reads_on_request  TEXT,   -- JSON list, granted only on mention/reply (H.1)
  also_posts_in     TEXT,   -- JSON list of room numbers this ONE persona is
                            -- also scheduled in, with a per-room brief
                            -- (PENDING-BATCH2 §13): @focused is home room 1
                            -- and also posts in room 3, @red-team likewise,
                            -- @story in all three. `room` stays the HOME
                            -- room; handles remain globally unique, so a
                            -- mention still resolves to exactly one agent.
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (handle)
);

CREATE TABLE IF NOT EXISTS snapshots (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER NOT NULL REFERENCES runs(id),
  seq          INTEGER NOT NULL,
  data_through TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  room               INTEGER NOT NULL CHECK (room IN (1, 2, 3)),
  agent_id           INTEGER REFERENCES agents(id),   -- NULL for human
  author_label       TEXT NOT NULL,
  type               TEXT NOT NULL CHECK (type IN ('origin', 'expansion', 'reply')),
  parent_id          INTEGER REFERENCES posts(id),
  body_md            TEXT NOT NULL,
  claims_json        TEXT,   -- [{"text": ..., "value": <number>, "tool_call_id": <id>}]
  status             TEXT NOT NULL DEFAULT 'published'
                       CHECK (status IN ('published', 'suppressed')),
  suppression_reason TEXT,
  run_id             INTEGER REFERENCES runs(id),
  significance       TEXT CHECK (significance IN
                       ('critical', 'notable', 'routine', 'quiet')),
  snapshot_id        INTEGER REFERENCES snapshots(id),
  sources_json       TEXT,   -- [post_id, ...] this post drew on (SPEC-APP H)
  web_sources_json   TEXT,   -- URLs a live agent actually read this post
  attachment_json    TEXT,   -- {"type": "...", "payload": {...}} rendered
                             -- under the post body, collapsed by default
                             -- (PENDING-BATCH2 §8). Optional; the first type
                             -- is `vcv_table`. The payload is engine data
                             -- read through a tool call like any other
                             -- number, so it inherits provenance.
  pinned             INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
  created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  kind            TEXT NOT NULL CHECK (kind IN
                    ('reply', 'mention_answered', 'gate_pending',
                     'snapshot_ready', 'suppressed')),
  post_id         INTEGER REFERENCES posts(id),
  thread_root_id  INTEGER REFERENCES posts(id),
  room            INTEGER,
  agent_id        INTEGER REFERENCES agents(id),
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  read_at         TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id       INTEGER REFERENCES posts(id),
  tool          TEXT NOT NULL,
  args_json     TEXT,
  result_json   TEXT,
  artifact_path TEXT,
  ts            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gates (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id              INTEGER NOT NULL REFERENCES runs(id),
  proposed_by_post_id INTEGER REFERENCES posts(id),
  adjustments_json    TEXT,
  rationale           TEXT,
  status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
  decided_by          TEXT,
  decided_at          TEXT,
  result_run_id       INTEGER REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_posts_room ON posts(room, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);
CREATE INDEX IF NOT EXISTS idx_stage_events_run ON stage_events(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_post ON tool_calls(post_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at);
"""

# Columns added after the original schema (fresh DBs get them via CREATE
# TABLE above; a pre-existing DB gets them bolted on here — plain ALTER
# TABLE ADD COLUMN, no CHECK constraint, since SQLite's CHECK-on-ADD-COLUMN
# support is version-sensitive and the constraint is enforced by the Python
# layer anyway). Idempotent: skipped when the column already exists.
_ADDED_COLUMNS = {
    "agents": [
        ("outlook", "TEXT NOT NULL DEFAULT 'internal'"),
        ("reads_from", "TEXT"),
        ("reads_on_request", "TEXT"),
        ("also_posts_in", "TEXT"),
    ],
    "posts": [
        ("significance", "TEXT"),
        ("snapshot_id", "INTEGER REFERENCES snapshots(id)"),
        ("sources_json", "TEXT"),
        ("pinned", "INTEGER NOT NULL DEFAULT 0"),
        ("web_sources_json", "TEXT"),
        ("attachment_json", "TEXT"),
    ],
}


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = ?", (table,)).fetchone()
        if exists is None:
            continue  # fresh DB: CREATE TABLE below already has it
        have = {r["name"] for r in
                conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()

def _migrate_runs_status(conn: sqlite3.Connection) -> None:
    """Widen `runs.status`'s CHECK to admit 'stopped' (PENDING-ROSTER J:
    Stop run — the engine subprocess is terminated, the row is marked
    stopped, and the partial stage events are kept). SQLite can only drop a
    CHECK by rebuilding the table; fresh databases get it from SCHEMA and
    never enter here. Same safe order as `_migrate_agents`: create-new,
    copy, DROP OLD, rename into place under `legacy_alter_table` so the
    referencing tables' FK clauses are not rewritten."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'runs'").fetchone()
    if row is None:
        return
    if "'stopped'" in (row["sql"] or ""):
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("""
            CREATE TABLE runs_migrated (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              asof            TEXT NOT NULL,
              kind            TEXT NOT NULL CHECK (kind IN ('base', 'rerun')),
              parent_run_id   INTEGER REFERENCES runs(id),
              seed            INTEGER,
              sims            INTEGER,
              status          TEXT NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'running', 'done',
                                                  'failed', 'stopped')),
              out_dir         TEXT,
              adjustments_json TEXT,
              started_at      TEXT,
              finished_at     TEXT
            )""")
        conn.execute(
            "INSERT INTO runs_migrated (id, asof, kind, parent_run_id, seed, "
            "sims, status, out_dir, adjustments_json, started_at, "
            "finished_at) SELECT id, asof, kind, parent_run_id, seed, sims, "
            "status, out_dir, adjustments_json, started_at, finished_at "
            "FROM runs")
        conn.execute("DROP TABLE runs")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("ALTER TABLE runs_migrated RENAME TO runs")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


_state = {"path": None}
_local = threading.local()


def _dict_factory(cursor, row):
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _migrate_agents(conn: sqlite3.Connection) -> None:
    """Rebuild a pre-existing `agents` table onto the current schema:
    adds `avatar_json` and moves handle uniqueness from (room, handle) to
    global UNIQUE(handle) — SPEC-APP section 3 ("globally unique (across
    rooms), so @-mentions are never ambiguous"). Duplicate handles keep the
    lowest id (builtins were seeded first); posts by dropped duplicates are
    re-pointed at the surviving row. Fresh databases never enter here."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'agents'").fetchone()
    if row is None:
        return
    sql = row["sql"] or ""
    cols = {r["name"] for r in
            conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "avatar_json" in cols and "UNIQUE (room, handle)" not in sql:
        return  # already on the current schema
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        keep = {r["handle"]: r["id"] for r in conn.execute(
            "SELECT handle, MIN(id) AS id FROM agents GROUP BY handle"
        ).fetchall()}
        for dup in conn.execute("SELECT id, handle FROM agents").fetchall():
            if dup["id"] != keep[dup["handle"]]:
                conn.execute("UPDATE posts SET agent_id = ? WHERE agent_id = ?",
                             (keep[dup["handle"]], dup["id"]))
        # The documented safe rebuild order: create-new, copy, DROP OLD,
        # rename new into place. Never rename the OLD table first — ALTER
        # TABLE ... RENAME rewrites foreign-key clauses in referencing
        # tables (posts.agent_id would end up referencing 'agents_old').
        conn.execute("""
            CREATE TABLE agents_migrated (
              id             INTEGER PRIMARY KEY AUTOINCREMENT,
              room           INTEGER NOT NULL CHECK (room IN (1, 2, 3)),
              handle         TEXT NOT NULL,
              name           TEXT,
              focus          TEXT,
              persona_prompt TEXT,
              avatar_json    TEXT,
              builtin        INTEGER NOT NULL DEFAULT 0
                               CHECK (builtin IN (0, 1)),
              created_at     TEXT NOT NULL DEFAULT (datetime('now')),
              UNIQUE (handle)
            )""")
        avatar_src = "avatar_json" if "avatar_json" in cols else "NULL"
        conn.execute(
            f"INSERT INTO agents_migrated (id, room, handle, name, focus, "
            f"persona_prompt, avatar_json, builtin, created_at) "
            f"SELECT id, room, handle, name, focus, persona_prompt, "
            f"{avatar_src}, builtin, created_at FROM agents "
            f"WHERE id IN (SELECT MIN(id) FROM agents GROUP BY handle)")
        conn.execute("DROP TABLE agents")
        # legacy_alter_table: skip the schema re-parse (posts still names
        # the just-dropped 'agents') and the reference rewrite (nothing
        # references 'agents_migrated')
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("ALTER TABLE agents_migrated RENAME TO agents")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(path) -> sqlite3.Connection:
    """Point the module at `path`, create the schema if needed, return a
    connection for the calling thread. Safe to call more than once."""
    _state["path"] = str(path)
    # Drop any connection this thread held to a previous path.
    if getattr(_local, "conn", None) is not None:
        try:
            _local.conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
    conn = get_db()
    _migrate_agents(conn)  # existing DBs: avatar_json + global handles
    _migrate_runs_status(conn)  # existing DBs: runs.status admits 'stopped'
    _migrate_add_columns(conn)  # existing DBs: outlook/reads_from/significance/...
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def is_initialized() -> bool:
    return _state["path"] is not None


def db_path() -> str | None:
    return _state["path"]


def get_db() -> sqlite3.Connection:
    """Per-thread connection to the initialized database (dict rows)."""
    if _state["path"] is None:
        raise RuntimeError("db.init_db(path) has not been called")
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != _state["path"]:
        conn = _connect(_state["path"])
        _local.conn = conn
        _local.path = _state["path"]
    return conn
