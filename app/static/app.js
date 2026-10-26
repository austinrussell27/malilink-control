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
