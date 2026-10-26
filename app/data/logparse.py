"""Parsing of dvmhost log files.

Verified formats (src/host/ActivityLog.h, src/common/Log.h, src/common/Log.cpp):
  activity: "A: YYYY-MM-DD HH:MM:SS.mmm <MODE> <RF|Net> <message>"
  system:   "<L>: YYYY-MM-DD HH:MM:SS.mmm (<MODULE>) <message>"
  files:    <filePath>/<fileRoot>-YYYY-MM-DD.activity.log and <fileRoot>-YYYY-MM-DD.log (local date)
Activity messages used (verified in the dvmhost source):
  P25 (src/host/p25/packet/Voice.cpp) and NXDN (src/host/nxdn/packet/Voice.cpp, nxdn/Control.cpp):
    "RF [encrypted ]voice transmission from %u to [TG ]%u"
    "network [encrypted ]voice transmission from %u to [TG ]%u"
    "RF [encrypted ]late entry from %u to [TG ]%u"            (NXDN)
    "RF end of transmission, %.1f seconds, BER: %.1f%%..."
    "network end of transmission, %.1f seconds[, %u%% packet loss]" | "network end of transmission, %u frames"
    "transmission lost, %.1f seconds, BER..."                 (NXDN)
    "network watchdog has expired[, %.1f seconds, ...]"
    "RF|network voice rejection from %u to [TG ]%u "
    "group grant request from %u to TG %u[ denied| queued]"  / "unit-to-unit grant request from %u to %u"
  DMR (src/host/dmr/packet/Voice.cpp, Data.cpp, Slot.cpp, ControlSignaling.cpp) - every line starts "Slot %u ":
    "Slot %u RF [encrypted ]voice header from %u to [TG ]%u"
    "Slot %u network voice header from %u to [TG ]%u"
    "Slot %u RF|network late entry from %u to [TG ]%u"
    "Slot %u RF end of voice transmission, %.1f seconds, BER: ..."
    "Slot %u network end of voice transmission, %.1f seconds, %u%% packet loss, BER: ..."
    "Slot %u RF voice transmission lost, %.1f seconds, BER: ..."
    "Slot %u network watchdog has expired[, %.1f seconds, ...]"
    "Slot %u RF voice rejection from %u to [TG ]%u "
    "Slot %u group|individual grant request from %u to TG %u"
    "Slot %u group grant request %u to TG %u denied|queued"   (no "from" in this form)
"""
from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path

ACT_RE = re.compile(r"^A: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) (\S+) (RF|Net) (.*)$")
SLOT_RE = re.compile(r"^Slot (\d+),? (.*)$")
VOICE_RE = re.compile(r"(?:RF|network) (encrypted )?(voice transmission|voice header|late entry) from (\d+) to (TG )?(\d+)")
END_RE = re.compile(r"(?:end of (?:voice )?transmission|transmission lost|watchdog has expired)"
                    r"(?:, ([\d.]+) seconds)?(.*)")
REJ_RE = re.compile(r"voice rejection from (\d+) to (TG )?(\d+)")
GRANT_RE = re.compile(r"(group|individual|unit-to-unit) grant request (?:from )?(\d+) to (TG )?(\d+)(?: (denied|queued))?")


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
    """Returns (recent events newest-first, currently-open calls). Open calls are keyed by
    (mode, RF|Net, DMR slot) so both DMR timeslots can carry a call at once."""
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
        slot = None
        body = msg
        if (sm := SLOT_RE.match(msg)):
            slot, body = int(sm.group(1)), sm.group(2)
        key = (mode, src, slot)
        base = {"ts": ts, "node": node, "source": src, "mode": mode, "slot": slot}
        if (v := VOICE_RE.search(body)):
            late = v.group(2) == "late entry"
            if late and key in open_calls:
                continue  # late entry for a call we already saw start
            call = {"node": node, "source": src, "mode": mode, "slot": slot, "encrypted": bool(v.group(1)),
                    "srcId": int(v.group(3)), "dstId": int(v.group(5)), "group": bool(v.group(4)), "since": ts}
            open_calls[key] = call
            events.append({**base, "event": "call start", "srcId": call["srcId"], "dstId": call["dstId"],
                           "duration": None, "detail": msg})
        elif (r := REJ_RE.search(body)):
            events.append({**base, "event": "rejected", "srcId": int(r.group(1)), "dstId": int(r.group(3)),
                           "duration": None, "detail": msg})
        elif (g := GRANT_RE.search(body)):
            outcome = g.group(5)
            events.append({**base, "event": f"grant {outcome}" if outcome else "grant request",
                           "srcId": int(g.group(2)), "dstId": int(g.group(4)), "duration": None, "detail": msg})
        elif (e := END_RE.search(body)):
            c = open_calls.pop(key, None)
            events.append({**base, "event": "call lost" if "lost" in body or "watchdog" in body else "call end",
                           "srcId": c["srcId"] if c else None, "dstId": c["dstId"] if c else None,
                           "duration": float(e.group(1)) if e.group(1) else None,
                           "detail": (e.group(2) or "").strip(", ")})
        else:
            events.append({**base, "event": "info", "srcId": None, "dstId": None, "duration": None, "detail": msg})
    events.reverse()
    # a call with no "end of transmission" for > 5 min is stale (dvmhost default system.timeout is 180 s)
    now = datetime.now().timestamp()
    live = [c for c in open_calls.values() if now - c["since"] < 300]
    return events[:100], live
