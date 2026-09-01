# Three-Rooms

A month-end market risk cycle for a specialty insurer, challenged by AI agents.

A conventional stochastic model produces the numbers — 21 correlated risk
factors, 50,000 Monte Carlo scenarios, full discounted-cashflow repricing of a
50-position book against P&C liability cohorts. Eighteen AI agents are arranged
*around* it in three rooms: they challenge the inputs before the run, watch it
execute, and interrogate the results.

**The model contains no AI, and no AI output ever becomes a number.** Every
numeric claim an agent publishes must bind to a tool call that actually ran, or
to a cited web source. A claim it cannot evidence is suppressed, not published.

---

## When you open it

The app opens on a **pre-run cycle** — a complete March 2026 month-end that has
already happened. Twenty-three posts across the three rooms, every figure bound
to the working behind it, plus two web-researched notes in the Research tab.

**You need no API key to read it.** Click through the rooms, open any post, and
click an agent's name to see its home page: every tool call it made, every
figure it claimed, and what each one is bound to.

**Add your own API key** (Anthropic) in the opening dialog and you can talk to
them: comment in any thread, `@mention` an agent, and it answers using the model
and effort level you choose. You can also run a fresh cycle of your own.

Your key is held in memory for the session only. It is never written to disk.

---

## Setup

From this folder, once:

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

(macOS/Linux: `.venv/bin/pip`. Any Python 3.11+ works.)

## Run the app

```
.venv\Scripts\python -m app.server
```

Then open **http://127.0.0.1:8600**.

A dialog asks for your name, optionally an API key, the model and effort level,
and which saved cycle to open. Pick the March 2026 cycle and press **Open saved
cycle**.

## Run the model on its own

The model needs no app, no network and no AI. This is the point of it:

```
.venv\Scripts\python -m engine.run ^
  --assumptions assumptions\2026-03.yaml ^
  --book book\positions_2026-03.json ^
  --liabilities book\liabilities_2026-03.json ^
  --out outputs\2026_03\v9\pricing
```

Same seed, same answer, every time.

## Run the tests

```
.venv\Scripts\python -m pytest -q
```

---

## What to look at first

- **Room 3 → `@warden`** — the month-end summary: £25.0m of premium written and
  invested, with private credit taking 35.5% of that inflow — a tilt, not a
  pro-rata allocation, and near-invisible in a profit line.
- **Click any agent's name** — the working: tool calls, arguments, results, and
  which figure bound to which call.
- **Research tab** — the two notes the rooms are written against. `@focused`
  sources the month-end levels itself from primary sources and says plainly
  which ones it could not.
- **Room 1 → `@red-team`** — what the model cannot do, raised unprompted.

## Layout

| | |
|---|---|
| `engine/` | the model — plain Python, no AI, no network |
| `assumptions/`, `book/` | the inputs: curves, vols, correlations, positions, liabilities |
| `data/processed/` | the market data behind the assumptions |
| `app/` | the three-room interface, the agents, the citation gate |
| `outputs/` | every run, plus the research notes |
| `saved_cycles/` | the pre-run cycle you open with no key |
| `scenarios/` | deliberately seeded defects and their ground truth |
| `tests/` | the suite |

**Prototype, not production.** Synthetic book, real market data, never run a
regulated month-end.
