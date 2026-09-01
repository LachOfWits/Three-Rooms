# API_ASSUMPTIONS — response shapes the frontend depends on

Audited against `app/server/main.py`, `app/config.py`, `app/server/events.py`,
`app/server/engine_bridge.py` and real committed output files on 2026-08-28;
re-audited 2026-08-29 against a live server (fresh DB, mock mode) for the
notifications centre, snapshots, significance, run control and the scenario
explorer; re-audited again **2026-08-30** against a server on port 8611
(mock mode, a copy of `app.sqlite`) for the PENDING-BATCH2 §3 stage strip,
§4 pass visibility, §5 agent profiles and §6 notification history; re-audited
again **2026-08-31** against a server on port 8613 over a freshly seeded
scratch DB (two engine runs, a research pass and one pass per room, mock
throughout) for the §8 VCV attachment, §10 `@story`, §11 in-place agent
editing and §13/§14 the agents panel.
Items marked **(assumed)** target endpoints being added in parallel;
the UI degrades gracefully when they are absent.

## Verified against the running server

- `GET /api/config` → `{agent_mode, anthropic_model (null in mock),
  limits: {MAX_TOOL_CALLS_PER_POST, MAX_REPLIES_PER_THREAD, MAX_POSTS_PER_PASS,
  ENGINE_PACE_SECONDS}, engine: {default_seed, default_sims}, agents_available}`.
- `GET /api/agents/{room}` → `{agents: [{id, room, handle, name, focus,
  persona_prompt, builtin, created_at, avatar_json?}]}`. `avatar_json` is
  currently absent from the schema (audit deviation #4); the client renders
  the §8.1 default-avatar rule whenever the field is missing/null, with the
  shipped `@red-team` red-circle-plus-yellow-horns default special-cased so
  the spec's avatar holds before the column lands.
- `GET /api/runs` → `{runs: [run]}`; `run = {id, asof (YYYY-MM-DD), kind,
  parent_run_id, seed, sims, status queued|running|done|failed, out_dir,
  adjustments_json, started_at, finished_at, seeded (bool, derived from the
  run manifest by the server)}`.
- `POST /api/runs {asof, seeded_assumptions?, seeded_book?}` → `{run}`;
  engine executes in a background task, progress via SSE.
- `GET /api/rooms/{room}/feed` → `{room, room_name, posts: [origin...],
  suppressed: [post...], suppression_rate}`. Origin posts carry `claims`
  (parsed) + `reply_count`; **tool_calls only appear in the thread view**.
- `GET /api/rooms/{room}/feed?thread=<id>` → `{room, room_name, thread: post,
  children: [post...]}` where each post carries `claims` (parsed
  `[{text, value, tool_call_id}]`) and `tool_calls`
  (`[{id, tool, args_json, result_json, artifact_path, ts}]`).
  `children` is the full descendant set; the client splits by
  `type === "expansion"` (the working) vs replies.
- `POST /api/rooms/{room}/posts {body, parent_id?, author_label?}` → `{post}`.
  `author_label` is accepted and stored (defaults to `"you"`).
- `POST /api/rooms/{room}/refresh {run_id | pair: [prev, curr], seeded}` →
  `{status: "scheduled", ...}`; 503 while the agents package is absent.
- `GET /api/gates` → `{gates: [{id, run_id, proposed_by_post_id,
  adjustments_json, rationale, status, decided_by, decided_at,
  result_run_id}]}`.
- `POST /api/gates/{id}/approve {decided_by}` → `{gate, run}` (the corrected
  rerun, already queued+executing). `/reject {decided_by}` → `{gate}`.
  422 when `decided_by` is empty — surfaced verbatim in the UI.
- `GET /api/dashboard/{room}?run=<id>` or `?pair=<prev,curr>` →
  `{room, room_name, current, previous?, attribution?}` plus
  room 1: `assumptions {meta, curves, spreads, equity, fx, vols}`;
  room 2: `stage_events`. `current/previous = {run, valuation {asset_total_gbp,
  liability_pv_gbp, surplus_gbp, meta {assumptions_sha256, book_sha256, seed,
  n_sims, ...}}, var_aggregate {aggregate_var_gbp, sum_standalone_blocks_gbp,
  diversification_benefit_gbp, diversification_ratio}, var_blocks {blocks:
  {ir_gbp, ir_usd, credit, equity, fx}}, top_positions_by_var
  [{id, name, market_value_gbp, var_99_5_1y_gbp}], manifest}`.
  `attribution = {meta, mtm, var}` with each of mtm/var =
  `{prev_*_gbp, curr_*_gbp, steps: [{step, name, delta_gbp}], residual_gbp,
  additivity_check}` (mtm anchors are `prev/curr_surplus_gbp`, var anchors
  `prev/curr_aggregate_var_gbp`).
- `GET /api/scorecard` → `{posts {published, suppressed, total},
  suppression_rate, citations {claims_total, claims_bound, binding_rate,
  tool_calls_recorded}, detection: null | {seeded_run_ids, defects
  [{id, field, severity, detected, post_ids}], recall, precision,
  must_not_flag_violations [...]}}`.
- `GET /api/runs/{id}/events` — SSE. Named events:
  - `stage`: a stage_events row `{id, run_id, stage, status, detail_json, ts}`
    (stored history replayed first, then live).
  - `run_status`: the run row (no `seeded` key — the client preserves the
    flag it already holds from `/api/runs`).
  - `post`: `{id, room, run_id, type, status, author_label, parent_id}` —
    broadcast to every open stream; the client refetches the feed rather than
    trusting this partial row.
- Static mount at `/` (html=True) serves `app/static/index.html`.

## Verified 2026-08-29 (the additions this pass depends on)

- `GET /api/notifications[?unread_only=]` → `{notifications: [{id, kind
  reply|mention_answered|gate_pending|snapshot_ready|suppressed, post_id,
  thread_root_id, room, agent_id, created_at, read_at, author_label?,
  excerpt?}], unread_count}`. `created_at` may be an ISO string with offset
  (server-originated rows) **or** SQLite's naive `YYYY-MM-DD HH:MM:SS` UTC —
  the client parses both and treats naive as UTC.
  `POST /api/notifications/{id}/read` and `POST /api/notifications/read_all`
  return the updated row / `{status:"ok"}`.
- SSE named event **`notification`** carries a raw `notifications` row (no
  `excerpt`), so the client refetches `/api/notifications` on receipt rather
  than trusting the partial row — same pattern as `post`.
- Feed posts now carry `significance` (`critical|notable|routine|quiet`,
  nullable), `snapshot_id` (nullable), `pinned` (0|1) and `sources_json`.
  The feed is ordered `pinned DESC, id DESC`, so `@warden` arrives first;
  the client still renders pinned posts in their own block above the groups.
- `GET /api/rooms/3/snapshots?run_id=<id>` → `{snapshots: [{id, run_id, seq,
  data_through, created_at}]}`; `POST /api/rooms/3/snapshot {run_id}` →
  `{status:"scheduled", run_id}` (409 when the run is not `done`, 503 with no
  agents package). Room 3 groups its feed by `snapshot_id`, newest seq first;
  posts with `snapshot_id: null` are the month-end pass.
- `GET /api/scorecard` also returns `quiet_rate` and
  `detection.must_flag_changes` (the client already accepted `must_flag` /
  `must_flag_items` as aliases).

## Verified 2026-08-30 (PENDING-BATCH2 §2–§6)

- `GET /api/research/reports[?asof=YYYY-MM]` → `{reports: [{agent
  ("focused" | "wide-eye"), month, file, path (null when unwritten),
  generated_at (ISO, null when unwritten), bytes}], month}`, month-descending.
  The research tab uses it as the **index**; the documents come from
  `GET /api/research?asof=<YYYY-MM>&agent=<agent>` →
  `{month, asof, agent, prev_asof, path, file, markdown}`.
  `generated_at: null` does **not** mean "no report": `/api/research`
  regenerates deterministically per request, so the tab renders the document
  and labels the header *"computed on request — no file written for this
  month yet"* rather than claiming the report is missing.
- `POST /api/research/run {month}` → `{status:"scheduled", stage:"research",
  month, cycle:["research",1,2,3]}`; 503 with no agents package, 422 on a
  month it cannot resolve. Runs the stage in the background.
- SSE named event **`research`** — broadcast to every open stream (run_id
  `None`) carrying `{month, reports: [...], errors: [...]}`. The client
  treats a non-empty `errors` as a failed stage.
- Run rows now carry `label` (`2603_v2`) and `version` — the client's derived
  fallback (below) is no longer exercised on this server, and the server
  values win.
- `reply_count` on a feed row is the count of **direct children**, which
  includes `expansion` posts (an agent's working) as well as replies. The
  footer says "N replies" per §4 and the tooltip states what is counted;
  inside a thread the same line is computed from the loaded descendant set.

### No pass-completion event — how the strip settles (§3/§4)

`POST /api/rooms/{room}/refresh` returns `{status:"scheduled"}` and the
server emits **no** "pass finished" event: `_bg_room_pass` publishes `post`
(and `notification`) rows only, all at the end of the pass. So the client
tracks a pass itself:

- on start it snapshots the room's current post ids and the room's roster
  from `GET /api/agents/{room}` (5 / 3 / 9 today, matching the brief);
- every post published after that snapshot removes its author from the
  "still to post" list (suppressed posts count — the agent did run);
- the stage is `done` when the list empties, and `failed` after 240s with
  nothing posted (`done` with a partial count if some landed).

This means an agent that legitimately publishes **nothing** in a pass would
hold the row open until the timeout. Every builtin posts every cycle today
(`quiet` is a short post, not silence), so this has not been observed; a
`GET /api/rooms/{room}/pass` (or a `pass` SSE event carrying the roster and
a terminal status) would replace the inference outright.

Research settles on whichever lands first: the `research` SSE event, or a
changed `generated_at` in `GET /api/research/reports` (polled at 1.5s, 90s
ceiling).

## Verified 2026-08-31 (PENDING-BATCH2 §8, §10, §11, §13)

Against a server on port 8613 over a scratch DB seeded with two real engine
runs, a research pass and one pass in each room (mock throughout).

- **`posts.attachment_json` (§8)** arrives on a feed row and on a thread row
  as a JSON **string** (`SELECT *`), shaped
  `{"type": "vcv_table", "payload": {...}}`. The payload carries `factors`
  (21 names, SPEC order), `vols` (`[{factor, current, prior, change}]`,
  `prior`/`change` null on the first run), `corr` (21×21 numbers),
  `mover_threshold` (0.05), and `corr_prior` **only when a prior run
  exists**. The client renders the vols table from `vols`, the heatmap from
  `corr`, outlines a cell when `|corr − corr_prior| > mover_threshold`, and
  says "no prior run to compare against" when `corr_prior` is absent.
  Unknown `type` values render a one-line note, never raw JSON; malformed
  JSON renders nothing at all (`tryJson`).
- **`posts.sources_json` (§10)** is a list of post ids and includes an
  agent's **expansion** posts as well as its origin, so ids that are not in
  any feed payload are expected. The client chips one source per
  agent-per-room and counts the remainder as "+N more posts" rather than
  claiming they are missing.
- **`agents.also_posts_in` (§13)** arrives on `GET /api/agents/{room}` and
  on the profile as a JSON string (`"[3]"`, `"[2, 3]"`). `room` stays the
  HOME room, so a persona scheduled elsewhere is listed once per room it
  posts in and never twice in one list.
- **`GET /api/agents/{handle}/profile` → `agent.modified`** (§11) is the
  server's own comparison against the shipped persona. Verified both ways:
  it turns **true** after an edit and back to **false** when the shipped
  text is restored through the edit form — which is also the check that the
  client's `_CITE`/`_STYLE` split-and-rejoin is byte-exact.
- The profile's post rows carry `thread_id`, so a reply opens its thread
  root rather than itself.

## Assumed (endpoints being added in parallel; graceful degradation)

- **Agent profile / activity** — **NO LONGER ASSUMED.** The server serves
  `GET /api/agents/{handle}/profile` (`app/server/main.py`, §5), returning
  `{agent, grants, counts, posts}`. The client probes that FIRST and
  remembers the *index* of whichever endpoint answered (never the resolved
  path — a path is per-agent, so caching the string sent every later agent
  to the first agent's URL). The three shapes guessed at before the route
  existed — `GET /api/agents/{room}/{id}/profile`,
  `GET /api/agents/{room}/{id}/activity`, `GET /api/agents/{id}/activity` —
  are kept as fallbacks and all still 404. Expected body (either at the top
  level or under `activity`):
  `{posts: [{id, room, parent_id, type, status, significance, run_id,
  body_md, created_at}], counts: {published, suppressed, quiet, tool_calls},
  grants: {web_search: bool, tools: [name…]}}`.
  **Degraded path**: posts come from the three `GET /api/rooms/{r}/feed`
  payloads (origin + suppressed rows filtered by `agent_id`), which
  **excludes replies inside threads**; `published`/`suppressed`/`quiet` are
  counted from those rows and `tool_calls` shows `—`. The panel says so in
  place, so a partial count is never passed off as a total.
- **Per-agent grants** — served by `GET /api/agents/{handle}/profile` as
  `grants: {tools, web_search, tool_count}`, which always wins. The mirror
  below is now only the fallback for when that endpoint does not answer.
  Client-side mirror of
  `app/agents/runtime.WEB_SEARCH_HANDLES` (7 handles) and the 19 names in
  `app/agents/tools.TOOL_SPECS`, labelled as a mirror. A server-supplied
  `grants` object always wins. **This mirror is a duplicate of server
  constants and will drift** — it is a stopgap, not a design.
- The persona prompt shown on a profile is split at the first
  `HARD RULE: every numeric claim` / `HOUSE STYLE, binding:` marker, because
  the seeded `persona_prompt` already has `_CITE` and `_STYLE` concatenated
  onto it. The tail renders greyed under "appended to every agent
  automatically". If the server ever stores the persona without those
  blocks, the split simply finds no marker and shows the whole field.

- **`POST /api/runs/{id}/stop`** **(assumed)** — body `{stopped_by}`; expected
  to terminate the engine subprocess, set the run's status to `stopped` and
  KEEP its partial `stage_events` (PENDING-ROSTER J). Today the server has no
  such route (`/api/runs/{id}` is GET-only), so the call returns 405 and the
  UI says so verbatim and leaves the run alone — verified.
  The client treats `stopped` / `cancelled` / `canceled` as the stopped state:
  struck through in the run dialog and the pair pickers, disabled as a basis
  for attribution or a room pass, still visible as history.
- **Run labels `YYMM_vN`** — **now served** (2026-08-30): rows carry `label`
  and `version`. The client still derives them when absent (month from
  `asof[0:7]`, version from the run's rank by ascending id within that month)
  so an older server keeps working; the server values win. `asof` may be a month (`2026-03`) or a full date
  (`2026-03-31`); both label identically.
- **Scenario explorer (N)** **(assumed)** — the client probes, in order,
  `GET /api/runs/{id}/scenario?rank=<n>&percentile=<p>` then
  `GET /api/scenario?run={id}&rank=<n>&percentile=<p>`, and remembers whichever
  answers. Both query params are sent so either signature works. The expected
  body is `app/agents/tools.read_scenario(run, rank=...)` verbatim (or wrapped
  as `{scenario: {...}}`): `{n_sims, loss_rank, loss_percentile, loss_gbp,
  surplus_pnl_gbp, reported_aggregate_var_gbp, factors: [{factor, shock,
  base_level, shocked_level, vol_annual, shock_in_vols, shock_kind, unit}],
  positions_by_loss: [{id, name, type, pnl_gbp}], position_pnl_gbp,
  spread_floor_incidence, joint_plausibility: {available, mahalanobis_d2,
  chi2_expected_d2, chi2_percentile}}`. Rendering was verified against a real
  `read_scenario(2)` payload injected into the page; `chi2_percentile` is
  treated as **already a percentage** (0–100), matching the tool. Rank for a
  percentile is `max(1, round((1-p) × run.sims))`. Absent endpoint → an inline
  "not deployed yet" note in the panel, nothing else breaks.
- **Month-end `asof` values** — `POST /api/runs` needs a full date, so the run
  dialog maps month → last business day from SPEC §8 (2025-12-31 … 2026-07-31)
  and falls back to the month key for anything outside that table. If an
  assumptions-listing endpoint appears, it should supersede this map.
- **`@`-menu filtering** matches handle, name, focus **and `persona_prompt`**.
  The roster's own example ("@correlation surfaces @vlad and @vcv-sentinel")
  is only true with the prompt included — Vlad's correlation decomposition is
  described there, not in his one-line `focus`.

- `GET /api/research?asof=<YYYY-MM>&agent=` — **now verified** (see above).
  The client still accepts a bare JSON string or the note under any of
  `markdown | md | body | content | text | note`, so a plainer response
  shape keeps rendering. 404 → a per-report "not generated yet" block;
  any other error is shown inline against that report only, so one broken
  report never hides the other.
- `PATCH /api/agents/{room}/{id}` — **no longer assumed; verified
  2026-08-31** against a server on 8613. Body
  `{name, focus, persona_prompt, outlook, avatar_json}` (handle immutable —
  never sent; the server 422s a changed one). It returns `{agent}`, the
  stored row, which the client adopts rather than trusting what it sent.
  The 404/405/501 fallback (apply locally for the session, toast saying so)
  is kept for an older server.
- `POST /api/agents/{room}` — the client also sends `avatar_json`
  **(assumed)**; today's server ignores unknown keys, so creation still
  works and the default-avatar rule covers rendering.
- Scorecard `detection.must_flag | must_flag_changes | must_flag_items`
  **(assumed)** — when the D4/must-flag scoring lands, any of these keys
  (array of `{id|field, detected|flagged}`) renders as "MF" rows.

## Client-side conventions

- Every piece of user/agent text is HTML-escaped (`esc()` in util.js) before
  entering the DOM; the tiny markdown renderer operates on escaped text only
  and emits a fixed tag whitelist. No raw `innerHTML` of server text, ever.
- Avatar colors are validated against `#hex` before being placed in SVG.
- `@wide-eye` posts — and `@wider-risk`, the pre-rename handle — plus any body
  carrying the exact label, render quarantined with the literal label
  "context — enters no calculation".
- Room *names* are never rendered anywhere (PENDING-ROSTER Q): the client
  ignores `room_name` from `/api/rooms/{room}/feed` and `/api/dashboard/{room}`
  and shows the digit only. The endpoints may keep returning it.
- The compute-governor readout is gone (Q). `/api/config` limits are still
  fetched (`ENGINE_PACE_SECONDS` etc. remain part of the contract) but only
  `agent_mode`, `anthropic_model` and `engine.default_*` are displayed.
- The base/seeded rail control is gone (R). Seeded inputs are chosen in the run
  dialog and posted as `seeded_assumptions` / `seeded_book` on
  `POST /api/runs`; the paths are held client-side per month (only 2026-03 has
  bundled seeded inputs today) and the checkbox is disabled for months with
  none. The scorecard's detection block renders only when the **active** run
  is `seeded`.
- **§5 — one identity, one surface.** An agent's avatar, handle, mention chip
  and roster card all carry `data-act="agent-profile"`; the profile opens in
  a second slide-out (`#profile`, z-index 64) stacked over the thread
  (z-index 60), so a profile opened from inside a thread returns to that
  thread on close. **§11 supersedes the old split:** the profile's `Edit`
  button no longer opens the modal — the same page switches to edit mode
  (`S.profile.mode`), `Save` PATCHes and returns to view with the panel
  still open, `Cancel` reverts, and every close path (button, veil, Escape,
  opening another agent) asks before discarding an unsaved draft. The modal
  is now the **create** surface only.
- **§6 — notifications are history.** The server returns every row, id
  descending; the client sorts **unread first, then newest first**, caps the
  render at 50 (and says so when it truncates), and marks a read row with
  `.read` (greyed) rather than removing it. "Mark all read" leaves the
  dropdown open so the greying is visible.
- Severity styling is derived from the deterministic check vocabulary:
  `FLAG —` / `DISCREPANT` → finding; `material allocation change` →
  material change; `propose…rerun` → gate proposed.
- **§8 — the attachment is collapsed, and it never widens the page.** The
  toggle is a one-line button (`▸ VCV · 21 factors`); the matrix is built
  only when it is open, and toggling swaps that block in place rather than
  re-rendering the feed (a full re-render throws the reader back to the top
  mid-table). The block and everything in it carry `data-act`, so reading a
  441-cell grid inside a feed card never opens the thread underneath it.
  Both tables scroll inside their own container; `.post-attach` is
  `max-width: 100%`, so the centre column, `#feed-scroll` and the page all
  stay at zero horizontal overflow.
- **§10 — a story is titles, not prose.** A post by `@story` is rendered
  from its own markdown shape rather than through `mdRender`: a lone bold
  line is the section head ("This month, closing."), `**Title.** line` is a
  story block (title as a header, the accumulated line beneath, `*(closed)*`
  becoming a marker), and `- @handle — why` stays a list for the room-3
  "worth reading" footer. Pinned display order is `@story`, then `@warden`,
  then anything else pinned — enforced client-side, not inherited from the
  feed's `pinned DESC, id DESC`.
- **§13/§14 — one card per persona.** The agents panel lists the agents
  whose output the current tab shows: a room lists its own roster plus the
  personas scheduled into it by `also_posts_in`, deduplicated by handle;
  the Research tab lists `@focused` and `@wide-eye`. `@focused` therefore
  appears once in room 1's list and once in room 3's, never twice in one,
  and its profile carries its posts from both rooms.
