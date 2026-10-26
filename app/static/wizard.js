(() => {
  let step = 0; const N = 10;
  const choice = {mode: "p25", topology: "conventional", network_mode: "local_fne", purpose: "new"};
  let tgs = [{tgid: 1, name: "Talkgroup 1", alias: "Tactical", parrot: false},
             {tgid: 9990, name: "Parrot", alias: "Parrot/Testing", parrot: true}];
  let preview = null, curFile = null;

  function show(n) {
    step = Math.max(0, Math.min(N - 1, n));
    $$(".panel").forEach(p => p.classList.toggle("on", +p.dataset.step === step));
    $$("#steps li").forEach(li => { const i = +li.dataset.step; li.classList.toggle("on", i === step); li.classList.toggle("done", i < step); });
    $("#back").disabled = step === 0;
    $("#next").style.visibility = step === N - 1 ? "hidden" : "visible";
    if (step === 9) doPreview();
    if (step === 4) calc();
    $("#next").hidden = step === 0 && choice.purpose === "voice";
    window.scrollTo({top: 0, behavior: "smooth"});
  }
  $$("#steps li").forEach(li => li.onclick = () => show(+li.dataset.step));
  $("#back").onclick = () => show(step - 1);
  $("#next").onclick = () => { if (validateStep()) show(step + 1); };

  function validateStep() {
    $$(".err", $(`.panel[data-step="${step}"]`)).forEach(e => e.remove());
    const need = {3: ["system_name", "callsign"], 4: ["tx_mhz", "rx_mhz"]}[step] || [];
    let ok = true;
    for (const id of need) { const el = $("#" + id); if (!el.value.trim()) { ok = false; el.insertAdjacentHTML("afterend", `<div class="err">Required</div>`); } }
    return ok;
  }

  // choice cards
  $$(".choices").forEach(g => $$(".choice", g).forEach(c => c.onclick = (ev) => {
    if (ev.target.tagName === "A") return;
    if (c.classList.contains("off")) return;
    $$(".choice", g).forEach(x => x.classList.remove("sel")); c.classList.add("sel");
    choice[g.dataset.group] = c.dataset.val;
    if (g.dataset.group === "network_mode") $("#remoteFields").hidden = c.dataset.val !== "remote_fne";
    if (g.dataset.group === "topology") applyTopology();
    if (g.dataset.group === "purpose") { $("#joinBox").hidden = c.dataset.val !== "voice"; show(0); }
  }));
  function applyTopology() {
    const t = choice.topology === "trunked";
    $("#ccTag").hidden = !t; $("#trunkNet").hidden = !t;
    $$('[data-group="network_mode"] .choice').forEach(c => c.classList.toggle("off", t && c.dataset.val !== "local_fne"));
    if (t) { choice.network_mode = "local_fne"; $$('[data-group="network_mode"] .choice').forEach(c => c.classList.toggle("sel", c.dataset.val === "local_fne")); $("#remoteFields").hidden = true; }
  }

  // talkgroups
  function renderTgs() {
    $("#tgBody").innerHTML = tgs.map((t, i) => `<tr>
      <td><input type="number" min="1" max="65535" value="${esc(t.tgid)}" data-i="${i}" data-k="tgid"></td>
      <td><input value="${esc(t.name)}" data-i="${i}" data-k="name"></td>
      <td><input value="${esc(t.alias)}" data-i="${i}" data-k="alias"></td>
      <td><input type="checkbox" ${t.parrot ? "checked" : ""} data-i="${i}" data-k="parrot" style="width:auto"></td>
      <td><button class="btn danger" type="button" data-del="${i}" aria-label="Remove">✕</button></td></tr>`).join("");
    $$("#tgBody input").forEach(inp => inp.oninput = inp.onchange = () => {
      const t = tgs[+inp.dataset.i], k = inp.dataset.k;
      t[k] = k === "parrot" ? inp.checked : k === "tgid" ? +inp.value : inp.value; });
    $$("#tgBody [data-del]").forEach(b => b.onclick = () => { tgs.splice(+b.dataset.del, 1); renderTgs(); });
  }
  $("#tgAdd").onclick = () => { tgs.push({tgid: (Math.max(0, ...tgs.map(t => t.tgid).filter(x => x < 9990)) + 1), name: "", alias: "", parrot: false}); renderTgs(); };

  // frequency helper
  async function calc() {
    const tx = +$("#tx_mhz").value, rx = +$("#rx_mhz").value, box = $("#freqCalc");
    if (!tx || !rx) { box.hidden = true; return; }
    try {
      const r = await api("/api/calc/freq", {method: "POST", body: {tx_mhz: tx, rx_mhz: rx}});
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
      modem_type: v("modem_type"), modem_port: v("modem_port"), modem_speed: +v("modem_speed"),
      fne_address: v("fne_address"), fne_port: +v("fne_port"), fne_password: v("fne_password"), peer_id: +v("peer_id"),
      talkgroups: tgs.filter(t => t.tgid),
      ack_license: $("#ack_license").checked, ack_terms: $("#ack_terms").checked,
    };
  }

  async function doPreview() {
    $("#reviewErr").hidden = true; $("#fileView").textContent = "Generating…";
    try {
      preview = await api("/api/wizard/preview", {method: "POST", body: payload()});
      $("#reviewWarn").innerHTML = preview.warnings.map(w => `<div class="notice blue small" style="margin-bottom:8px">${esc(w)}</div>`).join("")
        + preview.errors.map(w => `<div class="notice small" style="margin-bottom:8px">${esc(w)}</div>`).join("");
      const names = Object.keys(preview.files); curFile = curFile && names.includes(curFile) ? curFile : names[0];
      $("#fileTabs").innerHTML = names.map(n => `<button type="button" class="${n === curFile ? "on" : ""}" data-f="${n}">${n}</button>`).join("");
      $$("#fileTabs button").forEach(b => b.onclick = () => { curFile = b.dataset.f; doPreview(); });
      $("#fileView").textContent = preview.files[curFile];
      $("#target").innerHTML = `Files will be written to <code>${esc(preview.target_dir)}</code>${preview.dry_run ? " (dry-run folder — nothing on the system is changed)" : ""}. Existing files are backed up as <code>*.bak</code>.`;
    } catch (e) { $("#reviewErr").hidden = false; $("#reviewErr").textContent = "Please fix: " + e.message; $("#fileView").textContent = ""; }
  }

  $("#saveBtn").onclick = async () => {
    $("#saveMsg").textContent = "Saving…";
    try {
      const r = await api("/api/wizard/apply", {method: "POST", body: payload()});
      $("#saveMsg").textContent = `Saved ${r.written.length} files${r.dry_run ? " (dry-run)" : ""}.`;
      $("#afterSave").hidden = false; $("#fneStart").hidden = choice.network_mode !== "local_fne";
      $("#pairLink").hidden = choice.topology !== "trunked";
    } catch (e) { $("#saveMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $$("[data-svc]").forEach(b => b.onclick = async () => {
    try { const r = await api(`/api/service/${b.dataset.svc}/${b.dataset.verb}`, {method: "POST"});
      $("#svcOut").textContent += `${r.cmd}: ${r.ok ? "ok" : "failed"}${r.dry_run ? " (dry-run)" : ""}\n${r.output || ""}`;
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
    const b = $("#instBadge");
    if (s.state === "running") { b.className = "pill amber"; b.textContent = "installing…"; }
    else if (s.state === "ok") { b.className = "pill green"; b.textContent = s.dry_run ? "install simulated (dry-run)" : "installed"; }
    else if (s.state === "failed") { b.className = "pill red"; b.textContent = "install failed — see log"; }
    else { b.className = s.installed ? "pill green" : "pill gray"; b.textContent = s.installed ? "dvmhost is installed" : "not installed yet"; }
    $("#instBtn").disabled = s.state === "running";
    if (s.state === "running") setTimeout(poll, 700); else polling = false;
  }
  $("#instBtn").onclick = async () => {
    try { await api(`/api/install/start?with_fne=${$("#instFne").checked}`, {method: "POST"}); since = 0; if (!polling) { polling = true; poll(); } }
    catch (e) { alert(e.message); }
  };

  // init: serial ports + previous answers
  api("/api/serial-ports").then(r => {
    $("#ports").innerHTML = [...new Set([...r.ports, ...r.suggested])].map(p => `<option value="${esc(p)}">`).join("");
    $("#portHint").textContent = r.ports.length ? `Found: ${r.ports.join(", ")}` : "No serial devices detected right now — plug in the modem, or type the port.";
  });
  api("/api/wizard/current").then(w => {
    if (!w || !w.system_name) return;
    for (const [k, v] of Object.entries(w)) { const el = $("#" + k); if (el && el.type !== "checkbox" && v != null) el.value = v; }
    if (w.talkgroups) tgs = w.talkgroups;
    for (const g of ["network_mode", "topology"]) { if (w[g]) { choice[g] = w[g]; $$(`[data-group="${g}"] .choice`).forEach(c => c.classList.toggle("sel", c.dataset.val === w[g])); } }
    $("#remoteFields").hidden = choice.network_mode !== "remote_fne";
    applyTopology(); renderTgs();
  });
  $("#joinBtn").onclick = async () => {
    $("#joinMsg").textContent = "Contacting control Pi…";
    try {
      const r = await api("/api/pairing/join", {method: "POST", body: {
        control_url: $("#j_control").value, code: $("#j_code").value, name: $("#j_name").value,
        modem_port: $("#j_port").value, requested_tx_mhz: +$("#j_tx").value || null,
        ack_license: $("#j_ack_license").checked, ack_terms: $("#j_ack_terms").checked}});
      const s = r.summary;
      $("#joinMsg").innerHTML = `<span style="color:var(--green)">Joined ${esc(s.system_name)} as channel ${esc(s.channelNo)} (TX ${s.tx_mhz.toFixed(5)} / RX ${s.rx_mhz.toFixed(5)} MHz)${r.dry_run ? " — dry-run" : ""}.</span> <a href="/dashboard">Open dashboard →</a>`;
    } catch (e) { $("#joinMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  renderTgs(); poll(); show(0);
})();
