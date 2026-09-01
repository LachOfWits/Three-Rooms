"""Deterministic mock checks behind each built-in persona (SPEC-APP 2, 5).

Every check runs real tool calls through a ToolSession (recorded in
tool_calls) and returns drafts whose numbers all enter via bound claims.
None of these functions ever reads scenarios/seeded/ground_truth.yaml —
the tool layer additionally refuses it (defects are found by the checks,
or not at all).

Registry: ROOM_CHECKS[room] = [(handle, check_fn), ...]; check_fn(ctx) ->
list of draft dicts {kind, body, claims, context, session}.

A handle may appear in more than one room's list: PENDING-BATCH2 §13 makes
`@focused`, `@red-team` and `@story` ONE persona each, scheduled in several
rooms with a per-room brief, rather than a persona per room. The agents
table records that as `also_posts_in`; here it is simply a second entry
pointing at that room's check.
"""

from __future__ import annotations

from app.agents.checks.room1 import (holdings, focused, pre_flight_checks,
                                     red_team_opening, vcv)
from app.agents.checks.room2 import (results_validator, run_monitor_summary,
                                     vlad)
from app.agents.checks.room3 import (attrib, credit_desk,
                                     draft_report_review, equity_desk,
                                     focused_room3, lily, pc_desk,
                                     rates_desk, realist, red_team_closing,
                                     warden, wide_eye)
from app.agents.checks.story import story_post

ROOM_CHECKS = {
    1: [
        # §7: runs FIRST — the go/no-go before compute is spent, and before
        # any other agent in the room has an opinion about the inputs.
        # It absorbed @curve-check, which is gone.
        ("@pre-flight-checks", pre_flight_checks),
        ("@vcv", vcv),
        ("@holdings", holdings),
        # opening half of the two-pass cycle (SPEC-APP H.1); reads_from =
        # ["room:1"] puts it topologically last among this room's checks.
        ("@red-team", red_team_opening),
        ("@focused", focused),
        # §10: runs LAST (it reads the room), pinned FIRST.
        ("@story", story_post),
    ],
    2: [
        ("@run-monitor", run_monitor_summary),
        ("@results-validator", results_validator),
        ("@vlad", vlad),
        ("@story", story_post),
    ],
    3: [
        # the research desk's output-room brief: same agent as room 1's
        # @focused, same note, read for what the moves DID to the results
        # (PENDING-BATCH2 §2, §13). It runs before @attrib so the waterfall
        # has the research framing above it in the feed.
        ("@focused", focused_room3),
        ("@attrib", attrib),
        ("@rates-desk", rates_desk),
        ("@credit-desk", credit_desk),
        ("@equity-desk", equity_desk),
        # §9: private credit as a DESK — our sleeve, not the market.
        ("@pc-desk", pc_desk),
        ("@wide-eye", wide_eye),
        ("@realist", realist),
        ("@lily", lily),
        # The draft-report reconciliation is @results-validator's check and
        # posts into room 3 (Output Challenge), where the report lives.
        ("@results-validator", draft_report_review),
        # @warden runs LAST of the analysts — it reads everyone — and is
        # flagged for pinning FIRST (drafts carry pinned=True). Execution
        # order and display order differ deliberately: the pass runs detail
        # -> summary, the feed reads summary -> detail.
        ("@warden", warden),
        # closing half of @red-team's two-pass cycle (SPEC-APP H.1); reads
        # room:1 + room:3 + its own opening post — the last challenge in the
        # cycle. reads_from = ["room:1", "room:3"] puts it topologically
        # last here too.
        ("@red-team", red_team_closing),
        # §10: @story is the very last to run in the room and the first
        # post in the feed. Room 3 therefore reads @story, then @warden —
        # what this month is about, then the numbers behind it, then the
        # detail. Pinned posts order newest-first, so running after @warden
        # is what puts it above @warden.
        ("@story", story_post),
    ],
}
