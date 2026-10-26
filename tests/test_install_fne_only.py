"""'Install dvmfne service only': never rebuilds dvmhost / touches configs; builds only the dvmfne target if needed."""
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import installer
from app.settings import settings


def _wait(c):
    for _ in range(200):
        s = c.get("/api/install/status").json()
        if s["state"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError("install did not finish")


def test_dry_run_flow(monkeypatch):
    monkeypatch.setattr(installer, "DRY_DELAY", 0)
    j = installer.job
    j.sim = {k: False for k in j.sim}; j.state = "idle"
    c = TestClient(app)
    s = c.get("/api/install/status").json()
    assert s["pieces"] == {"dvmhost_bin": False, "dvmfne_bin": False, "dvmfne_unit": False, "build_tree": False}
    r = c.post("/api/install/fne")
    assert r.status_code == 409 and "Install / update DVM" in r.json()["detail"]  # no silent full rebuild
    assert c.post("/api/install/start?with_fne=false").json()["ok"]
    s = _wait(c)
    assert s["state"] == "ok" and s["pieces"]["dvmfne_bin"] and not s["pieces"]["dvmfne_unit"]
    assert c.post("/api/install/fne").json()["ok"]
    s = _wait(c)
    assert s["state"] == "ok" and s["kind"] == "fne" and s["pieces"]["dvmfne_unit"]
    assert [x["id"] for x in s["steps"]] == [i for i, _ in installer.FNE_STEPS]
    assert s["done_steps"] == [i for i, _ in installer.FNE_STEPS]
    assert "skipped (already installed)" in "\n".join(c.get("/api/install/status?since=0").json()["lines"])
    assert "Install dvmfne service only" in c.get("/setup").text


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    fake = tmp_path / "fakebin"; fake.mkdir()
    calls = tmp_path / "calls.log"
    for name, body in {
        "make": f'echo "make $@" >> {calls}; touch "$2/dvmfne"',  # make -C <build> -jN dvmfne
        "systemctl": f'echo "systemctl $@" >> {calls}',
        "strip": "exit 0", "useradd": f'echo "useradd $@" >> {calls}', "usermod": "exit 0",
        "chown": "exit 0", "id": "exit 1",
    }.items():
        f = fake / name; f.write_text("#!/bin/bash\n" + body + "\n"); f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(settings, "src_dir", tmp_path / "opt/src/dvmhost")
    monkeypatch.setattr(settings, "dvm_bin", tmp_path / "usr/local/bin")
    monkeypatch.setattr(settings, "dvm_log_dir", tmp_path / "var/log/dvm")
    monkeypatch.setattr(installer, "UNIT_DIRS", [tmp_path / "lib/systemd/system"])
    (tmp_path / "usr/local/bin").mkdir(parents=True)
    etc = tmp_path / "usr/local/etc"; etc.mkdir(parents=True); (etc / "config.yml").write_text("keep me\n")
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}

    def run():
        r = subprocess.run(["bash", "-c", installer.fne_only_script()], env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r, (calls.read_text() if calls.exists() else "")
    return tmp_path, run


def test_binary_present_only_installs_unit(sandbox):
    tmp, run = sandbox
    b = tmp / "usr/local/bin/dvmfne"; b.write_text("bin"); b.chmod(0o755)
    r, calls = run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "make" not in calls and "not rebuilding" in r.stdout
    assert "systemctl daemon-reload" in calls and "systemctl enable dvmfne" in calls and "start" not in calls
    assert (tmp / "lib/systemd/system/dvmfne.service").read_text().count("ExecStart=/usr/local/bin/dvmfne") == 1
    assert (tmp / "usr/local/etc/config.yml").read_text() == "keep me\n"
    assert not (tmp / "usr/local/bin/dvmhost").exists()


def test_missing_binary_builds_only_dvmfne_target(sandbox):
    tmp, run = sandbox
    build = tmp / "opt/src/dvmhost/build"; build.mkdir(parents=True); (build / "Makefile").write_text("")
    r, calls = run()
    assert r.returncode == 0, r.stdout + r.stderr
    make = [l for l in calls.splitlines() if l.startswith("make")]
    assert len(make) == 1 and make[0].endswith(" dvmfne") and "install" not in make[0]  # no `make install`
    assert (tmp / "usr/local/bin/dvmfne").exists() and "systemctl enable dvmfne" in calls
    assert "useradd" in calls  # user created when missing


def test_nothing_built_refuses(sandbox):
    tmp, run = sandbox
    r, calls = run()
    assert r.returncode == 3 and "Run 'Install / update DVM' first" in r.stdout
    assert "systemctl" not in calls and "make" not in calls
