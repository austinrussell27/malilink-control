"""Demo data: a simulated 3-Pi trunked site (control channel + 2 voice channels) with
calls, grants, affiliations and activity-log lines in the real dvmhost formats.

Protocol: MALILINK_DEMO_PROTOCOL=p25|dmr|nxdn, otherwise the protocol saved by the Setup wizard
(default P25). DMR voice channels carry two calls at once (timeslots 1 and 2); NXDN uses 6.25 kHz
channels. Activity lines match the strings in dvmhost's dmr/nxdn/p25 Voice.cpp (see logparse.py);
DMR/NXDN *system* log lines are illustrative only (marked "demo")."""
from __future__ import annotations

import os
import random
import time
from collections import deque
from datetime import datetime

NODES = [
    {"id": "mali-cc", "name": "mali-cc", "role": "control", "channelNo": 1, "rx": 449500000, "tx": 444500000},
    {"id": "mali-vc1", "name": "mali-vc1", "role": "voice", "channelNo": 2, "rx": 449512500, "tx": 444512500},
    {"id": "mali-vc2", "name": "mali-vc2", "role": "voice", "channelNo": 3, "rx": 449525000, "tx": 444525000},
]
TGS = {1: "Tactical", 2: "Ops", 9990: "Parrot", 100: "Events"}
NXDN_NODES_STEP = 6250  # NXDN channels are 6.25 kHz apart


def demo_protocol() -> str:
    p = (os.environ.get("MALILINK_DEMO_PROTOCOL") or "").lower()
    if p in ("p25", "dmr", "nxdn"):
        return p
    try:
        from .. import state
        m = (state.load().get("wizard") or {}).get("mode")
        return m if m in ("p25", "dmr", "nxdn") else "p25"
    except Exception:
        return "p25"


def _start_msg(proto: str, srcm: str, slot: int | None, src: int, dst: int) -> str:
    word = "RF" if srcm == "RF" else "network"
    if proto == "dmr":
        return f"Slot {slot} {word} voice header from {src} to TG {dst}"
    return f"{word} voice transmission from {src} to TG {dst}"


def _end_msg(proto: str, srcm: str, slot: int | None, dur: float, ber: float) -> str:
    if proto == "dmr":
        if srcm == "RF":
            return f"Slot {slot} RF end of voice transmission, {dur:.1f} seconds, BER: {ber:.1f}%"
        return f"Slot {slot} network end of voice transmission, {dur:.1f} seconds, 0% packet loss, BER: 0.0%"
    if srcm == "RF":
        return f"RF end of transmission, {dur:.1f} seconds, BER: {ber:.1f}%"
    if proto == "nxdn":
        return f"network end of transmission, {dur:.1f} seconds"
    return f"network end of transmission, {dur:.1f} seconds, 0% packet loss"
RIDS = [1234567, 3101001, 3101002, 3101007, 3105555, 3109999]


def _ts(t: float) -> str:
    d = datetime.fromtimestamp(t)
    return d.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(d.microsecond / 1000):03d}"


from .base import DataSource


class DemoSource(DataSource):
    def __init__(self) -> None:
        self._reset(demo_protocol())

    def _reset(self, proto: str) -> None:
        self.proto = proto
        self.label = proto.upper()
        self.t0 = time.time()
        self.rng = random.Random(42)
        self.calls: dict[tuple, dict] = {}   # (chNo, slot|None) -> call
        self.recent: deque = deque(maxlen=100)
        self._sys = {n["id"]: deque(maxlen=500) for n in NODES}
        self.act = {n["id"]: deque(maxlen=500) for n in NODES}
        self.down: set[str] = set()
        self.last_tick = time.time() - 120
        # NXDN unit IDs are 16-bit, so use small IDs for the NXDN demo
        self.rids = [r % 60000 + 1000 for r in RIDS] if proto == "nxdn" else list(RIDS)
        self.tscc_slot = 1
        self.affs = [{"srcId": r, "grpId": self.rng.choice(list(TGS)), "mode": self.label} for r in self.rids]
        for n in NODES:
            self._log(n["id"], "I", "HOST", f"Malinois demo - dvmhost started, channel {n['channelNo']}", self.last_tick)
        self._advance(time.time())

    def _log(self, nid, lvl, mod, msg, t):
        self._sys[nid].append(f"{lvl}: {_ts(t)} ({mod}) {msg}")

    def _advance(self, now: float) -> None:
        t = self.last_tick
        while t < now:
            t += 1.0
            # end calls
            for key, c in list(self.calls.items()):
                if t >= c["end"]:
                    ch, slot = key
                    node = NODES[ch - 1]["id"]
                    dur = c["end"] - c["since"]
                    msg = _end_msg(self.proto, c["source"], slot, dur, self.rng.uniform(0, 1.5))
                    self.act[node].append(f"A: {_ts(t)} {self.label} {c['source']} {msg}")
                    if self.proto == "p25":
                        self._log(node, "M", "RF" if c["source"] == "RF" else "NET",
                                  f"P25, TDU (Terminator Data Unit), srcId = {c['srcId']}, dstId = {c['dstId']}", t)
                    else:
                        self._log(node, "M", "RF" if c["source"] == "RF" else "NET",
                                  f"{self.label}{f' Slot {slot}' if slot else ''}, call end (demo), srcId = {c['srcId']}, dstId = {c['dstId']}", t)
                    self.recent.appendleft({"ts": t, "node": node, "source": c["source"], "event": "call end",
                                            "mode": self.label, "slot": slot,
                                            "srcId": c["srcId"], "dstId": c["dstId"], "duration": round(dur, 1), "detail": ""})
                    del self.calls[key]
            # maybe start a call on a free voice channel (CC grants it)
            if self.rng.random() < 0.18:
                slots = (1, 2) if self.proto == "dmr" else (None,)
                free = [(n, sl) for n in NODES[1:] for sl in slots
                        if (n["channelNo"], sl) not in self.calls and n["id"] not in self.down]
                busy_tgs = {c["dstId"] for c in self.calls.values()}
                if free:
                    n, slot = self.rng.choice(free)
                    src = self.rng.choice(self.rids)
                    dst = self.rng.choice([g for g in TGS if g not in busy_tgs] or list(TGS))
                    srcm = self.rng.choice(["RF", "RF", "Net"])
                    c = {"srcId": src, "dstId": dst, "source": srcm, "since": t,
                         "end": t + self.rng.uniform(2, 12), "encrypted": False}
                    self.calls[(n["channelNo"], slot)] = c
                    self.act["mali-cc"].append(
                        f"A: {_ts(t)} {self.label} RF " + (f"Slot {self.tscc_slot} group grant request from {src} to TG {dst}"
                                                           if self.proto == "dmr" else f"group grant request from {src} to TG {dst}"))
                    self.act[n["id"]].append(f"A: {_ts(t)} {self.label} {srcm} {_start_msg(self.proto, srcm, slot, src, dst)}")
                    if self.proto == "p25":
                        self._log("mali-cc", "M", "RF",
                                  f"P25, TSDU (Trunking System Data Unit), IOSP_GRP_VCH (Group Voice Channel Grant), emerg = 0, "
                                  f"encrypt = 0, prio = 4, chNo = 1-{n['channelNo']}, srcId = {src}, dstId = {dst}", t)
                        self._log(n["id"], "M", "RF", f"P25, HDU (Header Data Unit), dstId = {dst}, algo = $80, kid = $0000", t)
                    else:
                        self._log("mali-cc", "M", "RF", f"{self.label}, group voice grant (demo), chNo = {n['channelNo']}"
                                  f"{f', slot = {slot}' if slot else ''}, srcId = {src}, dstId = {dst}", t)
                    self.recent.appendleft({"ts": t, "node": n["id"], "source": srcm, "event": "call start",
                                            "mode": self.label, "slot": slot,
                                            "srcId": src, "dstId": dst, "duration": None, "detail": TGS[dst]})
            if self.rng.random() < 0.03:
                r = self.rng.choice(self.rids)
                g = self.rng.choice(list(TGS))
                for a in self.affs:
                    if a["srcId"] == r:
                        a["grpId"] = g
                if self.proto == "p25":
                    self._log("mali-cc", "M", "RF", f"P25, TSDU (Trunking System Data Unit), IOSP_GRP_AFF (Group Affiliation Request), srcId = {r}, dstId = {g}", t)
                else:
                    self._log("mali-cc", "M", "RF", f"{self.label}, group affiliation (demo), srcId = {r}, dstId = {g}", t)
        self.last_tick = t

    def _check_proto(self) -> None:
        p = demo_protocol()
        if p != self.proto:
            down = set(self.down)
            self._reset(p)
            self.down = down

    def _freqs(self, n: dict) -> tuple[int, int]:
        if self.proto != "nxdn":
            return n["rx"], n["tx"]
        k = n["channelNo"] - 1  # 6.25 kHz steps
        return 449500000 + k * NXDN_NODES_STEP, 444500000 + k * NXDN_NODES_STEP

    def _site_label(self) -> str:
        return {"dmr": "CC 1 · TS 1+2", "nxdn": "RAN 1"}.get(self.proto, "NAC 293")

    def snapshot(self) -> dict:
        self._check_proto()
        now = time.time()
        self._advance(now)
        nodes = []
        for n in NODES:
            up = n["id"] not in self.down
            cs = [c for (ch, _), c in self.calls.items() if ch == n["channelNo"]]
            c = cs[0] if cs else None
            rx, tx = self._freqs(n)
            nodes.append({
                "id": n["id"], "name": n["name"], "role": n["role"], "reachable": True, "error": None,
                "host": {"active": up, "uptime_s": (now - self.t0 + 86400 * 3 + 3600 * n["channelNo"]) if up else None},
                "fne": {"active": True, "uptime_s": now - self.t0 + 86400 * 3} if n["role"] == "control" else None,
                "mode": (self.label if (c or n["role"] == "control") else "Idle") if up else "Offline",
                "protocol": self.label, "site_label": self._site_label(),
                "tx": bool(c) or n["role"] == "control",
                "modem": {"portType": "uart", "modemPort": "/dev/ttyUSB0", "rxFrequency": rx,
                          "txFrequency": tx, "protoVer": 4},
                "channelId": 1, "channelNo": n["channelNo"], "nac": "293", "peerId": 100000 + n["channelNo"],
                "lastSrcId": c["srcId"] if c else None, "lastDstId": c["dstId"] if c else None, "dry_run": True,
            })
        order = sorted(self.calls.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))
        talking = [{"node": NODES[ch - 1]["name"], "channelNo": ch, "slot": slot, "mode": self.label,
                    "srcId": c["srcId"], "dstId": c["dstId"],
                    "tgName": TGS.get(c["dstId"]), "source": c["source"], "encrypted": False, "since": c["since"]}
                   for (ch, slot), c in order]
        grants = [{"node": "mali-cc", "channelNo": ch, "slot": slot, "srcId": c["srcId"], "dstId": c["dstId"], "tx": True}
                  for (ch, slot), c in order]
        return {"demo": True, "protocol": self.proto, "ts": now, "nodes": nodes, "talking": talking, "grants": grants,
                "affiliations": list(self.affs), "recent": list(self.recent)[:60]}

    def logs(self, node_id: str, kind: str = "system", lines: int = 200) -> list[str]:
        self._check_proto()
        self._advance(time.time())
        src = self.act if kind == "activity" else self._sys
        return list(src.get(node_id, []))[-lines:]

    def toggle(self, node_id: str, up: bool) -> None:
        (self.down.discard if up else self.down.add)(node_id)
