// Shared helpers (vanilla JS, no build step)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body});
  let data = null; try { data = await r.json(); } catch (e) {}
  if (!r.ok) { const d = data?.detail; throw new Error(Array.isArray(d) ? d.map(x => x.msg ? `${x.field ?? (x.loc || []).slice(-1)[0]}: ${x.msg}` : x).join("\n")
    : (d && typeof d === "object") ? [d.message, ...(d.errors || [])].filter(Boolean).join("\n") : (d || r.statusText)); }
  return data;
}
function fmtUptime(s) {
  if (s == null) return "—"; s = Math.floor(s);
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h ${m}m` : h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
}
const fmtMHz = (hz) => hz ? (hz / 1e6).toFixed(5) + " MHz" : "—";
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString() : "—";
function colorLog(line) {
  const k = (line[0] || "").toUpperCase();
  return `<div class="${/^[ADEFIMW]$/.test(k) ? k : ""}">${esc(line)}</div>`;
}

// ---- toasts (replace alert()) -------------------------------------------------
function toast(msg, kind = "info", ms = 3200) {
  const box = document.getElementById("toasts"); if (!box) return;
  const t = document.createElement("div");
  t.className = `toast ${kind}`; t.setAttribute("role", kind === "err" ? "alert" : "status");
  t.innerHTML = `<span class="toast-ico">${kind === "ok" ? "✓" : kind === "err" ? "!" : kind === "warn" ? "!" : "i"}</span><span></span>
    <button class="toast-x" type="button" aria-label="Dismiss">×</button>`;
  t.children[1].textContent = msg;
  t.querySelector(".toast-x").onclick = () => t.remove();
  box.appendChild(t);
  requestAnimationFrame(() => t.classList.add("in"));
  setTimeout(() => { t.classList.remove("in"); setTimeout(() => t.remove(), 250); }, kind === "err" ? ms * 2 : ms);
}

// ---- confirm dialog (replaces confirm()); resolves true/false --------------------
function confirmDialog({title = "Are you sure?", body = "", ok = "Continue", danger = false} = {}) {
  const d = document.getElementById("dlg");
  if (!d || typeof d.showModal !== "function") return Promise.resolve(window.confirm(title + "\n\n" + body));
  $("#dlgTitle").textContent = title;
  const b = $("#dlgBody");
  b.innerHTML = Array.isArray(body) ? `<ul>${body.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p>${esc(body)}</p>`;
  const okb = $("#dlgOk"); okb.textContent = ok; okb.className = "btn " + (danger ? "danger solid" : "primary");
  d.returnValue = "";
  d.showModal(); $("#dlgCancel").focus();
  return new Promise(res => d.addEventListener("close", () => res(d.returnValue === "ok"), {once: true}));
}

// ---- theme toggle ------------------------------------------------------------------
(() => {
  const b = document.getElementById("themeBtn"); if (!b) return;
  b.onclick = () => {
    const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("ml-theme", t); } catch (e) {}
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape") document.body.classList.remove("menu-open"); });
})();

// ---- sortable tables: click / Enter on a <th data-sort> header. State survives re-renders
// when the table has an id (dynamic tables call sortTable(el) after each render). ---------
const _sortState = {};
function _cellVal(td) {
  const v = (td?.dataset.v ?? td?.textContent ?? "").trim();
  const n = Number(v.replace(/^TG\s*/i, "").replace(/s$/, ""));
  return v !== "" && !isNaN(n) ? n : v.toLowerCase();
}
function sortTable(table) {
  if (!table) return;
  const key = table.id || "", st = _sortState[key];
  const heads = $$("th", table);
  heads.forEach((th, i) => {
    if (!("sort" in th.dataset)) return;
    th.tabIndex = 0; th.setAttribute("role", "button");
    th.setAttribute("aria-sort", st && st.col === i ? (st.dir > 0 ? "ascending" : "descending") : "none");
    const go = () => { const cur = _sortState[key]; _sortState[key] = {col: i, dir: cur && cur.col === i ? -cur.dir : 1}; sortTable(table); };
    th.onclick = go; th.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
  });
  if (!st) return;
  const body = table.tBodies[0] || table;
  const rows = $$("tr", body).filter(r => r.querySelector("td"));
  rows.sort((a, b) => { const x = _cellVal(a.children[st.col]), y = _cellVal(b.children[st.col]);
    return (x > y ? 1 : x < y ? -1 : 0) * st.dir; });
  rows.forEach(r => body.appendChild(r));
}
document.addEventListener("DOMContentLoaded", () => $$("table[data-sortable]").forEach(sortTable));

// ---- unpair / reset (Dashboard, Setup, Trunking, About)
function unpairOut(html) { document.querySelectorAll("[data-unpair-out]").forEach(e => e.innerHTML = html); }
async function unpairThisPi(role) {
  const opts = role === "voice"
    ? {title: "Unpair this Pi?", ok: "Unpair this Pi", body: ["dvmhost is stopped",
       "its config files are moved to *.bak and state.json is backed up to state.json.bak",
       "this Pi goes back to a fresh, unconfigured state",
       "the control Pi is told to drop this voice channel (if it can be reached)"]}
    : role === "control"
    ? {title: "Unpair & reset this CONTROL Pi?", ok: "Unpair & reset", body: ["dvmhost/dvmfne are stopped, configs moved to *.bak, state.json backed up",
       "every paired voice Pi that can be reached is released too", "works even if the voice Pis are offline"]}
    : {title: "Reset this Pi to a fresh start?", ok: "Reset this Pi", body: ["dvmhost/dvmfne are stopped, configs moved to *.bak, state.json backed up"]};
  if (!await confirmDialog({...opts, danger: true})) return;
  unpairOut("Working…");
  try {
    const r = await api("/api/pairing/reset", {method: "POST", body: {notify: true}});
    const n = (r.notified || []).map(x => `${esc(x.node)}: ${x.ok ? "notified" : "not reached (" + esc(x.detail || "") + ")"}`).join("<br>");
    toast("This Pi is now unconfigured", "ok");
    unpairOut(`<span style="color:var(--green)">Done — this Pi is unconfigured. ${r.backed_up.length} config file(s) kept as *.bak.</span>${n ? "<br>" + n : ""} <a href="/setup">Run Setup →</a>`);
    setTimeout(() => location.href = "/setup", 2500);
  } catch (e) { unpairOut(`<span class="err">${esc(e.message)}</span>`); toast(e.message, "err"); }
}
async function unpairVc(ch, name) {
  if (!await confirmDialog({title: `Remove / unpair voice channel ${ch} (${name})?`, ok: "Remove / unpair", danger: true,
      body: `This control Pi's configs are rewritten (dvmhost restarts if running) and ${name} is told to release itself if reachable.`})) return;
  unpairOut("Working…");
  try {
    const r = await api(`/api/pairing/voice-channels/${ch}/unpair`, {method: "POST"});
    toast(`Removed ${r.removed}`, "ok");
    unpairOut(`<span style="color:var(--green)">Removed ${esc(r.removed)}.</span> ${r.voice_pi.ok ? "It released itself." : "It could not be reached (" + esc(r.voice_pi.detail || "") + ") — use “Unpair this Pi” on it later."}`);
    setTimeout(() => location.reload(), 2500);
  } catch (e) { unpairOut(`<span class="err">${esc(e.message)}</span>`); toast(e.message, "err"); }
}

// ---- modem / serial ports panel (About, node page, wizard). Read-only: never talks to the modem. ----
function _yn(v, yes = "yes", no = "no") { return v == null ? '<span class="muted">?</span>' : v ? `<span style="color:var(--green)">${yes}</span>` : `<span style="color:var(--red)">${no}</span>`; }
function renderPorts(scan, pick) {
  const rows = (scan.ports || []).map(p => `<tr class="${p.likely_hat ? "hat" : ""}">
    <td class="mono">${esc(p.path)}${p.target ? ` <span class="muted">→ ${esc(p.target)}</span>` : ""}</td>
    <td class="small">${p.likely_hat ? '<span class="pill blue">likely hat</span> ' : ""}${esc(p.description || "")}</td>
    <td>${_yn(p.exists)}</td><td title="read / write access for the ${esc(scan.user || "dvmhost")} user">${_yn(p.readable)} / ${_yn(p.writable)}</td>
    ${pick ? `<td class="acts"><button type="button" class="btn sm${p.likely_hat ? " primary" : ""}" data-pick="${esc(p.target && p.kind !== "debug" ? p.target : p.path)}">Use</button></td>` : ""}</tr>`).join("");
  const b = scan.boot || {};
  const boot = b.config_path ? `<div class="small muted" style="margin-top:8px"><span class="mono">${esc(b.config_path)}</span>: enable_uart=${b.enable_uart == null ? "(not set)" : b.enable_uart ? "1" : "0"}${(b.dtparams || []).length ? " · dtparam=" + esc(b.dtparams.join(",")) : ""}${(b.dtoverlays || []).length ? " · dtoverlay=" + esc(b.dtoverlays.join(", ")) : ""}${(b.console || []).length ? ` · console=${esc(b.console.join(", "))}` : ""}</div>` : "";
  return `<div class="row between" style="gap:8px;flex-wrap:wrap"><div class="small"><b>${esc(scan.model || "Unknown machine")}</b>${scan.likely_hat ? ` — likely hat port <span class="mono">${esc(scan.likely_hat)}</span>` : ""}</div>
      ${scan.sample ? `<span class="pill amber" title="${esc(scan.sample_reason || "")}">SAMPLE DATA</span>` : ""}</div>
    ${scan.sample ? `<div class="small muted">${esc(scan.sample_reason || "")}</div>` : ""}
    <div class="small muted" style="margin:4px 0 8px">${esc(scan.likely_hat_reason || "")}</div>
    ${rows ? `<div style="overflow-x:auto"><table class="t ports"><thead><tr><th>Port</th><th>What it is</th><th>Exists</th><th>${esc(scan.user || "dvmhost")} r / w</th>${pick ? '<th><span class="sr">Use</span></th>' : ""}</tr></thead><tbody>${rows}</tbody></table></div>`
           : '<div class="empty">No serial ports found.</div>'}
    ${boot}
    ${(scan.hints || []).map(h => `<div class="notice small" style="margin-top:8px">${esc(h).replace(/`([^`]+)`/g, "<code>$1</code>")}</div>`).join("")}`;
}
function renderModem(d) {
  if (d.error) return `<div class="empty">${esc(d.error)}</div>`;
  const c = d.configured || {}, s = d.status || {}, m = s.modem || {};
  const mhz = v => v ? (v / 1e6).toFixed(5) + " MHz" : "—";
  const mism = c.config_port && c.port && c.config_port !== c.port;
  const st = s.running ? (m.modemPort ? `<span class="pill green">dvmhost running</span> <span class="pill gray">${esc(s.state || "")}</span>${s.tx ? ' <span class="pill red" title="reported by dvmhost (read-only)">TX</span>' : ""}` : '<span class="pill amber">running · no modem info</span>') : '<span class="pill gray">dvmhost not running</span>';
  const fields = [["Port", m.modemPort, 1], ["Type", m.portType], ["Speed", m.portSpeed], ["Firmware protocol", m.protoVer],
    ["RX freq (effective)", m.rxFrequencyEffective != null ? mhz(m.rxFrequencyEffective) : null, 1], ["TX freq (effective)", m.txFrequencyEffective != null ? mhz(m.txFrequencyEffective) : null, 1],
    ["RX level", m.rxLevel], ["RX / TX DC offset", m.rxDCOffset != null ? `${m.rxDCOffset} / ${m.txDCOffset}` : null], ["Gain mode", m.gainMode], ["V.24 connected", m.v24Connected == null ? null : m.v24Connected ? "yes" : "no"]]
    .filter(f => f[1] != null && f[1] !== "").map(f => `<tr><td class="muted">${f[0]}</td><td class="${f[2] ? "mono" : ""}">${esc(f[1])}</td></tr>`).join("");
  return `<table class="t"><tr><td class="muted">Configured port</td><td class="mono">${esc(c.port || "— not set yet")}${c.speed ? ` <span class="muted">@ ${esc(c.speed)}</span>` : ""}${c.source ? ` <span class="muted small">(${esc(c.source)})</span>` : ""}</td></tr>
      ${c.config_file ? `<tr><td class="muted">config.yml</td><td class="mono">${esc(c.config_port || "—")} <span class="muted small">${esc(c.config_type || "")}</span>${mism ? ' <span class="pill amber">differs from wizard — re-save Setup</span>' : ""}</td></tr>` : ""}</table>
    <h3 style="margin:14px 0 6px">Live modem status ${st}${s.sample ? ' <span class="pill amber">SAMPLE</span>' : ""}</h3>
    ${fields ? `<table class="t">${fields}</table>` : ""}<div class="small muted">${esc(s.note || "")}</div>
    <h3 style="margin:14px 0 6px">Serial ports on this Pi</h3>${renderPorts(d.scan || {}, false)}`;
}
async function loadModem(el, node = "local") {
  el.innerHTML = '<div class="skeleton" style="height:120px"></div>';
  try { el.innerHTML = renderModem(await api(`/api/modem?node=${encodeURIComponent(node)}`)); }
  catch (e) { el.innerHTML = `<div class="empty">Modem info unavailable: ${esc(e.message)}</div>`; }
}

// ---- calm service health (dashboard + node page). States come from app/data/health.py ----
const HEALTH_PILL = {ok: "green", info: "blue", idle: "gray", warn: "amber", err: "red"};
function healthHtml(h, nodeId) {
  if (!h) return "";
  const btn = a => a ? `<button type="button" class="btn sm${a.kind === "start" || a.kind === "install" ? " primary" : ""}" data-hact="${esc(a.kind)}" data-unit="${esc(a.unit || "")}" data-node="${esc(nodeId)}">${esc(a.label)}</button>` : "";
  return `<div class="health ${esc(h.level)}" data-state="${esc(h.state)}" role="status">
    <div class="row between" style="gap:8px"><b>${esc(h.title)}</b><span class="pill ${HEALTH_PILL[h.level] || "gray"}">${esc(h.state.replace(/_/g, " "))}</span></div>
    <div class="small muted" style="margin-top:3px">${esc(h.detail || "")}</div>
    ${h.action || h.secondary ? `<div class="row" style="gap:6px;margin-top:8px">${btn(h.action)}${btn(h.secondary)}</div>` : ""}</div>`;
}
async function startService(unit) {
  if (unit === "dvmhost" && !await confirmDialog({title: "Start dvmhost?", body: "Starting dvmhost puts your station on the air on the configured frequencies. Make sure you are licensed for them.", ok: "Start dvmhost"})) return;
  try {
    const r = await api(`/api/service/${encodeURIComponent(unit)}/start`, {method: "POST"});
    toast(`${unit} start: ${r.ok ? "ok" : "failed"}${r.dry_run ? " (dry-run)" : ""}`, r.ok ? "ok" : "err");
  } catch (e) { toast(e.message, "err"); }
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-hact]"); if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const node = b.dataset.node || "local", unit = b.dataset.unit;
  const go = {install: "/setup#install", setup: "/setup", logs: `/logs?node=${encodeURIComponent(node)}&kind=${unit === "dvmfne" ? "fne" : "system"}`,
              modem: `/node/${encodeURIComponent(node)}#modem`, node: `/node/${encodeURIComponent(node)}`}[b.dataset.hact];
  if (go) location.href = go; else if (b.dataset.hact === "start") startService(unit);
});
