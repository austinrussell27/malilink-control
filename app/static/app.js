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

// ---- unpair / reset (Dashboard, Setup, Trunking, About)
function unpairOut(html) { document.querySelectorAll("[data-unpair-out]").forEach(e => e.innerHTML = html); }
async function unpairThisPi(role) {
  const msg = role === "voice"
    ? "Unpair this Pi?\n\n• dvmhost is stopped\n• its config files are moved to *.bak and state.json is backed up to state.json.bak\n• this Pi goes back to a fresh, unconfigured state\n• the control Pi is told to drop this voice channel (if it can be reached)"
    : role === "control"
    ? "Unpair & reset this CONTROL Pi to a fresh start?\n\n• dvmhost/dvmfne are stopped, configs moved to *.bak, state.json backed up\n• every paired voice Pi that can be reached is released too\n• works even if the voice Pis are offline"
    : "Reset this Pi to a fresh start?\n\n• dvmhost/dvmfne are stopped, configs moved to *.bak, state.json backed up";
  if (!confirm(msg)) return;
  unpairOut("Working…");
  try {
    const r = await api("/api/pairing/reset", {method: "POST", body: {notify: true}});
    const n = (r.notified || []).map(x => `${esc(x.node)}: ${x.ok ? "notified" : "not reached (" + esc(x.detail || "") + ")"}`).join("<br>");
    unpairOut(`<span style="color:var(--green)">Done — this Pi is unconfigured. ${r.backed_up.length} config file(s) kept as *.bak.</span>${n ? "<br>" + n : ""} <a href="/setup">Run Setup →</a>`);
    setTimeout(() => location.href = "/setup", 2500);
  } catch (e) { unpairOut(`<span class="err">${esc(e.message)}</span>`); }
}
async function unpairVc(ch, name) {
  if (!confirm(`Remove / unpair voice channel ${ch} (${name})?\n\nThis control Pi's configs are rewritten (dvmhost restarts if running) and ${name} is told to release itself if reachable.`)) return;
  unpairOut("Working…");
  try {
    const r = await api(`/api/pairing/voice-channels/${ch}/unpair`, {method: "POST"});
    unpairOut(`<span style="color:var(--green)">Removed ${esc(r.removed)}.</span> ${r.voice_pi.ok ? "It released itself." : "It could not be reached (" + esc(r.voice_pi.detail || "") + ") — use “Unpair this Pi” on it later."}`);
    setTimeout(() => location.reload(), 2500);
  } catch (e) { unpairOut(`<span class="err">${esc(e.message)}</span>`); }
}
