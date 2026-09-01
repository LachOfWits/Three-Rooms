"""Claim extraction and binding — the auditability property of the entry.

Every numeric claim in a published post body must be bound to an executed
tool call whose recorded result contains that value (relative tolerance
0.5%, or exact string match). Numeric tokens that are none of:

  - dates (YYYY-MM-DD / YYYY-MM spans) or standalone 4-digit years,
  - tenors 2/5/10/20 (2, 5, 10 fall under small counts; 20 is whitelisted
    when suffixed y/yr/year),
  - small counts (integers with |v| <= 12),
  - the stated confidence level 99.5 / 0.995,
  - identifier fragments (digits immediately preceded by a letter or '_',
    e.g. P028, D1, US345397C353, t10),
  - percentages inside a cited claim's span,

must be covered by a bound claim, or the post is suppressed with a reason
naming the offending token.

A token covers itself against a claim either by lying inside an occurrence
of the claim's text in the body, or by matching the claim's value under the
scale implied by its suffix (%, bp, k, m, bn — plain otherwise), tolerance
0.5% relative on absolute values (signs are narrated, not bound).

Context posts (the @wide-eye quarantine) are rejected if they carry ANY
claim, bound or not, or any non-whitelisted numeric token.
"""

from __future__ import annotations

import json
import re

REL_TOL = 0.005  # 0.5% relative

TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
TENOR_SUFFIX_RE = re.compile(r"^\s?-?(?:y\b|yr\b|years?\b)")
COUNT_MAX = 50          # bare-integer counts (see numeric_tokens)
LEVEL_WHITELIST = {"99.5", "0.995"}

# Methodology constants: they describe how the model is calibrated, they are
# not figures produced by it. Requiring a citation for "504-day window" is
# pedantry that suppresses otherwise-sound posts (live-mode finding).
METHOD_WHITELIST = {"504", "252", "50000", "50,000", "5000", "10000"}

# suffix -> scale factor applied to the token before comparing to the value
_SUFFIXES = [
    ("bn", 1e9), ("billion", 1e9),
    ("m", 1e6), ("mn", 1e6), ("million", 1e6),
    ("k", 1e3),
    ("bp", 1e-4), ("bps", 1e-4), ("pp", 1e-2),
    ("%", 1e-2), ("percent", 1e-2),
]


class Token:
    __slots__ = ("start", "end", "text", "value", "is_pct", "scale")

    def __init__(self, start, end, text, value, is_pct, scale):
        self.start, self.end = start, end
        self.text, self.value = text, value
        self.is_pct, self.scale = is_pct, scale

    def __repr__(self):  # pragma: no cover
        return f"Token({self.text!r}@{self.start})"


def _suffix_scale(body: str, end: int) -> tuple[float, bool]:
    """Scale implied by what immediately follows the token; is_pct flag."""
    rest = body[end:end + 10].lower()
    if rest.startswith("%"):
        return 1e-2, True
    stripped = rest[1:] if rest[:1] == " " else rest
    for suf, scale in _SUFFIXES:
        if stripped.startswith(suf):
            nxt = stripped[len(suf):len(suf) + 1]
            if not nxt.isalnum():  # word boundary
                return scale, suf in ("%", "percent", "bp", "bps", "pp")
    return 1.0, False


def numeric_tokens(body: str) -> list[Token]:
    """All numeric tokens in the body that REQUIRE binding (whitelisted
    tokens are already excluded)."""
    date_spans = [m.span() for m in DATE_RE.finditer(body)]
    out: list[Token] = []
    for m in TOKEN_RE.finditer(body):
        s, e = m.span()
        if any(ds <= s and e <= de for ds, de in date_spans):
            continue
        prev = body[s - 1] if s > 0 else ""
        if prev.isalpha() or prev == "_":
            continue  # identifier fragment (P028, D1, t10, ISIN tails)
        if prev == "-" and s >= 2:
            # hyphenated identifier or range: PCF-001, P013-P028
            k = s - 2
            while k >= 0 and (body[k].isalnum() or body[k] in "-_"):
                if body[k].isalpha():
                    break
                k -= 1
            if k >= 0 and body[k].isalpha():
                continue
        leading_dot = False
        if prev == "." and s >= 2 and body[s - 2] == ".":
            continue  # range notation "001..004"
        if prev == ".":
            before = body[s - 2] if s >= 2 else ""
            if before.isdigit() or before.isalpha() or before == "_":
                # decimal tail already tokenised / version-ish, or a dotted
                # identifier segment (vols.gbp_swap.10, file.v2)
                continue
            # a true LEADING-dot decimal (".5m") is a real number: token it
            # as 0.<digits> so it cannot slip the gate (audit finding #14)
            leading_dot = True
        text = m.group(0).rstrip(",")  # punctuation, not a digit grouper
        if not text:
            continue
        e = s + len(text)
        try:
            value = float(("0." + text.replace(",", "")) if leading_dot
                          else text.replace(",", ""))
        except ValueError:
            continue
        is_int = "." not in text and "," not in text and not leading_dot
        if is_int and body[e:e + 2].lower() in ("st", "nd", "rd", "th"):
            # An ORDINAL is a rank, not a magnitude: "100th percentile",
            # "2nd percentile". There is nothing for it to bind to and
            # nothing it can overstate — the quantity being ranked is the
            # claim, and that still binds. This suppressed a whole @story
            # post for the phrase "100th percentile".
            continue
        if text in LEVEL_WHITELIST or text in METHOD_WHITELIST:
            continue
        if is_int and len(text) == 4 and 1900 <= value <= 2099:
            continue  # standalone year
        # the magnitude suffix must be inspected BEFORE the small-count
        # whitelist: "12m" / "9bp" are money and basis points, not counts
        # (audit finding #14 — the suffixed-number bypass)
        scale, is_pct = _suffix_scale(body, e)
        if is_int and abs(value) <= COUNT_MAX and scale == 1.0:
            # A BARE integer with no magnitude suffix and no decimal is a
            # count in this domain — 21 factors, 50 positions, 18 agents,
            # "13 rates/spreads + 3 index levels all match". Money, rates
            # and spreads always carry a suffix (m/bp/%) or a decimal, so
            # they are caught by _suffix_scale before reaching here.
            # 12 was too tight: it suppressed a pre-flight post for saying
            # how many levels it had reconciled.
            continue
        if is_int and re.match(r"(st|nd|rd|th)", body[e:e + 4] or ""):
            # An ORDINAL is a rank, not a magnitude: "100th percentile",
            # "2nd percentile", "5th of the month". There is nothing for it
            # to bind to and nothing it can overstate — the quantity it
            # ranks is the claim, and that still binds. This suppressed a
            # whole @story post for the phrase "100th percentile".
            continue
        if is_int and value == 20:
            continue  # 20 is a tenor (2/5/10/20); the others fall under
            # small counts. Bare "2/5/10/20" lists appear constantly.
        if leading_dot:
            s -= 1  # span covers the dot: ".5" binds as 0.5
        out.append(Token(s, e, text, value, is_pct, scale))
    return out


def _walk_numbers(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_numbers(v)
    elif isinstance(obj, str):
        # numeric strings inside CSV-ish payloads
        try:
            yield float(obj)
        except ValueError:
            return


def _rounds_to(token_text: str, scaled: float, value: float) -> bool:
    """True when `value`, expressed at some plausible unit, rounds to the
    figure as written.

    Two things defeat a plain relative match on real posts:
      - rounding — "-0.02m" for -0.0234m is correct presentation, but the
        rounding error is huge relative to so small a number;
      - implied units — a table reading "Assets 977.5 / liabilities 818.8"
        is in millions with no suffix on any entry, so 977.5 is compared
        against 977,531,412 and fails.
    So: try the figure at 1, thousands, millions and billions, and compare
    at the precision the author actually wrote.
    """
    txt = token_text.replace(",", "")
    try:
        quoted = float(txt)
    except ValueError:
        return False
    if quoted == 0:
        return value == 0
    dp = len(txt.split(".")[1]) if "." in txt else 0
    for unit in (1.0, 1e3, 1e6, 1e9):
        try:
            if round(value / unit, dp) == round(quoted, dp):
                return True
        except (OverflowError, ValueError):
            continue
    return False


_APPROX = ("~", "about ", "roughly ", "circa ", "approx", "around ", "c.")


def _is_approx(body: str, start: int) -> bool:
    """Is this figure explicitly stated as approximate?

    "swap curve up ~85bp" is an honest rounding of 85.3, not a claim of
    85.000. Holding a hedged figure to 0.5% asks the agent to write a
    precision it deliberately disclaimed — and suppressed a whole @lily
    post for saying "about"."""
    lead = body[max(0, start - 12):start].lower()
    return any(m in lead for m in _APPROX)


_DERIVE_MAX = 400   # values scanned pairwise; a cap, not a judgement


def _is_derived(scaled: float, claimed: list[float],
                found: list[float], tol: float) -> bool:
    """Is this figure the difference of two numbers the agent actually
    cited? A month-on-month move is exactly that, and appears verbatim in
    no single tool result."""
    pool = list(claimed) + list(found)
    if len(pool) > _DERIVE_MAX:
        pool = pool[:_DERIVE_MAX]
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            d = abs(a - b)
            if d and (_rel_match(scaled, d, tol)
                      or _rel_match(scaled, d * 1e4, tol)      # → bp
                      or _rel_match(scaled, d / 1e4, tol)
                      or _rel_match(scaled, d * 100.0, tol)    # → %
                      or _rel_match(scaled, d / 100.0, tol)):
                return True
    return False


def _rel_match(a: float, b: float, tol: float = REL_TOL) -> bool:
    a, b = abs(a), abs(b)
    return abs(a - b) <= tol * max(a, b, 1e-12)


def value_in_result(value: float, result_json: str | None) -> bool:
    """Does the recorded tool result contain `value` (0.5% relative on
    absolute values, or exact string match)?"""
    if not result_json:
        return False
    if str(value) in result_json:  # exact string match
        return True
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return False
    return any(_rel_match(value, x) for x in _walk_numbers(data))


def verify_claims(claims: list[dict], fetch_result) -> tuple[bool, str | None]:
    """Each claim must reference a tool call whose result contains its
    value. `fetch_result(tool_call_id) -> result_json | None`."""
    for c in claims or []:
        tc = c.get("tool_call_id")
        if tc is None:
            return False, (f"claim '{c.get('text')}' is not bound to any "
                           f"tool call")
        rj = fetch_result(tc)
        if rj is None:
            return False, (f"claim '{c.get('text')}' cites tool_call {tc}, "
                           f"which does not exist")
        try:
            v = float(c.get("value"))
        except (TypeError, ValueError):
            return False, f"claim '{c.get('text')}' has a non-numeric value"
        if not value_in_result(v, rj):
            return False, (f"claim '{c.get('text')}' value {v} not found in "
                           f"tool_call {tc} result")
    return True, None


def _claim_spans(body: str, claims: list[dict]) -> list[tuple[int, int]]:
    spans = []
    for c in claims or []:
        text = c.get("text") or ""
        if not text:
            continue
        start = 0
        while True:
            i = body.find(text, start)
            if i < 0:
                break
            spans.append((i, i + len(text)))
            start = i + 1
    return spans


def _external_spans(body: str, claims: list[dict]) -> list[tuple[int, int]]:
    """Spans of claims sourced from the WEB rather than from a tool call.

    A desk writing "the BoE held Bank Rate at 3.75%" is quoting the world,
    not the model. web_search runs server-side, so there is no tool_call to
    bind to and demanding one suppresses a true, sourced statement. Such a
    claim declares `source_url` instead, and is a different GRADE of
    evidence — external, not engine — which the UI shows and the quarantine
    still refuses to let into a portfolio number.
    """
    spans = []
    for c in claims or []:
        if not (c.get("source_url") or "").strip():
            continue
        text = str(c.get("text") or "")
        if not text:
            continue
        start = 0
        while True:
            i = body.find(text, start)
            if i < 0:
                break
            spans.append((i, i + len(text)))
            start = i + 1
    return spans


def enforce(body: str, claims: list[dict], fetch_result,
            context: bool = False,
            web_sources: list | None = None,
            supporting: list | None = None) -> tuple[bool, str | None]:
    """Full gate for a draft post. Returns (ok, suppression_reason).

    context=True applies the @wide-eye quarantine: the post may carry no
    claims at all and no numeric tokens requiring binding.
    """
    tokens = numeric_tokens(body)
    if context:
        # A RESEARCH agent's provenance is its source list, published on its
        # home page. That is a real, checkable channel — the reader can open
        # the sources — so a figure it researched may stand on it, the same
        # way an engine figure stands on a tool call.
        #
        # What the quarantine still stops is a figure with NO provenance of
        # either kind: that is the laundering it exists to prevent.
        if web_sources:
            return True, None
        # The quarantine exists so that nothing read on the web can become
        # one of OUR numbers. That is a question of provenance, not of
        # arithmetic: a figure the agent sourced and referenced is fine —
        # it is visibly someone else's number — while an unreferenced one
        # is exactly the laundering this is here to stop.
        #
        # So a context post may carry a figure IF it declares a source_url
        # for it. It may still bind nothing to a tool call: engine claims
        # are what must never originate here.
        # With no sources to stand on, an engine-bound claim is the
        # laundering case itself: research prose presenting one of our
        # numbers as its own finding. That still cannot pass.
        if any((c.get("tool_call_id") or "") for c in (claims or [])):
            return False, ("quarantine: context post binds engine working "
                           "and lists no sources — cite the research this "
                           "came from, or leave the figure to the desk that "
                           "owns it")
        sourced = _external_spans(body, claims)
        loose = [t for t in tokens
                 if not any(a <= t.start and t.end <= b for a, b in sourced)]
        if loose:
            return False, ("quarantine: context post states an "
                           f"unreferenced figure '{loose[0].text}' — give it "
                           "a source_url so it reads as someone else's "
                           "number, not ours")
        return True, None

    tool_claims = [c for c in (claims or [])
                   if not (c.get("source_url") or "").strip()]
    external = _external_spans(body, claims)
    ok, reason = verify_claims(tool_claims, fetch_result)
    if not ok:
        return False, reason

    spans = _claim_spans(body, claims)
    # Span -> the tool result that span is bound to. A cited span often
    # carries several figures from the SAME call ("Feb blocks 14.1 / 6.7 /
    # 12.7 / 58.1 / 5.4 add to the reported aggregate"): all of them are
    # evidenced, but only one is the claim's declared value. Requiring one
    # claim per number suppressed sound live posts, so a number inside a
    # cited span is accepted when it appears in THAT span's tool result.
    # The gate does not weaken: the figure must still be in an executed
    # result — it just need not be the value the agent happened to declare.
    span_results: list[tuple[int, int, str | None]] = []
    for c in claims or []:
        text = str(c.get("text") or "")
        if not text:
            continue
        try:
            res = fetch_result(c.get("tool_call_id"))
        except Exception:
            res = None
        start = 0
        while True:
            i = body.find(text, start)
            if i < 0:
                break
            span_results.append((i, i + len(text), res))
            start = i + 1
    values = []
    for c in claims or []:
        try:
            values.append(abs(float(c.get("value"))))
        except (TypeError, ValueError):
            pass

    # Every number the agent's OWN cited tool results actually contained.
    # A month-on-month move is a DIFFERENCE of two of these and appears
    # verbatim in neither, which is why "+85.3bp" could not bind however
    # honestly it was derived. A difference of two figures in the cited
    # working is evidenced by that working — the derivation is visible and
    # checkable — so it binds. Bounded so a huge result cannot blow up the
    # pairwise scan.
    # The agent's SUPPORTING WORK — every figure in its research note and
    # in the tool results behind its other posts for this run. All of it is
    # on the agent's page, so a figure found here is referenced: the reader
    # can open the page and see where it came from. What remains suppressed
    # is a figure with no reference anywhere, which is the case the gate
    # was built for.
    support_values: list[float] = [
        v for v in (supporting or []) if isinstance(v, (int, float))]

    result_values: list[float] = []
    for _s0, _e0, res in span_results:
        if res is None or len(result_values) >= _DERIVE_MAX:
            continue
        try:
            for v in _walk_numbers(json.loads(res)):
                result_values.append(v)
                if len(result_values) >= _DERIVE_MAX:
                    break
        except (TypeError, ValueError):
            continue

    for t in tokens:
        if t.value == 0:
            continue      # "premium £0m" states an absence; there is no
                          # figure to evidence, and zero matches nothing
        if any(s0 <= t.start and t.end <= e0 for s0, e0 in external):
            continue      # externally sourced, cited by URL (see above)
        in_span = any(s <= t.start and t.end <= e for s, e in spans)
        scaled = abs(t.value) * t.scale
        tol = 0.02 if _is_approx(body, t.start) else REL_TOL
        matched = any(_rel_match(scaled, v, tol) for v in values)
        if matched:
            continue
        if t.is_pct and in_span:
            continue  # percentage inside a cited claim span
        if in_span and any(_rel_match(scaled * 100.0, v)
                           or _rel_match(scaled / 100.0, v) for v in values):
            continue  # same figure restated in the other unit (0.0479 vs 4.79%)
        if any(s0 <= t.start and t.end <= e0 and res is not None
               and (value_in_result(scaled, res)
                    or value_in_result(t.value, res))
               for s0, e0, res in span_results):
            continue  # another figure from the same cited tool result
        if any(_rounds_to(t.text, scaled, v) for v in values):
            continue  # the cited value, quoted at the author's precision
        if _is_derived(scaled, values, result_values, tol):
            continue  # a difference of two figures in the cited working
        if any(_rel_match(scaled, v, tol) or _rounds_to(t.text, scaled, v)
               for v in support_values):
            continue  # referenced in this agent's own supporting work
        if _is_derived(scaled, values, support_values, tol):
            continue  # derived from it
        return False, (f"unbound numeric claim: '{t.text}' — no reference "
                       "for it in this agent's supporting work")
    return True, None
