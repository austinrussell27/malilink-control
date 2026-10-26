"""0.5.1: serial-port / MMDVM hat detection, read-only modem status, and calm dvmhost/dvmfne health states."""
import json
import os
import pwd
import socket
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.data import health
from app.data.dvmrest import READ_ONLY_PATHS, DvmRest, RestUnavailable
from app.hw import serialports
from app.main import app
from test_pairing import CC, _port, _start

ME = pwd.getpwuid(os.getuid()).pw_name


def _fake_root(model: str, config: str = "", cmdline: str = "console=tty1 root=PARTUUID=x rootwait",
               devs=("ttyAMA0", "ttyAMA10", "ttyS0", "ttyS1", "ttyS2", "ttyACM0"), serial0="ttyAMA10"):
    root = Path(tempfile.mkdtemp())
    (root / "dev").mkdir()
    for d in devs:
        (root / "dev" / d).write_text("")
        os.chmod(root / "dev" / d, 0o660)
    if serial0:
        os.symlink(serial0, root / "dev" / "serial0")
    (root / "proc/device-tree").mkdir(parents=True)
    (root / "proc/device-tree/model").write_bytes(model.encode() + b"\x00")
    (root / "boot/firmware").mkdir(parents=True)
    (root / "boot/firmware/config.txt").write_text(config)
    (root / "boot/firmware/cmdline.txt").write_text(cmdline)
    return str(root)


# ---------------------------------------------------------------- serial ports
def test_scan_pi5_resolves_serial0_and_marks_hat():
    r = serialports.scan(_fake_root("Raspberry Pi 5 Model B Rev 1.0", "[all]\n"), user=ME)
    by = {p["path"]: p for p in r["ports"]}
    assert r["pi_generation"] == 5 and r["likely_hat"] == "/dev/ttyAMA0"
    assert by["/dev/serial0"]["link"] and by["/dev/serial0"]["target"] == "/dev/ttyAMA10"
    assert by["/dev/serial0"]["kind"] == "debug" and not by["/dev/serial0"]["likely_hat"]
    assert by["/dev/ttyAMA0"]["likely_hat"] and by["/dev/ttyAMA0"]["exists"]
    assert by["/dev/ttyAMA0"]["readable"] is True and by["/dev/ttyAMA0"]["writable"] is True  # owner rw
    assert "/dev/ttyS1" not in by and "/dev/ttyS0" in by  # 8250 placeholders hidden
    assert by["/dev/ttyACM0"]["kind"] == "usb"
    assert any("dtparam=uart0" in h for h in r["hints"])  # UART not enabled in config.txt


def test_scan_pi5_uart_enabled_no_hint_and_unknown_user():
    r = serialports.scan(_fake_root("Raspberry Pi 5 Model B Rev 1.0", "dtparam=uart0\n"), user="no-such-user-xyz")
    assert not any("dtparam=uart0" in h for h in r["hints"])
    assert r["boot"]["dtparams"] == ["uart0"]
    assert all(p["readable"] is None for p in r["ports"])  # user doesn't exist (yet) -> unknown, not "no"


def test_scan_pi4_default_is_mini_uart_and_console_warning():
    r = serialports.scan(_fake_root("Raspberry Pi 4 Model B Rev 1.4", "enable_uart=1\n",
                                    cmdline="console=serial0,115200 console=tty1 root=x", serial0="ttyS0"))
    assert r["pi_generation"] == 4 and r["likely_hat"] == "/dev/ttyS0"
    assert any("disable-bt" in h for h in r["hints"])
    assert any("console" in h and "serial0" in h for h in r["hints"])
    assert r["boot"]["enable_uart"] is True and r["boot"]["console"] == ["serial0", "tty1"]


def test_scan_pi4_disable_bt_moves_hat_to_ttyAMA0():
    r = serialports.scan(_fake_root("Raspberry Pi 4 Model B Rev 1.4", "dtoverlay=disable-bt\nenable_uart=1\n", serial0="ttyAMA0"))
    assert r["likely_hat"] == "/dev/ttyAMA0" and not any("disable-bt" in h for h in r["hints"])
    assert {p["path"]: p for p in r["ports"]}["/dev/serial0"]["likely_hat"]


def test_scan_non_pi():
    r = serialports.scan(_fake_root("", devs=("ttyUSB0",), serial0=None))
    assert r["pi_generation"] is None and r["likely_hat"] is None and r["hints"] == []


# ---------------------------------------------------------------- health classification
CFG = {"file": "/usr/local/etc/config.yml", "exists": True, "enable": True, "port": 9990}
UP = {"active": True, "state": "active", "uptime_s": 300, "simulated": False}
DOWN = {"active": False, "state": "inactive", "uptime_s": None, "simulated": False}


def c(**kw):
    base = dict(installed=True, svc=UP, rest_cfg=CFG, rest_error=None, dry_run=False)
    return health.classify("host", **{**base, **kw})


def test_health_states():
    assert c()["state"] == "healthy"
    h = c(installed=False)
    assert h["state"] == "not_installed" and h["action"]["kind"] == "install"
    assert c(rest_cfg={**CFG, "exists": False})["action"]["kind"] == "setup"
    h = c(svc=DOWN)
    assert h["state"] == "stopped" and h["action"] == {"kind": "start", "label": "Start dvmhost", "unit": "dvmhost"}
    assert c(svc={**DOWN, "state": "failed"})["secondary"]["kind"] == "logs"
    assert c(svc={**DOWN, "state": "activating"})["state"] == "starting"
    assert c(rest_cfg={**CFG, "enable": False})["state"] == "rest_disabled"
    assert c(svc={**UP, "uptime_s": 4}, rest_error="refused")["state"] == "starting"
    h = c(rest_error="refused")
    assert h["state"] == "rest_unreachable" and h["action"]["kind"] == "logs" and h["secondary"]["kind"] == "modem"
    assert "Errno" not in h["title"] + h["detail"] and "9990" in h["detail"]
    assert c(rest_error="auth")["state"] == "auth_failed"
    assert c(svc={**DOWN, "simulated": True}, dry_run=True)["state"] == "simulated"
    f = health.classify("fne", installed=True, svc=UP, rest_cfg={**CFG, "port": 9991}, rest_error="refused", dry_run=False)
    assert f["unit"] == "dvmfne" and "9991" in f["detail"] and f["secondary"] is None
    assert c(svc=DOWN, local=False)["action"]["kind"] == "node"  # remote Pi: no start button


# ---------------------------------------------------------------- generated configs: REST on localhost with our password
def test_generated_configs_enable_rest_on_localhost():
    from app.configgen.generate import WizardInput, generate
    g = generate(WizardInput(**CC))
    y = YAML(typ="safe")
    host = y.load(g.files["config.yml"])["network"]
    fne = y.load(g.files["fne-config.yml"])["system"]
    assert (host["restEnable"], host["restAddress"], host["restPort"], host["restPassword"]) == \
        (True, "127.0.0.1", 9990, g.secrets["rest_password"])
    assert (fne["restEnable"], fne["restAddress"], fne["restPort"], fne["restPassword"]) == \
        (True, "127.0.0.1", 9991, g.secrets["fne_rest_password"])
    d = Path(tempfile.mkdtemp())
    (d / "config.yml").write_text(g.files["config.yml"]); (d / "fne-config.yml").write_text(g.files["fne-config.yml"])
    r = health.read_rest(d, "host")
    assert r["password"] == g.secrets["rest_password"] and health.rest_url(r, "host") == "http://127.0.0.1:9990"
    assert health.rest_url(health.read_rest(d, "fne"), "fne") == "http://127.0.0.1:9991"


# ---------------------------------------------------------------- REST client: quiet backoff, auth, read-only
def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_rest_refused_backs_off_quietly():
    r = DvmRest(f"http://127.0.0.1:{_free_port()}", "pw", timeout=0.5)
    with pytest.raises(RestUnavailable) as e:
        r.get("/status")
    assert e.value.kind == "refused" and r.failures == 1
    with pytest.raises(RestUnavailable) as e2:  # inside the backoff window: no new request, same calm error
        r.get("/status")
    assert e2.value is e.value and r.failures == 1


def test_rest_auth_and_status_against_fake_dvmhost():
    import fake_dvmhost
    port = _free_port()
    srv = HTTPServer(("127.0.0.1", port), fake_dvmhost.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RestUnavailable) as e:
            DvmRest(f"http://127.0.0.1:{port}", "wrong").get("/status")
        assert e.value.kind == "auth"
        ok = DvmRest(f"http://127.0.0.1:{port}", fake_dvmhost.PW)
        assert ok.get("/status")["modem"]["portType"] == "uart" and ok.failures == 0
    finally:
        srv.shutdown()


def test_rest_client_stays_read_only():
    for p in ("/mdm/mode", "/grant-tg", "/p25/raw-tsbk", "/dmr/beacon", "/nxdn/cc", "/p25/cc-broadcast"):
        assert p not in READ_ONLY_PATHS
        with pytest.raises(PermissionError):
            DvmRest("http://127.0.0.1:1", "x").get(p)


# ---------------------------------------------------------------- API (demo, in-process)
def test_api_modem_demo_is_labelled_sample():
    cl = TestClient(app)
    d = cl.get("/api/modem").json()
    assert d["scan"]["sample"] is True and "demo" in d["scan"]["sample_reason"]
    assert d["status"]["sample"] is True and d["status"]["modem"]["modemPort"] == "/dev/ttyAMA0"
    assert d["configured"]["port"]
    assert cl.get("/api/modem?node=mali-vc1").json()["status"]["running"] in (True, False)
    sp = cl.get("/api/serial-ports").json()
    assert sp["detail"]["likely_hat"] == "/dev/ttyAMA0" and "/dev/ttyAMA0" in sp["suggested"]
    snap = cl.get("/api/snapshot").json()
    assert all(n["health"]["host"]["state"] in ("healthy", "stopped") for n in snap["nodes"])
    assert "Detect serial ports" in cl.get("/setup").text
    assert 'id="modemPanel"' in cl.get("/about").text and 'id="modemPanel"' in cl.get("/node/mali-cc").text


# ---------------------------------------------------------------- live (non-demo) dry-run Pi: calm states, configured port
@pytest.fixture(scope="module")
def pi():
    d, port = Path(tempfile.mkdtemp()), _port()
    p = _start(d, port, "mali-cc")
    yield f"http://127.0.0.1:{port}", d
    p.terminate()


def test_live_dry_run_health_and_modem(pi):
    base, d = pi
    n = httpx.get(f"{base}/api/snapshot", timeout=10).json()["nodes"][0]
    assert n["health"]["host"]["state"] == "simulated" and not n.get("error")
    w = {**CC, "topology": "conventional", "modem_port": "/dev/ttyAMA0"}
    assert httpx.post(f"{base}/api/wizard/apply", json=w, timeout=20).status_code == 200
    m = httpx.get(f"{base}/api/modem", timeout=10).json()
    assert m["configured"]["port"] == "/dev/ttyAMA0" and m["configured"]["config_port"] == "/dev/ttyAMA0"
    assert m["status"]["running"] is False and "not running" in m["status"]["note"]
    if m["scan"]["pi_generation"] is None:
        assert m["scan"]["sample"] is True and "dry-run" in m["scan"]["sample_reason"]
    # starting the simulated service never produces a raw REST error (no Errno 111) in dry-run
    assert httpx.post(f"{base}/api/service/dvmhost/start", timeout=10).json()["dry_run"] is True
    n = httpx.get(f"{base}/api/snapshot", timeout=10).json()["nodes"][0]
    assert n["health"]["host"]["state"] == "simulated" and "running" in n["health"]["host"]["title"]
    assert "Errno" not in json.dumps(n)
