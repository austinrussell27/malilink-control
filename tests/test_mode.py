"""Real mode vs dry-run (settings auto-detection) and re-writing configs saved in dry-run."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
W = {"system_name": "MALI", "callsign": "kd2abc", "tx_mhz": 444.5, "rx_mhz": 449.5, "ack_license": True, "ack_terms": True}


def _bin(with_systemctl):
    d = Path(tempfile.mkdtemp())
    if with_systemctl:
        shutil.copy(ROOT / "tests/fake_systemctl.sh", d / "systemctl")
    return d


def _env(tmp, with_systemctl=True, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MALILINK_")}
    env.update(PATH=str(_bin(with_systemctl)), MALILINK_DATA_DIR=str(tmp / "data"),
               MALILINK_DVM_ETC=str(tmp / "usr/local/etc"), MALILINK_DVM_LOG_DIR=str(tmp / "var/log/dvm"),
               MALILINK_DVM_BIN=str(tmp / "usr/local/bin"), FAKE_SYSTEMCTL_LOG=str(tmp / "systemctl.log"))
    env.update(extra)
    return env


def _settings(env):
    code = ("import json; from app.settings import settings as s; print(json.dumps({'dry_run': s.dry_run, "
            "'reason': s.mode_reason, 'etc': str(s.dvm_etc)}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_auto_detection(tmp_path):
    # real Pi before Install DVM: systemctl present, dvmhost missing -> REAL mode, configs to /usr/local/etc
    s = _settings(_env(tmp_path))
    assert s == {"dry_run": False, "reason": "real", "etc": str(tmp_path / "usr/local/etc")}
    # no systemctl (laptop / container) -> auto dry-run
    s = _settings(_env(tmp_path, with_systemctl=False))
    assert s["dry_run"] and s["reason"] == "auto: systemctl not found" and s["etc"].endswith("dryrun/etc")
    # explicit switches still win
    assert _settings(_env(tmp_path, MALILINK_DRY_RUN="1"))["reason"] == "MALILINK_DRY_RUN=1"
    assert _settings(_env(tmp_path, MALILINK_DEMO="1"))["reason"] == "MALILINK_DEMO=1"
    assert _settings(_env(tmp_path, MALILINK_DEMO="1", MALILINK_DRY_RUN="0"))["dry_run"] is False
    assert _settings(_env(tmp_path, with_systemctl=False, MALILINK_DRY_RUN="0"))["dry_run"] is False


def _serve(env, port):
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz").status_code == 200:
                return p
        except Exception:
            time.sleep(0.1)
    p.kill(); raise RuntimeError("no server")


def _port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.mark.skipif(os.geteuid() == 0, reason="as root the app would auto-rewrite; covered by the banner path otherwise")
def test_dry_run_setup_is_rewritten_in_real_mode(tmp_path):
    etc = tmp_path / "usr/local/etc"
    # 1) setup done while the app was in dry-run
    port = _port(); p = _serve(_env(tmp_path, MALILINK_DRY_RUN="1"), port)
    try:
        url = f"http://127.0.0.1:{port}"
        assert httpx.post(f"{url}/api/wizard/apply", json=W).json()["ok"]
        assert "DRY-RUN" in httpx.get(f"{url}/dashboard").text
    finally:
        p.terminate(); p.wait()
    assert not etc.exists() and (tmp_path / "data/dryrun/etc/config.yml").exists()
    # 2) same Pi in real mode (dvmhost not installed yet): banner + refuse to start + rewrite on request
    port = _port(); p = _serve(_env(tmp_path), port)
    try:
        url = f"http://127.0.0.1:{port}"
        m = httpx.get(f"{url}/api/mode").json()
        assert m["real"] and m["needs_rewrite"] and not m["dvmhost_installed"]
        page = httpx.get(f"{url}/dashboard").text
        assert "REAL MODE" in page and "Re-write needed" in page and "Not running as root" in page
        r = httpx.post(f"{url}/api/install/start")  # real installer, but we're not root -> refused up front
        assert r.status_code == 403 and "root" in r.json()["detail"]
        assert httpx.get(f"{url}/api/install/status").json()["dry_run"] is False
        r = httpx.post(f"{url}/api/service/dvmhost/start")
        assert r.status_code == 409 and "Install DVM" in r.json()["detail"]
        r = httpx.post(f"{url}/api/mode/rewrite").json()
        assert r["ok"] and (etc / "config.yml").exists() and (tmp_path / "var/log/dvm").is_dir()
        cfg = (etc / "config.yml").read_text()
        assert str(etc / "rid_acl.dat") in cfg and "dryrun" not in cfg
        assert not httpx.get(f"{url}/api/mode").json()["needs_rewrite"]
        assert "Re-write needed" not in httpx.get(f"{url}/dashboard").text
        # wizard in real mode writes straight to the real etc dir
        r = httpx.post(f"{url}/api/wizard/apply", json={**W, "callsign": "kd2xyz"}).json()
        assert r["dry_run"] is False and all(w.startswith(str(etc)) for w in r["written"])
        assert "KD2XYZ" in (etc / "config.yml").read_text()
    finally:
        p.terminate(); p.wait()
    assert not (tmp_path / "systemctl.log").exists() or "start" not in (tmp_path / "systemctl.log").read_text()


def test_real_mode_config_dir_unwritable_gives_clear_error(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o500)
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    env = _env(tmp_path, MALILINK_DVM_ETC=str(ro / "etc"))
    port = _port(); p = _serve(env, port)
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/api/wizard/apply", json=W)
        assert r.status_code == 403 and "root" in r.json()["detail"]
    finally:
        p.terminate(); p.wait(); ro.chmod(0o700)


def test_voice_rewrite_regenerates_from_saved_bundle(tmp_path):
    from app import mode, state
    from app.settings import settings
    bundle = {"channelNo": 3, "tx_mhz": 444.5125, "rx_mhz": 449.5125,
              "iden": {"channel_id": 2, "base_freq": 450000000, "spacing_khz": 6.25, "offset_mhz": 5.0, "bandwidth_khz": 12.5},
              "iden_table": "2,450000000,6.25,5.000,12.5,\n", "site": {"nac": "069", "wacn": "A1055", "sys_id": "105",
              "rfss_id": "1", "site_id": "1"}, "system_name": "ML105", "callsign": "ML105",
              "talkgroups": [{"tgid": 101, "name": "ML105 Main"}],
              "rpc": {"address": "100.64.0.1", "port": 9890, "password": "pw"},
              "network": {"address": "100.64.0.1", "port": 62031, "password": "fnepw", "peerId": 900003}}
    st = state.load()
    saved = __import__("json").loads(__import__("json").dumps(st))
    st.update(role="voice", setup_complete=True, rest_password="rp",
              radios={"list": [{"rid": 800001, "alias": "", "enabled": True, "ip": ""}], "lockdown": True})
    st["pairing"] = {"codes": {}, "voice_channels": [], "joined": {"bundle_full": bundle,
                     "local": {"name": "vc1", "modem_port": "/dev/ttyACM0", "ack_license": True, "ack_terms": True}}}
    state.save(st)
    try:
        r = mode.rewrite()
        assert r["ok"] and r["how"] == "regenerated"
        cfg = (settings.dvm_etc / "config.yml").read_text()
        assert "900003" in cfg and "/dev/ttyACM0" in cfg
        assert "800001,1," in (settings.dvm_etc / "rid_acl.dat").read_text()
    finally:
        state.save(saved)
