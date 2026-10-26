"""Radios page / rid_acl.dat (format + semantics verified against dvmhost RadioIdLookup/AccessControl)."""
import shutil

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app import state
from app.configgen import ridacl
from app.main import app
from app.settings import settings

HEADER = (settings.template_dir / "rid_acl.example.dat").read_text()
PRESET = {"system_name": "ML105", "callsign": "ML105", "topology": "trunked", "wacn": "A1055", "sys_id": "105",
          "nac": "069", "rfss_id": "1", "site_id": "1", "tx_mhz": 455.63125, "rx_mhz": 460.63125,
          "peer_id": 900002, "fne_peer_id": 900001, "vc_peer_start": 900003,
          "talkgroups": [{"tgid": 101, "name": "ML105 Main", "alias": "ML105 Main"}],
          "radios": [{"rid": 800001, "enabled": True}, {"rid": 800002, "enabled": True}], "rid_lockdown": True,
          "ack_license": True, "ack_terms": True}


def y(p):
    return YAML(typ="safe").load(p.read_text())


@pytest.fixture()
def client():
    def reset():
        p = settings.data_dir / "state.json"
        p.unlink(missing_ok=True)
        shutil.rmtree(settings.dvm_etc, ignore_errors=True)
    reset()
    yield TestClient(app)
    reset()


def test_render_parse_roundtrip():
    rs = [ridacl.Radio(rid=800002, alias="Unit, 2"), ridacl.Radio(rid=800001, enabled=False),
          ridacl.Radio(rid=5, ip="10.0.0.5")]
    text = ridacl.render(rs, HEADER)
    assert text.endswith("\n\n")  # "This file should always end with an empty line!"
    assert text.startswith("#")   # repo header kept
    body = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert body == ["5,1,,10.0.0.5,", "800001,0,", "800002,1,Unit 2,"]  # sorted, comma stripped from alias
    back = {r.rid: r for r in ridacl.parse(text)}
    assert back[800001].enabled is False and back[800002].alias == "Unit 2" and back[5].ip == "10.0.0.5"
    assert ridacl.parse(HEADER) == []  # example file is all comments


def test_csv_formats():
    rs, errs = ridacl.from_csv("rid,alias,enabled,ip\n800001,Alpha,1,\n800002,Bravo,0,\nnope,x,1,\n")
    assert [(r.rid, r.alias, r.enabled) for r in rs] == [(800001, "Alpha", True), (800002, "Bravo", False)]
    assert len(errs) == 1
    rs, _ = ridacl.from_csv("800001,1,Alpha,\n800002,0,Bravo,\n")  # pasted rid_acl.dat lines
    assert [(r.rid, r.enabled) for r in rs] == [(800001, True), (800002, False)]
    rs, _ = ridacl.from_csv("Radio ID,Name\n123,Joe\n")
    assert rs[0].rid == 123 and rs[0].alias == "Joe" and rs[0].enabled
    assert ridacl.from_csv(ridacl.to_csv(rs))[0] == rs


def test_api_crud_lockdown(client):
    r = client.get("/api/radios").json()  # demo mode seeds sample radios
    assert r["managed_here"] and r["counts"]["total"] >= 2 and not r["lockdown"]
    assert client.get("/radios").status_code == 200
    assert client.post("/api/radios", json={"rid": 800001, "alias": "dup"}).status_code == 409
    assert client.post("/api/radios", json={"rid": 0}).status_code == 422
    r = client.post("/api/radios", json={"rid": 4242, "alias": "New", "enabled": False}).json()
    assert any(x["rid"] == 4242 and not x["enabled"] for x in r["radios"])
    r = client.put("/api/radios/4242", json={"rid": 4242, "alias": "Renamed", "enabled": True}).json()
    assert next(x for x in r["radios"] if x["rid"] == 4242)["alias"] == "Renamed"
    assert client.delete("/api/radios/4242").json()["ok"]
    assert client.delete("/api/radios/4242").status_code == 404
    # replace via CSV; lockdown with nobody enabled is refused
    r = client.post("/api/radios/import", json={"csv": "rid,alias,enabled\n1,a,0\n", "mode": "replace"}).json()
    assert r["counts"] == {"total": 1, "enabled": 0, "blocked": 1}
    assert client.post("/api/radios/lockdown", json={"enabled": True}).status_code == 400
    client.post("/api/radios/import", json={"csv": "800001,1,\n800002,1,\n", "mode": "replace"})
    assert client.post("/api/radios/lockdown", json={"enabled": True}).json()["lockdown"] is True
    dat = client.get("/api/radios/rid_acl.dat").text
    assert [l for l in dat.splitlines() if l and l[0] != "#"] == ["800001,1,", "800002,1,"]
    assert client.get("/api/radios/export.csv").text.startswith("rid,alias,enabled,ip")


def test_preset_and_wizard_writes_acl(client):
    r = client.post("/api/wizard/apply", json=PRESET)
    assert r.status_code == 200, r.text
    etc = settings.dvm_etc
    cfg = y(etc / "config.yml")
    sc = cfg["system"]["config"]
    assert (sc["nac"], sc["sysId"], sc["netId"], sc["rfssId"], sc["siteId"]) == ("069", "105", "A1055", "1", "1")
    assert sc["channelId"] == 2 and sc["channelNo"] == "385"  # iden row 2 (450 MHz, 6.25 kHz, +5)
    assert cfg["system"]["cwId"]["callsign"] == "ML105"
    assert cfg["network"]["id"] == 900002
    assert cfg["system"]["radio_id"]["acl"] is True and cfg["system"]["radio_id"]["file"] == str(etc / "rid_acl.dat")
    fne = y(etc / "fne-config.yml")
    assert fne["master"]["peerId"] == 900001 and fne["system"]["radio_id"]["file"] == str(etc / "rid_acl.dat")
    lines = [l for l in (etc / "rid_acl.dat").read_text().splitlines() if l and l[0] != "#"]
    assert lines == ["800001,1,", "800002,1,"]
    tg = y(etc / "talkgroup_rules.yml")
    assert any(g["source"]["tgid"] == 101 and g["name"] == "ML105 Main" for g in tg["groupVoice"])
    st = client.get("/api/radios").json()
    assert st["lockdown"] and st["config_acl"] is True and [x["rid"] for x in st["radios"]] == [800001, 800002]
    # Radios page edits rewrite the file; lockdown off flips system.radio_id.acl in config.yml
    client.post("/api/radios", json={"rid": 800003, "alias": "Spare"})
    assert "800003,1,Spare," in (etc / "rid_acl.dat").read_text()
    client.post("/api/radios/lockdown", json={"enabled": False})
    assert y(etc / "config.yml")["system"]["radio_id"]["acl"] is False
    # saved wizard answers don't carry the preset list (Radios page stays the source of truth)
    assert "radios" not in state.load()["wizard"]


def test_voice_pi_is_read_only(client):
    st = state.load(); st.update(role="voice", pairing={"codes": {}, "voice_channels": [], "joined": {"cluster_key": "k"}})
    state.save(st)
    assert client.get("/api/radios").json()["managed_here"] is False
    assert client.post("/api/radios", json={"rid": 9}).status_code == 409
    body = {"radios": [{"rid": 800001}], "lockdown": True}
    assert client.post("/api/radios/sync", json=body).status_code == 403
    assert client.post("/api/radios/sync", json=body, headers={"X-Malilink-Cluster-Key": "k"}).json()["ok"]
    assert client.get("/api/radios").json()["radios"][0]["rid"] == 800001
