/*
 * OmniVista Cirrus network map card for Home Assistant.
 * type: custom:ovcirrus-network-map
 * Options: entry_id, site (id or name), refresh (s, default 30), expand_clients (bool),
 *          max_columns (default 3), height (px, default 640), title,
 *          text_size (pt size of device names on the map, default 12pt; other map labels scale with it;
 *                     viewers can change it in the View menu; the old font_scale multiplier still works),
 *          layout_editing ("admin" default, true, or false: who may drag devices and save the layout),
 *          snap_to_grid (default true), grid_size (px, default 20),
 *          height: "fill" makes the map fill its container (used by the sidebar panel),
 *          device_size (size of gateways/switches/APs/wired devices, default 1),
 *          client_size (size of wireless and wired clients, default 1); viewers can change both.
 *          1x is 3.75 times the original ring size; the old device_scale / client_scale still work.
 */
const CARD_VERSION = "0.2.11"; // kept in step with manifest.json by scripts/release.sh
const CHIP_W = 210; // minimum device box width
const MAX_CHIP_W = 380; // boxes grow to fit their text up to this (times the text size), then ellipsis
const BASE = { CHIP_H: 54, LABEL_H: 24, LABEL_ROW: 19 };
let CHIP_H = BASE.CHIP_H;
const GAP_X = 22;
const ROW_GAP = 12;
const PAD = 14;
let LABEL_H = BASE.LABEL_H; // title row inside the box: PAD above the title, then title + gap before the contents
const V_GAP = 64;
const TRUNK = 28;
let LABEL_ROW = BASE.LABEL_ROW; // height of one link label row inside a group box
let FS = 1; // text size multiplier for the map (text size in px / NAME_PX)
// The map text size is set as the pt size of device names; every other map label keeps its proportion.
const NAME_PX = 13; // .name font-size in the CSS below, i.e. the size at FS = 1
const PT_PX = 4 / 3; // 1pt = 1.333px
const TEXT_DEFAULT = 12, TEXT_MIN = 8, TEXT_MAX = 30, TEXT_KEY = "ovcirrus-text-pt";
const textFs = (pt) => ((pt || TEXT_DEFAULT) * PT_PX) / NAME_PX;
const FS_KEY = "ovcirrus-font-scale"; // legacy multiplier, read once and converted
const clampText = (v) => Math.round(Math.min(TEXT_MAX, Math.max(TEXT_MIN, Number(v) || TEXT_DEFAULT)));
// Old saved multipliers were relative to the default size, so 1.0 maps onto the new default.
const legacyText = (m) => (Number(m) ? clampText(Number(m) * TEXT_DEFAULT) : null);
const configText = (cfg) => (Number(cfg?.text_size) ? clampText(cfg.text_size) : legacyText(cfg?.font_scale) || TEXT_DEFAULT);
const viewText = (view) => (Number(view?.text) ? clampText(view.text) : legacyText(view?.font));
function applyFontScale(fs) {
  FS = fs;
  const up = Math.max(1, fs);
  CHIP_H = Math.round(BASE.CHIP_H * (0.55 + 0.45 * up));
  LABEL_H = Math.round(BASE.LABEL_H * up);
  LABEL_ROW = Math.round(BASE.LABEL_ROW * Math.max(0.85, fs));
}
const ZOOM_KEY = "ovcirrus-zoom";
const BUNDLE_STEP = 16; // spacing between parallel link lines
const BAND_GAP = 2 * 22 + 1; // space between frequency-band sections in a client box (gap, dotted line, gap)
const bandLabel = (b) => (b ? String(b).replace(/\s*ghz$/i, " GHz").replace(/^(\d)ghz/i, "$1 GHz") : "Other");
const bandOrder = (b) => {
  const n = parseFloat(b);
  return isNaN(n) ? 99 : n;
};

// Devices and clients are drawn at SIZE_BASE times the 42px ring, times the viewer's size (1x default);
// the Internet node stays at 42px. Older settings were raw multipliers of 42px, so they are divided by SIZE_BASE.
const SIZE_BASE = 3.75;
const BIG_KINDS = new Set(["gateway", "switch", "ap", "bridge", "endpoint", "manual"]);
let DEVICE_SCALE = SIZE_BASE;
const DS_MIN = 0.5, DS_MAX = 2, DS_STEP = 0.25, DS_KEY = "ovcirrus-device-size", DS_OLD_KEY = "ovcirrus-device-scale";
const CLIENT_KINDS = new Set(["wireless", "wired"]);
let CLIENT_SCALE = SIZE_BASE;
const CS_MIN = 0.25, CS_MAX = 2, CS_STEP = 0.25, CS_KEY = "ovcirrus-client-size", CS_OLD_KEY = "ovcirrus-client-scale";
const clampSize = (v, lo, hi) => (Number(v) ? Math.min(hi, Math.max(lo, Math.round(Number(v) * 4) / 4)) : null);
// size in the new units from a new value, else from an old raw multiplier
const sizeFrom = (v, old, lo, hi) => clampSize(v, lo, hi) || (Number(old) ? clampSize(Number(old) / SIZE_BASE, lo, hi) : null);
const ringSize = (n) =>
  BIG_KINDS.has(n.kind) ? Math.round(42 * DEVICE_SCALE) : CLIENT_KINDS.has(n.kind) ? Math.round(42 * CLIENT_SCALE) : 42;
const chipH = (n) =>
  BIG_KINDS.has(n.kind) ? Math.max(CHIP_H, ringSize(n) + 14) : CLIENT_KINDS.has(n.kind) ? Math.max(CHIP_H, ringSize(n) + 12) : CHIP_H;

// Devices that can be dragged: infrastructure, wired and manual devices. Wireless clients follow their AP.
const SNAP_KEY = "ovcirrus-snap-to-grid";
const DRAGGABLE = new Set(["gateway", "switch", "ap", "bridge", "endpoint", "manual", "wired"]);

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);


// ---------- device graphics (drawn from model number + live port state) ----------
const portState = (g, n) => {
  const p = g.ports?.[String(n)];
  if (!p) return { cls: g.known ? "down" : "unknown", p: null };
  if (!p.admin) return { cls: "off", p };
  return { cls: p.up ? "up" : "down", p };
};

function switchSvg(g) {
  // Drawn at about 1 unit = 1px in the wide switch panel, so the port numbers read at their CSS size (10px).
  // Odd ports sit on the top row with their number above, even ports below with the number underneath.
  const PW = 19, PH = 14, GX = 3, BLOCK = 6, LEFT = 10, BG = 10;
  const cols = Math.ceil(g.copper / 2);
  const blockGap = (c) => Math.floor(c / BLOCK) * BG;
  const copperW = cols ? cols * (PW + GX) - GX + blockGap(cols - 1) : 0;
  const sfpCols = Math.ceil(g.sfp / 2);
  const stackCols = Math.ceil(g.stack / 2);
  const SW = 21, QW = 26;
  const sfpX = LEFT + copperW + (copperW ? 14 : 0);
  const stackX = sfpX + (sfpCols ? sfpCols * (SW + 3) - 3 + 12 : 0);
  const W = Math.max(stackX + (stackCols ? stackCols * (QW + 3) - 3 : 0) + LEFT, 180);
  const rowY = [48, 70];
  const H = 106;
  const parts = [];
  const port = (n, x, row, w, kind) => {
    const { cls, p } = portState(g, n);
    const y = rowY[row];
    const ledY = row === 0 ? y - 6 : y + PH + 3;
    const numY = row === 0 ? y - 10 : y + PH + 17;
    const vl = p ? [p.u?.length ? `untagged ${p.u.join(", ")}` : "", p.t?.length ? `tagged ${p.t.join(", ")}` : ""].filter(Boolean).join(" · ") : "";
    const tip = `1/1/${n} · ${p ? (p.admin ? (p.up ? "up" : "down") : "disabled") : "no data"}${p?.label ? " · " + p.label : ""}${vl ? " · VLAN " + vl : ""}`;
    const inVlan = p && VLAN_SELECTED !== null && carries({ untagged: p.u, tagged: p.t }, VLAN_SELECTED);
    parts.push(`<g class="port ${p?.label ? "linked" : ""} ${inVlan ? "invlan" : ""}" ${inVlan ? `style="--c:${vlanColor(VLAN_SELECTED)}"` : ""}><title>${esc(tip)}</title>
      <text class="sw-pn ${cls}" x="${x + w / 2}" y="${numY}">${n}</text>
      <rect class="jack ${kind}" x="${x}" y="${y}" width="${w}" height="${PH}" rx="1.5"/>
      <rect class="led ${cls}" x="${x + w / 2 - 4}" y="${ledY}" width="8" height="3" rx="1.2"/></g>`);
  };
  for (let n = 1; n <= g.copper; n++) {
    const c = Math.floor((n - 1) / 2);
    port(n, LEFT + c * (PW + GX) + blockGap(c), (n - 1) % 2, PW, "rj45");
  }
  for (let k = 0; k < g.sfp; k++) port(g.copper + 1 + k, sfpX + Math.floor(k / 2) * (SW + 3), k % 2, SW, "sfp");
  for (let k = 0; k < g.stack; k++) port(g.copper + g.sfp + 1 + k, stackX + Math.floor(k / 2) * (QW + 3), k % 2, QW, "qsfp");
  const model = esc((g.model || "").replace(/^OS/, "OS "));
  return `<svg class="dev switch" viewBox="0 0 ${W} ${H}" style="max-width:${W}px" role="img" aria-label="${esc(g.model || "Switch")} front panel">
    <rect class="sw-body" x="0.5" y="0.5" width="${W - 1}" height="${H - 1}" rx="5"/>
    <circle class="led-dot" cx="${LEFT + 4}" cy="14" r="3"/>
    <text class="sw-title" x="${LEFT + 13}" y="18">${model}${g.poe ? `<tspan class="sw-poe2" dx="8">PoE+</tspan>` : ""}</text>
    ${parts.join("")}</svg>`;
}

function switchMini(g) {
  // 2 x 12 LED grid; each dot summarises a run of ports.
  const per = Math.max(1, Math.ceil(Math.max(g.copper, 2) / 24));
  const dots = [];
  for (let c = 0; c < 12; c++) {
    for (let r = 0; r < 2; r++) {
      let up = false;
      for (let i = 0; i < per; i++) {
        const n = (c * 2 + r) * per + i + 1;
        if (n <= g.copper && g.ports?.[String(n)]?.up) up = true;
      }
      dots.push(`<rect class="led ${up ? "up" : g.known ? "down" : "unknown"}" x="${4 + c * 2.6}" y="${7 + r * 3.4}" width="1.8" height="2.2" rx=".5"/>`);
    }
  }
  return `<svg class="mini" viewBox="0 0 40 20"><rect class="sw-body" x=".5" y="3.5" width="39" height="13" rx="2"/>${dots.join("")}</svg>`;
}

function apSvg(g, status, mini) {
  const led = `led-status ${esc(status)}`;
  let body;
  if (g.form === "outdoor") {
    body = `<rect class="ap-ant" x="34" y="4" width="6" height="22" rx="3"/><rect class="ap-ant" x="60" y="4" width="6" height="22" rx="3"/>
      <rect class="ap-shell" x="24" y="20" width="52" height="70" rx="8"/>
      ${mini ? "" : [36, 44, 52, 60, 68].map((y) => `<line class="ap-fin" x1="32" x2="68" y1="${y}" y2="${y}"/>`).join("")}
      <circle class="${led}" cx="50" cy="80" r="${mini ? 6 : 3.5}"/>`;
  } else if (g.form === "wallplate") {
    body = `<rect class="ap-shell" x="22" y="10" width="56" height="80" rx="7"/>
      <circle class="${led}" cx="50" cy="26" r="${mini ? 6 : 3.5}"/>
      ${mini ? "" : [34, 46, 58].map((x) => `<rect class="jack rj45" x="${x}" y="72" width="9" height="8" rx="1"/>`).join("")}`;
  } else {
    body = `<circle class="ap-shell" cx="50" cy="50" r="44"/>
      <circle class="ap-ring" cx="50" cy="50" r="${mini ? 26 : 31}"/>
      <circle class="${led}" cx="50" cy="${mini ? 50 : 28}" r="${mini ? 8 : 3.5}"/>`;
  }
  const label = mini ? "" : `<text class="ap-model" x="50" y="${g.form === "ceiling" ? 58 : 98}">${esc(g.model || "")}</text>`;
  return `<svg class="${mini ? "mini" : "dev ap"}" viewBox="0 0 100 ${mini ? 100 : 104}" role="img" aria-label="${esc(g.model || "Access point")}">${body}${label}</svg>`;
}

// ---------- live traffic (UniFi gateway ports): bytes/s in, bits/s out ----------
let RATE_UNIT = "bits"; // "bits" (b/s, kb/s, Mb/s) or "bytes" (B/s, kB/s, MB/s), set in the View menu
const RATE_UNIT_KEY = "ovcirrus-rate-unit";
function fmtRate(bytes) {
  const b = num(bytes);
  if (b === null) return "";
  if (RATE_UNIT === "bytes") {
    if (b < 1000) return `${Math.round(b)} B/s`;
    if (b < 1e6) return `${Math.round(b / 1000)} kB/s`;
    if (b < 1e9) return `${(b / 1e6).toFixed(b < 1e7 ? 1 : 0)} MB/s`;
    return `${(b / 1e9).toFixed(2)} GB/s`;
  }
  const bits = b * 8;
  if (bits < 1000) return `${Math.round(bits)} b/s`;
  if (bits < 1e6) return `${Math.round(bits / 1000)} kb/s`;
  if (bits < 1e9) return `${(bits / 1e6).toFixed(bits < 1e7 ? 1 : 0)} Mb/s`;
  return `${(bits / 1e9).toFixed(2)} Gb/s`;
}
// how busy a link is: share of the link speed (Mbps) used in the busier direction
function linkLoad(rx, tx, speed) {
  const sp = num(speed);
  if (!sp) return "";
  const u = (Math.max(num(rx) || 0, num(tx) || 0) * 8) / (sp * 1e6);
  return u >= 0.8 ? "high" : u >= 0.5 ? "warn" : "";
}
// Text after a link label for the gateway's end of a cable: its port name and live traffic, seen from the
// device further from the Internet: ↓ = gateway sending to it, ↑ = gateway receiving from it.
function peerText(peer) {
  if (!peer) return "";
  const rate = peer.up ? `↓${fmtRate(peer.tx)} ↑${fmtRate(peer.rx)}` : "down";
  return `${peer.name || peer.ifname} ${rate}`;
}
function peerHtml(peer) {
  if (!peer) return "";
  const load = peer.up ? linkLoad(peer.rx, peer.tx, peer.speed) : "down";
  const tip = `Gateway port ${peer.ifname}${peer.name ? " (" + peer.name + ")" : ""}: ${peer.up ? `${peer.speed ? peer.speed + " Mbps, " : ""}sending ${fmtRate(peer.tx)}, receiving ${fmtRate(peer.rx)}` : "down"}`;
  return ` <span class="peer" title="${esc(tip)}">${esc(peer.name || peer.ifname)}</span> <span class="rate ${load}" title="${esc(tip)}">${peer.up ? `↓${esc(fmtRate(peer.tx))} ↑${esc(fmtRate(peer.rx))}` : "down"}</span>${
    LINK_GRAPHS ? `<span class="lspark" data-port="${esc(peer.ifname)}">${sparkSvg(ratePoints("30m", peer.ifname))}</span>` : ""
  }`;
}
// The Internet -> gateway line: the WAN port's download / upload and latency.
function wanText(link) {
  const r = link?.rate;
  if (!r) return "";
  return `↓${fmtRate(r.rx)} ↑${fmtRate(r.tx)}${num(link.latency) !== null ? ` · ${Math.round(link.latency)} ms` : ""}`;
}

// ---------- bandwidth history (gateway ports) ----------
let RATES = {}; // range -> ovcirrus/rates result
let LINK_GRAPHS = false; // small traffic trend on the map's link labels
const LINK_GRAPHS_KEY = "ovcirrus-link-graphs";
const SPARK_W = 44, SPARK_H = 12;
// Chart ranges: key, button label, seconds, x-axis tick step. Which buttons show is set in the View menu;
// ranges longer than the history the integration keeps are left out.
const RANGE_DEFS = [
  ["15m", "15 min", 900, 300],
  ["30m", "30 min", 1800, 300],
  ["1h", "1 h", 3600, 900],
  ["2h", "2 h", 7200, 1800],
  ["6h", "6 h", 21600, 3600],
  ["12h", "12 h", 43200, 7200],
  ["24h", "24 h", 86400, 10800],
  ["3d", "3 days", 259200, 43200],
  ["7d", "7 days", 604800, 86400],
];
const RANGE_INFO = Object.fromEntries(RANGE_DEFS.map(([k, l, s, x]) => [k, { label: l, span: s, xstep: x }]));
const RANGES_DEFAULT = ["30m", "2h", "24h"];
const RANGES_KEY = "ovcirrus-rate-ranges";
const RANGE_KEY = "ovcirrus-rate-range";
// The history kept by the integration (from the last ovcirrus/rates answer): which ranges it can fill.
const rangesAvailable = () => {
  const r = Object.values(RATES).find((d) => d?.ranges);
  return r ? r.ranges : RANGE_DEFS.map((d) => d[0]);
};
const rateStep = () => Object.values(RATES).find((d) => d?.step)?.step || 15;
// [[t, down, up]] in bytes/s; ↓ is towards the device further from the Internet (WAN: into the gateway)
function ratePoints(range, port) {
  const d = RATES[range];
  const pts = d?.series?.[port] || [];
  const wan = d?.ports?.[port]?.role === "wan";
  return pts.map(([t, rx, tx]) => (wan ? [t, rx, tx] : [t, tx, rx]));
}
function sparkSvg(pts, w = SPARK_W, h = SPARK_H) {
  if (pts.length < 2) return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><line class="spark-base" x1="0" x2="${w}" y1="${h - 0.5}" y2="${h - 0.5}"/></svg>`;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
  const max = Math.max(1, ...pts.map((p) => p[1]));
  const xy = pts.map((p) => [((p[0] - t0) / (t1 - t0 || 1)) * w, h - 1 - (p[1] / max) * (h - 2)]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path class="spark-area" d="${line}L${w},${h}L0,${h}Z"/><path class="spark-line" d="${line}"/></svg>`;
}
function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * e) return m * e;
  return 10 * e;
}
const hhmm = (t) => {
  const d = new Date(t * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dayTime = (t) => `${DAYS[new Date(t * 1000).getDay()]} ${hhmm(t)}`;
// Local midnight / noon ticks for the multi-day ranges, round hours or minutes otherwise.
function timeTicks(t0, t1, step) {
  const out = [];
  if (step >= 43200) {
    const d = new Date(t0 * 1000);
    d.setHours(step >= 86400 ? 0 : d.getHours() < 12 ? 0 : 12, 0, 0, 0);
    for (let t = d.getTime() / 1000; t <= t1; ) {
      if (t >= t0) out.push(t);
      const n = new Date(t * 1000);
      n.setHours(n.getHours() + step / 3600);
      t = n.getTime() / 1000;
    }
    return out;
  }
  const off = new Date(t0 * 1000).getTimezoneOffset() * 60; // keep hour ticks on local hours
  for (let t = Math.ceil((t0 - off) / step) * step + off; t <= t1; t += step) if (t >= t0) out.push(t);
  return out;
}
// Area (↓) + line (↑) chart of one port, Mbit/s (kbit/s when small) against time, with the peak marked.
function bandwidthChart(pts, range, dnLabel, upLabel) {
  const W = 420, H = 206, L = 40, R = 12, T = 24, B = 24;
  if (pts.length < 2)
    return `<div class="uchart-empty">Collecting traffic… the graph fills in as readings arrive (every ${rateStep()} s).</div>`;
  const info = RANGE_INFO[range] || RANGE_INFO["2h"];
  const span = info.span;
  const t1 = pts[pts.length - 1][0], t0 = t1 - span;
  const peakB = Math.max(...pts.map((p) => Math.max(p[1], p[2])));
  // unit name and bytes/s per unit
  const unit =
    RATE_UNIT === "bytes"
      ? peakB >= 1e6 ? ["MB/s", 1e6] : ["kB/s", 1e3]
      : peakB * 8 >= 1e6 ? ["Mbit/s", 1e6 / 8] : ["kbit/s", 1e3 / 8];
  const val = (b) => b / unit[1];
  const tlabel = info.xstep >= 86400 ? (t) => `${DAYS[new Date(t * 1000).getDay()]} ${new Date(t * 1000).getDate()}` : info.xstep >= 43200 ? dayTime : hhmm;
  const top = Math.max(...pts.map((p) => Math.max(val(p[1]), val(p[2])))) * 1.05 || 1;
  const step = niceMax(top / 4);
  const ymax = Math.ceil(top / step) * step;
  const X = (t) => L + ((t - t0) / span) * (W - L - R);
  const Y = (v) => T + (1 - v / ymax) * (H - T - B);
  const fmt = (v) => (v >= 100 ? Math.round(v) : v >= 10 ? +v.toFixed(1) : +v.toFixed(v >= 1 ? 1 : 2));
  const yt = [];
  for (let v = 0; v <= ymax + step / 2; v += step) yt.push(v);
  const xt = timeTicks(t0, t1, info.xstep);
  const path = (k) => pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(val(p[k])).toFixed(1)}`).join("");
  const dn = path(1), up = path(2);
  const baseY = Y(0).toFixed(1);
  const peak = pts.reduce((a, p) => (p[1] > a[1] ? p : a), pts[0]);
  const px = X(peak[0]), py = Y(val(peak[1]));
  const peakLabel = `Max ${fmt(val(peak[1]))} ${unit[0]}`;
  const lx = Math.min(Math.max(px + 6, L + 2), W - R - peakLabel.length * 5.6);
  return `<svg class="uchart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(dnLabel)} and ${esc(upLabel)} over ${esc(info.label)}" data-long="${info.xstep >= 43200 ? 1 : 0}" data-t0="${t0}" data-span="${span}" data-l="${L}" data-r="${R}" data-w="${W}" data-t="${T}" data-b="${B}" data-h="${H}" data-ymax="${ymax}" data-unit="${unit[1]}" data-uname="${unit[0]}">
    ${yt.map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text class="ytick" x="${L - 5}" y="${(Y(v) + 3).toFixed(1)}">${fmt(v)}</text>`).join("")}
    ${xt.map((t) => `<line class="grid v" x1="${X(t).toFixed(1)}" x2="${X(t).toFixed(1)}" y1="${T}" y2="${H - B}"/><text class="xtick" x="${X(t).toFixed(1)}" y="${H - 8}">${tlabel(t)}</text>`).join("")}
    <text class="yunit" x="4" y="10">${unit[0]}</text>
    <path class="area dn" d="${dn}L${X(pts[pts.length - 1][0]).toFixed(1)},${baseY}L${X(pts[0][0]).toFixed(1)},${baseY}Z"/>
    <path class="line dn" d="${dn}"/><path class="line up" d="${up}"/>
    <circle class="peak" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="4"/>
    <text class="peaklabel" x="${lx.toFixed(1)}" y="${Math.max(T + 8, py - 6).toFixed(1)}">${esc(peakLabel)}</text>
    <g class="hover" visibility="hidden"><line class="cross" y1="${T}" y2="${H - B}"/><circle class="hdot dn" r="4"/><circle class="hdot up" r="4"/></g>
    <rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/>
  </svg>`;
}

function gatewaySvg(status, mini, g) {
  const ports = g?.ports?.length ? g.ports : null;
  if (mini || !ports) {
    const n = ports ? ports.length : 4;
    const w = Math.min(11, 48 / n);
    return `<svg class="${mini ? "mini" : "dev gw"}" viewBox="0 0 100 60" role="img" aria-label="Gateway">
    <rect class="sw-body" x="4" y="14" width="92" height="32" rx="6"/>
    ${Array.from({ length: n }, (_, i) => {
      const p = ports?.[i];
      return `<rect class="jack rj45${p?.role === "wan" ? " wan" : ""}" x="${44 + i * w}" y="25" width="${w - 2.5}" height="9" rx="1"/>${
        p ? `<rect class="led ${p.up ? "up" : "down"}" x="${44 + i * w + (w - 2.5) / 2 - 1.5}" y="36" width="3" height="2" rx=".8"/>` : ""
      }`;
    }).join("")}
    <circle class="led-status ${esc(status)}" cx="18" cy="30" r="${mini ? 6 : 3.5}"/></svg>`;
  }
  // Front panel: one tile per port with its LED, name and speed; the WAN port is marked. The viewBox is
  // about the panel's own width so the text draws at roughly its CSS size (13px / 12px).
  const GAP = 6, PAD = 8, CH = 7.3; // CH: width of a 12px monospace character
  const TW = Math.max(58, (430 - 2 * PAD - (ports.length - 1) * GAP) / ports.length);
  const W = 2 * PAD + ports.length * TW + (ports.length - 1) * GAP;
  const per = Math.max(4, Math.floor((TW - 4) / CH));
  const wrap = (name) => {
    const lines = [];
    let cur = "";
    for (const word of String(name || "").split(/\s+/).filter(Boolean)) {
      const next = cur ? `${cur} ${word}` : word;
      if (next.length <= per || !cur) cur = next;
      else { lines.push(cur); cur = word; }
    }
    if (cur) lines.push(cur);
    const out = lines.slice(0, 2).map((l) => (l.length > per ? l.slice(0, per - 1) + "…" : l));
    if (lines.length > 2) out[1] = out[1].slice(0, per - 1) + "…";
    return out;
  };
  const named = ports.map((p) => wrap(p.name));
  const rows = Math.max(1, ...named.map((l) => l.length));
  const TOP = 30, JH = 22, H = TOP + JH + 22 + rows * 15 + 18;
  const tiles = ports
    .map((p, i) => {
      const x = PAD + i * (TW + GAP), cx = x + TW / 2;
      const sp = p.up ? (p.speed >= 1000 ? `${p.speed / 1000}G` : `${p.speed}M`) : "down";
      const tip = `Port ${p.idx} · ${p.ifname} · ${p.name}${p.role === "wan" ? " · WAN" : ""} · ${p.up ? `up ${p.speed} Mbps${p.full_duplex === false ? " half duplex" : ""} · ↓${fmtRate(p.rx)} ↑${fmtRate(p.tx)}` : "down"}${p.media ? " · " + p.media : ""}`;
      const jw = Math.min(TW - 12, 48);
      const y0 = TOP + JH + 18;
      return `<g class="port"><title>${esc(tip)}</title>
        <rect class="led ${p.up ? "up" : "down"}" x="${cx - 6}" y="${TOP - 7}" width="12" height="4" rx="1.5"/>
        <rect class="jack rj45${p.role === "wan" ? " wan" : ""}" x="${cx - jw / 2}" y="${TOP}" width="${jw}" height="${JH}" rx="2"/>
        <text class="gw-pn" x="${cx}" y="${y0}">${p.idx}${p.role === "wan" ? " WAN" : ""}</text>
        ${named[i].map((l, k) => `<text class="gw-pl" x="${cx}" y="${y0 + 16 + k * 15}">${esc(l)}</text>`).join("")}
        <text class="gw-ps ${p.up ? "" : "off"}" x="${cx}" y="${y0 + 16 + rows * 15}">${esc(sp)}</text></g>`;
    })
    .join("");
  return `<svg class="dev gw ports" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(g.model || "Gateway")} ports">
    <rect class="sw-body" x="0.5" y="0.5" width="${W - 1}" height="${H - 1}" rx="6"/>
    <circle class="led-status ${esc(status)}" cx="${PAD + 5}" cy="13" r="4"/>
    <text class="gw-model" x="${PAD + 15}" y="17">${esc(g.model || "Gateway")}</text>
    ${tiles}</svg>`;
}

// Your picture of a client: drawn onto a canvas no larger than PHOTO_PX, sent as WebP (PNG where the
// browser can't make WebP) so uploads stay small and transparency is kept.
const PHOTO_PX = 480;
async function shrinkPhoto(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, fail) => {
      const im = new Image();
      im.onload = () => ok(im);
      im.onerror = fail;
      im.src = url;
    });
    const k = Math.min(1, PHOTO_PX / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(img.naturalWidth * k));
    c.height = Math.max(1, Math.round(img.naturalHeight * k));
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    let data = c.toDataURL("image/webp", 0.86);
    if (!data.startsWith("data:image/webp")) data = c.toDataURL("image/png");
    return data.split(",")[1];
  } finally {
    URL.revokeObjectURL(url);
  }
}

function deviceGraphic(n, mini) {
  const g = n.graphic;
  if (!g) return "";
  if (g.image) {
    const img = `<img class="${mini ? "photo-mini" : "photo"}" src="${esc(g.image)}" alt="${esc(g.model || n.name)}" loading="lazy">`;
    if (mini) return img;
    // Photo of the hardware; switches also keep the live port-LED panel underneath.
    if (g.type === "switch") return `${img}<div class="sublabel">Live port status</div>${switchSvg(g)}`;
    if (g.type === "gateway" && g.ports?.length) return `${img}<div class="sublabel">Live port status</div>${gatewaySvg(n.status, false, g)}`;
    return img;
  }
  if (g.type === "switch") return mini ? switchMini(g) : switchSvg(g);
  if (g.type === "ap") return apSvg(g, n.status, mini);
  if (g.type === "gateway") return gatewaySvg(n.status, mini, g);
  return "";
}

// ---------- VLANs ----------
const VLAN_PALETTE = ["#2f80ed", "#e5484d", "#30a46c", "#f76b15", "#8e4ec6", "#12a594", "#d6409f", "#a18072", "#8d9b2a", "#1e3a8a", "#c99700", "#64748b"];
let VLAN_COLORS = {};
let VLAN_NAMES = {};
let VLAN_SELECTED = null;
let VLAN_EXPANDED = new Set();
const vlanColor = (id) => VLAN_COLORS[String(id)] || "var(--secondary-text-color)";
const carries = (link, id) => id !== null && [...(link.untagged || []), ...(link.tagged || [])].map(String).includes(String(id));
function vlanChips(link, max = 4, key = null) {
  const u = (link.untagged || []).map((v) => [v, "u"]);
  const t = (link.tagged || []).map((v) => [v, "t"]);
  const all = [...u, ...t];
  const open = key !== null && VLAN_EXPANDED.has(key);
  const shown = open ? all : all.slice(0, max);
  const tip = [
    u.length ? "Untagged: " + u.map(([v]) => `${v} ${VLAN_NAMES[v] || ""}`).join(", ") : "",
    t.length ? "Tagged: " + t.map(([v]) => `${v} ${VLAN_NAMES[v] || ""}`).join(", ") : "",
  ].filter(Boolean).join("\n");
  return (
    shown.map(([v, k]) => `<span class="vc ${k}${String(VLAN_SELECTED) === String(v) ? " on" : ""}" style="--c:${vlanColor(v)}" title="${esc(tip)}">${v}</span>`).join("") +
    (all.length > max
      ? key !== null
        ? `<button class="vc more" data-vmore="${esc(key)}" title="${open ? "Show fewer" : esc(tip)}">${open ? "less" : `+${all.length - max}`}</button>`
        : `<span class="vc more" title="${esc(tip)}">+${all.length - max}</span>`
      : "")
  );
}

const ago = (ts) => {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};
const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));

// Speed test: HA Speedtest.net sensors (auto-detected or configured) and/or the UniFi gateway result.
function speedInfo(hass, cfg, wan) {
  const tests = [];
  const states = hass?.states || {};
  const find = (suffix) =>
    cfg.speedtest?.[suffix] ||
    Object.keys(states).find((id) => id.startsWith("sensor.") && id.includes("speedtest") && id.endsWith("_" + suffix));
  const dl = find("download"), ul = find("upload"), ping = find("ping");
  if (dl && states[dl] && num(states[dl].state) !== null) {
    tests.push({
      source: "Home Assistant Speedtest",
      down: num(states[dl].state), up: ul ? num(states[ul]?.state) : null, ping: ping ? num(states[ping]?.state) : null,
      ts: Date.parse(states[dl].last_updated) / 1000,
    });
  }
  if (wan && num(wan.download_mbps) !== null) {
    tests.push({ source: "UniFi gateway", down: num(wan.download_mbps), up: num(wan.upload_mbps), ping: num(wan.latency_ms), ts: num(wan.speedtest_lastrun) });
  }
  tests.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return tests;
}
const speedText = (t) =>
  t ? `↓ ${Math.round(t.down)}${t.up !== null ? ` / ↑ ${Math.round(t.up)}` : ""} Mbps${t.ping !== null ? ` · ${Math.round(t.ping)} ms` : ""}` : "";

class OvCirrusNetworkMap extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._collapsed = new Map(); // id -> bool (user overrides)
    this._zoom = null; // null = fit
    try {
      const z = Number(localStorage.getItem(ZOOM_KEY)); // remembered per browser; missing or "fit" = fit
      if (z) this._zoom = Math.min(2, Math.max(0.2, z));
    } catch (e) {
      // storage blocked: start at fit
    }
    this._selected = null;
    this._siteIdx = 0;
  }

  setConfig(config) {
    this._config = {
      refresh: 30, max_columns: 3, height: 640, expand_clients: false,
      layout_editing: "admin", snap_to_grid: true, grid_size: 20, ...config,
    };
    let snap = null;
    try {
      snap = localStorage.getItem(SNAP_KEY);
    } catch (e) {
      snap = null;
    }
    this._snap = snap === null ? this._config.snap_to_grid !== false : snap === "1";
    this._grid = Math.max(5, Math.min(100, Number(this._config.grid_size) || 20));
    let storedDs = null;
    try {
      storedDs = sizeFrom(localStorage.getItem(DS_KEY), localStorage.getItem(DS_OLD_KEY), DS_MIN, DS_MAX);
    } catch (e) {
      storedDs = null;
    }
    this._ds = storedDs || this._cfgDevice();
    let storedCs = null;
    try {
      storedCs = sizeFrom(localStorage.getItem(CS_KEY), localStorage.getItem(CS_OLD_KEY), CS_MIN, CS_MAX);
    } catch (e) {
      storedCs = null;
    }
    this._cs = storedCs || this._cfgClient();
    this._local = { device: !!storedDs, client: !!storedCs };
    this._applyFill();
    let stored = null;
    try {
      stored = Number(localStorage.getItem(TEXT_KEY)) ? clampText(localStorage.getItem(TEXT_KEY)) : legacyText(localStorage.getItem(FS_KEY));
    } catch (e) {
      stored = null; // storage blocked: fall back to the card setting
    }
    this._tp = stored || configText(this._config);
    let lg = null;
    try {
      lg = localStorage.getItem(LINK_GRAPHS_KEY);
    } catch (e) {
      lg = null;
    }
    this._linkGraphs = lg === null ? !!this._config.link_graphs : lg === "1";
    let ru = null, rr = null, r1 = null;
    try {
      ru = localStorage.getItem(RATE_UNIT_KEY);
      rr = JSON.parse(localStorage.getItem(RANGES_KEY) || "null");
      r1 = localStorage.getItem(RANGE_KEY);
    } catch (e) {
      ru = rr = r1 = null;
    }
    const cfgRanges = Array.isArray(this._config.rate_ranges) ? this._config.rate_ranges.map(String) : null;
    const pick = (list) => (Array.isArray(list) ? list.filter((k) => RANGE_INFO[k]) : []);
    this._rateRanges = pick(rr).length ? pick(rr) : pick(cfgRanges).length ? pick(cfgRanges) : [...RANGES_DEFAULT];
    this._rateUnit = ru === "bytes" || ru === "bits" ? ru : this._config.rate_unit === "bytes" ? "bytes" : "bits";
    RATE_UNIT = this._rateUnit;
    if (RANGE_INFO[r1]) this._rRange = r1;
    else if (RANGE_INFO[this._config.rate_range]) this._rRange = this._config.rate_range;
    this._local = { ...(this._local || {}), font: !!stored };
  }

  _cfgDevice() {
    return sizeFrom(this._config?.device_size, this._config?.device_scale, DS_MIN, DS_MAX) || 1;
  }

  _cfgClient() {
    return sizeFrom(this._config?.client_size, this._config?.client_scale, CS_MIN, CS_MAX) || 1;
  }

  static getStubConfig() {
    return {};
  }

  // Fill mode is set by the sidebar panel so the map takes the whole main pane.
  _applyFill() {
    if (this._config?.height === "fill") this.setAttribute("fill", "");
    else this.removeAttribute("fill");
  }

  getCardSize() {
    return Math.ceil((this._config?.height || 640) / 50);
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._timer) {
      this._fetch();
      this._timer = setInterval(() => this._fetch(), Math.max(10, this._config.refresh) * 1000);
    }
    if (!this._rTimer) this._rTimer = setInterval(() => this._fetchRates(), 5000);
    const dark = !!hass?.themes?.darkMode;
    if (dark !== this.hasAttribute("dark")) this.toggleAttribute("dark", dark);
  }

  // Which history ranges are on screen: the open gateway panel's graph, and the trends on the map's links.
  _ratesWanted() {
    const site = this._sites?.[this._siteIdx];
    if (!site) return [];
    const want = new Set();
    const sel = this._selected && this._index?.get(this._selected);
    if (sel?.unifi) want.add(this._chartRange());
    if (LINK_GRAPHS && [...(this._index?.values() || [])].some((n) => n.unifi)) want.add("30m");
    return [...want];
  }

  // The range the chart shows: the one picked, if its button is shown and the history covers it.
  _chartRange() {
    const shown = this._shownRanges();
    return shown.includes(this._rRange) ? this._rRange : shown.includes("2h") ? "2h" : shown[0] || "2h";
  }

  _hasUnifi() {
    return [...(this._index?.values() || [])].some((n) => n.unifi);
  }

  _shownRanges() {
    const avail = rangesAvailable();
    const list = RANGE_DEFS.map((d) => d[0]).filter((k) => this._rateRanges.includes(k) && avail.includes(k));
    return list.length ? list : avail.slice(0, 1);
  }

  async _fetchRates(force) {
    const stale = (r) => !RATES[r] || Date.now() - (RATES[r]._at || 0) >= rateStep() * 1000 - 500;
    const want = this._ratesWanted().filter((r) => force || stale(r));
    const entryId = this._sites?.[this._siteIdx]?.entry_id;
    if (!want.length || !entryId || !this._hass || this._rLoading) return;
    this._rLoading = true;
    const before = rangesAvailable().join();
    try {
      for (const range of want) {
        RATES[range] = await this._hass.callWS({ type: "ovcirrus/rates", entry_id: entryId, range });
        RATES[range]._at = Date.now();
      }
    } catch (err) {
      console.debug("ovcirrus: rates", err);
    }
    this._rLoading = false;
    if (rangesAvailable().join() !== before) this._render(); // the range buttons depend on the history kept
    else this._updateRates();
  }

  // Refresh the graphs in place (no full redraw, so hovering and scrolling are undisturbed).
  _updateRates() {
    const root = this.shadowRoot;
    root.querySelectorAll(".lspark[data-port]").forEach((el) => (el.innerHTML = sparkSvg(ratePoints("30m", el.dataset.port))));
    const range = this._chartRange();
    root.querySelectorAll(".tspark[data-port]").forEach((el) => {
      if (el.innerHTML) el.innerHTML = sparkSvg(ratePoints(range, el.dataset.port), 48, 16);
    });
    const wrap = root.querySelector(".uchartwrap");
    if (wrap) {
      const pts = ratePoints(wrap.dataset.range, wrap.dataset.port);
      wrap.innerHTML = `${bandwidthChart(pts, wrap.dataset.range, wrap.dataset.dn, wrap.dataset.up)}<div class="utip" hidden></div>`;
      const last = pts[pts.length - 1];
      const nowDn = root.querySelector('[data-now="dn"]'), nowUp = root.querySelector('[data-now="up"]');
      if (last && nowDn) nowDn.textContent = fmtRate(last[1]);
      if (last && nowUp) nowUp.textContent = fmtRate(last[2]);
      this._bindChart(wrap, pts);
    }
  }

  _bindChart(wrap, pts) {
    const svg = wrap.querySelector("svg.uchart");
    const hit = svg?.querySelector(".hit");
    const tip = wrap.querySelector(".utip");
    if (!svg || !hit || !tip || pts.length < 2) return;
    const d = svg.dataset;
    const [W, L, R, T, B, H] = ["w", "l", "r", "t", "b", "h"].map((k) => Number(d[k]));
    const t0 = Number(d.t0), span = Number(d.span), ymax = Number(d.ymax), unit = Number(d.unit);
    const X = (t) => L + ((t - t0) / span) * (W - L - R);
    const Y = (b) => T + (1 - b / unit / ymax) * (H - T - B);
    const tl = d.long === "1" ? dayTime : hhmm;
    const g = svg.querySelector(".hover");
    const show = (ev) => {
      const box = svg.getBoundingClientRect();
      const x = ((ev.clientX - box.left) * W) / box.width;
      const t = t0 + ((x - L) / (W - L - R)) * span;
      let best = pts[0];
      for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      const cx = X(best[0]);
      g.setAttribute("visibility", "visible");
      const cross = g.querySelector(".cross");
      cross.setAttribute("x1", cx);
      cross.setAttribute("x2", cx);
      const [dDot, uDot] = g.querySelectorAll(".hdot");
      dDot.setAttribute("cx", cx);
      dDot.setAttribute("cy", Y(best[1]));
      uDot.setAttribute("cx", cx);
      uDot.setAttribute("cy", Y(best[2]));
      tip.hidden = false;
      tip.innerHTML = `<b>${tl(best[0])}</b><span><i class="key dn"></i>↓ ${esc(fmtRate(best[1]))}</span><span><i class="key up"></i>↑ ${esc(fmtRate(best[2]))}</span>`;
      const px = (cx / W) * box.width;
      tip.style.left = `${Math.min(box.width - tip.offsetWidth - 4, Math.max(4, px + 10))}px`;
    };
    hit.addEventListener("pointermove", show);
    hit.addEventListener("pointerdown", show);
    hit.addEventListener("pointerleave", () => {
      g.setAttribute("visibility", "hidden");
      tip.hidden = true;
    });
  }

  connectedCallback() {
    if (!this._ro && window.ResizeObserver) {
      this._ro = new ResizeObserver(() => {
        const w = Math.round(this.getBoundingClientRect().width);
        if (w && w !== this._lastW) {
          this._lastW = w;
          if (this._sites && (this._zoom == null || this._needsMeasure)) {
            this._needsMeasure = false;
            this._render();
          }
        }
      });
      this._ro.observe(this);
    }
    if (this._hass && !this._timer) {
      this._fetch();
      this._timer = setInterval(() => this._fetch(), Math.max(10, this._config.refresh) * 1000);
    }
    if (this._hass && !this._rTimer) this._rTimer = setInterval(() => this._fetchRates(), 5000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    clearInterval(this._rTimer);
    this._rTimer = null;
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      const msg = { type: "ovcirrus/topology" };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      const res = await this._hass.callWS(msg);
      this._sites = (res.entries || []).flatMap((e) => e.sites.map((s) => ({ ...s, entry: e.title, entry_id: s.entry_id || e.entry_id })));
      this._isAdmin = !!res.is_admin;
      this._integrationVersion = res.version || null;
      this._applyServerView(res.view || {});
      this._nmap = !!res.nmap;
      const cur = this._sites[this._siteIdx];
      if (cur && this._localOffsets) {
        // Drop local edits the server has confirmed.
        for (const [id, o] of Object.entries(this._localOffsets)) {
          const saved = cur.positions?.[id];
          if (saved && Math.abs(saved[0] - o[0]) < 1 && Math.abs(saved[1] - o[1]) < 1) delete this._localOffsets[id];
        }
      }
      this._types = res.types || {};
      this._error = this._sites.length ? null : "No OmniVista Cirrus data yet.";
      if (this._config.site && this._sites.length) {
        const i = this._sites.findIndex((s) => s.site_id === this._config.site || s.site_name === this._config.site);
        if (i >= 0) this._siteIdx = i;
      }
    } catch (err) {
      this._error = `Cannot load map: ${err.message || err}`;
    }
    if (!this._editing && !this._drag) this._render();
  }

  // ---------- layout ----------
  _isCollapsed(node) {
    if (this._collapsed.has(node.id)) return this._collapsed.get(node.id);
    const allLeaf = node.groups.length && node.groups.every((g) => g.leaf);
    return allLeaf && !this._config.expand_clients;
  }

  _textWidth(text, font) {
    if (!this._ctx) this._ctx = document.createElement("canvas").getContext("2d");
    if (!this._family) this._family = getComputedStyle(this).fontFamily || "sans-serif";
    // font is "[weight] <size>px"; scale the size by the current text size setting
    const scaled = font.replace(/([\d.]+)px/, (_, n) => `${(Number(n) * FS).toFixed(2)}px`);
    this._ctx.font = `${scaled} ${this._family}`;
    return this._ctx.measureText(text || "").width;
  }

  _chipsWidth(link, max, key) {
    const all = [...(link.untagged || []), ...(link.tagged || [])];
    const open = VLAN_EXPANDED.has(key);
    const shown = open ? all : all.slice(0, max);
    let w = shown.reduce((a, v) => a + this._textWidth(String(v), "9.5px") + 10 + 3, 0);
    if (all.length > max) w += this._textWidth(open ? "less" : `+${all.length - max}`, "9.5px") + 10 + 3;
    return w;
  }

  // Space a child's link labels need to the right of its centre line, and how many rows they use.
  _labelExtent(c) {
    const link = c.link || {};
    const members = link.links || [];
    if (members.length > 1) {
      const offset = ((members.length - 1) / 2) * BUNDLE_STEP + 12;
      const widest = Math.max(
        ...members.map(
          (m, i) =>
            8 + 2 + 11 + this._textWidth(m.label, "600 10.5px") + this._textWidth(m.speed ? ` ${m.speed} ` : " ", "10.5px") +
            this._chipsWidth(m, 4, `${c.id}:${i}`) + (m.peer ? this._textWidth(` ${peerText(m.peer)} `, "10.5px") + 12 + (LINK_GRAPHS ? SPARK_W + 6 : 0) : 0)
        )
      );
      return { right: offset + widest, rows: members.length };
    }
    if (!link.label && !members.length) return { right: 0, rows: 0 };
    const extra = (link.rate ? this._textWidth(` ${wanText(link)} `, "10.5px") + 8 : members[0]?.peer ? this._textWidth(` ${peerText(members[0].peer)} `, "10.5px") + 12 : 0) +
      (LINK_GRAPHS && (link.rate || members[0]?.peer) ? SPARK_W + 6 : 0);
    const w = 8 + 2 + this._textWidth(`${link.label || ""} `, "10.5px") + (members[0] ? this._chipsWidth(members[0], 3, `${c.id}:0`) : 0) + extra;
    return { right: 8 + w, rows: 1 };
  }

  _chipWidth(n) {
    const ring = n.graphic?.type === "switch" ? Math.round(ringSize(n) * 1.1) : ringSize(n);
    const toggle = n.groups.some((g) => g.children.length) ? 30 : 0;
    const text = Math.max(this._textWidth(n.name, "600 13px"), this._textWidth(n.subtitle, "11px"));
    // padding 6+6, gap ring->text 10, a little slack for sub-pixel rounding
    // ring outer width = content + border (2px, or 3px for big devices); gap ring->text 10 (14 when big)
    const big = BIG_KINDS.has(n.kind);
    const outer = ring + (big ? 6 : 4);
    const gap = big ? 14 : 10;
    const max = MAX_CHIP_W * Math.max(1, FS) + Math.max(0, outer - 46); // allow for bigger rings
    return Math.round(Math.min(max, Math.max(CHIP_W, 12 + outer + gap + text + toggle + 10)));
  }

  _measure(node) {
    node._groups = node.groups.filter((g) => g.children.length);
    node._collapsed = node._groups.length > 0 && this._isCollapsed(node);
    if (!node._groups.length || node._collapsed) {
      node._gw = 0;
      node._w = node._cw;
      return;
    }
    let total = 0;
    node._stack = node._groups.every((g) => g.leaf);
    if (node._stack) {
      // Client boxes stack vertically under an AP with a trunk line on the left.
      node._groups.forEach((g) => this._measureGroup(g));
      node._gw = Math.max(...node._groups.map((g) => g._w)) + 2 * TRUNK;
      node._w = Math.max(node._cw, node._gw);
      return;
    }
    for (const g of node._groups) {
      this._measureGroup(g);
      total += g._w;
    }
    total += GAP_X * (node._groups.length - 1);
    node._gw = total;
    node._w = Math.max(node._cw, total);
  }

  _measureGroup(g) {
    const n = g.children.length;
    if (g.leaf) {
      // Columns share the width of the widest client in the box so the grid stays aligned.
      g._colW = Math.max(...g.children.map((c) => c._cw));
      g._rowH = Math.max(...g.children.map((c) => c._ch || CHIP_H));
      const bandH = Math.round(18 * Math.max(0.85, FS));
      if (g.children.some((c) => c.kind === "wireless" && c.band)) {
        // Wireless clients: one section per frequency band, side by side, each labelled.
        const byBand = new Map();
        for (const c of g.children) {
          const key = bandLabel(c.band);
          if (!byBand.has(key)) byBand.set(key, []);
          byBand.get(key).push(c);
        }
        g._sections = [...byBand.entries()]
          .sort((a, b) => bandOrder(a[0]) - bandOrder(b[0]))
          .map(([label, kids]) => {
            const cols = Math.min(this._config.max_columns, kids.length);
            return { label, kids, cols, rows: Math.ceil(kids.length / cols), w: cols * g._colW + (cols - 1) * GAP_X };
          });
        g._rows = Math.max(...g._sections.map((x) => x.rows));
        g._bandH = bandH;
        const inner = g._sections.reduce((a, x) => a + x.w, 0) + BAND_GAP * (g._sections.length - 1);
        g._w = inner + 2 * PAD;
      } else {
        g._sections = null;
        g._bandH = 0;
        g._cols = Math.min(this._config.max_columns, n);
        g._rows = Math.ceil(n / g._cols);
        g._w = g._cols * g._colW + (g._cols - 1) * GAP_X + 2 * PAD;
      }
      g._above = LABEL_H + PAD + g._bandH;
      g._h = g._above + g._rows * g._rowH + (g._rows - 1) * ROW_GAP + PAD;
    } else {
      g.children.forEach((c) => {
        this._measure(c);
        const ext = this._labelExtent(c);
        c._labelRight = ext.right;
        c._labelRows = ext.rows;
        // Slot = the subtree, widened on the right if the link labels reach further.
        c._slot = Math.max(c._w, c._w / 2 + ext.right + 4);
      });
      g._rowsH = Math.max(0, ...g.children.map((c) => c._labelRows)) * LABEL_ROW;
      if (g._rowsH) g._rowsH += 6;
      const inner = g.children.reduce((a, c) => a + c._slot, 0) + GAP_X * (n - 1);
      g._w = inner + 2 * PAD;
      g._h = LABEL_H + PAD + g._rowsH + Math.max(...g.children.map((c) => c._ch)) + PAD;
    }
  }

  // Offsets saved from dragging: a node moves together with everything placed under it.
  _off(id) {
    const o = this._offsets?.[id];
    return o ? [Number(o[0]) || 0, Number(o[1]) || 0] : [0, 0];
  }

  // Wrap a group box around wherever its devices (and their link labels) ended up.
  _wrap(g, above) {
    const kids = g.children;
    const left = Math.min(...kids.map((c) => c._x)) - PAD;
    const right =
      Math.max(...kids.map((c) => Math.max(c._x + (c._rw || c._cw), c._x + c._cw / 2 + (c._labelRight || 0)))) + PAD;
    const top = Math.min(...kids.map((c) => c._y)) - above;
    const bottom = Math.max(...kids.map((c) => c._y + (c._ch || CHIP_H))) + PAD;
    g._bx = left;
    g._x = left;
    g._y = top;
    g._bw = right - left;
    g._h = bottom - top;
  }

  _place(node, cx, y, out, ox = 0, oy = 0) {
    const [dx, dy] = this._off(node.id);
    ox += dx;
    oy += dy;
    node._x = cx - node._cw / 2 + ox;
    node._y = y + oy;
    out.nodes.push(node);
    if (!node._groups.length || node._collapsed) return;
    if (node._stack) {
      const trunkX = cx - node._gw / 2 + TRUNK / 2;
      let gy = y + node._ch + 36;
      for (const g of node._groups) {
        g._x = trunkX + TRUNK / 2 + ox;
        g._y = gy + oy;
        this._placeLeaves(g, g._y + (g._above || LABEL_H + PAD), out);
        this._wrap(g, g._above || LABEL_H + PAD);
        out.groups.push(g);
        out.trunks.push({ node, g });
        gy += g._h + 34;
      }
      return;
    }
    let x = cx - node._gw / 2;
    const gy = y + node._ch + V_GAP;
    for (const g of node._groups) {
      const above = g.leaf ? g._above || LABEL_H + PAD : LABEL_H + PAD + (g._rowsH || 0);
      const chipY = gy + above;
      out.groups.push(g);
      if (g.leaf) {
        g._x = x + ox;
        this._placeLeaves(g, chipY + oy, out);
        this._wrap(g, above);
        out.edges.push({ from: node, toGroup: g, label: "", status: "up", group: true });
      } else {
        let cxs = x + PAD;
        for (const c of g.children) {
          const ccx = cxs + c._w / 2;
          c._box = g; // the box this device sits in (links to its children start from its centre)
          this._place(c, ccx, chipY, out, ox, oy);
          const link = c.link || {};
          out.edges.push({
            from: node,
            to: c,
            gid: g.id,
            cid: c.id,
            label: link.label || "",
            status: link.status || "up",
            dashed: !!link.inferred,
            links: link.links || [],
            rate: link.rate || null,
            latency: link.latency ?? null,
          });
          cxs += c._slot + GAP_X;
        }
        this._wrap(g, above);
      }
      x += g._w + GAP_X;
    }
  }

  _placeLeaves(g, chipY, out) {
    if (g._sections) {
      g._bandMarks = [];
      g._seps = [];
      let x = g._x + PAD;
      g._sections.forEach((sec, si) => {
        g._bandMarks.push({ label: sec.label, count: sec.kids.length, x, y: chipY });
        sec.kids.forEach((c, i) => {
          const col = i % sec.cols;
          const row = Math.floor(i / sec.cols);
          c._x = x + col * (g._colW + GAP_X);
          c._rw = g._colW;
          c._y = chipY + g._bandH + row * ((g._rowH || CHIP_H) + ROW_GAP);
          c._groups = [];
          out.nodes.push(c);
        });
        x += sec.w;
        if (si < g._sections.length - 1) {
          g._seps.push({ x: x + BAND_GAP / 2 });
          x += BAND_GAP;
        }
      });
      return;
    }
    g._bandMarks = null;
    g._seps = null;
    g.children.forEach((c, i) => {
      const col = i % g._cols;
      const row = Math.floor(i / g._cols);
      const [dx, dy] = this._off(c.id); // wired clients can be moved on their own
      c._x = g._x + PAD + col * (g._colW + GAP_X) + dx;
      c._rw = g._colW;
      c._y = chipY + row * ((g._rowH || CHIP_H) + ROW_GAP) + dy;
      c._groups = [];
      out.nodes.push(c);
    });
  }

  // After placement: shift everything into view and resolve edge end points.
  _finalize(out) {
    const xs = [...out.nodes.map((n) => n._x), ...out.groups.map((g) => g._bx)];
    const ys = [...out.nodes.map((n) => n._y), ...out.groups.map((g) => g._y)];
    // Shift in whole grid steps so devices snapped to the grid stay on it.
    const g = this._grid || 20;
    const sx = Math.max(0, Math.ceil((20 - Math.min(...xs)) / g) * g);
    const sy = Math.max(0, Math.ceil((20 - Math.min(...ys)) / g) * g);
    if (sx || sy) {
      out.nodes.forEach((n) => ((n._x += sx), (n._y += sy)));
      out.groups.forEach((g) => {
        g._bx += sx;
        g._x += sx;
        g._y += sy;
        (g._bandMarks || []).forEach((m) => ((m.x += sx), (m.y += sy)));
        (g._seps || []).forEach((m) => (m.x += sx));
      });
    }
    for (const e of out.edges) {
      if (e.toGroup) {
        e.toX = e.toGroup._bx + PAD + 40;
        e.toY = e.toGroup._y;
      } else {
        e.toX = e.to._x + e.to._cw / 2;
        e.toY = e.to._y;
      }
    }
    out.trunks = out.trunks.map(({ node, g }) => ({
      x1: node._x + 6, y1: node._y + node._ch / 2, tx: g._bx - TRUNK / 2, ty: g._y + 2, x2: g._bx,
    }));
    out.maxX = Math.max(...out.nodes.map((n) => n._x + (n._rw || n._cw)), ...out.groups.map((g) => g._bx + g._bw));
    out.maxY = Math.max(...out.nodes.map((n) => n._y + (n._ch || CHIP_H)), ...out.groups.map((g) => g._y + g._h));
  }

  // ---------- render ----------
  _render() {
    const cfg = this._config;
    const site = this._sites?.[this._siteIdx];
    const prevVp = this.shadowRoot.querySelector(".viewport");
    const scroll = prevVp ? [prevVp.scrollLeft, prevVp.scrollTop] : null;
    // Keep the open details panel (and View menu) where it was scrolled to when the same one is redrawn,
    // e.g. after picking a chart range or port.
    const prevPanel = this.shadowRoot.querySelector(".panel");
    const panelScroll = prevPanel && this._panelFor === this._selected ? prevPanel.scrollTop : 0;
    const prevMenu = this.shadowRoot.querySelector(".viewmenu");
    const menuScroll = prevMenu ? prevMenu.scrollTop : 0;

    let body = "";
    let W = 0;
    let H = 0;
    if (site) {
      const root = site.root;
      applyFontScale(textFs(this._tp));
      VLAN_NAMES = Object.fromEntries(Object.entries(site.vlans || {}).map(([k, v]) => [k, v.name]));
      VLAN_COLORS = Object.fromEntries(Object.keys(site.vlans || {}).map((k, i) => [k, VLAN_PALETTE[i % VLAN_PALETTE.length]]));
      if (this._vlan != null && !(String(this._vlan) in (site.vlans || {}))) this._vlan = null;
      VLAN_SELECTED = this._vlan ?? null;
      const tests = speedInfo(this._hass, cfg, root.wan);
      this._speed = tests;
      if (tests.length) {
        root.subtitle = speedText(tests[0]);
        const gw = root.groups[0]?.children?.[0];
        if (gw && !gw.link?.rate) gw.link = { ...(gw.link || {}), label: speedText(tests[0]), status: root.status === "offline" ? "down" : "up" };
      }
      this._index = new Map();
      const walk = (n) => {
        this._index.set(n.id, n);
        n.groups.forEach((g) => g.children.forEach(walk));
      };
      walk(root);
      DEVICE_SCALE = SIZE_BASE * (this._ds || 1);
      CLIENT_SCALE = SIZE_BASE * (this._cs || 1);
      for (const n of this._index.values()) {
        n._ch = chipH(n);
        n._cw = this._chipWidth(n);
        n._rw = null;
      }
      VLAN_EXPANDED = this._vmore || (this._vmore = new Set());
      LINK_GRAPHS = !!this._linkGraphs;
      this._measure(root);
      const out = { nodes: [], groups: [], edges: [], trunks: [], maxY: 0 };
      if (!this._drag) this._offsets = { ...(site.positions || {}), ...(this._localOffsets || {}) };
      this._place(root, root._w / 2 + 20, 20, out);
      this._finalize(out);
      W = Math.max(root._w + 40, out.maxX + 40);
      H = out.maxY + 30;

      const labels = [];
      const sv = VLAN_SELECTED;
      const edges = out.edges
        .map((e) => {
          const x1 = e.from._x + e.from._cw / 2;
          const y1 = e.from._y + e.from._ch;
          const x2 = e.toX;
          const y2 = e.toY;
          const my = (y1 + y2) / 2;
          const members = e.links?.length ? e.links : null;
          const lineCls = (st, m) => {
            const hl = sv !== null && m && carries(m, sv);
            const dim = sv !== null && !hl;
            return [`edge`, st === "down" ? "down" : "", e.dashed ? "dashed" : "", hl ? "hl" : "", dim ? "dim" : ""].join(" ");
          };
          if (members && members.length > 1) {
            // Split a bundle of parallel links into separate lines, each with its own label.
            // Each line ends at the centre of its port label (the label is opaque and drawn above the
            // lines, so the line meets its edge), entering from the side the parent is on. It leaves the
            // parent from the bottom, top or side facing the labels, so it never runs across a device.
            const n = members.length;
            const step = BUNDLE_STEP;
            // Lines leave from the centre of the parent's box (its own chip if it has no box).
            const p = e.from;
            const b = p._box && p._box._bw ? { x: p._box._bx, y: p._box._y, w: p._box._bw, h: p._box._h } : null;
            const px = b ? b.x : p._x, py = b ? b.y : p._y;
            const pw = b ? b.w : p._rw || p._cw, ph = b ? b.h : p._ch || CHIP_H;
            const pcx = px + pw / 2, pcy = py + ph / 2;
            const lx = x2 + ((n - 1) / 2) * step + 12;
            const rowH = 17 * FS;
            const rows = members.map((m, i) => {
              const key = `${e.cid}:${i}`;
              const w = 10 + 8 + 3 + this._textWidth(m.label, "600 10.5px") +
                this._textWidth(m.speed ? ` ${m.speed} ` : " ", "10.5px") + this._chipsWidth(m, 4, key) +
                (m.peer ? this._textWidth(` ${peerText(m.peer)} `, "10.5px") + 12 + (LINK_GRAPHS ? SPARK_W + 6 : 0) : 0);
              const y = y2 - LABEL_ROW * (n - i) - 4;
              return { m, key, y, cx: lx + w / 2, cy: y + rowH / 2, left: lx, right: lx + w };
            });
            // The bundle runs as one ribbon from the parent's block to the child's block: each line keeps
            // the same offset across the ribbon (measured to the right of the direction of travel) from end to end,
            // so the lines stay parallel and never cross however the blocks are arranged. Port labels stay inside
            // the child's block, matched to their line by colour.
            const c = e.to;
            const cb = c._box;
            const alone = cb && cb.children?.length === 1;
            const T = cb && cb._bw
              ? alone
                ? { x: cb._bx, y: cb._y, w: cb._bw, h: cb._h }
                : { x: c._x, y: cb._y, w: c._rw || c._cw, h: cb._h }
              : { x: c._x, y: c._y, w: c._rw || c._cw, h: c._ch || CHIP_H };
            const tcx = T.x + T.w / 2, tcy = T.y + T.h / 2;
            let d1, S, E;
            if (T.y > py + ph + 16) {
              d1 = [0, 1]; S = [pcx, py + ph]; E = [tcx, T.y];
            } else if (T.y + T.h < py - 16) {
              d1 = [0, -1]; S = [pcx, py]; E = [tcx, T.y + T.h];
            } else if (tcx >= pcx) {
              d1 = [1, 0]; S = [px + pw, pcy]; E = [T.x, tcy];
            } else {
              d1 = [-1, 0]; S = [px, pcy]; E = [T.x + T.w, tcy];
            }
            // Where the two blocks overlap enough (one above the other, or side by side), run the ribbon straight
            // across the shared part instead of bending it into the gap.
            const ribbon = (n - 1) * LABEL_ROW;
            const axis = d1[0] ? 1 : 0; // the coordinate across the ribbon: x for vertical runs, y for sideways
            const lo = axis ? Math.max(py, T.y) : Math.max(px, T.x);
            const hi = axis ? Math.min(py + ph, T.y + T.h) : Math.min(px + pw, T.x + T.w);
            if (hi - lo >= ribbon + 24) {
              const want = ((axis ? pcy : pcx) + (axis ? tcy : tcx)) / 2;
              const at = Math.min(hi - ribbon / 2 - 12, Math.max(lo + ribbon / 2 + 12, want));
              S[axis] = at;
              E[axis] = at;
            }
            const d2 = d1; // leave one block and enter the other square on, in the same direction
            const nrm = [-d1[1], d1[0]]; // right-hand normal (screen y points down)
            const sgn = d1[1] > 0 || d1[0] < 0 ? 1 : -1; // first port on the left going down, on top going sideways
            const gap = LABEL_ROW;
            const span = Math.abs(d1[0] ? E[0] - S[0] : E[1] - S[1]);
            const h = Math.max(24, span / 2);
            const r = (v) => Math.round(v * 10) / 10;
            return rows
              .map(({ m, key, y }, i) => {
                const color = sv !== null && carries(m, sv) ? vlanColor(sv) : vlanColor(m.untagged?.[0]);
                labels.push({
                  gid: e.gid,
                  x: lx,
                  y,
                  html: `<i class="sw" style="background:${color}"></i><b>${esc(m.label)}</b>${m.speed ? ` ${esc(m.speed)}` : ""} ${vlanChips(m, 4, key)}${peerHtml(m.peer)}`,
                  open: VLAN_EXPANDED.has(key),
                  dim: sv !== null && !carries(m, sv),
                });
                const k = sgn * ((n - 1) / 2 - i) * gap;
                const sx = S[0] + k * nrm[0], sy = S[1] + k * nrm[1];
                const ex = E[0] + k * nrm[0], ey = E[1] + k * nrm[1];
                return `<path class="${lineCls(m.status, m)} member" style="--c:${color}" d="M${r(sx)},${r(sy)} C${r(sx + d1[0] * h)},${r(sy + d1[1] * h)} ${r(ex - d2[0] * h)},${r(ey - d2[1] * h)} ${r(ex)},${r(ey)}"/>`;
              })
              .join("");
          }
          const m = members?.[0];
          if (e.label || m) {
            labels.push({
              gid: e.gid,
              x: x2 + 8,
              y: y2 - LABEL_ROW - 3,
              html: `${esc(e.label)} ${m ? vlanChips(m, 3, `${e.cid}:0`) : ""}${m?.peer ? peerHtml(m.peer) : ""}${
                e.rate
                  ? ` <span class="rate ${linkLoad(e.rate.rx, e.rate.tx, e.rate.speed)}" title="WAN port ${esc(e.label)}: download ${esc(fmtRate(e.rate.rx))}, upload ${esc(fmtRate(e.rate.tx))}${e.latency !== null ? `, latency ${Math.round(e.latency)} ms` : ""}">${esc(wanText(e))}</span>${
                      LINK_GRAPHS && e.rate.port ? `<span class="lspark" data-port="${esc(e.rate.port)}">${sparkSvg(ratePoints("30m", e.rate.port))}</span>` : ""
                    }`
                  : ""
              }`,
              open: VLAN_EXPANDED.has(`${e.cid}:0`),
              dim: sv !== null && !(m && carries(m, sv)),
            });
          }
          const style = sv !== null && m && carries(m, sv) ? ` style="--c:${vlanColor(sv)}"` : "";
          return `<path class="${lineCls(e.status, m)}"${style} d="M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}"/>`;
        })
        .join("") +
        out.trunks
          .map((t) => `<path class="edge" d="M${t.x1},${t.y1} H${t.tx + 8} Q${t.tx},${t.y1} ${t.tx},${t.y1 + 8} V${t.ty - 8} Q${t.tx},${t.ty} ${t.tx + 8},${t.ty} H${t.x2}"/>`)
          .join("");

      const groups = out.groups
        .map(
          (g) => `<div class="group" data-gid="${esc(g.id)}" style="left:${g._bx}px;top:${g._y}px;width:${g._bw}px;height:${g._h}px">
            <div class="glabel"><ha-icon icon="${esc(g.icon)}"></ha-icon><span>${esc(g.label)}</span></div>${(g._bandMarks || [])
              .map((m) => `<div class="band" style="left:${m.x - g._bx}px;top:${m.y - g._y}px">${esc(m.label)} <span>(${m.count})</span></div>`)
              .join("")}${(g._seps || [])
              .map((m) => `<div class="bandsep" style="left:${m.x - g._bx}px;top:${LABEL_H + PAD - 2}px;bottom:${PAD - 2}px"></div>`)
              .join("")}</div>`
        )
        .join("");

      const nodes = out.nodes
        .map((n) => {
          const hasKids = n.groups.some((g) => g.children.length);
          const toggle = hasKids
            ? `<button class="toggle" data-toggle="${esc(n.id)}" title="${n._collapsed ? "Expand" : "Collapse"}">${n._collapsed ? "+" : "−"}</button>`
            : "";
          const badge =
            (n.badge ? `<span class="badge">${n.badge}</span>` : "") +
            (n.logo ? `<img class="logo" src="${esc(n.logo)}" alt="" loading="lazy">` : "") +
            (n.is_new ? `<span class="new">NEW</span>` : "") +
            (n.alert ? `<span class="alertdot ${esc(n.alert)}" title="${esc((n.unifi?.alerts || []).map((a) => a.text).join(", "))}">!</span>` : "");
          const sel =
            (this._editLayout && DRAGGABLE.has(n.kind) ? " draggable" : "") +
            (this._drag?.id === n.id && this._drag.moved ? " dragging" : "") +
            (this._selected === n.id ? " selected" : "") +
            (sv !== null && n.vlan ? (String(n.vlan) === String(sv) ? " vmatch" : " dim") : "");
          const big = BIG_KINDS.has(n.kind) ? " big" : "";
          const square = n.graphic?.type === "switch" ? " square" : ""; // rounded-square icon (switches)
          const client = CLIENT_KINDS.has(n.kind) ? " client" : "";
          return `<div class="node ${esc(n.kind)}${big}${square}${client}${sel}" data-id="${esc(n.id)}" style="left:${n._x}px;top:${n._y}px;width:${n._rw || n._cw}px;height:${n._ch || CHIP_H}px;--rs:${ringSize(n)}px">
              <div class="ring ${esc(n.status)} ${n.graphic ? "g-" + esc(n.graphic.type) : ""}${n.photo && !n.graphic ? " hasphoto" : ""}"${big ? ` style="--rs:${ringSize(n)}px;--bs:${Math.round(16 * DEVICE_SCALE)}px"` : ""}>${
                n.graphic ? deviceGraphic(n, true) : n.photo ? `<img class="cphoto" src="${esc(n.photo)}" alt="" loading="lazy">` : `<ha-icon icon="${esc(n.icon)}"></ha-icon>`
              }${badge}</div>
              <div class="txt"><div class="name" title="${esc(n.name)}">${esc(n.name)}</div>
              <div class="sub" title="${esc(n.subtitle)}">${esc(n.subtitle)}</div></div>${toggle}</div>`;
        })
        .join("");

      const vpWidth = prevVp?.clientWidth || this.getBoundingClientRect().width || 800;
      const zoom = this._drag ? this._drag.zoom : this._zoom ?? Math.min(1, Math.max(0.35, (vpWidth - 4) / W));
      this._fitZoom = zoom;
      this._lastZoom = zoom;
      body = `<div class="sizer" style="width:${W * zoom}px;height:${H * zoom}px">
          <div class="canvas${this._editLayout && this._snap ? " grid" : ""}" style="width:${W}px;height:${H}px;transform:scale(${zoom});--fs:${textFs(this._tp).toFixed(4)};--grid:${this._grid}px">
            ${groups}<svg width="${W}" height="${H}">${edges}</svg>
            ${labels.map((l) => `<div class="elabel${l.dim ? " dim" : ""}${l.open ? " open" : ""}" data-gid="${esc(l.gid || "")}" style="left:${l.x}px;top:${l.y}px">${l.html}</div>`).join("")}${nodes}
          </div></div>`;
    }

    const s = site?.summary || {};
    const siteSel =
      this._sites && this._sites.length > 1
        ? `<select class="site">${this._sites
            .map((x, i) => `<option value="${i}" ${i === this._siteIdx ? "selected" : ""}>${esc(x.site_name)}</option>`)
            .join("")}</select>`
        : `<span class="sitename">${esc(site?.site_name || "")}</span>`;
    const stats = site
      ? `<span class="stat"><ha-icon icon="mdi:wifi"></ha-icon>${s.wireless_clients}</span>
         <span class="stat" title="${site.mac_table ? "From switch MAC tables" : "Enable SSH MAC tables to see wired clients"}"><ha-icon icon="mdi:ethernet"></ha-icon>${site.mac_table ? s.wired_clients : "–"}</span>
         <span class="stat"><ha-icon icon="mdi:access-point"></ha-icon>${s.access_points}</span>
         ${s.new_devices ? `<span class="stat newstat" title="Devices first seen in the last 24 hours">${s.new_devices} new</span>` : ""}`
      : "";
    const stale =
      this._integrationVersion && this._integrationVersion !== CARD_VERSION
        ? `<div class="warn stale"><span>A new version of the map is installed (v${esc(this._integrationVersion)}); this page is still running v${esc(CARD_VERSION)}.</span><button class="reload" data-reload>Reload</button></div>`
        : "";
    const warn = stale + (site?.ssh_errors?.length
      ? `<div class="warn" title="${esc(site.ssh_errors.join("\n"))}">MAC table read failed – see log</div>`
      : "");

    const sel = this._selected && this._index?.get(this._selected);
    const g = sel?.graphic;
    const linked = g?.type === "switch"
      ? Object.entries(g.ports || {}).filter(([, p]) => p.label).sort((a, b) => a[0] - b[0])
      : [];
    const graphicBlock = !g && sel?.photo
      ? `<div class="pgraphic photo"><img class="photo" src="${esc(sel.photo)}" alt="${esc(sel.name)}"></div>`
      : g
      ? `<div class="pgraphic ${esc(g.type)}">${deviceGraphic(sel, false)}</div>${
          g.type === "switch"
            ? `<div class="legend"><span><i class="led up"></i>up</span><span><i class="led down"></i>down</span><span><i class="led off"></i>disabled</span><span><i class="swatch linked"></i>has neighbour</span></div>${
                linked.length
                  ? `<table class="ports">${linked.map(([n, p]) => `<tr><td>1/1/${esc(n)}</td><td>${esc(p.label)}</td></tr>`).join("")}</table>`
                  : ""
              }`
            : ""
        }`
      : "";
    const entryId = site?.entry_id;
    const svc = sel?.services;
    const canScan = this._isAdmin && sel?.ip && !["internet", "ap", "switch", "gateway"].includes(sel.kind);
    const scanning = this._scanning && this._scanning.ip === sel?.ip ? this._scanning.mode : null;
    const svcRow = (x) => {
      const extra = [x.title, x.server, x.banner, x.certificate].filter(Boolean).join(" · ");
      const label = x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.service)}</a>` : esc(x.service);
      const naming = this._labeling && this._labeling.ip === sel.ip && this._labeling.port === x.port;
      const nameBtn =
        this._isAdmin && (x.unknown || x.labelled)
          ? `<button class="linkbtn" data-nameport="${x.port}">${x.labelled ? "rename" : "Name this port"}</button>`
          : "";
      const form = naming
        ? `<form class="portname" data-portform="${x.port}"><input id="ovc-portname" placeholder="What runs on port ${x.port}?" value="${esc(x.labelled ? x.service : "")}" maxlength="60">
             <label class="chk"><input type="checkbox" id="ovc-portall" ${x.label_scope === "all" ? "checked" : ""}> Use for this port on every device</label>
             <div class="row"><button class="act" type="submit">Save</button><button class="act ghost" type="button" data-portcancel>Cancel</button>
             ${x.labelled ? `<button class="act ghost" type="button" data-portclear="${x.port}">Clear name</button>` : ""}</div></form>`
        : "";
      const tag = x.unknown ? `<span class="tag unk">unknown</span>` : x.labelled ? `<span class="tag">${x.label_scope === "all" ? "your name · all devices" : "your name"}</span>` : "";
      return `<tr class="${x.unknown ? "unk" : ""}"><td>${x.port}</td><td>${label} ${tag}${extra ? `<div class="muted">${esc(extra)}</div>` : ""}${nameBtn}${form}</td></tr>`;
    };
    const unknownCount = (svc?.services || []).filter((x) => x.unknown).length;
    const servicesBlock =
      sel && (svc || canScan)
        ? `<div class="section"><div class="sublabel">Services${
            svc?.ts ? ` · ${svc.mode === "full" ? "full scan" : "quick scan"}${svc.engine === "nmap" ? " (nmap)" : ""} · ${esc(ago(svc.ts))}` : ""
          }</div>
            ${svc?.role || svc?.os ? `<div class="role">${esc([svc.role, svc.os].filter(Boolean).join(" · "))}</div>` : ""}
            ${unknownCount ? `<div class="muted">${unknownCount} open port${unknownCount > 1 ? "s" : ""} not recognised: name ${unknownCount > 1 ? "them" : "it"} below.</div>` : ""}
            ${
              svc?.services?.length
                ? `<table class="svc">${svc.services.map(svcRow).join("")}</table>`
                : svc
                ? `<div class="muted">No open ${svc.mode === "full" ? "TCP ports" : "common service ports"} found.</div>`
                : ""
            }
            ${
              canScan
                ? `<div class="row">${
                    scanning
                      ? `<button class="act" disabled>${scanning === "full" ? "Scanning all 65,535 ports… (can take a few minutes)" : "Scanning…"}</button>`
                      : `<button class="act" data-scan="quick" data-ip="${esc(sel.ip)}" title="About 40 common service ports">Quick scan</button>
                         <button class="act ghost" data-scan="full" data-ip="${esc(sel.ip)}" title="Every TCP port, with nmap service detection when available">Full scan (all ports)</button>`
                  }</div>${
                    this._scanErr?.ip === sel.ip ? `<div class="muted err">${esc(this._scanErr.text)}</div>` : ""
                  }<div class="muted">${
                    this._nmap
                      ? "Full scans use nmap with service detection."
                      : "nmap isn't installed in Home Assistant, so full scans use the built-in scanner (slower, less detail). See Settings → Repairs for how to add it."
                  }</div>`
                : ""
            }
          </div>`
        : "";
    // ---- add / edit device form
    const switchOptions = (site?.switches || [])
      .map((x) => `<option value="${esc(x.serial)}" ${this._form?.values?.switch === x.serial ? "selected" : ""}>${esc(x.name)}</option>`)
      .join("");
    const fv = this._form?.values || {};
    const typeOpts = Object.entries(this._types || {})
      .map(([k, v]) => `<option value="${esc(k)}" ${(fv.dev_type || "server") === k ? "selected" : ""}>${esc(v)}</option>`)
      .join("");
    const formBlock = this._form
      ? `<div class="panel"><div class="ptitle"><ha-icon icon="mdi:plus-network"></ha-icon><b>${this._form.mode === "edit" ? "Edit device" : "Add a device"}</b>
           <button class="close" data-formclose>×</button></div>
           <form class="edit devform" data-devform>
             <label>Name<input id="ovc-d-name" required maxlength="80" value="${esc(fv.name || "")}" placeholder="e.g. Proxmox host"></label>
             <label>IP address<input id="ovc-d-ip" value="${esc(fv.ip || "")}" placeholder="192.168.69.20"></label>
             <label>MAC address <span class="muted">(optional; places it from the switch MAC table)</span><input id="ovc-d-mac" value="${esc(fv.mac || "")}" placeholder="aa:bb:cc:dd:ee:ff"></label>
             <label>Type<select id="ovc-d-type">${typeOpts}</select></label>
             <label>Switch<select id="ovc-d-switch"><option value="">Find it automatically</option>${switchOptions}</select></label>
             <label>Switch port <span class="muted">(optional, e.g. 1/1/22)</span><input id="ovc-d-port" value="${esc(fv.port || "")}"></label>
             <label>Discover services<select id="ovc-d-scan">
               <option value="quick" ${(fv.scan || "quick") === "quick" && fv.discover !== false ? "selected" : ""}>Quick scan (about 40 common ports)</option>
               <option value="full" ${fv.scan === "full" && fv.discover !== false ? "selected" : ""}>Full scan (all 65,535 TCP ports)</option>
               <option value="none" ${fv.discover === false ? "selected" : ""}>Don't scan</option></select></label>
             <label>Notes<input id="ovc-d-notes" value="${esc(fv.notes || "")}"></label>
             ${this._formError ? `<div class="formerr">${esc(this._formError)}</div>` : ""}
             <div class="row"><button class="act" type="submit">${this._form.mode === "edit" ? "Save" : "Add to map"}</button>
               <button class="act ghost" type="button" data-formclose>Cancel</button>
               ${this._form.mode === "edit" ? `<button class="act ghost danger" type="button" data-devremove="${esc(fv.id)}">Remove from map</button>` : ""}</div>
           </form></div>`
      : "";
    const canAdd = this._isAdmin && sel && ["wired", "endpoint", "bridge"].includes(sel.kind) && (sel.ip || sel.mac);
    const deviceActions =
      this._isAdmin && sel?.kind === "manual"
        ? `<button class="act ghost" data-devedit>Edit device</button>`
        : canAdd
        ? `<button class="act ghost" data-devpromote>Add to map as a device</button>`
        : "";
    const speedBlock =
      sel?.kind === "internet" && this._speed?.length
        ? `<div class="section"><div class="sublabel">Speed test</div><table>${this._speed
            .map((t) => `<tr><td>${esc(t.source)}</td><td>${esc(speedText(t))}${t.ts ? `<div class="muted">${esc(ago(t.ts))}</div>` : ""}</td></tr>`)
            .join("")}</table></div>`
        : "";
    const typeOptions = Object.entries(this._types || {})
      .map(([k, v]) => `<option value="${esc(k)}" ${sel?.dev_type === k ? "selected" : ""}>${esc(v)}</option>`)
      .join("");
    const editBlock =
      sel?.editable && sel.mac && this._isAdmin
        ? this._editing
          ? `<form class="edit" data-edit="${esc(sel.mac)}"><label>Name<input id="ovc-name" value="${esc(sel.name)}"></label>
               <label>Type<select id="ovc-type"><option value="">Automatic</option>${typeOptions}</select></label>
               <div class="row"><button class="act" type="submit">Save</button><button class="act ghost" type="button" data-cancel>Cancel</button>
               <button class="act ghost" type="button" data-reset>Reset to automatic</button></div></form>`
          : `<button class="act ghost" data-startedit>Rename / change type</button>`
        : "";
    const photoBlock =
      sel?.photo_key && this._isAdmin
        ? `<div class="row photorow">${
            this._photoBusy
              ? `<button class="act ghost" disabled>Saving picture…</button>`
              : `<button class="act ghost" data-photo="pick" title="A PNG, JPEG or WebP picture of this exact device; it is shrunk to ${PHOTO_PX}px before upload">${sel.photo ? "Change picture" : "Add picture"}</button>${
                  sel.photo ? `<button class="act ghost" data-photo="remove">Remove picture</button>` : ""
                }`
          }<input type="file" accept="image/png,image/jpeg,image/webp" data-photofile hidden></div>${
            this._photoMsg ? `<div class="muted err">${esc(this._photoMsg)}</div>` : ""
          }`
        : "";
    const panel = sel
      ? `<div class="panel ${g?.type === "switch" ? "wide xwide" : sel.unifi ? "wide" : ""}"><div class="ptitle"><ha-icon icon="${esc(sel.icon)}"></ha-icon><b>${esc(sel.name)}</b>
           ${sel.is_new ? `<span class="new inline">NEW</span>` : ""}<button class="close" data-close>×</button></div>${graphicBlock}
           ${speedBlock}${this._unifiBlock(sel)}
           <table>${(sel.details || []).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>
           ${servicesBlock}${editBlock}${photoBlock}${deviceActions}</div>`
      : "";

    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card>
        <div class="header">
          ${cfg.title === false ? "" : `<div class="title">${esc(cfg.title || "Network map")}</div>`}${siteSel}
          <div class="stats">${stats}</div>
          ${
            this._canEditLayout()
              ? `<div class="layout">${
                  this._editLayout
                    ? `<span class="hint">Drag devices to arrange the map${this._snap ? " (hold Alt to place freely)" : ""}</span><button data-layout="add" title="Add a server or other wired device to the map">+ Add device</button><button data-layout="snap" class="${this._snap ? "on" : ""}" aria-pressed="${this._snap}" title="Snap devices to a ${this._grid}px grid">Snap to grid</button><button data-layout="reset" title="Put every device back where it was drawn automatically">Reset layout</button><button data-layout="done" class="on">Done</button>`
                    : `${this._isAdmin ? `<button data-layout="add" title="Add a server or other wired device to the map">+ Add device</button>` : ""}<button data-layout="edit" title="Drag devices to rearrange the map">Edit layout</button>`
                }</div>`
              : ""
          }<div class="zoom">
            <span class="viewwrap"><button class="viewbtn${this._viewOpen ? " on" : ""}" data-viewmenu aria-haspopup="true" aria-expanded="${!!this._viewOpen}" title="Text, client and device size"><ha-icon icon="mdi:tune-variant"></ha-icon>View</button>${this._viewOpen ? this._viewMenu() : ""}</span>
            <span class="grp" title="Zoom"><ha-icon icon="mdi:magnify"></ha-icon><button data-zoom="-" title="Zoom out" aria-label="Zoom out">−</button><button data-zoom="fit" title="Fit to width">fit</button><button data-zoom="+" title="Zoom in" aria-label="Zoom in">+</button></span>
          </div>
        </div>${warn}${
          site && Object.keys(site.vlans || {}).length && cfg.show_vlans !== false
            ? `<div class="vlans"><span class="vlabel">VLANs</span>${Object.entries(site.vlans)
                .map(
                  ([id, v]) =>
                    `<button class="vchip${String(this._vlan) === id ? " on" : ""}${v.up ? "" : " down"}" data-vlan="${esc(id)}" style="--c:${vlanColor(id)}" title="${esc(v.name)}${v.up ? "" : " (down)"}"><i></i>${esc(id)}<span>${esc(v.name)}</span></button>`
                )
                .join("")}${this._vlan != null ? `<button class="vchip clear" data-vlan="">Clear</button>` : ""}</div>`
            : ""
        }
        <div class="viewport" style="${cfg.height === "fill" ? "" : `height:${cfg.height}px`}">${this._error && !site ? `<div class="empty">${esc(this._error)}</div>` : body}</div>
        ${this._form ? formBlock : panel}
      </ha-card>`;

    const vp = this.shadowRoot.querySelector(".viewport");
    const canvas = this.shadowRoot.querySelector(".canvas");
    if (canvas) {
      // Only measure when the card is actually laid out: HA often renders it while still hidden,
      // where every size reads 0 (that used to shrink every box to a thin sliver).
      const visible = canvas.offsetWidth > 0 && canvas.getClientRects().length > 0;
      if (!visible) this._needsMeasure = true;
      // Safety net: if a label still renders wider than estimated, grow its box to cover it.
      // Positions come from the inline styles we set (layout units), widths from the label itself.
      const boxes = new Map([...canvas.querySelectorAll(".group[data-gid]")].map((b) => [b.dataset.gid, b]));
      for (const l of visible ? canvas.querySelectorAll(".elabel[data-gid]") : []) {
        const box = boxes.get(l.dataset.gid);
        if (!box || !l.offsetWidth) continue;
        const need = parseFloat(l.style.left) + l.offsetWidth + PAD - parseFloat(box.style.left);
        if (need > parseFloat(box.style.width)) box.style.width = `${need}px`;
      }
      // Link labels are HTML, so their width is only known after layout: grow the map to fit them.
      const labelEls = visible ? [...canvas.querySelectorAll(".elabel")] : [];
      const right = Math.max(0, ...labelEls.map((l) => parseFloat(l.style.left) + l.offsetWidth + 12));
      const w = parseFloat(canvas.style.width);
      if (right > w) {
        const zoom = this._lastZoom || 1;
        canvas.style.width = `${right}px`;
        const sizer = this.shadowRoot.querySelector(".sizer");
        if (sizer) sizer.style.width = `${right * zoom}px`;
        const svg = canvas.querySelector("svg");
        if (svg) svg.setAttribute("width", right);
      }
    }
    if (scroll && vp) [vp.scrollLeft, vp.scrollTop] = scroll;
    const panelEl = this.shadowRoot.querySelector(".panel");
    if (panelEl && panelScroll) panelEl.scrollTop = panelScroll;
    this._panelFor = this._selected;
    const menu = this.shadowRoot.querySelector(".viewmenu");
    if (menu && menuScroll) menu.scrollTop = menuScroll;
    this._bind();
  }

  _applyServerView(view) {
    this._serverView = view || {};
    if (!this._local?.font && viewText(view)) this._tp = viewText(view);
    const cs = sizeFrom(view.clients, view.client, CS_MIN, CS_MAX);
    const ds = sizeFrom(view.devices, view.device, DS_MIN, DS_MAX);
    if (!this._local?.client && cs) this._cs = cs;
    if (!this._local?.device && ds) this._ds = ds;
  }

  _chartBlock(u, port, range) {
    const p = (u.ports || []).find((x) => x.ifname === port) || {};
    const wan = p.role === "wan";
    const title = wan && /^Port \d+$/.test(p.name || "") ? "WAN" : p.name || port;
    const dnLabel = wan ? "Download" : `To ${p.name || port}`, upLabel = wan ? "Upload" : `From ${p.name || port}`;
    const pts = ratePoints(range, port);
    const last = pts[pts.length - 1];
    const now = (k) => (last ? fmtRate(last[k]) : "–");
    return `<div class="uchead"><b>${esc(title)}</b><span class="muted">${esc(port)}${wan && title !== "WAN" ? " · WAN" : ""}</span>
        <span class="rchips">${this._shownRanges()
          .map((k) => `<button class="rchip${k === range ? " on" : ""}" data-rrange="${k}">${RANGE_INFO[k].label}</button>`)
          .join("")}</span></div>
      <div class="ulegend"><span><i class="key dn"></i>↓ ${esc(dnLabel)} <b data-now="dn">${esc(now(1))}</b></span><span><i class="key up"></i>↑ ${esc(upLabel)} <b data-now="up">${esc(now(2))}</b></span></div>
      <div class="uchartwrap" data-port="${esc(port)}" data-range="${range}" data-dn="${esc(dnLabel)}" data-up="${esc(upLabel)}">${bandwidthChart(pts, range, dnLabel, upLabel)}<div class="utip" hidden></div></div>`;
  }

  // Gateway details from the UniFi controller: port table, Internet and health.
  _unifiBlock(sel) {
    const u = sel?.unifi;
    if (!u) return "";
    const range = this._chartRange();
    const port = this._rPort || (u.ports || []).find((p) => p.role === "wan")?.ifname || u.ports?.[0]?.ifname;
    const pct = (v) => (num(v) === null ? "" : `${Math.round(v)}%`);
    const bytes = (v) => (num(v) === null ? "" : v >= 1e9 ? `${(v / 1e9).toFixed(1)} GB` : v >= 1e6 ? `${Math.round(v / 1e6)} MB` : `${Math.round(v / 1e3)} kB`);
    const row = (k, v, extra) => (v === "" || v == null ? "" : `<tr><td>${esc(k)}</td><td>${esc(v)}${extra ? `<div class="muted">${esc(extra)}</div>` : ""}</td></tr>`);
    const alerts = (u.alerts || [])
      .map((a) => `<div class="ualert ${esc(a.level)}"><ha-icon icon="${a.level === "info" ? "mdi:information-outline" : "mdi:alert"}"></ha-icon>${esc(a.text)}</div>`)
      .join("");
    const ports = (u.ports || [])
      .map((p) => {
        const load = p.up ? linkLoad(p.rx, p.tx, p.speed) : "";
        const dn = p.role === "wan" ? p.rx : p.tx, upv = p.role === "wan" ? p.tx : p.rx;
        return `<tr class="${p.up ? "" : "off"}${p.ifname === port ? " sel" : ""}" data-rport="${esc(p.ifname)}" title="Show ${esc(p.name)} on the graph"><td>${esc(p.ifname)}</td><td>${esc(p.name)}${p.role === "wan" ? ` <span class="tag">WAN</span>` : ""}</td>
          <td>${p.up ? `${p.speed >= 1000 ? p.speed / 1000 + "G" : p.speed + "M"}` : "down"}</td>
          <td class="trend"><span class="tspark" data-port="${esc(p.ifname)}">${p.up ? sparkSvg(ratePoints(range, p.ifname), 48, 16) : ""}</span></td>
          <td class="num ${load}">${p.up ? esc(fmtRate(dn)) : ""}</td><td class="num ${load}">${p.up ? esc(fmtRate(upv)) : ""}</td></tr>`;
      })
      .join("");
    const i = u.internet || {};
    const st = i.speedtest || {};
    const h = u.health || {};
    const temps = (h.temps || []).map((t) => `${t.name} ${t.value} °C`).join(", ");
    const store = (h.storage || []).map((x) => `${x.name} ${bytes(x.used)} of ${bytes(x.size)}`).join(" · ");
    const mons = (i.monitors || []).filter((m) => m.latency != null).map((m) => `${m.target} ${m.latency} ms`).join(" · ");
    return `${alerts ? `<div class="ualerts">${alerts}</div>` : ""}
      <div class="section"><div class="sublabel">Ports</div>
        <table class="uports"><tr><th>Port</th><th>Name</th><th>Link</th><th>Trend</th><th class="num" title="Towards the device on this port (WAN: download)">↓</th><th class="num" title="From the device on this port (WAN: upload)">↑</th></tr>${ports}</table></div>
      <div class="section"><div class="sublabel">Bandwidth</div>${this._chartBlock(u, port, range)}</div>
      <div class="section"><div class="sublabel">Internet</div><table>
        ${row("Status", [i.status, i.isp].filter(Boolean).join(" · "))}
        ${row("WAN IP", i.wan_ip)}
        ${row("Traffic", i.port ? `↓ ${fmtRate(i.rx)} · ↑ ${fmtRate(i.tx)}` : "", i.port ? `on ${i.port}${i.link_speed ? ", " + i.link_speed + " Mbps link" : ""}` : "")}
        ${row("Availability", num(i.availability) !== null ? `${i.availability}%` : "")}
        ${row("Latency", num(i.latency) !== null ? `${Math.round(i.latency)} ms` : "", mons)}
        ${row("Speed test", st.down ? `↓ ${Math.round(st.down)} / ↑ ${Math.round(st.up)} Mbps${st.ping ? ` · ${Math.round(st.ping)} ms` : ""}` : "", [st.ts ? ago(st.ts) : "", st.server].filter(Boolean).join(" · "))}
      </table></div>
      <div class="section"><div class="sublabel">Health</div><table>
        ${row("CPU", pct(h.cpu), (h.load || []).every((v) => v != null) ? `load ${h.load.join(" / ")}` : "")}
        ${row("Memory", pct(h.mem))}
        ${row("Temperature", temps, h.overheating ? "overheating" : "")}
        ${row("Firmware", h.version ? `${h.version}${h.upgradable ? ` → ${h.upgrade_to || "update available"}` : " (up to date)"}` : "")}
        ${row("Storage", store)}
        ${row("Threat signatures", h.ips_rules ? `${Number(h.ips_rules).toLocaleString()} rules` : "", h.ips_updated ? `updated ${ago(h.ips_updated)}` : "")}
      </table></div>`;
  }

  _viewMenu() {
    const row = (label, key, value, hint) => `<div class="vrow"><span class="vlab">${label}<small>${hint}</small></span>
      <span class="vctl"><button data-${key}="-" aria-label="Smaller ${label.toLowerCase()}">−</button><b>${value}</b><button data-${key}="+" aria-label="Larger ${label.toLowerCase()}">+</button></span></div>`;
    const sv = this._serverView || {};
    const svCs = sizeFrom(sv.clients, sv.client, CS_MIN, CS_MAX);
    const svDs = sizeFrom(sv.devices, sv.device, DS_MIN, DS_MAX);
    const saved = viewText(sv) || svCs || svDs;
    return `<div class="viewmenu" role="dialog" aria-label="Map view">
      ${row("Text", "font", `${this._tp || TEXT_DEFAULT}pt`, "size of device names; other map labels follow")}
      ${row("Clients", "csize", `${this._cs || 1}×`, "wireless and wired clients")}
      ${row("Devices", "dsize", `${this._ds || 1}×`, "switches, APs, gateway, client counts")}
      ${this._hasUnifi() ? `<div class="vsec">Bandwidth graphs</div>
      <div class="vrow vstack"><span class="vlab">Range buttons<small>which time ranges the gateway chart offers${
        rangesAvailable().length < RANGE_DEFS.length ? "; longer ones need a longer history in the integration options" : ""
      }</small></span>
        <span class="vchips">${RANGE_DEFS.map(([k, l]) => {
          const ok = rangesAvailable().includes(k), on = this._rateRanges.includes(k);
          return `<button class="rchip${on ? " on" : ""}" data-rangeopt="${k}" aria-pressed="${on}" ${ok ? "" : 'disabled title="Not kept: raise the long history in the integration options"'}>${l}</button>`;
        }).join("")}</span></div>
      <div class="vrow"><span class="vlab">Units<small>for live rates and the graphs</small></span>
        <span class="vchips">${[["bits", "bits/s"], ["bytes", "bytes/s"]]
          .map(([k, l]) => `<button class="rchip${this._rateUnit === k ? " on" : ""}" data-runit="${k}" aria-pressed="${this._rateUnit === k}">${l}</button>`)
          .join("")}</span></div>
      <label class="vrow vtoggle"><span class="vlab">Traffic graphs on links<small>a small 30-minute trend next to each gateway link's live rate</small></span>
        <input type="checkbox" data-linkgraphs ${this._linkGraphs ? "checked" : ""}></label>` : ""}
      <div class="vfoot">
        ${this._isAdmin ? `<button class="act" data-viewsave title="Every browser and device starts with these sizes">Save as default for all devices</button>` : ""}
        <button class="act ghost" data-viewreset>${saved ? "Use saved default" : "Reset"}</button>
        <div class="muted">${this._viewSaved ? "Saved as the default for all devices." : "Changes are remembered in this browser."}${saved ? ` Saved default: text ${viewText(sv) || TEXT_DEFAULT}pt, clients ${svCs || 1}×, devices ${svDs || 1}×.` : ""}</div>
      </div>${this._isAdmin ? `
      <div class="vfoot backup">
        <div class="vlab">Backup &amp; restore<small>settings, manual devices, names, layout and your device images; never your Cirrus or UniFi credentials</small></div>
        <div class="vbtns"><button class="act" data-backup ${this._backupBusy ? "disabled" : ""}>Download backup</button>
          <button class="act ghost" data-restore ${this._backupBusy ? "disabled" : ""}>Restore from file…</button></div>
        <input type="file" accept=".json,application/json" data-restorefile hidden>
        ${this._backupMsg ? `<div class="muted${this._backupMsg.error ? " err" : ""}">${esc(this._backupMsg.text)}</div>` : ""}
      </div>` : ""}</div>`;
  }

  _canEditLayout() {
    const mode = this._config.layout_editing;
    return mode === true || (mode !== false && this._isAdmin);
  }

  _startDrag(ev, id) {
    if (ev.button !== undefined && ev.button !== 0) return;
    const zoom = this._lastZoom || 1;
    const node = this._index?.get(id);
    this._drag = {
      id, sx: ev.clientX, sy: ev.clientY, base: this._off(id), moved: false, zoom,
      nx: node?._x ?? 0, ny: node?._y ?? 0, // where the device was drawn when the drag started
    };
    const move = (e) => {
      const d = this._drag;
      if (!d) return;
      const dx = (e.clientX - d.sx) / d.zoom;
      const dy = (e.clientY - d.sy) / d.zoom;
      if (!d.moved && Math.hypot(dx, dy) < 4) return;
      d.moved = true;
      let mx = dx, my = dy;
      if (this._snap && !e.altKey) {
        // Snap the device's top-left corner to the grid the canvas shows.
        const g = this._grid;
        mx = Math.round((d.nx + dx) / g) * g - d.nx;
        my = Math.round((d.ny + dy) / g) * g - d.ny;
      }
      this._offsets = { ...this._offsets, [d.id]: [d.base[0] + mx, d.base[1] + my] };
      if (!this._raf) {
        this._raf = requestAnimationFrame(() => {
          this._raf = null;
          this._render();
        });
      }
    };
    const up = async () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      const d = this._drag;
      this._drag = null;
      if (!d?.moved) return;
      this._suppressClick = true;
      const offset = this._offsets[d.id];
      this._localOffsets = { ...(this._localOffsets || {}), [d.id]: offset };
      this._render();
      const site = this._sites?.[this._siteIdx];
      try {
        await this._hass.callWS({
          type: "ovcirrus/set_position", entry_id: site.entry_id, site_id: site.site_id,
          node_id: d.id, dx: offset[0], dy: offset[1],
        });
      } catch (err) {
        console.warn("ovcirrus: could not save layout", err);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-toggle]").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const node = this._index.get(b.dataset.toggle);
        this._collapsed.set(node.id, !this._isCollapsed(node));
        this._render();
      })
    );
    root.querySelectorAll(".node.draggable").forEach((el) =>
      el.addEventListener("pointerdown", (ev) => {
        if (ev.target.closest("button")) return;
        ev.preventDefault();
        this._startDrag(ev, el.dataset.id);
      })
    );
    root.querySelectorAll("[data-layout]").forEach((b) =>
      b.addEventListener("click", async () => {
        const action = b.dataset.layout;
        if (action === "edit") {
          this._editLayout = true;
          this._zoom = this._zoom ?? this._fitZoom; // keep the scale steady while arranging
        }
        if (action === "done") this._editLayout = false;
        if (action === "add") {
          this._form = { mode: "add", values: { dev_type: "server", scan: "quick" } };
          this._formError = null;
        }
        if (action === "snap") {
          this._snap = !this._snap;
          try {
            localStorage.setItem(SNAP_KEY, this._snap ? "1" : "0");
          } catch (e) {
            // storage blocked: the setting still applies until the page reloads
          }
        }
        if (action === "reset") {
          const site = this._sites?.[this._siteIdx];
          this._localOffsets = {};
          this._offsets = {};
          if (site) site.positions = {};
          try {
            await this._hass.callWS({ type: "ovcirrus/reset_positions", entry_id: site.entry_id, site_id: site.site_id });
          } catch (err) {
            console.warn("ovcirrus: could not reset layout", err);
          }
        }
        this._render();
      })
    );
    root.querySelectorAll(".node").forEach((el) =>
      el.addEventListener("click", () => {
        if (this._suppressClick) {
          this._suppressClick = false;
          return;
        }
        this._selected = this._selected === el.dataset.id ? null : el.dataset.id;
        this._render();
      })
    );
    root.querySelector("[data-close]")?.addEventListener("click", () => {
      this._selected = null;
      this._editing = false;
      this._render();
    });
    const entryId = this._sites?.[this._siteIdx]?.entry_id;
    root.querySelectorAll("[data-scan]").forEach((b) =>
      b.addEventListener("click", async () => {
        const ip = b.dataset.ip, mode = b.dataset.scan;
        this._scanning = { ip, mode };
        this._render();
        this._scanErr = null;
        try {
          await this._hass.callWS({ type: "ovcirrus/discover", entry_id: entryId, ip, mode });
        } catch (err) {
          console.warn("ovcirrus scan failed", err);
          this._scanErr = { ip, text: `The scan failed: ${err?.message || err?.code || err}` };
        }
        this._scanning = null;
        await this._fetch();
      })
    );
    // your own picture of this client / device
    const photoInput = root.querySelector("[data-photofile]");
    root.querySelector('[data-photo="pick"]')?.addEventListener("click", () => photoInput?.click());
    const sendPhoto = async (data) => {
      const sel = this._index?.get(this._selected);
      if (!sel?.photo_key) return;
      this._photoBusy = true;
      this._photoMsg = null;
      this._render();
      try {
        await this._hass.callWS({ type: "ovcirrus/set_photo", entry_id: entryId, key: sel.photo_key, data });
      } catch (err) {
        this._photoMsg = err?.message || "The picture could not be saved.";
      }
      this._photoBusy = false;
      await this._fetch();
    };
    photoInput?.addEventListener("change", async () => {
      const file = photoInput.files?.[0];
      if (!file) return;
      try {
        sendPhoto(await shrinkPhoto(file));
      } catch (err) {
        this._photoMsg = "That file could not be read as a picture.";
        this._render();
      }
    });
    root.querySelector('[data-photo="remove"]')?.addEventListener("click", () => sendPhoto(null));
    // name unknown ports
    root.querySelectorAll("[data-nameport]").forEach((b) =>
      b.addEventListener("click", () => {
        const sel = this._index?.get(this._selected);
        this._labeling = { ip: sel?.ip, port: Number(b.dataset.nameport) };
        this._editing = true; // pause refresh re-renders while typing
        this._render();
        root.getElementById("ovc-portname")?.focus();
      })
    );
    const savePort = async (port, label, all) => {
      const sel = this._index?.get(this._selected);
      try {
        await this._hass.callWS({ type: "ovcirrus/label_port", entry_id: entryId, ip: sel?.ip, port, label, all_devices: all });
      } catch (err) {
        console.warn("ovcirrus: could not name port", err);
      }
      this._labeling = null;
      this._editing = false;
      await this._fetch();
    };
    root.querySelector("[data-portform]")?.addEventListener("submit", (ev) => {
      ev.preventDefault();
      savePort(Number(ev.currentTarget.dataset.portform), root.getElementById("ovc-portname").value, root.getElementById("ovc-portall").checked);
    });
    root.querySelector("[data-portcancel]")?.addEventListener("click", () => {
      this._labeling = null;
      this._editing = false;
      this._render();
    });
    root.querySelector("[data-portclear]")?.addEventListener("click", (ev) =>
      savePort(Number(ev.currentTarget.dataset.portclear), null, root.getElementById("ovc-portall").checked)
    );
    // add / edit / remove devices
    const openForm = (mode, values) => {
      this._form = { mode, values };
      this._formError = null;
      this._editing = true;
      this._render();
      root.getElementById("ovc-d-name")?.focus();
    };
    root.querySelector("[data-devpromote]")?.addEventListener("click", () => {
      const sel = this._index?.get(this._selected);
      const port = (sel.details || []).find(([k]) => k === "Port")?.[1];
      openForm("add", {
        name: sel.name, ip: sel.ip || "", mac: sel.mac || "", dev_type: sel.dev_type && sel.dev_type !== "unknown" ? sel.dev_type : "server",
        port: port || "", scan: "quick",
      });
    });
    root.querySelector("[data-devedit]")?.addEventListener("click", () => {
      const m = this._index?.get(this._selected)?.manual || {};
      openForm("edit", { ...m, dev_type: m.type || "server" });
    });
    root.querySelectorAll("[data-formclose]").forEach((b) =>
      b.addEventListener("click", () => {
        this._form = null;
        this._editing = false;
        this._render();
      })
    );
    root.querySelector("[data-devremove]")?.addEventListener("click", async (ev) => {
      try {
        await this._hass.callWS({ type: "ovcirrus/remove_device", entry_id: entryId, id: ev.currentTarget.dataset.devremove });
      } catch (err) {
        console.warn("ovcirrus: could not remove device", err);
      }
      this._form = null;
      this._editing = false;
      this._selected = null;
      await this._fetch();
    });
    root.querySelector("[data-devform]")?.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const v = (id) => root.getElementById(id).value.trim();
      const msg = {
        type: "ovcirrus/save_device", entry_id: entryId, name: v("ovc-d-name"), ip: v("ovc-d-ip") || null,
        mac: v("ovc-d-mac") || null, dev_type: v("ovc-d-type"), switch: v("ovc-d-switch") || null,
        port: v("ovc-d-port") || null, scan: v("ovc-d-scan"), notes: v("ovc-d-notes") || null,
      };
      if (this._form.mode === "edit" && this._form.values.id) msg.id = this._form.values.id;
      try {
        const res = await this._hass.callWS(msg);
        this._form = null;
        this._editing = false;
        this._selected = `manual-${res?.device?.id || msg.id}`;
        if (msg.scan !== "none" && msg.ip) this._scanning = { ip: msg.ip, mode: msg.scan };
        await this._fetch();
        if (this._scanning) setTimeout(() => this._fetch(), msg.scan === "full" ? 30000 : 6000);
        setTimeout(() => ((this._scanning = null), this._fetch()), msg.scan === "full" ? 240000 : 15000);
      } catch (err) {
        this._formError = err?.message || String(err);
        this._render();
      }
    });
    root.querySelector("[data-startedit]")?.addEventListener("click", () => {
      this._editing = true;
      this._render();
    });
    root.querySelector("[data-cancel]")?.addEventListener("click", () => {
      this._editing = false;
      this._render();
    });
    const save = async (mac, name, devType) => {
      try {
        await this._hass.callWS({ type: "ovcirrus/set_override", entry_id: entryId, mac, name, dev_type: devType || null });
      } catch (err) {
        console.warn("ovcirrus override failed", err);
      }
      this._editing = false;
      await this._fetch();
    };
    const form = root.querySelector("form.edit[data-edit]"); // rename form only, not the device form
    form?.addEventListener("submit", (ev) => {
      ev.preventDefault();
      save(form.dataset.edit, root.getElementById("ovc-name").value, root.getElementById("ovc-type").value);
    });
    root.querySelector("[data-reset]")?.addEventListener("click", () => save(form.dataset.edit, null, null));
    root.querySelector("[data-reload]")?.addEventListener("click", async (ev) => {
      ev.currentTarget.disabled = true;
      ev.currentTarget.textContent = "Reloading…";
      // Drop any cached copy of the card's files, then reload: the reloaded page links the new version.
      // The cache clean-up is best effort and capped, so the reload always happens.
      const purge = (async () => {
        if (!window.caches) return;
        for (const name of await caches.keys()) {
          const cache = await caches.open(name);
          for (const req of await cache.keys()) {
            if (req.url.includes("/ovcirrus_static/")) await cache.delete(req);
          }
        }
      })().catch(() => {});
      await Promise.race([purge, new Promise((r) => setTimeout(r, 1500))]);
      window.location.reload();
    });
    root.querySelector("[data-viewmenu]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this._viewOpen = !this._viewOpen;
      this._viewSaved = false;
      this._render();
    });
    root.querySelector(".viewmenu")?.addEventListener("click", (ev) => ev.stopPropagation());
    if (!this._closeView) {
      // Close the menu on any click outside it, or Escape.
      this._closeView = (ev) => {
        if (!this._viewOpen) return;
        if (ev.type === "keydown" && ev.key !== "Escape") return;
        if (ev.type === "click" && ev.composedPath().some((el) => el.classList?.contains("viewwrap"))) return;
        this._viewOpen = false;
        this._render();
      };
      window.addEventListener("click", this._closeView);
      window.addEventListener("keydown", this._closeView);
    }
    const forgetLocal = () => {
      try {
        [TEXT_KEY, FS_KEY, CS_KEY, DS_KEY, CS_OLD_KEY, DS_OLD_KEY].forEach((k) => localStorage.removeItem(k));
      } catch (e) {
        // storage blocked: nothing stored to forget
      }
      this._local = { font: false, client: false, device: false };
    };
    root.querySelector("[data-viewsave]")?.addEventListener("click", async () => {
      const view = { text: this._tp || TEXT_DEFAULT, clients: this._cs || 1, devices: this._ds || 1 };
      try {
        await this._hass.callWS({ type: "ovcirrus/save_view", entry_id: entryId, ...view });
        forgetLocal(); // this browser now follows the saved default too
        this._serverView = view;
        this._viewSaved = true;
      } catch (err) {
        console.warn("ovcirrus: could not save view", err);
      }
      this._render();
    });
    const backupCount = (sm) =>
      [
        [sm.manual_devices, "manual device"], [sm.named_devices, "renamed device"], [sm.moved_devices, "moved device"],
        [sm.port_names, "port name"], [sm.images, "image"],
      ]
        .filter(([n]) => n)
        .map(([n, w]) => `${n} ${w}${n === 1 ? "" : "s"}`)
        .join(", ") || "no map changes";
    root.querySelector("[data-backup]")?.addEventListener("click", async () => {
      this._backupBusy = true;
      this._backupMsg = { text: "Preparing backup…" };
      this._render();
      try {
        const backup = await this._hass.callWS({ type: "ovcirrus/backup", entry_id: entryId });
        const blob = new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        const name = String(backup.entry?.title || "ovcirrus").replace(/[^\w.-]+/g, "-").toLowerCase();
        a.href = URL.createObjectURL(blob);
        a.download = `ovcirrus-backup-${name}-${backup.created?.slice(0, 10) || "now"}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        const skipped = backup.images_skipped?.length ? ` ${backup.images_skipped.length} large image(s) left out.` : "";
        this._backupMsg = { text: `Backup downloaded: ${backupCount(backup.summary || {})}.${skipped}` };
      } catch (err) {
        this._backupMsg = { text: `Backup failed: ${err?.message || err}`, error: true };
      }
      this._backupBusy = false;
      this._render();
    });
    root.querySelector("[data-restore]")?.addEventListener("click", () => root.querySelector("[data-restorefile]")?.click());
    root.querySelector("[data-restorefile]")?.addEventListener("change", async (ev) => {
      const file = ev.target.files?.[0];
      if (!file) return;
      let backup;
      try {
        backup = JSON.parse(await file.text());
        if (backup?.format !== "ovcirrus-backup") throw new Error("not an OmniVista Cirrus backup");
      } catch (err) {
        this._backupMsg = { text: `Can't read ${file.name}: ${err?.message || err}`, error: true };
        this._render();
        return;
      }
      const when = backup.created ? new Date(backup.created).toLocaleString() : "an unknown date";
      const ok = window.confirm(
        `Restore the backup from ${when} (version ${backup.integration_version || "?"})?\n\n` +
          `It contains ${backupCount(backup.summary || {})}.\n\n` +
          "This replaces the current settings, manual devices, names and layout. Your credentials stay as they are. " +
          "The integration reloads afterwards."
      );
      if (!ok) return;
      this._backupBusy = true;
      this._backupMsg = { text: "Restoring…" };
      this._render();
      try {
        const res = await this._hass.callWS({ type: "ovcirrus/restore", entry_id: entryId, backup });
        const notes = res.notes?.length ? ` ${res.notes.join(" ")}` : "";
        this._backupMsg = { text: `Restored ${backupCount(res.summary || {})}. Reloading the integration…${notes}` };
        forgetLocal(); // follow the restored saved view
        this._localOffsets = {};
        [3000, 8000].forEach((t) => setTimeout(() => this._fetch(), t));
      } catch (err) {
        this._backupMsg = { text: `Restore failed: ${err?.message || err}`, error: true };
      }
      this._backupBusy = false;
      this._render();
    });
    root.querySelector("[data-viewreset]")?.addEventListener("click", () => {
      forgetLocal();
      const sv = this._serverView || {};
      const cfg = this._config;
      this._tp = viewText(sv) || configText(cfg);
      this._cs = sizeFrom(sv.clients, sv.client, CS_MIN, CS_MAX) || this._cfgClient();
      this._ds = sizeFrom(sv.devices, sv.device, DS_MIN, DS_MAX) || this._cfgDevice();
      this._viewSaved = false;
      this._render();
    });
    root.querySelectorAll("[data-csize]").forEach((b) =>
      b.addEventListener("click", () => {
        const step = b.dataset.csize === "+" ? CS_STEP : -CS_STEP;
        this._cs = clampSize((this._cs || 1) + step, CS_MIN, CS_MAX);
        this._local = { ...this._local, client: true };
        this._viewSaved = false;
        try {
          localStorage.setItem(CS_KEY, String(this._cs));
          localStorage.removeItem(CS_OLD_KEY);
        } catch (e) {
          // storage blocked: the size still applies until the page reloads
        }
        this._render();
      })
    );
    root.querySelectorAll("[data-dsize]").forEach((b) =>
      b.addEventListener("click", () => {
        const step = b.dataset.dsize === "+" ? DS_STEP : -DS_STEP;
        this._ds = clampSize((this._ds || 1) + step, DS_MIN, DS_MAX);
        this._local = { ...this._local, device: true };
        this._viewSaved = false;
        try {
          localStorage.setItem(DS_KEY, String(this._ds));
          localStorage.removeItem(DS_OLD_KEY);
        } catch (e) {
          // storage blocked: the size still applies until the page reloads
        }
        this._render();
      })
    );
    root.querySelectorAll("[data-font]").forEach((b) =>
      b.addEventListener("click", () => {
        const step = b.dataset.font === "+" ? 1 : -1;
        this._tp = clampText((this._tp || TEXT_DEFAULT) + step);
        this._local = { ...this._local, font: true };
        this._viewSaved = false;
        try {
          localStorage.setItem(TEXT_KEY, String(this._tp));
          localStorage.removeItem(FS_KEY);
        } catch (e) {
          // storage blocked: the size still applies until the page reloads
        }
        this._render();
      })
    );
    root.querySelector("[data-linkgraphs]")?.addEventListener("change", (ev) => {
      this._linkGraphs = ev.target.checked;
      try {
        localStorage.setItem(LINK_GRAPHS_KEY, this._linkGraphs ? "1" : "0");
      } catch (e) {
        // storage blocked: applies until the page reloads
      }
      this._render();
    });
    const keep = (key, value) => {
      try {
        localStorage.setItem(key, value);
      } catch (e) {
        // storage blocked: applies until the page reloads
      }
    };
    root.querySelectorAll("[data-rrange]").forEach((b) =>
      b.addEventListener("click", () => {
        this._rRange = b.dataset.rrange;
        keep(RANGE_KEY, this._rRange);
        this._render();
      })
    );
    root.querySelectorAll("[data-rangeopt]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.rangeopt;
        const on = this._rateRanges.includes(k);
        if (on && this._rateRanges.length === 1) return; // keep at least one
        this._rateRanges = on ? this._rateRanges.filter((x) => x !== k) : [...this._rateRanges, k];
        keep(RANGES_KEY, JSON.stringify(this._rateRanges));
        this._render();
      })
    );
    root.querySelectorAll("[data-runit]").forEach((b) =>
      b.addEventListener("click", () => {
        this._rateUnit = RATE_UNIT = b.dataset.runit;
        keep(RATE_UNIT_KEY, this._rateUnit);
        this._render();
      })
    );
    root.querySelectorAll("tr[data-rport]").forEach((r) =>
      r.addEventListener("click", () => {
        this._rPort = r.dataset.rport;
        this._render();
      })
    );
    {
      const wrap = root.querySelector(".uchartwrap");
      if (wrap) this._bindChart(wrap, ratePoints(wrap.dataset.range, wrap.dataset.port));
      this._fetchRates();
    }
    root.querySelectorAll("[data-zoom]").forEach((b) =>
      b.addEventListener("click", () => {
        const z = this._zoom ?? this._fitZoom ?? 1;
        if (b.dataset.zoom === "fit") this._zoom = null;
        else this._zoom = Math.min(2, Math.max(0.2, z * (b.dataset.zoom === "+" ? 1.2 : 1 / 1.2)));
        try {
          localStorage.setItem(ZOOM_KEY, this._zoom == null ? "fit" : this._zoom.toFixed(3));
        } catch (e) {
          // storage blocked: the zoom still applies until the page reloads
        }
        this._render();
      })
    );
    root.querySelectorAll("[data-vmore]").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const key = b.dataset.vmore;
        if (this._vmore.has(key)) this._vmore.delete(key);
        else this._vmore.add(key);
        this._render();
      })
    );
    root.querySelectorAll("[data-vlan]").forEach((b) =>
      b.addEventListener("click", () => {
        const id = b.dataset.vlan;
        this._vlan = !id || String(this._vlan) === id ? null : id;
        this._render();
      })
    );
    root.querySelector("select.site")?.addEventListener("change", (ev) => {
      this._siteIdx = Number(ev.target.value);
      this._selected = null;
      this._render();
    });
  }
}

const STYLE = `
  :host { display:block; }
  ha-card { overflow:hidden; position:relative; }
  :host([fill]) { height:100%; }
  :host([fill]) ha-card { height:100%; display:flex; flex-direction:column; border-radius:0; border:none; box-shadow:none; }
  :host([fill]) .viewport { flex:1 1 auto; min-height:0; }
  .header { display:flex; align-items:center; gap:12px; padding:12px 16px; flex-wrap:wrap;
    border-bottom:1px solid var(--divider-color); }
  .title { font-size:1.1em; font-weight:500; }
  .sitename, select.site { color:var(--secondary-text-color); font:inherit; }
  select.site { background:transparent; border:1px solid var(--divider-color); border-radius:6px; padding:2px 6px; color:var(--primary-text-color); }
  .stats { display:flex; gap:12px; margin-left:auto; color:var(--secondary-text-color); }
  .stat { display:flex; align-items:center; gap:4px; } .stat ha-icon { --mdc-icon-size:18px; }
  .zoom { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .viewwrap { position:relative; }
  .viewbtn { display:inline-flex; align-items:center; gap:4px; height:28px; padding:0 10px; border-radius:8px; cursor:pointer; font:inherit;
    font-size:12.5px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); }
  .viewbtn ha-icon { --mdc-icon-size:16px; }
  .viewbtn.on { border-color:var(--primary-color); color:var(--primary-color); }
  .viewmenu { position:absolute; right:0; top:calc(100% + 6px); z-index:6; width:290px; padding:10px 12px; border-radius:12px;
    background:var(--card-background-color); border:1px solid var(--divider-color); box-shadow:0 8px 24px rgba(0,0,0,.18);
    display:grid; gap:8px; color:var(--primary-text-color); font-size:13px; }
  .vrow { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .vlab { display:grid; font-weight:600; }
  .vlab small { font-weight:400; font-size:11px; color:var(--secondary-text-color); }
  .vctl { display:inline-flex; align-items:center; gap:6px; }
  .vctl b { min-width:42px; text-align:center; font-variant-numeric:tabular-nums; font-weight:600; }
  .vctl button { width:28px; height:28px; border-radius:7px; cursor:pointer; font:inherit; font-size:16px; line-height:1;
    border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); }
  .vctl button:hover, .viewbtn:hover { border-color:var(--primary-color); }
  .vfoot { display:grid; gap:6px; border-top:1px solid var(--divider-color); padding-top:8px; }
  .vfoot .act { margin-top:0; }
  .vfoot.backup { margin-top:8px; }
  .vbtns { display:flex; gap:6px; flex-wrap:wrap; }
  .vfoot .muted.err { color:var(--error-color,#db4437); }
  .zoom .grp { display:inline-flex; align-items:center; gap:2px; padding:2px 3px 2px 6px; border:1px solid var(--divider-color);
    border-radius:8px; color:var(--secondary-text-color); }
  .zoom .grp:first-child { padding-left:3px; }
  .zoom .grp ha-icon { --mdc-icon-size:16px; margin-right:2px; }
  .zoom .grp button { margin-left:0; border-color:transparent; }
  .zoom .grp button:hover { border-color:var(--divider-color); }
  .layout { display:flex; align-items:center; gap:4px; }
  .canvas.grid { background-image:radial-gradient(circle, color-mix(in srgb, var(--secondary-text-color) 45%, transparent) 1px, transparent 1.3px);
    background-size:var(--grid) var(--grid); background-position:0 0; }
  .layout .hint { font-size:12px; color:var(--secondary-text-color); margin-right:4px; }
  .layout button { border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color);
    border-radius:6px; height:26px; padding:0 10px; cursor:pointer; font:inherit; font-size:12px; }
  .layout button.on { background:var(--primary-color); border-color:var(--primary-color); color:var(--text-primary-color,#fff); }
  .node.draggable { cursor:grab; touch-action:none; user-select:none; outline:1px dashed color-mix(in srgb, var(--primary-color) 55%, transparent);
    outline-offset:2px; }
  .node.dragging { cursor:grabbing; z-index:5; box-shadow:0 6px 18px rgba(0,0,0,.18); background:var(--card-background-color); }
  .zoom .sep { width:8px; }
  .zoom .ctl { display:inline-flex; align-items:center; color:var(--secondary-text-color); margin-right:2px; }
  .zoom .ctl ha-icon { --mdc-icon-size:18px; }
  .zoom button { border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color);
    border-radius:6px; min-width:30px; height:26px; cursor:pointer; margin-left:2px; }
  .warn { background:var(--warning-color,#ffa600); color:#000; font-size:12px; padding:2px 16px; }
  .warn.stale { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:6px 16px; font-size:13px; }
  .reload { font:inherit; font-size:12px; font-weight:600; padding:4px 14px; border-radius:6px; cursor:pointer;
    border:1px solid rgba(0,0,0,.35); background:#fff; color:#000; }
  .reload:hover { background:#f2f2f2; }
  .reload:focus-visible { outline:2px solid #000; outline-offset:2px; }
  .reload[disabled] { opacity:.7; cursor:progress; }
  .viewport { overflow:auto; position:relative; background:var(--primary-background-color); }
  .empty { padding:24px; color:var(--secondary-text-color); }
  .canvas { position:relative; transform-origin:0 0; }
  .canvas > svg { position:absolute; left:0; top:0; overflow:visible; }
  .ring svg.mini { position:relative; }
  .edge { fill:none; stroke:var(--secondary-text-color); stroke-width:1.2; opacity:.7; }
  .edge.down { stroke:var(--error-color,#db4437); opacity:1; }
  .edge.dashed { stroke-dasharray:5 4; }
  .elabel { position:absolute; z-index:1; display:flex; align-items:center; gap:3px; white-space:nowrap; height:calc(17px * var(--fs, 1));
    width:max-content; max-width:none; box-sizing:border-box; /* never shrink to the space left on the right */
    font-size:calc(10.5px * var(--fs, 1)); color:var(--secondary-text-color); background:var(--card-background-color);
    border:1px solid var(--divider-color); padding:0 4px; border-radius:5px; pointer-events:auto; }
  .elabel b { font-weight:600; color:var(--primary-text-color); }
  .elabel.dim { opacity:.25; }
  .elabel i.sw { display:inline-block; width:8px; height:3px; border-radius:2px; }
  .vc { display:inline-block; font-size:calc(9.5px * var(--fs, 1)); line-height:calc(13px * var(--fs, 1)); padding:0 4px; border-radius:7px; font-variant-numeric:tabular-nums;
    border:1px solid var(--c); color:var(--c); background:transparent; cursor:default; }
  .vc.u { background:var(--c); color:#fff; }
  .vc.on { outline:2px solid var(--c); outline-offset:1px; }
  .vc.more { border-color:var(--divider-color); color:var(--secondary-text-color); }
  button.vc.more { font:inherit; font-size:calc(9.5px * var(--fs, 1)); line-height:calc(13px * var(--fs, 1)); cursor:pointer; background:var(--card-background-color); }
  button.vc.more:hover { border-color:var(--primary-color); color:var(--primary-color); }
  button.vc.more:focus-visible { outline:2px solid var(--primary-color); outline-offset:1px; }
  .elabel.open { z-index:3; }
  .edge.member { stroke:var(--c); opacity:.85; stroke-width:1.6; }
  .edge.hl { stroke:var(--c) !important; stroke-width:3.2 !important; opacity:1 !important; }
  .edge.dim { opacity:.15 !important; }
  .node.dim { opacity:.3; }
  .node.vmatch .ring { box-shadow:0 0 0 3px color-mix(in srgb, var(--primary-color) 35%, transparent); }
  .vlans { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:8px 16px; border-bottom:1px solid var(--divider-color); }
  .vlabel { font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--secondary-text-color); margin-right:2px; }
  .vchip { display:inline-flex; align-items:center; gap:5px; font:inherit; font-size:11.5px; padding:3px 8px 3px 6px; border-radius:12px;
    cursor:pointer; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color);
    font-variant-numeric:tabular-nums; }
  .vchip i { width:9px; height:9px; border-radius:50%; background:var(--c); }
  .vchip span { color:var(--secondary-text-color); max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .vchip.on { border-color:var(--c); box-shadow:0 0 0 1px var(--c) inset; }
  .vchip.down { opacity:.55; }
  .vchip.clear { padding:3px 10px; }
  .vchip:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; }
  .port.invlan .jack { stroke:var(--c); stroke-width:1.8; }
  .group { position:absolute; border-radius:12px; background:var(--card-background-color);
    border:1px solid var(--divider-color); box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .glabel { position:absolute; z-index:1; top:11px; left:14px; max-width:calc(100% - 28px); display:flex; align-items:center; gap:5px;
    font-size:calc(12px * var(--fs, 1)); line-height:calc(16px * var(--fs, 1)); color:var(--primary-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    background:var(--card-background-color); padding-right:4px; /* sits above connector lines entering the box */ }
  .glabel ha-icon { --mdc-icon-size:16px; }
  .band { position:absolute; font-size:calc(11px * var(--fs, 1)); font-weight:600; letter-spacing:.03em; text-transform:uppercase;
    color:var(--secondary-text-color); white-space:nowrap; line-height:1.2; }
  .band span { font-weight:400; }
  .bandsep { position:absolute; width:0; border-left:2px dotted color-mix(in srgb, var(--secondary-text-color) 55%, transparent); }
  .node { position:absolute; z-index:1; width:${CHIP_W}px; height:${CHIP_H}px; display:flex; align-items:center; gap:10px;
    box-sizing:border-box; padding:4px 6px; border-radius:28px; cursor:pointer; }
  .node:hover, .node.selected { background:var(--secondary-background-color); }
  .node.internet, .node.gateway, .node.switch, .node.ap, .node.bridge, .node.endpoint { }
  .ring { position:relative; flex:0 0 auto; width:42px; height:42px; border-radius:50%; display:flex; align-items:center;
    justify-content:center; border:2px solid var(--disabled-text-color,#999); background:var(--card-background-color);
    color:var(--primary-text-color); }
  .ring.online { border-color:var(--success-color,#43a047); }
  .ring.offline { border-color:var(--error-color,#db4437); }
  .ring ha-icon { --mdc-icon-size:22px; }
  .ring svg.mini { width:30px; height:30px; }
  .node.client .ring { width:var(--rs); height:var(--rs); }
  .node.client .ring ha-icon { --mdc-icon-size:calc(var(--rs) * 0.52); }
  .node.client .ring img.logo { width:calc(var(--rs) * 0.38); height:calc(var(--rs) * 0.38); right:-5px; bottom:-5px; }
  .node.client .new { font-size:calc(var(--rs) * 0.2); }
  .ring.g-switch { border-radius:10px; width:46px; }
  .ring.g-switch svg.mini { width:40px; height:20px; }
  .sw-body { fill:var(--ovc-device-body,#2f363d); stroke:var(--ovc-device-edge,#1b2025); stroke-width:1; }
  .sw-ear { fill:var(--ovc-device-edge,#1b2025); }
  .sw-model { fill:#e8edf1; font:600 5.6px var(--ovc-font-data, ui-monospace, monospace); }
  .sw-poe { fill:#9fb0bd; font:500 5px var(--ovc-font-data, ui-monospace, monospace); }
  .jack { fill:#12161a; stroke:#4b555e; stroke-width:.6; }
  .jack.sfp, .jack.qsfp { fill:#0c0f12; stroke:#7b8791; }
  .port.linked .jack { stroke:var(--primary-color); stroke-width:1.1; }
  .port:hover .jack { stroke:#ffffff; }
  .led { fill:#59626b; }
  .led.up { fill:var(--success-color,#43a047); }
  .led.down { fill:#3d454c; }
  .led.off { fill:var(--warning-color,#ffa600); }
  .led.unknown { fill:#59626b; opacity:.5; }
  .led-dot { fill:var(--success-color,#43a047); }
  .ap-shell { fill:var(--ovc-device-plastic,#f4f6f8); stroke:var(--ovc-device-plastic-edge,#b9c2ca); stroke-width:1.5; }
  .ap-ring { fill:none; stroke:var(--ovc-device-plastic-edge,#b9c2ca); stroke-width:1; }
  .ap-ant { fill:var(--ovc-device-plastic-edge,#b9c2ca); }
  .ap-fin { stroke:var(--ovc-device-plastic-edge,#b9c2ca); stroke-width:1; }
  .ap-model { fill:var(--secondary-text-color); font:500 6px var(--ovc-font-data, ui-monospace, monospace); text-anchor:middle; }
  .led-status { fill:var(--disabled-text-color,#999); }
  .led-status.online { fill:var(--success-color,#43a047); }
  .led-status.offline { fill:var(--error-color,#db4437); }
  .pgraphic { margin:4px 0 8px; display:block; min-width:0; text-align:center; }
  .pgraphic svg { display:block; margin:0 auto; max-width:100%; }
  .ring img.photo-mini { width:34px; height:34px; object-fit:contain; }
  .ring.g-switch img.photo-mini { width:42px; height:30px; }
  .ring.g-ap { background:var(--card-background-color); }
  .pgraphic img.photo { display:block; margin:0 auto; max-width:100%; max-height:170px; object-fit:contain; }
  .pgraphic.ap img.photo { max-height:150px; }
  .sublabel { font-size:11px; color:var(--secondary-text-color); text-align:left; margin:8px 0 4px; letter-spacing:.03em; text-transform:uppercase; }
  .pgraphic svg.dev.switch { width:100%; height:auto; }
  .pgraphic svg.dev.ap { width:120px; height:auto; }
  .pgraphic svg.dev.gw { width:160px; height:auto; }
  .legend { display:flex; flex-wrap:wrap; gap:10px; font-size:11px; color:var(--secondary-text-color); margin-bottom:8px; }
  .legend span { display:inline-flex; align-items:center; gap:4px; }
  .legend i { display:inline-block; width:10px; height:5px; border-radius:2px; }
  .legend i.led.up { background:var(--success-color,#43a047); }
  .legend i.led.down { background:#3d454c; }
  .legend i.led.off { background:var(--warning-color,#ffa600); }
  .legend i.swatch.linked { width:10px; height:8px; border:1.5px solid var(--primary-color); background:#12161a; }
  table.ports { margin-bottom:10px; }
  table.ports td:first-child { font-family:var(--ovc-font-data, ui-monospace, monospace); }
  .ring.hasphoto { background:#fff; }
  .ring img.cphoto { width:70%; height:70%; object-fit:contain; display:block; }
  .pgraphic.photo img.photo { max-height:200px; border-radius:8px; }
  .photorow { margin-top:8px; }
  .muted.err { color:var(--error-color,#db4437); }
  .ring img.logo { position:absolute; right:-5px; bottom:-5px; width:16px; height:16px; object-fit:contain; padding:1.5px;
    border-radius:50%; background:#fff; border:1px solid var(--divider-color); box-sizing:border-box; }
  .new { position:absolute; left:-8px; top:-7px; background:var(--warning-color,#ffa600); color:#000; font-size:8.5px; font-weight:700;
    letter-spacing:.04em; padding:1px 4px; border-radius:6px; }
  .new.inline { position:static; margin-right:6px; }
  .newstat { color:var(--warning-color,#ffa600); font-weight:600; }
  .section { margin-top:10px; }
  .role { font-size:12px; font-weight:600; margin-bottom:4px; }
  .muted { color:var(--secondary-text-color); font-size:11px; }
  table.svc td:first-child { font-family:var(--ovc-font-data, ui-monospace, monospace); }
  table.svc a { color:var(--primary-color); }
  .act { margin-top:8px; font:inherit; font-size:12px; padding:5px 10px; border-radius:6px; cursor:pointer;
    border:1px solid var(--primary-color); background:var(--primary-color); color:var(--text-primary-color,#fff); }
  .act.ghost { background:transparent; color:var(--primary-color); }
  .act[disabled] { opacity:.6; cursor:progress; }
  form.edit { margin-top:10px; display:grid; gap:6px; font-size:12px; }
  .row { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .linkbtn { display:inline-block; margin-top:2px; border:none; background:none; padding:0; font:inherit; font-size:11px;
    color:var(--primary-color); cursor:pointer; text-decoration:underline; }
  .tag { display:inline-block; font-size:10px; padding:0 5px; border-radius:6px; border:1px solid var(--divider-color);
    color:var(--secondary-text-color); margin-left:4px; }
  .tag.unk { border-color:var(--warning-color,#ffa600); color:var(--warning-color,#ffa600); background:none; padding:0 5px; }
  tr.unk td:first-child { color:var(--warning-color,#ffa600); font-weight:600; }
  form.portname { display:grid; gap:5px; margin-top:6px; }
  form.portname input[type=text], form.portname input:not([type]) { font:inherit; padding:4px 6px; border-radius:6px;
    border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); }
  .chk { display:flex; gap:6px; align-items:center; font-size:11px; color:var(--secondary-text-color); }
  .formerr { color:var(--error-color,#db4437); font-size:12px; }
  .act.danger { border-color:var(--error-color,#db4437); color:var(--error-color,#db4437); }
  form.edit label { display:grid; gap:2px; color:var(--secondary-text-color); }
  form.edit input, form.edit select { font:inherit; padding:5px 6px; border-radius:6px; border:1px solid var(--divider-color);
    background:var(--card-background-color); color:var(--primary-text-color); }
  form.edit .row { display:flex; flex-wrap:wrap; gap:6px; }
  .node.big { border-radius:999px; gap:14px; }
  /* Square device icons get a rounded-rectangle highlight whose corners follow the icon (radius + padding). */
  .node.square { border-radius:calc(var(--rs) * 0.24 + 7px); }
  .node.square:not(.big) { border-radius:16px; }
  .node.big .ring { width:var(--rs); height:var(--rs); border-width:3px; }
  .node.big .ring ha-icon { --mdc-icon-size:calc(var(--rs) * 0.52); }
  .node.big .ring svg.mini { width:calc(var(--rs) * 0.72); height:calc(var(--rs) * 0.72); }
  .node.big .ring img.photo-mini { width:calc(var(--rs) * 0.8); height:calc(var(--rs) * 0.8); }
  .node.big .ring.g-switch { width:calc(var(--rs) * 1.1); border-radius:calc(var(--rs) * 0.24); }
  .node.big .ring.g-switch svg.mini { width:calc(var(--rs) * 0.95); height:calc(var(--rs) * 0.48); }
  .node.big .ring.g-switch img.photo-mini { width:calc(var(--rs) * 1); height:calc(var(--rs) * 0.7); }
  .node.big .badge { font-size:calc(var(--bs) * 0.5); min-width:var(--bs); height:var(--bs); line-height:var(--bs);
    border-radius:calc(var(--bs) / 2); right:calc(var(--bs) * -0.3); top:calc(var(--bs) * -0.3);
    padding:0 calc(var(--bs) * 0.18); font-weight:600; box-shadow:0 1px 3px rgba(0,0,0,.25); }
  .node.big .ring img.logo { width:calc(var(--bs) * 0.8); height:calc(var(--bs) * 0.8); right:-6px; bottom:-6px; }
  .node.big .new { font-size:11px; padding:2px 6px; left:-10px; top:-9px; }
  .badge { position:absolute; right:-6px; top:-6px; background:var(--primary-color); color:var(--text-primary-color,#fff);
    font-size:10px; min-width:16px; height:16px; line-height:16px; border-radius:8px; text-align:center; padding:0 3px; }
  .txt { min-width:0; flex:1; }
  .name { font-size:calc(13px * var(--fs, 1)); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:var(--primary-text-color); }
  .sub { font-size:calc(11px * var(--fs, 1)); color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .toggle { flex:0 0 auto; width:20px; height:20px; padding:0; border:1px solid var(--secondary-text-color); border-radius:4px;
    background:var(--card-background-color); color:var(--primary-text-color); cursor:pointer; line-height:16px; }
  .panel.wide { width:460px; }
  .panel.xwide { width:740px; }
  .elabel .peer { color:var(--primary-text-color); font-weight:500; }
  .elabel .rate { font-variant-numeric:tabular-nums; color:var(--secondary-text-color); }
  .rate.warn, td.num.warn { color:var(--warning-color,#ffa600); font-weight:600; }
  .rate.high, td.num.high { color:var(--error-color,#db4437); font-weight:600; }
  .rate.down { color:var(--error-color,#db4437); }
  .alertdot { position:absolute; left:-4px; top:-4px; min-width:16px; height:16px; line-height:16px; border-radius:8px; text-align:center;
    font-size:11px; font-weight:700; color:#fff; background:var(--warning-color,#ffa600); box-shadow:0 0 0 2px var(--card-background-color); }
  .alertdot.error { background:var(--error-color,#db4437); }
  .node.big .alertdot { min-width:calc(var(--bs) * 0.9); height:calc(var(--bs) * 0.9); line-height:calc(var(--bs) * 0.9);
    border-radius:calc(var(--bs) * 0.45); font-size:calc(var(--bs) * 0.55); left:2px; top:2px; }
  .ualerts { display:grid; gap:4px; margin:4px 0 8px; }
  .ualert { display:flex; align-items:center; gap:6px; font-size:12px; padding:5px 8px; border-radius:6px;
    background:color-mix(in srgb, var(--warning-color,#ffa600) 15%, transparent); }
  .ualert.error { background:color-mix(in srgb, var(--error-color,#db4437) 15%, transparent); }
  .ualert.info { background:color-mix(in srgb, var(--primary-color) 12%, transparent); }
  .ualert ha-icon { --mdc-icon-size:16px; }
  table.uports th { text-align:left; font-weight:600; font-size:11px; color:var(--secondary-text-color); padding:2px 4px; }
  table.uports td { padding:3px 4px; }
  table.uports td.num, table.uports th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  table.uports tr.off td { color:var(--secondary-text-color); }
  .jack.wan { stroke:var(--primary-color); stroke-width:1.4; }
  .sw-title { fill:#e8edf1; font:700 11px var(--ovc-font-data, ui-monospace, monospace); }
  .sw-poe2 { fill:#9fb0bd; font-weight:600; }
  .sw-pn { fill:#c3ced6; font:700 10px var(--ovc-font-data, ui-monospace, monospace); text-anchor:middle; }
  .sw-pn.down, .sw-pn.unknown { fill:#7d8992; }
  .sw-pn.off { fill:#c9a24a; }
  .gw-model { fill:#e8edf1; font:700 12px var(--ovc-font-data, ui-monospace, monospace); }
  .gw-pn { fill:#e8edf1; font:600 13px var(--ovc-font-data, ui-monospace, monospace); text-anchor:middle; }
  .gw-pl { fill:#c3ced6; font:700 12px var(--ovc-font-data, ui-monospace, monospace); text-anchor:middle; }
  .gw-ps { fill:#7fd19a; font:700 12px var(--ovc-font-data, ui-monospace, monospace); text-anchor:middle; }
  .gw-ps.off { fill:#8a96a0; }
  .pgraphic svg.dev.gw.ports { width:100%; height:auto; }
  :host { --ovc-dn:#2a78d6; --ovc-up:#eb6834; --ovc-grid:color-mix(in srgb, var(--primary-text-color) 9%, transparent); }
  :host([dark]) { --ovc-dn:#3987e5; --ovc-up:#d95926; }
  .uchead { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:2px 0 4px; }
  .uchead .muted { font-size:11px; }
  .rchips { margin-left:auto; display:inline-flex; gap:4px; }
  .rchip { font:inherit; font-size:11px; padding:2px 8px; border-radius:10px; cursor:pointer; border:1px solid var(--divider-color);
    background:var(--card-background-color); color:var(--primary-text-color); }
  .rchip.on { border-color:var(--primary-color); color:var(--primary-color); font-weight:600; }
  .ulegend { display:flex; gap:14px; flex-wrap:wrap; font-size:11.5px; color:var(--secondary-text-color); margin-bottom:2px; }
  .ulegend b { color:var(--primary-text-color); font-weight:600; font-variant-numeric:tabular-nums; margin-left:2px; }
  i.key { display:inline-block; width:12px; height:2px; border-radius:1px; vertical-align:middle; margin-right:5px; }
  i.key.dn { background:var(--ovc-dn); } i.key.up { background:var(--ovc-up); }
  .uchartwrap { position:relative; }
  svg.uchart { display:block; width:100%; height:auto; touch-action:none; }
  svg.uchart .grid { stroke:var(--ovc-grid); stroke-width:1; }
  svg.uchart .ytick { fill:var(--secondary-text-color); font-size:9.5px; text-anchor:end; font-variant-numeric:tabular-nums; }
  svg.uchart .xtick { fill:var(--secondary-text-color); font-size:9.5px; text-anchor:middle; }
  svg.uchart .yunit { fill:var(--secondary-text-color); font-size:9.5px; text-anchor:start; }
  svg.uchart .area.dn { fill:var(--ovc-dn); opacity:.12; }
  svg.uchart .line { fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
  svg.uchart .line.dn { stroke:var(--ovc-dn); } svg.uchart .line.up { stroke:var(--ovc-up); }
  svg.uchart .peak { fill:var(--ovc-dn); stroke:var(--card-background-color); stroke-width:2; }
  svg.uchart .peaklabel { fill:var(--primary-text-color); font-size:10px; font-weight:600; paint-order:stroke; stroke:var(--card-background-color); stroke-width:3px; }
  svg.uchart .cross { stroke:var(--secondary-text-color); stroke-width:1; }
  svg.uchart .hdot { stroke:var(--card-background-color); stroke-width:2; } svg.uchart .hdot.dn { fill:var(--ovc-dn); } svg.uchart .hdot.up { fill:var(--ovc-up); }
  svg.uchart .hit { fill:transparent; cursor:crosshair; }
  .utip { position:absolute; top:4px; display:grid; gap:2px; padding:5px 8px; border-radius:6px; font-size:11px; pointer-events:none;
    background:var(--card-background-color); border:1px solid var(--divider-color); box-shadow:0 2px 6px rgba(0,0,0,.12); white-space:nowrap; }
  .utip[hidden] { display:none; }
  .uchart-empty { font-size:12px; color:var(--secondary-text-color); padding:24px 4px; text-align:center; border:1px dashed var(--divider-color); border-radius:8px; }
  svg.spark { display:block; } svg.spark .spark-line { fill:none; stroke:var(--ovc-dn); stroke-width:1.4; stroke-linejoin:round; }
  svg.spark .spark-area { fill:var(--ovc-dn); opacity:.14; } svg.spark .spark-base { stroke:var(--ovc-grid); }
  .lspark { display:inline-block; vertical-align:middle; margin-left:4px; }
  table.uports tr[data-rport] { cursor:pointer; }
  table.uports tr.sel td { background:color-mix(in srgb, var(--primary-color) 9%, transparent); }
  table.uports td.trend { padding:2px 4px; width:48px; }
  table.uports td:nth-child(3), table.uports td:nth-child(1) { white-space:nowrap; }
  .vsec { font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; color:var(--secondary-text-color); margin:6px 0 -2px; }
  .vrow.vstack { flex-direction:column; align-items:stretch; gap:6px; }
  .vchips { display:flex; flex-wrap:wrap; gap:4px; }
  .rchip[disabled] { opacity:.4; cursor:not-allowed; }
  .vtoggle { cursor:pointer; } .vtoggle input { width:18px; height:18px; accent-color:var(--primary-color); }
  .panel { overflow-anchor:none; position:absolute; right:12px; top:58px; width:320px; max-width:calc(100% - 24px); max-height:70%; overflow:auto;
    background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px;
    box-shadow:0 4px 16px rgba(0,0,0,.2); padding:10px 12px; z-index:2; }
  .ptitle { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .ptitle b { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .close { border:none; background:none; font-size:20px; cursor:pointer; color:var(--secondary-text-color); }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  td { padding:3px 4px; border-top:1px solid var(--divider-color); vertical-align:top; word-break:break-word; }
  td:first-child { color:var(--secondary-text-color); width:34%; max-width:150px; overflow-wrap:anywhere; word-break:normal; }
  td + td { overflow-wrap:anywhere; word-break:normal; }
`;

// ---------- sidebar panel: the map full screen in the main pane ----------
class OvCirrusNetworkPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    this._build();
    if (this._card) this._card.hass = hass;
    if (this._menu) this._menu.hass = hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
    if (this._titleEl) this._titleEl.textContent = panel?.config?.title || "Network";
  }

  set route(_route) {}

  _build() {
    if (this._card || !this._hass) return;
    this.shadowRoot.innerHTML = `<style>
      :host { display:flex; flex-direction:column; height:100%; background:var(--primary-background-color); }
      .toolbar { display:flex; align-items:center; gap:4px; height:var(--header-height, 56px); padding:0 12px 0 4px;
        box-sizing:border-box; flex:0 0 auto; background:var(--app-header-background-color, var(--primary-color));
        color:var(--app-header-text-color, var(--text-primary-color, #fff));
        border-bottom:var(--app-header-border-bottom, none); font-size:20px; font-weight:400; }
      .title { margin-left:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .body { flex:1 1 auto; min-height:0; }
      ovcirrus-network-map { height:100%; }
    </style>
    <div class="toolbar"><ha-menu-button></ha-menu-button><div class="title"></div></div>
    <div class="body"></div>`;
    this._menu = this.shadowRoot.querySelector("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    this._titleEl = this.shadowRoot.querySelector(".title");
    this._titleEl.textContent = this._panel?.config?.title || "Network";
    this._card = document.createElement("ovcirrus-network-map");
    this._card.setConfig({ ...(this._panel?.config?.card || {}), height: "fill", title: false });
    this.shadowRoot.querySelector(".body").appendChild(this._card);
    this._card.hass = this._hass;
  }
}

if (!customElements.get("ovcirrus-network-panel")) {
  customElements.define("ovcirrus-network-panel", OvCirrusNetworkPanel);
}

console.info(`%c OVCIRRUS-NETWORK-MAP %c v${CARD_VERSION} `, "background:#0e6f8f;color:#fff;border-radius:3px 0 0 3px", "background:#ddd;color:#000;border-radius:0 3px 3px 0");

if (!customElements.get("ovcirrus-network-map")) {
  customElements.define("ovcirrus-network-map", OvCirrusNetworkMap);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "ovcirrus-network-map",
    name: "OmniVista Cirrus network map",
    description: "Auto-drawn network topology from Cirrus LLDP, clients and switch MAC tables.",
    preview: false,
  });
}
