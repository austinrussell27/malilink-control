"""Demo data: a simulated 3-Pi P25 trunked site (control channel + 2 voice channels) with
calls, grants, affiliations and log lines in the real dvmhost log formats."""
from __future__ import annotations

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
RIDS = [1234567, 3101001, 3101002, 3101007, 3105555, 3109999]


def _ts(t: float) -> str:
    d = datetime.fromtimestamp(t)
    return d.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(d.microsecond / 1000):03d}"


from .base import DataSource


class DemoSource(DataSource):
    def __init__(self) -> None:
        self.t0 = time.time()
        self.rng = random.Random(42)
        self.calls: dict[int, dict] = {}   # chNo -> call
        self.recent: deque = deque(maxlen=100)
        self._sys = {n["id"]: deque(maxlen=500) for n in NODES}
        self.act = {n["id"]: deque(maxlen=500) for n in NODES}
        self.down: set[str] = set()
        self.last_tick = time.time() - 120
        self.affs = [{"srcId": r, "grpId": self.rng.choice(list(TGS))} for r in RIDS]
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
            for ch, c in list(self.calls.items()):
                if t >= c["end"]:
                    node = NODES[ch - 1]["id"]
                    dur = c["end"] - c["since"]
                    if c["source"] == "RF":
                        msg = f"RF end of transmission, {dur:.1f} seconds, BER: {self.rng.uniform(0, 1.5):.1f}%"
                    else:
                        msg = f"network end of transmission, {dur:.1f} seconds, 0% packet loss"
                    self.act[node].append(f"A: {_ts(t)} P25 {c['source']} {msg}")
                    self._log(node, "M", "RF" if c["source"] == "RF" else "NET",
                              f"P25, TDU (Terminator Data Unit), srcId = {c['srcId']}, dstId = {c['dstId']}", t)
                    self.recent.appendleft({"ts": t, "node": node, "source": c["source"], "event": "call end",
                                            "srcId": c["srcId"], "dstId": c["dstId"], "duration": round(dur, 1), "detail": ""})
                    del self.calls[ch]
            # maybe start a call on a free voice channel (CC grants it)
            if self.rng.random() < 0.18:
                free = [n for n in NODES[1:] if n["channelNo"] not in self.calls and n["id"] not in self.down]
                if free:
                    n = self.rng.choice(free)
                    src, dst = self.rng.choice(RIDS), self.rng.choice(list(TGS))
                    srcm = self.rng.choice(["RF", "RF", "Net"])
                    c = {"srcId": src, "dstId": dst, "source": srcm, "since": t,
                         "end": t + self.rng.uniform(2, 12), "encrypted": False}
                    self.calls[n["channelNo"]] = c
                    word = "RF" if srcm == "RF" else "network"
                    self.act[n["id"]].append(f"A: {_ts(t)} P25 {srcm} {word} voice transmission from {src} to TG {dst}")
                    self._log("mali-cc", "M", "RF",
                              f"P25, TSDU (Trunking System Data Unit), IOSP_GRP_VCH (Group Voice Channel Grant), emerg = 0, "
                              f"encrypt = 0, prio = 4, chNo = 1-{n['channelNo']}, srcId = {src}, dstId = {dst}", t)
                    self._log(n["id"], "M", "RF", f"P25, HDU (Header Data Unit), dstId = {dst}, algo = $80, kid = $0000", t)
                    self.recent.appendleft({"ts": t, "node": n["id"], "source": srcm, "event": "call start",
                                            "srcId": src, "dstId": dst, "duration": None, "detail": TGS[dst]})
            if self.rng.random() < 0.03:
                r = self.rng.choice(RIDS)
                g = self.rng.choice(list(TGS))
                for a in self.affs:
                    if a["srcId"] == r:
                        a["grpId"] = g
                self._log("mali-cc", "M", "RF", f"P25, TSDU (Trunking System Data Unit), IOSP_GRP_AFF (Group Affiliation Request), srcId = {r}, dstId = {g}", t)
        self.last_tick = t

    def snapshot(self) -> dict:
        now = time.time()
        self._advance(now)
        nodes = []
        for n in NODES:
            up = n["id"] not in self.down
            c = self.calls.get(n["channelNo"])
            nodes.append({
                "id": n["id"], "name": n["name"], "role": n["role"], "reachable": True, "error": None,
                "host": {"active": up, "uptime_s": (now - self.t0 + 86400 * 3 + 3600 * n["channelNo"]) if up else None},
                "fne": {"active": True, "uptime_s": now - self.t0 + 86400 * 3} if n["role"] == "control" else None,
                "mode": ("P25" if (c or n["role"] == "control") else "Idle") if up else "Offline",
                "tx": bool(c) or n["role"] == "control",
                "modem": {"portType": "uart", "modemPort": "/dev/ttyUSB0", "rxFrequency": n["rx"],
                          "txFrequency": n["tx"], "protoVer": 4},
                "channelId": 1, "channelNo": n["channelNo"], "nac": "293", "peerId": 100000 + n["channelNo"],
                "lastSrcId": c["srcId"] if c else None, "lastDstId": c["dstId"] if c else None, "dry_run": True,
            })
        talking = [{"node": NODES[ch - 1]["name"], "channelNo": ch, "srcId": c["srcId"], "dstId": c["dstId"],
                    "tgName": TGS.get(c["dstId"]), "source": c["source"], "encrypted": False, "since": c["since"]}
                   for ch, c in sorted(self.calls.items())]
        grants = [{"node": "mali-cc", "channelNo": ch, "srcId": c["srcId"], "dstId": c["dstId"], "tx": True}
                  for ch, c in sorted(self.calls.items())]
        return {"demo": True, "ts": now, "nodes": nodes, "talking": talking, "grants": grants,
                "affiliations": list(self.affs), "recent": list(self.recent)[:60]}

    def logs(self, node_id: str, kind: str = "system", lines: int = 200) -> list[str]:
        self._advance(time.time())
        src = self.act if kind == "activity" else self._sys
        return list(src.get(node_id, []))[-lines:]

    def toggle(self, node_id: str, up: bool) -> None:
        (self.down.discard if up else self.down.add)(node_id)
