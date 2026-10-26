"""Malinois Link Control - FastAPI app (pages + JSON API)."""
from __future__ import annotations

import asyncio
import base64
import glob
import json
import secrets as pysecrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Literal
from pydantic import BaseModel, ValidationError

from . import __version__, mode, pairing, radios, state
from .configgen import iden, ridacl
from .configgen.generate import MODES, WizardInput, generate, write_files
from .data import get_source
from .services import systemd
from .services.installer import job as install_job
from .settings import APP_DIR, settings

app = FastAPI(title="Malinois Link Control", version=__version__)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
app.include_router(pairing.router)
app.include_router(radios.router)

@app.on_event("startup")
def _startup():
    mode.startup()


@app.exception_handler(PermissionError)
async def _perm(request: Request, exc: PermissionError):
    return JSONResponse({"detail": f"Permission denied writing {getattr(exc, 'filename', '') or 'system files'}. "
                                   "Malinois Link Control must run as root on the Pi (sudo scripts/install-app.sh "
                                   "installs its systemd unit), or start it with sudo."}, 403)


OPEN_PATHS = ("/static/", "/favicon.ico", "/api/local/", "/api/pairing/claim", "/api/pairing/released", "/api/pairing/vc-release", "/api/radios/sync", "/healthz")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if settings.ui_password and not request.url.path.startswith(OPEN_PATHS):
        h = request.headers.get("authorization", "")
        ok = False
        if h.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                ok = pysecrets.compare_digest(pw, settings.ui_password)
            except Exception:
                ok = False
        if not ok:
            return JSONResponse({"detail": "auth required"}, 401, {"WWW-Authenticate": 'Basic realm="Malinois Link"'})
    return await call_next(request)


def ctx(request: Request, page: str, **kw):
    st = state.load()
    return {"request": request, "page": page, "settings": settings, "st": st, "version": __version__,
            "mode": mode.status(st), **kw}


# ----------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/dashboard" if state.load().get("setup_complete") or settings.demo else "/setup")


@app.get("/setup", response_class=HTMLResponse)
def page_setup(request: Request):
    return templates.TemplateResponse(request, "setup.html", ctx(request, "setup"))


@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", ctx(request, "dashboard"))


@app.get("/node/{node_id}", response_class=HTMLResponse)
def page_node(request: Request, node_id: str):
    return templates.TemplateResponse(request, "node.html", ctx(request, "dashboard", node_id=node_id))


@app.get("/logs", response_class=HTMLResponse)
def page_logs(request: Request):
    return templates.TemplateResponse(request, "logs.html", ctx(request, "logs"))


@app.get("/program", response_class=HTMLResponse)
def page_program(request: Request):
    st = state.load()
    radio, err = None, None
    if st.get("role") == "voice":
        err = "This Pi is a voice channel. Radios are programmed against the control channel - open Program radio on the control Pi."
    elif st.get("wizard"):
        try:
            radio = generate(WizardInput(**st["wizard"]), st.get("secrets"), st["pairing"]["voice_channels"]).radio
        except Exception as e:
            err = str(e)
    return templates.TemplateResponse(request, "program.html", ctx(request, "program", radio=radio, err=err))


@app.get("/trunking", response_class=HTMLResponse)
def page_trunking(request: Request):
    return templates.TemplateResponse(request, "trunking.html", ctx(request, "trunking"))


@app.get("/radios", response_class=HTMLResponse)
def page_radios(request: Request):
    return templates.TemplateResponse(request, "radios.html", ctx(request, "radios"))


@app.get("/about", response_class=HTMLResponse)
def page_about(request: Request):
    return templates.TemplateResponse(request, "about.html", ctx(request, "about"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/favicon.ico")


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ----------------------------------------------------------------- API: info / helpers
@app.get("/api/mode")
def api_mode():
    return mode.status()


@app.post("/api/mode/rewrite")
def api_mode_rewrite():
    if settings.dry_run:
        raise HTTPException(409, "Still in dry-run mode - nothing to re-write.")
    r = mode.rewrite()
    if not r.get("ok"):
        raise HTTPException(400, r.get("error"))
    return r


@app.get("/api/info")
def api_info():
    st = state.load()
    return {"version": __version__, "demo": settings.demo, "dry_run": settings.dry_run,
            "auto_dry_run": getattr(settings, "auto_dry_run", False),
            "dvmhost_installed": settings.dvmhost_installed, "node": settings.node_name,
            "role": st.get("role"), "setup_complete": st.get("setup_complete"),
            "dvm_etc": str(settings.dvm_etc)}


@app.get("/api/serial-ports")
def api_serial_ports():
    pats = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/serial0", "/dev/serial/by-id/*"]
    ports = sorted({p for pat in pats for p in glob.glob(pat)})
    return {"ports": ports, "suggested": ["/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyAMA0"]}


class FreqIn(BaseModel):
    tx_mhz: float
    rx_mhz: float
    mode: Literal["p25", "dmr", "nxdn"] = "p25"


@app.post("/api/calc/freq")
def api_calc(f: FreqIn):
    try:
        m = MODES[f.mode]
        p = iden.plan_conventional(f.tx_mhz, f.rx_mhz, settings.template_dir / "iden_table.example.dat",
                                   bandwidth_khz=m["bandwidth_khz"], max_ch=m["max_ch"])
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"channelId": p.entry.channel_id, "channelNo": p.channel_no_hex, "iden_line": p.entry.line(),
            "duplex": p.duplex, "simplex": p.simplex_same_freq, "offset_mhz": (p.rx_hz - p.tx_hz) / 1e6,
            "reused_template_row": p.reused_template_row}


# ----------------------------------------------------------------- API: wizard
def _validation_detail(e: ValidationError):
    return [{"field": ".".join(map(str, err["loc"])), "msg": err["msg"].removeprefix("Value error, ")} for err in e.errors()]


def _wizard_rid(w: WizardInput, st: dict) -> dict:
    """RID list for generated files: the wizard's preset list if given, else the Radios page list."""
    cur = radios.rid_bundle(st)
    if w.radios is not None:
        cur["radios"] = [ridacl.Radio(**r).model_dump() for r in w.radios]
    if w.rid_lockdown is not None:
        cur["lockdown"] = w.rid_lockdown
    return cur


@app.post("/api/wizard/preview")
async def api_preview(request: Request):
    body = await request.json()
    try:
        w = WizardInput(**body)
        st = state.load()
        g = generate(w, st.get("secrets"), st["pairing"]["voice_channels"] if w.topology == "trunked" else None,
                     rid=_wizard_rid(w, st))
    except ValidationError as e:
        raise HTTPException(422, _validation_detail(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"files": g.files, "warnings": g.warnings, "errors": g.errors, "radio": g.radio,
            "target_dir": str(settings.dvm_etc), "dry_run": settings.dry_run}


@app.post("/api/wizard/apply")
async def api_apply(request: Request):
    body = await request.json()
    try:
        w = WizardInput(**body)
    except ValidationError as e:
        raise HTTPException(422, _validation_detail(e))
    if not (w.ack_license and w.ack_terms):
        raise HTTPException(400, "Please confirm the licensing and DVMProject terms first.")
    st = state.load()
    if st.get("role") == "voice":
        raise HTTPException(409, "This Pi is paired as a voice channel. Use the 'Unpair this Pi' button "
                                 "(Dashboard or the Setup welcome step) to release it first.")
    vcs = st["pairing"]["voice_channels"] if w.topology == "trunked" else None
    try:
        rid = _wizard_rid(w, st)
        g = generate(w, st.get("secrets"), vcs, rid=rid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if g.errors:
        raise HTTPException(400, g.errors)
    written = write_files(g)
    st.update(wizard=w.model_dump(exclude={"radios", "rid_lockdown"}), secrets=g.secrets, rest_password=g.secrets["rest_password"],
              fne_rest_password=g.secrets["fne_rest_password"] if (w.network_mode == "local_fne" or w.topology == "trunked") else None,
              setup_complete=True, role="control" if w.topology == "trunked" else "conventional")
    st["radios"] = {**(st.get("radios") or {}), "list": rid["radios"], "lockdown": rid["lockdown"],
                    "updated": time.time()}
    state.save(st)
    return {"ok": True, "written": written, "dry_run": settings.dry_run, "warnings": g.warnings}


@app.get("/api/wizard/current")
def api_current():
    return state.load().get("wizard") or {}


# ----------------------------------------------------------------- API: services
@app.get("/api/service/{unit}")
def api_service_status(unit: str):
    try:
        return systemd.status(unit)
    except ValueError:
        raise HTTPException(404, "unknown unit")


@app.post("/api/service/{unit}/{verb}")
def api_service(unit: str, verb: str):
    st = state.load()
    if verb in ("start", "restart", "enable"):
        acks = (st.get("pairing", {}).get("joined") or {}) if st.get("role") == "voice" else (st.get("wizard") or {})
        if not (acks.get("ack_license") and acks.get("ack_terms")):
            raise HTTPException(400, "Finish setup and confirm the licensing notice before starting.")
        if unit == settings.host_unit and st.get("role") == "control" and not st["pairing"]["voice_channels"]:
            raise HTTPException(400, "Pair at least one voice channel first - dvmhost will not start with an empty voice channel list.")
        if not settings.dry_run:
            if not (settings.dvmhost_installed if unit == settings.host_unit else settings.fne_installed):
                raise HTTPException(409, f"{unit} isn't installed yet - run Setup -> Install DVM first.")
            if mode.needs_rewrite(st):
                raise HTTPException(409, "Your configuration was saved in dry-run mode. Re-write it to "
                                         f"{settings.dvm_etc} first (banner at the top of the page).")
    try:
        return systemd.action(unit, verb)
    except ValueError:
        raise HTTPException(400, "not allowed")


# ----------------------------------------------------------------- API: installer
@app.get("/api/install/status")
def api_install_status(since: int = 0):
    return install_job.snapshot(since)


@app.post("/api/install/start")
def api_install_start(with_fne: bool = True, clean_src: bool = False):
    if not install_job.dry_run and not settings.is_root:
        raise HTTPException(403, "Installing DVM needs root (apt, make install, useradd, systemd units). "
                                 "Run Malinois Link Control as root: sudo ./scripts/install-app.sh, or start it with sudo.")
    if not install_job.start(with_fne, clean_src):
        raise HTTPException(409, "Install already running")
    return {"ok": True, "dry_run": install_job.dry_run}


@app.post("/api/install/fne")
def api_install_fne():
    """Install ONLY the dvmfne service (build just the dvmfne target if its binary is missing)."""
    if not install_job.dry_run and not settings.is_root:
        raise HTTPException(403, "Installing the dvmfne service needs root. Run Malinois Link Control as root: "
                                 "sudo ./scripts/install-app.sh, or start it with sudo.")
    if install_job.state == "running":
        raise HTTPException(409, "An install is already running")
    blocker = install_job.fne_only_blocker()
    if blocker:
        raise HTTPException(409, blocker)
    if not install_job.start(kind="fne"):
        raise HTTPException(409, "An install is already running")
    return {"ok": True, "dry_run": install_job.dry_run}


# ----------------------------------------------------------------- API: dashboard data
@app.get("/api/snapshot")
def api_snapshot():
    return get_source().snapshot()


@app.get("/api/stream")
async def api_stream(request: Request):
    async def gen():
        while not await request.is_disconnected():
            snap = await asyncio.to_thread(get_source().snapshot)
            yield f"data: {json.dumps(snap)}\n\n"
            await asyncio.sleep(1.5)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/logs/{node_id}")
def api_logs(node_id: str, kind: str = "system", lines: int = 200):
    if kind not in ("system", "activity", "fne"):
        raise HTTPException(400, "kind must be system|activity|fne")
    return {"node": node_id, "kind": kind, "lines": get_source().logs(node_id, kind, min(lines, 1000))}


def _cluster_ok(request: Request) -> None:
    keys = {(state.load().get("pairing", {}).get("joined") or {}).get("cluster_key")} - {None}
    if keys and request.headers.get("X-Malilink-Cluster-Key") not in keys:
        raise HTTPException(403, "bad cluster key")


@app.get("/api/local/snapshot")
def api_local_snapshot(request: Request):
    """Used by the control Pi's dashboard to pull this Pi's status (multi-Pi)."""
    _cluster_ok(request)
    src = get_source()
    if hasattr(src, "local"):
        return src.local()
    snap = src.snapshot()
    return {"node": snap["nodes"][0], "talking": [], "grants": [], "affiliations": [], "recent": []}


@app.get("/api/local/logs")
def api_local_logs(request: Request, kind: str = "system", lines: int = 200):
    _cluster_ok(request)
    return {"lines": get_source().logs("local", kind, min(lines, 1000))}
