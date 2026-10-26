"""Plain-language service health for dvmhost / dvmfne (dashboard + node page).

States (most specific first):
  simulated       dry-run: services are simulated, no real REST API exists
  not_installed   binary missing                         -> action "install"  (Setup -> Install DVM)
  not_configured  config file missing                    -> action "setup"
  stopped/failed  installed + configured, unit not active -> action "start"   (uses the existing start confirmation)
  rest_disabled   running, but restEnable is false in the config on disk -> action "setup" (re-save)
  starting        running < STARTUP_GRACE s and REST not answering yet
  auth_failed     REST answers but rejects our password  -> action "setup" (re-save writes a matching password)
  rest_unreachable running, REST not answering after the grace period -> action "logs" (+ modem hint for dvmhost:
                  Host.cpp opens the modem *before* createNetwork() starts the REST API, so a wrong/missing modem
                  port is the usual cause)
  healthy

REST settings are read from the config files actually on disk (dvmhost config.yml network.rest*;
dvmfne fne-config.yml system.rest* — key names from the upstream example configs in dvm_templates/),
so the app always talks to the port/password dvmhost was started with, on control and voice Pis alike."""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

STARTUP_GRACE = 25  # seconds; dvmhost opens/initialises the modem before the REST API comes up

DEFAULT_PORT = {"host": 9990, "fne": 9991}  # 9990 = REST_API_DEFAULT_PORT; we move dvmfne to 9991


def read_rest(etc: Path, kind: str) -> dict:
    """{"file", "exists", "enable", "address", "port", "password"} from config.yml / fne-config.yml."""
    f = Path(etc) / ("config.yml" if kind == "host" else "fne-config.yml")
    out = {"file": str(f), "exists": f.is_file(), "enable": None, "address": None, "port": None, "password": None}
    if not out["exists"]:
        return out
    try:
        d = YAML(typ="safe").load(f.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        out["error"] = f"could not parse: {e}"
        return out
    sec = (d.get("network") if kind == "host" else d.get("system")) or {}
    out.update(enable=bool(sec.get("restEnable", False)), address=sec.get("restAddress") or "127.0.0.1",
               port=int(sec.get("restPort") or 9990), password=sec.get("restPassword"))
    return out


def rest_url(cfg: dict, kind: str) -> str:
    addr = cfg.get("address") or "127.0.0.1"
    if addr in ("0.0.0.0", "::", ""):
        addr = "127.0.0.1"
    return f"http://{addr}:{cfg.get('port') or DEFAULT_PORT[kind]}"


def classify(kind: str, *, installed: bool, svc: dict, rest_cfg: dict, rest_error: str | None,
             dry_run: bool, local: bool = True) -> dict:
    """kind 'host'|'fne'. rest_error: None (ok) or refused|timeout|auth|http|backoff-kind|'no_password'."""
    name = "dvmhost" if kind == "host" else "dvmfne"
    what = "radio service" if kind == "host" else "network hub"
    port = rest_cfg.get("port") or DEFAULT_PORT[kind]
    act = lambda k, label: {"kind": k, "label": label, "unit": name}  # noqa: E731
    logs = act("logs", "View logs")

    def h(state, level, title, detail, action=None, extra=None):
        return {"unit": name, "state": state, "level": level, "title": title, "detail": detail,
                "action": action, "secondary": extra}

    active = bool(svc.get("active"))
    if dry_run and svc.get("simulated", True):
        if active:
            return h("simulated", "info", f"{name} running (simulated)",
                     "Dry-run mode: services are simulated, so there is no live status to read.")
        return h("simulated", "info", f"{name} stopped (simulated)", "Dry-run mode: nothing on this machine is changed.",
                 act("start", f"Start {name}") if local else None)
    if not installed:
        return h("not_installed", "warn", f"{name} isn't installed yet",
                 f"Install DVM from Setup to build and install the {what}.", act("install", "Install DVM"))
    if not rest_cfg.get("exists"):
        return h("not_configured", "warn", f"{name} isn't configured yet",
                 f"No {Path(rest_cfg['file']).name} yet — finish Setup to write it.", act("setup", "Open Setup"))
    if svc.get("state") in ("activating", "reloading"):
        return h("starting", "info", f"{name} is starting…", "systemd is starting it — give it a few seconds.")
    if not active:
        failed = svc.get("state") == "failed"
        return h("failed" if failed else "stopped", "err" if failed else "idle",
                 f"{name} {'stopped with an error' if failed else 'is not running'}",
                 ("It exited with an error — the log usually says why (often the modem port)." if failed
                  else f"The {what} is installed and configured but not started."),
                 act("start", f"Start {name}") if local else act("node", "Open Pi page"), logs if failed else None)
    if rest_cfg.get("enable") is False:
        return h("rest_disabled", "warn", f"{name} is running — live status is off",
                 f"restEnable is false in {Path(rest_cfg['file']).name}. Re-save Setup to turn the status API back on.",
                 act("setup", "Open Setup"))
    if rest_error is None:
        return h("healthy", "ok", f"{name} running", "Live status OK.")
    up = svc.get("uptime_s")
    if rest_error in ("refused", "timeout") and up is not None and up < STARTUP_GRACE:
        return h("starting", "info", f"{name} is starting…",
                 "Waiting for it to finish starting up — live status appears in a few seconds.")
    if rest_error in ("auth", "no_password"):
        return h("auth_failed", "warn", f"{name} is running — status password doesn't match",
                 "Re-save Setup so the app and the config file use the same status password, then restart it.",
                 act("setup", "Open Setup"))
    hint = (" Usually the modem didn't open: dvmhost starts its status API only after it connects to the modem — "
            "check the modem serial port." if kind == "host" else "")
    return h("rest_unreachable", "warn", f"{name} is running but not answering yet",
             f"Its status API on 127.0.0.1:{port} isn't responding.{hint} The app keeps retrying quietly.",
             logs, act("modem", "Check modem port") if kind == "host" else None)
