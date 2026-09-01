"""Re-run ONE agent against the current run pair and replace its post.

Why this exists: a full cycle is ~25 minutes and eighteen agents. When one
agent's post is suppressed or reads badly, re-running the other seventeen to
fix it is waste — and, worse, it re-rolls posts that were already right.

Usage:
    python -m scripts.rerun_agent @warden 3 [@story 1 ...]

The old post for that handle in that room is deleted (with its tool calls
and claims) and replaced by the new one, so the room does not end up with
two posts from the same desk.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                    # noqa: E402
from app.agents import api, personas, runtime             # noqa: E402
from app.server import db                                 # noqa: E402


def _latest_pair(conn):
    runs = [dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE status = 'done' ORDER BY asof, id")]
    if not runs:
        raise SystemExit("no completed runs in the database")
    curr = runs[-1]
    prev = next((r for r in reversed(runs) if r["asof"] < curr["asof"]), None)
    return prev, curr


def rerun(handle: str, room: int) -> int | None:
    conn = db.get_db()
    api.ensure_builtins(conn)
    prev, curr = _latest_pair(conn)
    ctx = api.PassContext(room, prev, curr, False)

    row = api._agent_row(conn, handle, room=room) or api._agent_row(conn, handle)
    if row is None:
        print(f"  {handle}: no such agent")
        return None

    session = ctx.session()
    system = api.persona_prompt_for(row, room) or (
        f"You are {handle}, focus: {row['focus']}.")
    try:
        parsed = runtime._run_agentic_loop(
            session, system, runtime._pass_brief(ctx), handle=handle)
    except Exception as e:
        print(f"  {handle}: FAILED {type(e).__name__}: "
              f"{' '.join(str(e).split())[:160]}")
        return None
    if not parsed or not parsed.get("body_md"):
        print(f"  {handle}: returned no body")
        return None

    old = [r["id"] for r in conn.execute(
        "SELECT id FROM posts WHERE room = ? AND agent_id = ? "
        "AND type = 'origin'", (room, row["id"]))]
    try:
        pid, _ = api.publish_post(
            room=room, agent_row=row, body=parsed["body_md"],
            claims=parsed.get("claims") or [], post_type="origin",
            run_id=curr["id"], session=session,
            context=handle in personas.CONTEXT_HANDLES,
            web_sources=parsed.get("sources_read"))
    except Exception as e:
        print(f"  {handle}: publish failed — {e}")
        return None

    # Only now remove the old one: if the rerun had failed we would still
    # have the original rather than an empty room.
    for oid in old:
        conn.execute("DELETE FROM tool_calls WHERE post_id = ?", (oid,))
        conn.execute("DELETE FROM posts WHERE id = ?", (oid,))
    conn.commit()

    new = conn.execute("SELECT status, suppression_reason FROM posts "
                       "WHERE id = ?", (pid,)).fetchone()
    print(f"  {handle}: #{pid} {new['status']}"
          + (f" — {new['suppression_reason']}" if new["suppression_reason"]
             else ""))
    return pid


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) % 2:
        print(__doc__)
        return 2
    if runtime.agent_mode() != "live":
        print("AGENT_MODE is not live — nothing to re-run.")
        return 1
    db.init_db(Path(config.DB_PATH))
    for i in range(0, len(argv), 2):
        rerun(argv[i], int(argv[i + 1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
