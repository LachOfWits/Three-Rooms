"""Regenerate AGENT-PROMPTS.md from the shipped roster.

The doc is held to the roster by tests/test_batch2_roster.py, so it must be
regenerated whenever a persona prompt, handle or grant changes:

    .venv/Scripts/python scripts/gen_agent_prompts.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config                      # noqa: E402
from app.agents import personas             # noqa: E402
# Retired handles live with the migration that retires them.
from app.agents.personas import RETIRED_HANDLES  # noqa: E402

RETIRED = tuple(RETIRED_HANDLES)

ROOM = {1: "Room 1 · inputs", 2: "Room 2 · execution", 3: "Room 3 · outputs"}


def main() -> int:
    web = personas.WEB_SEARCH_HANDLES
    out = [
        "# Agent prompts",
        "",
        "Every persona prompt as shipped. These seed the `agents` table on a",
        "fresh database and are editable per-agent in the app — this file is",
        "the source of the defaults, not a live mirror of your database.",
        "",
        "**Generated** by `scripts/gen_agent_prompts.py`; a test holds it to",
        "the roster, so regenerate it after any prompt or handle change.",
        "",
        "## How a prompt is assembled",
        "",
        "Blocks concatenate, and only the first is per-agent:",
        "",
        "1. **The persona prompt** below — voice, remit, favoured tools.",
        "2. **`_CITE`** — every numeric figure must bind to a tool call or the",
        "   post is suppressed rather than published.",
        "3. **`_STYLE`** — feed post is one lead line plus 2-5 bullets, 90",
        "   words max; method and working go to `detail_md`.",
        "4. **`_RESEARCH_DEPTH`** — web-enabled agents only: search broadly,",
        "   fetch and read the articles, target ~20 distinct sources.",
        "",
        "## Model",
        "",
        "`claude-opus-5` at **`low`** effort (`.env`). Low is deliberate:",
        "these are short, well-scoped analyses, and higher effort produced",
        "posts several times longer than a feed can carry.",
        "",
        "## Tools",
        "",
        "Agents reach the world only through the registry — run outputs,",
        "assumptions, the book, raw market series, vol recomputation,",
        "scenario drill-down, stress and reverse stress, plus `list_files` /",
        "`read_file` across the model folders.",
        "",
        "**Web search and fetch** (Anthropic server-side) go only to the",
        "outward-looking agents: " + ", ".join(f"`{h}`" for h in sorted(web)) + ".",
        "Verifiers work solely from our own data — a verifier that reads the",
        "internet is no longer an independent check.",
        "",
        "## Retired handles",
        "",
        "| Handle | Fate |",
        "|---|---|",
    ]
    fates = {
        "@curve-check": "absorbed by `@pre-flight-checks`, which owns input "
                        "validation end to end",
        "@vcv-sentinel": "renamed `@vcv`; history follows the row",
        "@focused-book": "merged into `@focused`, which posts in rooms 1 and 3",
    }
    for h in RETIRED:
        out.append(f"| `{h}` | {fates.get(h, 'retired')} |")
    out.append("")

    for r in (1, 2, 3):
        out += [f"## {ROOM[r]}", ""]
        for p in personas.BUILTINS:
            # One section per agent, filed under its HOME room; the rooms it
            # also posts in are named in the meta line. Filing by every room
            # would duplicate @focused, @red-team and @story.
            if p["room"] != r:
                continue
            rooms = [p["room"]] + list(p.get("also_posts_in") or [])
            out += [f"### `{p['handle']}` — {p['name']}", "",
                    f"**Focus (shown in the @-mention dropdown):** {p['focus']}",
                    ""]
            meta = []
            if p.get("outlook"):
                meta.append(f"outlook `{p['outlook']}`")
            if len(rooms) > 1:
                meta.append("posts in rooms " +
                            ", ".join(str(x) for x in sorted(rooms)))
            if p["handle"] in web:
                meta.append("**web search + fetch**")
            if p.get("reads_from"):
                meta.append("reads " + ", ".join(f"`{x}`" for x in p["reads_from"]))
            if meta:
                out += ["*" + " · ".join(meta) + "*", ""]
            out += ["```", p["persona_prompt"].strip(), "```", ""]
            for room_no, brief in sorted((p.get("room_briefs") or {}).items()):
                out += [f"**Room {room_no} brief**", "", "```",
                        brief.strip(), "```", ""]

    (config.PROJECT_ROOT / "AGENT-PROMPTS.md").write_text(
        "\n".join(out), encoding="utf-8", newline="\n")
    print(f"wrote AGENT-PROMPTS.md — {len(personas.BUILTINS)} personas, "
          f"{len(web)} with web access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
