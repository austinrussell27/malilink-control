"""Radios page: RID (radio ID) manager -> dvmhost/dvmfne rid_acl.dat.

How DVM uses it (verified in /workspace/ref/dvmhost, see app/configgen/ridacl.py for details):
  * dvmhost reads system.radio_id.{file,time,acl} (src/host/Host.cpp). `acl: true` = lockdown
    (only listed + enabled RIDs may key up / register); `acl: false` = everyone except listed+disabled.
  * dvmfne reads system.radio_id.{file,time} and always builds the table with acl=true
    (src/fne/HostFNE.cpp). The FNE can push its whitelist/blacklist to peers that set
    network.updateLookups (common/network/Network.cpp) - we instead push our own copy to each paired
    voice-channel Pi so alias/enabled stay identical and the VC's `acl` flag matches.
  * Both re-read the file every `time` minutes (LookupTable.h thread; template value 2), so list
    edits apply without a restart. The `acl` flag is only read at start-up -> lockdown changes need a
    dvmhost restart (offered in the UI; restarting never keys the transmitter).
On a trunked system the control Pi (which also runs dvmfne) is the source of truth; voice-channel
Pis show a read-only copy. The FNE REST /rid/* and /reload-rids endpoints exist but are not used
(our REST client is read-only by design).
"""
from __future__ import annotations

import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from . import state
from .configgen import ridacl
from .configgen.generate import _yaml
from .services import systemd
from .settings import settings

router = APIRouter(prefix="/api/radios", tags=["radios"])

DEMO_RADIOS = [
    {"rid": 800001, "alias": "ML105 Unit 1", "enabled": True},
    {"rid": 800002, "alias": "ML105 Unit 2", "enabled": True},
    {"rid": 1234567, "alias": "Base", "enabled": True},
    {"rid": 3101001, "alias": "Mobile 1", "enabled": True},
    {"rid": 3101002, "alias": "Mobile 2", "enabled": True},
    {"rid": 3101007, "alias": "Portable 7", "enabled": True},
    {"rid": 3105555, "alias": "Lost portable", "enabled": False},
    {"rid": 3109999, "alias": "Test bench", "enabled": True},
]


def _etc_file():
    return settings.dvm_etc / "rid_acl.dat"


def _config_acl() -> Optional[bool]:
    p = settings.dvm_etc / "config.yml"
    try:
        return bool(_yaml().load(p.read_text())["system"]["radio_id"].get("acl", False))
    except Exception:
        return None


def reload_minutes() -> Optional[int]:
    p = settings.dvm_etc / "config.yml"
    try:
        return int(_yaml().load(p.read_text())["system"]["radio_id"].get("time", 0))
    except Exception:
        return None


def get(st: dict) -> dict:
    """Current RID state; first use imports an existing rid_acl.dat (or seeds demo data)."""
    r = st.get("radios")
    if r is None:
        radios: list[dict] = []
        f = _etc_file()
        if f.exists():
            radios = [x.model_dump() for x in ridacl.parse(f.read_text())]
        elif settings.demo:
            radios = [ridacl.Radio(**x).model_dump() for x in DEMO_RADIOS]
        r = {"list": radios, "lockdown": bool(_config_acl()), "updated": None}
        st["radios"] = r
    return r


def rid_bundle(st: dict) -> dict:
    r = get(st)
    return {"radios": r["list"], "lockdown": r["lockdown"]}


def managed_here(st: dict) -> bool:
    return st.get("role") != "voice"


def _write_file(st: dict) -> Optional[str]:
    """Write rid_acl.dat (shared by dvmhost + dvmfne on this Pi) once the system is set up."""
    if not (st.get("setup_complete") or _etc_file().exists() or settings.demo):
        return None
    header = (settings.template_dir / "rid_acl.example.dat").read_text()
    text = ridacl.render([ridacl.Radio(**x) for x in get(st)["list"]], header)
    p = _etc_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.with_suffix(".dat.bak").write_text(p.read_text())
    p.write_text(text)
    try:
        import os, pwd
        u = pwd.getpwnam("dvmhost")
        os.chown(p, u.pw_uid, u.pw_gid)
    except Exception:
        pass
    return str(p)


def _host_active() -> bool:
    try:
        return bool(systemd.status(settings.host_unit)["active"])
    except Exception:
        return False


def _push_to_vcs(st: dict, restart: bool = False) -> list[dict]:
    out = []
    if st.get("role") != "control":
        return out
    body = {**rid_bundle(st), "restart": restart}
    for n in st.get("nodes", []):
        if n.get("role") != "voice" or not n.get("url"):
            continue
        try:
            r = httpx.post(n["url"].rstrip("/") + "/api/radios/sync", json=body, timeout=8,
                           headers={"X-Malilink-Cluster-Key": n.get("key", "")})
            out.append({"node": n["name"], "ok": r.status_code == 200,
                        "detail": None if r.status_code == 200 else r.text[:200]})
        except Exception as e:
            out.append({"node": n["name"], "ok": False, "detail": str(e)[:200]})
    return out


def commit(st: dict, *, restart: bool = False) -> dict:
    """Persist + write file + set acl flag + push to VCs; optionally restart dvmhost."""
    r = get(st)
    r["updated"] = time.time()
    state.save(st)
    written = _write_file(st)
    acl_changed = False
    if (settings.dvm_etc / "config.yml").exists() and _config_acl() != r["lockdown"]:
        ridacl.set_acl_flag(settings.dvm_etc / "config.yml", r["lockdown"])
        acl_changed = True
        if _host_active():
            r["restart_pending"] = True
            state.save(st)
    restarted = None
    if restart:
        restarted = systemd.action(settings.host_unit, "restart") if _host_active() else {"ok": True, "skipped": "dvmhost not running"}
        r["restart_pending"] = False
        state.save(st)
    pushed = _push_to_vcs(st, restart=restart)
    return {"written": written, "acl_changed": acl_changed, "restart": restarted, "pushed": pushed}


def _info(st: dict) -> dict:
    r = get(st)
    lst = sorted(r["list"], key=lambda x: x["rid"])
    mins = reload_minutes()
    joined = (st.get("pairing", {}).get("joined") or {}).get("summary") or {}
    return {
        "radios": lst, "lockdown": r["lockdown"], "updated": r.get("updated"),
        "restart_pending": bool(r.get("restart_pending")), "managed_here": managed_here(st),
        "role": st.get("role"), "control_url": joined.get("control_url"),
        "file": str(_etc_file()), "file_exists": _etc_file().exists(), "reload_minutes": mins,
        "config_acl": _config_acl(), "host_active": _host_active(),
        "voice_nodes": [n["name"] for n in st.get("nodes", []) if n.get("role") == "voice"],
        "counts": {"total": len(lst), "enabled": sum(1 for x in lst if x["enabled"]),
                   "blocked": sum(1 for x in lst if not x["enabled"])},
        "demo": settings.demo, "dry_run": settings.dry_run,
        "protocol": protocol(st), "rid_max": RID_MAX.get(protocol(st), ridacl.MAX_RID),
    }


def protocol(st: dict) -> str:
    joined = (st.get("pairing", {}).get("joined") or {})
    if st.get("role") == "voice" and joined.get("bundle_full"):
        return joined["bundle_full"].get("mode", "p25")
    return (st.get("wizard") or {}).get("mode", "p25")


# NXDN unit IDs are 16-bit (dvmhost nxdn/lc/RTCH.cpp masks srcId/dstId with 0xFFFF); P25/DMR are 24-bit.
RID_MAX = {"p25": ridacl.MAX_RID, "dmr": ridacl.MAX_RID, "nxdn": 65535}


def _check_rid(st: dict, rid: int) -> None:
    p = protocol(st)
    if rid > RID_MAX.get(p, ridacl.MAX_RID):
        raise HTTPException(400, f"RID {rid} is too large for {p.upper()} (NXDN radio IDs are 1-65535).")


def _editable(st: dict) -> None:
    if not managed_here(st):
        raise HTTPException(409, "This Pi is a voice channel. Radio IDs are managed on the control Pi "
                                 "and copied here automatically.")


def _check_lockdown(r: dict) -> None:
    if r["lockdown"] and not any(x["enabled"] for x in r["list"]):
        raise HTTPException(400, "Lockdown with no enabled radios would block every radio. "
                                 "Add or enable at least one RID first, or turn lockdown off.")


@router.get("")
def list_radios():
    st = state.load()
    info = _info(st)
    state.save(st)
    return info


class RadioIn(ridacl.Radio):
    pass


@router.post("")
def add_radio(r: RadioIn):
    st = state.load(); _editable(st)
    lst = get(st)["list"]
    _check_rid(st, r.rid)
    if any(x["rid"] == r.rid for x in lst):
        raise HTTPException(409, f"RID {r.rid} is already in the list - edit it instead.")
    lst.append(ridacl.Radio(**r.model_dump()).model_dump())
    return {"ok": True, **commit(st), **_info(st)}


@router.put("/{rid}")
def edit_radio(rid: int, r: RadioIn):
    st = state.load(); _editable(st)
    rs = get(st)
    lst = rs["list"]
    idx = next((i for i, x in enumerate(lst) if x["rid"] == rid), None)
    if idx is None:
        raise HTTPException(404, f"RID {rid} not found")
    _check_rid(st, r.rid)
    if r.rid != rid and any(x["rid"] == r.rid for x in lst):
        raise HTTPException(409, f"RID {r.rid} already exists")
    lst[idx] = ridacl.Radio(**r.model_dump()).model_dump()
    _check_lockdown(rs)
    return {"ok": True, **commit(st), **_info(st)}


@router.delete("/{rid}")
def delete_radio(rid: int):
    st = state.load(); _editable(st)
    rs = get(st)
    before = len(rs["list"])
    rs["list"] = [x for x in rs["list"] if x["rid"] != rid]
    if len(rs["list"]) == before:
        raise HTTPException(404, f"RID {rid} not found")
    _check_lockdown(rs)
    return {"ok": True, **commit(st), **_info(st)}


class Lockdown(BaseModel):
    enabled: bool
    restart: bool = False


@router.post("/lockdown")
def set_lockdown(b: Lockdown):
    st = state.load(); _editable(st)
    rs = get(st)
    rs["lockdown"] = b.enabled
    _check_lockdown(rs)
    return {"ok": True, **commit(st, restart=b.restart), **_info(st)}


class Apply(BaseModel):
    restart: bool = True


@router.post("/apply")
def apply(b: Apply):
    """Re-write the file/flag, push to voice channels and (optionally) restart dvmhost now."""
    st = state.load(); _editable(st)
    _check_lockdown(get(st))
    return {"ok": True, **commit(st, restart=b.restart), **_info(st)}


class ImportBody(BaseModel):
    csv: str
    mode: str = "merge"  # merge | replace


@router.post("/import")
def import_csv(b: ImportBody):
    st = state.load(); _editable(st)
    radios, errors = ridacl.from_csv(b.csv)
    mx = RID_MAX.get(protocol(st), ridacl.MAX_RID)
    errors += [f"RID {r.rid}: too large for {protocol(st).upper()} (max {mx})" for r in radios if r.rid > mx]
    radios = [r for r in radios if r.rid <= mx]
    if not radios:
        raise HTTPException(400, {"message": "No valid rows found", "errors": errors[:20]})
    rs = get(st)
    cur = {} if b.mode == "replace" else {x["rid"]: x for x in rs["list"]}
    for r in radios:
        cur[r.rid] = r.model_dump()
    rs["list"] = list(cur.values())
    _check_lockdown(rs)
    return {"ok": True, "imported": len(radios), "errors": errors[:50], **commit(st), **_info(st)}


@router.get("/export.csv")
def export_csv():
    st = state.load()
    data = ridacl.to_csv([ridacl.Radio(**x) for x in get(st)["list"]])
    return Response(data, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="malilink-radios.csv"'})


@router.get("/rid_acl.dat", response_class=PlainTextResponse)
def preview_dat():
    st = state.load()
    header = (settings.template_dir / "rid_acl.example.dat").read_text()
    return ridacl.render([ridacl.Radio(**x) for x in get(st)["list"]], header)


class SyncBody(BaseModel):
    radios: list[ridacl.Radio]
    lockdown: bool = False
    restart: bool = False


@router.post("/sync")
def sync(b: SyncBody, request: Request):
    """Voice-channel Pi: receive the control Pi's list (cluster key required)."""
    st = state.load()
    key = (st.get("pairing", {}).get("joined") or {}).get("cluster_key")
    if st.get("role") != "voice" or not key or request.headers.get("X-Malilink-Cluster-Key") != key:
        raise HTTPException(403, "not a paired voice channel / bad cluster key")
    st["radios"] = {"list": [r.model_dump() for r in b.radios], "lockdown": b.lockdown,
                    "updated": time.time(), "synced_from_control": True}
    return {"ok": True, **commit(st, restart=b.restart)}
