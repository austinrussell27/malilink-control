"""Configured modem port + read-only modem status for the About / node pages and the wizard.

SAFETY: status comes only from dvmhost's GET /status (already on DvmRest's read-only allow-list).
Nothing here opens the serial port or sends anything to the modem, so it can never key the transmitter.
The `modem` object fields are the ones Host::getStatus() sets in dvmhost src/host/Host.cpp
(portType, modemPort, portSpeed, rxLevel, rx/txDCOffset, rx/txFrequency(Effective), protoVer, v24Connected,
gainMode on hotspots, ...); we pass through whatever dvmhost reports."""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from .. import state
from ..settings import settings
from . import serialports

# Read-only modem fields we show (subset of Host::getStatus() "modem"; anything missing is just omitted).
STATUS_FIELDS = ("portType", "modemPort", "portSpeed", "protoVer", "rxFrequency", "txFrequency",
                 "rxFrequencyEffective", "txFrequencyEffective", "rxLevel", "rxDCOffset", "txDCOffset",
                 "v24Connected", "gainMode")


def configured(st: dict | None = None) -> dict:
    """Modem settings saved by the wizard (or the voice-channel join) + what config.yml on disk says."""
    st = st if st is not None else state.load()
    if st.get("role") == "voice":
        loc = ((st.get("pairing") or {}).get("joined") or {}).get("local") or {}
        src = "voice-channel join"
    else:
        loc = st.get("wizard") or {}
        src = "Setup wizard"
    out = {"source": src if loc else None, "type": loc.get("modem_type"), "port": loc.get("modem_port"),
           "speed": loc.get("modem_speed"), "config_file": None, "config_port": None, "config_type": None}
    if settings.demo and not loc:
        out.update(source="demo sample", type="uart", port="/dev/ttyAMA0", speed=115200)
    f = Path(settings.dvm_etc) / "config.yml"
    if f.is_file():
        out["config_file"] = str(f)
        try:
            m = ((YAML(typ="safe").load(f.read_text()) or {}).get("system") or {}).get("modem") or {}
            proto = m.get("protocol") or {}
            out["config_type"] = proto.get("type")
            out["config_port"] = (proto.get("uart") or {}).get("port")
        except Exception as e:  # a hand-edited / broken file must not break the page
            out["config_error"] = str(e)
    return out


def ports() -> dict:
    """Serial-port scan, or a clearly-labelled sample in demo mode / dry-run on a non-Pi machine."""
    if settings.demo:
        return serialports.sample("demo mode (MALILINK_DEMO=1) — sample data, not this machine")
    r = serialports.scan()
    if settings.dry_run and r["pi_generation"] is None:
        s = serialports.sample(f"dry-run on a non-Pi machine ({settings.mode_reason}) — sample data")
        s["real_ports"] = r["ports"]  # still show what this machine really has
        return s
    return r


def status_from_node(node: dict | None, demo: bool) -> dict:
    if not node:
        return {"running": False, "note": "No status for this Pi.", "sample": demo}
    running = bool((node.get("host") or {}).get("active"))
    m = node.get("modem") or {}
    if not running:
        return {"running": False, "note": "dvmhost is not running — no live modem status.", "sample": demo}
    if not m:
        return {"running": True, "note": node.get("error") or "dvmhost is running but its REST API returned no modem info "
                "(REST disabled or password unknown).", "sample": demo}
    return {"running": True, "state": node.get("mode"), "tx": bool(node.get("tx")),
            "modem": {k: m[k] for k in STATUS_FIELDS if k in m}, "sample": demo,
            "note": "Sample data (demo mode)." if demo else "Read-only, from dvmhost GET /status."}


def local_info() -> dict:
    from ..data import get_source
    src = get_source()
    if settings.demo:
        snap = src.snapshot()
        node = snap["nodes"][0] if snap.get("nodes") else None
    else:
        node = src.local()["node"] if hasattr(src, "local") else None
    return {"node": settings.node_name, "configured": configured(), "scan": ports(),
            "status": status_from_node(node, settings.demo)}


def node_info(node_id: str) -> dict:
    """node_id "local"/own name -> this Pi; a paired Pi -> proxied (read-only) to its /api/local/modem."""
    if node_id in ("local", settings.node_name):
        return local_info()
    if settings.demo:
        from ..data import get_source
        snap = get_source().snapshot()
        node = next((n for n in snap["nodes"] if n["id"] == node_id or n["name"] == node_id), None)
        if node is None:
            return {"node": node_id, "error": "unknown Pi"}
        return {"node": node_id, "configured": {"source": "demo", "type": "uart", "port": (node.get("modem") or {}).get("modemPort"),
                                                "speed": 115200, "config_file": None, "config_port": None},
                "scan": serialports.sample("demo mode (MALILINK_DEMO=1) — sample data, not a real Pi"),
                "status": status_from_node(node, True)}
    peer = next((p for p in state.load().get("nodes", []) if p["name"] == node_id), None)
    if not peer:
        return {"node": node_id, "error": "unknown Pi"}
    import httpx
    try:
        r = httpx.get(peer["url"].rstrip("/") + "/api/local/modem", timeout=3.0,
                      headers={"X-Malilink-Cluster-Key": peer.get("key", "")})
        r.raise_for_status()
        d = r.json()
        d["node"] = node_id
        return d
    except Exception as e:
        return {"node": node_id, "error": f"could not reach {node_id}: {e}"}
