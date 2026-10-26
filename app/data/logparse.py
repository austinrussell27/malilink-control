"""Parsing of dvmhost log files.

Verified formats (src/host/ActivityLog.h, src/common/Log.h, src/common/Log.cpp):
  activity: "A: YYYY-MM-DD HH:MM:SS.mmm <MODE> <RF|Net> <message>"
  system:   "<L>: YYYY-MM-DD HH:MM:SS.mmm (<MODULE>) <message>"
  files:    <filePath>/<fileRoot>-YYYY-MM-DD.activity.log and <fileRoot>-YYYY-MM-DD.log (local date)
Activity messages used (src/host/p25/packet/Voice.cpp):
  "RF [encrypted ]voice transmission from %u to [TG ]%u"
  "network [encrypted ]voice transmission from %u to [TG ]%u"
  "RF end of transmission, %.1f seconds, BER: %.1f%%..."
  "network end of transmission, %.1f seconds, %u%% packet loss" | "network end of transmission, %u frames"
  "RF voice rejection from %u to [TG ]%u "
"""
from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path

ACT_RE = re.compile(r"^A: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) (\S+) (RF|Net) (.*)$")
VOICE_RE = re.compile(r"(?:RF|network) (encrypted )?voice transmission from (\d+) to (TG )?(\d+)")
END_RE = re.compile(r"end of transmission, ([\d.]+) seconds(.*)")
REJ_RE = re.compile(r"voice rejection from (\d+) to (TG )?(\d+)")


def log_paths(log_dir: Path, root: str, day: datetime | None = None) -> tuple[Path, Path]:
    d = (day or datetime.now()).strftime("%Y-%m-%d")
    return log_dir / f"{root}-{d}.log", log_dir / f"{root}-{d}.activity.log"


def tail(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 256 * 1024))
        data = fh.read().decode("utf-8", "replace")
    return list(deque(data.splitlines(), maxlen=n))


def parse_activity(lines: list[str], node: str) -> tuple[list[dict], list[dict]]:
    """Returns (recent events newest-first, currently-open calls)."""
    events, open_calls = [], {}
    for line in lines:
        m = ACT_RE.match(line)
        if not m:
            continue
        ts_s, mode, src, msg = m.groups()
        try:
            ts = datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        except ValueError:
            continue
        if (v := VOICE_RE.search(msg)):
            call = {"node": node, "source": src, "mode": mode, "encrypted": bool(v.group(1)),
                    "srcId": int(v.group(2)), "dstId": int(v.group(4)), "group": bool(v.group(3)), "since": ts}
            open_calls[(mode, src)] = call
            events.append({"ts": ts, "node": node, "source": src, "event": "call start",
                           "srcId": call["srcId"], "dstId": call["dstId"], "duration": None, "detail": msg})
        elif (e := END_RE.search(msg)):
            c = open_calls.pop((mode, src), None)
            events.append({"ts": ts, "node": node, "source": src, "event": "call end",
                           "srcId": c["srcId"] if c else None, "dstId": c["dstId"] if c else None,
                           "duration": float(e.group(1)), "detail": e.group(2).strip(", ")})
        elif (r := REJ_RE.search(msg)):
            events.append({"ts": ts, "node": node, "source": src, "event": "rejected",
                           "srcId": int(r.group(1)), "dstId": int(r.group(3)), "duration": None, "detail": msg})
        else:
            events.append({"ts": ts, "node": node, "source": src, "event": "info",
                           "srcId": None, "dstId": None, "duration": None, "detail": msg})
    events.reverse()
    # a call with no "end of transmission" for > 5 min is stale (dvmhost default system.timeout is 180 s)
    now = datetime.now().timestamp()
    live = [c for c in open_calls.values() if now - c["since"] < 300]
    return events[:100], live
