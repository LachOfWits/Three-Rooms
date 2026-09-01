"""@story — one post per room, accumulating (PENDING-BATCH2 §10).

A story is not a retrospective summary; it is a thread that opens in room 1
and grows through the cycle:

    room 1   OPENS   what the inputs say this month is about
    room 2   ADDS    what the run did to it
    room 3   CLOSES  the result, and where it may go next

By room 3 each story reads as a full arc — input, process, output, future.
Stories that die are kept and marked closed rather than quietly dropped: a
story that went nowhere is information.

RULES, and they are the point of the agent:
  - It INVENTS NOTHING. Every story is composed only of posts that were
    actually published in the rooms it read, each carried in `sources` so
    provenance chains back through that post's claims to the originating
    tool call.
  - It NEVER writes a number. Connective tissue and a title, nothing else;
    if a figure matters, the post it points at already carries it, bound.
    (The citation gate enforces this from the other side: an unbound
    numeric token would suppress the post.)
  - Titles are A FEW WORDS. "The gilt sell-off." Not sentences.
  - At most FOUR live stories. A month with one says one; a month with
    none says so in a line.

Ordering, as with @warden: runs LAST in each pass (it reads everything in
that room) and is flagged for pinning FIRST. Room 3's feed therefore reads
@story, then @warden, then the detail — what this month is about, then the
numbers behind it.
"""

from __future__ import annotations

from app.agents.tools import ToolError, ToolLimitError

MAX_STORIES = 4          # §10: bounded, deliberately
# "Worth reading" links, room 3 only. §10 allows three to five; the house
# style (§12) caps the whole post at ninety words, and four stories plus
# five footer lines cannot be said in ninety words without saying nothing.
# Three links, each earning its line, is the version that fits.
FOOTER_MIN, FOOTER_MAX = 3, 3

# The handles whose posts a story is built from, per room. @story runs last
# in the pass, so all of them already exist to read.
#
# These lists are sized against MAX_TOOL_CALLS_PER_POST: one read per
# handle, and the room-3 pass reads THREE rooms (4 + 1 + 7 = 12). Adding a
# handle here silently costs the last one its evidence, so the room-2
# carry-forward is deliberately narrowed to the one agent whose findings
# change a story rather than merely narrate it.
ROOM1_SOURCES = ("@pre-flight-checks", "@vcv", "@holdings", "@focused")
ROOM2_SOURCES = ("@run-monitor", "@results-validator", "@vlad")
ROOM2_CARRIED = ("@results-validator",)
ROOM3_SOURCES = ("@attrib", "@rates-desk", "@credit-desk", "@equity-desk",
                 "@pc-desk", "@warden", "@red-team")

# What the inputs say the month is about. @focused's room-1 post names the
# month's material moves by the ASSUMPTIONS FIELD each lands in, one per
# group — so the groups it actually named are the stories the inputs open,
# read off a published post rather than decided here.
_MOVE_GROUPS = (
    ("rates", "curves.", "The rates move", ("@rates-desk",),
     "the curves, and the liabilities discounting on them",
     "the rate block"),
    ("credit", "spreads.", "The credit move",
     ("@credit-desk", "@pc-desk"),
     "the spread levels behind both credit sleeves",
     "the credit block"),
    ("equity_fx", "equity.", "The equity and FX move", ("@equity-desk",),
     "the index levels, which are the marks themselves",
     "the equity and fx blocks"),
)
_EQUITY_FX_ALT = "fx."   # either field opens the same story


class _Story:
    """One live story, and the posts that are the evidence for it."""

    __slots__ = ("key", "title", "opened_by", "sources", "lines", "closed",
                 "desks", "subject", "where")

    def __init__(self, key, title, subject="", where=""):
        self.key, self.title, self.subject = key, title, subject
        self.where = where          # where the run carries it, for room 2
        self.opened_by: list[str] = []
        self.sources: list[int] = []
        self.lines: dict[int, str] = {}
        self.closed = False
        self.desks: list[str] = []


def _read(s, room: int, handle: str):
    """Published posts by one agent in one room for this run, or [] — a
    handle that does not exist, or a budget that has run out, must not sink
    the pass."""
    try:
        _tc, res = s.call("read_agent_posts", room=room, handle=handle)
    except (ToolError, ToolLimitError):
        return []
    return res.get("posts") or []


def _origin_body(posts) -> str:
    for p in posts:
        if p.get("type") == "origin":
            return p.get("body_md") or ""
    return posts[0].get("body_md", "") if posts else ""


def _verdict_of(body: str) -> str | None:
    for v in ("DO NOT RUN", "RUN WITH CONCERNS", "CLEAR TO RUN"):
        if v in body:
            return v
    return None


def _gather(s, ctx) -> dict:
    """Read the rooms this pass can see: room -> {handle: published posts}.

    Room 1 is always read (it is where every story opens); the current
    room, and the room-2 carry-forward, are added as the cycle reaches
    them."""
    seen: dict[int, dict] = {1: {}}
    for h in ROOM1_SOURCES:
        seen[1][h] = _read(s, 1, h)
    if ctx.room >= 2:
        carried = ROOM2_SOURCES if ctx.room == 2 else ROOM2_CARRIED
        seen[2] = {h: _read(s, 2, h) for h in carried}
    if ctx.room >= 3:
        seen[3] = {}
        for h in ROOM3_SOURCES:
            seen[3][h] = _read(s, 3, h)
    return seen


def _open_stories(seen: dict) -> list[_Story]:
    """The stories room 1's published posts open. Priority order, capped at
    four — a flagged input outranks a market move, because an input nobody
    trusts makes every other story provisional."""
    room1 = seen.get(1, {})
    stories: list[_Story] = []

    flag_handles = [h for h, posts in room1.items()
                    if any("**FLAG" in (p.get("body_md") or "")
                           for p in posts)]
    verdict_posts = room1.get("@pre-flight-checks") or []
    verdict = _verdict_of(_origin_body(verdict_posts))
    if flag_handles or verdict in ("DO NOT RUN", "RUN WITH CONCERNS"):
        st = _Story("flagged_input", "A flagged input",
                    "an input the room does not trust")
        st.opened_by = flag_handles or ["@pre-flight-checks"]
        for h in st.opened_by:
            st.sources.extend(p["id"] for p in room1.get(h, []))
        st.sources.extend(p["id"] for p in verdict_posts)
        if verdict == "DO NOT RUN":
            st.lines[1] = ("DO NOT RUN, item named. Nothing is blocked; "
                           "everything below it is provisional.")
        elif flag_handles:
            st.lines[1] = (", ".join(flag_handles) + " flagged an input. "
                           "Not blocked; the finding stands.")
        else:
            st.lines[1] = ("Cleared to run, concerns logged. Not blocking, "
                           "and not nothing.")
        stories.append(st)

    focused_body = _origin_body(room1.get("@focused") or [])
    for key, field, title, desks, subject, where in _MOVE_GROUPS:
        hit = field in focused_body or (
            key == "equity_fx" and _EQUITY_FX_ALT in focused_body)
        if not hit:
            continue
        st = _Story(key, title, subject, where)
        st.opened_by = ["@focused"]
        st.desks = list(desks)
        st.sources.extend(p["id"] for p in room1.get("@focused", []))
        st.lines[1] = f"@focused named it, pointing at {subject}."
        stories.append(st)

    return stories[:MAX_STORIES]


def _add_room2(seen: dict, stories: list[_Story]) -> None:
    """What the run did to each story. Room 2 is process, so the addition is
    about the run, never a fresh view of the market."""
    room2 = seen.get(2, {})
    validator = room2.get("@results-validator") or []
    vlad = room2.get("@vlad") or []
    monitor = room2.get("@run-monitor") or []
    findings = [p for p in validator
                if (p.get("significance") in ("notable", "critical")
                    or "Finding" in (p.get("body_md") or ""))]
    failed_stage = any("failed" in (p.get("body_md") or "").lower()
                       for p in monitor)

    for st in stories:
        st.sources.extend(p["id"] for p in validator)
        if st.key == "flagged_input":
            st.lines[2] = ("Ran with the flag standing; @results-validator "
                           + ("adds a finding of its own."
                              if findings else "found nothing further."))
            continue
        st.sources.extend(p["id"] for p in vlad)
        where = st.where or "the run"
        if failed_stage:
            st.lines[2] = (f"A stage failed, so {where} rests on a partial "
                           "pass — see @run-monitor.")
        elif findings:
            st.lines[2] = (f"Priced into {where}; @results-validator "
                           "returned a finding, @vlad reconciles.")
        else:
            st.lines[2] = (f"Priced into {where} cleanly; "
                           "@results-validator and @vlad agree.")


def _add_room3(seen: dict, stories: list[_Story]) -> None:
    """Close each story and look forward. A story nothing came of is marked
    CLOSED and kept — dropping it would hide the fact that it went
    nowhere."""
    room3 = seen.get(3, {})
    room1 = seen.get(1, {})
    warden = room3.get("@warden") or []
    attrib = room3.get("@attrib") or []
    closing_challenge = room3.get("@red-team") or []
    still_flagged = any("**FLAG" in (p.get("body_md") or "")
                        for posts in room1.values() for p in posts)

    for st in stories:
        if st.key == "flagged_input":
            st.sources.extend(p["id"] for p in closing_challenge)
            st.sources.extend(p["id"] for p in warden)
            if still_flagged:
                st.lines[3] = ("Still open at the output stage; @red-team "
                               "calls it a process gap.")
            else:
                st.closed = True
                st.lines[3] = ("CLOSED — nothing in the output stage "
                               "sustains it.")
            continue

        covered = []
        for handle in st.desks:
            posts = room3.get(handle) or []
            if posts:
                covered.append(handle)
                st.sources.extend(p["id"] for p in posts)
        st.sources.extend(p["id"] for p in attrib)
        watch = any("**Watch**" in (p.get("body_md") or "")
                    for h in st.desks for p in (room3.get(h) or []))
        if not covered:
            st.closed = True
            st.lines[3] = ("CLOSED — no desk carried it; it went nowhere, "
                           "which is worth knowing.")
        elif watch:
            st.lines[3] = (covered[0] + " tied it to the block VaR and left "
                           "a watch item — not finished.")
        else:
            st.lines[3] = (covered[0] + " tied it to the block VaR; @attrib "
                           "and @warden carried it. Closed out.")


def _worth_reading(seen: dict) -> list[tuple[str, str]]:
    """Room 3 only: three to five posts a human should actually read, each
    with one line on why. Only handles that actually posted are offered —
    a link to a post nobody wrote is worse than a shorter list."""
    room1, room3 = seen.get(1, {}), seen.get(3, {})
    candidates = [
        ("@warden", room3, "AuM, flows and risk carried"),
        ("@pre-flight-checks", room1, "the go/no-go on the inputs"),
        ("@attrib", room3, "the waterfall, step by step"),
        ("@red-team", room3, "what this cycle still misses"),
        ("@vcv", room1, "the vols, the correlations, the matrix"),
        ("@lily", room3, "the liability side and the duration gap"),
    ]
    out = []
    for handle, room, why in candidates:
        if room.get(handle):
            out.append((handle, why))
        if len(out) >= FOOTER_MAX:
            break
    return out


def story_post(ctx) -> list[dict]:
    """@story's post for whichever room this pass is running.

    Named `story_post`, not `story`: `from ...checks.story import story`
    would rebind the package attribute `checks.story` from the MODULE to
    the function, and anything importing the module afterwards would get a
    function instead.

    One post per room; the same stories, carrying whatever is known so far.
    Runs LAST in the pass and is flagged for pinning FIRST."""
    s = ctx.session()
    seen = _gather(s, ctx)
    stories = _open_stories(seen)
    if ctx.room >= 2:
        _add_room2(seen, stories)
    if ctx.room >= 3:
        _add_room3(seen, stories)

    verb = {1: "opening", 2: "so far", 3: "closing"}.get(ctx.room, "so far")
    parts = [f"**This month, {verb}.**"]
    if not stories:
        parts.append(" No story yet: nothing in this room's posts opens "
                     "one.")
    else:
        for st in stories:
            line = st.lines.get(ctx.room) or st.lines.get(1) or ""
            mark = " *(closed)*" if st.closed else ""
            parts.append(f"\n**{st.title}.**{mark} {line}")

    sources: list[int] = []
    for st in stories:
        for pid in st.sources:
            if pid not in sources:
                sources.append(pid)

    if ctx.room == 3:
        links = _worth_reading(seen)
        if links:
            parts.append("\n**Worth reading.**")
            for handle, why in links:
                parts.append(f"\n- {handle} — {why}.")

    live = [st for st in stories if not st.closed]
    significance = "notable" if any(st.key == "flagged_input" for st in live) \
        else ("routine" if stories else "quiet")

    return [{
        "kind": "origin",
        "body": "".join(parts),
        "claims": [],          # §10: connective tissue and a title, never a
                               # number — there is nothing here to bind
        "context": False,
        "session": s,
        "significance": significance,
        "sources": sources,
        "pinned": True,        # displayed FIRST; executed LAST
    }]
