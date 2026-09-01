/* avatar.js — SPEC-APP §8.1: avatars are data, rendered client-side as
   inline SVG from avatar_json {bg, fg, glyph, accessory[, horn_color]}.
   Default rule: glyph = 1-2 initials from the name, bg from a curated
   12-hue palette (deterministic per handle/name when the server row carries
   no avatar_json yet), fg picked white/near-black by bg luminance. */

"use strict";

/* Curated ~12 saturated hues (purples, teals, ambers, blues…). */
const AV_PALETTE = [
  "#6A51A3", // purple
  "#0F766E", // teal
  "#B45309", // amber
  "#1D4ED8", // blue
  "#9D174D", // magenta
  "#1B7837", // green
  "#B91C1C", // red
  "#0E7490", // cyan
  "#7C2D92", // violet
  "#92400E", // brown-amber
  "#2C7FB8", // steel blue
  "#4D7C0F", // olive
];

function avHash(s) {
  let h = 5381;
  const str = String(s || "");
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h + str.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

function avLuminance(hex) {
  const m = String(hex || "").match(/^#?([0-9a-f]{6})$/i);
  if (!m) return 0;
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
  const lin = (c) => {
    c /= 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function avFgFor(bg) {
  return avLuminance(bg) > 0.42 ? "#0B0B0B" : "#FFFFFF";
}

/* "Private Credit" -> "PC"; "curve-check" -> "CC"; single word -> first letter */
function avInitials(name, handle) {
  let src = (name || "").trim();
  if (!src && handle) src = String(handle).replace(/^@/, "").replace(/-/g, " ");
  if (!src) return "?";
  const words = src.split(/[\s_-]+/).filter(Boolean);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return words[0][0].toUpperCase();
}

/* Shipped defaults for builtins whose avatar is part of the spec (§8.1):
   @red-team is a red circle with two little yellow horns. Applied only when
   the server row carries no stored avatar_json (the stored one always wins). */
const AV_SHIPPED = {
  "@red-team": { bg: "#C0392B", fg: "#FFFFFF", glyph: "RT",
                 accessory: "horns", horn_color: "#F1C40F" },
};

/* The default rule, applied when a row has no stored avatar_json. */
function defaultAvatar(name, handle) {
  const shipped = AV_SHIPPED[String(handle || "").toLowerCase()];
  if (shipped) return Object.assign({}, shipped);
  const bg = AV_PALETTE[avHash(handle || name) % AV_PALETTE.length];
  return { bg: bg, fg: avFgFor(bg), glyph: avInitials(name, handle),
           accessory: "none" };
}

function resolveAvatar(agentLike) {
  // agentLike: {name, handle, avatar_json?} (avatar_json string or object)
  const av = tryJson(agentLike && agentLike.avatar_json, null);
  if (av && av.bg) {
    return {
      bg: av.bg,
      fg: av.fg || avFgFor(av.bg),
      glyph: av.glyph != null ? av.glyph
             : avInitials(agentLike.name, agentLike.handle),
      accessory: av.accessory || "none",
      horn_color: av.horn_color || null,
    };
  }
  return defaultAvatar(agentLike && agentLike.name,
                       agentLike && agentLike.handle);
}

/* Inline SVG circle avatar. Crisp at 32px; horns rise from the top edge.
   All dynamic values are escaped/validated before insertion. */
function avatarSVG(av, size) {
  size = size || 32;
  const okColor = (c, fb) =>
    /^#[0-9a-fA-F]{3,8}$/.test(String(c || "")) ? c : fb;
  const bg = okColor(av.bg, "#52514E");
  const fg = okColor(av.fg, avFgFor(bg));
  const glyph = String(av.glyph == null ? "" : av.glyph).slice(0, 2);
  const horns = av.accessory === "horns";
  const hornColor = okColor(av.horn_color, fg);

  // viewBox 0 0 32 34: circle center (16,18) r 14; horns poke above y=4.
  let inner = "";
  if (horns) {
    inner +=
      '<path d="M6.6 10.4 L3.0 1.6 L11.2 5.6 Z" fill="' + hornColor + '"/>' +
      '<path d="M25.4 10.4 L29.0 1.6 L20.8 5.6 Z" fill="' + hornColor + '"/>';
  }
  inner += '<circle cx="16" cy="19" r="14" fill="' + bg + '"/>';
  if (glyph) {
    const fs = glyph.length > 1 ? 12.5 : 14.5;
    inner += '<text x="16" y="19.5" text-anchor="middle" ' +
      'dominant-baseline="central" font-family="ui-monospace,Consolas,monospace" ' +
      'font-size="' + fs + '" font-weight="700" fill="' + fg + '">' +
      esc(glyph) + "</text>";
  }
  return '<svg class="av" width="' + size + '" height="' + (size * 34 / 32) +
    '" viewBox="0 0 32 34" role="img" aria-label="avatar">' + inner + "</svg>";
}

function agentAvatarHTML(agentLike, size) {
  return avatarSVG(resolveAvatar(agentLike || {}), size);
}
