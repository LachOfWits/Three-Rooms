"""The house style, enforced (PENDING-BATCH2 §12).

The brevity contract that persona prompts bind on the LIVE path shaped
nothing in mock, where the prose is hardcoded templates — and mock is the
mode any reliable demo runs in, so the mock output *is* the product to
anyone watching. This module is the only thing that keeps that fixed.

It drives a full mock cycle over the COMMITTED runs (outputs/<YYYY_MM>/v1
— the same pattern as tests/test_research.py and
tests/test_snapshots_notifications.py: no engine subprocess, real tool
calls, real citation binding) and asserts of every published FEED post:

    lead line   one sentence, <= 25 words
    bullets     at most 5
    ceiling     90 words

plus: nothing was suppressed (the rewrite kept every claim bound to the
same tool call), no banned phrase appears anywhere — not in a post, not in
a template that this cycle's data happened not to reach — and the depth
that came off the feed landed on a BACKING PAGE rather than in the bin.

"Feed post" means an `origin` row: the thing a human scrolls past.
`expansion` rows are the backing pages and length is welcome there — that
is the whole point of the rewrite, so a test that capped them would undo
it. Replies are checked for banned phrases only; a mock reply quotes a
finding verbatim so its claims re-bind, and quoting cannot be shortened
without breaking the binding it exists to preserve.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_brevity_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"          # no API call, ever, from here

import pytest

from app import config
from app.agents import api as agents_api
from app.agents import style
from app.server import db

DB_FILE = _TMP / "brevity.sqlite"

CHECKS_DIR = Path(agents_api.__file__).resolve().parent / "checks"

# A walked-forward date the committed series actually reach, so the
# snapshot templates RUN rather than degrading to an error stub — an
# unreached template is an unmeasured one.
SNAPSHOT_THROUGH = "2026-04-15"

# The cycle must actually have happened. These are floors, not targets: a
# fixture that silently produced three posts would otherwise sail through
# every assertion below by having nothing to assert about.
MIN_FEED_POSTS = 20
MIN_BACKING_PAGES = 10


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


def _committed_run(conn, month: str):
    """A 'done' run row over the committed base run for <month> —
    outputs/<YYYY_MM>/v1/pricing (PENDING-BATCH2 §1)."""
    out_dir = config.OUTPUTS_DIR / month.replace("-", "_") / "v1" / "pricing"
    assert (out_dir / "valuation.json").exists(), out_dir
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, seed, sims, status, out_dir) "
        "VALUES (?, 'base', 20260831, 50000, 'done', ?)",
        (month, str(out_dir)))
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE id = ?",
                        (cur.lastrowid,)).fetchone()


@pytest.fixture(scope="module")
def pair(conn):
    return _committed_run(conn, "2026-02"), _committed_run(conn, "2026-03")


@pytest.fixture(scope="module")
def cycle(conn, pair):
    """Research → room 1 → room 2 → room 3 → a fresh snapshot: every mock
    template the roster can reach on a clean pair, in the order the cycle
    runs them."""
    prev, curr = pair
    res = agents_api.run_research_pass("2026-03")
    assert res["mode"] == "mock" and not res["errors"], res
    ids: list[int] = []
    for room in (1, 2, 3):
        ids += agents_api.run_room_pass(room, prev["id"], curr["id"],
                                        seeded=False)
    snap = agents_api.run_snapshot(curr["id"],
                                   data_through=SNAPSHOT_THROUGH)
    ids += snap["post_ids"]
    assert ids
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks}) ORDER BY id",
        ids).fetchall()


def _feed(cycle):
    return [p for p in cycle if p["type"] == "origin"]


def _backing(cycle):
    return [p for p in cycle if p["type"] == "expansion"]


def _describe(p) -> str:
    return f"{p['author_label']} (room {p['room']}, post {p['id']})"


# ==========================================================================
# the contract itself
# ==========================================================================

def test_the_cycle_actually_ran(cycle):
    """A floor under every other test in this module: no vacuous pass."""
    feed, backing = _feed(cycle), _backing(cycle)
    assert len(feed) >= MIN_FEED_POSTS, len(feed)
    assert len(backing) >= MIN_BACKING_PAGES, len(backing)
    assert {p["room"] for p in cycle} == {1, 2, 3}
    # and it is genuinely the whole roster talking, not one agent looping
    assert len({p["author_label"] for p in feed}) >= 12


def test_every_feed_post_is_within_the_word_ceiling(cycle):
    over = [(_describe(p), style.word_count(p["body_md"]))
            for p in _feed(cycle)
            if style.word_count(p["body_md"]) > style.FEED_WORD_CEILING]
    assert not over, (
        f"feed posts over {style.FEED_WORD_CEILING} words — the depth "
        f"belongs on the backing page, not here: {over}")


def test_every_feed_post_has_at_most_five_bullets(cycle):
    over = [(_describe(p), len(style.bullet_lines(p["body_md"])))
            for p in _feed(cycle)
            if len(style.bullet_lines(p["body_md"])) > style.FEED_MAX_BULLETS]
    assert not over, f"more than {style.FEED_MAX_BULLETS} bullets: {over}"


def test_every_feed_post_leads_with_one_short_sentence(cycle):
    over = []
    for p in _feed(cycle):
        lead = style.lead_line(p["body_md"])
        n = style.word_count(lead)
        if n > style.LEAD_WORD_CEILING:
            over.append((_describe(p), n, lead[:90]))
        # ...and the lead is a LEAD, not the first bullet of a list
        assert not style._BULLET.match(lead), _describe(p)
    assert not over, (
        f"lead lines over {style.LEAD_WORD_CEILING} words: {over}")


def test_house_style_breaches_reports_the_same_verdict(cycle):
    """The one helper a template author is expected to call must agree
    with the three assertions above — a measure nobody can trust is worse
    than no measure."""
    for p in _feed(cycle):
        assert style.house_style_breaches(p["body_md"]) == [], _describe(p)


# ==========================================================================
# the rewrite preserved every claim binding (§12: "preserve every claim
# binding" — a shorter post that no longer binds is not a fix)
# ==========================================================================

def test_nothing_was_suppressed(cycle):
    bad = [(_describe(p), p["suppression_reason"]) for p in cycle
           if p["status"] != "published"]
    assert not bad, f"the rewrite broke a claim binding: {bad}"


def test_the_numbers_still_bind_to_tool_calls(conn, cycle):
    """Every claim that survived onto a post still names a recorded tool
    call, and the posts that carry figures still carry claims. Shortening
    a post must move a figure, never orphan it."""
    carrying = 0
    for p in cycle:
        claims = json.loads(p["claims_json"] or "[]")
        for c in claims:
            assert c.get("tool_call_id"), (_describe(p), c)
            row = conn.execute(
                "SELECT id FROM tool_calls WHERE id = ?",
                (c["tool_call_id"],)).fetchone()
            assert row is not None, (_describe(p), c)
        if claims:
            carrying += 1
    assert carrying >= MIN_FEED_POSTS, carrying


# ==========================================================================
# banned phrases — in the output AND in the templates the output missed
# ==========================================================================

def test_no_banned_phrase_in_any_post(cycle):
    hits = [(_describe(p), style.banned_phrases_in(p["body_md"]))
            for p in cycle if style.banned_phrases_in(p["body_md"])]
    assert not hits, hits


_BAN_DECL = re.compile(r"BANNED_PHRASES\s*=\s*\([^)]*\)", re.S)


def _banned_in_source(text: str) -> list[tuple[int, str]]:
    """(line, phrase) for every banned phrase in a source file, ignoring the
    one place the phrases are legitimately written down: the declaration of
    the ban itself."""
    decl = [m.span() for m in _BAN_DECL.finditer(text)]
    out = []
    for phrase in style.BANNED_PHRASES:
        for m in re.finditer(re.escape(phrase), text):
            if any(a <= m.start() and m.end() <= b for a, b in decl):
                continue
            out.append((text[:m.start()].count("\n") + 1, phrase))
    return out


def test_the_source_scanner_can_actually_see_a_banned_phrase():
    """Guard against a scanner that passes because it finds nothing
    anywhere. It must fire on a planted phrase and stay silent on the
    declaration that names them."""
    planted = 'origin.add("It is worth noting that the vol rose.")'
    assert _banned_in_source(planted) == [(1, "It is worth noting")]
    decl_only = ('BANNED_PHRASES = ("Independent cross-check", '
                 '"It is worth noting",\n                  "Importantly", '
                 '"In summary")\n')
    assert _banned_in_source(decl_only) == []


def test_no_banned_phrase_in_any_mock_template(cycle):
    """A phrase on a branch this month's data never reached is still in the
    product. Read the templates themselves, not only what they printed."""
    files = sorted(CHECKS_DIR.glob("*.py")) + [Path(style.__file__).resolve()]
    assert len(files) >= 5, files
    hits = [(f.name, line, phrase) for f in files
            for line, phrase in _banned_in_source(
                f.read_text(encoding="utf-8"))]
    assert not hits, f"banned phrase left in a mock template: {hits}"


# ==========================================================================
# the depth was RELOCATED, not deleted (§12: "Do not delete that content —
# relocate it")
# ==========================================================================

# Every agent whose feed post lost method, working or a limitation list to
# a backing page. If one of these stops publishing an expansion, the
# content was dropped rather than moved and this test says so.
BACKED_BY_A_PAGE = ("@pre-flight-checks", "@vcv", "@holdings", "@focused",
                    "@red-team", "@results-validator", "@vlad", "@attrib",
                    "@rates-desk", "@credit-desk", "@equity-desk",
                    "@pc-desk", "@realist", "@lily", "@warden")

# @wide-eye and @focused's room-3 brief are the exceptions, and deliberate:
# their depth is the RESEARCH REPORT each writes in the research stage —
# a full section per risk on the Research tab, where §2 says length is
# welcome. A second copy in an expansion would be the same document twice.
DEPTH_IS_THE_RESEARCH_REPORT = ("@wide-eye", "@focused")


@pytest.mark.parametrize("handle", BACKED_BY_A_PAGE)
def test_depth_moved_to_a_backing_page(cycle, handle):
    pages = [p for p in _backing(cycle) if p["author_label"] == handle]
    assert pages, f"{handle} lost its depth instead of relocating it"
    assert any(p["status"] == "published" for p in pages), handle
    # a backing page that is no longer than the feed post is not a backing
    # page; length is welcome here and nowhere else
    feed = [p for p in _feed(cycle) if p["author_label"] == handle]
    assert feed, handle
    assert max(style.word_count(p["body_md"]) for p in pages) > \
        max(style.word_count(p["body_md"]) for p in feed), handle


@pytest.mark.parametrize("handle", DEPTH_IS_THE_RESEARCH_REPORT)
def test_research_desks_point_at_their_report(cycle, handle):
    """The other half of "relocate, do not delete": these two shortened
    their room-3 post to a few fragments, and the long form is the note
    they cite by name — which must exist on disk, be named in the post, and
    be a document rather than a paragraph."""
    posts = [p for p in _feed(cycle)
             if p["author_label"] == handle and p["room"] == 3]
    assert posts, handle
    body = posts[0]["body_md"]
    agent = handle.lstrip("@")
    note = config.OUTPUTS_DIR / "research" / f"2026_03_{agent}.md"
    assert note.exists(), note
    assert note.name in body, (handle, body[:120])
    assert len(note.read_text(encoding="utf-8").split()) > 400, handle


def test_backing_pages_are_deliberately_long(cycle):
    """The mirror of the ceiling: if the pages were also trimmed to 90
    words the content went in the bin, and every assertion above would
    still pass."""
    pages = [p for p in _backing(cycle) if p["status"] == "published"]
    assert pages
    long_pages = [p for p in pages
                  if style.word_count(p["body_md"]) > style.FEED_WORD_CEILING]
    assert len(long_pages) >= MIN_BACKING_PAGES, (
        f"only {len(long_pages)} backing pages carry real depth")


# ==========================================================================
# per-agent shapes named in the §12 table
# ==========================================================================

def _origin(cycle, handle, room):
    for p in _feed(cycle):
        if p["author_label"] == handle and p["room"] == room:
            return p
    return None


def test_red_team_posts_exactly_two_challenges_in_each_room(cycle):
    """§12: TWO challenges, the two that actually bite this month. The
    remaining standing limitations go to the backing page — six every
    month and nobody reads him by the second month."""
    for room in (1, 3):
        post = _origin(cycle, "@red-team", room)
        assert post is not None, room
        bullets = style.bullet_lines(post["body_md"])
        # two challenges, plus the one-line pointer at the rest
        assert len(bullets) in (3, 4), (room, bullets)
        challenges = [b for b in bullets if b.lstrip("- ").startswith("**")]
        assert len(challenges) == 2, (room, challenges)
        page = [p for p in _backing(cycle)
                if p["author_label"] == "@red-team" and p["room"] == room]
        assert page, room
        # the standing list survives, in full, on the page
        assert "(vi)" in page[0]["body_md"], room


def test_warden_is_the_aum_headline_plus_bullets(cycle):
    """§12: the AuM headline line + 3 bullets. Everything else is detail."""
    post = _origin(cycle, "@warden", 3)
    assert post is not None
    body = post["body_md"]
    lead = style.lead_line(body)
    assert lead.startswith("**AuM "), lead[:80]
    assert "premium" in lead and "investment performance" in lead
    assert "VaR" in lead and "of assets" in lead
    bullets = style.bullet_lines(body)
    assert 3 <= len(bullets) <= style.FEED_MAX_BULLETS, bullets
    assert post["pinned"] == 1


def test_a_quiet_post_is_one_line_and_stops(cycle):
    """§12: nothing material -> ONE line, significance quiet. @realist on a
    clean pair is the standing example."""
    quiet = [p for p in _feed(cycle) if p["significance"] == "quiet"]
    assert quiet, "a clean cycle with nothing quiet is not a clean cycle"
    for p in quiet:
        assert style.word_count(p["body_md"]) <= style.FEED_WORD_CEILING
    realist = _origin(cycle, "@realist", 3)
    assert realist is not None
    assert realist["significance"] == "quiet", realist["body_md"][:120]
    assert style.bullet_lines(realist["body_md"]) == []
    assert len(realist["body_md"].strip().splitlines()) == 1


def test_the_desks_keep_market_then_book_then_watch(cycle):
    """§12: desks are market / watch / book, one bullet each — inside the
    ceiling, with the fuller market note on the page."""
    for handle in ("@rates-desk", "@credit-desk", "@equity-desk",
                   "@pc-desk"):
        post = _origin(cycle, handle, 3)
        assert post is not None, handle
        body = post["body_md"]
        assert "Research note (" in body, handle       # the market, first
        assert "**For the results below.**" in body, handle
        head = body[:body.index("**For the results below.**")]
        assert "VaR" not in head, handle          # not our results, first
        assert 1 <= len(style.bullet_lines(head)) <= 4, handle


# ==========================================================================
# branches this month's data did not take
#
# A ceiling that holds only on the happy path is not a ceiling. These force
# the templates that fire on a flag, a break or a missing input and measure
# the same three things.
# ==========================================================================

def _shape_ok(drafts, label):
    for d in drafts:
        if d.get("kind", "origin") != "origin":
            continue
        breaches = style.house_style_breaches(d["body"])
        assert not breaches, (label, breaches, d["body"][:400])


def test_preflight_do_not_run_verdict_stays_inside_the_ceiling(conn, pair):
    """A blocking verdict is the loudest post the room can produce, and the
    one most likely to grow back into an essay."""
    from app.agents.checks import room1
    prev, curr = pair
    ctx = agents_api.PassContext(1, prev, curr, seeded=False)
    real = room1._pf_reconciliation

    def broken(s, doc, tc_a, asof):
        rows, items = real(s, doc, tc_a, asof)
        for row in rows[:4]:
            row["ok"] = False
            items.append(room1._Item(
                "reconciliation", f"{row['label']} does not match source",
                True, found=row["assumed"], expected=row["source_cited"],
                found_tc=tc_a, expected_tc=row["tc_src"],
                found_txt="0.0000%", expected_txt=row["source_txt"]))
        return rows, items

    room1._pf_reconciliation = broken
    try:
        drafts = room1.pre_flight_checks(ctx)
    finally:
        room1._pf_reconciliation = real
    assert drafts[0]["body"].startswith("**DO NOT RUN")
    _shape_ok(drafts, "@pre-flight-checks DO NOT RUN")


def test_vlad_joint_plausibility_flag_stays_inside_the_ceiling(conn, pair):
    from app.agents.checks import room2
    prev, curr = pair
    ctx = agents_api.PassContext(2, prev, curr, seeded=False)
    real = room2.VLAD_JOINT_PCTL_LIMIT
    room2.VLAD_JOINT_PCTL_LIMIT = -1.0        # force the flag branch
    try:
        drafts = room2.vlad(ctx)
    finally:
        room2.VLAD_JOINT_PCTL_LIMIT = real
    assert "**FLAG — joint plausibility.**" in drafts[0]["body"]
    _shape_ok(drafts, "@vlad joint-plausibility FLAG")


def test_realist_out_of_band_flag_stays_inside_the_ceiling(conn, pair):
    """Three quoted outliers is the widest this post ever gets."""
    from app.agents.checks import room3
    prev, curr = pair
    ctx = agents_api.PassContext(3, prev, curr, seeded=False)
    real = room3._realist_band
    room3._realist_band = lambda p, priors: (0.0, 0.0)   # everything out
    try:
        drafts = room3.realist(ctx)
    finally:
        room3._realist_band = real
    assert drafts[0]["body"].startswith("**FLAG — reasonableness**")
    _shape_ok(drafts, "@realist out-of-band")


def test_warden_without_a_pair_stays_inside_the_ceiling(conn, pair):
    from app.agents.checks.room3 import warden
    _prev, curr = pair
    ctx = agents_api.PassContext(3, None, curr, seeded=False)
    drafts = warden(ctx)
    assert "no market / flows / decision split" in drafts[0]["body"]
    _shape_ok(drafts, "@warden single month")


def test_story_with_nothing_to_say_is_one_line(conn, pair):
    """§10: a month with no story says so in a line. In rooms 1 and 2 that
    is the whole post; room 3 still carries its worth-reading footer,
    which is a pointer list, not a story."""
    from app.agents.checks import story as story_mod
    prev, curr = pair
    real = story_mod._open_stories
    story_mod._open_stories = lambda seen: []
    try:
        for room in (1, 2, 3):
            ctx = agents_api.PassContext(room, prev, curr, seeded=False)
            drafts = story_mod.story_post(ctx)
            body = drafts[0]["body"]
            assert "No story yet" in body, room
            _shape_ok(drafts, f"@story quiet month, room {room}")
            if room < 3:
                assert len(body.strip().splitlines()) == 1, room
    finally:
        story_mod._open_stories = real


def test_draft_report_review_with_findings_stays_inside_the_ceiling(
        conn, pair):
    """Two findings, each with its figure and its correction, is the
    longest this post gets — and it is the post a reader most needs to be
    able to read at a glance."""
    from app.agents.checks.room3 import draft_report_review
    prev, curr = pair
    ctx = agents_api.PassContext(3, prev, curr, seeded=True)
    drafts = draft_report_review(ctx)
    assert drafts, "the seeded draft report should reconcile against a pair"
    body = drafts[0]["body"]
    assert "SUM of the five block standalone VaRs" in body      # D3A
    assert "sign flip" in body and "attribution.json step fx" in body  # D3B
    _shape_ok(drafts, "@results-validator draft-report findings")
    # the method and the comparison-month working moved to the page
    assert len(drafts) == 2 and drafts[1]["kind"] == "expansion"
    assert style.word_count(drafts[1]["body"]) > style.FEED_WORD_CEILING


def test_desks_without_a_pair_stay_inside_the_ceiling(conn, pair):
    from app.agents.checks import room3
    _prev, curr = pair
    ctx = agents_api.PassContext(3, None, curr, seeded=False)
    for fn in (room3.rates_desk, room3.credit_desk, room3.equity_desk,
               room3.pc_desk, room3.attrib):
        _shape_ok(fn(ctx), fn.__name__)


# ==========================================================================
# the measure itself
#
# Everything above rests on style.word_count agreeing with what a reader
# sees. These pin it, so the ceiling cannot be met by making the ruler
# shorter.
# ==========================================================================

def test_word_count_ignores_markdown_but_counts_figures():
    # emphasis, table pipes, middots and dashes are punctuation; a money
    # figure is a word, and so is the token it is glued to
    assert style.word_count("**AuM £979.75m, +£2.22m** — premium") == 4
    assert style.word_count("- a · b — c") == 3
    assert style.word_count("`file.md` | table |") == 2
    assert style.word_count("4.1234%–4.5678% intra-month") == 2
    assert style.word_count("") == 0


def test_lead_line_and_bullets_are_read_off_the_markdown():
    body = "Lead sentence here.\n- one\n- two\n\nTail paragraph."
    assert style.lead_line(body) == "Lead sentence here."
    assert len(style.bullet_lines(body)) == 2
    assert style.shape(body)["lead_words"] == 3


def test_the_ceilings_are_the_ones_the_spec_names():
    assert style.FEED_WORD_CEILING == 90
    assert style.FEED_MAX_BULLETS == 5
    assert style.LEAD_WORD_CEILING == 25
    assert set(style.BANNED_PHRASES) == {
        "Independent cross-check", "It is worth noting", "Importantly",
        "In summary"}
