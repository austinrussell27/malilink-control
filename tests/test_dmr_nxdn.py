"""DMR + NXDN support (0.4.0): generator, validation, voice-channel bundle, dvmhost YAML parse,
log parsing, REST allow-list, Program radio page, demo, radios ID limits and live pairing."""
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from ruamel.yaml import YAML

os.environ.setdefault("MALILINK_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("MALILINK_DRY_RUN", "1")
from pydantic import ValidationError  # noqa: E402

from app.configgen.generate import (MODES, VoiceLocal, WizardInput, channel_for_freq, generate,  # noqa: E402
                                    generate_voice_channel, next_free_channel, nxdn_location_id, site_dict)
from app.data import dvmrest  # noqa: E402
from app.data.logparse import parse_activity  # noqa: E402

W = dict(system_name="MALI", callsign="kd2abc", tx_mhz=444.5, rx_mhz=449.5, ack_license=True, ack_terms=True)
BIN = Path(__file__).parent / "dvmyaml" / "yamlcheck"


def y(text):
    return YAML(typ="safe").load(text)


def gen(mode, topology="conventional", vcs=None, **kw):
    g = generate(WizardInput(**W, mode=mode, topology=topology, **kw), None, vcs)
    return g, y(g.files["config.yml"])


@pytest.mark.parametrize("mode", ["p25", "dmr", "nxdn"])
@pytest.mark.parametrize("topology", ["conventional", "trunked"])
def test_exactly_one_protocol_and_control(mode, topology):
    vcs = [{"name": "vc1", "channelNo": 3, "rpc_address": "100.64.0.2"}] if topology == "trunked" else None
    g, c = gen(mode, topology, vcs)
    pr = c["protocols"]
    assert [p for p in ("dmr", "p25", "nxdn") if pr[p]["enable"]] == [mode]
    assert c["system"]["fixedMode"] is True
    trunked = topology == "trunked"
    for p in ("dmr", "p25", "nxdn"):
        assert pr[p]["control"]["enable"] is (trunked and p == mode)
        assert pr[p]["control"]["dedicated"] is (trunked and p == mode)
    assert c["system"]["config"]["authoritative"] is True
    assert pr["dmr"]["beacons"]["enable"] is False
    assert g.radio["protocol"] == mode and g.radio["trunked"] is trunked
    if trunked:
        assert c["system"]["config"]["supervisor"] is True
        assert c["system"]["config"]["voiceChNo"][0]["channelNo"] == "3"
    assert not g.errors


def test_dmr_keys():
    g, c = gen("dmr", color_code=7, dmr_net_id="2A", dmr_slot2=False, site_id="1F",
               talkgroups=[{"tgid": 3100, "name": "a", "slot": 1}, {"tgid": 3100, "name": "b", "slot": 2},
                           {"tgid": 9990, "parrot": True, "slot": 2}])
    sc = c["system"]["config"]
    assert sc["colorCode"] == 7 and sc["dmrNetId"] == "2A" and sc["siteId"] == "1F"
    assert c["network"]["slot1"] is True and c["network"]["slot2"] is False
    tg = y(g.files["talkgroup_rules.yml"])["groupVoice"]
    assert [(t["source"]["tgid"], t["source"]["slot"]) for t in tg] == [(3100, 1), (3100, 2), (9990, 2)]
    assert g.radio["dmr"] == {"color_code": 7, "net_id": "2A", "site_id": "1F", "slots": [1], "tscc_slot": None}
    assert g.radio["band_plan"]["bandwidth_khz"] == 12.5
    assert g.radio["mode"].startswith("DMR Tier II")


def test_dmr_trunked_tscc_slot_and_beacons():
    g, c = gen("dmr", "trunked", [], dmr_tscc_slot=2)
    assert c["protocols"]["dmr"]["control"]["slot"] == 2
    assert g.radio["dmr"]["tscc_slot"] == 2 and "Tier III" in g.radio["mode"]
    _, c = gen("dmr", dmr_beacons=True)
    assert c["protocols"]["dmr"]["beacons"]["enable"] is True
    with pytest.raises(ValueError, match="beacons need a duplex"):
        generate(WizardInput(**{**W, "rx_mhz": 444.5}, mode="dmr", dmr_beacons=True))
    with pytest.raises(ValueError, match="duplex"):
        generate(WizardInput(**{**W, "rx_mhz": 444.5}, mode="dmr", topology="trunked"))


def test_nxdn_keys_and_location():
    g, c = gen("nxdn", ran=42, sys_id="3FF", site_id="A", talkgroups=[{"tgid": 65535, "slot": 2}])
    assert c["system"]["config"]["ran"] == 42
    assert y(g.files["talkgroup_rules.yml"])["groupVoice"][0]["source"]["slot"] == 1  # slot ignored for NXDN
    loc = nxdn_location_id("3FF", "A")
    assert loc == (0x3FF << 12) + 0xA
    assert g.radio["nxdn"] == {"ran": 42, "sys_id": "3FF", "site_id": "A", "location_id": loc,
                               "location_id_hex": format(loc, "06X")}
    # NXDN uses 6.25 kHz channels -> a dedicated 6.25 kHz iden row, channel step 1
    assert g.plan.entry.bandwidth_khz == 6.25 and g.radio["mode"].startswith("NXDN conventional")
    row = [ln for ln in g.files["iden_table.dat"].splitlines() if ln and not ln.startswith("#")][-1]
    assert row.split(",")[2:5] == ["6.25", "5.00000", "6.25"]  # spacing, offset, bandwidth (not rounded to 6.2)


def test_nxdn_channel_limits():
    g, _ = gen("nxdn", "trunked", [])
    assert next_free_channel(g.plan.channel_no, {g.plan.channel_no}, 1, MODES["nxdn"]["max_ch"]) == g.plan.channel_no + 1
    with pytest.raises(ValueError):
        next_free_channel(1023, {1023}, 1, MODES["nxdn"]["max_ch"])
    with pytest.raises(ValueError, match="band plan"):
        channel_for_freq(g.plan.entry, 444.5 + 1024 * 0.00625, MODES["nxdn"]["max_ch"])


@pytest.mark.parametrize("bad", [
    dict(mode="dmr", color_code=16), dict(mode="dmr", color_code=-1), dict(mode="nxdn", ran=64),
    dict(mode="dmr", dmr_net_id="80"), dict(mode="dmr", site_id="20"), dict(mode="nxdn", sys_id="400"),
    dict(mode="nxdn", talkgroups=[{"tgid": 65536}]), dict(mode="p25", talkgroups=[{"tgid": 65536}]),
    dict(mode="dmr", talkgroups=[{"tgid": 5, "slot": 1}, {"tgid": 5, "slot": 1}]),
    dict(mode="nxdn", talkgroups=[{"tgid": 5}, {"tgid": 5}]),
    dict(mode="dmr", dmr_slot1=False, dmr_slot2=False), dict(mode="dmr", dmr_tscc_slot=3),
    dict(mode="dmr", talkgroups=[{"tgid": 1, "slot": 3}]),
])
def test_validation(bad):
    with pytest.raises(ValidationError):
        WizardInput(**W, **bad)


def test_dmr_allows_24bit_tg():
    WizardInput(**W, mode="dmr", talkgroups=[{"tgid": 3100001, "slot": 1}])


def _bundle(mode, **kw):
    w = WizardInput(**W, mode=mode, topology="trunked", **kw)
    g = generate(w, None, [])
    e = g.plan.entry
    ch = g.plan.channel_no + (1 if mode == "nxdn" else 2)
    return {"mode": mode, "channelNo": ch,
            "iden": {"channel_id": e.channel_id, "base_freq": e.base_freq, "spacing_khz": e.spacing_khz,
                     "offset_mhz": e.offset_mhz, "bandwidth_khz": e.bandwidth_khz},
            "iden_table": g.files["iden_table.dat"], "site": site_dict(w),
            "system_name": "MALI", "callsign": "KD2ABC", "talkgroups": [t.model_dump() for t in w.talkgroups],
            "rpc": {"address": "100.64.0.1", "port": 9890, "password": "pw"},
            "network": {"address": "100.64.0.1", "port": 62031, "password": "fnepw", "peerId": 100003}}


@pytest.mark.parametrize("mode", ["dmr", "nxdn"])
def test_voice_channel_from_bundle(mode):
    b = _bundle(mode, color_code=9, ran=33, dmr_slot2=False)
    g = generate_voice_channel(b, VoiceLocal(name="vc1", ack_terms=True))
    c = y(g.files["config.yml"])
    pr = c["protocols"]
    assert [p for p in ("dmr", "p25", "nxdn") if pr[p]["enable"]] == [mode]
    assert not any(pr[p]["control"]["enable"] for p in ("dmr", "p25", "nxdn"))
    assert pr["dmr"]["beacons"]["enable"] is False
    sc = c["system"]["config"]
    assert sc["authoritative"] is False and sc["controlCh"]["notifyEnable"] is True
    assert sc["controlCh"]["rpcAddress"] == "100.64.0.1"
    assert sc["colorCode"] == 9 and sc["ran"] == 33
    if mode == "dmr":
        assert c["network"]["slot2"] is False
    assert g.radio["protocol"] == mode


def test_old_bundle_without_mode_is_p25():
    b = _bundle("p25"); b.pop("mode")
    c = y(generate_voice_channel(b, VoiceLocal(name="vc1", ack_terms=True)).files["config.yml"])
    assert c["protocols"]["p25"]["enable"] and not c["protocols"]["dmr"]["enable"]


# ---------------------------------------------------------------- dvmhost's own YAML parser
def check(text):
    f = Path(tempfile.mktemp(suffix=".yml")); f.write_text(text)
    out = subprocess.run([str(BIN), str(f)], capture_output=True, text=True).stdout
    assert "PARSE_FAIL" not in out
    return out


needs_bin = pytest.mark.skipif(not BIN.exists(), reason="yamlcheck not built (tests/dvmyaml/build.sh)")


@needs_bin
def test_dvmyaml_dmr_conventional():
    g, _ = gen("dmr", color_code=12, dmr_net_id="7F", dmr_slot1=False, dmr_beacons=True)
    out = check(g.files["config.yml"])
    assert "dmr.enable=1 dmr.control.enable=0 dmr.dedicated=0" in out and "dmr.beacons=1" in out
    assert "p25.enable=0" in out and "nxdn.enable=0" in out
    assert "colorCode=12 dmrNetId=7F" in out and "fixedMode=1 duplex=1" in out
    assert "net.slot1=0 net.slot2=1" in out


@needs_bin
def test_dvmyaml_dmr_trunked_and_vc():
    g, _ = gen("dmr", "trunked", [{"name": "vc1", "channelNo": 3, "rpc_address": "100.64.0.2"}], dmr_tscc_slot=2)
    out = check(g.files["config.yml"])
    assert "dmr.enable=1 dmr.control.enable=1 dmr.dedicated=1 dmr.slot=2 dmr.beacons=0" in out
    assert "authoritative=1 supervisor=1" in out and "vc[0] id=1 no=3 rpc=100.64.0.2:9890" in out
    v = check(generate_voice_channel(_bundle("dmr"), VoiceLocal(name="vc1", ack_terms=True)).files["config.yml"])
    assert "dmr.enable=1 dmr.control.enable=0 dmr.dedicated=0" in v and "notify=1" in v
    assert "authoritative=0 supervisor=0" in v


@needs_bin
def test_dvmyaml_nxdn():
    g, _ = gen("nxdn", ran=63)
    out = check(g.files["config.yml"])
    assert "nxdn.enable=1 nxdn.control.enable=0 nxdn.dedicated=0" in out and "ran=63" in out
    assert "p25.enable=0" in out and "dmr.enable=0" in out
    g, _ = gen("nxdn", "trunked", [{"name": "vc1", "channelNo": 2, "rpc_address": "100.64.0.2"}])
    out = check(g.files["config.yml"])
    assert "nxdn.enable=1 nxdn.control.enable=1 nxdn.dedicated=1" in out and "vc[0] id=1 no=2" in out
    v = check(generate_voice_channel(_bundle("nxdn"), VoiceLocal(name="vc1", ack_terms=True)).files["config.yml"])
    assert "nxdn.enable=1 nxdn.control.enable=0" in v and "notify=1" in v


# ---------------------------------------------------------------- activity log parsing
def _a(t, mode, src, msg):
    return f"A: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))}.000 {mode} {src} {msg}"


def test_logparse_dmr_two_slots():
    t = time.time() - 10
    lines = [
        _a(t, "DMR", "RF", "Slot 1 RF voice header from 3101001 to TG 3100"),
        _a(t + 1, "DMR", "Net", "Slot 2 network voice header from 3101002 to TG 9"),
        _a(t + 2, "DMR", "RF", "Slot 1 group grant request from 3101007 to TG 2"),
        _a(t + 2, "DMR", "RF", "Slot 1 group grant request 3101007 to TG 2 denied"),
        _a(t + 3, "DMR", "RF", "Slot 1 RF voice rejection from 999 to TG 3100 "),
        _a(t + 4, "DMR", "RF", "Slot 1 RF end of voice transmission, 4.0 seconds, BER: 0.1%"),
        _a(t + 5, "DMR", "Net", "Slot 2 network late entry from 3101002 to TG 9"),
    ]
    ev, live = parse_activity(lines, "n")
    assert [(c["slot"], c["srcId"], c["dstId"]) for c in live] == [(2, 3101002, 9)]
    kinds = [e["event"] for e in reversed(ev)]
    assert kinds == ["call start", "call start", "grant request", "grant denied", "rejected", "call end"]
    end = ev[0]
    assert end["slot"] == 1 and end["srcId"] == 3101001 and end["duration"] == 4.0 and end["mode"] == "DMR"
    lost = parse_activity(lines[:1] + [_a(t + 3, "DMR", "RF", "Slot 1 RF voice transmission lost, 2.5 seconds, BER: 3.0%, loss count: 4")], "n")
    assert lost[0][0]["event"] == "call lost" and lost[0][0]["duration"] == 2.5 and not lost[1]


def test_logparse_nxdn():
    t = time.time() - 10
    lines = [
        _a(t, "NXDN", "RF", "RF voice transmission from 4201 to TG 100"),
        _a(t + 1, "NXDN", "RF", "RF late entry from 4201 to TG 100"),
        _a(t + 3, "NXDN", "RF", "RF end of transmission, 3.2 seconds, BER: 0.4%"),
        _a(t + 4, "NXDN", "Net", "network encrypted voice transmission from 4300 to TG 7"),
        _a(t + 5, "NXDN", "Net", "network end of transmission, 1.0 seconds"),
        _a(t + 6, "NXDN", "RF", "RF voice transmission from 4400 to 4401"),
        _a(t + 7, "NXDN", "RF", "transmission lost, 1.5 seconds, BER: 9.0%, loss count: 3"),
        _a(t + 8, "NXDN", "RF", "group grant request from 4201 to TG 100 queued"),
    ]
    ev, live = parse_activity(lines, "n")
    assert not live
    kinds = [e["event"] for e in reversed(ev)]
    assert kinds == ["call start", "call end", "call start", "call end", "call start", "call lost", "grant queued"]
    assert ev[-2]["srcId"] == 4201 and ev[-2]["duration"] == 3.2 and ev[-2]["slot"] is None


def test_rest_allow_list():
    for p in ("/dmr/report-affiliations", "/nxdn/report-affiliations", "/p25/report-affiliations"):
        assert p in dvmrest.READ_ONLY_PATHS
    for bad in ("/dmr/beacon", "/dmr/cc-enable", "/dmr/cc-broadcast", "/nxdn/cc", "/nxdn/cc-enable", "/dmr/rid",
                "/mdm/mode", "/grant-tg"):
        assert bad not in dvmrest.READ_ONLY_PATHS
        with pytest.raises(PermissionError):
            dvmrest.DvmRest("http://127.0.0.1:1", "x").get(bad)
