// Radios page (RID manager)
let R = null, editing = null;

function render() {
  const q = $("#q").value.trim().toLowerCase();
  const rows = R.radios.filter(r => !q || String(r.rid).includes(q) || (r.alias || "").toLowerCase().includes(q));
  const ro = !R.managed_here;
  $("#tbl tbody").innerHTML = rows.map(r => `<tr class="${r.enabled ? "" : "blocked"}">
    <td class="mono">${r.rid}</td><td>${esc(r.alias) || '<span class="muted">—</span>'}</td>
    <td>${r.enabled ? '<span class="pill green">ALLOWED</span>' : '<span class="pill red">BLOCKED</span>'}</td>
    <td class="hide-sm mono muted">${esc(r.ip)}</td>
    <td class="acts">${ro ? "" : `<button class="btn sm" data-tog="${r.rid}">${r.enabled ? "Block" : "Allow"}</button>
      <button class="btn sm" data-ed="${r.rid}">Edit</button><button class="btn sm danger" data-del="${r.rid}" aria-label="Delete">✕</button>`}</td></tr>`).join("");
  sortTable($("#tbl"));
  $("#none").hidden = rows.length > 0;
  $("#none").textContent = R.radios.length ? "No radios match your search." : "No radios yet — add one above or import a CSV.";
  const pn = $("#protoNote");
  pn.hidden = R.protocol !== "nxdn";
  pn.textContent = "NXDN system: radio (unit) IDs are 16-bit, so only 1–65535 can be used.";
  $("#cTotal").textContent = R.counts.total; $("#cOn").textContent = R.counts.enabled; $("#cOff").textContent = R.counts.blocked;
  $("#lock").checked = R.lockdown; $("#lock").disabled = ro;
  $("#lockCard").classList.toggle("locked", R.lockdown);
  $("#lockText").innerHTML = R.lockdown
    ? "<b>On</b> — only radios listed as <b>Allowed</b> can use the system."
    : "<b>Off</b> — any radio can use the system except ones marked <b>Blocked</b>.";
  const mins = R.reload_minutes;
  $("#fileInfo").innerHTML = `<span class="mono">${esc(R.file)}</span>${R.file_exists ? "" : " (written when you finish Setup)"}<br>` +
    (mins ? `dvmhost/dvmfne re-read it every ${mins} min — edits apply without a restart.` :
     mins === 0 ? "Auto re-read is off (radio_id.time: 0) — restart dvmhost to apply edits." : "") +
    (R.demo ? " <span class='pill amber'>DEMO</span>" : "") + (R.dry_run ? " <span class='pill blue'>DRY-RUN</span>" : "");
  const p = $("#lockPending");
  p.hidden = !R.restart_pending;
  p.innerHTML = `Lockdown setting saved to config.yml. dvmhost only reads it at start-up — <button class="btn sm primary" id="rs">Restart dvmhost now</button>
    <span class="muted">(a restart drops calls in progress for a few seconds; it never keys the transmitter)</span>`;
  const rs = $("#rs"); if (rs) rs.onclick = () => doApply();
  $("#editCard").hidden = ro; $("#impBtn").hidden = ro;
  const vn = $("#voiceNote");
  vn.hidden = !ro;
  if (ro) vn.innerHTML = `This Pi is a <b>voice channel</b>. Radio IDs are managed on the control Pi${R.control_url ? ` (<a href="${esc(R.control_url)}/radios">${esc(R.control_url)}</a>)` : ""} and copied here automatically. The list below is read-only.`;
}

async function load() { R = await api("/api/radios"); render(); }
function result(d, msg) {
  R = d; render();
  const bad = (d.pushed || []).filter(p => !p.ok);
  if (bad.length) toast("Saved, but could not update voice channel(s): " + bad.map(b => b.node).join(", ") + ". They will get the list on the next change.", "warn", 6000);
  if (msg) toast(msg, "ok");
}

function resetForm() {
  editing = null; $("#f").reset(); $("#formTitle").textContent = "Add a radio"; $("#save").textContent = "Add radio";
  $("#cancel").hidden = true; $("#ferr").textContent = "";
}
$("#f").onsubmit = async (e) => {
  e.preventDefault(); $("#ferr").textContent = "";
  const body = {rid: Number($("#rid").value.trim()), alias: $("#alias").value.trim(), enabled: $("#en").value === "1", ip: $("#ip").value.trim()};
  if (!Number.isInteger(body.rid) || body.rid < 1 || body.rid > 16777215) { $("#ferr").textContent = "Radio ID must be a whole number from 1 to 16777215."; return; }
  if (/,/.test(body.alias)) { $("#ferr").textContent = "Commas are not allowed in names (rid_acl.dat is comma-separated)."; return; }
  try {
    const d = editing ? await api(`/api/radios/${editing}`, {method: "PUT", body}) : await api("/api/radios", {method: "POST", body});
    result(d, editing ? `Saved ${body.rid}` : `Added ${body.rid}`); resetForm();
  } catch (err) { $("#ferr").textContent = err.message; }
};
$("#cancel").onclick = resetForm;
$("#q").oninput = render;
$("#tbl").onclick = async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  try {
    if (b.dataset.del) {
      if (!await confirmDialog({title: `Delete radio ${b.dataset.del}?`, body: "It is removed from rid_acl.dat.", ok: "Delete", danger: true})) return;
      result(await api(`/api/radios/${b.dataset.del}`, {method: "DELETE"}), `Deleted ${b.dataset.del}`);
    } else if (b.dataset.tog) {
      const r = R.radios.find(x => x.rid == b.dataset.tog);
      result(await api(`/api/radios/${r.rid}`, {method: "PUT", body: {...r, enabled: !r.enabled}}));
    } else if (b.dataset.ed) {
      const r = R.radios.find(x => x.rid == b.dataset.ed);
      editing = r.rid; $("#rid").value = r.rid; $("#alias").value = r.alias || ""; $("#en").value = r.enabled ? "1" : "0"; $("#ip").value = r.ip || "";
      $("#formTitle").textContent = `Edit radio ${r.rid}`; $("#save").textContent = "Save"; $("#cancel").hidden = false;
      $("#editCard").scrollIntoView({behavior: "smooth", block: "center"}); $("#alias").focus();
    }
  } catch (err) { toast(err.message, "err"); }
};
$("#lock").onchange = async (e) => {
  const on = e.target.checked;
  if (on && !await confirmDialog({title: "Turn lockdown on?", body: "Only radios listed as Allowed will be able to use the system.", ok: "Turn on lockdown"})) { e.target.checked = false; return; }
  try { result(await api("/api/radios/lockdown", {method: "POST", body: {enabled: on}}), `Lockdown ${on ? "on" : "off"}`); }
  catch (err) { e.target.checked = !on; toast(err.message, "err"); }
};
async function doApply() {
  try { result(await api("/api/radios/apply", {method: "POST", body: {restart: true}}), "dvmhost restarted"); } catch (err) { toast(err.message, "err"); }
}
$("#impBtn").onclick = () => { $("#imp").hidden = !$("#imp").hidden; };
$("#impFile").onchange = async (e) => { const f = e.target.files[0]; if (f) $("#impText").value = await f.text(); };
$("#impGo").onclick = async () => {
  const csv = $("#impText").value; if (!csv.trim()) { $("#impOut").textContent = "Choose a file or paste rows first."; return; }
  const mode = $("#impMode").value;
  if (mode === "replace" && !await confirmDialog({title: "Replace the whole radio list?", body: "Every radio not in this file is removed.", ok: "Replace list", danger: true})) return;
  try {
    const d = await api("/api/radios/import", {method: "POST", body: {csv, mode}});
    $("#impOut").textContent = `Imported ${d.imported} radio(s).` + (d.errors.length ? `\nSkipped:\n${d.errors.join("\n")}` : "");
    result(d);
  } catch (err) { $("#impOut").textContent = err.message; }
};
load().catch(err => { $("#none").hidden = false; $("#none").textContent = err.message; });
