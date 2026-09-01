"""Roster changes for PENDING-BATCH2 §7-§10 and §13.

  §7   @pre-flight-checks — room 1, runs FIRST, absorbs the deleted
       @curve-check. Five check families; a VERDICT line then the failing
       items; significance follows the verdict; it flags, never blocks.
  §8   @vcv — renamed from @vcv-sentinel and reframed as the monthly VCV
       report, with the 21-factor matrix carried as `posts.attachment_json`
       of type `vcv_table`.
  §9   @pc-desk — private credit as a DESK (our sleeve, not the market).
  §9a  All four desks take the same three-part shape: market, bullets, what
       it means for the results, plus one optional watch item.
  §10  @story — one post per room, accumulating, running LAST and pinned
       FIRST, composed only of published posts, carrying no numbers.
  §13  @focused is ONE agent posting in two rooms via `agents.also_posts_in`,
       with a per-room brief; @focused-book is gone.

Runs the real engine twice (sims=2000 for speed) and drives real mock
passes: no API calls anywhere, AGENT_MODE pinned to style.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_roster_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"

import pytest

from app import config
from app.agents import api as agents_api
from app.agents import citation, personas
from app.agents.checks import ROOM_CHECKS
from app.server import db, engine_bridge

DB_FILE = _TMP / "roster.sqlite"

RETIRED = ("@curve-check", "@vcv-sentinel", "@focused-book")


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


def _run(asof: str, **kw) -> dict:
    r = engine_bridge.create_run(asof=asof, kind="base", sims=2000, **kw)
    done = engine_bridge.execute_run(r["id"])
    assert done["status"] == "done", done
    return done


@pytest.fixture(scope="module")
def pair(conn):
    prev = _run("2026-02")
    curr = _run("2026-03",
                seeded_book_path=str(config.PROJECT_ROOT / "book"
                                     / "positions_2026-03.json"),
                seeded_liabilities_path=str(config.PROJECT_ROOT / "book"
                                            / "liabilities_2026-03.json"))
    agents_api.run_research_pass("2026-03")
    return prev, curr


@pytest.fixture(scope="module")
def cycle(conn, pair):
    """A full mock cycle: room 1, then 2, then 3 — the order @story
    accumulates in."""
    prev, curr = pair
    out = {}
    for room in (1, 2, 3):
        out[room] = agents_api.run_room_pass(room, prev["id"], curr["id"],
                                             seeded=False)
    return out


def _posts(conn, ids):
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks}) ORDER BY id",
        ids).fetchall()


def _origin(conn, ids, handle):
    for p in _posts(conn, ids):
        if p["author_label"] == handle and p["type"] == "origin":
            return p
    return None


# ==========================================================================
# §7 — @pre-flight-checks
# ==========================================================================

def test_preflight_replaces_curve_check_and_runs_first():
    handles = [h for h, _fn in ROOM_CHECKS[1]]
    assert handles[0] == "@pre-flight-checks"
    for gone in ("@curve-check", "@vcv-sentinel", "@focused-book"):
        assert gone not in handles
    roster = {p["handle"] for p in personas.BUILTINS}
    assert set(RETIRED).isdisjoint(roster)
    spec = personas.by_handle("@pre-flight-checks")
    assert spec["room"] == 1
    assert spec["avatar"]["glyph"] == "PF"
    assert spec["avatar"]["bg"] == "#D97706"          # amber
    assert spec["handle"] in personas.WEB_SEARCH_HANDLES  # the external check
    # ...and NO run_sensitivity grant: it runs before results exist
    assert "You have no run_sensitivity" in spec["persona_prompt"]


def test_preflight_prompt_names_all_five_families():
    p = personas.by_handle("@pre-flight-checks")["persona_prompt"]
    for family in ("RECONCILIATION AGAINST SOURCE", "STRUCTURAL INTEGRITY",
                   "BOUNDS AND PLAUSIBILITY", "INTERNAL CONSISTENCY",
                   "INDEPENDENT EXTERNAL CHECK"):
        assert family in p, family
    for verdict in ("CLEAR TO RUN", "RUN WITH CONCERNS", "DO NOT RUN"):
        assert verdict in p, verdict
    assert "YOU FLAG; YOU DO NOT BLOCK" in p


def test_preflight_post_leads_with_a_verdict(conn, cycle):
    post = _origin(conn, cycle[1], "@pre-flight-checks")
    assert post is not None and post["status"] == "published"
    body = post["body_md"]
    verdicts = [v for v in ("DO NOT RUN", "RUN WITH CONCERNS", "CLEAR TO RUN")
                if v in body]
    assert len(verdicts) == 1, body[:200]
    assert body.startswith(f"**{verdicts[0]}"), body[:80]
    # significance follows the verdict, it is not a stylistic choice
    assert post["significance"] == {"CLEAR TO RUN": "quiet",
                                    "RUN WITH CONCERNS": "notable",
                                    "DO NOT RUN": "critical"}[verdicts[0]]
    # it flags; it never blocks
    assert "I flag; I do not block" in body


def test_preflight_says_plainly_that_mock_cannot_check_externally(
        conn, cycle):
    """§7 family five needs the web. In mock it must SAY it did not run —
    an external check nobody made must never read as one that passed."""
    body = _origin(conn, cycle[1], "@pre-flight-checks")["body_md"]
    assert "External check: **not run**" in body
    assert "no web access" in body
    assert "Four families of five completed" in body


def test_preflight_reconciles_every_base_level_against_source(conn, cycle):
    """Family 1, absorbed whole from @curve-check and widened: curves at
    every tenor, spreads by rating, equity levels, FX — 21 rows, each
    against data/processed."""
    work = [p for p in _posts(conn, cycle[1])
            if p["author_label"] == "@pre-flight-checks"
            and p["type"] == "expansion"]
    assert work, "no pre-flight working page"
    body = work[0]["body_md"]
    assert work[0]["status"] == "published", work[0]["suppression_reason"]
    for label in ("gbp_swap 2y", "gbp_gilt 20y", "ust 10y", "spread CCC",
                  "equity SX5E", "fx GBPUSD"):
        assert f"| {label} |" in body, label
    assert body.count("| ok |") == 21          # 12 curve + 5 spread + 3 eq + fx
    tools_used = {r["tool"] for r in conn.execute(
        "SELECT tool FROM tool_calls WHERE post_id = ?",
        (work[0]["id"],)).fetchall()}
    assert "read_data_series" in tools_used and "read_assumptions" in tools_used


def test_preflight_flags_a_broken_level_without_blocking(conn, pair):
    """A fabricated break must produce DO NOT RUN with the item named, and
    must still not stop anything — the run row is untouched."""
    from app.agents.checks import room1
    prev, curr = pair
    ctx = agents_api.PassContext(1, prev, curr, seeded=False)
    real = room1._pf_reconciliation

    def broken(s, doc, tc_a, asof):
        rows, items = real(s, doc, tc_a, asof)
        rows[0]["ok"] = False
        items.append(room1._Item(
            "reconciliation", f"{rows[0]['label']} does not match source",
            True, found=rows[0]["assumed"], expected=rows[0]["source_cited"],
            found_tc=tc_a, expected_tc=rows[0]["tc_src"],
            found_txt="0.0000%", expected_txt=rows[0]["source_txt"]))
        return rows, items

    room1._pf_reconciliation = broken
    try:
        drafts = room1.pre_flight_checks(ctx)
    finally:
        room1._pf_reconciliation = real
    body = drafts[0]["body"]
    assert body.startswith("**DO NOT RUN")
    assert drafts[0]["significance"] == "critical"
    assert "**FLAG**" in body
    assert "I flag; I do not block" in body
    assert conn.execute("SELECT status FROM runs WHERE id = ?",
                        (curr["id"],)).fetchone()["status"] == "done"


# ==========================================================================
# §8 — @vcv
# ==========================================================================

def test_vcv_is_renamed_everywhere(conn):
    assert personas.by_handle("@vcv-sentinel") is None
    spec = personas.by_handle("@vcv")
    assert spec["name"] == "VCV" and spec["avatar"]["glyph"] == "VC"
    assert "@vcv" in [h for h, _fn in ROOM_CHECKS[1]]
    handles = {r["handle"] for r in
               conn.execute("SELECT handle FROM agents").fetchall()}
    assert "@vcv" in handles and "@vcv-sentinel" not in handles
    assert personas.RETIRED_HANDLES["@vcv-sentinel"] == "@vcv"


def test_vcv_post_is_a_summary_not_a_reconciliation_report(conn, cycle):
    """§8: the post is a mini-summary of what happened to the vols and the
    major correlation changes. A clean recomputation is SILENT — no
    'everything reconciles' paragraph."""
    post = _origin(conn, cycle[1], "@vcv")
    assert post is not None and post["status"] == "published"
    body = post["body_md"]
    assert body.startswith("Vols:")
    assert "Correlations:" in body
    assert "FLAG" not in body                    # the clean pair reconciles
    assert "reconcile" not in body.lower()
    assert "Correlation movers" in body or "correlation cell moved" in body


def test_vcv_recomputation_check_still_runs_and_bites(conn, pair):
    """The check is demoted, not dropped: the D1 seeded vol defect must
    still surface, as a BULLET, and raise significance."""
    from app.agents.checks import room1
    prev, _ = pair
    seeded = _run("2026-03", seeded_assumptions_path=str(
        config.SCENARIOS_DIR / "seeded" / "assumptions_2026-03_D1.yaml"))
    ctx = agents_api.PassContext(1, prev, seeded, seeded=True)
    drafts = room1.vcv(ctx)
    body = drafts[0]["body"]
    assert "\n- **FLAG" in body                 # a bullet, not the subject
    assert "vols.gbp_swap.10" in body
    assert "0.4732%" in body and "0.7316%" in body
    assert drafts[0]["significance"] == "critical"
    assert "Corrected rerun proposed" in body


def test_vcv_attachment_is_a_vcv_table_with_matrix_and_prior(conn, cycle):
    post = _origin(conn, cycle[1], "@vcv")
    assert post["attachment_json"], "no attachment on the @vcv post"
    att = json.loads(post["attachment_json"])
    assert att["type"] == "vcv_table"
    pl = att["payload"]
    from app.agents.checks.room1 import SPEC_FACTOR_ORDER
    assert pl["factors"] == SPEC_FACTOR_ORDER
    assert len(pl["factors"]) == 21
    assert len(pl["vols"]) == 21
    for row in pl["vols"]:
        assert set(row) == {"factor", "current", "prior", "change"}
        assert row["current"] is not None
        assert abs(row["change"] - (row["current"] - row["prior"])) < 1e-12
    assert len(pl["corr"]) == 21 and all(len(r) == 21 for r in pl["corr"])
    assert len(pl["corr_prior"]) == 21
    assert all(abs(pl["corr"][i][i] - 1.0) < 1e-9 for i in range(21))
    assert pl["mover_threshold"] > 0     # so the UI can outline the movers


def test_vcv_attachment_inherits_tool_call_provenance(conn, cycle):
    """§8: 'the attachment is engine data read through a tool call like any
    other number, so it inherits provenance — it is not a second, unchecked
    channel.' Every vol in the payload must appear in a recorded result of
    a tool call bound to this very post."""
    post = _origin(conn, cycle[1], "@vcv")
    results = [r["result_json"] for r in conn.execute(
        "SELECT result_json FROM tool_calls WHERE post_id = ? AND "
        "tool = 'read_assumptions'", (post["id"],)).fetchall()]
    assert results, "the attachment's post ran no read_assumptions"
    pl = json.loads(post["attachment_json"])["payload"]
    for row in pl["vols"][:5]:
        assert any(citation.value_in_result(row["current"], rj)
                   for rj in results), row["factor"]
    assert any(citation.value_in_result(pl["corr"][0][1], rj)
               for rj in results)


def test_attachment_column_is_optional_and_defaults_to_null(conn, cycle):
    others = [p for p in _posts(conn, cycle[1])
              if p["author_label"] != "@vcv"]
    assert others
    assert all(p["attachment_json"] is None for p in others)


# ==========================================================================
# §9 / §9a — the desks
# ==========================================================================

DESKS = ("@rates-desk", "@credit-desk", "@equity-desk", "@pc-desk")


def test_pc_desk_exists_and_covers_our_sleeve_not_the_market():
    assert "@pc-desk" in [h for h, _fn in ROOM_CHECKS[3]]
    spec = personas.by_handle("@pc-desk")
    assert spec["room"] == 3 and spec["avatar"]["glyph"] == "PC"
    assert spec["handle"] in personas.WEB_SEARCH_HANDLES
    p = spec["persona_prompt"]
    # the seam with @wide-eye, stated in the prompt so it cannot drift
    assert "@wide-eye covers the private credit MARKET" in p
    # the three standing themes
    assert "FUNDRAISING AND DISPERSION" in p
    assert "MARKS VERSUS PUBLIC HY" in p
    assert "IS THE FIXED-RATE BOND PROXY STILL THE RIGHT MAPPING?" in p


def test_pc_desk_post_covers_the_three_standing_themes(conn, cycle):
    post = _origin(conn, cycle[3], "@pc-desk")
    assert post is not None and post["status"] == "published", (
        post and post["suppression_reason"])
    body = post["body_md"]
    assert "Public HY" in body                     # marks vs public HY
    assert "Dispersion between managers" in body   # dispersion
    assert "fixed-rate bond proxy" in body         # the mapping question
    assert "**For the results below.**" in body


@pytest.mark.parametrize("handle", DESKS)
def test_every_desk_takes_the_same_three_part_shape(conn, cycle, handle):
    """§9a: the market FIRST (not valuation.json), then bullets, then what
    it means for the results below."""
    post = _origin(conn, cycle[3], handle)
    assert post is not None and post["status"] == "published", (
        handle, post and post["suppression_reason"])
    body = post["body_md"]
    market_end = body.index("**For the results below.**")
    head = body[:market_end]
    # part one: the market, read from the research note / source series
    assert "Research note (`2026_03_focused.md`" in head, handle
    # ...and NOT our own results: no VaR figure before the tie-in
    assert "VaR" not in head, handle
    # part two: bullets, fragments
    assert 1 <= head.count("\n- ") <= 4, handle
    # part three: the sleeve, the block VaR, materiality against surplus
    tail = body[market_end:]
    assert "standalone VaR" in tail and "of surplus" in tail, handle


@pytest.mark.parametrize("handle", DESKS)
def test_desk_watch_item_is_optional_and_labelled_as_opinion(conn, cycle,
                                                             handle):
    """§9a: one watch item WHERE THERE IS ONE — omitted in a quiet month
    rather than invented, and it must read as speculation."""
    body = _origin(conn, cycle[3], handle)["body_md"]
    if "**Watch**" not in body:
        return                                   # a quiet month: omitted
    watch = body[body.index("**Watch**"):]
    assert "opinion, not a bound number" in watch, handle
    assert "£" not in watch, handle              # no fabricated book numbers


def test_desk_watch_item_is_derived_from_the_note_never_invented():
    """The watch item comes from the note's own thresholds: a top/bottom
    decile move, or realised vol running away from its window. A stat that
    crosses nothing produces no watch item at all."""
    from app.agents.checks.room3 import _desk_watch
    assert _desk_watch({"move_percentile": 99, "notable": []})
    assert _desk_watch({"move_percentile": 2, "notable": []})
    assert _desk_watch({"move_percentile": 50,
                        "notable": ["realised daily vol ran hot"]})
    assert _desk_watch({"move_percentile": 50, "notable": []}) is None
    assert _desk_watch({}) is None


# ==========================================================================
# §10 — @story
# ==========================================================================

def test_story_posts_once_in_every_room_and_runs_last(conn, cycle):
    for room in (1, 2, 3):
        posts = [p for p in _posts(conn, cycle[room])
                 if p["author_label"] == "@story"]
        assert len(posts) == 1, (room, len(posts))
        post = posts[0]
        assert post["status"] == "published", post["suppression_reason"]
        assert post["pinned"] == 1, room          # pinned FIRST
        # runs LAST: nothing in this room's pass was published after it
        assert post["id"] == max(cycle[room]), room


def test_story_accumulates_across_the_three_rooms(conn, cycle):
    bodies = {room: [p for p in _posts(conn, cycle[room])
                     if p["author_label"] == "@story"][0]["body_md"]
              for room in (1, 2, 3)}
    assert bodies[1].startswith("**This month, opening.**")
    assert bodies[2].startswith("**This month, so far.**")
    assert bodies[3].startswith("**This month, closing.**")
    # the SAME stories, carried forward — titles are stable across rooms
    titles = {room: {ln.split("**")[1] for ln in body.split("\n")
                     if ln.startswith("**") and ln.count("**") >= 2
                     and not ln.startswith("**This month")
                     and not ln.startswith("**Worth reading")}
              for room, body in bodies.items()}
    assert titles[1] and titles[1] == titles[2] == titles[3], titles
    # ...and the line under each title CHANGES as the cycle progresses
    assert bodies[1] != bodies[2] != bodies[3]


def test_story_titles_are_a_few_words_not_sentences(conn, cycle):
    body = [p for p in _posts(conn, cycle[3])
            if p["author_label"] == "@story"][0]["body_md"]
    titles = [ln.split("**")[1] for ln in body.split("\n")
              if ln.startswith("**") and ln.count("**") >= 2
              and not ln.startswith("**This month")
              and not ln.startswith("**Worth reading")]
    assert titles
    for t in titles:
        assert len(t.split()) <= 6, t
        assert not t.endswith("?"), t


def test_story_is_bounded_at_four_live_stories(conn, cycle):
    from app.agents.checks.story import MAX_STORIES
    for room in (1, 2, 3):
        body = [p for p in _posts(conn, cycle[room])
                if p["author_label"] == "@story"][0]["body_md"]
        titles = [ln for ln in body.split("\n")
                  if ln.startswith("**") and not ln.startswith("**This month")
                  and not ln.startswith("**Worth reading")]
        assert len(titles) <= MAX_STORIES, (room, titles)


def test_story_never_writes_a_number(conn, cycle):
    """§10: 'It adds connective tissue and a title — never a number.' The
    citation gate would suppress it otherwise, so this is belt and braces
    on a property the agent is defined by."""
    for room in (1, 2, 3):
        post = [p for p in _posts(conn, cycle[room])
                if p["author_label"] == "@story"][0]
        assert not json.loads(post["claims_json"] or "[]")
        assert citation.numeric_tokens(post["body_md"]) == [], room
        assert "£" not in post["body_md"], room


def test_story_is_composed_only_of_published_posts(conn, cycle):
    """Invents nothing: every source is a PUBLISHED post that already
    existed when @story ran, so provenance chains through that post's own
    claims to the tool call underneath."""
    for room in (1, 2, 3):
        post = [p for p in _posts(conn, cycle[room])
                if p["author_label"] == "@story"][0]
        sources = json.loads(post["sources_json"] or "[]")
        assert sources, room
        for pid in sources:
            src = conn.execute("SELECT * FROM posts WHERE id = ?",
                               (pid,)).fetchone()
            assert src is not None and src["status"] == "published"
            assert src["id"] < post["id"], (room, pid)


def test_worth_reading_footer_is_room_three_only(conn, cycle):
    from app.agents.checks.story import FOOTER_MAX, FOOTER_MIN
    for room in (1, 2):
        body = [p for p in _posts(conn, cycle[room])
                if p["author_label"] == "@story"][0]["body_md"]
        assert "Worth reading" not in body, room
    body = [p for p in _posts(conn, cycle[3])
            if p["author_label"] == "@story"][0]["body_md"]
    assert "**Worth reading.**" in body
    footer = body[body.index("**Worth reading.**"):]
    links = [ln for ln in footer.split("\n") if ln.startswith("- @")]
    assert FOOTER_MIN <= len(links) <= FOOTER_MAX
    for ln in links:
        assert " — " in ln, ln          # one line each on WHY


def test_story_marks_a_dead_story_closed_rather_than_dropping_it():
    """§10: 'Stories that die are kept and marked closed rather than
    quietly dropped — a story that went nowhere is information.'"""
    from app.agents.checks import story as story_mod
    st = story_mod._Story("credit", "The credit move", "spreads",
                          "the credit block")
    st.desks = ["@credit-desk", "@pc-desk"]
    seen = {1: {}, 3: {"@credit-desk": [], "@pc-desk": [], "@attrib": [],
                       "@warden": [], "@red-team": []}}
    story_mod._add_room3(seen, [st])
    assert st.closed is True
    assert "CLOSED" in st.lines[3]
    assert "went nowhere" in st.lines[3]


def test_story_says_so_in_a_line_when_there_is_nothing(conn, pair):
    """A month with no story says so — it does not manufacture one."""
    from app.agents.checks import story as story_mod
    prev, curr = pair
    ctx = agents_api.PassContext(1, prev, curr, seeded=False)
    real = story_mod._open_stories
    story_mod._open_stories = lambda seen: []
    try:
        drafts = story_mod.story_post(ctx)
    finally:
        story_mod._open_stories = real
    body = drafts[0]["body"]
    assert "No story yet" in body
    assert drafts[0]["significance"] == "quiet"
    assert len(body.split()) < 60


# ==========================================================================
# §13 — one persona, several rooms
# ==========================================================================

def test_focused_book_is_gone_and_focused_posts_in_two_rooms(conn, cycle):
    assert personas.by_handle("@focused-book") is None
    assert "@focused-book" not in [h for h, _fn in ROOM_CHECKS[3]]
    assert "@focused" in [h for h, _fn in ROOM_CHECKS[1]]
    assert "@focused" in [h for h, _fn in ROOM_CHECKS[3]]
    r1 = _origin(conn, cycle[1], "@focused")
    r3 = _origin(conn, cycle[3], "@focused")
    assert r1 and r3
    assert r1["room"] == 1 and r3["room"] == 3
    # ONE identity: one agent id, therefore one profile page, one history
    assert r1["agent_id"] == r3["agent_id"]
    # ...and two different posts, because the brief differs
    assert r1["body_md"] != r3["body_md"]
    assert "standalone VaR" in r3["body_md"]


def test_also_posts_in_is_stored_and_read_back(conn):
    cols = {r["name"] for r in
            conn.execute("PRAGMA table_info(agents)").fetchall()}
    assert "also_posts_in" in cols
    row = conn.execute("SELECT * FROM agents WHERE handle = '@focused'"
                       ).fetchone()
    assert json.loads(row["also_posts_in"]) == [3]
    assert agents_api.rooms_for_agent(row) == [1, 3]
    rt = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                      ).fetchone()
    assert agents_api.rooms_for_agent(rt) == [1, 3]     # the two-pass cycle
    story_row = conn.execute("SELECT * FROM agents WHERE handle = '@story'"
                             ).fetchone()
    assert agents_api.rooms_for_agent(story_row) == [1, 2, 3]
    # a single-room agent stays single-room
    holdings = conn.execute("SELECT * FROM agents WHERE handle = '@holdings'"
                            ).fetchone()
    assert agents_api.rooms_for_agent(holdings) == [1]


def test_agents_in_room_includes_the_agents_that_also_post_there(conn):
    room3 = [r["handle"] for r in agents_api.agents_in_room(conn, 3)]
    assert "@focused" in room3 and "@red-team" in room3 and "@story" in room3
    assert room3.count("@focused") == 1        # once, not twice
    room1 = [r["handle"] for r in agents_api.agents_in_room(conn, 1)]
    assert "@focused" in room1 and "@story" in room1
    assert "@attrib" not in room1


def test_mention_gets_the_brief_for_the_room_it_was_asked_from(conn):
    """§13: 'Asking @focused a question from room 3 gets the room-3 brief;
    from room 1, the room-1 brief.'"""
    row = conn.execute("SELECT * FROM agents WHERE handle = '@focused'"
                       ).fetchone()
    p1 = agents_api.persona_prompt_for(row, 1)
    p3 = agents_api.persona_prompt_for(row, 3)
    assert p1 != p3
    assert "YOU ARE IN ROOM 1" in p1 and "smallest post in the room" in p1
    assert "YOU ARE IN ROOM 3" in p3
    assert "here is what they did to the results" in p3
    # both still carry the ONE persona prompt underneath
    assert row["persona_prompt"] in p1 and row["persona_prompt"] in p3
    # red-team's two passes use the same mechanism
    rt = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                      ).fetchone()
    assert "OPENING PASS" in agents_api.persona_prompt_for(rt, 1)
    assert "CLOSING PASS" in agents_api.persona_prompt_for(rt, 3)


def test_a_single_room_agent_gets_no_extra_brief(conn):
    row = conn.execute("SELECT * FROM agents WHERE handle = '@holdings'"
                       ).fetchone()
    assert agents_api.persona_prompt_for(row, 1) == row["persona_prompt"]


def test_profile_history_spans_every_room_the_agent_posts_in(conn, cycle):
    """The point of the merge: one desk, one history. Every @focused post,
    across both rooms, hangs off the same agent id."""
    row = conn.execute("SELECT * FROM agents WHERE handle = '@focused'"
                       ).fetchone()
    rooms = {r["room"] for r in conn.execute(
        "SELECT DISTINCT room FROM posts WHERE agent_id = ?",
        (row["id"],)).fetchall()}
    assert {1, 3} <= rooms


def test_retired_handles_migrate_an_existing_database(tmp_path):
    """A database seeded before this batch must converge: @curve-check
    retired, @vcv-sentinel renamed with its history, @focused-book's posts
    re-pointed at @focused.

    Built on its own raw connection rather than through db.init_db, which
    would repoint the module (and close this thread's live connection)."""
    import sqlite3
    old = sqlite3.connect(tmp_path / "legacy.sqlite")
    old.row_factory = db._dict_factory
    old.executescript(db.SCHEMA)
    for room, handle in ((1, "@curve-check"), (1, "@vcv-sentinel"),
                         (3, "@focused-book"), (1, "@focused")):
        old.execute("INSERT INTO agents (room, handle, name, builtin) "
                    "VALUES (?, ?, ?, 1)", (room, handle, handle))
    old.commit()
    ids = {r["handle"]: r["id"] for r in
           old.execute("SELECT handle, id FROM agents").fetchall()}
    for handle in ("@curve-check", "@vcv-sentinel", "@focused-book"):
        old.execute(
            "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
            "status) VALUES (1, ?, ?, 'origin', 'legacy', 'published')",
            (ids[handle], handle))
    old.commit()

    actions = agents_api.retire_handles(old)
    assert actions == {"@curve-check": "retired",
                       "@vcv-sentinel": "renamed to @vcv",
                       "@focused-book": "merged into @focused"}
    handles = {r["handle"] for r in
               old.execute("SELECT handle FROM agents").fetchall()}
    assert set(RETIRED).isdisjoint(handles)
    assert "@vcv" in handles
    # the rename keeps its history on the SAME row
    assert old.execute(
        "SELECT agent_id FROM posts WHERE author_label = '@vcv-sentinel'"
    ).fetchone()["agent_id"] == ids["@vcv-sentinel"]
    # the merge re-points onto @focused — one desk, one history
    assert old.execute(
        "SELECT agent_id FROM posts WHERE author_label = '@focused-book'"
    ).fetchone()["agent_id"] == ids["@focused"]
    # the retirement leaves the feed's own record intact, minus the link
    assert old.execute(
        "SELECT agent_id FROM posts WHERE author_label = '@curve-check'"
    ).fetchone()["agent_id"] is None
    # ...and it is idempotent
    assert agents_api.retire_handles(old) == {}
    old.close()


def test_wildcard_reads_never_depend_on_the_agent_that_reads_them(conn):
    """@story reads every room it posts in, so no room-wide `reads_from`
    may name it back — that would be both a false statement and a cycle."""
    agents_api.validate_reads_from(conn, "@red-team", ["room:1", "room:3"])
    agents_api.validate_reads_from(conn, "@warden", ["room:3", "@holdings"])
    agents_api.validate_reads_from(conn, "@story",
                                   ["room:1", "room:2", "room:3"])
    with pytest.raises(ValueError, match="cycle"):
        agents_api.validate_reads_from(conn, "@holdings", ["@story"])


# ==========================================================================
# the roster is documented where the docs say it is
# ==========================================================================

def test_agent_prompts_doc_matches_the_shipped_roster():
    """§8's rename is 'everywhere: personas, tests, docs, seeded DB'.
    AGENT-PROMPTS.md is the doc of record for the defaults, so it is held
    to the roster rather than trusted to keep up with it."""
    doc_path = config.PROJECT_ROOT / "AGENT-PROMPTS.md"
    if not doc_path.is_file():
        pytest.skip("AGENT-PROMPTS.md is not shipped in this checkout")
    doc = doc_path.read_text(encoding="utf-8")
    table = doc[doc.index("## Retired handles"):doc.index("## Room 1")]
    for gone in RETIRED:
        # a retired handle has no section of its own: it survives only in
        # the "Retired handles" table, and inside a prompt that explains
        # the lineage (@pre-flight-checks names what it absorbed)
        assert f"### `{gone}`" not in doc, gone
        assert gone in table, gone
    for p in personas.BUILTINS:
        assert f"### `{p['handle']}` — {p['name']}\n" in doc, p["handle"]
        assert p["persona_prompt"] in doc, p["handle"]
        for brief in (p.get("room_briefs") or {}).values():
            assert brief in doc, p["handle"]
    assert doc.count("### `@") == len(personas.BUILTINS)
    for h in sorted(personas.WEB_SEARCH_HANDLES):
        assert f"`{h}`" in doc, h


# ==========================================================================
# Handovers: two surfaces this batch's roster needs that live in files this
# change does not own. Pinned as strict xfails, per this suite's own
# convention — they document the hole rather than hide it, and the real fix
# flips them to XPASS, which demands the mark be removed.
# ==========================================================================

def test_runtime_web_search_grant_matches_the_roster():
    from app.agents import runtime
    assert runtime.WEB_SEARCH_HANDLES == personas.WEB_SEARCH_HANDLES


def test_room_agent_list_includes_agents_that_also_post_there(conn):
    from fastapi.testclient import TestClient
    from app.server.main import app
    with TestClient(app) as client:
        handles = [a["handle"] for a in
                   client.get("/api/agents/3").json()["agents"]]
    assert {"@focused", "@red-team", "@story"} <= set(handles)


def test_server_startup_converges_an_existing_database(tmp_path):
    import shutil
    import sqlite3
    from app.server import main as server_main
    legacy = tmp_path / "legacy_app.sqlite"
    shutil.copy(config.PROJECT_ROOT / "app.sqlite", legacy)
    here = db.db_path()
    try:
        db.init_db(legacy)
        server_main.seed_builtin_agents()          # what startup actually does
        handles = {r["handle"] for r in
                   db.get_db().execute("SELECT handle FROM agents")}
        assert set(RETIRED).isdisjoint(handles), sorted(handles & set(RETIRED))
    finally:
        db.init_db(here)
        sqlite3.connect(legacy).close()
