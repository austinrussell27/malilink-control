// Shared helpers (vanilla JS, no build step)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body});
  let data = null; try { data = await r.json(); } catch (e) {}
  if (!r.ok) { const d = data?.detail; throw new Error(Array.isArray(d) ? d.map(x => x.msg ? `${x.field}: ${x.msg}` : x).join("\n") : (d || r.statusText)); }
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
