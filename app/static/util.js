/* util.js — escaping, formatting, and a small safe markdown renderer.
   SECURITY: every piece of user/agent text passes through esc() before it
   is ever concatenated into HTML. The markdown renderer operates ONLY on
   escaped text and emits a fixed whitelist of tags it constructs itself. */

"use strict";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* attribute-safe (same as esc; kept separate for intent at call sites) */
function escAttr(s) { return esc(s); }

/* ---- number formatting (mono, tabular) --------------------------------- */

function fmtMoney(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v), sign = v < 0 ? "-" : "";
  if (a >= 1e9) return sign + "£" + (a / 1e9).toFixed(3) + "bn";
  if (a >= 1e6) return sign + "£" + (a / 1e6).toFixed(2) + "m";
  if (a >= 1e3) return sign + "£" + (a / 1e3).toFixed(1) + "k";
  return sign + "£" + a.toFixed(2);
}
function fmtSignedMoney(v) {
  if (v == null || isNaN(v)) return "—";
  return (v >= 0 ? "+" : "") + fmtMoney(v);
}
function fmtPct(v, dp) {
  if (v == null || isNaN(v)) return "—";
  return (v * 100).toFixed(dp == null ? 2 : dp) + "%";
}
function fmtBp(v, dp) {
  if (v == null || isNaN(v)) return "—";
  return (v * 1e4).toFixed(dp == null ? 1 : dp) + "bp";
}
function fmtNum(v, dp) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toLocaleString("en-GB", {
    minimumFractionDigits: dp == null ? 0 : dp,
    maximumFractionDigits: dp == null ? 4 : dp,
  });
}
function shortSha(s, n) {
  if (!s || typeof s !== "string") return null;
  return s.slice(0, n || 8);
}
function fmtTs(iso) {
  if (!iso) return "";
  // "2026-08-28T10:22:31.123+00:00" / "2026-08-28 10:22:31" -> "10:22:31"
  const m = String(iso).match(/[T ](\d{2}:\d{2}:\d{2})/);
  if (m) return m[1];
  return String(iso);
}
function fmtTsFull(iso) {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  return m ? m[1] + " " + m[2] : String(iso);
}

function debounce(fn, ms) {
  let t = null;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), ms);
  };
}

function tryJson(s, fallback) {
  if (s == null || s === "") return fallback;
  if (typeof s === "object") return s;
  try { return JSON.parse(s); } catch (_) { return fallback; }
}

/* ---- markdown (tiny, safe) --------------------------------------------- */
/* Supported: # headings, - bullets, | tables |, paragraphs, blank lines,
   inline: `code`, **bold**, *italic*, @mentions (via opts.mention).
   Input is escaped FIRST; the renderer never passes raw input to HTML. */

function mdInline(escaped, opts) {
  const codeTokens = [];
  let s = escaped.replace(/`([^`]+)`/g, (_, c) => {
    codeTokens.push("<code>" + c + "</code>");
    return "\uE000" + (codeTokens.length - 1) + "\uE001";
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  if (opts && opts.mention) {
    s = s.replace(/(^|[\s(&;>.,:!?·—-])@([a-z0-9][a-z0-9-]*)/gi,
      (m, pre, h) => {
        const chip = opts.mention("@" + h.toLowerCase());
        return chip ? pre + chip : m;
      });
  }
  s = s.replace(/\uE000(\d+)\uE001/g, (_, i) => codeTokens[Number(i)] || "");
  return s;
}

function mdRender(raw, opts) {
  const lines = esc(raw).split(/\r?\n/);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    // table block
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const block = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        block.push(lines[i]); i++;
      }
      out.push(mdTable(block, opts));
      continue;
    }
    // heading
    const hm = line.match(/^(#{1,3})\s+(.*)$/);
    if (hm) {
      const lvl = hm[1].length;
      out.push("<h" + lvl + ">" + mdInline(hm[2], opts) + "</h" + lvl + ">");
      i++; continue;
    }
    // bullet list
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push("<li>" + mdInline(lines[i].replace(/^\s*[-*]\s+/, ""), opts) + "</li>");
        i++;
      }
      out.push("<ul>" + items.join("") + "</ul>");
      continue;
    }
    // paragraph: gather until blank / structural line
    const para = [];
    while (i < lines.length && lines[i].trim() &&
           !/^\s*[-*]\s+/.test(lines[i]) &&
           !/^\s*\|.*\|\s*$/.test(lines[i]) &&
           !/^#{1,3}\s+/.test(lines[i])) {
      para.push(mdInline(lines[i], opts));
      i++;
    }
    out.push("<p>" + para.join("<br>") + "</p>");
  }
  return out.join("");
}

function mdTable(blockLines, opts) {
  const rows = blockLines.map(l =>
    l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim()));
  let body = "";
  let start = 0;
  const hasSep = rows.length > 1 && rows[1].every(c => /^:?-{2,}:?$/.test(c));
  if (hasSep) {
    body += "<thead><tr>" +
      rows[0].map(c => "<th>" + mdInline(c, opts) + "</th>").join("") +
      "</tr></thead>";
    start = 2;
  }
  body += "<tbody>";
  for (let r = start; r < rows.length; r++) {
    if (rows[r].every(c => /^:?-{2,}:?$/.test(c))) continue;
    body += "<tr>" + rows[r].map(c => "<td>" + mdInline(c, opts) + "</td>").join("") + "</tr>";
  }
  body += "</tbody>";
  return '<div class="md-table-wrap"><table>' + body + "</table></div>";
}
