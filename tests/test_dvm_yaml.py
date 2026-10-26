"""Parse generated configs with dvmhost's OWN YAML parser (src/common/yaml) via tests/dvmyaml/yamlcheck.
Skipped unless the helper is built:  tests/dvmyaml/build.sh /path/to/dvmhost"""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MALILINK_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("MALILINK_DRY_RUN", "1")
from app.configgen.generate import VoiceLocal, WizardInput, generate, generate_voice_channel  # noqa: E402

BIN = Path(__file__).parent / "dvmyaml" / "yamlcheck"
pytestmark = pytest.mark.skipif(not BIN.exists(), reason="yamlcheck not built (tests/dvmyaml/build.sh)")
W = dict(system_name="MALI", callsign="kd2abc", tx_mhz=444.5, rx_mhz=449.5, ack_license=True, ack_terms=True)


def check(text):
    f = Path(tempfile.mktemp(suffix=".yml")); f.write_text(text)
    out = subprocess.run([str(BIN), str(f)], capture_output=True, text=True).stdout
    assert "PARSE_FAIL" not in out
    return out


def test_conventional():
    out = check(generate(WizardInput(**W)).files["config.yml"])
    assert "control.enable=0 dedicated=0" in out and "channelNo=1 nac=293" in out
    assert "voiceChNo.size=1" in out and "iAgree=1" in out and "cwid=KD2ABC" in out
    assert "time=2 acl=0" in out


def test_previous_system_preset_lockdown():
    w = WizardInput(system_name="ML105", callsign="ML105", topology="trunked", wacn="A1055", sys_id="105", nac="069",
                    tx_mhz=455.63125, rx_mhz=460.63125, peer_id=900002, ack_license=True, ack_terms=True)
    out = check(generate(w, None, [{"name": "vc1", "channelNo": 0x457, "rpc_address": "100.64.0.2"}],
                         rid={"radios": [{"rid": 800001}, {"rid": 800002}], "lockdown": True}).files["config.yml"])
    assert "channelId=2 channelNo=901 nac=69 sysId=105 netId=A1055" in out  # parsed as hex like dvmhost
    assert "vc[0] id=2 no=1111" in out and "id=900002" in out and "time=2 acl=1" in out


def test_control_and_voice():
    g = generate(WizardInput(**W, topology="trunked"), None,
                 [{"name": "vc1", "channelNo": 3, "rpc_address": "100.64.0.2"}])
    out = check(g.files["config.yml"])
    assert "control.enable=1 dedicated=1" in out and "authoritative=1 supervisor=1" in out
    assert "voiceChNo.size=1" in out and "vc[0] id=1 no=3 rpc=100.64.0.2:9890" in out
    assert "rpcAddress=0.0.0.0" in out
    empty = check(generate(WizardInput(**W, topology="trunked"), None, []).files["config.yml"])
    assert "voiceChNo.size=0" in empty
    e = g.plan.entry
    bundle = {"channelNo": 3, "iden": {"channel_id": e.channel_id, "base_freq": e.base_freq, "spacing_khz": e.spacing_khz,
                                       "offset_mhz": e.offset_mhz, "bandwidth_khz": e.bandwidth_khz},
              "iden_table": g.files["iden_table.dat"], "site": {"nac": "293", "wacn": "BB800", "sys_id": "001", "rfss_id": "1", "site_id": "1"},
              "system_name": "MALI", "callsign": "KD2ABC", "talkgroups": [{"tgid": 1, "name": "TG1"}],
              "rpc": {"address": "100.64.0.1", "port": 9890, "password": "pw"},
              "network": {"address": "100.64.0.1", "port": 62031, "password": "fnepw", "peerId": 100003}}
    v = check(generate_voice_channel(bundle, VoiceLocal(name="vc1", ack_terms=True)).files["config.yml"])
    assert "control.enable=0 dedicated=0" in v and "authoritative=0 supervisor=0" in v
    assert "controlCh=100.64.0.1:9890 pw=pw notify=1" in v and "channelNo=3" in v
    assert "addr=100.64.0.1:62031 id=100003" in v
