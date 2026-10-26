"""Turns wizard answers into dvmhost/dvmfne config files, using the repo example files as
round-trip templates (ruamel.yaml keeps all of the upstream comments and structure).

Every key we set below exists in configs/config.example.yml or configs/fne-config.example.yml
at the vendored commit (see dvm_templates/SOURCE.txt). _set() raises if a key is missing so
we can never silently invent a config key.
"""
from __future__ import annotations

import io
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from ..settings import settings
from . import iden, talkgroups as tgmod


class Talkgroup(BaseModel):
    tgid: int = Field(ge=1, le=65535)
    name: str = ""
    alias: str = ""
    parrot: bool = False


def _hex(v: str, lo: int, hi: int, what: str) -> str:
    s = str(v).strip().upper().removeprefix("0X").removeprefix("$")
    if not re.fullmatch(r"[0-9A-F]+", s or "x"):
        raise ValueError(f"{what} must be hexadecimal (0-9, A-F)")
    n = int(s, 16)
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be between {lo:X} and {hi:X} (hex)")
    return s


class WizardInput(BaseModel):
    mode: Literal["p25", "dmr", "nxdn"] = "p25"
    topology: Literal["conventional", "trunked"] = "conventional"
    system_name: str = Field(min_length=1, max_length=32)
    callsign: str = Field(min_length=3, max_length=12)
    location: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    power_w: int = Field(default=10, ge=0, le=1000)
    tx_mhz: float = Field(gt=1, lt=1300, description="Repeater/hotspot TRANSMIT (radios receive here)")
    rx_mhz: float = Field(gt=1, lt=1300, description="Repeater/hotspot RECEIVE (radios transmit here)")
    nac: str = "293"
    wacn: str = "BB800"
    sys_id: str = "001"
    rfss_id: str = "1"
    site_id: str = "1"
    modem_type: Literal["uart", "null"] = "uart"
    modem_port: str = "/dev/ttyUSB0"
    modem_speed: int = 115200
    network_mode: Literal["local_fne", "remote_fne", "standalone"] = "local_fne"
    fne_address: str = "127.0.0.1"
    fne_port: int = 62031
    fne_password: str = ""
    peer_id: int = Field(default=100000, ge=1, le=999999999)
    talkgroups: list[Talkgroup] = Field(default_factory=lambda: [
        Talkgroup(tgid=1, name="Talkgroup 1", alias="Tactical"),
        Talkgroup(tgid=9990, name="Parrot", alias="Parrot/Testing", parrot=True),
    ])
    ack_license: bool = False
    ack_terms: bool = False

    @field_validator("nac")
    @classmethod
    def _nac(cls, v):  # 12-bit NAC
        return _hex(v, 0x0, 0xFFF, "NAC")

    @field_validator("wacn")
    @classmethod
    def _wacn(cls, v):
        return _hex(v, 0x1, 0xFFFFF, "WACN (Network ID)")

    @field_validator("sys_id")
    @classmethod
    def _sys(cls, v):
        return _hex(v, 0x1, 0xFFF, "System ID").zfill(3)

    @field_validator("rfss_id")
    @classmethod
    def _rfss(cls, v):
        return _hex(v, 0x1, 0xFF, "RFSS ID")

    @field_validator("site_id")
    @classmethod
    def _site(cls, v):
        return _hex(v, 0x1, 0xFF, "Site ID")

    @field_validator("callsign")
    @classmethod
    def _cs(cls, v):
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z0-9/]+", v):
            raise ValueError("Callsign may only contain letters, digits and /")
        return v


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=4, sequence=4, offset=2)
    y.width = 4096
    return y


def _set(doc: Any, path: str, value: Any) -> None:
    """Set an EXISTING key (dotted path). Refuses to create keys that the template lacks."""
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur:
            raise KeyError(f"template is missing '{path}' - refusing to invent config keys")
        cur = cur[p]
    if parts[-1] not in cur:
        raise KeyError(f"template is missing '{path}' - refusing to invent config keys")
    cur[parts[-1]] = value


def _dump(doc: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


@dataclass
class Generated:
    files: dict[str, str]
    plan: iden.ChannelPlan
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)
    radio: dict[str, Any] = field(default_factory=dict)


def generate(w: WizardInput, existing_secrets: Optional[dict[str, str]] = None) -> Generated:
    if w.mode != "p25":
        raise ValueError(f"{w.mode.upper()} is coming next - only P25 is supported in this version")
    if w.topology != "conventional":
        raise ValueError("Trunked systems are coming next (see the 'Trunking & voice channels' page)")

    tdir = settings.template_dir
    etc = settings.dvm_etc
    logdir = str(settings.dvm_log_dir)
    sec = dict(existing_secrets or {})
    sec.setdefault("rest_password", secrets.token_urlsafe(18))
    sec.setdefault("rpc_password", secrets.token_urlsafe(18))
    sec.setdefault("fne_password", w.fne_password or secrets.token_urlsafe(12))
    sec.setdefault("fne_rest_password", secrets.token_urlsafe(18))
    if w.network_mode == "remote_fne":
        sec["fne_password"] = w.fne_password

    plan = iden.plan_conventional(w.tx_mhz, w.rx_mhz, tdir / "iden_table.example.dat")
    warnings: list[str] = []
    if not plan.reused_template_row:
        warnings.append(f"Created a custom identity-table row (id {plan.entry.channel_id}) so "
                        f"{w.tx_mhz:.5f} MHz is channel 1.")
    if plan.simplex_same_freq:
        warnings.append("Simplex (same TX/RX frequency): duplex is turned off and simplexSameFrequency is on; "
                        "the iden offset is a non-zero placeholder because dvmhost rejects a 0 MHz offset.")

    # ---------------- dvmhost config.yml ----------------
    h = _yaml().load((tdir / "config.example.yml").read_text())
    _set(h, "iAgreeNotToBeStupid", bool(w.ack_terms))
    _set(h, "log.filePath", logdir)
    _set(h, "log.activityFilePath", logdir)
    _set(h, "log.fileRoot", "DVM")
    # network
    net_on = w.network_mode != "standalone"
    _set(h, "network.enable", net_on)
    _set(h, "network.id", w.peer_id)
    _set(h, "network.address", "127.0.0.1" if w.network_mode == "local_fne" else w.fne_address)
    _set(h, "network.port", w.fne_port)
    _set(h, "network.password", sec["fne_password"])
    _set(h, "network.rpcPassword", sec["rpc_password"])
    # REST API (read-only use by this app: /auth, /status, /p25/report-affiliations, /voice-ch)
    _set(h, "network.restEnable", True)
    _set(h, "network.restAddress", "127.0.0.1")
    _set(h, "network.restPort", 9990)
    _set(h, "network.restPassword", sec["rest_password"])
    # protocols: P25 only, conventional (no control channel)
    _set(h, "protocols.dmr.enable", False)
    _set(h, "protocols.p25.enable", True)
    _set(h, "protocols.nxdn.enable", False)
    _set(h, "protocols.p25.control.enable", False)
    _set(h, "protocols.p25.control.dedicated", False)
    # system
    _set(h, "system.identity", w.system_name)
    _set(h, "system.duplex", plan.duplex)
    _set(h, "system.simplexSameFrequency", plan.simplex_same_freq)
    _set(h, "system.fixedMode", True)
    _set(h, "system.info.latitude", w.latitude)
    _set(h, "system.info.longitude", w.longitude)
    _set(h, "system.info.power", w.power_w)
    _set(h, "system.info.location", w.location or w.system_name)
    # site config (channelNo/nac/netId/sysId/siteId are parsed as HEX strings by Host.Config.cpp)
    _set(h, "system.config.channelId", plan.entry.channel_id)
    _set(h, "system.config.channelNo", plan.channel_no_hex)
    _set(h, "system.config.nac", w.nac)
    _set(h, "system.config.netId", w.wacn)
    _set(h, "system.config.sysId", w.sys_id)
    _set(h, "system.config.rfssId", w.rfss_id)
    _set(h, "system.config.siteId", w.site_id)
    # conventional: dvmhost refuses to start with an empty voiceChNo list (Host.Config.cpp
    # "No voice channel list defined!"), so like the example we list our own channel.
    vc = h["system"]["config"]["voiceChNo"][0]
    vc["channelId"] = plan.entry.channel_id
    vc["channelNo"] = plan.channel_no_hex
    vc["rpcAddress"] = "127.0.0.1"
    vc["rpcPort"] = 9890
    vc["rpcPassword"] = sec["rpc_password"]
    del h["system"]["config"]["voiceChNo"][1:]
    _set(h, "system.config.controlCh.rpcPort", 0)  # 0 = CC notifications disabled (conventional)
    # CW ID with the licensee callsign (station identification)
    _set(h, "system.cwId.enable", True)
    _set(h, "system.cwId.callsign", w.callsign)
    # modem
    _set(h, "system.modem.protocol.type", w.modem_type)
    _set(h, "system.modem.protocol.uart.port", w.modem_port)
    _set(h, "system.modem.protocol.uart.speed", w.modem_speed)
    # lookup files (absolute, like the CMake install step rewrites them)
    _set(h, "system.iden_table.file", str(etc / "iden_table.dat"))
    _set(h, "system.radio_id.file", str(etc / "rid_acl.dat"))
    _set(h, "system.talkgroup_id.file", str(etc / "talkgroup_rules.yml"))

    files: dict[str, str] = {"config.yml": _dump(h)}
    files["iden_table.dat"] = iden.render_table(tdir / "iden_table.example.dat", [plan.entry])
    tg_text = tgmod.build(tdir / "talkgroup_rules.example.yml", [t.model_dump() for t in w.talkgroups])
    files["talkgroup_rules.yml"] = tg_text
    errors = tgmod.validate(tg_text, tdir / "talkgroup_rules.yaml_schema.json")
    if errors and errors != ["schema not found; skipped"]:
        errors = [f"talkgroup_rules.yml: {e}" for e in errors]
    else:
        errors = []
    files["rid_acl.dat"] = (tdir / "rid_acl.example.dat").read_text()

    # ---------------- dvmfne fne-config.yml (local FNE only) ----------------
    if w.network_mode == "local_fne":
        f = _yaml().load((tdir / "fne-config.example.yml").read_text())
        _set(f, "iAgreeNotToBeStupid", bool(w.ack_terms))
        _set(f, "log.filePath", logdir)
        _set(f, "log.activityFilePath", logdir)
        _set(f, "log.fileRoot", "DVMFNE")
        _set(f, "master.address", "0.0.0.0")  # so future voice-channel Pis can peer over Tailscale
        _set(f, "master.port", w.fne_port)
        _set(f, "master.password", sec["fne_password"])
        _set(f, "master.talkgroup_rules.file", str(etc / "talkgroup_rules.yml"))
        _set(f, "master.adj_site_map.file", str(etc / "adj_site_map.yml"))
        f["peers"] = CommentedSeq()  # no upstream FNE; HostFNE.cpp tolerates an empty list
        _set(f, "system.identity", f"{w.system_name}-FNE"[:32])
        _set(f, "system.restEnable", True)
        _set(f, "system.restAddress", "127.0.0.1")
        # dvmhost and dvmfne both default restPort to 9990; move the FNE to 9991 to avoid a clash.
        _set(f, "system.restPort", 9991)
        _set(f, "system.restPassword", sec["fne_rest_password"])
        _set(f, "system.radio_id.file", str(etc / "rid_acl.dat"))
        _set(f, "system.peer_acl.file", str(etc / "peer_list.dat"))
        files["fne-config.yml"] = _dump(f)
        files["adj_site_map.yml"] = (tdir / "adj_site_map.example.yml").read_text()
        # Unverified: whether dvmfne needs peer_list.dat to exist when peer_acl.enable=false.
        files["peer_list.dat"] = "# Malinois Link Control: peer ACL disabled (peer_acl.enable=false)\n"

    if not w.ack_terms:
        warnings.append("dvmhost will refuse to start until you accept the DVMProject terms (iAgreeNotToBeStupid).")

    radio = {
        "mode": "P25 Phase 1 conventional (FDMA, 12.5 kHz)",
        "radio_rx_mhz": plan.tx_hz / 1e6,
        "radio_tx_mhz": plan.rx_hz / 1e6,
        "offset_mhz": (plan.rx_hz - plan.tx_hz) / 1e6,
        "nac_hex": w.nac,
        "nac_dec": int(w.nac, 16),
        "talkgroups": [t.model_dump() for t in w.talkgroups],
        "system_name": w.system_name,
        "callsign": w.callsign,
    }
    return Generated(files=files, plan=plan, warnings=warnings, errors=errors, secrets=sec, radio=radio)


def write_files(gen: Generated) -> list[str]:
    """Write generated files to dvm_etc (backing up existing ones). Returns written paths."""
    etc = settings.dvm_etc
    etc.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in gen.files.items():
        p = etc / name
        if p.exists():
            p.with_suffix(p.suffix + ".bak").write_text(p.read_text())
        p.write_text(text)
        written.append(str(p))
    try:  # dvmhost runs as the 'dvmhost' user (linux/dvmhost.service)
        import pwd, os
        u = pwd.getpwnam("dvmhost")
        for pth in written:
            os.chown(pth, u.pw_uid, u.pw_gid)
    except Exception:
        pass
    return written
