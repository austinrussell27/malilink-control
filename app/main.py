"""Malinois Link Control - FastAPI app (pages + JSON API)."""
from __future__ import annotations

import asyncio
import base64
import glob
import json
import secrets as pysecrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from . import __version__, pairing, state
from .configgen import iden
from .configgen.generate import WizardInput, generate, write_files
from .data import get_source
from .services import systemd
from .services.installer import job as install_job
from .settings import APP_DIR, settings

app = FastAPI(title="Malinois Link Control", version=__version__)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
app.include_router(pairing.router)

OPEN_PATHS = ("/static/", "/api/local/", "/api/pairing/claim", "/healthz")


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
    return {"request": request, "page": page, "settings": settings, "st": st, "version": __version__, **kw}


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
    if st.get("wizard"):
        try:
            radio = generate(WizardInput(**st["wizard"]), st.get("secrets")).radio
        except Exception as e:
            err = str(e)
    return templates.TemplateResponse(request, "program.html", ctx(request, "program", radio=radio, err=err))


@app.get("/trunking", response_class=HTMLResponse)
def page_trunking(request: Request):
    return templates.TemplateResponse(request, "trunking.html", ctx(request, "trunking"))


@app.get("/about", response_class=HTMLResponse)
def page_about(request: Request):
    return templates.TemplateResponse(request, "about.html", ctx(request, "about"))


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ----------------------------------------------------------------- API: info / helpers
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


@app.post("/api/calc/freq")
def api_calc(f: FreqIn):
    try:
        p = iden.plan_conventional(f.tx_mhz, f.rx_mhz, settings.template_dir / "iden_table.example.dat")
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"channelId": p.entry.channel_id, "channelNo": p.channel_no_hex, "iden_line": p.entry.line(),
            "duplex": p.duplex, "simplex": p.simplex_same_freq, "offset_mhz": (p.rx_hz - p.tx_hz) / 1e6,
            "reused_template_row": p.reused_template_row}


# ----------------------------------------------------------------- API: wizard
def _validation_detail(e: ValidationError):
    return [{"field": ".".join(map(str, err["loc"])), "msg": err["msg"].removeprefix("Value error, ")} for err in e.errors()]


@app.post("/api/wizard/preview")
async def api_preview(request: Request):
    body = await request.json()
    try:
        w = WizardInput(**body)
        g = generate(w, state.load().get("secrets"))
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
    try:
        g = generate(w, st.get("secrets"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if g.errors:
        raise HTTPException(400, g.errors)
    written = write_files(g)
    st.update(wizard=w.model_dump(), secrets=g.secrets, rest_password=g.secrets["rest_password"],
              fne_rest_password=g.secrets["fne_rest_password"] if w.network_mode == "local_fne" else None,
              setup_complete=True, role="conventional")
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
        w = st.get("wizard") or {}
        if not (w.get("ack_license") and w.get("ack_terms")):
            raise HTTPException(400, "Finish setup and confirm the licensing notice before starting.")
    try:
        return systemd.action(unit, verb)
    except ValueError:
        raise HTTPException(400, "not allowed")


# ----------------------------------------------------------------- API: installer
@app.get("/api/install/status")
def api_install_status(since: int = 0):
    return install_job.snapshot(since)


@app.post("/api/install/start")
def api_install_start(with_fne: bool = True):
    if not install_job.start(with_fne):
        raise HTTPException(409, "Install already running")
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
    keys = {(st_key := state.load().get("pairing", {}).get("joined", {}).get("bundle", {}).get("cluster_key"))} - {None}
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
