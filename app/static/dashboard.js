(() => {
  const roleName = {control: "Control channel", voice: "Voice channel", conventional: "Conventional"};
  function svc(s, label) {
    if (!s) return "";
    return `<span class="pill ${s.active ? "green" : "red"}">${label} ${s.active ? "up" : "down"}</span>`;
  }
  function render(d) {
    $("#sub").textContent = `${d.nodes.length} Pi${d.nodes.length === 1 ? "" : "s"} · updated ${fmtTime(d.ts)}`;
    $("#talking").innerHTML = d.talking.length ? d.talking.map(t => `
      <div class="talk"><span class="dot down" style="background:var(--red);box-shadow:0 0 10px var(--red)"></span>
        <div><div class="ch">${esc(t.node)} · ${esc(t.mode || "")}${t.slot ? " TS" + esc(t.slot) : ""} · channel ${esc(t.channelNo ?? "—")} · ${esc(t.source)}${t.encrypted ? " · encrypted" : ""}</div>
        <div class="who">Radio ${esc(t.srcId)} → <span class="tg">TG ${esc(t.dstId)}${t.tgName ? " " + esc(t.tgName) : ""}</span></div></div>
        <div class="muted small" style="margin-left:auto">${Math.max(0, Math.round(d.ts - t.since))}s</div></div>`).join("")
      : `<div class="empty">Quiet — nobody is talking.</div>`;
    $("#nodes").innerHTML = d.nodes.map(n => {
      const up = n.host && n.host.active;
      return `<div class="card node"><a href="/node/${encodeURIComponent(n.id)}">
        <div class="row between"><span class="name"><span class="dot ${up ? "up" : "down"}"></span> ${esc(n.name)}</span>
        <span class="pill ${["P25", "DMR", "NXDN"].includes(n.mode) ? "amber" : n.mode === "Idle" ? "gray" : up ? "blue" : "red"}">${esc(n.mode)}</span></div>
        <div class="muted small">${esc(roleName[n.role] || n.role || "")}${n.channelNo ? " · ch " + esc(n.channelNo) : ""}${n.protocol ? " · " + esc(n.protocol) : ""}${n.site_label ? " · " + esc(n.site_label) : ""}</div>
        <div class="row" style="margin-top:8px;gap:6px">${svc(n.host, "dvmhost")} ${svc(n.fne, "dvmfne")}</div>
        <div class="kv"><span>Uptime</span><span class="stat">${fmtUptime(n.host && n.host.uptime_s)}</span>
          <span>TX freq</span><span class="stat">${fmtMHz(n.modem && n.modem.txFrequency)}</span>
          <span>RX freq</span><span class="stat">${fmtMHz(n.modem && n.modem.rxFrequency)}</span>
          <span>Modem</span><span>${esc((n.modem && n.modem.modemPort) || "—")}</span>
          <span>Last call</span><span>${n.lastSrcId ? esc(n.lastSrcId) + " → TG " + esc(n.lastDstId) : "—"}</span></div>
        ${n.error ? `<div class="err">${esc(n.error)}</div>` : ""}
        <div class="txbar ${n.tx && up ? "on" : ""}" title="${n.tx ? "transmitting" : "idle"}"></div></a></div>`;
    }).join("");
    $("#grants").innerHTML = d.grants.length ? `<table class="t"><tr><th>Channel</th><th>Talkgroup</th><th>Radio</th><th>Via</th></tr>
      ${d.grants.map(g => `<tr><td>${esc(g.channelNo)}${g.slot ? " TS" + esc(g.slot) : ""}</td><td>TG ${esc(g.dstId)}</td><td>${esc(g.srcId)}</td><td>${esc(g.node)}</td></tr>`).join("")}</table>`
      : `<div class="empty">No active grants${d.nodes.some(n => n.role === "control") ? "" : " (grants appear on trunked control channels)"}.</div>`;
    $("#affs").innerHTML = d.affiliations.length ? `<table class="t"><tr><th>Radio</th><th>Talkgroup</th><th>Mode</th></tr>
      ${d.affiliations.map(a => `<tr><td>${esc(a.srcId)}</td><td>TG ${esc(a.grpId)}</td><td class="muted">${esc(a.mode || "")}</td></tr>`).join("")}</table>` : `<div class="empty">No affiliations reported.</div>`;
    $("#recent").innerHTML = d.recent.length ? `<table class="t"><tr><th>Time</th><th>Pi</th><th>Event</th><th>Radio</th><th>TG</th><th>Length</th></tr>
      ${d.recent.map(e => `<tr><td>${fmtTime(e.ts)}</td><td>${esc(e.node)}</td><td>${esc(e.event)} <span class="muted small">${esc(e.mode || "")}${e.slot ? " TS" + esc(e.slot) : ""} ${esc(e.source)}</span></td>
        <td>${esc(e.srcId ?? "")}</td><td>${e.dstId != null ? "TG " + esc(e.dstId) : ""}</td><td>${e.duration != null ? e.duration + "s" : ""}</td></tr>`).join("")}</table>`
      : `<div class="empty">No activity yet today.</div>`;
  }
  function connect() {
    const es = new EventSource("/api/stream");
    es.onmessage = ev => { $("#live").className = "pill green"; render(JSON.parse(ev.data)); };
    es.onerror = () => { $("#live").className = "pill red"; $("#live").textContent = "● reconnecting"; };
    es.onopen = () => { $("#live").textContent = "● live"; };
  }
  api("/api/snapshot").then(render).catch(e => $("#sub").textContent = e.message);
  // ?poll=1 uses plain polling instead of Server-Sent Events (handy behind some proxies / for screenshots)
  if (new URLSearchParams(location.search).has("poll")) setInterval(() => api("/api/snapshot").then(render), 2000);
  else connect();
})();
