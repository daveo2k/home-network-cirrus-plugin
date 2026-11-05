/*
 * OmniVista Cirrus network map card for Home Assistant.
 * type: custom:ovcirrus-network-map
 * Options: entry_id, site (id or name), refresh (s, default 30), expand_clients (bool),
 *          max_columns (default 3), height (px, default 640), title,
 *          font_scale (map text size multiplier, default 1; viewers can change it with A− / A+),
 *          layout_editing ("admin" default, true, or false: who may drag devices and save the layout),
 *          snap_to_grid (default true), grid_size (px, default 20),
 *          height: "fill" makes the map fill its container (used by the sidebar panel),
 *          device_scale (size of gateways/switches/APs/wired devices, default 2; wireless clients stay 1)
 */
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
let FS = 1; // text size multiplier for the map
const FS_MIN = 0.7, FS_MAX = 1.8, FS_KEY = "ovcirrus-font-scale";
function applyFontScale(fs) {
  FS = fs;
  const up = Math.max(1, fs);
  CHIP_H = Math.round(BASE.CHIP_H * (0.55 + 0.45 * up));
  LABEL_H = Math.round(BASE.LABEL_H * up);
  LABEL_ROW = Math.round(BASE.LABEL_ROW * Math.max(0.85, fs));
}
const BUNDLE_STEP = 16; // spacing between parallel link lines

// Devices drawn at device_scale (default 2x); wireless clients and the Internet node stay normal size.
const BIG_KINDS = new Set(["gateway", "switch", "ap", "bridge", "endpoint", "manual"]);
let DEVICE_SCALE = 2;
const ringSize = (n) => (BIG_KINDS.has(n.kind) ? Math.round(42 * DEVICE_SCALE) : 42);
const chipH = (n) => (BIG_KINDS.has(n.kind) ? Math.max(CHIP_H, ringSize(n) + 14) : CHIP_H);

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
  const PW = 10, PH = 8, GX = 2, BLOCK = 6, LEFT = 62;
  const cols = Math.ceil(g.copper / 2);
  const blockGap = (c) => Math.floor(c / BLOCK) * 5;
  const copperW = cols ? cols * (PW + GX) + blockGap(cols - 1) : 0;
  const sfpCols = Math.ceil(g.sfp / 2);
  const stackCols = Math.ceil(g.stack / 2);
  const sfpX = LEFT + copperW + 10;
  const stackX = sfpX + sfpCols * 17 + (sfpCols ? 8 : 0);
  const W = stackX + stackCols * 21 + 8;
  const H = 46;
  const rowY = [13, 27];
  const parts = [];
  const port = (n, x, row, w, kind) => {
    const { cls, p } = portState(g, n);
    const y = rowY[row];
    const ledY = row === 0 ? y - 4 : y + PH + 2;
    const vl = p ? [p.u?.length ? `untagged ${p.u.join(", ")}` : "", p.t?.length ? `tagged ${p.t.join(", ")}` : ""].filter(Boolean).join(" · ") : "";
    const tip = `1/1/${n} · ${p ? (p.admin ? (p.up ? "up" : "down") : "disabled") : "no data"}${p?.label ? " · " + p.label : ""}${vl ? " · VLAN " + vl : ""}`;
    const inVlan = p && VLAN_SELECTED !== null && carries({ untagged: p.u, tagged: p.t }, VLAN_SELECTED);
    parts.push(`<g class="port ${p?.label ? "linked" : ""} ${inVlan ? "invlan" : ""}" ${inVlan ? `style="--c:${vlanColor(VLAN_SELECTED)}"` : ""}><title>${esc(tip)}</title>
      <rect class="jack ${kind}" x="${x}" y="${y}" width="${w}" height="${PH}" rx="1"/>
      <rect class="led ${cls}" x="${x + w / 2 - 2}" y="${ledY}" width="4" height="2" rx="1"/></g>`);
  };
  for (let n = 1; n <= g.copper; n++) {
    const c = Math.floor((n - 1) / 2);
    port(n, LEFT + c * (PW + GX) + blockGap(c), (n - 1) % 2, PW, "rj45");
  }
  for (let k = 0; k < g.sfp; k++) port(g.copper + 1 + k, sfpX + Math.floor(k / 2) * 17, k % 2, 14, "sfp");
  for (let k = 0; k < g.stack; k++) port(g.copper + g.sfp + 1 + k, stackX + Math.floor(k / 2) * 21, k % 2, 18, "qsfp");
  return `<svg class="dev switch" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(g.model || "Switch")} front panel">
    <rect class="sw-body" x="0.5" y="0.5" width="${W - 1}" height="${H - 1}" rx="3"/>
    <rect class="sw-ear" x="0.5" y="0.5" width="6" height="${H - 1}" rx="2"/>
    <rect class="sw-ear" x="${W - 6.5}" y="0.5" width="6" height="${H - 1}" rx="2"/>
    <text class="sw-model" x="11" y="15">${esc((g.model || "").replace(/^OS/, "OS "))}</text>
    ${g.poe ? `<text class="sw-poe" x="11" y="25">PoE+</text>` : ""}
    <rect class="jack rj45" x="11" y="31" width="9" height="7" rx="1"/>
    <circle class="led-dot" cx="27" cy="34.5" r="2"/>
    <circle class="led-dot" cx="34" cy="34.5" r="2"/>
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

function gatewaySvg(status, mini) {
  return `<svg class="${mini ? "mini" : "dev gw"}" viewBox="0 0 100 60" role="img" aria-label="Gateway">
    <rect class="sw-body" x="4" y="14" width="92" height="32" rx="6"/>
    ${[0, 1, 2, 3].map((i) => `<rect class="jack rj45" x="${46 + i * 11}" y="25" width="8" height="9" rx="1"/>`).join("")}
    <circle class="led-status ${esc(status)}" cx="18" cy="30" r="${mini ? 6 : 3.5}"/></svg>`;
}

function deviceGraphic(n, mini) {
  const g = n.graphic;
  if (!g) return "";
  if (g.image) {
    const img = `<img class="${mini ? "photo-mini" : "photo"}" src="${esc(g.image)}" alt="${esc(g.model || n.name)}" loading="lazy">`;
    if (mini) return img;
    // Photo of the hardware; switches also keep the live port-LED panel underneath.
    return g.type === "switch" ? `${img}<div class="sublabel">Live port status</div>${switchSvg(g)}` : img;
  }
  if (g.type === "switch") return mini ? switchMini(g) : switchSvg(g);
  if (g.type === "ap") return apSvg(g, n.status, mini);
  if (g.type === "gateway") return gatewaySvg(n.status, mini);
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
    this._selected = null;
    this._siteIdx = 0;
  }

  setConfig(config) {
    this._config = {
      refresh: 30, max_columns: 3, height: 640, expand_clients: false, font_scale: 1,
      layout_editing: "admin", snap_to_grid: true, grid_size: 20, device_scale: 2, ...config,
    };
    let snap = null;
    try {
      snap = localStorage.getItem(SNAP_KEY);
    } catch (e) {
      snap = null;
    }
    this._snap = snap === null ? this._config.snap_to_grid !== false : snap === "1";
    this._grid = Math.max(5, Math.min(100, Number(this._config.grid_size) || 20));
    this._applyFill();
    let stored = null;
    try {
      stored = Number(localStorage.getItem(FS_KEY)) || null;
    } catch (e) {
      stored = null; // storage blocked: fall back to the card setting
    }
    this._fs = Math.min(FS_MAX, Math.max(FS_MIN, stored || Number(this._config.font_scale) || 1));
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
  }

  connectedCallback() {
    if (!this._ro && window.ResizeObserver) {
      this._ro = new ResizeObserver(() => {
        const w = Math.round(this.getBoundingClientRect().width);
        if (w && w !== this._lastW) {
          this._lastW = w;
          if (this._zoom === null && this._sites) this._render();
        }
      });
      this._ro.observe(this);
    }
    if (this._hass && !this._timer) {
      this._fetch();
      this._timer = setInterval(() => this._fetch(), Math.max(10, this._config.refresh) * 1000);
    }
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      const msg = { type: "ovcirrus/topology" };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      const res = await this._hass.callWS(msg);
      this._sites = (res.entries || []).flatMap((e) => e.sites.map((s) => ({ ...s, entry: e.title, entry_id: s.entry_id || e.entry_id })));
      this._isAdmin = !!res.is_admin;
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
            this._chipsWidth(m, 4, `${c.id}:${i}`)
        )
      );
      return { right: offset + widest, rows: members.length };
    }
    if (!link.label && !members.length) return { right: 0, rows: 0 };
    const w = 8 + 2 + this._textWidth(`${link.label || ""} `, "10.5px") + (members[0] ? this._chipsWidth(members[0], 3, `${c.id}:0`) : 0);
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
    const max = MAX_CHIP_W * Math.max(1, FS) + (big ? outer - 46 : 0);
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
      g._cols = Math.min(this._config.max_columns, n);
      g._rows = Math.ceil(n / g._cols);
      // Columns share the width of the widest client in the box so the grid stays aligned.
      g._colW = Math.max(...g.children.map((c) => c._cw));
      g._w = g._cols * g._colW + (g._cols - 1) * GAP_X + 2 * PAD;
      g._h = LABEL_H + PAD + g._rows * CHIP_H + (g._rows - 1) * ROW_GAP + PAD;
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
        this._placeLeaves(g, g._y + LABEL_H + PAD, out);
        this._wrap(g, LABEL_H + PAD);
        out.groups.push(g);
        out.trunks.push({ node, g });
        gy += g._h + 34;
      }
      return;
    }
    let x = cx - node._gw / 2;
    const gy = y + node._ch + V_GAP;
    for (const g of node._groups) {
      const above = LABEL_H + PAD + (g.leaf ? 0 : g._rowsH || 0);
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
          });
          cxs += c._slot + GAP_X;
        }
        this._wrap(g, above);
      }
      x += g._w + GAP_X;
    }
  }

  _placeLeaves(g, chipY, out) {
    g.children.forEach((c, i) => {
      const col = i % g._cols;
      const row = Math.floor(i / g._cols);
      const [dx, dy] = this._off(c.id); // wired clients can be moved on their own
      c._x = g._x + PAD + col * (g._colW + GAP_X) + dx;
      c._rw = g._colW;
      c._y = chipY + row * (CHIP_H + ROW_GAP) + dy;
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
      out.groups.forEach((g) => ((g._bx += sx), (g._x += sx), (g._y += sy)));
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

    let body = "";
    let W = 0;
    let H = 0;
    if (site) {
      const root = site.root;
      applyFontScale(this._fs || 1);
      VLAN_NAMES = Object.fromEntries(Object.entries(site.vlans || {}).map(([k, v]) => [k, v.name]));
      VLAN_COLORS = Object.fromEntries(Object.keys(site.vlans || {}).map((k, i) => [k, VLAN_PALETTE[i % VLAN_PALETTE.length]]));
      if (this._vlan != null && !(String(this._vlan) in (site.vlans || {}))) this._vlan = null;
      VLAN_SELECTED = this._vlan ?? null;
      const tests = speedInfo(this._hass, cfg, root.wan);
      this._speed = tests;
      if (tests.length) {
        root.subtitle = speedText(tests[0]);
        const gw = root.groups[0]?.children?.[0];
        if (gw) gw.link = { ...(gw.link || {}), label: speedText(tests[0]), status: root.status === "offline" ? "down" : "up" };
      }
      this._index = new Map();
      const walk = (n) => {
        this._index.set(n.id, n);
        n.groups.forEach((g) => g.children.forEach(walk));
      };
      walk(root);
      DEVICE_SCALE = Math.min(3, Math.max(1, Number(cfg.device_scale) || 2));
      for (const n of this._index.values()) {
        n._ch = chipH(n);
        n._cw = this._chipWidth(n);
        n._rw = null;
      }
      VLAN_EXPANDED = this._vmore || (this._vmore = new Set());
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
            const n = members.length;
            const step = BUNDLE_STEP;
            return members
              .map((m, i) => {
                const dx = (i - (n - 1) / 2) * step;
                const color = sv !== null && carries(m, sv) ? vlanColor(sv) : vlanColor(m.untagged?.[0]);
                labels.push({
                  gid: e.gid,
                  x: x2 + ((n - 1) / 2) * step + 12,
                  y: y2 - LABEL_ROW * (n - i) - 4,
                  html: `<i class="sw" style="background:${color}"></i><b>${esc(m.label)}</b>${m.speed ? ` ${esc(m.speed)}` : ""} ${vlanChips(m, 4, `${e.cid}:${i}`)}`,
                  open: VLAN_EXPANDED.has(`${e.cid}:${i}`),
                  dim: sv !== null && !carries(m, sv),
                });
                return `<path class="${lineCls(m.status, m)} member" style="--c:${color}" d="M${x1 + dx},${y1} C${x1 + dx},${my} ${x2 + dx},${my} ${x2 + dx},${y2}"/>`;
              })
              .join("");
          }
          const m = members?.[0];
          if (e.label || m) {
            labels.push({
              gid: e.gid,
              x: x2 + 8,
              y: y2 - LABEL_ROW - 3,
              html: `${esc(e.label)} ${m ? vlanChips(m, 3, `${e.cid}:0`) : ""}`,
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
            <div class="glabel"><ha-icon icon="${esc(g.icon)}"></ha-icon><span>${esc(g.label)}</span></div></div>`
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
            (n.is_new ? `<span class="new">NEW</span>` : "");
          const sel =
            (this._editLayout && DRAGGABLE.has(n.kind) ? " draggable" : "") +
            (this._drag?.id === n.id && this._drag.moved ? " dragging" : "") +
            (this._selected === n.id ? " selected" : "") +
            (sv !== null && n.vlan ? (String(n.vlan) === String(sv) ? " vmatch" : " dim") : "");
          const big = BIG_KINDS.has(n.kind) ? " big" : "";
          return `<div class="node ${esc(n.kind)}${big}${sel}" data-id="${esc(n.id)}" style="left:${n._x}px;top:${n._y}px;width:${n._rw || n._cw}px;height:${n._ch || CHIP_H}px">
              <div class="ring ${esc(n.status)} ${n.graphic ? "g-" + esc(n.graphic.type) : ""}"${big ? ` style="--rs:${ringSize(n)}px"` : ""}>${n.graphic ? deviceGraphic(n, true) : `<ha-icon icon="${esc(n.icon)}"></ha-icon>`}${badge}</div>
              <div class="txt"><div class="name" title="${esc(n.name)}">${esc(n.name)}</div>
              <div class="sub" title="${esc(n.subtitle)}">${esc(n.subtitle)}</div></div>${toggle}</div>`;
        })
        .join("");

      const vpWidth = prevVp?.clientWidth || this.getBoundingClientRect().width || 800;
      const zoom = this._drag ? this._drag.zoom : this._zoom ?? Math.min(1, Math.max(0.35, (vpWidth - 4) / W));
      this._fitZoom = zoom;
      this._lastZoom = zoom;
      body = `<div class="sizer" style="width:${W * zoom}px;height:${H * zoom}px">
          <div class="canvas${this._editLayout && this._snap ? " grid" : ""}" style="width:${W}px;height:${H}px;transform:scale(${zoom});--fs:${this._fs || 1};--grid:${this._grid}px">
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
    const warn = site?.ssh_errors?.length
      ? `<div class="warn" title="${esc(site.ssh_errors.join("\n"))}">MAC table read failed – see log</div>`
      : "";

    const sel = this._selected && this._index?.get(this._selected);
    const g = sel?.graphic;
    const linked = g?.type === "switch"
      ? Object.entries(g.ports || {}).filter(([, p]) => p.label).sort((a, b) => a[0] - b[0])
      : [];
    const graphicBlock = g
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
                  }</div><div class="muted">${
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
    const panel = sel
      ? `<div class="panel ${g?.type === "switch" ? "wide" : ""}"><div class="ptitle"><ha-icon icon="${esc(sel.icon)}"></ha-icon><b>${esc(sel.name)}</b>
           ${sel.is_new ? `<span class="new inline">NEW</span>` : ""}<button class="close" data-close>×</button></div>${graphicBlock}
           ${speedBlock}
           <table>${(sel.details || []).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>
           ${servicesBlock}${editBlock}${deviceActions}</div>`
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
          }<div class="zoom"><button data-font="-" title="Smaller map text" aria-label="Smaller map text">A−</button><button data-font="+" title="Larger map text" aria-label="Larger map text">A+</button><span class="sep"></span><button data-zoom="-" title="Zoom out">−</button><button data-zoom="fit" title="Fit to width">fit</button><button data-zoom="+" title="Zoom in">+</button></div>
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
      // Safety net: if a label still renders wider than estimated, grow its box to cover it.
      const boxes = new Map([...canvas.querySelectorAll(".group[data-gid]")].map((b) => [b.dataset.gid, b]));
      for (const l of canvas.querySelectorAll(".elabel[data-gid]")) {
        const box = boxes.get(l.dataset.gid);
        if (!box) continue;
        const need = l.offsetLeft + l.offsetWidth + PAD - box.offsetLeft;
        if (need > box.offsetWidth) box.style.width = `${need}px`;
      }
      // Link labels are HTML, so their width is only known after layout: grow the map to fit them.
      const right = Math.max(0, ...[...canvas.querySelectorAll(".elabel")].map((l) => l.offsetLeft + l.offsetWidth + 12));
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
    this._bind();
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
        try {
          await this._hass.callWS({ type: "ovcirrus/discover", entry_id: entryId, ip, mode });
        } catch (err) {
          console.warn("ovcirrus scan failed", err);
        }
        this._scanning = null;
        await this._fetch();
      })
    );
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
    root.querySelectorAll("[data-font]").forEach((b) =>
      b.addEventListener("click", () => {
        const step = b.dataset.font === "+" ? 0.1 : -0.1;
        this._fs = Math.round(Math.min(FS_MAX, Math.max(FS_MIN, (this._fs || 1) + step)) * 10) / 10;
        try {
          localStorage.setItem(FS_KEY, String(this._fs));
        } catch (e) {
          // storage blocked: the size still applies until the page reloads
        }
        this._render();
      })
    );
    root.querySelectorAll("[data-zoom]").forEach((b) =>
      b.addEventListener("click", () => {
        const z = this._zoom ?? this._fitZoom ?? 1;
        if (b.dataset.zoom === "fit") this._zoom = null;
        else this._zoom = Math.min(2, Math.max(0.2, z * (b.dataset.zoom === "+" ? 1.2 : 1 / 1.2)));
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
  .zoom { display:flex; align-items:center; }
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
  .zoom button { border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color);
    border-radius:6px; min-width:30px; height:26px; cursor:pointer; margin-left:2px; }
  .warn { background:var(--warning-color,#ffa600); color:#000; font-size:12px; padding:2px 16px; }
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
  .node.big .ring { width:var(--rs); height:var(--rs); border-width:3px; }
  .node.big .ring ha-icon { --mdc-icon-size:calc(var(--rs) * 0.52); }
  .node.big .ring svg.mini { width:calc(var(--rs) * 0.72); height:calc(var(--rs) * 0.72); }
  .node.big .ring img.photo-mini { width:calc(var(--rs) * 0.8); height:calc(var(--rs) * 0.8); }
  .node.big .ring.g-switch { width:calc(var(--rs) * 1.1); border-radius:calc(var(--rs) * 0.24); }
  .node.big .ring.g-switch svg.mini { width:calc(var(--rs) * 0.95); height:calc(var(--rs) * 0.48); }
  .node.big .ring.g-switch img.photo-mini { width:calc(var(--rs) * 1); height:calc(var(--rs) * 0.7); }
  .node.big .badge { font-size:16px; min-width:32px; height:32px; line-height:32px; border-radius:16px; right:-10px; top:-10px;
    padding:0 6px; font-weight:600; box-shadow:0 1px 3px rgba(0,0,0,.25); }
  .node.big .ring img.logo { width:26px; height:26px; right:-6px; bottom:-6px; }
  .node.big .new { font-size:11px; padding:2px 6px; left:-10px; top:-9px; }
  .badge { position:absolute; right:-6px; top:-6px; background:var(--primary-color); color:var(--text-primary-color,#fff);
    font-size:10px; min-width:16px; height:16px; line-height:16px; border-radius:8px; text-align:center; padding:0 3px; }
  .txt { min-width:0; flex:1; }
  .name { font-size:calc(13px * var(--fs, 1)); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:var(--primary-text-color); }
  .sub { font-size:calc(11px * var(--fs, 1)); color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .toggle { flex:0 0 auto; width:20px; height:20px; padding:0; border:1px solid var(--secondary-text-color); border-radius:4px;
    background:var(--card-background-color); color:var(--primary-text-color); cursor:pointer; line-height:16px; }
  .panel.wide { width:460px; }
  .panel { position:absolute; right:12px; top:58px; width:320px; max-width:calc(100% - 24px); max-height:70%; overflow:auto;
    background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px;
    box-shadow:0 4px 16px rgba(0,0,0,.2); padding:10px 12px; z-index:2; }
  .ptitle { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .ptitle b { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .close { border:none; background:none; font-size:20px; cursor:pointer; color:var(--secondary-text-color); }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  td { padding:3px 4px; border-top:1px solid var(--divider-color); vertical-align:top; word-break:break-word; }
  td:first-child { color:var(--secondary-text-color); white-space:nowrap; width:1%; }
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
