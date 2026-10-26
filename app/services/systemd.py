"""systemd control for dvmhost/dvmfne. In dry-run mode nothing is executed; state is simulated.

Safety: this module only starts/stops/restarts services the user explicitly asks for. It never
calls any dvmhost REST/RPC endpoint that keys the transmitter.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime

from ..settings import settings

_SIM: dict[str, dict] = {}   # dry-run simulated unit state
ALLOWED = {settings.host_unit, settings.fne_unit}


def _run(args: list[str], timeout: int = 20) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def status(unit: str) -> dict:
    if unit not in ALLOWED:
        raise ValueError("unknown unit")
    if settings.dry_run:
        s = _SIM.get(unit, {"active": False, "since": None})
        up = time.time() - s["since"] if s["active"] and s["since"] else None
        return {"unit": unit, "active": s["active"], "state": "active" if s["active"] else "inactive",
                "uptime_s": up, "simulated": True}
    rc, out = _run(["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState",
                    "-p", "ActiveEnterTimestamp", "-p", "LoadState"])
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    active = props.get("ActiveState") == "active"
    up = None
    ts = props.get("ActiveEnterTimestamp", "")
    if active and ts:
        try:  # e.g. "Sun 2026-10-25 20:01:02 EDT"
            dt = datetime.strptime(" ".join(ts.split()[1:3]), "%Y-%m-%d %H:%M:%S")
            up = (datetime.now() - dt).total_seconds()
        except Exception:
            pass
    return {"unit": unit, "active": active, "state": props.get("ActiveState", "unknown"),
            "sub": props.get("SubState"), "loaded": props.get("LoadState") == "loaded",
            "uptime_s": up, "simulated": False}


def action(unit: str, verb: str) -> dict:
    if unit not in ALLOWED or verb not in ("start", "stop", "restart", "enable", "disable"):
        raise ValueError("not allowed")
    if settings.dry_run:
        s = _SIM.setdefault(unit, {"active": False, "since": None})
        if verb in ("start", "restart"):
            s.update(active=True, since=time.time())
        elif verb == "stop":
            s.update(active=False, since=None)
        return {"ok": True, "dry_run": True, "cmd": f"systemctl {verb} {unit}"}
    rc, out = _run(["systemctl", verb, unit], timeout=60)
    return {"ok": rc == 0, "dry_run": False, "cmd": f"systemctl {verb} {unit}", "output": out}
