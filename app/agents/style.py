"""Number formatting and the house-style contract.

No API calls anywhere in this module, and no prose: the templated
replies that used to live here are gone from the project. The checks
(app/agents/checks) run
real tool calls through the ToolSession — recorded for real — and hand back
structured drafts built with the Prose helper below, so every number in a
body enters through a claim bound to a tool call. Citation binding and
suppression run identically to live mode (in api.publish_drafts).

HOUSE STYLE (PENDING-BATCH2 §12). Every post must hold the same SHAPE. The brevity contract that persona prompts bind on the live path is
measured here, in code, so `tests/test_brevity.py` can fail a template that
drifts back into an essay:

    lead line   one plain sentence, <= 25 words
    bullets     2-5 fragments (a "nothing material" post is ONE line)
    ceiling     90 words for the whole feed post

Depth — method, working, the full limitation list, caveats — belongs on the
BACKING PAGE, which in mock is the `expansion` draft published under the
origin (api.publish_drafts) and in live is `detail_md`. Length is welcome
there; it is not welcome in the feed.
"""

from __future__ import annotations

import re


# --------------------------------------------------------------------------
# formatting helpers — enough significant digits to stay inside the 0.5%
# citation tolerance
# --------------------------------------------------------------------------

def money(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}£{a / 1e9:,.3f}bn"
    if a >= 1e6:
        return f"{sign}£{a / 1e6:,.2f}m"
    if a >= 1e4:
        return f"{sign}£{a / 1e3:,.1f}k"
    if a >= 1e3:
        # 2dp below 10k: 1dp would give only 2 significant digits, breaching
        # the 0.5% citation tolerance (e.g. 1465 -> "1.5k" is 2.4% off)
        return f"{sign}£{a / 1e3:,.2f}k"
    return f"{sign}£{a:,.2f}"


def signed_money(v: float) -> str:
    return ("+" if v >= 0 else "") + money(v)


def pc(v: float, dp: int = 2) -> str:
    """0.0673 -> '6.73%' (input is a decimal fraction)."""
    return f"{v * 100:.{dp}f}%"


def pc4(v: float) -> str:
    """vols: 0.007316 -> '0.7316%'."""
    return pc(v, 4)


def bp(v: float, dp: int = 1) -> str:
    """0.008528 -> '85.3bp'."""
    return f"{v * 1e4:.{dp}f}bp"


def signed_bp(v: float, dp: int = 1) -> str:
    return ("+" if v >= 0 else "-") + bp(abs(v), dp)


def num(v: float, dp: int = 4) -> str:
    return f"{v:,.{dp}f}"


class Prose:
    """Assemble a post body where numbers only enter via claims."""

    def __init__(self):
        self._parts: list[str] = []
        self.claims: list[dict] = []

    def add(self, text: str) -> "Prose":
        self._parts.append(text)
        return self

    def lead(self, text: str) -> "Prose":
        """Put the lead line in FRONT of everything already written.

        House style says the post opens with one plain sentence saying what
        was found — but a check often cannot write that sentence until it
        has run every branch (how many findings? which verdict?). Building
        the bullets first and leading afterwards keeps the sentence honest
        instead of hedged. Carries no claim: a lead assembled after the
        fact must not smuggle in an unbound figure."""
        self._parts.insert(0, text)
        return self

    def claim(self, value: float, tool_call_id: int, fmt=None,
              text: str | None = None) -> "Prose":
        s = text if text is not None else (fmt or str)(value)
        self._parts.append(s)
        self.claims.append({"text": s, "value": float(value),
                            "tool_call_id": int(tool_call_id)})
        return self

    def body(self) -> str:
        return "".join(self._parts)

    def draft(self, kind: str = "origin", session=None,
              context: bool = False, significance: str = "routine",
              sources: list[int] | None = None) -> dict:
        return {"kind": kind, "body": self.body(), "claims": list(self.claims),
                "context": context, "session": session,
                "significance": significance, "sources": sources or []}


# --------------------------------------------------------------------------
# the house style, measured (PENDING-BATCH2 §12)
#
# These are the definitions tests/test_brevity.py enforces. They live here,
# next to the templates, so there is exactly ONE definition of "90 words"
# and a template author can measure a draft without guessing.
# --------------------------------------------------------------------------

FEED_WORD_CEILING = 90      # hard ceiling on a published feed post
FEED_MAX_BULLETS = 5        # 2-5 bullets; a quiet post has none
LEAD_WORD_CEILING = 25      # the one plain sentence that opens it

# The tells banned on the live path, banned here too.
BANNED_PHRASES = ("Independent cross-check", "It is worth noting",
                  "Importantly", "In summary")

_MD_NOISE = re.compile(r"[*`_>#|]+")
_BULLET = re.compile(r"^\s*[-*+]\s+")


def _plain(text: str) -> str:
    """Markdown emphasis, code ticks and table pipes are not words."""
    return _MD_NOISE.sub(" ", text or "")


def word_count(text: str) -> int:
    """Words in a post body. A token counts only if it carries a letter or
    a digit, so em dashes, bullets and middots are punctuation rather than
    padding a template could hide behind."""
    return sum(1 for tok in _plain(text).split()
               if any(ch.isalnum() for ch in tok))


def lead_line(body: str) -> str:
    """The first non-empty line — the sentence saying what was found."""
    for line in (body or "").splitlines():
        if line.strip():
            return line
    return ""


def bullet_lines(body: str) -> list[str]:
    return [ln for ln in (body or "").splitlines() if _BULLET.match(ln)]


def banned_phrases_in(text: str) -> list[str]:
    low = (text or "").lower()
    return [p for p in BANNED_PHRASES if p.lower() in low]


def shape(body: str) -> dict:
    """Everything the brevity contract measures, in one call."""
    return {"words": word_count(body),
            "bullets": len(bullet_lines(body)),
            "lead_words": word_count(lead_line(body)),
            "banned": banned_phrases_in(body)}


def house_style_breaches(body: str) -> list[str]:
    """Human-readable reasons this body breaks the house style — empty when
    it obeys it."""
    sh = shape(body)
    out = []
    if sh["words"] > FEED_WORD_CEILING:
        out.append(f"{sh['words']} words (ceiling {FEED_WORD_CEILING})")
    if sh["bullets"] > FEED_MAX_BULLETS:
        out.append(f"{sh['bullets']} bullets (max {FEED_MAX_BULLETS})")
    if sh["lead_words"] > LEAD_WORD_CEILING:
        out.append(f"lead line {sh['lead_words']} words "
                   f"(max {LEAD_WORD_CEILING})")
    for p in sh["banned"]:
        out.append(f"banned phrase {p!r}")
    return out


def context_draft(body: str, session=None,
                  significance: str = "routine") -> dict:
    """A quarantined context draft (no claims by construction; the citation
    layer still enforces it)."""
    return {"kind": "origin", "body": body, "claims": [], "context": True,
            "session": session, "significance": significance, "sources": []}
