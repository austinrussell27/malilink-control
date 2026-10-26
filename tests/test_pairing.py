"""End-to-end pairing: two real app processes (control Pi + voice Pi) in dry-run mode."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
CC = {"system_name": "MALI", "callsign": "kd2abc", "topology": "trunked", "tx_mhz": 444.5, "rx_mhz": 449.5,
      "ack_license": True, "ack_terms": True}


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _start(data_dir, port, name):
    env = {**os.environ, "MALILINK_DATA_DIR": str(data_dir), "MALILINK_DRY_RUN": "1", "MALILINK_NODE_NAME": name}
    env.pop("MALILINK_DEMO", None)
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz").status_code == 200:
                return p
        except Exception:
            time.sleep(0.1)
    p.kill(); raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def pis():
    d1, d2, d3 = (Path(tempfile.mkdtemp()) for _ in range(3))
    p1, p2, p3 = _port(), _port(), _port()
    procs = [_start(d1, p1, "mali-cc"), _start(d2, p2, "mali-vc1"), _start(d3, p3, "mali-vc2")]
    yield [(f"http://127.0.0.1:{p}", d) for p, d in ((p1, d1), (p2, d2), (p3, d3))]
    for p in procs:
        p.terminate()


def y(path):
    return YAML(typ="safe").load(Path(path).read_text())


def test_pairing_flow(pis):
    (cc, ccd), (vc, vcd), (vc2, vc2d) = pis
    # code creation needs a control channel
    assert httpx.post(f"{cc}/api/pairing/codes").status_code == 409
    r = httpx.post(f"{cc}/api/wizard/apply", json=CC).json()
    assert r["ok"]
    cfg = y(ccd / "dryrun/etc/config.yml")
    assert cfg["protocols"]["p25"]["control"]["enable"] and cfg["protocols"]["p25"]["control"]["dedicated"]
    assert cfg["system"]["config"]["authoritative"] and cfg["system"]["config"]["supervisor"]
    assert cfg["system"]["config"]["voiceChNo"] == [] and cfg["network"]["rpcAddress"] == "0.0.0.0"
    assert (ccd / "dryrun/etc/fne-config.yml").exists()
    # CC refuses to start with no voice channels
    assert httpx.post(f"{cc}/api/service/dvmhost/start").status_code == 400

    code = httpx.post(f"{cc}/api/pairing/codes").json()["code"]
    join = {"control_url": cc, "code": code, "name": "mali-vc1", "modem_port": "/dev/ttyACM0",
            "ack_license": True, "ack_terms": True}
    assert httpx.post(f"{vc}/api/pairing/join", json={**join, "ack_terms": False}).status_code == 400
    r = httpx.post(f"{vc}/api/pairing/join", json=join, timeout=20)
    assert r.status_code == 200, r.text
    s = r.json()["summary"]
    assert s["channelNo"] == 3  # CC is ch 1, step 2 (12.5 kHz bw / 6.25 kHz spacing)
    assert abs(s["tx_mhz"] - 444.5125) < 1e-6 and abs(s["rx_mhz"] - 449.5125) < 1e-6
    # code is single use
    assert httpx.post(f"{vc2}/api/pairing/join", json={**join, "name": "x"}).status_code == 403

    ccfg = y(ccd / "dryrun/etc/config.yml")
    vcs = ccfg["system"]["config"]["voiceChNo"]
    assert len(vcs) == 1 and vcs[0]["channelNo"] == "3" and vcs[0]["rpcAddress"] == "127.0.0.1"
    vcfg = y(vcd / "dryrun/etc/config.yml")
    sc = vcfg["system"]["config"]
    assert not vcfg["protocols"]["p25"]["control"]["enable"] and not sc["authoritative"] and not sc["supervisor"]
    assert sc["channelNo"] == "3" and sc["nac"] == ccfg["system"]["config"]["nac"]
    assert sc["controlCh"]["rpcAddress"] == "127.0.0.1" and sc["controlCh"]["rpcPort"] == 9890
    assert sc["controlCh"]["notifyEnable"] is True
    # one shared RPC password (NetRPC uses it as the pre-shared key)
    rpc = ccfg["network"]["rpcPassword"]
    assert sc["controlCh"]["rpcPassword"] == rpc == vcfg["network"]["rpcPassword"] == vcs[0]["rpcPassword"]
    assert vcfg["network"]["address"] == "127.0.0.1" and vcfg["network"]["id"] == 100003
    assert vcfg["network"]["password"] == y(ccd / "dryrun/etc/fne-config.yml")["master"]["password"]
    assert (vcd / "dryrun/etc/iden_table.dat").read_text() == (ccd / "dryrun/etc/iden_table.dat").read_text()

    # control Pi restarts dvmhost when the list changes (if running)
    assert httpx.post(f"{cc}/api/service/dvmhost/start").json()["ok"]
    code = httpx.post(f"{cc}/api/pairing/codes").json()["code"]
    r = httpx.post(f"{vc2}/api/pairing/join", json={**join, "code": code, "name": "mali-vc2", "requested_tx_mhz": 444.55},
                   timeout=20).json()
    assert r["summary"]["channelNo"] == 9  # (444.55 - 444.49375) / 6.25 kHz
    lst = httpx.get(f"{cc}/api/pairing/voice-channels").json()
    assert [c["channelNo"] for c in lst["channels"]] == [1, 3, 9]

    # dashboard on the CC aggregates the voice Pis via /api/local/snapshot + cluster key
    snap = httpx.get(f"{cc}/api/snapshot", timeout=20).json()
    names = {n["name"]: n for n in snap["nodes"]}
    assert names["mali-vc1"]["reachable"] and names["mali-vc1"]["role"] == "voice"
    assert httpx.get(f"{vc}/api/local/snapshot").status_code == 403  # key required

    # remove a VC -> CC config rewritten
    assert httpx.delete(f"{cc}/api/pairing/voice-channels/9").json()["ok"]
    assert [v["channelNo"] for v in y(ccd / "dryrun/etc/config.yml")["system"]["config"]["voiceChNo"]] == ["3"]

    # voice Pi keeps what it needs to re-write its config later (e.g. paired while in dry-run)
    vst = __import__("json").loads((vcd / "state.json").read_text())
    assert vst["pairing"]["joined"]["bundle_full"]["rpc"]["password"] == rpc
    assert vst["pairing"]["joined"]["local"]["modem_port"] == "/dev/ttyACM0"

    # Radios: control Pi is the source of truth; VCs got the list at join and get every change pushed
    assert httpx.get(f"{vc}/api/radios").json()["managed_here"] is False
    r = httpx.post(f"{cc}/api/radios/import", json={"csv": "800001,1,Alpha\n800002,1,Bravo\n", "mode": "replace"},
                   timeout=20).json()
    assert [p["ok"] for p in r["pushed"]] == [True]  # vc2 was removed above
    r = httpx.post(f"{cc}/api/radios/lockdown", json={"enabled": True}, timeout=20).json()
    assert r["lockdown"] and r["restart_pending"]  # CC dvmhost is running -> acl needs a restart
    vtext = (vcd / "dryrun/etc/rid_acl.dat").read_text()
    assert "800001,1,Alpha," in vtext and vtext == (ccd / "dryrun/etc/rid_acl.dat").read_text()
    assert y(vcd / "dryrun/etc/config.yml")["system"]["radio_id"]["acl"] is True
    assert y(ccd / "dryrun/etc/config.yml")["system"]["radio_id"]["acl"] is True
    assert httpx.post(f"{vc}/api/radios", json={"rid": 5}).status_code == 409
    assert httpx.post(f"{vc}/api/radios/sync", json={"radios": [], "lockdown": False}).status_code == 403
    r = httpx.post(f"{cc}/api/radios/apply", json={"restart": True}, timeout=20).json()
    assert r["restart"]["ok"] and not r["restart_pending"]
    # regenerating the CC (pairing change) keeps the list + lockdown
    code = httpx.post(f"{cc}/api/pairing/codes").json()["code"]
    httpx.post(f"{vc2}/api/pairing/join", json={**join, "code": code, "name": "mali-vc2b"}, timeout=20)
    assert "800002,1,Bravo," in (ccd / "dryrun/etc/rid_acl.dat").read_text()
    assert y(ccd / "dryrun/etc/config.yml")["system"]["radio_id"]["acl"] is True
    assert "800002,1,Bravo," in (vc2d / "dryrun/etc/rid_acl.dat").read_text()

    # ---- unpair: control Pi removes a voice channel and the voice Pi releases itself
    assert "Unpair this Pi" in httpx.get(f"{vc2}/dashboard").text
    assert "unpairVc(" in httpx.get(f"{cc}/trunking").text
    assert httpx.post(f"{vc2}/api/pairing/released").status_code == 403          # cluster key required
    assert httpx.post(f"{cc}/api/pairing/vc-release", headers={"X-Malilink-Cluster-Key": "nope"}).status_code == 403
    ch2 = next(v["channelNo"] for v in httpx.get(f"{cc}/api/pairing/voice-channels").json()["voice_channels"]
               if v["name"] == "mali-vc2b")
    r = httpx.post(f"{cc}/api/pairing/voice-channels/{ch2}/unpair", timeout=20).json()
    assert r["ok"] and r["removed"] == "mali-vc2b" and r["voice_pi"]["ok"]
    assert [v["channelNo"] for v in y(ccd / "dryrun/etc/config.yml")["system"]["config"]["voiceChNo"]] == ["3"]
    vst2 = __import__("json").loads((vc2d / "state.json").read_text())
    assert vst2["role"] is None and not vst2["setup_complete"] and (vc2d / "state.json.bak").exists()
    assert not (vc2d / "dryrun/etc/config.yml").exists() and (vc2d / "dryrun/etc/config.yml.bak").exists()

    # ---- unpair from the voice Pi: it resets itself and the control Pi drops it
    r = httpx.post(f"{vc}/api/wizard/apply", json={**CC, "topology": "conventional"})
    assert r.status_code == 409 and "Unpair this Pi" in r.json()["detail"]
    r = httpx.post(f"{vc}/api/pairing/reset", json={"notify": True}, timeout=20).json()
    assert r["ok"] and r["previous_role"] == "voice" and r["notified"][0]["ok"]
    assert httpx.get(f"{cc}/api/pairing/voice-channels").json()["voice_channels"] == []
    assert all(n.get("role") != "voice" for n in __import__("json").loads((ccd / "state.json").read_text())["nodes"])
    assert httpx.post(f"{vc}/api/wizard/apply", json={**CC, "topology": "conventional"}).json()["ok"]  # fresh again
