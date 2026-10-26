(() => {
  const roleName = {control: "Control channel", voice: "Voice channel", conventional: "Conventional"};
  const I = {
    wave: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12h3l3-8 4 16 3-8h7"/></svg>',
    server: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/></svg>',
    grant: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>',
    users: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="9" cy="7" r="4"/><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/></svg>',
    radio: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="6" y="7" width="12" height="15" rx="2"/><path d="M9 7V2M10 12h4M10 16h4"/></svg>',
    quiet: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 12h20"/></svg>',
  };
  let last = null;
  function svc(s, label) {
    if (!s) return "";
    return `<span class="pill ${s.active ? "green" : "red"}">${label} ${s.active ? "up" : "down"}</span>`;
  }
  const kpi = (ico, cls, v, l) => `<div class="kpi"><span class="k-ico ${cls}">${ico}</span><div><div class="k-v">${v}</div><div class="k-l">${l}</div></div></div>`;
  const evClass = e => /start/.test(e) ? "start" : /end/.test(e) ? "end" : /lost/.test(e) ? "lost" : /reject/.test(e) ? "rej" : /grant/.test(e) ? "grant" : "";
  function callHtml(t, now) {
    return `<div class="call"><div class="who">${esc(t.srcId)} <span class="muted" style="font-weight:500">→</span> <span class="tg">TG ${esc(t.dstId)}</span></div>
      <div class="meta">${t.tgName ? esc(t.tgName) + " · " : ""}${esc(t.source)}${t.encrypted ? " · encrypted" : ""} · ${Math.max(0, Math.round(now - t.since))}s</div></div>`;
  }
  function tiles(d) {
    const chans = d.nodes.filter(n => n.reachable !== false || n.channelNo);
    const byNode = {};
    d.talking.forEach(t => (byNode[t.node] = byNode[t.node] || []).push(t));
    const html = chans.map(n => {
      const calls = byNode[n.name] || byNode[n.id] || [];
      const up = n.host && n.host.active;
      const proto = (n.protocol || n.mode || "").toUpperCase();
      const isDmr = proto === "DMR" || calls.some(c => c.slot);
      let body;
      if (!up) body = `<div class="c-idle">${I.quiet} Offline</div>`;
      else if (isDmr && n.role !== "control") {
        body = `<div class="slots">${[1, 2].map(s => { const c = calls.find(x => x.slot === s);
          return `<div class="slot ${c ? "active" : ""}"><div class="s-l">TS${s}${c ? ' · <span class="onair"><span class="pdot"></span>ON AIR</span>' : ""}</div>${c ? callHtml(c, d.ts) : '<span class="muted small">idle</span>'}</div>`; }).join("")}</div>`;
      } else if (calls.length) body = `<div class="c-body">${calls.map(c => callHtml(c, d.ts)).join("")}</div>`;
      else body = n.role === "control" ? `<div class="c-idle">${I.wave} Control channel · ${d.grants.length} grant${d.grants.length === 1 ? "" : "s"}</div>` : `<div class="c-idle">${I.quiet} Idle</div>`;
      return `<div class="chan ${calls.length ? "active" : ""} ${up ? "" : "offline"}">
        <div class="c-top"><span class="c-name">${esc(n.name)}</span><span class="row" style="gap:6px">${calls.length ? '<span class="onair"><span class="pdot"></span>ON AIR</span>' : ""}<span class="c-ch">ch ${esc(n.channelNo ?? "—")}</span></span></div>
        <div class="muted small">${esc(roleName[n.role] || n.role || "")}${proto ? " · " + esc(proto) : ""}${n.site_label ? " · " + esc(n.site_label) : ""}</div>${body}</div>`;
    }).join("");
    // calls on Pis we don't have a tile for (shouldn't happen, but never hide a live call)
    const known = new Set(chans.flatMap(n => [n.name, n.id]));
    const orphan = d.talking.filter(t => !known.has(t.node)).map(t => `<div class="chan active"><div class="c-top"><span class="c-name">${esc(t.node)}</span><span class="c-ch">ch ${esc(t.channelNo ?? "—")}</span></div>${callHtml(t, d.ts)}</div>`).join("");
    return html + orphan || `<div class="empty">${I.quiet}Quiet — nobody is talking.</div>`;
  }
  function render(d) {
    last = d;
    const up = d.nodes.filter(n => n.host && n.host.active).length;
    $("#sub").textContent = `${d.nodes.length} Pi${d.nodes.length === 1 ? "" : "s"} · updated ${fmtTime(d.ts)}`;
    const proto = [...new Set(d.nodes.map(n => n.protocol).filter(Boolean))].join(" / ") || (d.nodes[0] && d.nodes[0].mode) || "—";
    $("#kpis").innerHTML = kpi(I.server, up === d.nodes.length ? "g" : "r", `${up}<span class="muted" style="font-size:15px">/${d.nodes.length}</span>`, "Pis online")
      + kpi(I.wave, d.talking.length ? "r" : "", d.talking.length, "On the air")
      + kpi(I.grant, "", d.grants.length, "Active grants")
      + kpi(I.users, "", d.affiliations.length, "Affiliations")
      + kpi(I.radio, "a", esc(proto), "Protocol");
    $("#airCount").textContent = d.talking.length ? `${d.talking.length} active call${d.talking.length === 1 ? "" : "s"}` : "all quiet";
    $("#talking").innerHTML = tiles(d);
    $("#nodes").innerHTML = d.nodes.map(n => {
      const up = n.host && n.host.active;
      return `<div class="card node"><a href="/node/${encodeURIComponent(n.id)}" aria-label="${esc(n.name)} details">
        <div class="row between"><span class="name"><span class="dot ${up ? "up" : "down"}"></span>${esc(n.name)}</span>
        <span class="pill ${["P25", "DMR", "NXDN"].includes(n.mode) ? "amber" : n.mode === "Idle" ? "gray" : up ? "blue" : "red"}">${esc(n.mode)}</span></div>
        <div class="muted small" style="margin-top:4px">${esc(roleName[n.role] || n.role || "")}${n.channelNo ? " · ch " + esc(n.channelNo) : ""}${n.protocol ? " · " + esc(n.protocol) : ""}${n.site_label ? " · " + esc(n.site_label) : ""}</div>
        <div class="row" style="margin-top:10px;gap:6px">${svc(n.host, "dvmhost")} ${svc(n.fne, "dvmfne")}</div>
        <div class="kv"><span>Uptime</span><span class="stat">${fmtUptime(n.host && n.host.uptime_s)}</span>
          <span>TX freq</span><span class="stat">${fmtMHz(n.modem && n.modem.txFrequency)}</span>
          <span>RX freq</span><span class="stat">${fmtMHz(n.modem && n.modem.rxFrequency)}</span>
          <span>Modem</span><span class="mono small">${esc((n.modem && n.modem.modemPort) || "—")}</span>
          <span>Last call</span><span>${n.lastSrcId ? esc(n.lastSrcId) + " → TG " + esc(n.lastDstId) : "—"}</span></div>
        ${n.error ? `<div class="err">${esc(n.error)}</div>` : ""}
        <div class="txbar ${n.tx && up ? "on" : ""}" title="${n.tx ? "transmitting" : "idle"}"></div></a></div>`;
    }).join("") || `<div class="empty">No Pis reported yet.</div>`;
    $("#grantCount").textContent = d.grants.length;
    $("#grants").innerHTML = d.grants.length ? `<table class="t" id="tGrants"><thead><tr><th data-sort>Channel</th><th data-sort>Talkgroup</th><th data-sort>Radio</th><th data-sort>Via</th></tr></thead><tbody>
      ${d.grants.map(g => `<tr><td class="mono">${esc(g.channelNo)}${g.slot ? " TS" + esc(g.slot) : ""}</td><td data-v="${esc(g.dstId)}">TG ${esc(g.dstId)}</td><td class="mono">${esc(g.srcId)}</td><td>${esc(g.node)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">${I.grant}No active grants${d.nodes.some(n => n.role === "control") ? "" : " (grants appear on trunked control channels)"}.</div>`;
    sortTable($("#tGrants"));
    renderAffs(); renderRecent();
  }
  function renderAffs() {
    const d = last; if (!d) return;
    const q = $("#affQ").value.trim().toLowerCase();
    const rows = d.affiliations.filter(a => !q || `${a.srcId} ${a.grpId} tg ${a.grpId}`.toLowerCase().includes(q));
    $("#affCount").textContent = d.affiliations.length;
    $("#affs").innerHTML = rows.length ? `<table class="t" id="tAffs"><thead><tr><th data-sort>Radio</th><th data-sort>Talkgroup</th><th data-sort>Mode</th></tr></thead><tbody>
      ${rows.map(a => `<tr><td class="mono">${esc(a.srcId)}</td><td data-v="${esc(a.grpId)}">TG ${esc(a.grpId)}</td><td class="muted">${esc(a.mode || "")}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">${I.users}${q ? "No affiliations match." : "No affiliations reported."}</div>`;
    sortTable($("#tAffs"));
  }
  function renderRecent() {
    const d = last; if (!d) return;
    const q = $("#recentQ").value.trim().toLowerCase(), k = $("#recentKind").value;
    const rows = d.recent.filter(e => (!k || (k === "call" ? /call/.test(e.event) : e.event.includes(k)))
      && (!q || `${e.node} ${e.event} ${e.srcId ?? ""} tg ${e.dstId ?? ""} ${e.mode || ""}`.toLowerCase().includes(q)));
    $("#recentCount").textContent = d.recent.length;
    $("#recent").innerHTML = rows.length ? `<table class="t" id="tRecent"><thead><tr><th data-sort>Time</th><th data-sort>Pi</th><th data-sort>Event</th><th data-sort>Radio</th><th data-sort>TG</th><th data-sort class="num">Length</th></tr></thead><tbody>
      ${rows.map(e => `<tr><td data-v="${e.ts}" class="mono small">${fmtTime(e.ts)}</td><td>${esc(e.node)}</td><td><span class="ev ${evClass(e.event)}">${esc(e.event)}</span> <span class="muted small">${esc(e.mode || "")}${e.slot ? " TS" + esc(e.slot) : ""} ${esc(e.source)}</span></td>
        <td class="mono">${esc(e.srcId ?? "")}</td><td data-v="${e.dstId ?? ""}">${e.dstId != null ? "TG " + esc(e.dstId) : ""}</td><td class="num" data-v="${e.duration ?? ""}">${e.duration != null ? e.duration + "s" : ""}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">${I.quiet}${q || k ? "Nothing matches the filter." : "No activity yet today."}</div>`;
    sortTable($("#tRecent"));
  }
  $("#affQ").oninput = renderAffs; $("#recentQ").oninput = renderRecent; $("#recentKind").onchange = renderRecent;
  const setLive = (cls, txt) => { const l = $("#live"); l.className = "pill " + cls; l.innerHTML = cls === "green" ? `<span class="live-dot"></span>${txt}` : txt; };
  function connect() {
    const es = new EventSource("/api/stream");
    es.onmessage = ev => { setLive("green", "live"); render(JSON.parse(ev.data)); };
    es.onerror = () => setLive("red", "● reconnecting");
    es.onopen = () => setLive("green", "live");
  }
  api("/api/snapshot").then(render).catch(e => { $("#sub").textContent = e.message; $("#talking").innerHTML = `<div class="errbox">Could not load the dashboard: ${esc(e.message)}</div>`; });
  // ?poll=1 uses plain polling instead of Server-Sent Events (handy behind some proxies / for screenshots)
  if (new URLSearchParams(location.search).has("poll")) { setLive("green", "polling"); setInterval(() => api("/api/snapshot").then(render).catch(() => setLive("red", "● offline")), 2000); }
  else connect();
})();
