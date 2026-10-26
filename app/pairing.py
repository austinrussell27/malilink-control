"""Trunking: control-channel Pi <-> voice-channel Pi pairing.  ** DESIGN + STUB **

Flow
----
1. On the CONTROL Pi (role=control) the user clicks "Add a voice channel":
     POST /api/pairing/codes            -> {"code": "K7Q-4MD", "expires": <epoch>}  (10 min, single use)
2. On the NEW Pi the user opens this same app, picks "Add a voice channel" mode and enters the
   control Pi's address (Tailscale name/IP) + code:
     POST /api/pairing/join  {"control_url": "http://mali-cc:8080", "code": "K7Q-4MD"}
   which calls, on the control Pi:
     POST /api/pairing/claim {"code","name","url","rpc_address","rpc_port"}
3. The control Pi allocates the next free channelNo on its iden row and answers with a bundle:
     {channelId, channelNo (hex), iden_table (text), site {nac, netId, sysId, rfssId, siteId},
      controlCh {rpcAddress, rpcPort, rpcPassword}, network {address, port, password, peerId},
      cluster_key}
   and records the VC in state["pairing"]["voice_channels"] + state["nodes"] (dashboard).
4. TODO(control config): append to system.config.voiceChNo in config.yml
     {channelId, channelNo, rpcAddress: <vc tailscale ip>, rpcPort: 9890, rpcPassword}
   then `systemctl restart dvmhost`.  (all keys verified in configs/config.example.yml)
5. TODO(voice config): the VC Pi writes its config.yml from the template with
     protocols.p25.control.enable=false, system.config.channelId/channelNo from the bundle,
     system.config.controlCh.{rpcAddress,rpcPort,rpcPassword,notifyEnable=true},
     network.{address,port,password,id} pointing at the control Pi's dvmfne,
     voiceChNo=[own channel], the shared iden_table.dat, and starts dvmhost.
6. TODO(control channel): control Pi itself needs protocols.p25.control.enable=true,
   dedicated=true (verify recommended CC flags with DVMProject docs before enabling).

UNVERIFIED: exact recommended CC/VC flag combination for dvmhost trunking - confirm against the
DVMProject wiki before implementing steps 4-6. Nothing here keys a transmitter.
"""
from __future__ import annotations

import secrets
import string
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import state
from .settings import settings

router = APIRouter(prefix="/api/pairing", tags=["pairing"])
CODE_TTL = 600
_ALPH = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1")


def _new_code() -> str:
    s = "".join(secrets.choice(_ALPH) for _ in range(6))
    return f"{s[:3]}-{s[3:]}"


@router.post("/codes")
def create_code():
    st = state.load()
    if st.get("role") not in ("control",) and not (settings.demo or settings.dry_run):
        raise HTTPException(409, "Only a control-channel Pi can issue pairing codes (trunking is coming next).")
    codes = {c: v for c, v in st["pairing"]["codes"].items() if v["expires"] > time.time()}
    code = _new_code()
    codes[code] = {"expires": time.time() + CODE_TTL}
    st["pairing"]["codes"] = codes
    state.save(st)
    return {"code": code, "expires": codes[code]["expires"]}


@router.get("/voice-channels")
def list_vcs():
    return {"voice_channels": state.load()["pairing"]["voice_channels"]}


class Claim(BaseModel):
    code: str
    name: str
    url: str
    rpc_address: str
    rpc_port: int = 9890


@router.post("/claim")
def claim(c: Claim):
    st = state.load()
    code = c.code.strip().upper()
    entry = st["pairing"]["codes"].pop(code, None)
    if not entry or entry["expires"] < time.time():
        state.save(st)
        raise HTTPException(403, "Invalid or expired pairing code")
    used = {1} | {v["channelNo"] for v in st["pairing"]["voice_channels"]}  # ch 1 = control channel
    ch = next(n for n in range(2, 4096) if n not in used)
    w = st.get("wizard") or {}
    key = secrets.token_urlsafe(24)
    vc = {"name": c.name, "url": c.url, "rpc_address": c.rpc_address, "rpc_port": c.rpc_port,
          "channelNo": ch, "paired_at": time.time()}
    st["pairing"]["voice_channels"].append(vc)
    st["nodes"].append({"name": c.name, "url": c.url, "role": "voice", "chNo": ch, "key": key})
    state.save(st)
    bundle = {
        "channelId": 1, "channelNo": format(ch, "X"),
        "site": {k: w.get(k) for k in ("nac", "wacn", "sys_id", "rfss_id", "site_id")},
        "controlCh": {"rpcAddress": "TODO: control Pi tailscale IP", "rpcPort": 9890,
                      "rpcPassword": (st.get("secrets") or {}).get("rpc_password")},
        "network": {"address": "TODO: control Pi tailscale IP", "port": w.get("fne_port", 62031),
                    "password": (st.get("secrets") or {}).get("fne_password"), "peerId": 100000 + ch},
        "iden_table": "TODO: copy of control Pi iden_table.dat",
        "cluster_key": key,
        "applied": False,
        "todo": ["control: append voiceChNo entry + restart dvmhost",
                 "voice: write VC config.yml from bundle + start dvmhost"],
    }
    # TODO(trunking): apply_vc_to_control_config(vc) -> ruamel edit of system.config.voiceChNo
    return bundle


class Join(BaseModel):
    control_url: str
    code: str
    name: str | None = None
    my_url: str | None = None
    rpc_address: str | None = None


@router.post("/join")
def join(j: Join):
    """Run on the NEW voice-channel Pi."""
    body = {"code": j.code, "name": j.name or settings.node_name,
            "url": j.my_url or f"http://{settings.node_name}:8080",
            "rpc_address": j.rpc_address or settings.node_name, "rpc_port": 9890}
    try:
        r = httpx.post(j.control_url.rstrip("/") + "/api/pairing/claim", json=body, timeout=5)
    except Exception as e:
        raise HTTPException(502, f"Could not reach control Pi: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.json().get("detail", r.text))
    bundle = r.json()
    st = state.load()
    st["role"] = "voice"
    st["pairing"]["joined"] = {"control_url": j.control_url, "bundle": bundle}
    state.save(st)
    # TODO(trunking): write_voice_channel_config(bundle) + systemd.action("dvmhost","start")
    return {"ok": True, "bundle": bundle, "applied": False,
            "note": "Paired. Writing the voice-channel config is coming next (see TODOs in app/pairing.py)."}
