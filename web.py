"""Status web server for claudeink.

A full-page, paper-styled companion to the e-ink panel: every limit
window the API reports (the panel only fits three), usage history with
a crosshair chart, and a live preview of the physical frame with a
refresh button. Stdlib only, so nothing new to install on the Pi.

Endpoints:
  GET  /           status page
  GET  /frame.png  latest rendered frame
  GET  /status     JSON: limits, updated timestamp, stale flag, meta
  GET  /history    JSON: ?hours=N (&points=M) usage history series
  GET  /payload    JSON: last raw API payload, for poking at
  POST /refresh    wake the main loop for an immediate fetch + render
"""

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class State:
    """Shared between the main render loop and the request handlers."""

    def __init__(self, meta=None):
        self._lock = threading.Lock()
        self._png = b""
        self._limits = []
        self._payload = None
        self._updated = None
        self._last_ok = None
        self._error = None
        self._stale = False
        self._meta = meta or {}
        self.refresh_event = threading.Event()

    def update(self, png, limits, stale, payload=None, error=None):
        with self._lock:
            self._png = png
            self._limits = limits
            self._stale = stale
            self._payload = payload
            self._error = error
            self._updated = datetime.now().astimezone()
            if not stale:
                self._last_ok = self._updated

    def note_error(self, error):
        """Surface a fetch failure when there's nothing to render yet."""
        with self._lock:
            self._error = error
            self._stale = True
            self._updated = datetime.now().astimezone()

    def png(self):
        with self._lock:
            return self._png

    def payload(self):
        with self._lock:
            return self._payload

    def status(self):
        with self._lock:
            return {
                "updated": self._updated.isoformat() if self._updated else None,
                "last_ok": self._last_ok.isoformat() if self._last_ok else None,
                "error": self._error,
                "stale": self._stale,
                "limits": self._limits,
                "meta": self._meta,
            }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>claudeink</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%23f5f2ea'/%3E%3Crect x='3' y='7' width='10' height='3' rx='1.5' fill='none' stroke='%231a1a1a'/%3E%3Crect x='3' y='7' width='6' height='3' rx='1.5' fill='%231a1a1a'/%3E%3C/svg%3E">
<style>
  :root {
    --paper: #f5f2ea;
    --paper-raised: #fbf9f3;
    --ink: #1a1a1a;
    --ink-2: #55524b;
    --ink-3: #8a8377;
    --rule: #c9c4b6;
    --hatch: repeating-linear-gradient(45deg,
              var(--ink) 0 3px, transparent 3px 6px);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --paper: #17181a;
      --paper-raised: #1e2023;
      --ink: #e8e5dc;
      --ink-2: #b0ac9f;
      --ink-3: #7c7869;
      --rule: #3a3c40;
    }
  }
  * { box-sizing: border-box; }
  body { background: var(--paper); color: var(--ink);
         font: 15px/1.5 "DejaVu Sans", "Helvetica Neue", Arial, sans-serif;
         margin: 0; padding: 24px 16px 48px;
         display: flex; justify-content: center; }
  main { width: 100%; max-width: 920px; }

  header { display: flex; align-items: baseline; gap: 10px;
           border-bottom: 2px solid var(--ink); padding-bottom: 8px; }
  header h1 { font-size: 24px; font-weight: 700; margin: 0; letter-spacing: -0.3px; }
  header .plan { color: var(--ink-2); font-size: 15px; }
  header .clock { margin-left: auto; font-weight: 700; font-size: 20px;
                  font-variant-numeric: tabular-nums; }
  #staleflag { color: var(--ink); font-weight: 700; display: none; }
  #stalebanner { display: none; margin-top: 16px; border: 2px solid var(--ink);
                 border-radius: 8px; padding: 12px 16px; background: var(--hatch),
                 var(--paper-raised); background-size: 100% 4px, auto;
                 background-repeat: repeat-x, repeat; background-position: top, center; }
  #stalebanner strong { display: block; margin-bottom: 2px; }
  #stalebanner .why { color: var(--ink-2); font-size: 13.5px;
                      overflow-wrap: anywhere; }

  .section { display: flex; align-items: center; gap: 10px;
             margin: 28px 0 14px; color: var(--ink-2);
             font-size: 12px; font-weight: 700; letter-spacing: 2px;
             text-transform: uppercase; }
  .section::after { content: ""; flex: 1; border-top: 1px solid var(--rule); }

  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
           gap: 12px; }
  .tile { background: var(--paper-raised); border: 1px solid var(--rule);
          border-radius: 8px; padding: 14px 16px; }
  .tile .label { font-size: 13px; font-weight: 700; }
  .tile .big { font-size: 40px; font-weight: 700; line-height: 1.15;
               font-variant-numeric: tabular-nums; }
  .tile .sub, .tile .delta { font-size: 12.5px; color: var(--ink-2); }
  .tile .delta { font-variant-numeric: tabular-nums; }

  .bars .row { display: grid; grid-template-columns: minmax(120px, 180px) 1fr 52px;
               gap: 12px; align-items: center; margin: 10px 0; }
  .bars .name { font-weight: 700; font-size: 14px; }
  .bars .sub { font-size: 12px; color: var(--ink-2); font-weight: 400; }
  .bars .pct { text-align: right; font-weight: 700;
               font-variant-numeric: tabular-nums; }
  .pill { height: 14px; border: 1.5px solid var(--ink); border-radius: 999px;
          overflow: hidden; background: var(--paper-raised); }
  .pill .fill { height: 100%; background: var(--ink); border-radius: 999px;
                min-width: 0; transition: width 0.6s ease; }
  .pill .fill.warn { background: var(--hatch); }

  .chartcard { background: var(--paper-raised); border: 1px solid var(--rule);
               border-radius: 8px; padding: 14px; }
  .chartbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
              margin-bottom: 8px; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px;
            color: var(--ink-2); margin-left: auto; }
  .legend svg { vertical-align: -2px; }
  .chip { background: none; border: 1px solid var(--rule); color: var(--ink-2);
          border-radius: 999px; padding: 3px 12px; font: inherit; font-size: 13px;
          cursor: pointer; }
  .chip[aria-pressed="true"] { background: var(--ink); color: var(--paper);
                               border-color: var(--ink); font-weight: 700; }
  #chartwrap { position: relative; }
  #chart { width: 100%; height: auto; display: block; touch-action: pan-y; }
  #tooltip { position: absolute; pointer-events: none; display: none;
             background: var(--ink); color: var(--paper); border-radius: 6px;
             padding: 7px 10px; font-size: 12.5px; line-height: 1.5;
             white-space: nowrap; z-index: 2;
             font-variant-numeric: tabular-nums; }
  #tooltip .t { font-weight: 700; }
  #chartempty { color: var(--ink-2); font-size: 13.5px; padding: 30px 8px;
                text-align: center; display: none; }

  .panelcard { display: flex; flex-wrap: wrap; gap: 20px; align-items: center;
               background: var(--paper-raised); border: 1px solid var(--rule);
               border-radius: 8px; padding: 16px; }
  .panelcard img { width: min(100%, 375px); image-rendering: pixelated;
                   border: 1px solid var(--rule); border-radius: 4px;
                   background: #fff; }
  .panelcard .side { flex: 1; min-width: 200px; }
  .panelcard .meta { color: var(--ink-2); font-size: 13px; margin-bottom: 12px; }
  button.action { background: var(--paper); color: var(--ink);
                  border: 1.5px solid var(--ink); border-radius: 6px;
                  padding: 9px 20px; font: inherit; font-weight: 700;
                  cursor: pointer; }
  button.action:hover:not(:disabled) { background: var(--ink); color: var(--paper); }
  button.action:disabled { opacity: 0.45; cursor: wait; }

  details { margin-top: 14px; }
  summary { cursor: pointer; color: var(--ink-2); font-size: 13.5px; }
  table { border-collapse: collapse; margin-top: 10px; font-size: 13.5px;
          font-variant-numeric: tabular-nums; }
  th, td { border: 1px solid var(--rule); padding: 5px 12px; text-align: left; }
  th { background: var(--paper-raised); }
  pre { background: var(--paper-raised); border: 1px solid var(--rule);
        border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 12px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>Plan usage</h1>
    <span class="plan" id="plan"></span>
    <span id="staleflag" title="last fetch failed; showing cached data">!</span>
    <span class="clock" id="clock">--:--</span>
  </header>

  <div id="stalebanner" role="alert">
    <strong>⚠ Showing stale data</strong>
    <span class="why" id="stalewhy"></span>
  </div>

  <div class="section">Now</div>
  <div class="tiles" id="tiles"></div>

  <div class="section">Limits</div>
  <div class="bars" id="bars"></div>

  <div class="section">History</div>
  <div class="chartcard">
    <div class="chartbar">
      <span id="ranges"></span>
      <span class="legend" id="legend"></span>
    </div>
    <div id="chartwrap">
      <svg id="chart" viewBox="0 0 880 300" role="img"
           aria-label="Usage history chart; data table below"></svg>
      <div id="tooltip"></div>
    </div>
    <div id="chartempty">No history yet — samples are recorded on every poll,
      so this fills in as claudeink runs.</div>
    <details>
      <summary>Data table</summary>
      <table id="histtable"></table>
    </details>
  </div>

  <div class="section">Panel</div>
  <div class="panelcard">
    <img id="frame" src="/frame.png" alt="current e-ink panel frame">
    <div class="side">
      <div class="meta">This is the frame on the physical display.
        <span id="updated"></span></div>
      <button class="action" id="btn">Refresh now</button>
    </div>
  </div>

  <details id="rawdetails"><summary>Raw API payload</summary><pre id="raw">–</pre></details>
</main>

<script>
"use strict";
const $ = id => document.getElementById(id);
const RANGES = [["6h", 6], ["24h", 24], ["7d", 168], ["30d", 720]];
// achromatic by design (e-ink): identity = lightness step + dash + labels
const SERIES_STYLE = [
  { dash: "",      var: "--ink"   },
  { dash: "7 5",   var: "--ink-2" },
  { dash: "2 4",   var: "--ink-3" },
  { dash: "10 4 2 4", var: "--ink-2" },
];
let status = null, lastUpdated = null, hours = 24, hist = null, deltas = {};

const css = name => getComputedStyle(document.body).getPropertyValue(name).trim();
const fmtPct = p => p == null ? "--" : Math.round(p) + "%";

function resetText(item) {
  if (!item.resets_at) return "no reset data";
  const reset = new Date(item.resets_at);
  if (item.weekly)
    return "resets " + reset.toLocaleDateString([], { weekday: "short" }) +
           " " + reset.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const mins = Math.round((reset - Date.now()) / 60000);
  if (mins <= 0) return "resetting now";
  const h = Math.floor(mins / 60), m = mins % 60;
  return "resets in " + (h ? h + " hr " : "") + m + " min";
}

function renderNow() {
  if (!status) return;
  $("plan").textContent = status.meta.plan || "";
  $("staleflag").style.display = status.stale ? "inline" : "none";
  $("stalebanner").style.display = status.stale ? "block" : "none";
  if (status.stale) {
    const since = status.last_ok
      ? "Last successful update " + new Date(status.last_ok).toLocaleString([], {
          weekday: "short", hour: "2-digit", minute: "2-digit" }) + ". "
      : "No successful update since the service started. ";
    $("stalewhy").textContent = since + (status.error || "");
  }

  const tiles = status.limits.map(item => {
    const d = deltas[item.label];
    const delta = d == null ? "" :
      `<div class="delta">${d >= 0.05 ? "▲" : d <= -0.05 ? "▼" : "·"} ` +
      `${Math.abs(d).toFixed(1)} pts/hr</div>`;
    return `<div class="tile"><div class="label">${item.label}</div>` +
           `<div class="big">${fmtPct(item.percent)}</div>` +
           `<div class="sub">${resetText(item)}</div>${delta}</div>`;
  });
  $("tiles").innerHTML = tiles.join("");

  const warnAt = status.meta.warn_at || 80;
  let html = "", weeklyMarked = false;
  for (const item of status.limits) {
    if (item.weekly && !weeklyMarked) {
      weeklyMarked = true;
      html += `<div class="section" style="margin:16px 0 4px">Weekly</div>`;
    }
    const pct = item.percent || 0;
    html += `<div class="row" title="${item.label}: ${fmtPct(item.percent)}">` +
      `<div class="name">${item.label}<div class="sub">${resetText(item)}</div></div>` +
      `<div class="pill"><div class="fill${pct >= warnAt ? " warn" : ""}"` +
      ` style="width:${pct}%"></div></div>` +
      `<div class="pct">${fmtPct(item.percent)}</div></div>`;
  }
  $("bars").innerHTML = html;
}

// ---- history chart -------------------------------------------------------

function seriesOrder(names) {
  const order = status ? status.limits.map(l => l.label) : [];
  return [...names].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
}

function chartGeom() {
  // shrink the drawing space on narrow screens so text stays legible
  const w = $("chartwrap").clientWidth || 880;
  const W = w >= 700 ? 880 : Math.max(420, Math.round(w * 1.25));
  const compact = W < 560;
  return { W, H: 300, L: 46, R: compact ? 42 : 108, T: 14, B: 30, compact };
}

function drawChart() {
  const svg = $("chart"), g = chartGeom();
  svg.setAttribute("viewBox", "0 0 " + g.W + " " + g.H);
  svg.innerHTML = "";
  $("tooltip").style.display = "none";
  const names = hist ? seriesOrder(Object.keys(hist.series)) : [];
  const has = names.some(n => hist.series[n].length > 1);
  $("chartempty").style.display = has ? "none" : "block";
  svg.style.display = has ? "block" : "none";
  drawLegend(names);
  if (!has) { $("histtable").innerHTML = ""; return; }

  const x = t => g.L + (t - hist.from) / (hist.to - hist.from) * (g.W - g.L - g.R);
  const y = p => g.T + (1 - p / 100) * (g.H - g.T - g.B);
  const el = (tag, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    svg.appendChild(node);
    return node;
  };

  for (const p of [0, 25, 50, 75, 100]) {           // recessive grid
    el("line", { x1: g.L, x2: g.W - g.R, y1: y(p), y2: y(p),
                 stroke: css("--rule"), "stroke-width": 1 });
    el("text", { x: g.L - 8, y: y(p) + 4, "text-anchor": "end",
                 "font-size": 11, fill: css("--ink-2") }, p + "%");
  }
  const warnAt = status && status.meta.warn_at;
  if (warnAt) {
    el("line", { x1: g.L, x2: g.W - g.R, y1: y(warnAt), y2: y(warnAt),
                 stroke: css("--ink-3"), "stroke-width": 1,
                 "stroke-dasharray": "3 4" });
    el("text", { x: g.W - g.R + 6, y: y(warnAt) + 4, "font-size": 10,
                 fill: css("--ink-3") }, "warn " + warnAt + "%");
  }
  const span = hist.to - hist.from;
  for (let i = 0; i <= 4; i++) {                     // x ticks
    const t = hist.from + span * i / 4;
    const d = new Date(t * 1000);
    const label = span > 48 * 3600
      ? d.toLocaleDateString([], { day: "numeric", month: "short" })
      : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    el("text", { x: x(t), y: g.H - 8, "text-anchor": "middle",
                 "font-size": 11, fill: css("--ink-2") }, label);
  }

  const endLabels = [];
  names.forEach((name, i) => {
    const pts = hist.series[name];
    if (!pts.length) return;
    const style = SERIES_STYLE[i % SERIES_STYLE.length];
    el("path", {
      d: pts.map((p, j) => (j ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1)).join(""),
      fill: "none", stroke: css(style.var), "stroke-width": 2,
      "stroke-dasharray": style.dash, "stroke-linejoin": "round",
    });
    const last = pts[pts.length - 1];
    if (!g.compact)
      endLabels.push({ name, i, x: x(last[0]) + 6, y: y(last[1]) + 4 });
  });
  endLabels.sort((a, b) => a.y - b.y);               // nudge collisions apart
  for (let i = 1; i < endLabels.length; i++)
    if (endLabels[i].y - endLabels[i - 1].y < 14)
      endLabels[i].y = endLabels[i - 1].y + 14;
  for (const lb of endLabels)
    el("text", { x: lb.x, y: lb.y, "font-size": 11.5, "font-weight": 700,
                 fill: css(SERIES_STYLE[lb.i % SERIES_STYLE.length].var) }, lb.name);

  drawHistTable(names);
  attachHover(svg, g, names, x, y);
}

function drawLegend(names) {
  $("legend").innerHTML = names.map((name, i) => {
    const style = SERIES_STYLE[i % SERIES_STYLE.length];
    return `<span><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4"` +
      ` stroke="${css(style.var)}" stroke-width="2"` +
      ` stroke-dasharray="${style.dash}"/></svg> ${name}</span>`;
  }).join("");
}

function drawHistTable(names) {
  const rows = names.map(name => {
    const vals = hist.series[name].map(p => p[1]);
    if (!vals.length) return "";
    const min = Math.min(...vals), max = Math.max(...vals);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return `<tr><td>${name}</td><td>${fmtPct(vals[vals.length - 1])}</td>` +
      `<td>${fmtPct(min)}</td><td>${fmtPct(avg)}</td><td>${fmtPct(max)}</td></tr>`;
  });
  $("histtable").innerHTML =
    "<tr><th>Window</th><th>Now</th><th>Min</th><th>Avg</th><th>Max</th></tr>" +
    rows.join("") +
    `<tr><td colspan="5">over the selected ${RANGES.find(r => r[1] === hours)[0]} range</td></tr>`;
}

function attachHover(svg, g, names, x, y) {
  const tip = $("tooltip");
  const cross = document.createElementNS("http://www.w3.org/2000/svg", "line");
  cross.setAttribute("stroke", css("--ink-3"));
  cross.setAttribute("stroke-width", 1);
  cross.style.display = "none";
  svg.appendChild(cross);

  svg.onpointerleave = () => { tip.style.display = "none"; cross.style.display = "none"; };
  svg.onpointermove = ev => {
    const box = svg.getBoundingClientRect();
    const vx = (ev.clientX - box.left) / box.width * g.W;
    const t = hist.from + (vx - g.L) / (g.W - g.L - g.R) * (hist.to - hist.from);
    if (t < hist.from || t > hist.to) { svg.onpointerleave(); return; }
    let tx = null;
    const lines = names.map((name, i) => {
      const pts = hist.series[name];
      if (!pts.length) return "";
      let lo = 0, hi = pts.length - 1;             // nearest sample
      while (hi - lo > 1) { const mid = (lo + hi) >> 1;
        (pts[mid][0] < t) ? lo = mid : hi = mid; }
      const p = Math.abs(pts[lo][0] - t) < Math.abs(pts[hi][0] - t) ? pts[lo] : pts[hi];
      if (tx == null) tx = p[0];
      return `${name} ${fmtPct(p[1])}`;
    }).filter(Boolean);
    if (tx == null) return;

    cross.setAttribute("x1", x(tx)); cross.setAttribute("x2", x(tx));
    cross.setAttribute("y1", g.T); cross.setAttribute("y2", g.H - g.B);
    cross.style.display = "block";
    const when = new Date(tx * 1000);
    tip.innerHTML = `<div class="t">${when.toLocaleString([], {
      weekday: "short", hour: "2-digit", minute: "2-digit" })}</div>` +
      lines.join("<br>");
    tip.style.display = "block";
    const px = (x(tx) / g.W) * box.width;
    const flip = px > box.width * 0.65;
    tip.style.left = flip ? "" : (px + 14) + "px";
    tip.style.right = flip ? (box.width - px + 14) + "px" : "";
    tip.style.top = Math.max(0, ev.clientY - box.top - 30) + "px";
  };
}

// ---- data ----------------------------------------------------------------

async function loadHistory() {
  try {
    hist = await (await fetch("/history?hours=" + hours)).json();
    drawChart();
  } catch (e) { /* keep the last chart */ }
}

async function loadDeltas() {
  try {
    const h = await (await fetch("/history?hours=1&points=120")).json();
    deltas = {};
    for (const name in h.series) {
      const pts = h.series[name];
      if (pts.length < 2) continue;
      const dt = (pts[pts.length - 1][0] - pts[0][0]) / 3600;
      if (dt > 0.2) deltas[name] = (pts[pts.length - 1][1] - pts[0][1]) / dt;
    }
  } catch (e) { deltas = {}; }
}

async function poll() {
  try {
    status = await (await fetch("/status")).json();
    if (status.updated && status.updated !== lastUpdated) {
      lastUpdated = status.updated;
      $("frame").src = "/frame.png?t=" + encodeURIComponent(status.updated);
      await loadDeltas();
      loadHistory();
    }
    const when = status.updated ? new Date(status.updated).toLocaleTimeString() : "never";
    $("updated").textContent = "Updated " + when + (status.stale ? " (stale data)" : "") + ".";
    renderNow();
  } catch (e) {
    $("updated").textContent = "Unreachable.";
  }
}

$("ranges").innerHTML = RANGES.map(([label, h]) =>
  `<button class="chip" data-h="${h}" aria-pressed="${h === hours}">${label}</button>`).join(" ");
$("ranges").onclick = ev => {
  const btn = ev.target.closest(".chip");
  if (!btn) return;
  hours = +btn.dataset.h;
  for (const c of document.querySelectorAll(".chip"))
    c.setAttribute("aria-pressed", String(+c.dataset.h === hours));
  loadHistory();
};

$("btn").onclick = async () => {
  const btn = $("btn"), before = lastUpdated;
  btn.disabled = true;
  try { await fetch("/refresh", { method: "POST" }); } catch (e) {}
  for (let i = 0; i < 20 && lastUpdated === before; i++) {
    await new Promise(r => setTimeout(r, 1000));
    await poll();
  }
  btn.disabled = false;
};

$("rawdetails").addEventListener("toggle", async ev => {
  if (!ev.target.open) return;
  try {
    const raw = await (await fetch("/payload")).json();
    $("raw").textContent = JSON.stringify(raw, null, 2);
  } catch (e) { $("raw").textContent = "unavailable"; }
});

function tickClock() {
  $("clock").textContent = new Date().toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit" });
  renderNow();  // keeps the countdowns honest
}
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (hist) drawChart(); }, 150);
});

tickClock();
setInterval(tickClock, 30000);
poll();
setInterval(poll, 15000);
</script>
</body>
</html>
"""


def make_handler(state, history_query):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, "application/json", json.dumps(obj).encode())

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif url.path == "/frame.png":
                png = state.png()
                if png:
                    self._send(200, "image/png", png)
                else:
                    self._send(503, "text/plain", b"no frame rendered yet\n")
            elif url.path == "/status":
                self._json(state.status())
            elif url.path == "/history":
                if history_query is None:
                    self._json({"series": {}, "from": 0, "to": 0})
                    return
                params = parse_qs(url.query)
                try:
                    hours = min(24 * 45, max(1, int(params.get("hours", ["24"])[0])))
                    points = min(2000, max(10, int(params.get("points", ["600"])[0])))
                except ValueError:
                    self._json({"error": "bad hours/points"}, 400)
                    return
                self._json(history_query(hours, points))
            elif url.path == "/payload":
                self._json(state.payload() or {})
            else:
                self._send(404, "text/plain", b"not found\n")

        def do_POST(self):
            if urlparse(self.path).path == "/refresh":
                state.refresh_event.set()
                self._send(202, "application/json", b'{"refreshing": true}\n')
            else:
                self._send(404, "text/plain", b"not found\n")

        def log_message(self, fmt, *args):
            pass  # keep journald to the render loop's own log lines

    return Handler


def start(port, history_query=None, meta=None):
    """Run the server on a daemon thread; returns the shared State."""
    state = State(meta)
    server = ThreadingHTTPServer(("", port), make_handler(state, history_query))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return state
