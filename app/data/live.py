"""Live data: systemd + dvmhost/dvmfne REST + log files on this Pi, plus other Pis' Malinois
Link Control instances (GET <node url>/api/local/snapshot) for multi-Pi systems."""
from __future__ import annotations

import time

import httpx

from .. import state
from ..services import systemd
from ..settings import settings
from .base import DataSource, state_name
from .dvmrest import DvmRest
from .logparse import log_paths, parse_activity, tail


def site_info(st: dict) -> dict:
    """Protocol + site identifiers from our own saved config (dvmhost's /status has no colorCode/RAN)."""
    w = st.get("wizard") or {}
    joined = ((st.get("pairing") or {}).get("joined") or {})
    if st.get("role") == "voice" and joined.get("bundle_full"):
        b = joined["bundle_full"]
        proto, site = b.get("mode", "p25"), b.get("site", {})
    else:
        proto = w.get("mode", "p25")
        site = {"nac": w.get("nac", "293"), "color_code": w.get("color_code", 1), "ran": w.get("ran", 1),
                "dmr_slot1": w.get("dmr_slot1", True), "dmr_slot2": w.get("dmr_slot2", True)}
    if proto == "dmr":
        slots = [str(n) for n in (1, 2) if site.get(f"dmr_slot{n}", True)]
        label = f"CC {site.get('color_code', 1)} · TS {'+'.join(slots)}"
    elif proto == "nxdn":
        label = f"RAN {site.get('ran', 1)}"
    else:
        label = f"NAC {site.get('nac', '293')}"
    return {"protocol": proto.upper(), "site_label": label}


class LiveSource(DataSource):
    def __init__(self) -> None:
        self._host: DvmRest | None = None
        self._fne: DvmRest | None = None

    def _clients(self):
        st = state.load()
        if st.get("rest_password") and (self._host is None or self._host.password != st["rest_password"]):
            self._host = DvmRest("http://127.0.0.1:9990", st["rest_password"])
        if st.get("fne_rest_password") and (self._fne is None or self._fne.password != st["fne_rest_password"]):
            self._fne = DvmRest("http://127.0.0.1:9991", st["fne_rest_password"])
        return st

    def local(self) -> dict:
        st = self._clients()
        role = st.get("role") or "conventional"
        node = {"id": "local", "name": settings.node_name, "role": role, "reachable": True, "error": None,
                "host": systemd.status(settings.host_unit), "fne": None, "mode": "Offline", "tx": False,
                "modem": {}, "channelId": None, "channelNo": None, "nac": None, "peerId": None,
                "lastSrcId": None, "lastDstId": None, "dry_run": settings.dry_run}
        w = st.get("wizard") or {}
        node.update(site_info(st))
        if role != "voice" and (w.get("network_mode") == "local_fne" or w.get("topology") == "trunked"):
            node["fne"] = systemd.status(settings.fne_unit)
        grants, affs = [], []
        if node["host"]["active"] and self._host:
            try:
                s = self._host.get("/status")
                node.update(mode=state_name(s.get("state")), tx=bool(s.get("tx")),
                            channelId=s.get("channelId"), channelNo=s.get("channelNo"),
                            nac=format(s["p25NAC"], "X") if "p25NAC" in s else None,
                            peerId=s.get("peerId"), lastSrcId=s.get("lastSrcId"), lastDstId=s.get("lastDstId"),
                            modem={k: s.get("modem", {}).get(k) for k in
                                   ("portType", "modemPort", "rxFrequency", "txFrequency", "protoVer")})
                for ch in s.get("vcChannels", []):  # populated on a P25/DMR/NXDN control channel
                    if ch.get("lastDstId"):
                        grants.append({"node": node["name"], "channelNo": ch.get("channelNo"),
                                       "srcId": ch.get("lastSrcId"), "dstId": ch.get("lastDstId"),
                                       "tx": ch.get("tx", False)})
                # /status reports which protocol objects exist (RESTAPI.cpp restAPI_GetStatus)
                for proto in ("p25", "dmr", "nxdn"):
                    if s.get(f"{proto}Enabled"):
                        affs += [dict(a, mode=proto.upper()) for a in
                                 self._host.get(f"/{proto}/report-affiliations").get("affiliations", [])]
            except Exception as e:
                node["error"] = f"REST: {e}"
        _, act = log_paths(settings.dvm_log_dir, "DVM")
        recent, talking = parse_activity(tail(act, 400), node["name"])
        if not node["host"]["active"]:
            talking = []
        for t in talking:
            t["channelNo"] = node["channelNo"]
        return {"node": node, "talking": talking, "grants": grants, "affiliations": affs, "recent": recent}

    def snapshot(self) -> dict:
        loc = self.local()
        nodes, talking, grants, affs, recent = [loc["node"]], loc["talking"], loc["grants"], loc["affiliations"], loc["recent"]
        for peer in state.load().get("nodes", []):
            try:
                r = httpx.get(peer["url"].rstrip("/") + "/api/local/snapshot", timeout=2.0,
                              headers={"X-Malilink-Cluster-Key": peer.get("key", "")})
                r.raise_for_status()
                d = r.json()
                d["node"]["id"] = peer["name"]
                nodes.append(d["node"])
                talking += d.get("talking", [])
                recent += d.get("recent", [])
            except Exception as e:
                nodes.append({"id": peer["name"], "name": peer["name"], "role": peer.get("role", "voice"),
                              "reachable": False, "error": str(e), "host": {"active": False}, "mode": "Unreachable"})
        recent.sort(key=lambda e: e["ts"], reverse=True)
        return {"demo": False, "ts": time.time(), "nodes": nodes, "talking": talking, "grants": grants,
                "affiliations": affs, "recent": recent[:100]}

    def logs(self, node_id: str, kind: str = "system", lines: int = 200) -> list[str]:
        if node_id != "local":
            peer = next((p for p in state.load().get("nodes", []) if p["name"] == node_id), None)
            if not peer:
                return []
            try:
                r = httpx.get(peer["url"].rstrip("/") + f"/api/local/logs?kind={kind}&lines={lines}", timeout=2.0,
                              headers={"X-Malilink-Cluster-Key": peer.get("key", "")})
                return r.json().get("lines", [])
            except Exception as e:
                return [f"(could not reach {node_id}: {e})"]
        root = "DVMFNE" if kind == "fne" else "DVM"
        sysf, act = log_paths(settings.dvm_log_dir, root)
        return tail(act if kind == "activity" else sysf, lines)
