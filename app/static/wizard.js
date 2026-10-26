(() => {
  let step = 0; const N = 10;
  const choice = {mode: "p25", topology: "conventional", network_mode: "local_fne", purpose: "new"};
  let tgs = [{tgid: 1, name: "Talkgroup 1", alias: "Tactical", parrot: false, slot: 1},
             {tgid: 9990, name: "Parrot", alias: "Parrot/Testing", parrot: true, slot: 1}];
  // Per-protocol limits (mirror app/configgen/generate.py MODES; verified in dvmhost source)
  const MODES = {
    p25: {label: "P25", tgMax: 65535, siteMax: 0xFE, sysMax: 0xFFE},
    dmr: {label: "DMR", tgMax: 16777215, siteMax: 0x1F, sysMax: 0xFFE},
    nxdn: {label: "NXDN", tgMax: 65535, siteMax: 0xFE, sysMax: 0x3FF},
  };
  const MODE_NOTES = {
    p25: "",
    dmr: "DMR: conventional = Tier II repeater/hotspot with a color code and two timeslots. Trunked = dvmhost's Tier III-style TSCC dedicated control channel that grants voice channels on paired Pis. Trunked DMR needs radios that support DMR Tier III trunking; dvmhost's DMR trunking is less widely used than P25 — test it on the bench first.",
    nxdn: "NXDN: NXDN48 (4800 bps, 6.25 kHz — dvmhost uses 384-bit NXDN48 frames; NXDN96 is not supported). Trunked = dvmhost's NXDN dedicated control channel. Honest note: the dvmhost config generator only ships P25/DMR trunking templates, so NXDN trunking is less exercised upstream — test it on the bench first. NXDN unit and talkgroup IDs are 16-bit (1–65535) and channel numbers stop at 1023.",
  };
  let preview = null, curFile = null, presetRids = null;

  function show(n) {
    step = Math.max(0, Math.min(N - 1, n));
    $$(".panel").forEach(p => p.classList.toggle("on", +p.dataset.step === step));
    $$("#steps li").forEach(li => { const i = +li.dataset.step; li.classList.toggle("on", i === step); li.classList.toggle("done", i < step);
      if (i === step) { li.setAttribute("aria-current", "step"); const ul = li.parentElement; if (ul.scrollWidth > ul.clientWidth) ul.scrollTo({left: li.offsetLeft - ul.offsetLeft - (ul.clientWidth - li.offsetWidth) / 2, behavior: "smooth"}); } else li.removeAttribute("aria-current"); });
    $("#wizStepN").textContent = step + 1; $("#wizStepL").textContent = $(`#steps li[data-step="${step}"] .l`).textContent;
    $("#wizBar").style.width = `${((step + 1) / N) * 100}%`;
    $("#back").disabled = step === 0;
    $("#next").style.visibility = step === N - 1 ? "hidden" : "visible";
    if (step === 9) doPreview();
    if (step === 4) calc();
    $("#next").hidden = step === 0 && choice.purpose === "voice";
    window.scrollTo({top: 0, behavior: "smooth"});
  }
  $$("#steps li").forEach(li => { li.onclick = () => show(+li.dataset.step);
    li.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(+li.dataset.step); } }; });
  $("#back").onclick = () => show(step - 1);
  $("#next").onclick = () => { if (validateStep()) show(step + 1); };

  const hexIn = (v, lo, hi) => { const s = String(v).trim().replace(/^(0x|\$)/i, ""); if (!/^[0-9a-f]+$/i.test(s)) return false; const n = parseInt(s, 16); return n >= lo && n <= hi; };
  function validateStep() {
    $$(".err", $(`.panel[data-step="${step}"]`)).forEach(e => e.remove());
    const need = {3: ["system_name", "callsign"], 4: ["tx_mhz", "rx_mhz"]}[step] || [];
    let ok = true;
    const bad = (id, msg) => { ok = false; $("#" + id).insertAdjacentHTML("afterend", `<div class="err">${esc(msg)}</div>`); };
    for (const id of need) { const el = $("#" + id); if (!el.value.trim()) bad(id, "Required"); }
    if (step === 5) {
      const m = MODES[choice.mode];
      if (!hexIn($("#site_id").value, 1, m.siteMax)) bad("site_id", `Hex 1–${m.siteMax.toString(16).toUpperCase()}`);
      if (choice.mode === "p25") {
        if (!hexIn($("#nac").value, 0, 0xFFF)) bad("nac", "Hex 0–FFF");
        if (!hexIn($("#sys_id").value, 1, m.sysMax)) bad("sys_id", "Hex 1–FFE");
      }
      if (choice.mode === "dmr") {
        const cc = +$("#color_code").value; if (!(cc >= 0 && cc <= 15)) bad("color_code", "0–15");
        if (!hexIn($("#dmr_net_id").value, 1, 0x7F)) bad("dmr_net_id", "Hex 1–7F");
        if (!$("#dmr_slot1").checked && !$("#dmr_slot2").checked) bad("dmr_slot2", "Pick at least one timeslot");
      }
      if (choice.mode === "nxdn") {
        const r = $("#ran").value.trim(); if (!/^\d+$/.test(r) || +r > 63) bad("ran", "0–63");
        if (!hexIn($("#sys_id").value, 1, 0x3FF)) bad("sys_id", "Hex 1–3FF for NXDN");
      }
    }
    if (step === 8) {
      const m = MODES[choice.mode], seen = new Set();
      for (const t of tgs) {
        if (!(t.tgid >= 1 && t.tgid <= m.tgMax)) { ok = false; $("#tgNote").innerHTML = `<span class="err">${m.label} talkgroup IDs must be 1–${m.tgMax} (TG ${esc(t.tgid)}).</span>`; break; }
        const k = choice.mode === "dmr" ? `${t.tgid}/${t.slot || 1}` : `${t.tgid}`;
        if (seen.has(k)) { ok = false; $("#tgNote").innerHTML = `<span class="err">Talkgroup ${esc(t.tgid)} is listed twice${choice.mode === "dmr" ? " on the same slot" : ""}.</span>`; break; }
        seen.add(k);
      }
      if (ok) tgNote();
    }
    return ok;
  }

  // protocol branching
  function nxdnLoc() {
    const s = parseInt($("#sys_id").value, 16), t = parseInt($("#site_id").value, 16);
    if (isNaN(s) || isNaN(t)) { $("#nxdnLoc").textContent = "NXDN location ID: enter hex system and site IDs."; return; }
    const loc = ((s & 0x3FF) * 4096) + (t & 0xFFF);  // dvmhost nxdn/Control.cpp, category GLOBAL
    $("#nxdnLoc").innerHTML = `NXDN location ID (computed by dvmhost from system &amp; site ID): <b class="mono">${loc}</b> <span class="muted">(hex ${loc.toString(16).toUpperCase().padStart(6, "0")})</span>`;
  }
  function tgNote() {
    $("#tgNote").innerHTML = choice.mode === "dmr" ? "DMR: each talkgroup is tied to a timeslot (<code>source.slot</code> in talkgroup_rules.yml). The same TG can be listed on both slots."
      : choice.mode === "nxdn" ? "NXDN group IDs are 16-bit (1–65535)." : "";
  }
  function applyMode() {
    const m = choice.mode;
    $$("[data-proto]").forEach(el => el.hidden = !el.dataset.proto.split(" ").includes(m));
    $("#idsProto").textContent = MODES[m].label;
    $("#siteHint").textContent = `Hex 1–${MODES[m].siteMax.toString(16).toUpperCase()}${m === "dmr" ? " (DMR small site model)" : ""}.`;
    $("#sysHint").textContent = m === "nxdn" ? "Hex 1–3FF (NXDN uses 10 bits)." : "Hex 1–FFE.";
    $("#modeNote").hidden = !MODE_NOTES[m]; $("#modeNote").textContent = MODE_NOTES[m];
    $("#tsccSlotField").hidden = !(m === "dmr" && choice.topology === "trunked");
    $("#beaconField").hidden = !(m === "dmr" && choice.topology !== "trunked");
    $$(".slotcol").forEach(el => el.hidden = m !== "dmr");
    nxdnLoc(); tgNote(); renderTgs();
  }
  function setChoice(g, v) { choice[g] = v; $$(`[data-group="${g}"] .choice`).forEach(c => c.classList.toggle("sel", c.dataset.val === v)); }
  $("#sys_id").addEventListener("input", nxdnLoc); $("#site_id").addEventListener("input", nxdnLoc);

  // choice cards
  $$(".choices").forEach(g => $$(".choice", g).forEach(c => c.onclick = (ev) => {
    if (ev.target.tagName === "A") return;
    if (c.classList.contains("off")) return;
    $$(".choice", g).forEach(x => x.classList.remove("sel")); c.classList.add("sel");
    choice[g.dataset.group] = c.dataset.val;
    if (g.dataset.group === "network_mode") $("#remoteFields").hidden = c.dataset.val !== "remote_fne";
    if (g.dataset.group === "topology") applyTopology();
    if (g.dataset.group === "mode") { applyMode(); calc(); }
    if (g.dataset.group === "purpose") { $("#joinBox").hidden = c.dataset.val !== "voice"; $("#presetBox").hidden = c.dataset.val === "voice"; show(0); }
  }));
  // keyboard access for the choice cards (radio-group semantics)
  $$(".choices").forEach(g => { g.setAttribute("role", "radiogroup"); $$(".choice", g).forEach(c => {
    c.setAttribute("role", "radio"); c.tabIndex = c.classList.contains("off") ? -1 : 0;
    c.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); c.click(); } };
  }); });
  const syncAria = () => $$(".choice").forEach(c => c.setAttribute("aria-checked", c.classList.contains("sel")));
  document.addEventListener("click", e => { if (e.target.closest(".choice")) setTimeout(syncAria); });
  setTimeout(syncAria, 300);
  function applyTopology() {
    const t = choice.topology === "trunked";
    $("#ccTag").hidden = !t; $("#trunkNet").hidden = !t;
    $$('[data-group="network_mode"] .choice').forEach(c => c.classList.toggle("off", t && c.dataset.val !== "local_fne"));
    if (t) { choice.network_mode = "local_fne"; $$('[data-group="network_mode"] .choice').forEach(c => c.classList.toggle("sel", c.dataset.val === "local_fne")); $("#remoteFields").hidden = true; }
    $("#tsccSlotField").hidden = !(choice.mode === "dmr" && t);
    $("#beaconField").hidden = !(choice.mode === "dmr" && !t);
  }

  // talkgroups
  function renderTgs() {
    const dmr = choice.mode === "dmr";
    $("#tgBody").innerHTML = tgs.map((t, i) => `<tr>
      <td><input type="number" min="1" max="${MODES[choice.mode].tgMax}" value="${esc(t.tgid)}" data-i="${i}" data-k="tgid"></td>
      ${dmr ? `<td><select data-i="${i}" data-k="slot"><option value="1" ${(t.slot || 1) == 1 ? "selected" : ""}>TS1</option><option value="2" ${t.slot == 2 ? "selected" : ""}>TS2</option></select></td>` : ""}
      <td><input value="${esc(t.name)}" data-i="${i}" data-k="name"></td>
      <td><input value="${esc(t.alias)}" data-i="${i}" data-k="alias"></td>
      <td><input type="checkbox" ${t.parrot ? "checked" : ""} data-i="${i}" data-k="parrot" style="width:auto"></td>
      <td><button class="btn danger" type="button" data-del="${i}" aria-label="Remove">✕</button></td></tr>`).join("");
    $$("#tgBody input, #tgBody select").forEach(inp => inp.oninput = inp.onchange = () => {
      const t = tgs[+inp.dataset.i], k = inp.dataset.k;
      t[k] = k === "parrot" ? inp.checked : (k === "tgid" || k === "slot") ? +inp.value : inp.value; });
    $$("#tgBody [data-del]").forEach(b => b.onclick = () => { tgs.splice(+b.dataset.del, 1); renderTgs(); });
  }
  $("#tgAdd").onclick = () => { tgs.push({tgid: (Math.max(0, ...tgs.map(t => t.tgid).filter(x => x < 9990)) + 1), name: "", alias: "", parrot: false, slot: 1}); renderTgs(); };

  // frequency helper
  async function calc() {
    const tx = +$("#tx_mhz").value, rx = +$("#rx_mhz").value, box = $("#freqCalc");
    if (!tx || !rx) { box.hidden = true; return; }
    try {
      const r = await api("/api/calc/freq", {method: "POST", body: {tx_mhz: tx, rx_mhz: rx, mode: choice.mode}});
      box.hidden = false;
      box.innerHTML = r.simplex ? `Simplex: same frequency in and out (hotspot style). Channel ${r.channelId}-${r.channelNo}.`
        : `Duplex repeater, offset <b>${r.offset_mhz > 0 ? "+" : ""}${r.offset_mhz.toFixed(4)} MHz</b>. Will use identity row <code>${esc(r.iden_line)}</code>, channel ${r.channelId}-${r.channelNo}.`;
    } catch (e) { box.hidden = false; box.textContent = e.message; }
  }
  $("#tx_mhz").oninput = $("#rx_mhz").oninput = calc;

  function payload() {
    const v = id => $("#" + id).value;
    return {
      mode: choice.mode, topology: choice.topology, network_mode: choice.network_mode,
      system_name: v("system_name").trim(), callsign: v("callsign").trim(), location: v("location"),
      latitude: +v("latitude") || 0, longitude: +v("longitude") || 0, power_w: +v("power_w") || 0,
      tx_mhz: +v("tx_mhz"), rx_mhz: +v("rx_mhz"),
      nac: v("nac"), site_id: v("site_id"), wacn: v("wacn"), sys_id: v("sys_id"), rfss_id: v("rfss_id"),
      color_code: +v("color_code"), dmr_net_id: v("dmr_net_id"), ran: v("ran") === "" ? 1 : +v("ran"),
      dmr_slot1: $("#dmr_slot1").checked, dmr_slot2: $("#dmr_slot2").checked,
      dmr_beacons: $("#dmr_beacons").checked, dmr_tscc_slot: +v("dmr_tscc_slot"),
      modem_type: v("modem_type"), modem_port: v("modem_port"), modem_speed: +v("modem_speed"),
      fne_address: v("fne_address"), fne_port: +v("fne_port"), fne_password: v("fne_password"), peer_id: +v("peer_id"),
      fne_peer_id: +v("fne_peer_id") || null, vc_peer_start: +v("vc_peer_start") || null,
      ...(presetRids ? {radios: presetRids.radios, rid_lockdown: presetRids.lockdown} : {}),
      talkgroups: tgs.filter(t => t.tgid).map(t => ({...t, slot: choice.mode === "dmr" ? (t.slot || 1) : 1})),
      ack_license: $("#ack_license").checked, ack_terms: $("#ack_terms").checked,
    };
  }

  async function doPreview() {
    $("#reviewErr").hidden = true; $("#fileView").textContent = "Generating…";
    try {
      preview = await api("/api/wizard/preview", {method: "POST", body: payload()});
      $("#reviewWarn").innerHTML = preview.warnings.map(w => `<div class="notice blue small" style="margin-bottom:8px">${esc(w)}</div>`).join("")
        + preview.errors.map(w => `<div class="notice small" style="margin-bottom:8px">${esc(w)}</div>`).join("");
      reviewSummary(preview.radio);
      const names = Object.keys(preview.files); curFile = curFile && names.includes(curFile) ? curFile : names[0];
      $("#fileTabs").innerHTML = names.map(n => `<button type="button" class="${n === curFile ? "on" : ""}" data-f="${n}">${n}</button>`).join("");
      $$("#fileTabs button").forEach(b => b.onclick = () => { curFile = b.dataset.f; doPreview(); });
      $("#fileView").textContent = preview.files[curFile];
      $("#target").innerHTML = `Files will be written to <code>${esc(preview.target_dir)}</code>${preview.dry_run ? " (dry-run folder — nothing on the system is changed)" : ""}. Existing files are backed up as <code>*.bak</code>.`;
    } catch (e) { $("#reviewErr").hidden = false; $("#reviewErr").textContent = "Please fix: " + e.message; $("#fileView").textContent = ""; }
  }

  function reviewSummary(r) {
    const box = $("#reviewSummary"); if (!r) { box.hidden = true; return; }
    const row = (k, v) => `<tr><td class="muted">${esc(k)}</td><td class="mono">${v}</td></tr>`;
    let rows = row("Mode", esc(r.mode)) + row("Radio RX / TX", `${r.radio_rx_mhz.toFixed(5)} / ${r.radio_tx_mhz.toFixed(5)} MHz`)
      + row("Channel", `${r.band_plan.iden}-${r.channelNo} (bw ${r.band_plan.bandwidth_khz} kHz)`);
    if (r.protocol === "p25") rows += row("NAC", esc(r.nac_hex)) + (r.trunked ? row("WACN / Sys / RFSS / Site", `${esc(r.wacn)} / ${esc(r.sys_id)} / ${esc(r.rfss_id)} / ${esc(r.site_id)}`) : "");
    if (r.dmr) rows += row("Color code", r.dmr.color_code) + row("Network timeslots", r.dmr.slots.map(s => "TS" + s).join(" + "))
      + row("DMR net / site ID", `${esc(r.dmr.net_id)} / ${esc(r.dmr.site_id)}`) + (r.dmr.tscc_slot ? row("TSCC slot", r.dmr.tscc_slot) : "");
    if (r.nxdn) rows += row("RAN", r.nxdn.ran) + row("System / site ID", `${esc(r.nxdn.sys_id)} / ${esc(r.nxdn.site_id)}`)
      + row("Location ID", `${r.nxdn.location_id} (hex ${esc(r.nxdn.location_id_hex)})`);
    rows += row("Talkgroups", r.talkgroups.map(t => esc(t.tgid) + (r.protocol === "dmr" ? ` (TS${t.slot})` : "")).join(", ") || "—");
    box.hidden = false; box.innerHTML = `<h3 style="margin-top:0">Summary</h3><table class="t">${rows}</table>`;
  }

  $("#saveBtn").onclick = async () => {
    $("#saveMsg").textContent = "Saving…";
    try {
      const r = await api("/api/wizard/apply", {method: "POST", body: payload()});
      $("#saveMsg").textContent = `Saved ${r.written.length} files${r.dry_run ? " (dry-run)" : ""}.`;
      toast(`Configuration saved — ${r.written.length} files${r.dry_run ? " (dry-run)" : ""}`, "ok");
      $("#afterSave").hidden = false; $("#fneStart").hidden = choice.network_mode !== "local_fne";
      $("#pairLink").hidden = choice.topology !== "trunked";
    } catch (e) { $("#saveMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; toast(e.message, "err"); }
  };
  $$("[data-svc]").forEach(b => b.onclick = async () => {
    try { const r = await api(`/api/service/${b.dataset.svc}/${b.dataset.verb}`, {method: "POST"});
      $("#svcOut").textContent += `${r.cmd}: ${r.ok ? "ok" : "failed"}${r.dry_run ? " (dry-run)" : ""}\n${r.output || ""}`;
      toast(`${b.dataset.svc} ${b.dataset.verb}: ${r.ok ? "ok" : "failed"}${r.dry_run ? " (dry-run)" : ""}`, r.ok ? "ok" : "err");
    } catch (e) { $("#svcOut").textContent += e.message + "\n"; }
  });

  // installer
  let since = 0, polling = false;
  async function poll() {
    const s = await api(`/api/install/status?since=${since}`);
    if (since === 0 && s.lines.length) $("#instLog").innerHTML = "";
    since = s.next;
    s.lines.forEach(l => $("#instLog").insertAdjacentHTML("beforeend", `<div class="${l.startsWith("==>") ? "I" : /error|fail/i.test(l) ? "E" : ""}">${esc(l)}</div>`));
    if (s.lines.length) $("#instLog").scrollTop = 1e9;
    $("#instSteps").innerHTML = s.steps.map(x => `<li class="${s.done_steps.includes(x.id) ? "done" : s.step === x.id ? "run" : ""}">${s.done_steps.includes(x.id) ? "✓" : s.step === x.id ? "●" : "○"} ${esc(x.label)}</li>`).join("");
    if (s.src_dir) $("#instPaths").innerHTML = `Source: <span class="mono">${esc(s.src_dir)}</span> · build dir (wiped before every configure): <span class="mono">${esc(s.build_dir)}</span>`;
    const P = s.pieces || {}, chip = (ok, label) => `<span class="pill ${ok ? "green" : "gray"}">${ok ? "✓" : "✗"} ${label}</span>`;
    $("#pieces").innerHTML = chip(P.dvmhost_bin, "dvmhost binary") + chip(P.dvmfne_bin, "dvmfne binary") + chip(P.dvmfne_unit, "dvmfne service")
      + (P.build_tree ? "" : ` <span class="muted small">no build tree yet</span>`) + (s.dry_run ? ` <span class="pill blue">simulated</span>` : "");
    $("#fneBtn").disabled = s.state === "running";
    $("#fneBtn").title = !P.dvmfne_bin && !P.build_tree ? "Run Install / update DVM first" : "";
    const b = $("#instBadge");
    const what = s.kind === "fne" ? "dvmfne service" : "install";
    if (s.state === "running") { b.className = "pill amber"; b.textContent = s.kind === "fne" ? "installing dvmfne service…" : "installing…"; }
    else if (s.state === "ok") { b.className = "pill green"; b.textContent = s.dry_run ? `${what} simulated (dry-run)` : (s.kind === "fne" ? "dvmfne service installed" : "installed"); }
    else if (s.state === "failed") { b.className = "pill red"; b.textContent = "install failed — see log"; }
    else { b.className = s.installed ? "pill green" : "pill gray"; b.textContent = s.installed ? "dvmhost is installed" : "not installed yet"; }
    $("#instBtn").disabled = s.state === "running";
    if (s.state === "running") setTimeout(poll, 700); else polling = false;
  }
  $("#fneBtn").onclick = async () => {
    $("#fneMsg").textContent = "";
    try { await api("/api/install/fne", {method: "POST"}); since = 0; if (!polling) { polling = true; poll(); } }
    catch (e) { $("#fneMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $("#instBtn").onclick = async () => {
    try { await api(`/api/install/start?with_fne=${$("#instFne").checked}&clean_src=${$("#instClean").checked}`, {method: "POST"}); since = 0; if (!polling) { polling = true; poll(); } }
    catch (e) { toast(e.message, "err"); }
  };

  // init: serial ports + previous answers
  api("/api/serial-ports").then(r => {
    $("#ports").innerHTML = [...new Set([...r.ports, ...r.suggested])].map(p => `<option value="${esc(p)}">`).join("");
    const hat = r.detail && r.detail.likely_hat && !r.detail.sample ? ` · likely hat port: ${r.detail.likely_hat}` : "";
    if (r.detail && r.detail.sample && !r.ports.length) { $("#portHint").textContent = "Sample data here (demo / non-Pi) — click Detect serial ports to see the list."; return; }
    $("#portHint").textContent = (r.ports.length ? `Found: ${r.ports.join(", ")}` : "No serial devices detected right now — plug in the modem, or type the port.") + hat;
  }).catch(() => { $("#portHint").textContent = "Could not list serial ports — type the port."; });
  $("#detectPorts").onclick = async () => {
    const el = $("#portList"); el.innerHTML = '<div class="skeleton" style="height:90px"></div>';
    try {
      const r = await api("/api/serial-ports");
      el.innerHTML = renderPorts(r.detail, true);
      $$("[data-pick]", el).forEach(b => b.onclick = () => {
        $("#modem_port").value = b.dataset.pick; if ($("#modem_type").value !== "uart") $("#modem_type").value = "uart";
        toast(`Modem port set to ${b.dataset.pick}`, "ok");
      });
    } catch (e) { el.innerHTML = `<div class="empty">Could not scan serial ports: ${esc(e.message)}</div>`; }
  };
  api("/api/wizard/current").then(w => {
    if (!w || !w.system_name) return;
    for (const [k, v] of Object.entries(w)) { const el = $("#" + k); if (!el || v == null) continue; if (el.type === "checkbox") { if (k.startsWith("dmr_")) el.checked = !!v; } else el.value = v; }
    if (w.talkgroups) tgs = w.talkgroups;
    for (const g of ["mode", "network_mode", "topology"]) { if (w[g]) { choice[g] = w[g]; $$(`[data-group="${g}"] .choice`).forEach(c => c.classList.toggle("sel", c.dataset.val === w[g])); } }
    $("#remoteFields").hidden = choice.network_mode !== "remote_fne";
    applyTopology(); applyMode();
  });
  $("#joinBtn").onclick = async () => {
    $("#joinMsg").textContent = "Contacting control Pi…";
    try {
      const r = await api("/api/pairing/join", {method: "POST", body: {
        control_url: $("#j_control").value, code: $("#j_code").value, name: $("#j_name").value,
        modem_port: $("#j_port").value, requested_tx_mhz: +$("#j_tx").value || null,
        requested_peer_id: +$("#j_peer").value || null,
        ack_license: $("#j_ack_license").checked, ack_terms: $("#j_ack_terms").checked}});
      const s = r.summary;
      $("#joinMsg").innerHTML = `<span style="color:var(--green)">Joined ${esc(s.system_name)} as channel ${esc(s.channelNo)} (TX ${s.tx_mhz.toFixed(5)} / RX ${s.rx_mhz.toFixed(5)} MHz)${r.dry_run ? " — dry-run" : ""}.</span> <a href="/dashboard">Open dashboard →</a>`;
      toast(`Joined ${s.system_name} as channel ${s.channelNo}`, "ok");
    } catch (e) { $("#joinMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; toast(e.message, "err"); }
  };
  // "Load my previous system values" (ML105). UNVERIFIED mapping notes:
  //  - "System ID 105" is taken as written in config.yml (hex 0x105); NAC "105" decimal = 0x069.
  //  - channel "2-385"/"2-457": iden row 2 = 450 MHz base, 6.25 kHz, +5 MHz (iden_table.example.dat);
  //    dvmhost parses channelNo as hex, the user can pick decimal instead.
  //  - console peer 900004 is not configured here (consoles connect to dvmfne with the hub password).
  const PREV = {system_name: "ML105", callsign: "ML105", wacn: "A1055", sys_id: "105", nac: "069", rfss_id: "1", site_id: "1",
                peer_id: 900002, fne_peer_id: 900001, vc_peer_start: 900003, cc: 0x385, vc: 0x457, ccDec: 385, vcDec: 457};
  const chMHz = ch => 450 + ch * 0.00625;
  const fmtF = f => f.toFixed(5);
  $("#presetBtn").onclick = () => { $("#presetOpts").hidden = !$("#presetOpts").hidden; };
  $("#presetGo").onclick = () => {
    const dec = $('input[name="chfmt"]:checked').value === "dec";
    const cc = dec ? PREV.ccDec : PREV.cc;
    for (const k of ["system_name", "callsign", "wacn", "sys_id", "nac", "rfss_id", "site_id", "peer_id", "fne_peer_id", "vc_peer_start"]) $("#" + k).value = PREV[k];
    $("#tx_mhz").value = fmtF(chMHz(cc)); $("#rx_mhz").value = fmtF(chMHz(cc) + 5);
    setChoice("mode", "p25"); applyMode(); choice.topology = "trunked";
    $$('[data-group="topology"] .choice').forEach(c => c.classList.toggle("sel", c.dataset.val === "trunked"));
    applyTopology();
    tgs = [{tgid: 101, name: "ML105 Main", alias: "ML105 Main", parrot: false}]; renderTgs();
    presetRids = {radios: [{rid: 800001, alias: "", enabled: true}, {rid: 800002, alias: "", enabled: true}], lockdown: true};
    localStorage.setItem("ml_prev_vc_mhz", fmtF(chMHz(dec ? PREV.vcDec : PREV.vc)));
    $("#presetMsg").innerHTML = `<span style="color:var(--green)">Filled.</span> Control ${fmtF(chMHz(cc))} MHz. Radios 800001 & 800002 + lockdown will be saved with Setup. Console peer 900004 isn't set here — use it in your console app.`;
  };
  $("#jPreset").onclick = () => {
    $("#j_tx").value = localStorage.getItem("ml_prev_vc_mhz") || fmtF(chMHz(PREV.vc)); $("#j_peer").value = PREV.vc_peer_start;
  };
  applyMode(); poll(); show(location.hash === "#install" ? 1 : 0);
})();
