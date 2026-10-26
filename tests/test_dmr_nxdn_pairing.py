"""Live DMR / NXDN trunked pairing + auto-join (two app processes, dry-run), Program radio page per
protocol, NXDN radio-ID limits and the protocol-aware demo."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from ruamel.yaml import YAML

from test_pairing import ROOT, _port, _start

BASE = {"system_name": "MALI", "callsign": "kd2abc", "tx_mhz": 444.5, "rx_mhz": 449.5,
        "ack_license": True, "ack_terms": True}


def y(path):
    return YAML(typ="safe").load(Path(path).read_text())


@pytest.fixture(scope="module")
def pis():
    d1, d2 = (Path(tempfile.mkdtemp()) for _ in range(2))
    p1, p2 = _port(), _port()
    procs = [_start(d1, p1, "mali-cc"), _start(d2, p2, "mali-vc1")]
    yield [(f"http://127.0.0.1:{p}", d) for p, d in ((p1, d1), (p2, d2))]
    for p in procs:
        p.terminate()


def _pair(cc, vc, name="mali-vc1", **extra):
    code = httpx.post(f"{cc}/api/pairing/codes").json()["code"]
    r = httpx.post(f"{vc}/api/pairing/join", json={"control_url": cc, "code": code, "name": name,
                                                   "ack_license": True, "ack_terms": True, **extra}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["summary"]


@pytest.mark.parametrize("mode", ["dmr", "nxdn"])
def test_trunked_pairing(pis, mode):
    (cc, ccd), (vc, vcd) = pis
    # start from a fresh pair each time
    httpx.post(f"{vc}/api/pairing/reset", json={"notify": True}, timeout=20)
    httpx.post(f"{cc}/api/pairing/reset", json={"notify": False}, timeout=20)
    extra = {"dmr": {"color_code": 5, "dmr_tscc_slot": 1, "talkgroups": [{"tgid": 3100, "slot": 1}, {"tgid": 3100, "slot": 2}]},
             "nxdn": {"ran": 21, "sys_id": "2A", "talkgroups": [{"tgid": 100}]}}[mode]
    r = httpx.post(f"{cc}/api/wizard/apply", json={**BASE, "mode": mode, "topology": "trunked", **extra})
    assert r.status_code == 200, r.text
    c = y(ccd / "dryrun/etc/config.yml")
    assert c["protocols"][mode]["control"]["enable"] and c["protocols"][mode]["control"]["dedicated"]
    assert not c["protocols"]["p25"]["enable"]

    s = _pair(cc, vc)
    assert s["mode"] == mode
    step = 1 if mode == "nxdn" else 2   # NXDN 6.25 kHz channels vs 12.5 kHz
    assert s["channelNo"] == 1 + step
    assert abs(s["tx_mhz"] - (444.5 + step * 0.00625)) < 1e-6

    v = y(vcd / "dryrun/etc/config.yml")
    assert [p for p in ("dmr", "p25", "nxdn") if v["protocols"][p]["enable"]] == [mode]
    assert not v["protocols"][mode]["control"]["enable"]
    sc = v["system"]["config"]
    assert sc["controlCh"]["notifyEnable"] and not sc["authoritative"]
    if mode == "dmr":
        assert sc["colorCode"] == 5
        tgs = y(vcd / "dryrun/etc/talkgroup_rules.yml")["groupVoice"]
        assert sorted(t["source"]["slot"] for t in tgs) == [1, 2]
    else:
        assert sc["ran"] == 21 and int(sc["sysId"], 16) == 0x2A
    cv = y(ccd / "dryrun/etc/config.yml")["system"]["config"]["voiceChNo"]
    assert [x["channelNo"] for x in cv] == [format(1 + step, "X")]

    # Program radio page (control Pi) shows protocol values; voice Pi points to the control Pi
    html = httpx.get(f"{cc}/program").text
    if mode == "dmr":
        assert 'id="pgColorCode"' in html and "TS1" in html and "Tier III" in html
    else:
        assert 'id="pgRan"' in html and "Location ID" in html and str((0x2A << 12) + 1) in html
    assert "voice channel" in httpx.get(f"{vc}/program").text

    # voice Pi dashboard snapshot carries protocol + site label
    snap = httpx.get(f"{cc}/api/snapshot", timeout=20).json()
    me = snap["nodes"][0]
    assert me["protocol"] == mode.upper()
    assert me["site_label"] == ("CC 5 · TS 1+2" if mode == "dmr" else "RAN 21")
    vcn = next(n for n in snap["nodes"] if n["name"] == "mali-vc1")
    assert vcn["protocol"] == mode.upper()

    # NXDN radio IDs are 16-bit
    if mode == "nxdn":
        assert httpx.post(f"{cc}/api/radios", json={"rid": 70000}).status_code == 400
        assert httpx.post(f"{cc}/api/radios", json={"rid": 65535}, timeout=20).status_code == 200
        r = httpx.post(f"{cc}/api/radios/import", json={"csv": "65000,1,a\n70001,1,b\n", "mode": "merge"}, timeout=20).json()
        assert r["imported"] == 1 and any("70001" in e for e in r["errors"])
        assert httpx.get(f"{cc}/api/radios").json()["protocol"] == "nxdn"
    else:
        assert httpx.post(f"{cc}/api/radios", json={"rid": 3101001}, timeout=20).status_code == 200


def test_conventional_program_pages(pis):
    (cc, ccd), (vc, vcd) = pis
    httpx.post(f"{vc}/api/pairing/reset", json={"notify": True}, timeout=20)
    httpx.post(f"{cc}/api/pairing/reset", json={"notify": False}, timeout=20)
    r = httpx.post(f"{cc}/api/wizard/apply", json={**BASE, "mode": "dmr", "color_code": 3, "dmr_slot1": False,
                                                    "talkgroups": [{"tgid": 9, "slot": 2}]})
    assert r.status_code == 200, r.text
    html = httpx.get(f"{cc}/program").text
    assert "DMR Tier II conventional" in html and "TS2" in html and 'id="pgDmrTgs"' in html
    r = httpx.post(f"{cc}/api/wizard/apply", json={**BASE, "mode": "nxdn", "ran": 7})
    html = httpx.get(f"{cc}/program").text
    assert "NXDN conventional" in html and "6.25 kHz" in html and 'id="pgRan"' in html
    # wizard page has the per-protocol fields
    setup = httpx.get(f"{cc}/setup").text
    for fid in ("color_code", "ran", "dmr_net_id", "dmr_slot1", "dmr_slot2", "dmr_tscc_slot", "dmr_beacons"):
        assert f'id="{fid}"' in setup
    assert "coming next" not in setup
    calc = httpx.post(f"{cc}/api/calc/freq", json={"tx_mhz": 444.5, "rx_mhz": 449.5, "mode": "nxdn"}).json()
    assert calc["iden_line"].endswith(",6.25,")


@pytest.mark.parametrize("proto", ["dmr", "nxdn"])
def test_demo_protocol(proto):
    d, p = Path(tempfile.mkdtemp()), _port()
    env = {**os.environ, "MALILINK_DATA_DIR": str(d), "MALILINK_DEMO": "1", "MALILINK_DEMO_PROTOCOL": proto}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(p)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{p}/healthz").status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        s = httpx.get(f"http://127.0.0.1:{p}/api/snapshot").json()
        assert s["protocol"] == proto and s["nodes"][0]["mode"] == proto.upper()
        act = httpx.get(f"http://127.0.0.1:{p}/api/logs/mali-vc1?kind=activity").json()
        lines = act.get("lines", act) if isinstance(act, dict) else act
        assert any(f" {proto.upper()} " in ln for ln in lines)
        if proto == "dmr":
            assert any("Slot " in ln for ln in lines)
    finally:
        proc.terminate()
