import os, tempfile
os.environ.setdefault("MALILINK_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("MALILINK_DEMO", "1")
os.environ.setdefault("MALILINK_DRY_RUN", "1")
from fastapi.testclient import TestClient  # noqa: E402
from ruamel.yaml import YAML  # noqa: E402
from app.main import app  # noqa: E402
from app.settings import settings  # noqa: E402

c = TestClient(app)
W = {"system_name": "MALI1", "callsign": "kd2abc", "tx_mhz": 444.5, "rx_mhz": 449.5, "ack_license": True, "ack_terms": True}


def test_pages():
    for p in ["/setup", "/dashboard", "/program", "/logs", "/trunking", "/about", "/node/mali-cc"]:
        assert c.get(p).status_code == 200, p


def test_wizard_generates_real_keys():
    r = c.post("/api/wizard/preview", json=W).json()
    assert not r["errors"]
    cfg = YAML(typ="safe").load(r["files"]["config.yml"])
    sc = cfg["system"]["config"]
    assert cfg["iAgreeNotToBeStupid"] is True
    assert sc["nac"] == "293" and sc["channelNo"] == "1" and len(sc["voiceChNo"]) == 1
    assert cfg["protocols"]["p25"]["enable"] and not cfg["protocols"]["dmr"]["enable"]
    assert cfg["network"]["restEnable"] and cfg["network"]["restPort"] == 9990
    fne = YAML(typ="safe").load(r["files"]["fne-config.yml"])
    assert fne["system"]["restPort"] == 9991 and fne["peers"] == []
    assert "1,444493750,6.25,5.00000,12.5," in r["files"]["iden_table.dat"]


def test_validation_and_safety():
    assert c.post("/api/wizard/preview", json={**W, "nac": "XYZ"}).status_code == 422
    assert c.post("/api/wizard/preview", json={**W, "mode": "dmr"}).status_code == 400
    assert c.post("/api/wizard/apply", json={**W, "ack_terms": False}).status_code == 400
    assert c.post("/api/service/ssh/stop").status_code == 400


def test_apply_and_dashboard():
    r = c.post("/api/wizard/apply", json=W).json()
    assert r["ok"] and all(p.startswith(str(settings.dvm_etc)) for p in r["written"])
    assert c.post("/api/service/dvmhost/start").json()["dry_run"]
    s = c.get("/api/snapshot").json()
    assert s["demo"] and len(s["nodes"]) == 3
