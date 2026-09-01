/* app.js — Three Rooms frontend (SPEC-APP §8 / §8.1 + PENDING-ROSTER
   E, F, G, I, J, K, N, O, P, Q, R).
   Vanilla JS, no build step, no external assets. All dynamic text is
   escaped via esc() before entering the DOM (see util.js).

   Declutter pass (PENDING-ROSTER Q): rooms are 1, 2, 3 and nothing else —
   no room names anywhere; no compute-governor readout; no strapline. */

"use strict";

/* ======================================================================
   state
   ====================================================================== */

const S = {
  cfg: null,
  agents: [],            // rows from /api/agents/{1..3}
  agentsById: {},
  agentsByHandle: {},
  runs: [],
  runsById: {},
  room: 1,               // active room
  view: "room",          // "room" | "research"
  prevId: null,          // selected prev run id (pair)
  currId: null,          // selected curr run id
  feeds: { 1: null, 2: null, 3: null },
  dash: { 1: null, 2: null, 3: null },
  stage2: {},            // stage_event id -> row (curr run, merged SSE+fetch)
  thread: null,          // {rootId, room, data}
  gates: [],
  scorecard: null,
  unread: { 1: false, 2: false, 3: false },
  seenPostIds: new Set(),
  seenStageIds: new Set(),
  es: null, esRun: null, esOk: false,
  anchor: null,          // {a: sha8, b: sha8} for the active curr run
  wfMode: "var",         // waterfall toggle: "var" | "mtm"
  operator: "",
  // F — notifications centre
  notifs: [], notifUnread: 0, notifOpen: false,
  // E — room 3 snapshots (run_id -> rows)
  snapshots: {},
  snapBusy: false,
  quietOpen: {},         // group key -> expanded?
  // F "nice touch" — an agent is working on a reply to your comment
  pending: {},           // thread root post id -> {handles, at, baseReplies}
  // N — scenario explorer
  scn: { runId: null, pct: 0.995, data: null, err: null, busy: false,
         open: false },
  scnPath: null,         // which assumed endpoint answered
  // O — collapsible side panels
  panels: { dash: false, rail: false },
  // J — run dialog
  runModal: null,
  // BATCH2 §3 — stage strip: research -> 1 -> 2 -> 3, each idle|running|done|failed
  stages: {
    research: { state: "idle", note: "" },
    1: { state: "idle", note: "" },
    2: { state: "idle", note: "" },
    3: { state: "idle", note: "" },
  },
  runAll: null,          // {queue: [...stages], at: idx} while "Run all" chains
  // BATCH2 §4 — a pass in flight per room: who has yet to post
  passes: { 1: null, 2: null, 3: null },
  // BATCH2 §5 — agent profile slide-out
  profile: null,         // {agent, data, busy, err, src, mode, draft}
  profileEp: null,       // index into PROFILE_ENDPOINTS that answered
  // BATCH2 §8 — post attachments (vcv_table): COLLAPSED by default
  attachOpen: {},        // post id -> expanded?
  // BATCH2 §10 — @story's source chips point at posts in any room, so the
  // three feeds are fetched once rather than only the room being looked at
  feedsAll: false,
  // BATCH2 §2/§5 — the research tab holds BOTH reports for a month
  reports: { asof: null, rows: [], docs: {}, err: null, busy: false },
  reportMonths: null,    // months with a report already written (index probe)
  // R — seeded inputs are a choice of files at run creation, not a mode
  seededPaths: {
    "2026-03": { assumptions: "scenarios/seeded/assumptions_2026-03_D1.yaml",
                 book: "scenarios/seeded/positions_D2.json" },
  },
};

/* SPEC §8 month-end candidates (last business day). */
const MONTH_ASOF = {
  "2025-12": "2025-12-31", "2026-01": "2026-01-30", "2026-02": "2026-02-27",
  "2026-03": "2026-03-31", "2026-04": "2026-04-30", "2026-05": "2026-05-29",
  "2026-06": "2026-06-30", "2026-07": "2026-07-31",
};
/* PENDING-BATCH2 §1: only 2026_02 and 2026_03 exist. The other YAMLs stay on
   disk (regenerating them is cheap) but they are dropped from the pickers —
   offering a month with no committed run just clutters the UI. A month that
   HAS a run is added back by availableMonths(). */
const COMMITTED_MONTHS = ["2026-02", "2026-03"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const BLOCK_LABELS = { ir_gbp: "rates GBP", ir_usd: "rates USD",
                       credit: "credit", equity: "equity", fx: "FX" };

function lsGet(k, fb) { try { return localStorage.getItem(k) ?? fb; } catch (_) { return fb; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }

/* ======================================================================
   api
   ====================================================================== */

async function api(path, opts) {
  const res = await fetch(path, Object.assign({
    headers: { "Content-Type": "application/json" },
  }, opts));
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) {
    const msg = data && (data.detail || data.error) ? (data.detail || data.error)
      : (res.status + " " + res.statusText);
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}

async function refreshConfig() {
  try { S.cfg = await api("/api/config"); } catch (e) { toast("config: " + e.message, "err"); }
}

async function refreshAgents() {
  try {
    const rooms = await Promise.all([1, 2, 3].map(r => api("/api/agents/" + r)));
    S.agents = [].concat(...rooms.map(r => r.agents || []));
    S.agentsById = {}; S.agentsByHandle = {};
    for (const a of S.agents) {
      S.agentsById[a.id] = a;
      if (a.handle) S.agentsByHandle[String(a.handle).toLowerCase()] = a;
    }
  } catch (e) { toast("agents: " + e.message, "err"); }
}

async function refreshRuns(pickDefaults) {
  try {
    const d = await api("/api/runs");
    S.runs = d.runs || [];
    S.runsById = {};
    for (const r of S.runs) S.runsById[r.id] = r;
    if (pickDefaults) pickDefaultPair();
    if (S.currId && !S.runsById[S.currId]) S.currId = null;
    if (S.prevId && !S.runsById[S.prevId]) S.prevId = null;
  } catch (e) { toast("runs: " + e.message, "err"); }
}

function pickDefaultPair() {
  const rawPrev = lsGet("prevId", null);   // null = never chosen; "" = cleared
  const savedPrev = parseInt(rawPrev == null ? "" : rawPrev, 10);
  const savedCurr = parseInt(lsGet("currId", ""), 10);
  if (S.runsById[savedCurr]) { S.currId = savedCurr; }
  if (S.runsById[savedPrev]) { S.prevId = savedPrev; }
  const byMonth = (m) => S.runs.filter(r => monthOf(r) === m && !runStopped(r))
    .sort((a, b) => (b.status === "done") - (a.status === "done") ||
                    (a.seeded === true) - (b.seeded === true) || b.id - a.id);
  if (!S.currId) {
    const currs = byMonth("2026-03");
    if (currs.length) S.currId = currs[0].id;
  }
  if (S.currId && !S.prevId && rawPrev == null) {
    // a curr with no chosen prev still deserves a comparison: the newest
    // completed run of the closest EARLIER month (J: pairs are
    // version-to-version, and the picker can override this at any time)
    const currMonth = monthOf(S.runsById[S.currId]);
    const earlier = S.runs
      .filter(r => monthOf(r) < currMonth && r.status === "done" && !runStopped(r))
      .sort((a, b) => (monthOf(b) < monthOf(a) ? -1 : monthOf(b) > monthOf(a) ? 1 : b.id - a.id));
    if (earlier.length) S.prevId = earlier[0].id;
  }
}

async function refreshFeed(room) {
  try {
    S.feeds[room] = await api("/api/rooms/" + room + "/feed");
    clearSettledPending(room);
    updatePass(room);
  } catch (e) { toast("feed: " + e.message, "err"); }
  if (room === 3) await refreshSnapshots();
  if (room === S.room && S.view === "room") renderFeed();
}
const refreshFeedActiveDebounced = debounce(() => refreshFeed(S.room), 350);
/* A pass in a room you are NOT looking at still has to be tracked, so the
   switcher dot clears and the strip settles — one debounce per room. */
const refreshFeedDebounced = {
  1: debounce(() => refreshFeed(1), 400),
  2: debounce(() => refreshFeed(2), 400),
  3: debounce(() => refreshFeed(3), 400),
};

/* E — snapshot rows for the active run (room 3 groups its feed by them). */
async function refreshSnapshots() {
  if (!S.currId) return;
  try {
    const d = await api("/api/rooms/3/snapshots?run_id=" + S.currId);
    S.snapshots[S.currId] = d.snapshots || [];
  } catch (e) { /* endpoint absent -> feed renders ungrouped */ }
}

async function refreshDash(room) {
  const curr = S.runsById[S.currId];
  if (!curr) { S.dash[room] = null; if (room === S.room) renderDash(); return; }
  let q = "?run=" + S.currId;
  if (S.prevId && S.runsById[S.prevId]) q = "?pair=" + S.prevId + "," + S.currId;
  try {
    const d = await api("/api/dashboard/" + room + q);
    S.dash[room] = d;
    const meta = d.current && d.current.valuation && d.current.valuation.meta;
    if (meta) {
      S.anchor = { a: shortSha(meta.assumptions_sha256), b: shortSha(meta.book_sha256) };
    } else { S.anchor = null; }
    if (room === 2 && d.stage_events) {
      for (const ev of d.stage_events) S.stage2[ev.id] = ev;
    }
  } catch (e) {
    S.dash[room] = null;
    if (e.status !== 422) toast("dashboard: " + e.message, "err");
  }
  if (room === S.room) renderDash();
  renderHeaderStatus();
}

async function refreshGates() {
  try { S.gates = (await api("/api/gates")).gates || []; }
  catch (e) { /* silent on poll */ }
  renderRail();
}

async function refreshScorecard() {
  try { S.scorecard = await api("/api/scorecard"); } catch (e) { /* silent */ }
  renderRail();
}
const refreshScorecardDebounced = debounce(refreshScorecard, 800);

async function refreshNotifications() {
  try {
    const d = await api("/api/notifications");
    S.notifs = d.notifications || [];
    S.notifUnread = d.unread_count != null
      ? d.unread_count
      : S.notifs.filter(n => !n.read_at).length;
  } catch (e) { /* endpoint absent -> the bell stays quiet */ }
  renderHeaderStatus(); renderSwitcher();
}
const refreshNotificationsDebounced = debounce(refreshNotifications, 500);

/* ======================================================================
   boot
   ====================================================================== */

async function boot() {
  const theme = lsGet("theme", "dark");
  document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "dark");
  S.operator = lsGet("operator", "");
  S.wfMode = lsGet("wfMode", "var") === "mtm" ? "mtm" : "var";
  S.panels.dash = lsGet("panel.dash", "") === "off";
  S.panels.rail = lsGet("panel.rail", "") === "off";
  applyPanels();

  bindGlobal();
  await refreshConfig();
  await Promise.all([refreshAgents(), refreshRuns(true)]);
  renderSwitcher(); renderHeaderStatus();
  buildFeedComposer();
  await Promise.all([
    refreshFeed(S.room), refreshDash(S.room), refreshGates(), refreshScorecard(),
    refreshNotifications(),
  ]);
  renderAll();
  connectSSE();
  // §10: @story's source chips name posts in the other rooms, so the other
  // two feeds are fetched once in the background rather than on demand
  ensureAllFeeds();
  setInterval(pollTick, 10000);
}

let pollBusy = false;
async function pollTick() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const running = S.runs.some(r => r.status === "running" || r.status === "queued");
    await refreshRuns(false);
    await refreshGates();
    await refreshScorecard();
    await refreshNotifications();
    if (running) { await refreshDash(S.room); }
    renderRail(); renderFeedHead(); renderHeaderStatus();
  } finally { pollBusy = false; }
}

function renderAll() {
  renderSwitcher(); renderHeaderStatus(); renderCenter(); renderDash(); renderRail();
}

/* ======================================================================
   run identity — YYMM_vN everywhere a run is named (J)
   ====================================================================== */

function monthOf(r) { return r ? String(r.asof).slice(0, 7) : ""; }

function runVersion(r) {
  if (!r) return 1;
  if (r.version != null) return r.version;
  const sibs = S.runs.filter(x => monthOf(x) === monthOf(r))
    .sort((a, b) => a.id - b.id);
  const i = sibs.findIndex(x => x.id === r.id);
  return i >= 0 ? i + 1 : 1;
}

function runLabel(r) {
  if (!r) return "—";
  if (r.label) return String(r.label);
  const m = monthOf(r).replace("-", "");         // 202603
  return m.slice(2) + "_v" + runVersion(r);      // 2603_v1
}

function nextVersionFor(month) {
  return S.runs.filter(x => monthOf(x) === month).length + 1;
}

function runStopped(r) {
  return !!r && ["stopped", "cancelled", "canceled"].includes(String(r.status));
}
function runInFlight(r) {
  return !!r && (r.status === "running" || r.status === "queued");
}
function runStatusMark(r) {
  if (runStopped(r)) return { glyph: "⏹", word: "stopped", cls: "stopped" };
  if (r.status === "done") return { glyph: "✓", word: "complete", cls: "done" };
  if (r.status === "running") return { glyph: "●", word: "running", cls: "running" };
  if (r.status === "failed") return { glyph: "✗", word: "failed", cls: "failed" };
  return { glyph: "·", word: r.status, cls: "queued" };
}

/* "2602_v4 -> 2603_v1" (or a single run label) */
function pairLabelHTML() {
  const p = S.runsById[S.prevId], c = S.runsById[S.currId];
  const one = (r) => '<span class="runlabel' + (runStopped(r) ? " stopped" : "") +
    '">' + esc(runLabel(r)) + "</span>" +
    (r.seeded ? ' <span class="seed-chip">seeded</span>' : "");
  if (p && c) return one(p) + " &#8594; " + one(c);
  if (c) return one(c);
  return "no run selected";
}

/* ======================================================================
   time helpers
   ====================================================================== */

function parseTs(s) {
  if (!s) return null;
  let t = String(s).trim().replace(" ", "T");
  if (!/[Zz]$|[+-]\d{2}:?\d{2}$/.test(t)) t += "Z";   // SQLite naive UTC
  const d = new Date(t);
  return isNaN(d.getTime()) ? null : d;
}

function relTime(s) {
  const d = parseTs(s);
  if (!d) return "";
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 45) return "just now";
  if (secs < 3600) return Math.round(secs / 60) + "m ago";
  if (secs < 86400) return Math.round(secs / 3600) + "h ago";
  return Math.round(secs / 86400) + "d ago";
}

function fmtDay(s) {
  const d = parseTs(s);
  if (!d) return String(s || "");
  return d.getUTCDate() + " " + MONTH_NAMES[d.getUTCMonth()] + " " + d.getUTCFullYear();
}

/* ======================================================================
   header: switcher (K/Q), status, notification bell (F)
   ====================================================================== */

function roomHasUnread(r) {
  if (S.unread[r]) return true;
  return S.notifs.some(n => !n.read_at && Number(n.room) === r);
}

function renderSwitcher() {
  const el = document.getElementById("switch");
  if (!el) return;
  const active = S.view === "room" ? S.room : 0;
  let html = '<span class="sw-ind" style="--i:' + Math.max(0, active - 1) +
    ";width:30px;transform:translateX(calc(var(--i) * 30px));" +
    (active ? "" : "opacity:0;") + '"></span>';
  for (const r of [1, 2, 3]) {
    // §4: a room with a pass IN FLIGHT gets a pulsing dot, so work happening
    // in a room you are not looking at is visible. It outranks the plain
    // unread dot (a pass in flight will produce unread posts anyway).
    const busy = passInFlight(r);
    html += '<button class="seg' + (active === r ? " on" : "") +
      '" role="tab" aria-selected="' + (active === r) +
      '" data-act="room" data-room="' + r + '"' +
      (busy ? ' title="a pass is running in room ' + r + '"' : "") + ">" + r +
      (busy ? '<span class="working" aria-label="pass running"></span>'
            : roomHasUnread(r) ? '<span class="unread" aria-label="unread"></span>' : "") +
      "</button>";
  }
  el.innerHTML = html;
  const rt = document.getElementById("research-tab");
  if (rt) rt.classList.toggle("active", S.view === "research");
}

function renderHeaderStatus() {
  const el = document.getElementById("hdr-status");
  const cfg = S.cfg || {};
  // A saved cycle is real Claude output. When one is loaded the badge
  // reports ITS mode, not this process's: a judge replaying a live cycle
  // without a key is looking at live output, and "MOCK" over it is a lie
  // the software tells about itself.
  const cyc = cfg.active_cycle;
  // Two states: the agents can run, or there is no key. "MOCK" was a third
  // label for the second one and it read as though the OUTPUT were fake.
  const raw = (cyc && cyc.mode) || cfg.agent_mode || "";
  const mode = String(raw).toLowerCase() === "live" ? "LIVE" : "NO KEY";
  const curr = S.runsById[S.currId];
  let html = '<span class="badge ' + (mode === "LIVE" ? "badge-live" : "badge-nokey") +
    '">' + esc(mode) + "</span>";
  const shownModel = (cyc && cyc.model) || cfg.anthropic_model;
  if (mode === "LIVE" && shownModel) {
    html += '<span class="hdr-kv">model <b>' + esc(shownModel) + "</b></span>";
  }
  // `cyc` still decides the mode badge above — the cycle's own label is not
  // status and does not earn a chip.
  // Seed and the run/anchor sha pair used to sit here. They are provenance,
  // not status: they never change while you read, and they pushed the things
  // that DO change to the edge of the bar. Both are still on the run itself.
  if (!curr) {
    html += '<span class="hdr-kv">no active run</span>';
  }
  if (S.session && S.session.operator) {
    html += '<button class="hdr-kv hdr-op" data-act="open-welcome" ' +
      'title="operator — click to change key, model, or saved cycle">' +
      esc(S.session.operator) +
      (S.session.key_set ? ' <b>key set</b>' : ' <b>no key</b>') + "</button>";
  }
  html += '<span class="sse-dot' + (S.esOk ? " on" : "") +
    '" title="' + (S.esOk ? "SSE connected (live events)" : "SSE not connected") + '"></span>';
  html += bellHTML();
  html += '<button class="btn-ghost" data-act="theme-toggle" title="toggle theme">' +
    (document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light") +
    "</button>";
  el.innerHTML = html;
}

function bellHTML() {
  const n = S.notifUnread || 0;
  let html = '<span class="bell-wrap">' +
    '<button class="bell' + (n ? " has-unread" : "") +
    '" data-act="bell" title="notifications">⚑' +
    (n ? '<span class="count">' + esc(n > 99 ? "99+" : n) + "</span>" : "") +
    "</button>";
  if (S.notifOpen) html += notifPopHTML();
  return html + "</span>";
}

const NOTIF_KIND = {
  reply: "reply", mention_answered: "answered", gate_pending: "gate",
  snapshot_ready: "snapshot", suppressed: "suppressed",
};

/* §6: unread above read, newest first within each, capped at 50. Reading a
   notification GREYS it and keeps it — the dropdown is history, not an
   inbox that empties itself. */
const NOTIF_CAP = 50;

function notifRows() {
  const key = (n) => -(parseTs(n.created_at) ? parseTs(n.created_at).getTime()
                                             : Number(n.id) || 0);
  return S.notifs.slice()
    .sort((a, b) => (!!a.read_at) - (!!b.read_at) || key(a) - key(b) ||
                    (Number(b.id) || 0) - (Number(a.id) || 0))
    .slice(0, NOTIF_CAP);
}

function notifPopHTML() {
  const rows = notifRows();
  const readCount = rows.filter(n => n.read_at).length;
  let html = '<div class="notif-pop"><div class="notif-hd">' +
    '<span class="micro">notifications</span>' +
    (S.notifUnread ? '<button class="btn btn-sm" data-act="notif-read-all">Mark all read</button>' : "") +
    "</div>";
  if (!rows.length) {
    html += '<div class="empty" style="margin:4px">Nothing yet. An agent reply to ' +
      "a thread you posted in, a pending gate or a fresh snapshot lands here.</div>";
    return html + "</div>";
  }
  for (const n of rows) {
    const ag = n.agent_id ? S.agentsById[n.agent_id] : null;
    const who = ag || { name: "", handle: n.author_label || "system" };
    html += '<div class="notif-row' + (n.read_at ? " read" : " unread") +
      '" data-act="notif-open" data-id="' + esc(n.id) + '" data-room="' +
      esc(n.room || "") + '" data-thread="' +
      esc(n.thread_root_id || n.post_id || "") + '">' +
      '<span class="nr-dot' + (n.read_at ? " read" : "") + '"></span>' +
      agentAvatarHTML(who, 24) +
      '<span class="nr-main"><span class="nr-top">' +
        '<span class="nr-kind">' + esc(NOTIF_KIND[n.kind] || n.kind) + "</span>" +
        (n.room ? '<span class="nr-room">room ' + esc(n.room) + "</span>" : "") +
        '<span class="nr-time">' + esc(relTime(n.created_at)) + "</span>" +
      "</span>" +
      (n.excerpt ? '<span class="nr-ex">' + esc(String(n.excerpt).slice(0, 150)) + "</span>" : "") +
      "</span></div>";
  }
  html += '<div class="notif-ft micro">' +
    esc(S.notifUnread + " unread · " + readCount + " read") +
    (S.notifs.length > NOTIF_CAP
      ? esc(" · showing the newest " + NOTIF_CAP + " of " + S.notifs.length) : "") +
    " — read stays here, greyed</div>";
  return html + "</div>";
}

async function openNotification(id, room, threadId) {
  S.notifOpen = false;
  const n = S.notifs.find(x => String(x.id) === String(id));
  if (n && !n.read_at) {
    n.read_at = new Date().toISOString();
    S.notifUnread = Math.max(0, (S.notifUnread || 0) - 1);
  }
  renderHeaderStatus();
  try { await api("/api/notifications/" + id + "/read", { method: "POST" }); }
  catch (e) { /* degrade: the row stays unread server-side */ }
  const r = Number(room);
  if ([1, 2, 3].includes(r)) {
    S.view = "room"; S.room = r; S.unread[r] = false;
    renderAll(); buildFeedComposer();
    await Promise.all([refreshFeed(r), refreshDash(r)]);
  }
  if (threadId) openThread(Number(threadId));
  refreshNotifications();
}

async function markAllNotificationsRead() {
  for (const n of S.notifs) n.read_at = n.read_at || new Date().toISOString();
  S.notifUnread = 0;
  // §6: the dropdown STAYS OPEN — the point of "mark all read" is that you
  // watch the rows grey out, not that the list disappears.
  renderHeaderStatus(); renderSwitcher();
  try { await api("/api/notifications/read_all", { method: "POST" }); }
  catch (e) { toast("mark all read: " + e.message, "err"); }
  refreshNotifications();
}

/* ======================================================================
   authors / avatars / severity helpers
   ====================================================================== */

function authorOf(post) {
  if (post.agent_id && S.agentsById[post.agent_id]) {
    const a = S.agentsById[post.agent_id];
    return { agent: a, handle: a.handle, name: a.name || "", human: false };
  }
  const label = post.author_label || "you";
  const known = S.agentsByHandle[String(label).toLowerCase()];
  if (known) return { agent: known, handle: known.handle, name: known.name || "", human: false };
  return { agent: null, handle: label, name: "", human: true };
}

function authorAvatarHTML(post, size) {
  const au = authorOf(post);
  if (au.agent) return agentAvatarHTML(au.agent, size);
  return avatarSVG(defaultAvatar(au.handle, null), size);
}

/* §5: the avatar is a click target for the profile wherever it appears
   (feed, thread, quiet row, suppressed drawer, notification list). Human
   posts have no profile, so they stay inert. */
function authorAvatarLinkHTML(post, size) {
  const au = authorOf(post);
  if (!au.agent) return authorAvatarHTML(post, size);
  return '<span class="av-link" data-act="agent-profile" data-id="' +
    au.agent.id + '" title="' + escAttr(au.handle) + ' — open profile">' +
    agentAvatarHTML(au.agent, size) + "</span>";
}

/* §4: the feed rows carry `reply_count` from the server; inside a thread the
   root and each child get theirs from the loaded descendant set, so the same
   footer line works in both places. */
function replyCountOf(p) {
  if (p.reply_count != null) return Number(p.reply_count) || 0;
  const t = S.thread;
  if (t && t.data) {
    return (t.data.children || []).filter(c => c.parent_id === p.id).length;
  }
  return 0;
}

function isContextPost(post) {
  const h = String(authorOf(post).handle || "").toLowerCase();
  if (h === "@wide-eye" || h === "@wider-risk") return true;
  return (post.body_md || "").includes("context — enters no calculation");
}

function severityOf(post) {
  if (post.significance === "critical") return "flag";
  const b = post.body_md || "";
  if (/\bFLAG —|\bFLAG -|\bDISCREPANT\b|\*\*Finding\b/.test(b)) return "flag";
  if (/material allocation change/i.test(b)) return "material";
  if (/propose[sd]?[^.]*rerun|pending gate|corrected rerun .*gate|gate #\d+/i.test(b) &&
      /propose/i.test(b)) return "gate";
  return null;
}

function sevBadgeHTML(sev) {
  if (sev === "flag") return '<span class="sev-badge flag" title="deterministic check flagged a defect">&#9873; finding</span>';
  if (sev === "material") return '<span class="sev-badge material" title="material change surfaced (not an error)">&#916; material change</span>';
  if (sev === "gate") return '<span class="sev-badge gate" title="rerun proposed — a named human decides in the right rail">&#8594; gate proposed</span>';
  return "";
}

/* §5: a mention chip is one of the four places an agent's identity appears,
   so it opens the profile like the others. */
function mentionChip(handle) {
  const a = S.agentsByHandle[handle];
  if (!a) return null;
  return '<span class="mention" data-act="agent-profile" data-id="' + a.id +
    '" title="' + escAttr(a.name || a.handle) + ' — open profile">' +
    agentAvatarHTML(a, 16) + esc(a.handle) + "</span>";
}

function renderBody(md) {
  return mdRender(md || "", { mention: mentionChip });
}

/* ======================================================================
   pending replies ("@red-team is looking into this")
   ====================================================================== */

function mentionsIn(body) {
  const out = [];
  const re = /(^|[\s(])@([a-z0-9][a-z0-9-]*)/gi;
  let m;
  while ((m = re.exec(body)) && out.length < 3) {
    const h = "@" + m[2].toLowerCase();
    if (S.agentsByHandle[h] && !out.includes(h)) out.push(h);
  }
  return out;
}

function markPending(rootId, body, baseReplies) {
  if (!rootId) return;
  S.pending[rootId] = { handles: mentionsIn(body), at: Date.now(),
                        baseReplies: baseReplies == null ? null : baseReplies };
}

function pendingFor(rootId) {
  const p = S.pending[rootId];
  if (!p) return null;
  if (Date.now() - p.at > 150000) { delete S.pending[rootId]; return null; }
  return p;
}

function clearSettledPending(room) {
  const feed = S.feeds[room];
  if (!feed || !feed.posts) return;
  for (const p of feed.posts) {
    const e = S.pending[p.id];
    if (!e) continue;
    if (e.baseReplies != null && (p.reply_count || 0) > e.baseReplies) {
      delete S.pending[p.id];
    }
  }
}

function pendingChipHTML(rootId) {
  const p = pendingFor(rootId);
  if (!p) return "";
  const who = p.handles.length ? p.handles.map(h => esc(h)).join(", ") : "an agent";
  return '<div class="pending-chip"><span class="dots"><span></span>' +
    "<span></span><span></span></span>" + who +
    (p.handles.length > 1 ? " are" : " is") + " looking into this</div>";
}

/* ======================================================================
   §3/§4 — stages, passes in flight, and who has yet to post
   ====================================================================== */

/* The cycle, in order. Mirrors app/agents/api.CYCLE_STAGES. */
const CYCLE_STAGES = ["research", 1, 2, 3];

/* A pass in a room is bounded: if nothing has landed by this point we stop
   claiming it is running rather than pulse forever. Mock passes finish in
   seconds; the ceiling is for live and for a server that died mid-pass. */
const PASS_TIMEOUT_MS = 240000;

function stageOf(stage) {
  return S.stages[stage] || (S.stages[stage] = { state: "idle", note: "" });
}

function setStage(stage, state, note) {
  const s = stageOf(stage);
  s.state = state;
  s.note = note || "";
  renderRail(); renderSwitcher(); renderFeedHead();
}

function passInFlight(room) {
  const p = S.passes[room];
  return !!p && !p.ended;
}

/* Who is expected to post in this room's pass: the room's own roster, in
   roster order. Visiting agents (@red-team's closing half in room 3, say)
   are homed elsewhere and are not counted — the strip's "5 / 3 / 9" is the
   room's own list, which is what /api/agents/{room} returns. */
function passRoster(room) {
  return S.agents.filter(a => a.room === room)
    .sort((a, b) => (a.id || 0) - (b.id || 0));
}

function beginPass(room) {
  const feed = S.feeds[room];
  const seen = new Set();
  for (const p of (feed && feed.posts) || []) seen.add(p.id);
  for (const p of (feed && feed.suppressed) || []) seen.add(p.id);
  S.passes[room] = {
    room: room, at: Date.now(), ended: false, baseline: seen,
    expected: passRoster(room).map(a => String(a.handle || "").toLowerCase()),
    posted: [],
  };
  setStage(room, "running", "pass scheduled");
}

function endPass(room, state, note) {
  const p = S.passes[room];
  if (p) p.ended = true;
  S.passes[room] = null;
  setStage(room, state, note);
  if (S.room === room && S.view === "room") renderFeed();
  onStageSettled(room, state === "done");
}

/* Called after every feed load: anything published since the pass started
   removes its author from the "still to post" list. */
function updatePass(room) {
  const p = S.passes[room];
  if (!p || p.ended) return;
  const feed = S.feeds[room];
  const rows = [].concat((feed && feed.posts) || [], (feed && feed.suppressed) || []);
  for (const post of rows) {
    if (p.baseline.has(post.id)) continue;
    const h = String(authorOf(post).handle || "").toLowerCase();
    if (!p.posted.includes(h)) p.posted.push(h);
  }
  const remaining = passRemaining(room);
  if (!remaining.length) { endPass(room, "done", passNote(p)); return; }
  if (Date.now() - p.at > PASS_TIMEOUT_MS) {
    endPass(room, p.posted.length ? "done" : "failed",
            p.posted.length ? passNote(p)
                            : "nothing posted in " + Math.round(PASS_TIMEOUT_MS / 1000) + "s");
  }
}

/* "9 of 9 posted · 2 visiting" — visitors are agents homed in another room
   whose check runs here (@red-team closes in room 3, @results-validator
   reviews the draft report there). They are not part of the room's own
   roster, so they are counted separately rather than inflating it. */
function passNote(p) {
  const own = p.posted.filter(h => p.expected.includes(h)).length;
  const visiting = p.posted.length - own;
  return own + " of " + p.expected.length + " posted" +
    (visiting > 0 ? " · " + visiting + " visiting" : "");
}

function passRemaining(room) {
  const p = S.passes[room];
  if (!p || p.ended) return [];
  return p.expected.filter(h => !p.posted.includes(h));
}

/* §4: the typing-indicator row — avatar, "@vlad is working…", one line each,
   removed as posts land. */
function passPendingHTML(room) {
  const remaining = passRemaining(room);
  if (!remaining.length) return "";
  const p = S.passes[room];
  let html = '<div class="pass-pending"><div class="pp-hd micro">' +
    esc("pass running · " + p.posted.length + " of " + p.expected.length +
        " posted · " + remaining.length + " to go") + "</div>";
  for (const h of remaining) {
    const a = S.agentsByHandle[String(h).toLowerCase()];
    html += '<div class="pp-row">' +
      (a ? '<span class="av-link" data-act="agent-profile" data-id="' + a.id + '">' +
           agentAvatarHTML(a, 24) + "</span>"
         : avatarSVG(defaultAvatar(h, h), 24)) +
      '<span class="pp-who">' + esc(h) + "</span>" +
      '<span class="pp-state">is working<span class="dots"><span></span>' +
      "<span></span><span></span></span></span></div>";
  }
  return html + "</div>";
}

/* ======================================================================
   center column: feed head, feed, suppressed drawer, composer
   ====================================================================== */

function renderCenter() {
  if (S.view === "research") { renderResearch(); return; }
  renderFeedHead(); renderFeed();
  document.getElementById("feed-composer").style.display = "";
}

function renderFeedHead() {
  if (S.view !== "room") return;
  const el = document.getElementById("feed-head");
  const curr = S.runsById[S.currId];
  const busy = passInFlight(S.room);
  const canRefresh = !!curr && !busy && !runStopped(curr);
  let actions = "";
  if (S.room === 2) {
    if (runInFlight(curr)) {
      actions += '<button class="btn btn-sm btn-danger" data-act="stop-run">' +
        "⏹ Stop run</button>";
    }
    actions += '<button class="btn btn-sm btn-primary" data-act="run-model">' +
      "Run model</button>";
  }
  if (S.room === 3 && curr && curr.status === "done") {
    actions += '<button class="btn btn-sm" data-act="snapshot" ' +
      (S.snapBusy ? "disabled " : "") +
      'title="re-run the outward-looking agents against a later data-through date">' +
      (S.snapBusy ? "snapshot running…" : "Fresh snapshot") + "</button>";
  }
  actions += '<button class="btn btn-sm" data-act="refresh-room" ' +
    (canRefresh ? "" : "disabled ") +
    'title="run this room’s full agent pass against the selected runs">' +
    (busy ? "pass running…" : "&#10227; Refresh pass") + "</button>";

  el.innerHTML =
    '<div class="feedhead-row">' +
      '<div class="runline" style="flex:1;margin:0">' + pairLabelHTML() + "</div>" +
      '<div class="fh-actions">' + actions + "</div>" +
    "</div>";
}

function feedEmptyHTML() {
  if (!S.runsById[S.currId]) {
    if (S.room === 2) {
      return '<div class="empty">No run yet.<br><br>' +
        '<button class="btn btn-primary" data-act="run-model">Run model</button></div>';
    }
    return '<div class="empty">No run selected.<br>' +
      "Pick one in the right rail, or start one from room <b>2</b>.</div>";
  }
  if (S.room === 2) {
    return '<div class="empty">Fills <b>live</b> while a run executes.<br><br>' +
      '<button class="btn btn-primary" data-act="run-model">Run model</button>' +
      '<div class="inline-note">or <b>&#10227; Refresh pass</b> above for the ' +
      "post-run validation.</div></div>";
  }
  return '<div class="empty">Nothing here yet.<br>' +
    "Click <b>&#10227; Refresh pass</b> above to run the builtin agents " +
    "against the selected runs.</div>";
}

/* ---- snapshot grouping (E) + significance folding (G) ------------------ */

/* §10: pinned order is @story, then @warden, then anything else pinned
   later — display order, deliberately the reverse of execution order. */
function pinRank(p) {
  const h = String(authorOf(p).handle || "").toLowerCase();
  return h === "@story" ? 0 : h === "@warden" ? 1 : 2;
}

function snapshotRows() {
  return (S.snapshots[S.currId] || []).slice()
    .sort((a, b) => (b.seq || 0) - (a.seq || 0));
}

function renderFeed() {
  const el = document.getElementById("feed");
  const feed = S.feeds[S.room];
  const pending = passPendingHTML(S.room);
  if (!feed || !feed.posts || !feed.posts.length) {
    el.innerHTML = pending + (pending ? "" : feedEmptyHTML());
    renderSuppressed();
    return;
  }
  const posts = feed.posts;
  // §10: @story is pinned FIRST in every room — in room 3 that puts it
  // above @warden: what this month is about, then the numbers behind it.
  const pinned = posts.filter(p => p.pinned).sort(
    (a, b) => pinRank(a) - pinRank(b) || (b.id || 0) - (a.id || 0));
  const rest = posts.filter(p => !p.pinned);
  if (posts.some(isStoryPost) && !S.feedsAll) ensureAllFeeds();
  let html = pending;
  for (const p of pinned) {
    html += postCardHTML(p, { feed: true, entered: !S.seenPostIds.has(p.id) });
    S.seenPostIds.add(p.id);
  }

  const snaps = S.room === 3 ? snapshotRows() : [];
  if (S.room === 3 && snaps.length) {
    const bySnap = new Map();
    const base = [];
    for (const p of rest) {
      if (p.snapshot_id == null) { base.push(p); continue; }
      if (!bySnap.has(p.snapshot_id)) bySnap.set(p.snapshot_id, []);
      bySnap.get(p.snapshot_id).push(p);
    }
    for (const s of snaps) {
      const group = bySnap.get(s.id) || [];
      if (!group.length) continue;
      const allQuiet = group.every(p => p.significance === "quiet");
      html += '<div class="snap-div' + (allQuiet ? " quiet" : "") + '">' +
        "<b>Fresh snapshot " + esc(s.seq) + "</b> · " +
        (allQuiet ? "no material change"
                  : "data through " + esc(fmtDay(s.data_through))) + "</div>";
      html += feedGroupHTML(group, "s" + s.id);
    }
    if (base.length) {
      html += '<div class="snap-div"><b>Month-end pass</b>' +
        (S.runsById[S.currId] ? " · " + esc(runLabel(S.runsById[S.currId])) : "") +
        "</div>";
      html += feedGroupHTML(base, "base");
    }
  } else {
    html += feedGroupHTML(rest, "all");
  }
  el.innerHTML = html;
  renderSuppressed();
}

/* G: quiet posts fold into one summary row so the feed shows signal. */
function feedGroupHTML(posts, key) {
  const quiet = posts.filter(p => p.significance === "quiet");
  const loud = posts.filter(p => p.significance !== "quiet");
  let html = "";
  for (const p of loud) {
    html += postCardHTML(p, { feed: true, entered: !S.seenPostIds.has(p.id) });
    S.seenPostIds.add(p.id);
  }
  if (!quiet.length) return html;
  const open = !!S.quietOpen[key];
  html += '<button class="quiet-row" data-act="quiet-toggle" data-key="' +
    escAttr(key) + '"><span class="qr-avs">' +
    quiet.slice(0, 6).map(p => authorAvatarHTML(p, 20)).join("") + "</span>" +
    esc(quiet.length + " agent" + (quiet.length === 1 ? "" : "s") +
        " reporting nothing material") +
    '<span class="qr-more">' + (open ? "collapse" : "expand") + "</span></button>";
  if (open) {
    for (const p of quiet) {
      html += postCardHTML(p, { feed: true, entered: false });
      S.seenPostIds.add(p.id);
    }
  }
  return html;
}

/* ======================================================================
   §8 — post attachments: engine data under the body, COLLAPSED by default
   ====================================================================== */

/* `posts.attachment_json` is {"type": ..., "payload": {...}}. Only types
   this client can actually draw are drawn; an unknown type says so rather
   than dumping JSON into the feed. */
function attachmentOf(p) {
  const a = tryJson(p.attachment_json, null);
  if (!a || typeof a !== "object" || !a.type) return null;
  return { type: String(a.type), payload: a.payload || {} };
}

function attachTitle(att) {
  if (att.type === "vcv_table") {
    const n = (att.payload.factors || []).length;
    return "VCV · " + n + " factor" + (n === 1 ? "" : "s");
  }
  return att.type;
}

function attachmentHTML(p) {
  const att = attachmentOf(p);
  if (!att) return "";
  const open = !!S.attachOpen[p.id];
  let inner = "";
  if (open) {
    inner = att.type === "vcv_table"
      ? vcvTableHTML(att.payload)
      : '<div class="empty">No renderer for attachment type <code>' +
        esc(att.type) + "</code> yet — it is stored, not shown.</div>";
  }
  // the wrapper's own data-act swallows clicks, so reading the matrix
  // inside a feed card never opens the thread underneath it
  return '<div class="post-attach" data-act="attach">' +
    '<button class="attach-toggle" data-act="attach-toggle" data-id="' +
      esc(p.id) + '" aria-expanded="' + (open ? "true" : "false") + '">' +
      '<span class="at-caret">' + (open ? "&#9662;" : "&#9656;") + "</span>" +
      '<span class="at-title">' + esc(attachTitle(att)) + "</span>" +
      '<span class="at-hint">' + (open ? "hide" : "show") + "</span>" +
    "</button>" +
    (open ? '<div class="attach-body">' + inner + "</div>" : "") +
    "</div>";
}

function volPct(v) {
  if (v == null || isNaN(v)) return "—";
  return (v * 100).toFixed(3) + "%";
}
function volChange(v) {
  if (v == null || isNaN(v)) return "—";
  return (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(3) + "pp";
}
function corrNum(v) {
  return (v == null || isNaN(v)) ? "—" : Number(v).toFixed(3);
}

/* §8 — vols as a plain aligned table, correlations as a compact diverging
   heatmap: factor names on both axes, the value on hover, and every cell
   whose change beats the threshold outlined so the movers are findable
   without reading 441 numbers. Both scroll INSIDE their own container. */
function vcvTableHTML(pl) {
  const factors = pl.factors || [];
  const vols = pl.vols || [];
  const corr = pl.corr || [];
  const prior = Array.isArray(pl.corr_prior) ? pl.corr_prior : null;
  const thr = Number(pl.mover_threshold || 0.05);

  let rows = "";
  for (const v of vols) {
    const ch = v.change;
    const cls = ch == null || ch === 0 ? "" : (ch > 0 ? " up" : " down");
    rows += "<tr><td class='lbl'>" + esc(v.factor) + "</td>" +
      "<td class='num'>" + esc(volPct(v.current)) + "</td>" +
      "<td class='num dim'>" + esc(volPct(v.prior)) + "</td>" +
      "<td class='num chg" + cls + "'>" + esc(volChange(v.change)) + "</td></tr>";
  }
  let html = '<div class="vcv-sect micro">volatilities · annualised · ' +
    esc(vols.length) + " factors</div>" +
    '<div class="vcv-scroll"><table class="dt vcv-vols">' +
    "<thead><tr><th>factor</th><th class='num'>current</th>" +
    "<th class='num'>prior</th><th class='num'>change</th></tr></thead><tbody>" +
    rows + "</tbody></table></div>";

  if (!corr.length) return html;

  let movers = 0, grid = '<div class="hc corner"></div>';
  for (const f of factors) {
    grid += '<div class="hc col" title="' + escAttr(f) + '"><span>' +
      esc(f) + "</span></div>";
  }
  for (let i = 0; i < corr.length; i++) {
    const fi = factors[i] == null ? "f" + i : factors[i];
    grid += '<div class="hc row" title="' + escAttr(fi) + '">' + esc(fi) + "</div>";
    for (let j = 0; j < corr[i].length; j++) {
      const v = Number(corr[i][j]);
      const pv = prior && prior[i] && prior[i][j] != null
        ? Number(prior[i][j]) : null;
      const ch = pv == null ? null : v - pv;
      const mv = ch != null && Math.abs(ch) > thr && i !== j;
      if (mv && j > i) movers++;
      const fj = factors[j] == null ? "f" + j : factors[j];
      // diverging: positive toward the accent, negative toward the warm
      // negative ink, both mixed into the card's own inset so the scale
      // reads the same in light and dark
      const pct = Math.round(Math.max(0, Math.min(1, Math.abs(v))) * 78);
      const tone = v >= 0 ? "var(--pos)" : "var(--neg)";
      const tip = fi + " × " + fj + " · " + corrNum(v) +
        (pv == null ? "" : " (prior " + corrNum(pv) + ", " +
          (ch >= 0 ? "+" : "−") + Math.abs(ch).toFixed(3) + ")");
      grid += '<div class="cell' + (mv ? " mover" : "") +
        (i === j ? " diag" : "") + '" style="background:color-mix(in srgb, ' +
        tone + " " + pct + '%, var(--inset))" title="' + escAttr(tip) + '"></div>';
    }
  }

  html += '<div class="vcv-sect micro">correlations · ' + esc(factors.length) +
    "×" + esc(factors.length) + " · hover a cell for its value</div>" +
    '<div class="vcv-legend"><span class="lg-neg">−1</span>' +
    '<span class="lg-bar"></span><span class="lg-pos">+1</span>' +
    '<span class="lg-note">' +
    (prior
      ? esc(movers) + " cell" + (movers === 1 ? "" : "s") + " moved more than " +
        esc(thr.toFixed(2)) + " since the prior run — outlined"
      : "no prior run to compare against, so nothing is outlined") +
    "</span></div>" +
    '<div class="vcv-scroll heat"><div class="vcv-heat" ' +
    'style="grid-template-columns:104px repeat(' + Number(factors.length) +
    ',17px)">' + grid + "</div></div>";
  return html;
}

/* A post by id, from whichever feed or thread is already loaded — the
   attachment toggle and @story's source chips both need it. */
function findPostById(id) {
  id = Number(id);
  for (const r of [1, 2, 3]) {
    const f = S.feeds[r];
    if (!f) continue;
    for (const p of (f.posts || [])) if (p.id === id) return p;
    for (const p of (f.suppressed || [])) if (p.id === id) return p;
  }
  const t = S.thread;
  if (t && t.data) {
    if (t.data.thread && t.data.thread.id === id) return t.data.thread;
    for (const c of (t.data.children || [])) if (c.id === id) return c;
  }
  return null;
}

/* Toggling swaps only the attachment block in place: a full re-render would
   throw the reader back to the top of the feed mid-table. */
function toggleAttachment(id) {
  id = Number(id);
  S.attachOpen[id] = !S.attachOpen[id];
  const p = findPostById(id);
  if (!p) return;
  const html = attachmentHTML(p);
  const sel = '[data-act="attach-toggle"][data-id="' + id + '"]';
  for (const btn of document.querySelectorAll(sel)) {
    const box = btn.closest(".post-attach");
    if (box) box.outerHTML = html;
  }
}

/* ======================================================================
   §10 — @story: few-word titles as headers, the accumulated line beneath,
   and the posts it was composed from as source chips
   ====================================================================== */

function isStoryPost(p) {
  return String(authorOf(p).handle || "").toLowerCase() === "@story";
}

function storyBodyHTML(p) {
  const opts = { mention: mentionChip };
  const lines = String(p.body_md || "").split(/\r?\n/);
  let html = "", list = "";
  const flush = () => { if (list) { html += "<ul>" + list + "</ul>"; list = ""; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) { list += "<li>" + mdInline(esc(bullet[1]), opts) + "</li>"; continue; }
    flush();
    const m = line.match(/^\*\*(.+?)\*\*\s*(.*)$/);
    if (!m) { html += "<p>" + mdInline(esc(line), opts) + "</p>"; continue; }
    let rest = m[2], closed = false;
    const cm = rest.match(/^\*\(closed\)\*\s*/);
    if (cm) { closed = true; rest = rest.slice(cm[0].length); }
    if (!rest) {
      // a lone bold line is a section head ("This month, closing.")
      html += '<div class="story-kicker">' + esc(m[1]) + "</div>";
      continue;
    }
    html += '<div class="story-item' + (closed ? " closed" : "") + '">' +
      '<div class="si-title">' + esc(m[1]) +
      (closed ? '<span class="si-closed">closed</span>' : "") + "</div>" +
      '<div class="si-line">' + mdInline(esc(rest), opts) + "</div></div>";
  }
  flush();
  return html + storySourcesHTML(p);
}

/* §10: the posts the story was composed from. `sources_json` carries every
   post @story read — an agent's origin AND the expansion holding its
   working — so the chips are one per agent per room, pointing at the post
   that has a card in the feed. What is left over is named rather than
   dropped: a source with no card of its own is still a source. */
function storySourcesHTML(p) {
  const ids = tryJson(p.sources_json, null);
  if (!Array.isArray(ids) || !ids.length) return "";
  const seen = new Set();
  let chips = "", extra = 0;
  for (const id of ids) {
    const src = findPostById(id);
    if (!src) { extra++; continue; }
    const au = authorOf(src);
    const key = String(au.handle).toLowerCase() + "/" + src.room;
    if (seen.has(key)) { extra++; continue; }
    seen.add(key);
    chips += '<span class="src-chip" data-act="story-source" data-id="' +
      esc(src.id) + '" data-room="' + esc(src.room) + '" title="' +
      escAttr(au.handle + " · room " + src.room + " · post #" + src.id +
              " — open it") + '">' +
      (au.agent ? agentAvatarHTML(au.agent, 16) : "") +
      esc(au.handle) + "</span>";
  }
  return '<div class="story-src"><span class="micro">composed from</span>' +
    chips + (extra
      ? '<span class="src-chip dim" title="the rest of what it read: an ' +
        'agent&#39;s working and replies live inside a thread, so they have ' +
        'no card of their own in the feed">+' + esc(extra) +
        " more posts</span>"
      : "") + "</div>";
}

/* @story's chips point across rooms, so all three feeds are fetched once
   (they are small, and the switcher + profile fallback both want them). */
async function ensureAllFeeds() {
  if (S.feedsAll) return;
  S.feedsAll = true;
  const missing = [1, 2, 3].filter(r => !S.feeds[r]);
  if (!missing.length) return;
  await Promise.all(missing.map(async (r) => {
    try { S.feeds[r] = await api("/api/rooms/" + r + "/feed"); } catch (_) {}
  }));
  if (S.view === "room") renderFeed();
}

function postCardHTML(p, opts) {
  opts = opts || {};
  const au = authorOf(p);
  const ctx = isContextPost(p);
  const sev = ctx ? null : severityOf(p);
  const cls = ["post"];
  if (ctx) cls.push("context");
  if (sev) cls.push("sev-" + sev);
  if (p.significance) cls.push("sig-" + p.significance);
  if (p.pinned) cls.push("pinned");
  if (opts.entered) cls.push("enter");
  if (opts.feed) cls.push("clickable");

  const claims = p.claims || tryJson(p.claims_json, []) || [];
  const bound = claims.filter(c => c.tool_call_id).length;

  let hd = '<div class="post-hd">' +
    authorAvatarLinkHTML(p, 32) +
    '<span class="who">' +
      '<span class="handle"' +
        (au.agent ? ' data-act="agent-profile" data-id="' + au.agent.id +
                    '" title="open ' + escAttr(au.handle) + '’s profile"' : "") +
      ">" + esc(au.handle) + "</span>" +
      (au.name ? '<span class="name">' + esc(au.name) + "</span>" : "") +
      (p.pinned ? '<span class="pin-mark" title="pinned to the top of this feed">PINNED</span>' : "") +
      (ctx ? '<span class="ctx-label">context — enters no calculation</span>' : "") +
      (sev ? sevBadgeHTML(sev) : "") +
    "</span>" +
    (p.type && p.type !== "origin" ? '<span class="type-pill">' + esc(p.type) + "</span>" : "") +
    '<span class="ts" title="' + escAttr(fmtTsFull(p.created_at)) + '">' +
      esc(fmtTs(p.created_at)) + "</span>" +
  "</div>";

  let ft = "";
  const bits = [];
  // §4: every post carries its reply count in the footer. Zero shows
  // nothing at all rather than "0 replies".
  const nRep = replyCountOf(p);
  if (nRep) {
    bits.push('<span class="replies" data-act="open-thread" data-id="' + p.id +
      '" title="direct children of this post — replies and, where an agent ' +
      'showed its working, expansions">' +
      nRep + (nRep === 1 ? " reply" : " replies") + "</span>");
  }
  if (opts.feed) {
    bits.push('<span class="replies" data-act="open-thread" data-id="' + p.id +
      '">open thread &#8594;</span>');
  }
  if (claims.length) {
    bits.push('<span title="numeric claims bound to executed tool calls">' +
      '<span class="' + (bound === claims.length ? "bound" : "") +
      '" style="color:' + (bound === claims.length ? "var(--good)" : "var(--warn-ink)") + '">' +
      bound + "/" + claims.length + "</span> claims bound</span>");
  }
  if (p.run_id && S.runsById[p.run_id]) {
    bits.push("<span>" + esc(runLabel(S.runsById[p.run_id])) + "</span>");
  }
  if (bits.length) {
    ft = '<div class="post-ft">' + bits.join(" &nbsp;·&nbsp; ") + "</div>";
  }

  const story = isStoryPost(p);
  const body = story ? storyBodyHTML(p) : renderBody(p.body_md);
  if (story) cls.push("story");

  return "<article class='" + cls.join(" ") + "'" +
    (opts.feed ? ' data-act="open-thread" data-id="' + p.id + '"' : "") + ">" +
    hd + '<div class="post-body">' + body + "</div>" +
    attachmentHTML(p) +
    (opts.feed ? pendingChipHTML(p.id) : "") + ft +
    "</article>";
}

function renderSuppressed() {
  const el = document.getElementById("suppressed-drawer");
  const feed = S.feeds[S.room];
  if (!feed) { el.innerHTML = ""; return; }
  const sup = feed.suppressed || [];
  const rate = feed.suppression_rate || 0;
  if (!sup.length) {
    el.innerHTML = '<details class="drawer"><summary>suppressed posts · 0 · rate ' +
      esc(fmtPct(rate, 1)) + "</summary>" +
      '<div class="empty">Nothing suppressed in this room. A post whose numeric claims ' +
      "cannot be bound to an executed tool call is stored here, not published.</div>" +
      "</details>";
    return;
  }
  let rows = "";
  for (const p of sup) {
    const sau = authorOf(p);
    rows += '<div class="post" style="border-style:dashed;opacity:.8">' +
      '<div class="post-hd">' + authorAvatarLinkHTML(p, 24) +
      '<span class="who"><span class="handle"' +
      (sau.agent ? ' data-act="agent-profile" data-id="' + sau.agent.id + '"' : "") +
      ">" + esc(sau.handle) + "</span></span>" +
      '<span class="ts">' + esc(fmtTs(p.created_at)) + "</span></div>" +
      '<div class="supp-reason">suppressed — ' + esc(p.suppression_reason || "no reason recorded") + "</div>" +
      '<div class="post-body" style="color:var(--muted)">' + renderBody(p.body_md) + "</div>" +
      // a suppressed post keeps its attachment exactly as it keeps its body
      attachmentHTML(p) + "</div>";
  }
  el.innerHTML = '<details class="drawer"><summary>suppressed posts · ' + sup.length +
    " · running rate " + esc(fmtPct(rate, 1)) + " — kept, never published</summary>" +
    rows + "</details>";
}

/* ======================================================================
   composer + @-mention autocomplete (I: a discovery surface)
   ====================================================================== */

function buildFeedComposer() {
  makeComposer(document.getElementById("feed-composer"), {
    parentId: null,
    placeholder: "Post here… type @ to see what each agent is for (e.g. @red-team what are we missing?)",
    onPosted: (body, postId) => {
      markPending(postId, body, 0);
      refreshFeed(S.room);
    },
  });
}

function makeComposer(container, cfgc) {
  container.innerHTML =
    '<div class="composer">' +
      '<div class="mention-pop" hidden></div>' +
      '<textarea rows="2" placeholder="' + escAttr(cfgc.placeholder || "Write a post…") + '"></textarea>' +
      '<div class="row">' +
        '<span class="hint">@mention routes the question to that agent (max 3, any room) · Ctrl+Enter to post</span>' +
        '<button class="btn btn-primary btn-sm">Post</button>' +
      "</div>" +
    "</div>";
  const root = container.querySelector(".composer");
  const ta = root.querySelector("textarea");
  const pop = root.querySelector(".mention-pop");
  const btn = root.querySelector("button");
  let matches = [], active = 0, mentionStart = -1;

  function closePop() { pop.hidden = true; matches = []; mentionStart = -1; }

  function openPop() {
    const caret = ta.selectionStart;
    const upto = ta.value.slice(0, caret);
    const m = upto.match(/(^|[\s(])@([a-z0-9-]*)$/i);
    if (!m) { closePop(); return; }
    mentionStart = caret - m[2].length - 1;
    const q = m[2].toLowerCase();
    // I: the filter matches handle, name AND focus — plus the persona
    // prompt, because the spec's own example ("@correlation should surface
    // @vlad and @vcv-sentinel") only holds if the prompt counts: Vlad's
    // correlation work is described there, not in his one-line focus.
    // An empty query lists everyone, so "@" alone is a menu of the desk.
    matches = S.agents.filter(a =>
      !q ||
      (a.handle || "").toLowerCase().includes(q) ||
      (a.name || "").toLowerCase().includes(q) ||
      (a.focus || "").toLowerCase().includes(q) ||
      (a.persona_prompt || "").toLowerCase().includes(q));
    if (!matches.length) { closePop(); return; }
    matches.sort((a, b) => (a.room - b.room) ||
      String(a.handle).localeCompare(String(b.handle)));
    active = Math.min(Math.max(active, 0), matches.length - 1);
    let html = "", lastRoom = null;
    matches.forEach((a, i) => {
      if (a.room !== lastRoom) {
        html += '<div class="mp-group">room ' + esc(a.room) + "</div>";
        lastRoom = a.room;
      }
      const focus = String(a.focus || "");
      const short = focus.length > 90 ? focus.slice(0, 89) + "…" : focus;
      html += '<div class="mention-row' + (i === active ? " active" : "") +
        '" data-i="' + i + '" title="' + escAttr(focus) + '">' +
        agentAvatarHTML(a, 26) +
        '<span class="mr-main"><span class="mr-top">' +
          '<span class="mr-handle">' + esc(a.handle) + "</span>" +
          '<span class="mr-name">' + esc(a.name || "") + "</span>" +
          '<span class="mr-room">Room ' + esc(a.room) + "</span></span>" +
          (short ? '<span class="mr-focus">' + esc(short) + "</span>" : "") +
        "</span></div>";
    });
    pop.innerHTML = html;
    pop.hidden = false;
    const activeEl = pop.querySelector(".mention-row.active");
    if (activeEl && activeEl.scrollIntoView) activeEl.scrollIntoView({ block: "nearest" });
  }

  function insertMention(a) {
    if (!a) return;
    const caret = ta.selectionStart;
    const before = ta.value.slice(0, mentionStart);
    const after = ta.value.slice(caret);
    ta.value = before + a.handle + " " + after;
    const pos = (before + a.handle + " ").length;
    ta.focus(); ta.setSelectionRange(pos, pos);
    closePop();
  }

  ta.addEventListener("input", openPop);
  ta.addEventListener("click", openPop);
  ta.addEventListener("blur", () => setTimeout(closePop, 150));
  ta.addEventListener("keydown", (e) => {
    if (!pop.hidden && matches.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % matches.length; openPop(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); active = (active + matches.length - 1) % matches.length; openPop(); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertMention(matches[active]); return; }
      if (e.key === "Escape") { e.stopPropagation(); closePop(); return; }
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
  });
  pop.addEventListener("mousedown", (e) => {
    const row = e.target.closest(".mention-row");
    if (row) { e.preventDefault(); insertMention(matches[Number(row.dataset.i)]); }
  });

  async function submit() {
    const body = ta.value.trim();
    if (!body) return;
    const room = cfgc.room || S.room;
    btn.disabled = true;
    try {
      const payload = { body: body };
      const parentId = typeof cfgc.parentId === "function" ? cfgc.parentId() : cfgc.parentId;
      if (parentId) payload.parent_id = parentId;
      if (S.operator) payload.author_label = S.operator;
      const res = await api("/api/rooms/" + (typeof room === "function" ? room() : room) + "/posts",
                { method: "POST", body: JSON.stringify(payload) });
      ta.value = "";
      closePop();
      if (cfgc.onPosted) cfgc.onPosted(body, parentId || (res && res.post && res.post.id));
    } catch (e) { toast("post failed: " + e.message, "err"); }
    btn.disabled = false;
  }
  btn.addEventListener("click", submit);
}

/* ======================================================================
   thread slide-in
   ====================================================================== */

async function openThread(rootId, room) {
  const r = Number(room || S.room);
  try {
    const data = await api("/api/rooms/" + r + "/feed?thread=" + rootId);
    S.thread = { rootId: rootId, room: r, data: data };
  } catch (e) { toast("thread: " + e.message, "err"); return; }
  renderThread(true);
}

async function reloadThread() {
  if (!S.thread) return;
  try {
    S.thread.data = await api("/api/rooms/" + S.thread.room + "/feed?thread=" + S.thread.rootId);
    const after = (S.thread.data.children || []).length;
    const e = S.pending[S.thread.rootId];
    if (e && e.baseReplies != null && after > e.baseReplies) delete S.pending[S.thread.rootId];
    renderThread(false);
  } catch (_) { /* thread may be gone */ }
}
const reloadThreadDebounced = debounce(reloadThread, 350);

function closeThread() {
  const panel = document.getElementById("thread");
  const veil = document.getElementById("thread-veil");
  panel.classList.remove("open");
  setTimeout(() => { panel.hidden = true; veil.hidden = true; }, 230);
  S.thread = null;
}

function threadOrder(rootId, replies) {
  // Depth-first, so an answer sits under the question it answers.
  //
  // This rendered FLAT before, in id order, which separated a question from
  // its reply as soon as a second question was asked while the first was
  // still being answered: Q1, Q2, A1, A2 — with A1 reading as an answer to
  // Q2. The thread is a tree; render it as one.
  const byParent = new Map();
  for (const c of replies) {
    const k = c.parent_id == null ? rootId : c.parent_id;
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k).push(c);
  }
  const when = p => (p.created_at || "") + String(p.id || 0).padStart(9, "0");
  for (const list of byParent.values()) {
    list.sort((a, b) => (when(a) < when(b) ? -1 : when(a) > when(b) ? 1 : 0));
  }
  const out = [];
  const seen = new Set();
  (function walk(id, depth) {
    for (const c of byParent.get(id) || []) {
      if (seen.has(c.id)) continue;      // defensive: never loop on a cycle
      seen.add(c.id);
      out.push({ post: c, depth: depth });
      walk(c.id, depth + 1);
    }
  })(rootId, 0);
  // Anything whose parent is not in this thread still gets shown, in time
  // order, rather than silently dropped.
  for (const c of replies) {
    if (!seen.has(c.id)) out.push({ post: c, depth: 0 });
  }
  return out;
}


function renderThread(fresh) {
  const t = S.thread;
  if (!t) return;
  const panel = document.getElementById("thread");
  const veil = document.getElementById("thread-veil");
  const root = t.data.thread;
  const children = t.data.children || [];
  const expansions = children.filter(c => c.type === "expansion");
  const replies = children.filter(c => c.type !== "expansion");

  document.getElementById("thread-head").innerHTML =
    "<h2>Thread <span class='mono' style='color:var(--muted)'>#" + esc(root.id) + "</span></h2>" +
    '<span class="micro">room ' + esc(t.room) + "</span>" +
    '<button class="btn btn-sm" data-act="close-thread">Close ✕</button>';

  let html = postCardHTML(root, { feed: false });
  html += toolCallsHTML(root);
  html += claimsHTML(root);

  if (expansions.length) {
    html += '<div class="thread-sect micro">the working · ' + expansions.length +
      " expansion" + (expansions.length === 1 ? "" : "s") + "</div><div class='working'>";
    for (const c of expansions) {
      html += postCardHTML(c, { feed: false, entered: fresh ? false : !S.seenPostIds.has(c.id) });
      S.seenPostIds.add(c.id);
      html += toolCallsHTML(c);
      html += claimsHTML(c);
    }
    html += "</div>";
  }
  html += '<div class="thread-sect micro">conversation · ' + replies.length +
    " repl" + (replies.length === 1 ? "y" : "ies") + "</div>";
  if (!replies.length) {
    html += '<div class="empty">No replies yet — write below. Mention an agent ' +
      "(<span class='kbd'>@red-team</span>…) to route the question to it, from any room.</div>";
  }
  for (const node of threadOrder(root.id, replies)) {
    const c = node.post;
    const ind = node.depth > 0
      ? ' style="margin-left:' + Math.min(node.depth, 4) * 18 + 'px"' : "";
    html += '<div class="thread-node"' + ind + ">";
    html += postCardHTML(c, { feed: false, entered: fresh ? false : !S.seenPostIds.has(c.id) });
    S.seenPostIds.add(c.id);
    html += toolCallsHTML(c);
    html += "</div>";
  }
  html += pendingChipHTML(t.rootId);
  document.getElementById("thread-body").innerHTML = html;

  if (fresh || !panel.dataset.composer) {
    makeComposer(document.getElementById("thread-composer"), {
      room: () => S.thread && S.thread.room,
      parentId: () => S.thread && S.thread.rootId,
      placeholder: "Reply in this thread… @mention any agent to route the question",
      onPosted: (body) => {
        if (S.thread) {
          // +1: our own reply is already on its way into the child set, so
          // the wait only ends when something ELSE lands after it
          markPending(S.thread.rootId, body,
                      (S.thread.data.children || []).length + 1);
          renderThread(false);
        }
        reloadThread();
      },
    });
    panel.dataset.composer = "1";
  }

  veil.hidden = false; panel.hidden = false;
  // force a reflow so the slide-in transition runs; rAF is unreliable in
  // backgrounded/hidden tabs and would leave the panel offscreen
  void panel.offsetWidth;
  panel.classList.add("open");
}

function toolCallsHTML(post) {
  const tcs = post.tool_calls || [];
  if (!tcs.length) return "";
  let html = "";
  for (const tc of tcs) {
    const args = tryJson(tc.args_json, tc.args_json);
    const result = tryJson(tc.result_json, tc.result_json);
    const argsStr = typeof args === "object" ? JSON.stringify(args) : String(args || "");
    const resStr = typeof result === "object" ? JSON.stringify(result, null, 2) : String(result || "");
    html += '<details class="tc"><summary>' +
      '<span class="tc-tool">' + esc(tc.tool) + "</span>" +
      '<span class="tc-args">' + esc(argsStr.slice(0, 110)) + "</span>" +
      '<span class="tc-id">#' + esc(tc.id) + "</span>" +
      "</summary><div class='tc-detail'>" +
      '<span class="micro">args</span><pre>' + esc(typeof args === "object" ? JSON.stringify(args, null, 2) : argsStr) + "</pre>" +
      '<span class="micro">result</span><pre>' + esc(resStr.length > 4000 ? resStr.slice(0, 4000) + " …" : resStr) + "</pre>" +
      (tc.artifact_path ? '<span class="micro">artifact</span><pre>' + esc(tc.artifact_path) + "</pre>" : "") +
      '<span class="micro">recorded ' + esc(fmtTsFull(tc.ts)) + "</span>" +
      "</div></details>";
  }
  return html;
}

function claimsHTML(post) {
  const claims = post.claims || tryJson(post.claims_json, []) || [];
  if (!claims.length) return "";
  const bound = claims.filter(c => c.tool_call_id).length;
  let rows = "";
  for (const c of claims) {
    rows += '<div class="claim-row">' +
      '<span class="cv">' + esc(c.text != null ? c.text : c.value) + "</span>" +
      '<span class="ctext">' + esc(c.value != null && String(c.value) !== String(c.text) ? "= " + c.value : "") + "</span>" +
      '<span class="cid">' + (c.tool_call_id ? "&#10003; tool call #" + esc(c.tool_call_id) : "unbound") + "</span>" +
      "</div>";
  }
  return '<details class="claims-ft"><summary><span class="' +
    (bound === claims.length ? "bound" : "") + '">' + bound + "/" + claims.length +
    "</span> numeric claims bound to tool calls — expand</summary>" + rows + "</details>";
}

/* ======================================================================
   §5 — agent profile slide-out (the same surface as the thread)
   ====================================================================== */

/* Probed in order; the index of the one that answers is remembered for the
   session. The FIRST is what the server actually serves —
   `GET /api/agents/{handle}/profile` (main.py, §5): it returns
   {agent, grants, counts, posts}, which is exactly what normalizeActivity
   reads. The rest are the shapes API_ASSUMPTIONS.md guessed at before the
   route existed; they are kept as fallbacks, not as the primary. */
const PROFILE_ENDPOINTS = [
  (a) => "/api/agents/" + encodeURIComponent(
           String(a.handle || "").replace(/^@/, "")) + "/profile",
  (a) => "/api/agents/" + a.room + "/" + a.id + "/profile",
  (a) => "/api/agents/" + a.room + "/" + a.id + "/activity",
  (a) => "/api/agents/" + a.id + "/activity",
];

/* Mirrors app/agents/runtime.WEB_SEARCH_HANDLES and the names in
   app/agents/tools.TOOL_SPECS. Used ONLY while the profile endpoint is
   absent — a server-supplied `grants` always wins. */
const WEB_SEARCH_HANDLES = ["@wide-eye", "@focused", "@focused-book",
  "@rates-desk", "@credit-desk", "@equity-desk", "@lily"];
const REGISTRY_TOOLS = ["list_files", "read_file", "read_output",
  "read_assumptions", "read_book", "read_liabilities", "read_data_series",
  "recompute_vol", "verify_claim", "read_research", "read_agent_posts",
  "read_reference", "delta_normal", "read_scenario", "tail_analysis",
  "price_scenario", "query_scenarios", "run_sensitivity", "propose_rerun"];

async function openAgentProfile(agentId) {
  const a = S.agentsById[Number(agentId)];
  if (!a) return;
  // §11: an unsaved edit is never dropped silently, not even by clicking
  // another agent's avatar from inside this panel.
  if (!confirmDiscardProfileEdit()) return;
  S.profile = { agent: a, data: null, busy: true, err: null, src: null,
                mode: "view", draft: null };
  renderProfile();
  await loadAgentActivity(a);
  renderProfile();
}

async function loadAgentActivity(a) {
  // Remember WHICH endpoint answered (its index), never the resolved path —
  // a path is per-agent, so caching the string sent every later agent to the
  // first agent's URL.
  const idxs = S.profileEp != null
    ? [S.profileEp]
    : PROFILE_ENDPOINTS.map((_, i) => i);
  for (const i of idxs) {
    const p = PROFILE_ENDPOINTS[i](a);
    try {
      const d = await api(p);
      S.profileEp = i;
      if (S.profile && S.profile.agent.id === a.id) {
        S.profile.data = normalizeActivity(d, a);
        S.profile.src = p;
        S.profile.busy = false;
      }
      return;
    } catch (e) {
      if (![404, 405, 501, 422].includes(e.status)) {
        // a real error on a real endpoint — report it rather than falling back
        if (S.profile && S.profile.agent.id === a.id) {
          S.profile.err = p + ": " + e.message;
        }
      }
    }
  }
  // graceful degradation: assemble the activity from the room feeds we can
  // already read. Origin posts and suppressed posts only — replies are not
  // in the feed payload, so the counts say what they cover.
  const rows = [];
  for (const room of [1, 2, 3]) {
    let feed = S.feeds[room];
    if (!feed) {
      try { feed = await api("/api/rooms/" + room + "/feed"); S.feeds[room] = feed; }
      catch (_) { continue; }
    }
    for (const p of (feed.posts || [])) if (p.agent_id === a.id) rows.push(p);
    for (const p of (feed.suppressed || [])) if (p.agent_id === a.id) rows.push(p);
  }
  rows.sort((x, y) => (y.id || 0) - (x.id || 0));
  if (!S.profile || S.profile.agent.id !== a.id) return;
  S.profile.data = {
    posts: rows,
    counts: {
      published: rows.filter(p => p.status !== "suppressed").length,
      suppressed: rows.filter(p => p.status === "suppressed").length,
      quiet: rows.filter(p => p.significance === "quiet").length,
      tool_calls: null,
    },
    grants: derivedGrants(a),
    derived: true,
  };
  S.profile.busy = false;
  S.profile.src = null;
}

function derivedGrants(a) {
  return {
    web_search: WEB_SEARCH_HANDLES.includes(String(a.handle).toLowerCase()),
    tools: REGISTRY_TOOLS.slice(),
    derived: true,
  };
}

function normalizeActivity(d, a) {
  const src = d && d.activity ? d.activity : (d || {});
  const posts = src.posts || d.posts || [];
  const counts = src.counts || d.counts || {};
  const grants = src.grants || d.grants ||
    (d.agent && (d.agent.grants || d.agent.tool_grants)) || null;
  return {
    // the server's own agent row — richer than the roster row the client
    // holds: `modified` (§11), home_room and also_posts_in (§13)
    agent: (d && d.agent) || null,
    posts: posts.slice().sort((x, y) => (y.id || 0) - (x.id || 0)),
    counts: {
      published: counts.published != null ? counts.published
        : posts.filter(p => p.status !== "suppressed").length,
      suppressed: counts.suppressed != null ? counts.suppressed
        : posts.filter(p => p.status === "suppressed").length,
      quiet: counts.quiet != null ? counts.quiet
        : posts.filter(p => p.significance === "quiet").length,
      tool_calls: counts.tool_calls != null ? counts.tool_calls : null,
    },
    grants: grants || derivedGrants(a),
    sources: (d && d.sources) || [],
    research_notes: (d && d.research_notes) || [],
    derived: false,
  };
}

/* The stored persona_prompt already has the _CITE and _STYLE blocks
   concatenated onto it (personas/__init__.py appends them at seed time), so
   the profile splits them back out: the agent's own voice above, the
   boilerplate every agent receives below, greyed. */
const PERSONA_APPENDED_MARKERS = [
  "HARD RULE: every numeric claim",
  "HOUSE STYLE, binding:",
];

function splitPersona(prompt) {
  const s = String(prompt || "");
  let cut = -1;
  for (const m of PERSONA_APPENDED_MARKERS) {
    const i = s.indexOf(m);
    if (i >= 0 && (cut < 0 || i < cut)) cut = i;
  }
  if (cut < 0) return { body: s, appended: "" };
  return { body: s.slice(0, cut).trim(), appended: s.slice(cut).trim() };
}

/* §11: the panel never closes over an unsaved edit without asking. */
function confirmDiscardProfileEdit() {
  if (!profileDirty()) return true;
  const h = S.profile && S.profile.agent ? S.profile.agent.handle : "this agent";
  return window.confirm("Unsaved changes to " + h +
    " will be lost. Discard them?");
}

function closeProfile(force) {
  if (!force && !confirmDiscardProfileEdit()) return;
  const panel = document.getElementById("profile");
  const veil = document.getElementById("profile-veil");
  panel.classList.remove("open");
  setTimeout(() => { panel.hidden = true; veil.hidden = true; }, 230);
  S.profile = null;
}

function grantChips(a, g) {
  const web = g && g.web_search;
  let html = '<span class="grant' + (web ? " on" : "") + '">web_search ' +
    (web ? "granted" : "not granted") + "</span>";
  for (const t of (g && g.tools) || []) {
    html += '<span class="grant tool">' + esc(t) + "</span>";
  }
  return html;
}

/* One surface, two modes (§11): `view` is the read-only page; `edit` turns
   the SAME page into inputs. There is no separate edit modal any more. */
function renderProfile() {
  const P = S.profile;
  if (!P) return;
  document.getElementById("profile-head").innerHTML = profileHeadHTML(P);
  document.getElementById("profile-body").innerHTML =
    P.mode === "edit" ? profileEditHTML(P) : profileViewHTML(P);
  if (P.mode === "edit") bindProfileEdit();
  const veil = document.getElementById("profile-veil");
  const panel = document.getElementById("profile");
  veil.hidden = false; panel.hidden = false;
  void panel.offsetWidth;
  panel.classList.add("open");
}

/* The server's own agent row when the profile endpoint answered (it carries
   `modified`, `home_room`, `also_posts_in`); the roster row otherwise. */
function profileRecord(P) {
  return (P.data && P.data.agent) || P.agent;
}

function profileHeadHTML(P) {
  const a = P.agent;
  const rec = profileRecord(P);
  if (P.mode === "edit") {
    return "<h2>" + esc(a.handle) + "</h2>" +
      '<span class="micro">editing</span>' +
      '<span class="micro pe-dirty" id="pe-dirty"></span>' +
      '<button class="btn btn-sm" data-act="profile-cancel">Cancel</button>' +
      '<button class="btn btn-sm btn-primary" data-act="profile-save"' +
        (P.saving ? " disabled" : "") + ">" +
        (P.saving ? "Saving…" : "Save") + "</button>";
  }
  return "<h2>" + esc(a.handle) + "</h2>" +
    '<span class="micro">room ' + esc(a.room) + "</span>" +
    (rec && rec.modified
      ? '<span class="mod-mark" title="a builtin whose shipped persona has ' +
        'been edited here">modified</span>' : "") +
    '<button class="btn btn-sm btn-primary" data-act="profile-edit" data-id="' +
      a.id + '">Edit</button>' +
    '<button class="btn btn-sm" data-act="close-profile">Close ✕</button>';
}

/* §13: one agent, several rooms — the profile is the one place that says so,
   and the one history that covers all of them. */
function alsoPostsIn(a) {
  const v = tryJson(a && a.also_posts_in, null);
  return Array.isArray(v) ? v.map(Number).filter(n => [1, 2, 3].includes(n)) : [];
}

function roomsLine(a) {
  const also = alsoPostsIn(a);
  return "room <b>" + esc(a.room) + "</b>" +
    (also.length ? " &nbsp;·&nbsp; also posts in <b>" +
      esc(also.join(", ")) + "</b>" : "");
}

/* The WORKING behind one post: every tool call the agent made for it, with
   its arguments and what came back, plus the claims and what each bound to.
   This is the answer to "where did that number come from", and it belongs
   on the agent's own page rather than a click away inside the thread. */
function profWorkingHTML(p) {
  const tcs = p.tool_calls || [];
  const claims = p.claims || [];
  if (!tcs.length && !claims.length) return "";
  let h = '<details class="pr-working"><summary>the working · ' +
    tcs.length + " tool call" + (tcs.length === 1 ? "" : "s") +
    (claims.length ? " · " + claims.length + " claim" +
      (claims.length === 1 ? "" : "s") : "") + "</summary>";
  for (const t of tcs) {
    h += '<div class="pw-tc"><span class="pw-name">' + esc(t.name) +
      '</span> <span class="pw-args">' + esc(String(t.args || "").slice(0, 160)) +
      '</span> <span class="pw-id">#' + esc(t.id) + "</span>";
    if (t.result) {
      h += '<div class="pw-res">' + esc(String(t.result).slice(0, 300)) + "</div>";
    }
    h += "</div>";
  }
  for (const c of claims) {
    const src = c.source_url
      ? '<a href="' + esc(c.source_url) + '" target="_blank" rel="noopener ' +
        'noreferrer nofollow">source</a>'
      : (c.tool_call_id ? "tool call #" + esc(c.tool_call_id) : "unbound");
    h += '<div class="pw-claim">' + esc(c.text || "") +
      ' <span class="pw-from">&#8592; ' + src + "</span></div>";
  }
  return h + "</details>";
}


function profileViewHTML(P) {
  const a = P.agent;
  const rec = profileRecord(P);
  const av = resolveAvatar(a);
  const d = P.data;

  const reads = tryJson(a.reads_from, null);
  let html =
    '<div class="prof-hd">' + avatarSVG(av, 72) +
    '<div class="prof-id"><div class="p-handle">' + esc(a.handle) + "</div>" +
    '<div class="p-name">' + esc(a.name || "—") + "</div>" +
    '<div class="p-meta">' + roomsLine(a) + " &nbsp;·&nbsp; outlook <b>" +
      esc(a.outlook || "internal") + "</b> &nbsp;·&nbsp; " +
      (a.builtin ? "builtin" : "custom") +
      (rec && rec.modified ? " &nbsp;·&nbsp; <b>modified</b>" : "") + "</div>" +
    (Array.isArray(reads) && reads.length
      ? '<div class="p-meta">reads_from ' + esc(reads.join(", ")) + "</div>" : "") +
    "</div></div>";

  // The agent's own research notes — the documents its room posts are
  // drawn from. Reading one answers "what is this post actually based on".
  const notes = (P.data && P.data.research_notes) || [];
  if (notes.length) {
    html += '<div class="prof-sect micro">research notes · ' + notes.length +
      "</div><ul class=\"prof-sources\">";
    for (const n of notes) {
      html += '<li><a href="/api/research?asof=' + encodeURIComponent(n.month) +
        "&agent=" + encodeURIComponent(n.agent) +
        '" target="_blank" rel="noopener">' + esc(n.file) + "</a></li>";
    }
    html += "</ul>";
  }

  // SOURCES. For a research agent this list is its provenance: figures it
  // read on the web stand on these the way engine figures stand on a tool
  // call, so they have to be openable from the agent's own page.
  const srcs = (P.data && P.data.sources) || [];
  if (srcs.length) {
    html += '<div class="prof-sect micro">sources · ' + srcs.length + "</div>";
    html += '<ul class="prof-sources">';
    for (const u of srcs) {
      let label = u;
      try { label = new URL(u).hostname.replace(/^www\./, ""); } catch (e) {}
      html += '<li><a href="' + esc(u) + '" target="_blank" rel="noopener ' +
        'noreferrer nofollow" title="' + esc(u) + '">' + esc(label) +
        "</a></li>";
    }
    html += "</ul>";
  }

  if (rec && rec.modified) {
    html += '<div class="gov-note mod-note">This builtin has been edited here, ' +
      "so its persona differs from the one shipped in <code>AGENT-PROMPTS.md</code>. " +
      "A changed prompt is never invisible.</div>";
  }

  html += '<div class="prof-grants">' + grantChips(a, d && d.grants) + "</div>";
  if (d && d.grants && d.grants.derived) {
    html += '<div class="gov-note">grants shown from the client-side mirror of ' +
      "<code>runtime.WEB_SEARCH_HANDLES</code> and <code>tools.TOOL_SPECS</code> — " +
      "<code>GET /api/agents/{handle}/profile</code> did not answer.</div>";
  }

  html += '<div class="prof-sect micro">focus — what the @-mention menu shows</div>' +
    '<div class="prof-focus">' + esc(a.focus || "—") + "</div>";

  const persona = splitPersona(a.persona_prompt);
  html += '<div class="prof-sect micro">persona prompt · read-only</div>' +
    '<pre class="prof-prompt">' + esc(persona.body || "—") + "</pre>";
  if (persona.appended) {
    html += '<div class="micro" style="margin:8px 0 4px">appended to every ' +
      "agent automatically (<code>_CITE</code> + <code>_STYLE</code>)</div>" +
      '<pre class="prof-prompt appended">' + esc(persona.appended) + "</pre>";
  }
  html += '<div class="gov-note">Editing happens on this page — press ' +
    "<b>Edit</b> above and these fields become inputs. " +
    '<button class="btn btn-sm" data-act="profile-edit" data-id="' + a.id +
    '">Edit this agent</button></div>';

  const c = (d && d.counts) || {};
  html += '<div class="prof-sect micro">counts</div><div class="prof-counts">' +
    profCount("published", c.published) +
    profCount("suppressed", c.suppressed) +
    profCount("quiet", c.quiet) +
    profCount("tool calls", c.tool_calls) +
    "</div>";
  if (d && d.derived) {
    html += '<div class="gov-note">assembled from the three room feeds: origin ' +
      "posts and suppressed posts. Replies inside threads and the tool-call " +
      "total come from <code>GET /api/agents/{handle}/profile</code>, which " +
      "did not answer.</div>";
  }

  const also = alsoPostsIn(a);
  html += '<div class="prof-sect micro">posts · newest first, every room</div>' +
    (also.length
      ? '<div class="gov-note">One agent, one history: every post it made in ' +
        "room " + esc(a.room) + " and room " + esc(also.join(", ")) +
        " is here — which is the point of merging the handles.</div>"
      : "");
  if (P.busy) {
    html += '<div class="empty">Loading this agent’s posts…</div>';
  } else if (P.err) {
    html += '<div class="empty">Could not load activity (' + esc(P.err) + ").</div>";
  } else if (!d || !d.posts.length) {
    html += '<div class="empty">No posts yet. Run this room’s pass from the ' +
      "stage strip in the right rail.</div>";
  } else {
    for (const p of d.posts) {
      const rootId = p.thread_id || p.parent_id || p.id;
      html += '<div class="prof-post" data-act="profile-open-thread" data-id="' +
        esc(rootId) + '" data-room="' + esc(p.room || a.room) + '">' +
        '<div class="pr-top"><span class="pr-room">room ' + esc(p.room || a.room) +
        "</span>" +
        (p.status === "suppressed" ? '<span class="pr-supp">suppressed</span>' : "") +
        (p.significance ? '<span class="pr-sig">' + esc(p.significance) + "</span>" : "") +
        (p.run_id && S.runsById[p.run_id]
          ? '<span class="pr-run">' + esc(runLabel(S.runsById[p.run_id])) + "</span>" : "") +
        '<span class="pr-ts">' + esc(fmtTsFull(p.created_at)) + "</span></div>" +
        '<div class="pr-body">' + esc(String(p.body_md || "")) + "</div>" +
        (p.suppression_reason
          ? '<div class="pr-supp-why">suppressed — ' +
            esc(p.suppression_reason) + "</div>" : "") +
        profWorkingHTML(p) +
        '<div class="pr-go">open thread &#8594;</div></div>';
    }
  }
  return html;
}

/* ---- §11 edit mode: the same page, fields become inputs ---------------- */

const OUTLOOKS = ["internal", "outward", "both"];

function normAv(av) {
  return {
    bg: String(av.bg || ""), fg: String(av.fg || ""),
    glyph: String(av.glyph == null ? "" : av.glyph).slice(0, 2),
    accessory: av.accessory === "horns" ? "horns" : "none",
    horn_color: av.accessory === "horns" ? String(av.horn_color || av.fg || "") : "",
  };
}

function startProfileEdit() {
  const P = S.profile;
  if (!P) return;
  const a = P.agent;
  const persona = splitPersona(a.persona_prompt);
  P.mode = "edit";
  P.saveErr = null;
  P.saving = false;
  P.draft = {
    name: a.name || "",
    focus: a.focus || "",
    persona: persona.body,
    appended: persona.appended,
    outlook: a.outlook || "internal",
    av: Object.assign({}, resolveAvatar(a)),
  };
  renderProfile();
}

function cancelProfileEdit() {
  const P = S.profile;
  if (!P) return;
  if (!confirmDiscardProfileEdit()) return;
  P.mode = "view"; P.draft = null; P.saveErr = null; P.saving = false;
  renderProfile();
}

/* Dirty = the draft differs from the row it was opened from. Cancel and
   every close path ask this before throwing work away. */
function profileDirty() {
  const P = S.profile;
  if (!P || P.mode !== "edit" || !P.draft) return false;
  const a = P.agent, d = P.draft;
  if (d.name !== (a.name || "")) return true;
  if (d.focus !== (a.focus || "")) return true;
  if (d.persona !== splitPersona(a.persona_prompt).body) return true;
  if (d.outlook !== (a.outlook || "internal")) return true;
  return JSON.stringify(normAv(d.av)) !== JSON.stringify(normAv(resolveAvatar(a)));
}

function profileEditHTML(P) {
  const a = P.agent;
  const d = P.draft;
  let html =
    '<div class="pe-preview-row"><span id="pe-preview">' +
      avatarSVG(d.av, 32) + avatarSVG(d.av, 20) + avatarSVG(d.av, 64) +
    "</span>" +
    '<div class="pe-preview-meta"><div class="p-handle">' + esc(a.handle) +
    '</div><div class="p-name">live preview · 32px, 20px, 64px</div></div></div>';

  html += '<div class="field"><label>handle · immutable</label>' +
    '<input type="text" class="pe-handle" value="' + escAttr(a.handle) +
    '" disabled>' +
    '<div class="pe-note">Mentions and every post already written point at ' +
    "the handle, so it never changes.</div></div>";

  html += '<div class="prof-sect micro">persona prompt</div>' +
    '<div class="pe-note" style="margin-bottom:6px">The field that actually ' +
    "matters: voice and remit. What you write here is what the next pass reads.</div>" +
    '<textarea class="pe-prompt" id="pe-prompt" rows="18" spellcheck="false">' +
    esc(d.persona) + "</textarea>";
  if (d.appended) {
    html += '<div class="micro" style="margin:10px 0 4px">appended to every ' +
      "agent automatically (<code>_CITE</code> + <code>_STYLE</code>) — not editable</div>" +
      '<pre class="prof-prompt appended">' + esc(d.appended) + "</pre>";
  }

  html += '<div class="prof-sect micro">identity</div>' +
    '<div class="field"><label>name</label><input type="text" id="pe-name" value="' +
    escAttr(d.name) + '"></div>' +
    '<div class="field"><label>focus</label><input type="text" id="pe-focus" value="' +
    escAttr(d.focus) + '">' +
    '<div class="pe-note">This is the line the @-mention dropdown shows.</div></div>' +
    '<div class="field"><label>outlook</label><select id="pe-outlook">' +
    OUTLOOKS.map(o => '<option value="' + o + '"' +
      (d.outlook === o ? " selected" : "") + ">" + o + "</option>").join("") +
    "</select></div>";

  html += '<div class="prof-sect micro">avatar</div>' +
    '<div class="av-controls pe-av">' +
      '<div><label>bg</label><input type="color" id="pe-bg" value="' +
        escAttr(d.av.bg) + '"></div>' +
      '<div><label>fg</label><input type="color" id="pe-fg" value="' +
        escAttr(d.av.fg) + '"></div>' +
      '<div><label>glyph (1-2)</label><input type="text" id="pe-glyph" maxlength="2" value="' +
        escAttr(d.av.glyph || "") + '"></div>' +
      '<div><label>horns</label><div class="pe-horns">' +
        '<input type="checkbox" id="pe-horns"' +
          (d.av.accessory === "horns" ? " checked" : "") + ">" +
        '<input type="color" id="pe-horncolor" title="horn colour" value="' +
        escAttr(d.av.horn_color || d.av.fg) + '"></div></div>' +
    "</div>";

  const g = (P.data && P.data.grants) || {};
  html += '<div class="prof-sect micro">tool grants · not editable here</div>' +
    '<div class="prof-grants">' + grantChips(a, g) + "</div>" +
    '<div class="pe-note">Grants are server constants ' +
    "(<code>runtime.WEB_SEARCH_HANDLES</code>, <code>tools.TOOL_SPECS</code>), " +
    "not per-agent fields — shown so you can see what this prompt is written " +
    "against.</div>";

  html += '<div class="pe-actions">' +
    '<button class="btn" data-act="profile-cancel">Cancel</button>' +
    '<button class="btn btn-primary" data-act="profile-save"' +
      (P.saving ? " disabled" : "") + ">" +
      (P.saving ? "Saving…" : "Save") + "</button></div>";
  if (P.saveErr) html += '<div class="pe-err">' + esc(P.saveErr) + "</div>";
  return html;
}

/* Inputs write straight into the draft; nothing here re-renders the page,
   because re-rendering under a cursor is how an edit surface eats a
   keystroke. Only the preview and the dirty marker repaint. */
function bindProfileEdit() {
  const P = S.profile;
  const root = document.getElementById("profile-body");
  if (!P || !P.draft || !root) return;
  const $ = (sel) => root.querySelector(sel);

  function paint() {
    const el = $("#pe-preview");
    if (el) {
      el.innerHTML = avatarSVG(P.draft.av, 32) + avatarSVG(P.draft.av, 20) +
        avatarSVG(P.draft.av, 64);
    }
    const dirty = document.getElementById("pe-dirty");
    if (dirty) dirty.textContent = profileDirty() ? "unsaved changes" : "";
  }
  const on = (sel, ev, fn) => {
    const el = $(sel);
    if (el) el.addEventListener(ev, (e) => { fn(e); paint(); });
  };
  on("#pe-name", "input", (e) => { P.draft.name = e.target.value; });
  on("#pe-focus", "input", (e) => { P.draft.focus = e.target.value; });
  on("#pe-prompt", "input", (e) => { P.draft.persona = e.target.value; });
  on("#pe-outlook", "change", (e) => { P.draft.outlook = e.target.value; });
  on("#pe-bg", "input", (e) => { P.draft.av.bg = e.target.value; });
  on("#pe-fg", "input", (e) => { P.draft.av.fg = e.target.value; });
  on("#pe-glyph", "input", (e) => {
    P.draft.av.glyph = String(e.target.value).slice(0, 2);
  });
  on("#pe-horns", "change", (e) => {
    P.draft.av.accessory = e.target.checked ? "horns" : "none";
    const hc = $("#pe-horncolor");
    if (e.target.checked && hc) P.draft.av.horn_color = hc.value;
  });
  on("#pe-horncolor", "input", (e) => { P.draft.av.horn_color = e.target.value; });
  paint();
}

/* Save PATCHes and returns to VIEW mode with the panel still open (§11). */
async function saveProfileEdit() {
  const P = S.profile;
  if (!P || P.mode !== "edit" || P.saving) return;
  const a = P.agent, d = P.draft;
  const av = { bg: d.av.bg, fg: d.av.fg,
               glyph: String(d.av.glyph == null ? "" : d.av.glyph).slice(0, 2),
               accessory: d.av.accessory === "horns" ? "horns" : "none" };
  if (av.accessory === "horns") av.horn_color = d.av.horn_color || d.av.fg;
  // the stored prompt is persona + _CITE + _STYLE joined by one space —
  // rejoin exactly, or saving an edit would silently drop the two blocks
  // every agent is supposed to receive
  const persona = d.appended
    ? (d.persona.trim() + " " + d.appended).trim()
    : d.persona.trim();
  const fields = {
    name: d.name.trim() || null,
    focus: d.focus.trim() || null,
    persona_prompt: persona || null,
    outlook: d.outlook,
    avatar_json: JSON.stringify(av),
  };
  P.saving = true; P.saveErr = null;
  renderProfile();
  try {
    const res = await api("/api/agents/" + a.room + "/" + a.id,
      { method: "PATCH", body: JSON.stringify(fields) });
    if (res && res.agent) Object.assign(a, res.agent);
    await finishProfileSave(a, a.handle + " saved");
  } catch (e) {
    if ([404, 405, 501].includes(e.status)) {
      Object.assign(a, { name: fields.name, focus: fields.focus,
        persona_prompt: fields.persona_prompt, outlook: fields.outlook,
        avatar_json: fields.avatar_json });
      S.agentsById[a.id] = a;
      toast("PATCH /api/agents not available (" + e.status +
        ") — change applied locally for this session only", "err");
      P.saving = false; P.mode = "view"; P.draft = null;
      renderProfile(); renderAll();
      return;
    }
    P.saving = false;
    P.saveErr = "save failed: " + e.message;
    renderProfile();
  }
}

async function finishProfileSave(a, msg) {
  const P = S.profile;
  await refreshAgents();
  const fresh = S.agentsById[a.id];
  if (P && P.agent && P.agent.id === a.id) {
    if (fresh) P.agent = fresh;
    P.mode = "view"; P.draft = null; P.saving = false;
    renderProfile();
    // re-read the profile so the "modified" marker and the counts come from
    // the server rather than from what we just sent it
    await loadAgentActivity(P.agent);
    if (S.profile === P) renderProfile();
  }
  toast(msg, "good");
  renderAll();
  if (S.thread) reloadThread();
}

function profCount(label, v) {
  return '<div class="pc"><span class="pc-v">' +
    esc(v == null ? "—" : v) + '</span><span class="pc-k">' + esc(label) +
    "</span></div>";
}

/* ======================================================================
   dashboard (left panel) — numbers straight from engine outputs
   ====================================================================== */

function renderDash() {
  const el = document.getElementById("dash");
  if (S.view === "research") {
    el.innerHTML = '<div class="card">Research runs <b>first</b> in a cycle — ' +
      "research &#8594; room 1 &#8594; room 2 &#8594; room 3 — because rooms 1 " +
      "and 3 write their posts against these reports.<br><br>" +
      "The numbers in them are computed directly from " +
      "<code>data/processed/*.csv</code> — never from assumptions or engine " +
      "outputs. An assumptions-vs-research mismatch is evidence of an error " +
      "between them.</div>" +
      '<div class="card"><b>@focused</b> — the standing focused-risk set, the ' +
      "same list every month so month-on-month comparison means something." +
      "<br><br><b>@wide-eye</b> — the world around our factor set, and what " +
      "we cannot currently price.</div>";
    return;
  }
  const d = S.dash[S.room];
  const curr = S.runsById[S.currId];
  let html = "";
  if (!curr) {
    html += '<div class="empty">No active run.<br>Start one from room <b>2</b> ' +
      "— <b>Run model</b>.</div>";
    el.innerHTML = html; return;
  }
  if (!d) {
    // Room 2's stage timeline streams over SSE independently of the
    // dashboard fetch — never hide live progress behind a loading state.
    if (S.room === 2) {
      html += dashRoom2HTML({}, curr);
    } else {
      html += '<div class="empty">Loading… (' + esc(runLabel(curr)) + " " +
        esc(curr.status) + ")</div>";
    }
    el.innerHTML = html; return;
  }
  const v = d.current && d.current.valuation;

  if (!v) {
    html += '<div class="empty">' + esc(runLabel(curr)) + " is <b>" + esc(curr.status) +
      "</b> — outputs appear here when the engine finishes." +
      (S.room !== 2 ? " Watch room <b>2</b> for live stage events." : "") + "</div>";
  } else {
    html += headlineListHTML(d);
  }

  if (S.room === 1) html += dashRoom1HTML(d);
  if (S.room === 2) html += dashRoom2HTML(d, curr);
  if (S.room === 3) html += dashRoom3HTML(d);
  if (S.room === 2 || S.room === 3) html += scenarioSectionHTML();

  html += '<div class="gov-note">numbers straight from engine outputs — ' +
    "no AI in this path</div>";
  el.innerHTML = html;
  bindDash(el);
}

/* ---- P: one aligned list. Label / value / change vs previous. ---------- */

function hlRow(label, value, change, opts) {
  opts = opts || {};
  const cls = ["hl-row"];
  if (opts.child) cls.push("child");
  if (opts.lead) cls.push("lead");
  if (opts.act) cls.push("clickable");
  return '<div class="' + cls.join(" ") + '"' +
    (opts.act ? ' data-act="' + escAttr(opts.act) + '"' : "") +
    (opts.title ? ' title="' + escAttr(opts.title) + '"' : "") + ">" +
    '<span class="hl-label">' + esc(label) + "</span>" +
    '<span class="hl-val">' + esc(value) + "</span>" +
    '<span class="hl-chg ' + (opts.chgCls || "") + '">' + esc(change || "") + "</span>" +
    "</div>";
}

function chgMoney(curr, prev, invert) {
  if (curr == null || prev == null) return { text: "", cls: "" };
  const dv = curr - prev;
  if (Math.abs(dv) < 1e-6) return { text: "—", cls: "" };
  const good = invert ? dv < 0 : dv > 0;
  return { text: fmtSignedMoney(dv), cls: good ? "up" : "down" };
}

function chgPp(curr, prev, invert) {
  if (curr == null || prev == null) return { text: "", cls: "" };
  const dv = (curr - prev) * 100;
  if (Math.abs(dv) < 0.005) return { text: "—", cls: "" };
  const good = invert ? dv < 0 : dv > 0;
  return { text: (dv >= 0 ? "+" : "") + dv.toFixed(1) + "pp", cls: good ? "up" : "down" };
}

function headlineListHTML(d) {
  const v = d.current && d.current.valuation;
  const agg = d.current && d.current.var_aggregate;
  const blocks = d.current && d.current.var_blocks && d.current.var_blocks.blocks;
  const pv = d.previous && d.previous.valuation;
  const pagg = d.previous && d.previous.var_aggregate;
  const pblocks = d.previous && d.previous.var_blocks && d.previous.var_blocks.blocks;
  if (!v) return "";

  const aggv = agg && agg.aggregate_var_gbp;
  const paggv = pagg && pagg.aggregate_var_gbp;
  let html = '<div class="hl">';

  let c = chgMoney(v.asset_total_gbp, pv && pv.asset_total_gbp);
  html += hlRow("Assets", fmtMoney(v.asset_total_gbp), c.text, { chgCls: c.cls });
  c = chgMoney(v.liability_pv_gbp, pv && pv.liability_pv_gbp, true);
  html += hlRow("Liabilities", fmtMoney(v.liability_pv_gbp), c.text, { chgCls: c.cls });
  c = chgMoney(v.surplus_gbp, pv && pv.surplus_gbp);
  html += hlRow("Surplus", fmtMoney(v.surplus_gbp), c.text, { chgCls: c.cls, lead: true });

  if (aggv != null) {
    html += '<div class="hl-rule"></div>';
    c = chgMoney(aggv, paggv, true);
    html += hlRow("VaR  99.5% · 1y", fmtMoney(aggv), c.text, {
      chgCls: c.cls, act: "open-scenario",
      title: "what actually happens in this scenario?" });
    if (blocks) {
      for (const k of Object.keys(blocks)) {
        const cc = chgMoney(blocks[k], pblocks ? pblocks[k] : null, true);
        html += hlRow(BLOCK_LABELS[k] || k, fmtMoney(blocks[k]), cc.text,
                      { child: true, chgCls: cc.cls });
      }
    }
  }

  const ratios = [];
  if (aggv != null && v.asset_total_gbp) {
    const r = aggv / v.asset_total_gbp;
    const pr = (paggv != null && pv && pv.asset_total_gbp)
      ? paggv / pv.asset_total_gbp : null;
    const cc = chgPp(r, pr, true);
    ratios.push(hlRow("VaR / assets", fmtPct(r, 1), cc.text, { chgCls: cc.cls }));
  }
  if (aggv != null && v.surplus_gbp) {
    const r = aggv / v.surplus_gbp;
    const pr = (paggv != null && pv && pv.surplus_gbp)
      ? paggv / pv.surplus_gbp : null;
    const cc = chgPp(r, pr, true);
    ratios.push(hlRow("VaR / surplus", fmtPct(r, 1), cc.text, { chgCls: cc.cls }));
  }
  if (agg && agg.diversification_benefit_gbp != null) {
    ratios.push(hlRow("Diversification", fmtMoney(agg.diversification_benefit_gbp),
      agg.diversification_ratio != null ? fmtPct(agg.diversification_ratio, 1) : ""));
  }
  if (ratios.length) html += '<div class="hl-rule"></div>' + ratios.join("");
  return html + "</div>";
}

function dashRoom1HTML(d) {
  const a = d.assumptions;
  if (!a) return '<div class="card">Assumptions not readable for this run yet.</div>';
  let html = "";
  const curves = a.curves || {};
  const names = ["gbp_swap", "gbp_gilt", "ust"];
  let rows = "";
  for (const t of [2, 5, 10, 20]) {
    rows += "<tr><td class='lbl mono'>" + t + "y</td>" + names.map(n => {
      const c = curves[n] || {};
      const val = c[t] != null ? c[t] : c[String(t)];
      return '<td class="num">' + esc(val != null ? fmtPct(val, 2) : "—") + "</td>";
    }).join("") + "</tr>";
  }
  html += '<details class="sect" open><summary><span class="micro">input curves (zero, %)</span></summary><div class="sect-body">' +
    '<table class="dt"><tr><th></th>' + names.map(n => '<th class="num">' + esc(n.replace("gbp_", "")) + "</th>").join("") + "</tr>" +
    rows + "</table></div></details>";

  const sp = a.spreads || {};
  html += '<details class="sect" open><summary><span class="micro">spread levels (5y-equiv)</span></summary><div class="sect-body">' +
    '<table class="dt"><tr>' + Object.keys(sp).map(k => '<th class="num">' + esc(k) + "</th>").join("") + "</tr><tr>" +
    Object.keys(sp).map(k => '<td class="num">' + esc(fmtBp(sp[k], 0)) + "</td>").join("") + "</tr></table></div></details>";

  const eq = a.equity || {}, fx = a.fx || {};
  html += '<details class="sect"><summary><span class="micro">equity · fx levels</span></summary><div class="sect-body"><table class="dt">' +
    Object.keys(eq).map(k => "<tr><td class='lbl'>" + esc(k) + '</td><td class="num">' + esc(fmtNum(eq[k], 1)) + "</td></tr>").join("") +
    Object.keys(fx).map(k => "<tr><td class='lbl'>" + esc(k) + '</td><td class="num">' + esc(fmtNum(fx[k], 4)) + "</td></tr>").join("") +
    "</table></div></details>";

  const vols = a.vols || {};
  let volRows = "";
  for (const n of names) {
    const vv = vols[n] || {};
    volRows += "<tr><td class='lbl mono'>" + esc(n) + "</td>" + [2, 5, 10, 20].map(t => {
      const val = vv[t] != null ? vv[t] : vv[String(t)];
      return '<td class="num">' + esc(val != null ? fmtBp(val, 0) : "—") + "</td>";
    }).join("") + "</tr>";
  }
  html += '<details class="sect"><summary><span class="micro">rate vols (annualised, bp)</span></summary><div class="sect-body">' +
    '<table class="dt"><tr><th></th><th class="num">2y</th><th class="num">5y</th><th class="num">10y</th><th class="num">20y</th></tr>' +
    volRows + "</table>" +
    '<div class="gov-note">window ' + esc(a.meta && a.meta.calibration_window_days) +
    "d · equity/fx vols are proportional-return vols</div></div></details>";
  return html;
}

function dashRoom2HTML(d, curr) {
  let html = "";
  const man = d.current && d.current.manifest;
  const mark = runStatusMark(curr);
  html += '<div class="card"><span class="micro">active run</span>' +
    '<table class="dt">' +
    "<tr><td class='lbl'>run</td><td class='num'>" + esc(runLabel(curr)) +
      (curr.seeded ? " <span class='seed-chip'>seeded</span>" : "") + "</td></tr>" +
    "<tr><td class='lbl'>asof</td><td class='num'>" + esc(curr.asof) + "</td></tr>" +
    "<tr><td class='lbl'>status</td><td class='num'><span class='status-chip " + esc(mark.cls) + "'>" + esc(mark.word) + "</span></td></tr>" +
    "<tr><td class='lbl'>kind</td><td class='num'>" + esc(curr.kind) +
      (curr.parent_run_id && S.runsById[curr.parent_run_id]
        ? " of " + esc(runLabel(S.runsById[curr.parent_run_id])) : "") + "</td></tr>" +
    "<tr><td class='lbl'>seed / sims</td><td class='num'>" + esc(curr.seed) + " / " + esc(fmtNum(curr.sims, 0)) + "</td></tr>" +
    (man ? "<tr><td class='lbl'>assumptions</td><td class='num' title='" + escAttr(man.assumptions_path || "") + "'>" +
      esc(String(man.assumptions_path || "—").split(/[\\/]/).pop()) + "</td></tr>" +
      "<tr><td class='lbl'>book</td><td class='num' title='" + escAttr(man.book_path || "") + "'>" +
      esc(String(man.book_path || "—").split(/[\\/]/).pop()) + "</td></tr>" +
      (man.adjustment_changes ? "<tr><td class='lbl'>adjusted</td><td class='num'>" +
        esc(man.adjustment_changes.map(c => c.path).join(", ")) + "</td></tr>" : "") : "") +
    "</table></div>";

  const evs = Object.values(S.stage2).filter(ev => ev.run_id === curr.id)
    .sort((x, y) => x.id - y.id);
  html += '<div class="card"><span class="micro">stage events · live via SSE</span>';
  if (!evs.length) {
    html += '<div class="empty" style="margin:6px 0">No stage events yet — they stream ' +
      "in the moment the engine starts.</div>";
  } else {
    for (const ev of evs) {
      const entered = !S.seenStageIds.has(ev.id);
      S.seenStageIds.add(ev.id);
      html += '<div class="stage-line ' + esc(ev.status) + (entered ? " enter" : "") + '">' +
        '<span class="st-dot"></span>' +
        '<span class="st-stage">' + esc(ev.stage) + "</span>" +
        '<span class="st-status">' + esc(ev.status) + "</span>" +
        '<span class="st-ts">' + esc(fmtTs(ev.ts)) + "</span></div>";
    }
    if (runStopped(curr)) {
      html += '<div class="gov-note">stopped — the partial stage record above is ' +
        "kept deliberately.</div>";
    }
  }
  html += "</div>";
  return html;
}

function dashRoom3HTML(d) {
  let html = "";
  if (d.attribution) {
    html += waterfallCardHTML(d.attribution);
  } else if (S.prevId) {
    html += '<div class="card"><span class="micro">attribution</span>' +
      '<div class="empty" style="margin:6px 0">No committed attribution outputs for this pair.</div></div>';
  }
  const tops = d.current && d.current.top_positions_by_var;
  if (tops && tops.length) {
    let rows = "";
    for (const p of tops) {
      rows += "<tr><td class='lbl mono'>" + esc(p.position_id || p.id || "") + "</td>" +
        "<td class='lbl' style='max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='" +
        escAttr(p.name || "") + "'>" + esc(p.name || "") + "</td>" +
        '<td class="num">' + esc(fmtMoney(p.var_99_5_1y_gbp)) + "</td></tr>";
    }
    html += '<details class="sect"><summary><span class="micro">top positions by standalone VaR</span></summary>' +
      '<div class="sect-body"><table class="dt"><tr><th>id</th><th>name</th><th class="num">VaR</th></tr>' +
      rows + "</table></div></details>";
  }
  return html;
}

/* ---- N: scenario explorer --------------------------------------------- */

const SCN_CHOICES = [
  { p: 0.995, label: "the VaR scenario · 99.5th percentile" },
  { p: 0.999, label: "99.9th percentile" },
  { p: 0.99, label: "99th percentile" },
  { p: 0.95, label: "95th percentile" },
  { p: 0.90, label: "90th percentile" },
  { p: 0.50, label: "median" },
];

function scnRankFor(pct) {
  const run = S.runsById[S.currId];
  const sims = (run && run.sims) ||
    (S.cfg && S.cfg.engine && S.cfg.engine.default_sims) || 50000;
  return Math.max(1, Math.round((1 - pct) * sims));
}

const SCN_ENDPOINTS = [
  (id, q) => "/api/runs/" + id + "/scenario?" + q,
  (id, q) => "/api/scenario?run=" + id + "&" + q,
];

async function loadScenario(pct) {
  const run = S.runsById[S.currId];
  if (!run) return;
  S.scn.busy = true; S.scn.err = null; S.scn.pct = pct;
  S.scn.runId = run.id; S.scn.open = true;
  renderDash();
  const qs = "rank=" + scnRankFor(pct) + "&percentile=" + pct;
  const tries = S.scnPath ? [S.scnPath] : SCN_ENDPOINTS;
  let data = null, lastErr = null;
  for (const make of tries) {
    try { data = await api(make(run.id, qs)); S.scnPath = make; break; }
    catch (e) { lastErr = e; }
  }
  S.scn.busy = false;
  if (data) { S.scn.data = data.scenario || data; S.scn.err = null; }
  else {
    S.scn.data = null;
    S.scn.err = lastErr && [404, 405, 501].includes(lastErr.status)
      ? "not-deployed" : (lastErr ? lastErr.message : "unavailable");
  }
  renderDash();
  const sect = document.getElementById("scn");
  if (sect && sect.scrollIntoView) sect.scrollIntoView({ block: "nearest" });
}

function scenarioSectionHTML() {
  const run = S.runsById[S.currId];
  if (!run || run.status !== "done") return "";
  const open = S.scn.open && S.scn.runId === run.id;
  let html = '<details class="sect" id="scn"' + (open ? " open" : "") +
    '><summary><span class="micro">scenario explorer</span></summary>' +
    '<div class="sect-body">';
  html += '<div class="scn-head"><select id="scn-pct">' +
    SCN_CHOICES.map(c => '<option value="' + c.p + '"' +
      (Math.abs(c.p - S.scn.pct) < 1e-9 ? " selected" : "") + ">" +
      esc(c.label) + "</option>").join("") + "</select>" +
    '<button class="btn btn-sm" data-act="scn-load">Show</button></div>';

  if (S.scn.busy) {
    html += '<div class="empty" style="margin:6px 0">Reading the saved simulation…</div>';
  } else if (S.scn.err === "not-deployed") {
    html += '<div class="empty" style="margin:6px 0">Scenario endpoint not deployed ' +
      "yet. The saved simulation is on disk (<code>sim_*.npy</code>); this panel " +
      "lights up when <code>/api/runs/{id}/scenario</code> lands.</div>";
  } else if (S.scn.err) {
    html += '<div class="empty" style="margin:6px 0">' + esc(S.scn.err) + "</div>";
  } else if (S.scn.data && S.scn.runId === run.id) {
    html += scenarioBodyHTML(S.scn.data);
  } else {
    html += '<div class="gov-note">Pick a percentile and press <b>Show</b> — or ' +
      "click the aggregate VaR figure above.</div>";
  }
  return html + "</div></details>";
}

function scenarioBodyHTML(s) {
  let html = "";
  const loss = s.loss_gbp != null ? s.loss_gbp
    : (s.surplus_pnl_gbp != null ? -s.surplus_pnl_gbp : null);
  html += '<div class="scn-line">loss <b>' + esc(fmtMoney(loss)) + "</b>" +
    (s.loss_rank != null ? " · rank " + esc(fmtNum(s.loss_rank, 0)) +
      " of " + esc(fmtNum(s.n_sims, 0)) : "") +
    (s.reported_aggregate_var_gbp != null
      ? " · reported VaR " + esc(fmtMoney(s.reported_aggregate_var_gbp)) : "") +
    "</div>";
  const jp = s.joint_plausibility;
  if (jp && jp.available) {
    html += '<div class="scn-line">joint plausibility d² <b>' +
      esc(fmtNum(jp.mahalanobis_d2, 1)) + "</b> vs " +
      esc(fmtNum(jp.chi2_expected_d2, 0)) + " expected" +
      (jp.chi2_percentile != null
        ? " · " + esc(fmtNum(jp.chi2_percentile, 1)) + "% of draws are milder" : "") +
      "</div>";
  }

  const factors = s.factors || [];
  if (factors.length) {
    const maxV = Math.max(1e-9, ...factors.map(f =>
      Math.abs(f.shock_in_vols == null ? 0 : f.shock_in_vols)));
    html += '<div class="micro" style="margin:8px 0 4px">factor draws · in vols</div>' +
      '<div class="scn-bars">';
    for (const f of factors) {
      const z = f.shock_in_vols == null ? 0 : f.shock_in_vols;
      const frac = Math.min(1, Math.abs(z) / maxV) * 50;
      const neg = z < 0;
      html += '<div class="scn-bar' + (Math.abs(z) > 1.5 ? " big" : "") +
        '" title="' + escAttr(f.factor + "  shock " + f.shock +
          "  level " + f.base_level + " -> " + f.shocked_level) + '">' +
        '<span class="sb-name">' + esc(String(f.factor).replace(/^(gbp_|eq_)/, "")) + "</span>" +
        '<span class="sb-track"><span class="sb-mid" style="left:50%"></span>' +
        '<span class="sb-fill ' + (neg ? "neg" : "pos") + '" style="' +
        (neg ? "right:50%;" : "left:50%;") + "width:" + frac.toFixed(2) + '%"></span></span>' +
        '<span class="sb-v">' + esc((z >= 0 ? "+" : "") + z.toFixed(1) + "σ") + "</span>" +
        "</div>";
    }
    html += "</div>";
  }

  let rows = s.positions_by_loss;
  if (!rows && s.position_pnl_gbp) {
    rows = Object.keys(s.position_pnl_gbp)
      .map(k => ({ id: k, pnl_gbp: s.position_pnl_gbp[k] }))
      .sort((a, b) => a.pnl_gbp - b.pnl_gbp);
  }
  if (rows && rows.length) {
    let body = "";
    for (const r of rows.slice(0, 14)) {
      body += "<tr><td class='lbl mono'>" + esc(r.id) + "</td>" +
        "<td class='lbl' style='max-width:118px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='" +
        escAttr(r.name || "") + "'>" + esc(r.name || "") + "</td>" +
        '<td class="num" style="color:' +
        (r.pnl_gbp < 0 ? "var(--critical)" : "var(--good)") + '">' +
        esc(fmtSignedMoney(r.pnl_gbp)) + "</td></tr>";
    }
    html += '<div class="micro" style="margin:8px 0 4px">position P&amp;L · worst first</div>' +
      '<table class="dt"><tr><th>id</th><th>name</th><th class="num">P&amp;L</th></tr>' +
      body + "</table>";
  }
  if (s.spread_floor_incidence) {
    html += '<div class="gov-note">spread floor bound on ' +
      esc(s.spread_floor_incidence) + " factor(s) in this draw</div>";
  }
  return html;
}

function bindDash(el) {
  const sel = el.querySelector("#scn-pct");
  if (sel) sel.addEventListener("change", () => { S.scn.pct = Number(sel.value); });
}

/* ---- attribution waterfall (SVG, theme-aware via CSS vars) ------------- */

function wfMoney(v) {
  const sign = v < 0 ? "−" : "+";
  const a = Math.abs(v);
  if (a >= 1e9) return sign + (a / 1e9).toFixed(2) + "bn";
  return sign + (a / 1e6).toFixed(1) + "m";
}

function pairPlainLabel() {
  const p = S.runsById[S.prevId], c = S.runsById[S.currId];
  if (p && c) return runLabel(p) + " → " + runLabel(c);
  return c ? runLabel(c) : "";
}

function waterfallCardHTML(att) {
  const mode = S.wfMode;
  const sec = mode === "mtm" ? att.mtm : att.var;
  if (!sec || !sec.steps) return "";
  const start = mode === "mtm" ? sec.prev_surplus_gbp : sec.prev_aggregate_var_gbp;
  const end = mode === "mtm" ? sec.curr_surplus_gbp : sec.curr_aggregate_var_gbp;
  const rows = [{ label: "prev", anchor: true, value: start }];
  let c = start;
  for (const st of sec.steps) {
    rows.push({ label: st.name, from: c, to: c + st.delta_gbp, delta: st.delta_gbp });
    c += st.delta_gbp;
  }
  if (sec.residual_gbp != null) {
    rows.push({ label: "residual", from: c, to: c + sec.residual_gbp,
                delta: sec.residual_gbp, residual: true });
    c += sec.residual_gbp;
  }
  rows.push({ label: "curr", anchor: true, value: end });

  let lo = Math.min(start, end), hi = Math.max(start, end);
  for (const r of rows) if (!r.anchor) { lo = Math.min(lo, r.from, r.to); hi = Math.max(hi, r.from, r.to); }
  const pad = (hi - lo) * 0.04 || 1;
  lo -= pad; hi += pad;

  const W = 330, L = 78, R = 246, rowH = 24, barH = 12;
  const X = (v) => L + (v - lo) / (hi - lo) * (R - L);
  const H = rows.length * rowH + 6;
  let svg = '<svg viewBox="0 0 ' + W + " " + H + '" aria-label="attribution waterfall">';

  rows.forEach((r, i) => {
    const y = i * rowH + 4, ym = y + barH / 2;
    svg += '<text x="' + (L - 8) + '" y="' + (ym + 4) + '" text-anchor="end" ' +
      'font-size="12" fill="var(--ink-2)">' + esc(r.label) + "</text>";
    if (r.anchor) {
      const x = X(r.value);
      svg += '<line x1="' + X(lo + pad) + '" y1="' + ym + '" x2="' + x + '" y2="' + ym +
        '" stroke="var(--hairline)" stroke-width="' + barH + '" stroke-linecap="butt" opacity="0.6"/>';
      svg += '<line x1="' + x + '" y1="' + (y - 2) + '" x2="' + x + '" y2="' + (y + barH + 2) +
        '" stroke="var(--ink-2)" stroke-width="2"/>';
      svg += '<text x="' + (W - 2) + '" y="' + (ym + 4) + '" text-anchor="end" font-size="12" ' +
        'font-weight="700" fill="var(--ink)">' + esc(fmtMoney(r.value)) + "</text>";
    } else {
      const x1 = X(Math.min(r.from, r.to)), x2 = X(Math.max(r.from, r.to));
      const w = Math.max(x2 - x1, 1.5);
      const fill = r.residual ? "none" : (r.delta >= 0 ? "var(--pos)" : "var(--neg)");
      const stroke = r.residual ? ' stroke="var(--muted)" stroke-dasharray="3 2"' : "";
      svg += '<rect x="' + x1 + '" y="' + y + '" width="' + w + '" height="' + barH +
        '" rx="2" fill="' + fill + '"' + stroke + ">" +
        "<title>" + esc(r.label + " " + wfMoney(r.delta)) + "</title></rect>";
      const nxt = rows[i + 1];
      if (nxt && !nxt.anchor) {
        svg += '<line x1="' + X(r.to) + '" y1="' + (y + barH) + '" x2="' + X(r.to) +
          '" y2="' + (y + rowH) + '" stroke="var(--hairline)" stroke-width="1"/>';
      }
      svg += '<text x="' + (W - 2) + '" y="' + (ym + 4) + '" text-anchor="end" font-size="12" ' +
        'fill="' + (r.residual ? "var(--muted)" : (r.delta >= 0 ? "var(--pos)" : "var(--neg)")) + '">' +
        esc(wfMoney(r.delta)) + "</text>";
    }
  });
  svg += "</svg>";

  return '<div class="card wf"><div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
    '<span class="micro" style="flex:1;margin:0">attribution ' + esc(pairPlainLabel()) + "</span>" +
    '<span class="wf-toggle">' +
      '<button data-act="wf-mode" data-mode="var" class="' + (mode === "var" ? "on" : "") + '">&#916;VaR</button>' +
      '<button data-act="wf-mode" data-mode="mtm" class="' + (mode === "mtm" ? "on" : "") + '">&#916;MTM</button>' +
    "</span></div>" + svg +
    '<div class="gov-note">sequential re-pricing, fixed order; residual explicit, never absorbed</div></div>';
}

/* ======================================================================
   right rail
   ====================================================================== */

function renderRail() {
  const el = document.getElementById("rail");
  // never yank the DOM out from under an input the user is typing in
  if (el.contains(document.activeElement) &&
      document.activeElement.matches("input, textarea, select")) return;
  el.innerHTML =
    railOperatorHTML() +
    railStageStripHTML() +
    railRunPickerHTML() +
    railGatesHTML() +
    railScorecardHTML() +
    railAgentsHTML();
  bindRail(el);
}

function railOperatorHTML() {
  return '<div class="rail-sect"><span class="micro">operator</span>' +
    '<div class="field"><input type="text" id="operator-input" placeholder="your name — signs posts & gate decisions" value="' +
    escAttr(S.operator) + '"></div></div>';
}

/* ---- §3 stage strip ---------------------------------------------------- */

const STAGE_MARK = {
  idle:    { glyph: "&#9656;", word: "idle" },       // ▸
  running: { glyph: "&#9679;", word: "running" },    // ●
  done:    { glyph: "&#10003;", word: "done" },      // ✓
  failed:  { glyph: "&#10007;", word: "failed" },    // ✗
};

function stageLabel(stage) {
  if (stage === "research") return "Run research";
  return "Run room " + stage;
}

function stageSub(stage) {
  if (stage === "research") return "@focused, @wide-eye";
  const n = passRoster(stage).length;
  return n + " agent" + (n === 1 ? "" : "s");
}

function railStageStripHTML() {
  const curr = S.runsById[S.currId];
  const month = researchMonth();
  const anyRunning = CYCLE_STAGES.some(s => stageOf(s).state === "running");
  let html = '<div class="rail-sect"><span class="micro">stages · ' +
    esc(month || "no month") + " · research &#8594; 1 &#8594; 2 &#8594; 3</span>" +
    '<div class="stage-strip">';
  for (const s of CYCLE_STAGES) {
    const st = stageOf(s);
    const mark = STAGE_MARK[st.state] || STAGE_MARK.idle;
    // Rooms are individually runnable in ANY order — nothing hard-blocks.
    // Only a stage already running, and a missing run for a room pass,
    // disable the button.
    const needsRun = s !== "research" && !curr;
    const dis = st.state === "running" || needsRun ||
      (S.runAll && S.runAll.active);
    html += '<button class="stage-btn ' + esc(st.state) +
      '" data-act="stage-run" data-stage="' + escAttr(s) + '"' +
      (dis ? " disabled" : "") +
      (needsRun ? ' title="a room pass needs an active run — pick or start one below"' : "") +
      '><span class="sb-mark">' + mark.glyph + "</span>" +
      '<span class="sb-main"><span class="sb-label">' + esc(stageLabel(s)) + "</span>" +
      '<span class="sb-sub">' + esc(stageSub(s)) + "</span></span>" +
      '<span class="sb-state">' + esc(st.state === "idle" ? "" : mark.word) + "</span>" +
      "</button>";
    if (st.note) {
      html += '<div class="stage-note">' + esc(st.note) + "</div>";
    }
  }
  const raState = S.runAll && S.runAll.active
    ? "running" : (S.runAll && S.runAll.state) || "idle";
  const raMark = STAGE_MARK[raState] || STAGE_MARK.idle;
  html += '<button class="stage-btn all ' + esc(raState) +
    '" data-act="stage-run-all"' +
    ((S.runAll && S.runAll.active) || anyRunning || !curr ? " disabled" : "") +
    ((!curr) ? ' title="the chain includes room passes, which need an active run"' : "") +
    '><span class="sb-mark">' + raMark.glyph + "</span>" +
    '<span class="sb-main"><span class="sb-label">Run all</span>' +
    '<span class="sb-sub">research &#8594; 1 &#8594; 2 &#8594; 3</span></span>' +
    '<span class="sb-state">' + esc(raState === "idle" ? "" : raMark.word) +
    "</span></button>";
  if (S.runAll && S.runAll.active) {
    html += '<div class="stage-note">chaining · at <b>' +
      esc(stageLabel(S.runAll.queue[S.runAll.at])) + "</b></div>";
  }
  if (S.runAll && !S.runAll.active && S.runAll.note) {
    html += '<div class="stage-note">' + esc(S.runAll.note) + "</div>";
  }
  return html + "</div></div>";
}

/* J: two run selectors, each grouped by month with versions beneath, so any
   version of one month can be compared against any version of another. */
function runSelectHTML(id, selectedId) {
  const months = Array.from(new Set(S.runs.map(monthOf))).sort().reverse();
  let html = '<select id="' + id + '"><option value="">—</option>';
  for (const m of months) {
    const rows = S.runs.filter(r => monthOf(r) === m)
      .sort((a, b) => runVersion(a) - runVersion(b));
    html += '<optgroup label="' + escAttr(m) + '">';
    for (const r of rows) {
      const mark = runStatusMark(r);
      html += '<option value="' + r.id + '"' +
        (selectedId === r.id ? " selected" : "") +
        (runStopped(r) ? ' disabled class="opt-stopped"' : "") + ">" +
        esc(runLabel(r) + (r.seeded ? " · seeded" : "") + " · " + mark.word) +
        "</option>";
    }
    html += "</optgroup>";
  }
  return html + "</select>";
}

function railRunPickerHTML() {
  const curr = S.runsById[S.currId];
  let html = '<div class="rail-sect"><span class="micro">runs</span>' +
    '<div class="row-2">' +
      '<div class="field"><label>prev</label>' + runSelectHTML("prev-select", S.prevId) + "</div>" +
      '<div class="field"><label>curr (active)</label>' + runSelectHTML("curr-select", S.currId) + "</div>" +
    "</div>";
  if (curr) {
    const mark = runStatusMark(curr);
    html += '<div class="runrow"><span class="status-chip ' + esc(mark.cls) + '">' +
      esc(mark.word) + "</span><span" + (runStopped(curr) ? ' class="opt-stopped"' : "") +
      ">" + esc(runLabel(curr)) + "</span>" +
      (curr.seeded ? ' <span class="seed-chip">seeded</span>' : "") + "</div>";
  }
  html += '<div class="row-2" style="margin-top:8px">' +
    '<button class="btn btn-sm btn-primary" data-act="run-model">Run model</button>' +
    (runInFlight(curr)
      ? '<button class="btn btn-sm btn-danger" data-act="stop-run">⏹ Stop run</button>'
      : "") +
    "</div></div>";
  return html;
}

function railGatesHTML() {
  const pending = S.gates.filter(g => g.status === "pending");
  const decided = S.gates.filter(g => g.status !== "pending");
  let html = '<div class="rail-sect"><span class="micro">gates · the agent proposes, a named human disposes</span>';
  if (!pending.length) {
    html += '<div class="empty" style="padding:14px">No pending gates.<br>' +
      "A gate appears when an agent calls <code>propose_rerun</code>.</div>";
  }
  for (const g of pending) {
    const adj = tryJson(g.adjustments_json, {});
    const adjRows = Object.keys(adj || {}).map(k =>
      '<div><span class="path">' + esc(k) + '</span> &#8594; <span class="val">' + esc(adj[k]) + "</span></div>").join("");
    html += '<div class="gate-card">' +
      '<div class="gate-hd"><span class="gid">GATE #' + esc(g.id) + '</span>' +
      '<span class="micro">rerun of ' +
      esc(S.runsById[g.run_id] ? runLabel(S.runsById[g.run_id]) : "#" + g.run_id) +
      "</span></div>" +
      (g.rationale ? '<div class="gate-rationale">' + esc(g.rationale) + "</div>" : "") +
      (adjRows ? '<div class="gate-adj">' + adjRows + "</div>" : "") +
      (g.proposed_by_post_id ? '<div class="gov-note">proposed by post #' + esc(g.proposed_by_post_id) + "</div>" : "") +
      '<div class="gate-actions">' +
        '<input type="text" class="decided-by" placeholder="decided_by — your name" value="' + escAttr(S.operator) + '">' +
      "</div>" +
      '<div class="gate-actions">' +
        '<button class="btn btn-primary btn-sm" data-act="gate-approve" data-id="' + g.id + '">Approve &amp; rerun</button>' +
        '<button class="btn btn-sm btn-danger" data-act="gate-reject" data-id="' + g.id + '">Reject</button>' +
      "</div></div>";
  }
  if (decided.length) {
    html += '<details class="sect"><summary><span class="micro">decided · ' + decided.length +
      "</span></summary><div class='sect-body'>";
    for (const g of decided.slice(0, 8)) {
      html += '<div class="gate-decided">#' + esc(g.id) + " <span class='" +
        (g.status === "approved" ? "ok" : "no") + "'>" + esc(g.status) + "</span> by " +
        esc(g.decided_by || "?") +
        (g.result_run_id && S.runsById[g.result_run_id]
          ? " &#8594; " + esc(runLabel(S.runsById[g.result_run_id])) : "") + "</div>";
    }
    html += "</div></details>";
  }
  html += "</div>";
  return html;
}

function railScorecardHTML() {
  const sc = S.scorecard;
  if (!sc) return "";
  const c = sc.citations || {};
  let html = '<div class="rail-sect"><span class="micro">scorecard</span>' +
    '<div class="card" style="margin-bottom:8px">' +
    '<div class="score-kv"><span class="k">posts published / suppressed</span><span class="v">' +
      esc(sc.posts.published) + " / " + esc(sc.posts.suppressed) + "</span></div>" +
    '<div class="score-kv"><span class="k">suppression rate</span><span class="v">' +
      esc(fmtPct(sc.suppression_rate, 1)) + "</span></div>" +
    (sc.quiet_rate != null
      ? '<div class="score-kv"><span class="k">nothing-material rate</span><span class="v">' +
        esc(fmtPct(sc.quiet_rate, 0)) + "</span></div>" : "") +
    '<div class="score-kv"><span class="k">claims bound</span><span class="v">' +
      esc(c.claims_bound) + " / " + esc(c.claims_total) +
      (c.binding_rate != null ? " (" + esc(fmtPct(c.binding_rate, 0)) + ")" : "") + "</span></div>" +
    '<div class="score-kv"><span class="k">tool calls recorded</span><span class="v">' +
      esc(c.tool_calls_recorded) + "</span></div>" +
    "</div>";

  // R: the detection section appears only when the active run is seeded,
  // and is otherwise absent rather than showing an empty state.
  const curr = S.runsById[S.currId];
  const det = sc.detection;
  if (det && curr && curr.seeded) {
    html += '<div class="card"><span class="micro">detection vs ground truth</span>';
    for (const dRow of det.defects || []) {
      html += '<div class="det-row"><span class="d-id">' + esc(dRow.id || "?") + "</span>" +
        '<span class="d-field" title="' + escAttr(dRow.field || "") + '">' + esc(dRow.field || "") + "</span>" +
        '<span class="det-mark ' + (dRow.detected ? "hit" : "miss") + '">' +
        (dRow.detected ? "&#10003; caught" : "&#10007; missed") + "</span></div>";
    }
    const mf = det.must_flag_changes || det.must_flag || det.must_flag_items;
    if (Array.isArray(mf)) {
      for (const m of mf) {
        const hit = m.detected != null ? m.detected : m.flagged;
        html += '<div class="det-row"><span class="d-id">MF</span>' +
          '<span class="d-field" title="' + escAttr(m.field || m.id || "") + '">' +
          esc(m.id || m.field || "must-flag change") + "</span>" +
          '<span class="det-mark ' + (hit ? "hit" : "miss") + '">' +
          (hit ? "&#10003; surfaced" : "&#10007; not surfaced") + "</span></div>";
      }
    }
    html += '<div class="score-kv"><span class="k">recall</span><span class="v">' +
      esc(det.recall != null ? det.recall.toFixed(2) : "—") + "</span></div>" +
      '<div class="score-kv"><span class="k">precision</span><span class="v">' +
      esc(det.precision != null ? det.precision.toFixed(2) : "—") + "</span></div>";
    const viols = det.must_not_flag_violations || [];
    html += '<div class="score-kv"><span class="k">must-not-flag violations</span>' +
      '<span class="v" style="color:' + (viols.length ? "var(--critical)" : "var(--good)") + '">' +
      esc(viols.length) + "</span></div>";
    html += "</div>";
  }
  html += "</div>";
  return html;
}

/* The agents whose output the current tab shows (§14), each ONCE (§13):
   a room lists its own roster plus the personas scheduled into it through
   `also_posts_in`; the research tab lists the two research desks. One
   persona, one card — @focused posts in rooms 1 and 3 and is still a single
   row, with the other room named on it. */
const RESEARCH_HANDLES = ["@focused", "@wide-eye"];

function agentsForView() {
  if (S.view === "research") {
    return RESEARCH_HANDLES.map(h => S.agentsByHandle[h]).filter(Boolean);
  }
  const seen = new Set(), out = [];
  for (const a of S.agents) {
    const home = a.room === S.room;
    if (!home && !alsoPostsIn(a).includes(S.room)) continue;
    const key = String(a.handle || a.id).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(a);
  }
  return out;
}

function railAgentsHTML() {
  const research = S.view === "research";
  const agents = agentsForView();
  let html = '<div class="rail-sect"><span class="micro">agents</span>' +
    (research
      ? '<div class="inline-note" style="margin:-2px 0 7px">the two research ' +
        "desks — whose reports this tab holds</div>" : "");
  for (const a of agents) {
    const also = alsoPostsIn(a);
    const visiting = !research && a.room !== S.room;
    // on a room tab: where ELSE this persona posts. on the research tab:
    // every room it posts in, since neither is "this" room.
    const rooms = research ? [a.room].concat(also)
                           : (visiting ? [a.room] : also);
    const word = research ? (rooms.length > 1 ? "rooms " : "room ")
                          : (visiting ? "home " : "also ");
    html += '<div class="agent-card" data-act="agent-profile" data-id="' + a.id +
      '" title="open ' + escAttr(a.handle) + '’s profile">' +
      agentAvatarHTML(a, 32) +
      '<div class="a-main"><div class="a-handle">' + esc(a.handle) +
      (a.builtin ? ' <span style="color:var(--muted);font-weight:400">· builtin</span>' : "") +
      (rooms.length
        ? ' <span class="a-also" title="one agent, one handle, a brief per ' +
          'room">· ' + word + esc(rooms.join(", ")) + "</span>" : "") +
      '</div><div class="a-focus" title="' + escAttr(a.focus || "") + '">' +
      esc(a.focus || "") + "</div></div></div>";
  }
  if (!agents.length) {
    html += '<div class="inline-note">no agents listed for this tab.</div>';
  }
  if (!research) {
    html += '<button class="btn btn-sm" style="margin-top:6px" data-act="new-agent">+ New agent</button>';
  }
  return html + "</div>";
}

function bindRail(el) {
  const op = el.querySelector("#operator-input");
  if (op) op.addEventListener("change", () => {
    S.operator = op.value.trim(); lsSet("operator", S.operator);
  });
  const ps = el.querySelector("#prev-select"), cs = el.querySelector("#curr-select");
  if (ps) ps.addEventListener("change", () => {
    S.prevId = ps.value ? Number(ps.value) : null;
    lsSet("prevId", S.prevId || "");
    onPairChanged();
  });
  if (cs) cs.addEventListener("change", () => {
    S.currId = cs.value ? Number(cs.value) : null;
    lsSet("currId", S.currId || "");
    S.scn.data = null; S.scn.err = null;
    onPairChanged();
  });
}

async function onPairChanged() {
  S.anchor = null;
  renderHeaderStatus(); renderFeedHead();
  connectSSE();
  await refreshDash(S.room);
  if (S.room === 3) { await refreshSnapshots(); renderFeed(); }
  renderRail();
}

/* ======================================================================
   run control (J) — dialog, versions, stop
   ====================================================================== */

function availableMonths() {
  const months = new Set(COMMITTED_MONTHS);
  for (const r of S.runs) months.add(monthOf(r));
  return Array.from(months).filter(Boolean).sort();
}

function asofForMonth(m) { return MONTH_ASOF[m] || m; }
function monthKeyShort(m) { return String(m).replace("-", "").slice(2); }

function openRunModal() {
  const months = availableMonths();
  const curr = S.runsById[S.currId];
  const month = (curr && months.includes(monthOf(curr))) ? monthOf(curr)
    : months[months.length - 1];
  S.runModal = { year: month.slice(0, 4), month: month, sel: "new", seeded: false };
  renderRunModal();
}

function closeRunModal() {
  S.runModal = null;
  document.getElementById("run-modal").hidden = true;
  if (document.getElementById("agent-modal").hidden) {
    document.getElementById("modal-veil").hidden = true;
  }
}

function renderRunModal() {
  const R = S.runModal;
  if (!R) return;
  const months = availableMonths();
  const years = Array.from(new Set(months.map(m => m.slice(0, 4)))).sort();
  const monthsInYear = months.filter(m => m.slice(0, 4) === R.year);
  const runs = S.runs.filter(r => monthOf(r) === R.month)
    .sort((a, b) => runVersion(a) - runVersion(b));
  const seedable = !!S.seededPaths[R.month];

  let list = "";
  for (const r of runs) {
    const mark = runStatusMark(r);
    const dead = runStopped(r);
    list += '<div class="run-opt' + (R.sel === r.id ? " on" : "") +
      (dead ? " dead" : "") + '" data-act="run-pick" data-id="' + r.id + '"' +
      (dead ? ' title="a stopped run is history, not a basis"' : "") + ">" +
      '<span class="ro-mark ' + esc(mark.cls) + '">' + mark.glyph + "</span>" +
      '<span class="ro-label">' + esc(runLabel(r)) + "</span>" +
      '<span class="ro-status">' + esc(mark.word) +
      (r.seeded ? " · seeded" : "") + "</span>" +
      '<span class="ro-sims">' + esc(fmtNum(r.sims, 0)) + " sims</span></div>";
  }
  list += '<div class="run-opt' + (R.sel === "new" ? " on" : "") +
    '" data-act="run-pick" data-id="new">' +
    '<span class="ro-mark">+</span>' +
    '<span class="ro-label">' + esc(monthKeyShort(R.month) + "_v" +
      nextVersionFor(R.month)) + "</span>" +
    '<span class="ro-status">new run</span></div>';

  const modal = document.getElementById("run-modal");
  modal.innerHTML =
    "<h2>Run the model</h2>" +
    '<div class="run-grid">' +
      '<div class="field"><label>year</label><select id="rm-year">' +
        years.map(y => '<option value="' + escAttr(y) + '"' +
          (y === R.year ? " selected" : "") + ">" + esc(y) + "</option>").join("") +
      "</select></div>" +
      '<div class="field"><label>month</label><select id="rm-month">' +
        monthsInYear.map(m => '<option value="' + escAttr(m) + '"' +
          (m === R.month ? " selected" : "") + ">" +
          esc(MONTH_NAMES[Number(m.slice(5, 7)) - 1] || m) + "</option>").join("") +
      "</select></div>" +
    "</div>" +
    '<div class="field"><label>run</label></div>' +
    '<div class="run-list">' + list + "</div>" +
    '<label class="seed-check"><input type="checkbox" id="rm-seeded"' +
      (R.seeded ? " checked" : "") + (seedable ? "" : " disabled") +
      "> use seeded inputs" +
      (seedable ? "" : " (none bundled for this month)") + "</label>" +
    '<div class="actions">' +
      '<button class="btn" data-act="run-modal-close">Cancel</button>' +
      '<button class="btn btn-primary" data-act="run-start">' +
      (R.sel === "new" ? "Start" : "Load") + "</button>" +
    "</div>" +
    '<div class="notice" id="rm-notice"></div>';

  modal.querySelector("#rm-year").addEventListener("change", (e) => {
    S.runModal.year = e.target.value;
    const inYear = months.filter(m => m.slice(0, 4) === S.runModal.year);
    S.runModal.month = inYear[inYear.length - 1];
    S.runModal.sel = "new"; S.runModal.seeded = false;
    renderRunModal();
  });
  modal.querySelector("#rm-month").addEventListener("change", (e) => {
    S.runModal.month = e.target.value;
    S.runModal.sel = "new"; S.runModal.seeded = false;
    renderRunModal();
  });
  const sc = modal.querySelector("#rm-seeded");
  if (sc) sc.addEventListener("change", () => { S.runModal.seeded = sc.checked; });

  document.getElementById("modal-veil").hidden = false;
  modal.hidden = false;
}

async function startRunFromModal() {
  const R = S.runModal;
  if (!R) return;
  if (R.sel !== "new") {
    const r = S.runsById[Number(R.sel)];
    if (!r) return;
    if (runStopped(r)) {
      document.getElementById("rm-notice").textContent =
        "a stopped run cannot be loaded as a basis";
      return;
    }
    S.currId = r.id; lsSet("currId", S.currId);
    S.scn.data = null;
    closeRunModal();
    S.view = "room";
    renderAll(); buildFeedComposer(); connectSSE();
    await Promise.all([refreshFeed(S.room), refreshDash(S.room)]);
    return;
  }
  const payload = { asof: asofForMonth(R.month) };
  if (R.seeded && S.seededPaths[R.month]) {
    payload.seeded_assumptions = S.seededPaths[R.month].assumptions;
    payload.seeded_book = S.seededPaths[R.month].book;
  }
  try {
    const res = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
    const run = res.run;
    closeRunModal();
    await refreshRuns(false);
    S.currId = run.id; lsSet("currId", S.currId);
    S.scn.data = null;
    S.room = 2; S.view = "room";      // watch it land
    toast(runLabel(S.runsById[run.id] || run) + " queued — engine executing", "good");
    renderAll(); buildFeedComposer(); connectSSE();
    await Promise.all([refreshFeed(2), refreshDash(2)]);
  } catch (e) {
    const n = document.getElementById("rm-notice");
    if (n) n.textContent = "run creation failed: " + e.message;
    else toast("run creation failed: " + e.message, "err");
  }
}

/* Stop: terminate the engine subprocess, mark the run stopped, keep the
   partial stage events. Degrades cleanly while the endpoint is being added. */
async function stopRun() {
  const curr = S.runsById[S.currId];
  if (!curr) return;
  try {
    await api("/api/runs/" + curr.id + "/stop", { method: "POST",
      body: JSON.stringify({ stopped_by: S.operator || "you" }) });
    toast(runLabel(curr) + " stopped — partial stage events kept", "good");
  } catch (e) {
    if ([404, 405, 501].includes(e.status)) {
      toast("POST /api/runs/{id}/stop is not deployed yet (" + e.status +
            ") — the run was left alone", "err");
    } else {
      toast("stop failed: " + e.message, "err");
    }
  }
  await refreshRuns(false);
  renderAll();
  await refreshDash(S.room);
}

/* ---- §3 stage runners: research -> 1 -> 2 -> 3 -------------------------- */

/* The month a research pass covers: the active run's, else the newest month
   that has a run, else the first month-end in the SPEC table. */
function researchMonth() {
  const curr = S.runsById[S.currId];
  if (curr) return monthOf(curr);
  const withRuns = Array.from(new Set(S.runs.map(monthOf))).filter(Boolean).sort();
  if (withRuns.length) return withRuns[withRuns.length - 1];
  return "2026-03";
}

function startStage(stage) {
  if (stage === "research") return startResearchStage();
  return startRoomPass(Number(stage));
}

/* Research (stage 1 of the cycle). Completion arrives either as the
   broadcast `research` SSE event or as a changed generated_at in the
   reports index — whichever lands first. */
async function reportsIndex(month) {
  try {
    const d = await api("/api/research/reports?asof=" + encodeURIComponent(month));
    const out = {};
    for (const r of d.reports || []) out[r.agent] = r.generated_at || "";
    return out;
  } catch (e) { return null; }
}

async function startResearchStage() {
  const month = researchMonth();
  const before = await reportsIndex(month);
  S.researchSignal = null;
  setStage("research", "running", "regenerating both reports for " + month);
  try {
    await api("/api/research/run", { method: "POST",
      body: JSON.stringify({ month: month }) });
  } catch (e) {
    setStage("research", "failed", "POST /api/research/run: " + e.message);
    onStageSettled("research", false);
    return;
  }
  watchResearch(month, before);
}

function watchResearch(month, before) {
  let ticks = 0;
  const settle = (ok, note) => {
    clearInterval(iv);
    setStage("research", ok ? "done" : "failed", note);
    refreshReportMonths();
    if (S.view === "research") loadReports(month, true);
    onStageSettled("research", ok);
  };
  const iv = setInterval(async () => {
    ticks++;
    const sig = S.researchSignal;
    if (sig) {
      const errs = (sig.errors || []).length;
      settle(!errs, errs ? errs + " report(s) errored"
                         : (sig.reports || []).length + " reports written");
      return;
    }
    const now = await reportsIndex(month);
    if (now && before) {
      const changed = Object.keys(now).filter(k => now[k] && now[k] !== before[k]);
      if (changed.length) { settle(true, changed.join(", ") + " regenerated"); return; }
    } else if (now === null && ticks > 3) {
      settle(false, "GET /api/research/reports unavailable — cannot confirm");
      return;
    }
    if (ticks > 60) settle(false, "no report change seen in 90s");
  }, 1500);
}

/* A room pass. Rooms are individually runnable in any order — this never
   checks what ran before it. */
async function startRoomPass(room) {
  const curr = S.runsById[S.currId];
  if (!curr) {
    toast("select or create a run first", "err");
    setStage(room, "failed", "no active run"); onStageSettled(room, false); return;
  }
  if (runStopped(curr)) {
    toast("a stopped run is not a basis for a pass", "err");
    setStage(room, "failed", "active run is stopped"); onStageSettled(room, false); return;
  }
  if (passInFlight(room)) return;
  const payload = {};
  if (S.prevId && S.runsById[S.prevId] && !runStopped(S.runsById[S.prevId])) {
    payload.pair = [S.prevId, S.currId];
  } else payload.run_id = S.currId;
  payload.seeded = !!curr.seeded;
  beginPass(room);
  if (S.room === room && S.view === "room") renderFeed();
  try {
    await api("/api/rooms/" + room + "/refresh", { method: "POST",
      body: JSON.stringify(payload) });
    toast("room " + room + " pass scheduled — posts stream in as agents publish",
          "good");
  } catch (e) {
    endPass(room, "failed", "POST /api/rooms/" + room + "/refresh: " + e.message);
    toast("refresh failed: " + e.message, "err");
    return;
  }
  watchPass(room);
}

function watchPass(room) {
  const iv = setInterval(async () => {
    if (!passInFlight(room)) { clearInterval(iv); return; }
    await refreshFeed(room);           // updatePass() ends it when all land
    if (!passInFlight(room)) clearInterval(iv);
    else { updatePass(room); renderSwitcher(); if (S.room === room) renderFeed(); }
  }, 1500);
}

/* Run all: chains the cycle. A failed stage stops the chain — silently
   carrying on past a stage that did not run would be worse than stopping. */
function startRunAll() {
  S.runAll = { active: true, queue: CYCLE_STAGES.slice(), at: 0, note: "", state: "running" };
  renderRail();
  runAllStep();
}

function runAllStep() {
  const ra = S.runAll;
  if (!ra || !ra.active) return;
  if (ra.at >= ra.queue.length) {
    ra.active = false; ra.state = "done"; ra.note = "chain complete";
    renderRail();
    toast("run all complete — research, room 1, room 2, room 3", "good");
    return;
  }
  startStage(ra.queue[ra.at]);
}

function onStageSettled(stage, ok) {
  const ra = S.runAll;
  if (!ra || !ra.active) return;
  if (String(ra.queue[ra.at]) !== String(stage)) return;
  if (!ok) {
    ra.active = false; ra.state = "failed";
    ra.note = stageLabel(stage) + " failed — chain stopped";
    renderRail();
    toast("run all stopped at " + stageLabel(stage), "err");
    return;
  }
  ra.at++;
  setTimeout(runAllStep, 600);
}

/* the feed-head button and the strip are the same action */
function refreshRoomPass() { startRoomPass(S.room); }

async function freshSnapshot() {
  const curr = S.runsById[S.currId];
  if (!curr) return;
  S.snapBusy = true; renderFeedHead();
  try {
    await api("/api/rooms/3/snapshot", { method: "POST",
      body: JSON.stringify({ run_id: curr.id }) });
    toast("fresh snapshot scheduled — outward agents re-run against later data", "good");
  } catch (e) {
    toast("snapshot: " + e.message, "err");
  }
  setTimeout(async () => {
    S.snapBusy = false;
    await refreshFeed(3);
    renderFeedHead();
  }, 3000);
}

/* ---- gates ------------------------------------------------------------- */

async function decideGate(gateId, verb, btn) {
  const card = btn.closest(".gate-card");
  const name = (card.querySelector(".decided-by").value || "").trim();
  if (!name) { toast("gate decisions need a named human — fill decided_by", "err"); return; }
  try {
    const res = await api("/api/gates/" + gateId + "/" + verb,
      { method: "POST", body: JSON.stringify({ decided_by: name }) });
    if (verb === "approve") {
      const run = res.run;
      await refreshRuns(false);
      toast("gate #" + gateId + " approved by " + name + " — corrected rerun " +
        runLabel(S.runsById[run.id] || run) + " executing; watching in room 2", "good");
      S.currId = run.id; lsSet("currId", S.currId);
      S.room = 2; S.view = "room";
      renderAll(); buildFeedComposer();
      connectSSE();
      await Promise.all([refreshFeed(2), refreshDash(2)]);
    } else {
      toast("gate #" + gateId + " rejected by " + name, "good");
    }
    await refreshGates();
  } catch (e) { toast("gate: " + e.message, "err"); }
}

/* ======================================================================
   research view
   ====================================================================== */

/* Months the research tab offers. NOT the whole SPEC month table: asking for
   a month WRITES `outputs/research/<YYYY_MM>_<agent>.md` as a side effect of
   the GET, and PENDING-BATCH2 §1 says only 2026_02 and 2026_03 exist. So the
   picker lists months that already have a report on disk (from the reports
   index) plus months that have a run — nothing speculative. */
function researchMonths() {
  const months = new Set(S.reportMonths || []);
  for (const r of S.runs) { const m = monthOf(r); if (m) months.add(m); }
  if (!months.size) months.add(researchMonth());
  return Array.from(months).filter(Boolean).sort();
}

async function refreshReportMonths() {
  try {
    const d = await api("/api/research/reports");
    S.reportMonths = Array.from(new Set((d.reports || [])
      .filter(r => r.generated_at)
      .map(r => r.month))).filter(Boolean);
  } catch (e) { S.reportMonths = S.reportMonths || []; }
}

/* Which agent wrote which report: the API's `agent` is the bare stem
   (`focused`, `wide-eye`); the roster handle is the same with an @. */
function researchHandle(agent) { return "@" + String(agent || ""); }

/* §2/§5: BOTH reports for the active month, newest first, each a document —
   agent, timestamp, month covered, full length with headings. */
async function loadReports(asof, force) {
  if (!force && S.reports.asof === asof && S.reports.rows.length) {
    renderResearch(); return;
  }
  S.reports = { asof: asof, rows: [], docs: {}, err: null, busy: true };
  renderResearch();
  let rows = null;
  try {
    const d = await api("/api/research/reports?asof=" + encodeURIComponent(asof));
    rows = d.reports || [];
  } catch (e) {
    if (![404, 405, 501].includes(e.status)) S.reports.err = e.message;
    // index endpoint absent -> assume the two known agents and let the
    // per-report fetch decide what exists
    rows = ["focused", "wide-eye"].map(a =>
      ({ agent: a, month: asof, file: null, generated_at: null }));
  }
  S.reports.rows = rows;
  S.reports.busy = false;
  renderResearch();
  for (const r of rows) {
    if (S.reports.asof !== asof) return;      // month changed under us
    await loadOneReport(asof, r.agent);
  }
}

async function loadOneReport(asof, agent) {
  const key = agent;
  S.reports.docs[key] = { state: "loading" };
  renderResearch();
  try {
    const d = await api("/api/research?asof=" + encodeURIComponent(asof) +
                        "&agent=" + encodeURIComponent(agent));
    const md = typeof d === "string" ? d
      : (d.markdown || d.md || d.body || d.content || d.text || d.note || "");
    S.reports.docs[key] = { state: "ok", md: md, meta: d };
  } catch (e) {
    S.reports.docs[key] = { state: e.status === 404 ? "missing" : "err",
                            err: e.message };
  }
  renderResearch();
}

function renderResearch() {
  if (S.view !== "research") return;
  const head = document.getElementById("feed-head");
  const feedEl = document.getElementById("feed");
  document.getElementById("suppressed-drawer").innerHTML = "";
  document.getElementById("feed-composer").style.display = "none";

  const months = researchMonths();
  const sel = S.reports.asof || researchMonth();
  const stg = stageOf("research");
  head.innerHTML = '<div class="feedhead-row">' +
    '<h1 style="flex:1;font-size:var(--fs-3);margin:0">Research</h1>' +
    '<select id="research-asof" class="btn btn-sm" style="appearance:auto">' +
    months.map(m => '<option value="' + escAttr(m) + '"' + (m === sel ? " selected" : "") + ">" +
      esc(m) + "</option>").join("") + "</select>" +
    '<button class="btn btn-sm btn-primary" data-act="stage-run" data-stage="research"' +
      (stg.state === "running" ? " disabled" : "") + ">" +
      (stg.state === "running" ? "research running…" : "Run research") +
    "</button></div>" +
    '<div class="runline">two reports a month — <b>@focused</b> (the standing ' +
    "focused-risk set) and <b>@wide-eye</b> (wider risks). Read-only documents; " +
    "the room posts that cite them are written by the room passes.</div>";
  const selEl = head.querySelector("#research-asof");
  selEl.addEventListener("change", () => loadReports(selEl.value, true));

  if (S.reports.asof !== sel) { loadReports(sel); return; }

  if (S.reports.err) {
    feedEl.innerHTML = '<div class="empty">Could not list the reports (' +
      esc(S.reports.err) + ").</div>";
    return;
  }
  if (S.reports.busy && !S.reports.rows.length) {
    feedEl.innerHTML = '<div class="empty">Loading the month’s reports…</div>';
    return;
  }
  if (!S.reports.rows.length) {
    feedEl.innerHTML = '<div class="empty">No reports listed for ' + esc(sel) +
      ".</div>";
    return;
  }
  // newest first: generated_at descending, never-generated last
  const rows = S.reports.rows.slice().sort((a, b) =>
    String(b.generated_at || "").localeCompare(String(a.generated_at || "")));
  let html = "";
  for (const r of rows) {
    const doc = S.reports.docs[r.agent] || { state: "loading" };
    const ag = S.agentsByHandle[researchHandle(r.agent)];
    html += '<article class="research-doc">' +
      '<header class="rd-hd">' +
      (ag ? '<span class="av-link" data-act="agent-profile" data-id="' + ag.id +
            '">' + agentAvatarHTML(ag, 40) + "</span>"
          : avatarSVG(defaultAvatar(r.agent, researchHandle(r.agent)), 40)) +
      '<div class="rd-id">' +
        '<div class="rd-handle"' + (ag ? ' data-act="agent-profile" data-id="' +
          ag.id + '"' : "") + ">" + esc(researchHandle(r.agent)) + "</div>" +
        '<div class="rd-meta">covering <b>' + esc(r.month || sel) + "</b>" +
        (r.generated_at
          ? " &nbsp;·&nbsp; written " + esc(fmtTsFull(r.generated_at)) +
            " (" + esc(relTime(r.generated_at)) + ")"
          : " &nbsp;·&nbsp; computed on request — no file written for this " +
            "month yet, so <b>Run research</b> to keep one") +
        (r.file ? " &nbsp;·&nbsp; <code>" + esc(r.file) + "</code>" : "") +
        "</div></div></header>";
    if (doc.state === "loading") {
      html += '<div class="empty">Loading…</div>';
    } else if (doc.state === "missing") {
      html += '<div class="empty"><b>not generated yet</b> — press ' +
        "<b>Run research</b> above (or the stage strip in the right rail) " +
        "to write it.</div>";
    } else if (doc.state === "err") {
      html += '<div class="empty">Could not load (' + esc(doc.err) + ").</div>";
    } else {
      html += '<div class="rd-body post-body">' +
        mdRender(doc.md, { mention: mentionChip }) + "</div>";
    }
    html += "</article>";
  }
  feedEl.innerHTML = html;
}

/* ======================================================================
   agent modal (edit / builder) — §8.1
   ====================================================================== */

const M = { mode: null, agent: null, room: null, av: null, dirty: {} };

function openAgentModal(mode, agent, room) {
  M.mode = mode; M.agent = agent || null; M.room = room || S.room; M.dirty = {};
  M.av = agent ? resolveAvatar(agent) : defaultAvatar("", "");
  const veil = document.getElementById("modal-veil");
  const modal = document.getElementById("agent-modal");
  const isEdit = mode === "edit";

  modal.innerHTML =
    "<h2>" + (isEdit ? "Edit agent" : "New agent · room " + esc(M.room)) +
    (isEdit && agent.builtin ? ' <span class="micro">builtin — editable, handle immutable</span>' : "") + "</h2>" +
    '<div class="preview-row"><span id="m-preview"></span>' +
      '<div class="preview-meta"><div class="p-handle" id="m-p-handle"></div>' +
      '<div class="p-name" id="m-p-name"></div></div></div>' +
    (isEdit
      ? '<div class="field"><label>handle (immutable)</label><input type="text" id="m-handle" value="' + escAttr(agent.handle) + '" disabled></div>'
      : '<div class="field"><label>handle</label><input type="text" id="m-handle" placeholder="@private-credit"></div>') +
    '<div class="field"><label>name</label><input type="text" id="m-name" value="' + escAttr(agent ? agent.name || "" : "") + '"></div>' +
    '<div class="field"><label>focus (shown in the @-mention menu)</label><input type="text" id="m-focus" value="' + escAttr(agent ? agent.focus || "" : "") + '"></div>' +
    '<div class="field"><label>persona prompt (voice + focus + favoured tools)</label>' +
      '<textarea id="m-prompt" rows="5">' + esc(agent ? agent.persona_prompt || "" : "") + "</textarea></div>" +
    '<span class="micro">avatar</span>' +
    '<div class="av-controls" style="margin-top:6px">' +
      '<div><label>bg</label><input type="color" id="m-bg"></div>' +
      '<div><label>fg</label><input type="color" id="m-fg"></div>' +
      '<div><label>glyph (1-2)</label><input type="text" id="m-glyph" maxlength="2" style="width:100%;background:var(--inset);border:1px solid var(--hairline);border-radius:6px;padding:4px 8px"></div>' +
      '<div><label>horns</label><div style="display:flex;align-items:center;gap:6px;height:30px">' +
        '<input type="checkbox" id="m-horns">' +
        '<input type="color" id="m-horncolor" style="flex:1" title="horn color"></div></div>' +
    "</div>" +
    (isEdit ? "" : '<div class="notice" id="m-nokey-note"></div>') +
    '<div class="actions">' +
      '<button class="btn" data-act="modal-close">Cancel</button>' +
      '<button class="btn btn-primary" data-act="modal-save">' + (isEdit ? "Save" : "Create") + "</button>" +
    "</div>" +
    '<div class="notice" id="m-notice"></div>';

  const $ = (id) => modal.querySelector(id);
  $("#m-bg").value = M.av.bg; $("#m-fg").value = M.av.fg;
  $("#m-glyph").value = M.av.glyph || "";
  $("#m-horns").checked = M.av.accessory === "horns";
  $("#m-horncolor").value = M.av.horn_color || M.av.fg;
  if (!isEdit && S.cfg && S.cfg.agent_mode !== "live") {
    $("#m-nokey-note").textContent =
      "No API key connected — a new persona is created, but cannot post " +
      "analysis until a key is added.";
  }

  function currentAv() {
    return {
      bg: $("#m-bg").value, fg: $("#m-fg").value,
      glyph: $("#m-glyph").value.slice(0, 2),
      accessory: $("#m-horns").checked ? "horns" : "none",
      horn_color: $("#m-horns").checked ? $("#m-horncolor").value : undefined,
    };
  }
  function paint() {
    const av = currentAv();
    $("#m-preview").innerHTML =
      avatarSVG(av, 64) + " " + avatarSVG(av, 32) + " " + avatarSVG(av, 20);
    $("#m-p-handle").textContent = $("#m-handle").value || "@…";
    $("#m-p-name").textContent = $("#m-name").value || "";
  }
  $("#m-name").addEventListener("input", () => {
    if (isEdit) { paint(); return; }
    const dflt = defaultAvatar($("#m-name").value, $("#m-handle").value);
    if (!M.dirty.bg) $("#m-bg").value = dflt.bg;
    if (!M.dirty.fg) $("#m-fg").value = dflt.fg;
    if (!M.dirty.glyph) $("#m-glyph").value = dflt.glyph;
    paint();
  });
  $("#m-handle").addEventListener("input", paint);
  for (const [id, key] of [["#m-bg", "bg"], ["#m-fg", "fg"], ["#m-glyph", "glyph"]]) {
    $(id).addEventListener("input", () => { M.dirty[key] = true; paint(); });
  }
  $("#m-horns").addEventListener("input", paint);
  $("#m-horncolor").addEventListener("input", paint);
  paint();

  veil.hidden = false; modal.hidden = false;
}

function closeAgentModal() {
  document.getElementById("agent-modal").hidden = true;
  if (!S.runModal) document.getElementById("modal-veil").hidden = true;
}

async function saveAgentModal() {
  const modal = document.getElementById("agent-modal");
  const $ = (id) => modal.querySelector(id);
  const av = {
    bg: $("#m-bg").value, fg: $("#m-fg").value,
    glyph: $("#m-glyph").value.slice(0, 2),
    accessory: $("#m-horns").checked ? "horns" : "none",
  };
  if ($("#m-horns").checked) av.horn_color = $("#m-horncolor").value;
  const fields = {
    name: $("#m-name").value.trim() || null,
    focus: $("#m-focus").value.trim() || null,
    persona_prompt: $("#m-prompt").value.trim() || null,
    avatar_json: JSON.stringify(av),
  };
  if (M.mode === "create") {
    const handle = $("#m-handle").value.trim();
    if (!handle) { $("#m-notice").textContent = "handle is required"; return; }
    try {
      await api("/api/agents/" + M.room, { method: "POST",
        body: JSON.stringify(Object.assign({ handle: handle }, fields)) });
      toast("agent " + handle + " created in room " + M.room, "good");
      closeAgentModal();
      await refreshAgents(); renderAll();
    } catch (e) { $("#m-notice").textContent = "create failed: " + e.message; }
    return;
  }
  const a = M.agent;
  try {
    await api("/api/agents/" + a.room + "/" + a.id,
      { method: "PATCH", body: JSON.stringify(fields) });
    toast("agent " + a.handle + " saved", "good");
    closeAgentModal();
    await refreshAgents();
    renderAll();
    if (S.thread) reloadThread();
  } catch (e) {
    if (e.status === 404 || e.status === 405 || e.status === 501) {
      Object.assign(a, { name: fields.name, focus: fields.focus,
        persona_prompt: fields.persona_prompt, avatar_json: fields.avatar_json });
      S.agentsById[a.id] = a;
      toast("PATCH /api/agents not available yet (" + e.status +
        ") — change applied locally for this session only", "err");
      closeAgentModal();
      renderAll();
      if (S.thread) reloadThread();
    } else {
      document.getElementById("agent-modal").querySelector("#m-notice").textContent =
        "save failed: " + e.message;
    }
  }
}

/* ======================================================================
   SSE
   ====================================================================== */

function connectSSE() {
  if (S.es) { S.es.close(); S.es = null; S.esOk = false; }
  if (!S.currId || !S.runsById[S.currId]) { renderHeaderStatus(); return; }
  const runId = S.currId;
  const es = new EventSource("/api/runs/" + runId + "/events");
  S.es = es; S.esRun = runId;
  es.onopen = () => { S.esOk = true; renderHeaderStatus(); };
  es.onerror = () => { S.esOk = false; renderHeaderStatus(); };
  es.addEventListener("stage", (e) => {
    const row = tryJson(e.data, null);
    if (!row) return;
    S.stage2[row.id] = row;
    if (S.room === 2 && S.view === "room") renderDash();
  });
  es.addEventListener("run_status", (e) => {
    const r = tryJson(e.data, null);
    if (!r) return;
    S.runsById[r.id] = Object.assign(S.runsById[r.id] || {}, r);
    const idx = S.runs.findIndex(x => x.id === r.id);
    if (idx >= 0) S.runs[idx] = S.runsById[r.id]; else S.runs.unshift(r);
    renderRail(); renderFeedHead(); renderHeaderStatus();
    if (r.status === "done" && r.id === S.currId) {
      refreshDash(S.room); refreshScorecardDebounced(); refreshRuns(false);
      toast(runLabel(S.runsById[r.id]) + " done — outputs on the dashboard", "good");
    }
    if (r.status === "failed" && r.id === S.currId) {
      refreshDash(S.room);
      toast(runLabel(S.runsById[r.id]) + " FAILED — see room 2 stage detail", "err");
    }
    if (runStopped(r) && r.id === S.currId) {
      refreshDash(S.room);
      toast(runLabel(S.runsById[r.id]) + " stopped", "err");
    }
  });
  es.addEventListener("post", (e) => {
    const p = tryJson(e.data, null);
    if (!p) return;
    // only an AGENT post settles a pending thread — the human's own post
    // arrives on this stream too and must not clear its own wait state
    if (p.parent_id && S.pending[p.parent_id] &&
        String(p.author_label || "").startsWith("@")) {
      delete S.pending[p.parent_id];
    }
    if (p.room === S.room && S.view === "room") {
      refreshFeedActiveDebounced();
    } else {
      S.unread[p.room] = true; renderSwitcher();
      // §4: a pass running in a room you are not looking at still has to be
      // tracked, so its dot clears and its stage settles.
      if (passInFlight(p.room) && refreshFeedDebounced[p.room]) {
        refreshFeedDebounced[p.room]();
      }
    }
    if (S.thread && p.room === S.thread.room) reloadThreadDebounced();
    refreshScorecardDebounced();
  });
  es.addEventListener("notification", () => { refreshNotificationsDebounced(); });
  // §2/§3: the research stage broadcasts its result on the same stream.
  es.addEventListener("research", (e) => {
    const d = tryJson(e.data, null);
    if (!d) return;
    S.researchSignal = d;
    if (S.view === "research" && S.reports.asof) loadReports(S.reports.asof, true);
  });
}

/* ======================================================================
   panels (O) + global event wiring
   ====================================================================== */

function applyPanels() {
  const layout = document.getElementById("layout");
  layout.classList.toggle("dash-off", !!S.panels.dash);
  layout.classList.toggle("rail-off", !!S.panels.rail);
  const dc = document.getElementById("dash-chev");
  const rc = document.getElementById("rail-chev");
  if (dc) {
    dc.textContent = S.panels.dash ? "›" : "‹";
    dc.title = (S.panels.dash ? "show" : "hide") + " the dashboard   [";
  }
  if (rc) {
    rc.textContent = S.panels.rail ? "‹" : "›";
    rc.title = (S.panels.rail ? "show" : "hide") + " the controls   ]";
  }
}

function togglePanel(which) {
  S.panels[which] = !S.panels[which];
  lsSet("panel." + which, S.panels[which] ? "off" : "on");
  applyPanels();
}

function gotoRoom(r) {
  S.view = "room";
  S.room = r;
  S.unread[r] = false;
  S.scn.data = null; S.scn.err = null;
  renderAll(); buildFeedComposer();
  refreshFeed(r); refreshDash(r);
}

function bindGlobal() {
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-act]");
    if (!t) {
      if (S.notifOpen && !e.target.closest(".notif-pop")) {
        S.notifOpen = false; renderHeaderStatus();
      }
      return;
    }
    const act = t.dataset.act;
    if (!["bell", "notif-open", "notif-read-all"].includes(act) && S.notifOpen) {
      S.notifOpen = false; renderHeaderStatus();
    }
    if (act === "room") { gotoRoom(Number(t.dataset.room)); return; }
    if (act === "tab-research") {
      S.view = "research";
      // §14: the agents panel is contextual to the tab, so the rail
      // re-renders with it
      renderSwitcher(); renderDash(); renderRail(); renderResearch();
      // the month picker only offers months that HAVE a report or a run —
      // asking for any other month writes a file as a side effect
      refreshReportMonths().then(() => { if (S.view === "research") renderResearch(); });
      return;
    }
    if (act === "open-welcome") {
      if (window.openWelcome) window.openWelcome();
      return;
    }
    if (act === "theme-toggle") {
      const cur = document.documentElement.getAttribute("data-theme");
      const nxt = cur === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", nxt);
      lsSet("theme", nxt);
      renderHeaderStatus();
      return;
    }
    if (act === "bell") {
      S.notifOpen = !S.notifOpen;
      renderHeaderStatus();
      if (S.notifOpen) refreshNotifications();
      return;
    }
    if (act === "notif-open") {
      openNotification(t.dataset.id, t.dataset.room, t.dataset.thread);
      return;
    }
    if (act === "notif-read-all") { markAllNotificationsRead(); return; }
    if (act === "panel-toggle") { togglePanel(t.dataset.panel); return; }
    if (act === "open-thread") { e.preventDefault(); openThread(Number(t.dataset.id)); return; }
    if (act === "close-thread") { closeThread(); return; }
    if (act === "refresh-room") { refreshRoomPass(); return; }
    if (act === "stage-run") {
      e.stopPropagation();
      const s = t.dataset.stage;
      startStage(s === "research" ? "research" : Number(s));
      return;
    }
    if (act === "stage-run-all") { startRunAll(); return; }
    if (act === "agent-profile") {
      e.preventDefault(); e.stopPropagation();
      openAgentProfile(t.dataset.id);
      return;
    }
    if (act === "close-profile") { closeProfile(); return; }
    if (act === "profile-edit") {
      // §11: the profile edits IN PLACE — no second surface for one agent
      if (S.profile && S.profile.agent.id === Number(t.dataset.id)) {
        startProfileEdit();
      } else {
        const a = S.agentsById[Number(t.dataset.id)];
        if (a) openAgentProfile(a.id).then(startProfileEdit);
      }
      return;
    }
    if (act === "profile-cancel") { cancelProfileEdit(); return; }
    if (act === "profile-save") { saveProfileEdit(); return; }
    if (act === "attach-toggle") {
      e.preventDefault(); e.stopPropagation();
      toggleAttachment(t.dataset.id);
      return;
    }
    if (act === "attach") { e.stopPropagation(); return; }
    if (act === "story-source") {
      e.preventDefault(); e.stopPropagation();
      openThread(Number(t.dataset.id), Number(t.dataset.room) || S.room);
      return;
    }
    if (act === "profile-open-thread") {
      const room = Number(t.dataset.room) || S.room;
      closeProfile();
      if ([1, 2, 3].includes(room) && room !== S.room) {
        S.view = "room"; S.room = room; S.unread[room] = false;
        renderAll(); buildFeedComposer();
        refreshFeed(room); refreshDash(room);
      }
      openThread(Number(t.dataset.id), room);
      return;
    }
    if (act === "snapshot") { freshSnapshot(); return; }
    if (act === "quiet-toggle") {
      const k = t.dataset.key;
      S.quietOpen[k] = !S.quietOpen[k];
      renderFeed();
      return;
    }
    if (act === "run-model") { openRunModal(); return; }
    if (act === "run-modal-close") { closeRunModal(); return; }
    if (act === "run-pick") {
      const id = t.dataset.id;
      S.runModal.sel = id === "new" ? "new" : Number(id);
      renderRunModal();
      return;
    }
    if (act === "run-start") { startRunFromModal(); return; }
    if (act === "stop-run") { stopRun(); return; }
    if (act === "open-scenario") { loadScenario(S.scn.pct); return; }
    if (act === "scn-load") {
      const sel = document.getElementById("scn-pct");
      loadScenario(sel ? Number(sel.value) : S.scn.pct);
      return;
    }
    if (act === "gate-approve") { decideGate(Number(t.dataset.id), "approve", t); return; }
    if (act === "gate-reject") { decideGate(Number(t.dataset.id), "reject", t); return; }
    if (act === "wf-mode") {
      S.wfMode = t.dataset.mode === "mtm" ? "mtm" : "var";
      lsSet("wfMode", S.wfMode); renderDash(); return;
    }
    if (act === "edit-agent") {
      e.stopPropagation();
      const a = S.agentsById[Number(t.dataset.id)];
      if (a) openAgentModal("edit", a, a.room);
      return;
    }
    if (act === "new-agent") { openAgentModal("create", null, S.room); return; }
    if (act === "modal-close") { closeAgentModal(); return; }
    if (act === "modal-save") { saveAgentModal(); return; }
  });

  document.getElementById("thread-veil").addEventListener("click", closeThread);
  // wrapped, not passed by reference: the click event would arrive as
  // closeProfile's `force` argument and skip the unsaved-changes prompt
  document.getElementById("profile-veil").addEventListener(
    "click", () => closeProfile());
  document.getElementById("modal-veil").addEventListener("click", () => {
    if (S.runModal) closeRunModal();
    closeAgentModal();
  });

  // K: arrows on the focused switcher; 1/2/3 as global shortcuts.
  // O: [ and ] (plus Ctrl+arrows) collapse the side panels.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (S.notifOpen) { S.notifOpen = false; renderHeaderStatus(); return; }
      if (S.runModal) { closeRunModal(); return; }
      if (!document.getElementById("agent-modal").hidden) { closeAgentModal(); return; }
      if (S.profile) { closeProfile(); return; }
      if (S.thread) closeThread();
      return;
    }
    const typing = e.target && e.target.matches &&
      e.target.matches("input, textarea, select, [contenteditable='true']");
    const sw = document.getElementById("switch");
    if (!typing && sw && document.activeElement === sw &&
        (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      e.preventDefault();
      const d = e.key === "ArrowRight" ? 1 : -1;
      const base = S.view === "room" ? S.room : 1;
      gotoRoom(Math.min(3, Math.max(1, base + d)));
      sw.focus();
      return;
    }
    const modalOpen = !!S.runModal ||
      !document.getElementById("agent-modal").hidden;
    if (typing || modalOpen || e.metaKey || e.altKey) return;
    if (e.ctrlKey) {
      if (e.key === "ArrowLeft") { e.preventDefault(); togglePanel("dash"); }
      else if (e.key === "ArrowRight") { e.preventDefault(); togglePanel("rail"); }
      return;
    }
    if (e.key === "[") { e.preventDefault(); togglePanel("dash"); return; }
    if (e.key === "]") { e.preventDefault(); togglePanel("rail"); return; }
    if (["1", "2", "3"].includes(e.key)) { e.preventDefault(); gotoRoom(Number(e.key)); }
  });
}

/* ======================================================================
   toasts
   ====================================================================== */

function toast(msg, kind) {
  const box = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast" + (kind === "err" ? " err" : kind === "good" ? " good" : "");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.remove(); }, kind === "err" ? 9000 : 6000);
}

/* go */
boot();

/* =====================================================================
   Welcome dialog (PENDING-JUDGE §2/§3). Runs before the app is usable.
   A judge lands on a real saved cycle without a key; a key only becomes
   necessary to run their own. The key goes to the server and is held in
   memory there — this file never stores it.
   ===================================================================== */
(function welcomeDialog() {
  const scrim = document.getElementById("welcome");
  if (!scrim) return;
  const $ = (id) => document.getElementById(id);
  const foot = $("w-foot");

  const say = (msg, bad) => {
    foot.textContent = msg || "";
    foot.style.color = bad ? "var(--critical, #c0392b)" : "";
  };

  async function boot() {
    try {
      if (sessionStorage.getItem("cycleLoaded")) {
        window.__cycleLoaded = true;
        sessionStorage.removeItem("cycleLoaded");
      }
    } catch (e) { /* private mode: just show the dialog */ }
    let sess = {};
    try { sess = await (await fetch("/api/session")).json(); } catch (e) { /* offline */ }

    // The dialog ALWAYS opens. Skipping it once an operator was set meant
    // there was no way back to the cycle, model and effort pickers without
    // restarting the server — the one screen where you choose what you are
    // looking at became unreachable after the first visit.
    //
    // The exception is the reload that loading a cycle triggers itself:
    // that one carries __cycleLoaded, so the app opens on the cycle you
    // just asked for instead of asking again.
    S.session = sess;
    if (window.__cycleLoaded) {
      window.__cycleLoaded = false;
      scrim.hidden = true;
      return;
    }
    window.__forceWelcome = false;

    const models = sess.models || ["claude-opus-5"];
    const efforts = sess.efforts || ["low", "medium", "high"];
    $("w-model").innerHTML = models
      .map((m) => `<option${m === "claude-opus-5" ? " selected" : ""}>${m}</option>`).join("");
    $("w-effort").innerHTML = efforts
      .map((e) => `<option${e === "low" ? " selected" : ""}>${e}</option>`).join("");
    if (sess.operator) $("w-operator").value = sess.operator;

    let cycles = [];
    try { cycles = (await (await fetch("/api/cycles")).json()).cycles || []; } catch (e) { /* none */ }
    if (cycles.length) {
      $("w-cycle-wrap").hidden = false;
      $("w-cycle").innerHTML = cycles
        .map((c) => `<option value="${c.slug}">${c.label}</option>`).join("");
      const describe = () => {
        const c = cycles.find((x) => x.slug === $("w-cycle").value) || cycles[0];
        const n = (c.counts || {});
        $("w-cycle-note").textContent =
          `Real ${c.model || "Claude"} output at ${c.effort || "low"} effort, ` +
          `saved ${(c.saved_at || "").slice(0, 10)} — ${n.posts_published || 0} posts, ` +
          `${n.web_sources || 0} web sources.`;
      };
      $("w-cycle").addEventListener("change", describe);
      describe();
    } else {
      $("w-open").disabled = true;
      say("No saved cycle in this checkout — add an API key and start fresh.");
    }
    scrim.hidden = false;
    $("w-operator").focus();
  }

  async function saveSession() {
    const body = {
      operator: $("w-operator").value.trim(),
      model: $("w-model").value,
      effort: $("w-effort").value,
    };
    const key = $("w-key").value.trim();
    if (key) body.api_key = key;
    const r = await fetch("/api/session", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("w-key").value = "";          // never linger in the DOM
    return r.json();
  }

  function requireOperator() {
    if ($("w-operator").value.trim()) return true;
    say("Enter a name first — posts and gate decisions are signed.", true);
    $("w-operator").focus();
    return false;
  }

  $("w-open").addEventListener("click", async () => {
    if (!requireOperator()) return;
    $("w-open").disabled = true;
    say("Loading the saved cycle…");
    try {
      await saveSession();
      const slug = $("w-cycle").value;
      const r = await fetch(`/api/cycles/${slug}/load`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      scrim.hidden = true;
      try { sessionStorage.setItem("cycleLoaded", "1"); } catch (e) { /* private mode */ }
      location.reload();
    } catch (e) {
      $("w-open").disabled = false;
      say("Could not load that cycle — see the server log.", true);
    }
  });

  $("w-fresh").addEventListener("click", async () => {
    if (!requireOperator()) return;
    say("Checking for a key…");
    const sess = await saveSession();
    if (!sess.can_run) {
      say("Connect an API key to run your own cycle.", true);
      $("w-key").focus();
      return;
    }
    scrim.hidden = true;
  });

  window.openWelcome = function () { window.__forceWelcome = true; boot(); };
  boot();
})();
