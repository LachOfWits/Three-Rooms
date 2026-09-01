"""Server-side default-avatar rule (SPEC-APP section 8.1).

Anyone without a custom avatar gets: `glyph` = one or two initials derived
from the name ("Private Credit" -> "PC", single-word names -> first letter),
`bg` = a colour picked at creation from a curated palette of ~12 saturated
hues — random-feeling but assigned once and stored in avatar_json, never
re-rolled on render — and `fg` auto-selected white/near-black by bg
luminance for contrast. Avatars are data (rendered client-side as inline
SVG); this module only decides the stored JSON.
"""

from __future__ import annotations

import hashlib
import json
import re

# Curated palette: 12 saturated hues (purples, teals, ambers, blues, ...).
PALETTE = [
    "#7C3AED",  # purple
    "#0D9488",  # teal
    "#D97706",  # amber
    "#2563EB",  # blue
    "#DB2777",  # pink
    "#059669",  # emerald
    "#E11D48",  # crimson
    "#4F46E5",  # indigo
    "#B45309",  # bronze
    "#0891B2",  # cyan
    "#65A30D",  # lime
    "#9333EA",  # violet
]

FG_LIGHT = "#FFFFFF"   # on dark backgrounds
FG_DARK = "#1F2937"    # near-black, on light backgrounds

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ACCESSORIES = {"none", "horns"}


def _luminance(hex_color: str) -> float:
    """Relative luminance (WCAG) of a #rrggbb colour, 0..1."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def fg_for(bg: str) -> str:
    """White or near-black, whichever contrasts with bg (section 8.1)."""
    return FG_DARK if _luminance(bg) > 0.45 else FG_LIGHT


def initials(name: str | None, handle: str = "") -> str:
    """"Private Credit" -> "PC"; single-word names -> first letter; falls
    back to the handle's first letters when the name is empty."""
    words = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if len(words) == 1:
        return words[0][0].upper()
    tail = re.findall(r"[A-Za-z0-9]", str(handle or ""))
    return "".join(tail[:2]).upper() or "?"


def pick_bg(key: str) -> str:
    """Deterministic 'random-feeling' palette pick, stable for a given
    handle so seeding is idempotent; assigned once at creation and stored."""
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def default_avatar(name: str | None, handle: str) -> dict:
    bg = pick_bg(handle or name or "")
    return {"bg": bg, "fg": fg_for(bg),
            "glyph": initials(name, handle), "accessory": "none"}


def normalize(avatar, name: str | None, handle: str) -> str:
    """Validate/complete a caller-supplied avatar (dict or JSON string);
    fill anything missing or malformed from the default rule. Returns the
    JSON string stored in agents.avatar_json."""
    if isinstance(avatar, str):
        try:
            avatar = json.loads(avatar)
        except json.JSONDecodeError:
            avatar = None
    if not isinstance(avatar, dict):
        avatar = {}
    default = default_avatar(name, handle)
    bg = avatar.get("bg")
    bg = bg if isinstance(bg, str) and _HEX_RE.match(bg) else default["bg"]
    fg = avatar.get("fg")
    fg = fg if isinstance(fg, str) and _HEX_RE.match(fg) else fg_for(bg)
    glyph = avatar.get("glyph")
    if not isinstance(glyph, str) or len(glyph) > 4:
        glyph = default["glyph"]
    accessory = avatar.get("accessory")
    if accessory not in _ACCESSORIES:
        accessory = "none"
    out = {"bg": bg, "fg": fg, "glyph": glyph, "accessory": accessory}
    horn = avatar.get("horn_color")
    if isinstance(horn, str) and _HEX_RE.match(horn):
        out["horn_color"] = horn
    return json.dumps(out)
