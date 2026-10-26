"""Shapes returned to the UI (plain dicts, documented here)."""
from __future__ import annotations

from abc import ABC, abstractmethod

# dvmhost host state values (verified: src/host/modem/Modem.h STATE_* and src/common/Defines.h HOST_STATE_*)
HOST_STATES = {0: "Idle", 1: "DMR", 2: "P25", 3: "NXDN", 10: "CW ID", 240: "FNE",
               250: "Lockout", 254: "Error", 255: "Quit"}


def state_name(v) -> str:
    try:
        return HOST_STATES.get(int(v), f"State {v}")
    except Exception:
        return "Unknown"


class DataSource(ABC):
    """
    snapshot() -> {
      "demo": bool, "ts": float,
      "nodes": [{ "id","name","role","reachable","error",
                  "host": {"active","uptime_s"}, "fne": {...}|None,
                  "mode","tx", "modem": {"portType","modemPort","rxFrequency","txFrequency"},
                  "channelId","channelNo","nac","peerId","lastSrcId","lastDstId" }],
      "talking":  [{"node","channelNo","srcId","dstId","source":"RF"|"Net","encrypted","since"}],
      "grants":   [{"node","channelNo","srcId","dstId","tx"}],
      "affiliations": [{"srcId","grpId"}],
      "recent":   [{"ts","node","source","event","srcId","dstId","duration","detail"}],
    }
    logs(node_id, kind="system"|"activity", lines=200) -> [str]
    """

    @abstractmethod
    def snapshot(self) -> dict: ...

    @abstractmethod
    def logs(self, node_id: str, kind: str = "system", lines: int = 200) -> list[str]: ...
