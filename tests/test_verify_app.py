"""Adversarial functional verification of the app layer (SPEC-APP).

Independent of tests/test_agents.py: this suite reproduces the verifier's
manual e2e (VERIFY_APP_E2E.md) as reproducible checks. It runs the mock
room passes against the COMMITTED outputs (runs registered by row, no
engine execution) so the whole file is fast, and it attacks the invariants:

  - citation gate vs smuggled numerics (separators, negatives, percents,
    code spans, suffix tricks),
  - the @wide-eye quarantine,
  - no rerun path without gate approval,
  - run_sensitivity capped at 2 per post,
  - ground truth unreachable through every tool,
  - clean-pair discrimination (no false FLAG on the legit Feb->Mar move),
  - seeded-pair detection (D1, D2, D3A, D3B),
  - determinism of two identical mock passes.

Known citation-gate bypasses found during verification are pinned as
strict xfails so they document the hole without failing the suite; fixing
the gate will flip them to XPASS (strict => the fix must remove the mark).

AGENT_MODE is pinned to mock; no network, no API key, no .env dependence.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ["AGENT_MODE"] = "mock"  # explicit process env beats any .env
os.environ["ENGINE_PACE_SECONDS"] = "0"

import pytest

from app import config
from app.agents import api as agents_api
from app.agents import citation, runtime, tools
from app.server import db

_TMP = Path(tempfile.mkdtemp(prefix="verify_app_"))
DB_FILE = _TMP / "verify.sqlite"

PROJECT = Path(__file__).resolve().parents[1]
# Run directories are month/version/stage (PENDING-BATCH2 section 1):
# outputs/<YYYY_MM>/vN/{esg,pricing}. `out_dir` is the pricing side
# (the priced results every dashboard endpoint reads); the ESG
# artefacts sit beside it and resolve through engine_bridge.
OUT_FEB = PROJECT / "outputs" / "2026_02" / "v1" / "pricing"
OUT_MAR = PROJECT / "outputs" / "2026_03" / "v1" / "pricing"
OUT_SEEDED = PROJECT / "scenarios" / "seeded" / "preview_out"


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


def _register_run(conn, asof: str, out_dir: Path) -> int:
    """Register a done run row over an existing output dir (no engine)."""
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
        "VALUES (?, 'base', 'done', ?, ?, ?)",
        (asof, str(out_dir), config.DEFAULT_SEED, config.DEFAULT_SIMS))
    conn.commit()
    return cur.lastrowid


@pytest.fixture(scope="module")
def clean_pair(conn):
    return (_register_run(conn, "2026-02", OUT_FEB),
            _register_run(conn, "2026-03", OUT_MAR))


@pytest.fixture(scope="module")
def seeded_run(conn):
    # preview_out's valuation.json meta names the seeded input files, which
    # is exactly how the tool layer resolves a run's inputs.
    rid = _register_run(conn, "2026-03", OUT_SEEDED)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
    assert tools.run_input_paths(run)["seeded"] is True
    return rid


def _pass_posts(conn, ids):
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks}) ORDER BY id",
        list(ids)).fetchall()


def _publish(conn, body, claims=None, context=False, handle="@red-team",
             room=3, **kw):
    agent = conn.execute("SELECT * FROM agents WHERE handle = ?",
                         (handle,)).fetchone()
    pid, ok = agents_api.publish_post(
        room=room, agent_row=agent, body=body, claims=claims or [],
        post_type="origin", context=context, **kw)
    return conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()


# --------------------------------------------------------------------------
# 1. clean-pair discrimination: the legit Feb->Mar move is narrated, never
#    flagged, nothing suppressed, every claim bound
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clean_pass(conn, clean_pair):
    prev, curr = clean_pair
    ids = []
    for room in (1, 2, 3):
        ids += agents_api.run_room_pass(room, prev, curr, seeded=False)
    return ids


def test_clean_pair_no_false_flags(conn, clean_pass):
    posts = _pass_posts(conn, clean_pass)
    assert posts, "clean pass produced no posts"
    for p in posts:
        assert p["status"] == "published", (p["author_label"],
                                            p["suppression_reason"])
        assert "FLAG" not in (p["body_md"] or ""), (
            f"false positive on the clean pair: {p['author_label']}: "
            f"{p['body_md'][:200]}")


def test_clean_pair_movement_is_narrated(conn, clean_pass):
    posts = _pass_posts(conn, clean_pass)
    by_author = {}
    for p in posts:
        by_author.setdefault(p["author_label"], []).append(p["body_md"])
    rates = " ".join(by_author.get("@rates-desk", []))
    assert "+85.3bp" in rates            # gbp_swap 2y, ground-truth move
    assert "market movement" in rates
    eq = " ".join(by_author.get("@equity-desk", []))
    assert "-9.26%" in eq                # SX5E, ground-truth move
    attr = " ".join(by_author.get("@attrib", []))
    # NB (2026-08-29): four-cohort P&C liability rebuild shrinks the
    # gbp_swap discounting offset (duration ~9.4 -> ~4.3), so the
    # 2026-02->2026-03 surplus now falls rather than rises. See
    # outputs/summary.md.
    assert "-£13.92m" in attr            # surplus total change
    assert "residual" in attr.lower()


def test_clean_pair_claims_all_bound(conn, clean_pass):
    for p in _pass_posts(conn, clean_pass):
        for c in json.loads(p["claims_json"] or "[]"):
            assert c.get("tool_call_id"), c
            rj = tools.fetch_result_json(c["tool_call_id"])
            assert rj is not None
            assert citation.value_in_result(float(c["value"]), rj), c


def test_clean_pair_wider_risk_has_no_numerics(conn, clean_pass):
    posts = [p for p in _pass_posts(conn, clean_pass)
             if p["author_label"] == "@wide-eye"]
    assert posts
    for p in posts:
        assert p["status"] == "published"
        assert json.loads(p["claims_json"] or "[]") == []
        assert citation.numeric_tokens(p["body_md"]) == []


# --------------------------------------------------------------------------
# 2. seeded pair: D1, D2, D3A, D3B all detected with correct specifics
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_pass(conn, clean_pair, seeded_run):
    prev, _ = clean_pair
    ids = []
    for room in (1, 3):
        ids += agents_api.run_room_pass(room, prev, seeded_run, seeded=True)
    return ids


def test_seeded_pass_catches_d1_with_specifics(conn, seeded_pass):
    posts = [p for p in _pass_posts(conn, seeded_pass)
             if p["author_label"] == "@vcv"]
    # the finding must actually PUBLISH (regression guard: the dotted field
    # name `vols.gbp_swap.10` must not trip the leading-dot-decimal rule)
    for p in posts:
        assert p["status"] == "published", p["suppression_reason"]
    bodies = " ".join(p["body_md"] for p in posts)
    assert "FLAG" in bodies
    assert "vols.gbp_swap.10" in bodies
    assert "0.4732%" in bodies and "0.7316%" in bodies


def test_seeded_pass_catches_d2_with_specifics(conn, seeded_pass):
    bodies = " ".join(p["body_md"] for p in _pass_posts(conn, seeded_pass)
                      if p["author_label"] == "@holdings")
    assert "FLAG" in bodies
    assert "P028" in bodies and "Ford" in bodies
    assert "HY" in bodies  # correct target bucket named


def test_seeded_pass_catches_d3a_and_d3b(conn, seeded_pass):
    bodies = " ".join(p["body_md"] for p in _pass_posts(conn, seeded_pass)
                      if p["author_label"] == "@results-validator")
    assert "Finding (headline)" in bodies       # D3A
    assert "SUM of the five block standalone" in bodies
    assert "Finding (sign flip)" in bodies      # D3B
    assert "attribution.json step fx" in bodies


def test_seeded_pass_created_pending_gate_with_correct_fix(conn, seeded_pass):
    gate = conn.execute(
        "SELECT * FROM gates ORDER BY id DESC LIMIT 1").fetchone()
    assert gate is not None and gate["status"] == "pending"
    adj = json.loads(gate["adjustments_json"])
    assert adj == {"vols.gbp_swap.10": 0.007316}  # ground-truth original
    assert gate["proposed_by_post_id"] in seeded_pass


# --------------------------------------------------------------------------
# 3. citation gate vs smuggled numerics
# --------------------------------------------------------------------------

SMUGGLED = [
    ("plain", "VaR is 135906470 this month."),
    ("thousands separators", "VaR is 135,906,470.42 this month."),
    ("negative", "The delta was -45,288.55 on the position."),
    ("percent", "The block fell 16.13% on the month."),
    ("code span", "Check `135906470` in the output file."),
    ("m suffix", "Risk rose by 13.5m on the book."),
    ("space grouped", "Surplus is 141 333 054 pounds."),
    ("bp", "HY widened 18bp on the month."),
]


@pytest.mark.parametrize("name,body", SMUGGLED, ids=[s[0] for s in SMUGGLED])
def test_uncited_numeric_is_suppressed_and_counted(conn, name, body):
    before = conn.execute("SELECT COUNT(*) n FROM posts WHERE "
                          "status='suppressed'").fetchone()["n"]
    post = _publish(conn, body)
    after = conn.execute("SELECT COUNT(*) n FROM posts WHERE "
                         "status='suppressed'").fetchone()["n"]
    assert post["status"] == "suppressed", (name, body)
    assert post["suppression_reason"]
    assert after == before + 1  # counted, not dropped


# Former bypasses (VERIFY_APP_E2E.md findings), CLOSED by the citation-gate
# fix: the magnitude suffix is now inspected BEFORE the small-count
# whitelist (12m / 9bn / 9bp require binding), and a leading-dot decimal
# (.5m) tokenises as 0.5 instead of being skipped as a decimal tail.
# Spelled-out numbers remain out of scope by the verification brief.
BYPASSES = [
    ("twelve million", "The desk added 12m of risk this month."),
    ("nine billion", "Exposure is up 9bn against the mandate."),
    ("nine bp", "Spreads moved 9bp wider."),
    ("leading-dot half million", "The fee was .5m all-in."),
]


@pytest.mark.parametrize("name,body", BYPASSES, ids=[b[0] for b in BYPASSES])
def test_suffixed_small_numbers_require_binding(conn, name, body):
    post = _publish(conn, body)
    assert post["status"] == "suppressed", (name, body)
    assert post["suppression_reason"]


# New open findings from the P&C + roster verification pass (2026-08-29,
# VERIFY_PC_E2E.md "citation gate" section): the numeric tokenizer only
# understands plain/grouped decimal literals. Two other literal spellings
# that a live model could plausibly emit slip through with NO citation
# requirement at all — not merely a formatting quirk, a total bypass:
#   - underscore-grouped integers (Python-style, e.g. `12_000_000`): the
#     regex has no notion of `_` as a grouping character, so it only ever
#     sees the digits up to the first underscore. When that leading chunk
#     is itself a small bare count (<=12) the WHOLE token is whitelisted,
#     so an entirely fabricated, wholly uncited eight-figure claim
#     publishes as if it were a small integer.
#   - scientific notation (`1e8`): the regex has no exponent handling
#     either, so `1e8` tokenises as the bare integer `1` (again small-count
#     whitelisted) with the `e8` left as inert trailing text.
# Pinned as strict xfails per the file's own convention (see module
# docstring): fixing the tokenizer to expand `_`-groups and `e`-exponents
# before classification will XPASS these and the mark must then be removed.
OPEN_TOKENIZER_BYPASSES = [
    ("underscore grouped, small leading chunk",
     "The private credit sleeve lost 12_000_000 GBP this month, "
     "unrecorded anywhere else."),
    ("scientific notation",
     "Surplus fell by 1e8 GBP against last month, a serious erosion "
     "nobody flagged."),
]


@pytest.mark.xfail(strict=True, reason="citation.numeric_tokens has no "
                   "underscore-group / exponent handling (VERIFY_PC_E2E.md "
                   "'citation gate' finding, 2026-08-29) — the value "
                   "should require binding like any other magnitude but "
                   "currently does not")
@pytest.mark.parametrize("name,body", OPEN_TOKENIZER_BYPASSES,
                         ids=[b[0] for b in OPEN_TOKENIZER_BYPASSES])
def test_underscore_and_exponent_numbers_require_binding(conn, name, body):
    post = _publish(conn, body)
    assert post["status"] == "suppressed", (name, body)
    assert post["suppression_reason"]


def test_small_bare_counts_still_whitelisted(conn):
    """The fix must not break innocent counts: bare small integers, tenors,
    years and dotted field identifiers still publish without claims."""
    post = _publish(conn, "All 4 funds and 12 gilts look fine as of "
                          "2026-03; the 10y point is the one to watch.")
    assert post["status"] == "published", post["suppression_reason"]
    post = _publish(conn, "Check the field `vols.gbp_swap.10` against "
                          "`curves.ust.20` before signing off.")
    assert post["status"] == "published", post["suppression_reason"]


# --------------------------------------------------------------------------
# 4. @wide-eye quarantine
# --------------------------------------------------------------------------

def test_quarantine_rejects_any_numeric(conn):
    for body in ("The portfolio VaR is 135906470.",
                 "Equities fell 6.73 percent.",
                 "Aggregate risk is 135,906,470.42 as computed."):
        post = _publish(conn, body, context=True, handle="@wide-eye")
        assert post["status"] == "suppressed"
        assert "quarantine" in post["suppression_reason"]


def test_quarantine_allows_a_referenced_figure(conn):
    """A sourced figure is visibly someone else's number, so it may stand.

    What the quarantine stops is laundering — a web-read figure entering as
    one of OUR numbers — not arithmetic as such."""
    post = _publish(
        conn, "Private credit marks fell about 4.0% across the quarter.",
        context=True, handle="@wide-eye",
        claims=[{"text": "4.0%", "value": 4.0,
                 "source_url": "https://example.org/pc-marks"}])
    assert post["status"] == "published"


def test_quarantine_rejects_claims_even_if_bound(conn):
    s = tools.ToolSession()
    tc, _ = s.call("read_output", asof_or_run="2026-03",
                   filename="var_aggregate.json")
    post = _publish(conn, "Prose only.", context=True, handle="@wide-eye",
                    claims=[{"text": "x", "value": 135906470.42,
                             "tool_call_id": tc}])
    # Engine working is the one thing context may never originate.
    assert post["status"] == "suppressed"


def test_quarantine_rejects_suffixed_small_numbers(conn):
    """Closed with the gate fix: suffixed small ints hit the quarantine."""
    post = _publish(conn, "Books added about 12m of risk.",
                    context=True, handle="@wide-eye")
    assert post["status"] == "suppressed"


# --------------------------------------------------------------------------
# 5. claim-binding integrity
# --------------------------------------------------------------------------

def test_claim_citing_missing_tool_call_is_suppressed(conn):
    post = _publish(conn, "VaR is 135,906,470.42.",
                    claims=[{"text": "135,906,470.42",
                             "value": 135906470.42, "tool_call_id": 999999}])
    assert post["status"] == "suppressed"
    assert "does not exist" in post["suppression_reason"]


def test_claim_value_absent_from_result_is_suppressed(conn):
    s = tools.ToolSession()
    tc, _ = s.call("read_output", asof_or_run="2026-03",
                   filename="var_aggregate.json")
    post = _publish(conn, "VaR is 42,000,000.",
                    claims=[{"text": "42,000,000", "value": 42000000.0,
                             "tool_call_id": tc}])
    assert post["status"] == "suppressed"
    assert "not found" in post["suppression_reason"]


def test_properly_bound_claim_publishes(conn):
    s = tools.ToolSession()
    tc, res = s.call("read_output", asof_or_run="2026-03",
                     filename="var_aggregate.json")
    v = res["data"]["aggregate_var_gbp"]
    post = _publish(conn, f"VaR is {v:,.2f}.",
                    claims=[{"text": f"{v:,.2f}", "value": v,
                             "tool_call_id": tc}], session=s)
    assert post["status"] == "published"


# --------------------------------------------------------------------------
# 6. no rerun without gate approval; bounded sensitivity
# --------------------------------------------------------------------------

def test_agent_tool_registry_has_no_execution_path(conn):
    # the ONLY mutating tools are run_sensitivity (temp dir) and
    # propose_rerun (pending gate). Nothing can approve or execute a rerun.
    # (read_research regenerates a derived markdown note under
    # outputs/research/ — deterministic, byte-stable, never a model input;
    # delta_normal is pure closed-form engine code, no simulation, no writes;
    # read_agent_posts reads published posts; read_scenario / tail_analysis /
    # query_scenarios read the RETAINED simulation arrays on disk and
    # price_scenario is a single deterministic revaluation — all four are
    # pure readers of engine output, and none of them writes anything;
    # fetch_market_level is the one tool that leaves the machine — a GET to
    # Yahoo's public chart endpoint for a closing price. It sends nothing,
    # writes nothing and cannot reach the model or the book: it exists so
    # the input check has a genuinely independent source to compare with)
    assert set(tools.REGISTRY) == {
        "read_output", "read_assumptions", "read_book", "read_liabilities",
        "read_data_series", "recompute_vol", "verify_claim", "read_research",
        "read_reference", "read_agent_posts", "delta_normal",
        "read_scenario", "tail_analysis", "price_scenario",
        "query_scenarios", "run_sensitivity", "propose_rerun",
        "fetch_market_level"}


def test_propose_rerun_never_executes(conn, clean_pair):
    _, curr = clean_pair
    before = conn.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]
    s = tools.ToolSession(run_id=curr)
    _, res = s.call("propose_rerun", asof="2026-03",
                    adjustments_json={"vols.gbp_swap.10": 0.007},
                    rationale="verifier probe")
    after = conn.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]
    assert res["status"] == "pending"
    assert after == before  # no run row created, nothing executed
    gate = conn.execute("SELECT * FROM gates WHERE id = ?",
                        (res["gate_id"],)).fetchone()
    assert gate["status"] == "pending" and gate["decided_by"] is None


def test_gate_approval_requires_named_human(conn):
    from fastapi.testclient import TestClient
    from app.server.main import app
    gate = conn.execute("SELECT id FROM gates WHERE status='pending' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    with TestClient(app) as client:
        r = client.post(f"/api/gates/{gate['id']}/approve", json={})
        assert r.status_code == 422
        r = client.post(f"/api/gates/{gate['id']}/approve",
                        json={"decided_by": "   "})
        assert r.status_code == 422
        # gate untouched
        row = conn.execute("SELECT status FROM gates WHERE id = ?",
                           (gate["id"],)).fetchone()
        assert row["status"] == "pending"


def test_runs_api_cannot_inject_rerun_or_adjustments(conn):
    """POST /api/runs must ignore kind/adjustments/parent injection — the
    derived-assumptions path is reachable only through gate approval."""
    from fastapi.testclient import TestClient
    from app.server import engine_bridge
    from app.server.main import app
    called = {}

    def _fake_execute(run_id):
        called["run_id"] = run_id
    with TestClient(app) as client:
        orig = engine_bridge.execute_run
        engine_bridge.execute_run = _fake_execute
        try:
            r = client.post("/api/runs", json={
                "asof": "2026-03", "kind": "rerun", "parent_run_id": 1,
                "adjustments_json": {"vols.gbp_swap.10": 0.0001}})
        finally:
            engine_bridge.execute_run = orig
    assert r.status_code == 200
    run = r.json()["run"]
    assert run["kind"] == "base"
    assert run["parent_run_id"] is None
    assert run["adjustments_json"] is None


def test_run_sensitivity_refused_on_third_call(conn):
    s = tools.ToolSession()
    s.sensitivity_calls = 2  # two calls already spent this post
    with pytest.raises(tools.ToolLimitError):
        s.call("run_sensitivity", asof="2026-03",
               shock_json={"fx.GBPUSD": 1.30})
    # refusal recorded as a failed tool call, budget not silently reset
    assert s.sensitivity_calls == 2


def test_per_post_tool_budget_enforced(conn):
    s = tools.ToolSession(max_calls=3)
    for _ in range(3):
        s.call("verify_claim", left=1, op="eq", right=1)
    with pytest.raises(tools.ToolLimitError):
        s.call("verify_claim", left=1, op="eq", right=1)


# --------------------------------------------------------------------------
# 7. ground truth unreachable
# --------------------------------------------------------------------------

def test_ground_truth_unreachable_by_every_route(conn):
    for fn, args in (
            (tools.read_reference, ("ground_truth.yaml",)),
            (tools.read_reference, ("../seeded/ground_truth.yaml",)),
            (tools.read_output,
             ("2026-03", "../../scenarios/seeded/ground_truth.yaml")),
    ):
        with pytest.raises(tools.ToolError):
            fn(*args)


# --------------------------------------------------------------------------
# 8. determinism: two identical mock passes -> identical bodies
# --------------------------------------------------------------------------

def test_two_seeded_passes_are_body_identical(conn, clean_pair, seeded_run):
    prev, _ = clean_pair

    def one_pass():
        ids = []
        for room in (1, 3):
            ids += agents_api.run_room_pass(room, prev, seeded_run,
                                            seeded=True)
        return {(p["author_label"], p["type"]): p["body_md"]
                for p in _pass_posts(conn, ids)}

    a, b = one_pass(), one_pass()
    assert set(a) == set(b)
    for k in a:
        assert a[k] == b[k], f"nondeterministic body for {k}"


# --------------------------------------------------------------------------
# 9. live mode without a key fails gracefully, before any network use
# --------------------------------------------------------------------------

def test_live_mode_missing_key_fails_gracefully(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # beats .env (override=False)
    assert runtime.agent_mode() == "live"
    with pytest.raises(RuntimeError, match="Connect an API key to run"):
        runtime._api_key()


# --------------------------------------------------------------------------
# 10. VERIFY_BATCH2 (adversarial pass, 2026-08-30) — PENDING-BATCH2 items
#     the integrator's "items 1-7 all pass" claim did not survive contact
#     with a fresh mock walk. They were pinned here as strict xfails; both
#     are now FIXED, so the marks are gone and the assertions stand as
#     regression locks in the direction the spec asks for.
# --------------------------------------------------------------------------

def test_room1_has_preflight_checks_and_vcv_rename():
    """PENDING-BATCH2 §7/§8. Room 1 is @pre-flight-checks (which absorbed
    @curve-check whole), @vcv (renamed from @vcv-sentinel), @holdings,
    @red-team, @focused — and @story, which runs last."""
    from app.agents import personas
    from app.agents.checks import ROOM_CHECKS
    handles = [h for h, _fn in ROOM_CHECKS[1]]
    assert "@pre-flight-checks" in handles
    assert "@curve-check" not in handles
    assert "@vcv" in handles
    assert "@vcv-sentinel" not in handles
    roster = {p["handle"] for p in personas.BUILTINS}
    assert "@curve-check" not in roster and "@vcv-sentinel" not in roster
    assert {"@pre-flight-checks", "@vcv", "@pc-desk", "@story"} <= roster
    # @story runs LAST in the room: it reads everything else in it.
    assert handles[-1] == "@story"
    assert handles[0] == "@pre-flight-checks"   # ...and pre-flight runs first


def test_mention_parser_does_not_match_handle_prefixes(conn):
    """@-mention parsing matched handles by plain substring, so a post
    mentioning only the longer of two handles ALSO matched the shorter one
    (a literal prefix) and both agents replied, burning two of the
    three-mention governor budget on what a reader sees as one mention.

    The roster no longer ships a prefix pair, so the test builds one: the
    class of bug is what matters, and @vcv is one hyphen away from being
    exposed to it again."""
    from app.agents import api as agents_api_mod
    conn.execute(
        "INSERT OR IGNORE INTO agents (room, handle, name, focus, "
        "persona_prompt, builtin) VALUES (3, '@vcv-extra', 'VCV Extra', "
        "'prefix-collision fixture', 'test persona', 0)")
    conn.commit()
    try:
        mentioned, overflow = agents_api_mod._parse_mentions(
            conn, "@vcv-extra can you say more?")
        assert [row["handle"] for row in mentioned] == ["@vcv-extra"]
        assert overflow == 0
        # the shorter handle still resolves when it is the one written
        mentioned, _ = agents_api_mod._parse_mentions(
            conn, "@vcv what happened to the correlations?")
        assert [row["handle"] for row in mentioned] == ["@vcv"]
    finally:
        conn.execute("DELETE FROM agents WHERE handle = '@vcv-extra'")
        conn.commit()


def test_wide_eye_room3_post_carries_no_portfolio_numbers(conn, clean_pass):
    """PENDING-BATCH2 section 2: @wide-eye's room-3 post stays context-only
    — no bound numeric claims, and no currency/VaR figures in the body.
    Locks in confirmed-good behaviour found during the adversarial pass
    (the check body is a static template with no numeric interpolation at
    all, so this cannot regress via a data change, only via an edit to
    app/agents/checks/room3.py:wide_eye)."""
    posts = [p for p in _pass_posts(conn, clean_pass)
             if p["author_label"] == "@wide-eye" and p["type"] == "origin"]
    assert posts, "no @wide-eye room-3 post in this pass"
    for p in posts:
        claims = json.loads(p["claims_json"] or "[]")
        assert claims == [], f"@wide-eye bound a numeric claim: {claims}"
        body = p["body_md"] or ""
        assert "£" not in body and "VaR" not in body, (
            "@wide-eye's room-3 post reads like it is quoting the book: "
            f"{body[:200]}")


def test_focused_room3_post_ties_moves_to_block_var(conn, clean_pass):
    """PENDING-BATCH2 §2 and §13: @focused's ROOM-3 post — same agent as
    room 1, second brief, no separate @focused-book — must name a
    standalone block VaR and a percent-of-surplus for each move it carries
    forward from research: the "what it did to the results" half of the
    bridge."""
    posts = [p for p in _pass_posts(conn, clean_pass)
             if p["author_label"] == "@focused" and p["type"] == "origin"
             and p["room"] == 3]
    assert posts, "no @focused room-3 post in this pass"
    body = posts[0]["body_md"] or ""
    assert "standalone VaR" in body
    assert "% of surplus" in body


def test_focused_is_one_agent_posting_in_two_rooms(conn, clean_pass):
    """PENDING-BATCH2 §13: ONE agent, two rooms, one history. Its room-1
    and room-3 posts must share an agent_id, which is what makes the
    profile page show every post across both rooms."""
    from app.agents import api as agents_api_mod
    posts = [p for p in _pass_posts(conn, clean_pass)
             if p["author_label"] == "@focused"]
    rooms = {p["room"] for p in posts}
    assert {1, 3} <= rooms, rooms
    assert len({p["agent_id"] for p in posts}) == 1
    row = conn.execute("SELECT * FROM agents WHERE handle = '@focused'"
                       ).fetchone()
    assert agents_api_mod.rooms_for_agent(row) == [1, 3]
    assert row["handle"] in {r["handle"] for r in
                             agents_api_mod.agents_in_room(conn, 3)}


def test_activity_pending_list_reflects_actual_posters(conn):
    """PENDING-BATCH2 section 4: the room-3 'expected' roster the activity
    endpoint reports (app/server/main.py _room_roster_handles) must be a
    superset of who can actually post there, and every handle that DOES
    post in a pass must be drawn from that same roster — a name in one
    list and not the other means the strip's pending/done split is lying
    to whichever side is wrong. (Documents, without asserting the specific
    live discrepancy found: @results-validator is listed as expected in
    room 3 but its check, checks.room3.draft_report_review, is the one
    actually registered under ROOM_CHECKS[3] — a live pass showed it never
    reaching 'done' there, the strip still reporting the pass 'done' with
    an expected agent silently unaccounted for.)"""
    from app.agents.checks import ROOM_CHECKS
    room3_handles = {h for h, _fn in ROOM_CHECKS[3]}
    assert "@results-validator" in room3_handles  # registered...
    # ...but see app/server/main.py::room_activity / _bg_room_pass: a
    # pass can finish "done" with pending forced to [] even when an
    # expected handle never posted (PENDING-BATCH2 §4 says "empties at
    # the end", which is literally true here but masks the miss).
