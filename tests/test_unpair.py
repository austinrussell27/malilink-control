"""Unpair / reset when the other Pi is offline (fallback) - in-process, dry-run."""
import json

from fastapi.testclient import TestClient

from app import state
from app.main import app
from app.settings import settings


def test_voice_reset_works_with_control_offline():
    settings.dvm_etc.mkdir(parents=True, exist_ok=True)
    (settings.dvm_etc / "config.yml").write_text("old: config\n")
    st = state.load()
    st.update(role="voice", setup_complete=True, wizard={"x": 1})
    st["pairing"] = {"codes": {}, "voice_channels": [], "joined": {
        "cluster_key": "k", "summary": {"control_url": "http://127.0.0.1:9", "channelNo": 3, "system_name": "ML105"}}}
    state.save(st)
    c = TestClient(app)
    page = c.get("/dashboard").text
    assert "Unpair this Pi" in page and "ML105" in page
    r = c.post("/api/pairing/reset", json={"notify": True}).json()
    assert r["ok"] and r["notified"][0]["ok"] is False and "unreachable" in r["notified"][0]["detail"]
    assert state.load()["role"] is None and state.load()["wizard"] == {}
    assert json.loads((settings.data_dir / "state.json.bak").read_text())["role"] == "voice"
    assert (settings.dvm_etc / "config.yml.bak").read_text() == "old: config\n"
    assert not (settings.dvm_etc / "config.yml").exists()
    assert "Unpair this Pi" not in c.get("/dashboard").text


def test_control_reset_releases_offline_vcs_gracefully():
    st = state.load()
    st.update(role="control", setup_complete=True, wizard={"topology": "trunked"},
              nodes=[{"name": "vc1", "url": "http://127.0.0.1:9", "role": "voice", "key": "k"}])
    st["pairing"] = {"codes": {}, "voice_channels": [{"name": "vc1", "channelNo": 3, "rpc_address": "1.2.3.4"}]}
    state.save(st)
    c = TestClient(app)
    assert "Remove / unpair" in c.get("/dashboard").text and "Unpair &amp; reset" in c.get("/trunking").text
    r = c.post("/api/pairing/reset", json={"notify": True}).json()
    assert r["ok"] and r["previous_role"] == "control" and r["notified"][0]["node"] == "vc1"
    assert not r["notified"][0]["ok"] and state.load()["pairing"]["voice_channels"] == []
