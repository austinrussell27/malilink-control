"""Trunking: control-channel Pi <-> voice-channel Pi pairing.

Flow
----
1. CONTROL Pi (set up with the wizard as P25, DMR or NXDN -> Trunked, role=control):
     POST /api/pairing/codes  -> {"code": "K7Q-4MD", "expires": <epoch>}   (10 min, single use)
2. NEW Pi, "Add a voice channel" mode (Setup welcome screen / Trunking page):
     POST /api/pairing/join {control_url, code, name, modem_*, requested_tx_mhz?, ack_license, ack_terms}
   which calls the control Pi:
     POST /api/pairing/claim {code, name, url, rpc_address?, requested_tx_mhz?}
3. Control Pi: allocates the next free channel number (step = bandwidth/spacing so channels don't
   overlap) or the requested frequency, appends the VC to its state, regenerates its config.yml with
   the new system.config.voiceChNo list, writes it and restarts dvmhost if it was running.
   Responds with a bundle: channel, iden row + iden_table.dat text, site IDs, talkgroups,
   RPC {address, port, password}, FNE {address, port, password, peerId}, cluster_key.
4. New Pi: resolves the control Pi's address to an IP, writes its own VC config.yml
   (controlCh -> control Pi RPC, network -> control Pi dvmfne), iden_table.dat and
   talkgroup_rules.yml, and sets role=voice. It does NOT start dvmhost by itself (safety: the user
   starts it from the dashboard).
DELETE /api/pairing/voice-channels/{chNo} removes a VC from the control Pi (rewrites + restarts).

Addresses: the control Pi records the VC's RPC address as the source IP of the claim request
(over Tailscale this is the VC's 100.x address) unless rpc_address is given. The VC uses the host
it reached the control Pi on. dvmhost resolves hostnames (common/network/udp/Socket::lookup), but we
store IPs anyway. Nothing here keys a transmitter.
"""
from __future__ import annotations

import secrets
import socket
import string
import time
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import radios, state
from .configgen import iden
from .configgen.generate import (MODES, RPC_PORT, site_dict, VoiceLocal, WizardInput, channel_for_freq, channel_step, generate,
                                 generate_voice_channel, next_free_channel, write_files)
from .services import systemd
from .settings import settings

router = APIRouter(prefix="/api/pairing", tags=["pairing"])
CODE_TTL = 600
_ALPH = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1")


def _new_code() -> str:
    s = "".join(secrets.choice(_ALPH) for _ in range(6))
    return f"{s[:3]}-{s[3:]}"


def _is_control(st: dict) -> bool:
    return st.get("role") == "control" and (st.get("wizard") or {}).get("topology") == "trunked"


def apply_control_config(st: dict) -> dict:
    """Regenerate + write the control Pi's files with the current voice-channel list, then restart
    dvmhost if it is running. Returns {"written": [...], "restart": {...}|None}."""
    w = WizardInput(**st["wizard"])
    g = generate(w, st.get("secrets"), st["pairing"]["voice_channels"], rid=radios.rid_bundle(st))
    written = write_files(g)
    restart = None
    try:
        if systemd.status(settings.host_unit)["active"]:
            # dvmhost has no config reload; a restart is required for voiceChNo changes.
            restart = systemd.action(settings.host_unit, "restart")
    except Exception as e:  # pragma: no cover
        restart = {"ok": False, "error": str(e)}
    return {"written": written, "restart": restart}


@router.post("/codes")
def create_code():
    st = state.load()
    if not _is_control(st):
        raise HTTPException(409, "Set this Pi up as a trunked control channel first (Setup -> Trunked).")
    codes = {c: v for c, v in st["pairing"]["codes"].items() if v["expires"] > time.time()}
    code = _new_code()
    codes[code] = {"expires": time.time() + CODE_TTL}
    st["pairing"]["codes"] = codes
    state.save(st)
    return {"code": code, "expires": codes[code]["expires"]}


@router.get("/voice-channels")
def list_vcs():
    st = state.load()
    out = []
    if _is_control(st):
        g = generate(WizardInput(**st["wizard"]), st.get("secrets"), st["pairing"]["voice_channels"],
                     rid=radios.rid_bundle(st))
        e = g.plan.entry
        cc = {"name": settings.node_name + " (control)", "channelNo": g.plan.channel_no,
              "tx_mhz": g.plan.tx_hz / 1e6, "rx_mhz": g.plan.rx_hz / 1e6, "control": True}
        out = [cc] + [{**v, "tx_mhz": e.tx_for(v["channelNo"]) / 1e6, "rx_mhz": e.rx_for(v["channelNo"]) / 1e6}
                      for v in st["pairing"]["voice_channels"]]
    return {"role": st.get("role"), "channels": out, "voice_channels": st["pairing"]["voice_channels"],
            "joined": (st["pairing"].get("joined") or {}).get("summary")}


class Claim(BaseModel):
    code: str
    name: str
    url: Optional[str] = None   # VC's Malinois Link Control URL; default http://<source ip>:<app_port>
    app_port: int = 8080
    rpc_address: Optional[str] = None
    rpc_port: int = RPC_PORT
    requested_tx_mhz: Optional[float] = None
    requested_peer_id: Optional[int] = None


@router.post("/claim")
def claim(c: Claim, request: Request):
    st = state.load()
    if not _is_control(st):
        raise HTTPException(409, "That Pi is not a trunked control channel.")
    code = c.code.strip().upper()
    entry = st["pairing"]["codes"].pop(code, None)
    state.save(st)  # consume the code even if the rest fails
    if not entry or entry["expires"] < time.time():
        raise HTTPException(403, "Invalid or expired pairing code")
    w = WizardInput(**st["wizard"])
    g = generate(w, st.get("secrets"), st["pairing"]["voice_channels"], rid=radios.rid_bundle(st))
    plan, sec = g.plan, g.secrets
    used = {plan.channel_no} | {v["channelNo"] for v in st["pairing"]["voice_channels"]}
    try:
        if c.requested_tx_mhz:
            ch = channel_for_freq(plan.entry, c.requested_tx_mhz, MODES[w.mode]["max_ch"])
            if ch in used:
                raise ValueError("That frequency is already used by another channel in this system")
        else:
            ch = next_free_channel(plan.channel_no, used, channel_step(plan.entry), MODES[w.mode]["max_ch"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    if any(v["name"] == c.name for v in st["pairing"]["voice_channels"]):
        raise HTTPException(409, f"A voice channel named {c.name} is already paired")
    rpc_addr = c.rpc_address or (request.client.host if request.client else None)
    if not rpc_addr:
        raise HTTPException(400, "Could not determine the voice channel's address")
    # network peer ID: explicit request > wizard vc_peer_start (+ n-th VC) > peer_id + channelNo
    taken = {w.peer_id, w.fne_peer_id} | {v.get("peerId") for v in st["pairing"]["voice_channels"]}
    if c.requested_peer_id:
        peer_id = c.requested_peer_id
    elif w.vc_peer_start:
        peer_id = w.vc_peer_start
        while peer_id in taken:
            peer_id += 1
    else:
        peer_id = w.peer_id + ch
    if peer_id in taken:
        raise HTTPException(409, f"Peer ID {peer_id} is already used in this system")
    key = secrets.token_urlsafe(24)
    vc_url = c.url or f"http://{request.client.host if request.client else rpc_addr}:{c.app_port}"
    vc = {"name": c.name, "url": vc_url, "rpc_address": rpc_addr, "rpc_port": c.rpc_port,
          "channelNo": ch, "peerId": peer_id, "paired_at": time.time()}
    st["pairing"]["voice_channels"].append(vc)
    st["nodes"] = [n for n in st["nodes"] if n["name"] != c.name] + [
        {"name": c.name, "url": vc_url, "role": "voice", "chNo": ch, "key": key}]
    state.save(st)
    applied = apply_control_config(st)
    e = plan.entry
    cc_host = request.url.hostname  # the address the VC used to reach us
    return {
        "channelNo": ch, "channelId": e.channel_id,
        "tx_mhz": e.tx_for(ch) / 1e6, "rx_mhz": e.rx_for(ch) / 1e6,
        "iden": {"channel_id": e.channel_id, "base_freq": e.base_freq, "spacing_khz": e.spacing_khz,
                 "offset_mhz": e.offset_mhz, "bandwidth_khz": e.bandwidth_khz},
        "iden_table": g.files["iden_table.dat"],
        "mode": w.mode,
        "site": site_dict(w),
        "system_name": w.system_name, "callsign": w.callsign, "location": w.location,
        "talkgroups": [t.model_dump() for t in w.talkgroups],
        "rpc": {"address": cc_host, "port": RPC_PORT, "password": sec["rpc_password"]},
        "network": {"address": cc_host, "port": w.fne_port, "password": sec["fne_password"],
                    "peerId": peer_id},
        "rid": radios.rid_bundle(st),
        "cluster_key": key,
        "control": {"name": settings.node_name, "restart": applied["restart"]},
    }


@router.delete("/voice-channels/{ch_no}")
def remove_vc(ch_no: int):
    return unpair_vc(ch_no)


class Join(BaseModel):
    control_url: str
    code: str
    name: Optional[str] = None
    my_url: Optional[str] = None
    rpc_address: Optional[str] = None
    requested_tx_mhz: Optional[float] = None
    requested_peer_id: Optional[int] = None
    modem_type: str = "uart"
    modem_port: str = "/dev/ttyUSB0"
    modem_speed: int = 115200
    ack_license: bool = False
    ack_terms: bool = False


def _resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except Exception:
        return host  # dvmhost can resolve hostnames itself (Socket::lookup)


@router.post("/join")
def join(j: Join, request: Request):
    """Run on the NEW voice-channel Pi."""
    if not (j.ack_license and j.ack_terms):
        raise HTTPException(400, "Please confirm the licensing and DVMProject terms first.")
    st = state.load()
    if st.get("role") == "control":
        raise HTTPException(409, "This Pi is a control channel; use a separate Pi for each voice channel.")
    name = (j.name or settings.node_name).strip()
    try:
        local = VoiceLocal(name=name, modem_type=j.modem_type, modem_port=j.modem_port, modem_speed=j.modem_speed,
                           ack_license=j.ack_license, ack_terms=j.ack_terms)
    except Exception as e:
        raise HTTPException(422, str(e))
    control_url = j.control_url.strip().rstrip("/")
    if "://" not in control_url:
        control_url = "http://" + control_url
    body = {"code": j.code, "name": name, "url": j.my_url, "app_port": request.url.port or 8080,
            "rpc_address": j.rpc_address, "requested_tx_mhz": j.requested_tx_mhz,
            "requested_peer_id": j.requested_peer_id}
    try:
        r = httpx.post(control_url + "/api/pairing/claim", json=body, timeout=15)
    except Exception as e:
        raise HTTPException(502, f"Could not reach the control Pi: {e}")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    bundle = r.json()
    # Use an IP for the control Pi in our config (the host we reached it on).
    cc_ip = _resolve(urlparse(control_url).hostname or bundle["rpc"]["address"])
    bundle["rpc"]["address"] = cc_ip
    bundle["network"]["address"] = cc_ip
    g = generate_voice_channel(bundle, local, st.get("rest_password"))
    written = write_files(g)
    summary = {"control_url": control_url, "control_ip": cc_ip, "channelNo": bundle["channelNo"],
               "tx_mhz": bundle["tx_mhz"], "rx_mhz": bundle["rx_mhz"], "system_name": bundle["system_name"],
               "name": name, "mode": bundle.get("mode", "p25")}
    st.update(role="voice", setup_complete=True, rest_password=g.secrets["rest_password"], fne_rest_password=None,
              secrets={**(st.get("secrets") or {}), "rest_password": g.secrets["rest_password"]})
    st["pairing"]["joined"] = {"summary": summary, "cluster_key": bundle["cluster_key"],
                               "ack_license": j.ack_license, "ack_terms": j.ack_terms,
                               "bundle": {k: v for k, v in bundle.items() if k not in ("rpc", "network")},
                               # full bundle + local answers so configs can be re-written later
                               # (e.g. paired while in dry-run); same file already holds our secrets
                               "bundle_full": bundle, "local": local.model_dump()}
    rid = bundle.get("rid") or {}
    st["radios"] = {"list": rid.get("radios") or [], "lockdown": bool(rid.get("lockdown")),
                    "updated": time.time(), "synced_from_control": True}
    state.save(st)
    return {"ok": True, "written": written, "summary": summary, "warnings": g.warnings, "dry_run": settings.dry_run,
            "note": "Voice channel configured. Start dvmhost from the dashboard when you're ready to go on the air."}


# ----------------------------------------------------------------- unpair / reset
CONFIG_FILES = ["config.yml", "iden_table.dat", "talkgroup_rules.yml", "rid_acl.dat", "fne-config.yml",
                "adj_site_map.yml", "peer_list.dat"]


def _stop_services(units) -> list[dict]:
    out = []
    for u in units:
        try:
            if systemd.status(u)["active"]:
                out.append(systemd.action(u, "stop"))
        except Exception as e:  # pragma: no cover
            out.append({"ok": False, "cmd": f"systemctl stop {u}", "error": str(e)})
    return out


def reset_local(reason: str) -> dict:
    """Return this Pi to a fresh, unconfigured state (backups kept):
    stop dvmhost/dvmfne, move generated configs to *.bak, copy state.json to state.json.bak, reset state."""
    import json as _json
    import shutil
    stopped = _stop_services([settings.host_unit, settings.fne_unit])
    backed = []
    for name in CONFIG_FILES:
        p = settings.dvm_etc / name
        if p.exists():
            bak = p.with_suffix(p.suffix + ".bak")
            shutil.move(str(p), str(bak))
            backed.append(str(bak))
    sp = settings.data_dir / "state.json"
    if sp.exists():
        shutil.copyfile(sp, settings.data_dir / "state.json.bak")
    (settings.data_dir / "config_written.json").unlink(missing_ok=True)
    state.save(_json.loads(_json.dumps(state.DEFAULT)))
    return {"ok": True, "reason": reason, "stopped": stopped, "backed_up": backed,
            "state_backup": str(settings.data_dir / "state.json.bak")}


def _remove_vc(st: dict, ch_no: int) -> Optional[dict]:
    vcs = st["pairing"]["voice_channels"]
    gone = next((v for v in vcs if v["channelNo"] == ch_no), None)
    if not gone:
        return None
    node = next((n for n in st["nodes"] if n.get("name") == gone["name"]), None)
    st["pairing"]["voice_channels"] = [v for v in vcs if v["channelNo"] != ch_no]
    st["nodes"] = [n for n in st["nodes"] if n.get("name") != gone["name"]]
    state.save(st)
    return {"vc": gone, "node": node}


def _tell_vc_released(node: Optional[dict]) -> dict:
    if not node or not node.get("url"):
        return {"ok": False, "detail": "no address for that Pi"}
    try:
        r = httpx.post(node["url"].rstrip("/") + "/api/pairing/released", timeout=6,
                       headers={"X-Malilink-Cluster-Key": node.get("key", "")})
        return {"ok": r.status_code == 200, "detail": None if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        return {"ok": False, "detail": f"unreachable: {str(e)[:150]}"}


@router.post("/voice-channels/{ch_no}/unpair")
def unpair_vc(ch_no: int):
    """Control Pi: remove a voice channel, rewrite our configs, and best-effort release the voice Pi."""
    st = state.load()
    if not _is_control(st):
        raise HTTPException(409, "Not a control channel")
    r = _remove_vc(st, ch_no)
    if not r:
        raise HTTPException(404, "No such voice channel")
    applied = apply_control_config(st)
    return {"ok": True, "removed": r["vc"]["name"], "voice_pi": _tell_vc_released(r["node"]), **applied}


@router.post("/released")
def released(request: Request):
    """Voice Pi: the control Pi removed us -> reset to a fresh state (cluster key required)."""
    st = state.load()
    key = (st.get("pairing", {}).get("joined") or {}).get("cluster_key")
    if st.get("role") != "voice" or not key or request.headers.get("X-Malilink-Cluster-Key") != key:
        raise HTTPException(403, "not a paired voice channel / bad cluster key")
    return reset_local("released by control Pi")


@router.post("/vc-release")
def vc_release(request: Request):
    """Control Pi: a voice Pi tells us it is leaving (authenticated by its cluster key)."""
    st = state.load()
    key = request.headers.get("X-Malilink-Cluster-Key")
    node = next((n for n in st.get("nodes", []) if key and n.get("key") == key and n.get("role") == "voice"), None)
    if not _is_control(st) or not node:
        raise HTTPException(403, "unknown voice channel")
    vc = next((v for v in st["pairing"]["voice_channels"] if v["name"] == node["name"]), None)
    if vc:
        _remove_vc(st, vc["channelNo"])
    else:
        st["nodes"] = [n for n in st["nodes"] if n is not node]
        state.save(st)
    return {"ok": True, "removed": node["name"], **apply_control_config(state.load())}


class ResetBody(BaseModel):
    notify: bool = True  # best-effort tell the other Pi(s); reset happens even if they're offline


@router.post("/reset")
def reset_this_pi(b: ResetBody):
    """'Unpair this Pi' (voice) / 'Unpair & reset this Pi to a fresh start' (any role)."""
    st = state.load()
    notified: list[dict] = []
    if b.notify and st.get("role") == "voice":
        j = st["pairing"].get("joined") or {}
        url = (j.get("summary") or {}).get("control_url")
        if url and j.get("cluster_key"):
            try:
                r = httpx.post(url.rstrip("/") + "/api/pairing/vc-release", timeout=6,
                               headers={"X-Malilink-Cluster-Key": j["cluster_key"]})
                notified.append({"node": url, "ok": r.status_code == 200,
                                 "detail": None if r.status_code == 200 else r.text[:200]})
            except Exception as e:
                notified.append({"node": url, "ok": False, "detail": f"unreachable: {str(e)[:150]}"})
    elif b.notify and st.get("role") == "control":
        for n in st.get("nodes", []):
            if n.get("role") == "voice":
                notified.append({"node": n["name"], **_tell_vc_released(n)})
    return {**reset_local("reset from this Pi"), "previous_role": st.get("role"), "notified": notified}
