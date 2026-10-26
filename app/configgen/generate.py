"""Turns wizard answers into dvmhost/dvmfne config files, using the repo example files as
round-trip templates (ruamel.yaml keeps all of the upstream comments and structure).

Every key we set below exists in configs/config.example.yml or configs/fne-config.example.yml
at the vendored commit (see dvm_templates/SOURCE.txt). _set() raises if a key is missing so
we can never silently invent a config key.
"""
from __future__ import annotations

import copy
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
    # dvmfne master.peerId (fne-config.example.yml key, default 9000100). None = keep template value.
    fne_peer_id: Optional[int] = Field(default=None, ge=1, le=999999999)
    # Trunked: network peer ID handed to the 1st/2nd/... paired voice channel (None = peer_id + channelNo).
    vc_peer_start: Optional[int] = Field(default=None, ge=1, le=999999999)
    # Optional: replace the Radios (RID) list + lockdown on apply (used by the "previous system" preset).
    radios: Optional[list[dict]] = None
    rid_lockdown: Optional[bool] = None
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


# ---------------------------------------------------------------------------------------------
# Role-specific key combinations.
#
# VERIFIED in dvmhost source:
#   * protocols.p25.control.enable / .dedicated   (src/host/p25/Control.cpp: dedicated only honoured
#     when control.enable is true; Host.Config.cpp uses p25.control.dedicated for the CC loop)
#   * system.config.authoritative / supervisor    (Host.Config.cpp: supervisor only read when authoritative)
#   * system.config.controlCh.{rpcAddress,rpcPort,rpcPassword,notifyEnable}  (Host.Config.cpp + p25/Control.cpp:
#     a VC notifies the CC only if rpcAddress non-empty, rpcPort != 0 and notifyEnable true)
#   * system.config.voiceChNo[] {channelId, channelNo(hex), rpcAddress, rpcPort, rpcPassword} must be
#     non-empty or dvmhost exits ("No voice channel list defined!")
#   * RPC (src/common/network/NetRPC.cpp) uses SHA-256(rpcPassword) as the socket pre-shared key, so the
#     CC and every VC must share ONE rpcPassword (network.rpcPassword == controlCh/voiceChNo rpcPassword).
#   * network.rpcAddress is the RPC listen address ("or 0.0.0.0 for all" - config.example.yml); the
#     default 127.0.0.1 would make the CC/VCs unreachable from other Pis.
# FROM THE REPO'S OWN GENERATOR (tools/dvmcfggen/templates.py), not independently verified:
#   * CC:  authoritative=true, supervisor=true, p25.control.enable=true, dedicated=true, duplex=true
#   * VC:  authoritative=false, supervisor=false, p25.control.enable=false, dedicated=false,
#          controlCh.notifyEnable=true
# UNVERIFIED (our choice): a VC lists only its own channel in voiceChNo (needed to start; whether the
#   VC uses it is unclear); channel spacing between channels = bandwidth/spacing (2 x 6.25 kHz).
# ---------------------------------------------------------------------------------------------
RPC_PORT = 9890  # RPC_DEFAULT_PORT in src/common/Defines.h


def _common_host(h, *, ack_terms: bool, identity: str, callsign: str, peer_id: int, fne_address: str,
                 fne_port: int, fne_password: str, net_on: bool, sec: dict, plan_channel_id: int,
                 channel_no_hex: str, duplex: bool, simplex_same: bool, site: dict, modem: dict,
                 info: dict, rpc_listen: str) -> None:
    etc = settings.dvm_etc
    logdir = str(settings.dvm_log_dir)
    _set(h, "iAgreeNotToBeStupid", bool(ack_terms))
    _set(h, "log.filePath", logdir)
    _set(h, "log.activityFilePath", logdir)
    _set(h, "log.fileRoot", "DVM")
    _set(h, "network.enable", net_on)
    _set(h, "network.id", peer_id)
    _set(h, "network.address", fne_address)
    _set(h, "network.port", fne_port)
    _set(h, "network.password", fne_password)
    _set(h, "network.rpcAddress", rpc_listen)
    _set(h, "network.rpcPort", RPC_PORT)
    _set(h, "network.rpcPassword", sec["rpc_password"])
    # REST API (read-only use by this app: /auth, /status, /p25/report-affiliations, /voice-ch)
    _set(h, "network.restEnable", True)
    _set(h, "network.restAddress", "127.0.0.1")
    _set(h, "network.restPort", 9990)
    _set(h, "network.restPassword", sec["rest_password"])
    _set(h, "protocols.dmr.enable", False)
    _set(h, "protocols.p25.enable", True)
    _set(h, "protocols.nxdn.enable", False)
    _set(h, "system.identity", identity[:32])
    _set(h, "system.duplex", duplex)
    _set(h, "system.simplexSameFrequency", simplex_same)
    _set(h, "system.fixedMode", True)
    _set(h, "system.info.latitude", info.get("latitude", 0.0))
    _set(h, "system.info.longitude", info.get("longitude", 0.0))
    _set(h, "system.info.power", info.get("power_w", 10))
    _set(h, "system.info.location", info.get("location") or identity)
    # channelNo/nac/netId/sysId/rfssId/siteId are parsed as HEX strings by Host.Config.cpp
    _set(h, "system.config.channelId", plan_channel_id)
    _set(h, "system.config.channelNo", channel_no_hex)
    _set(h, "system.config.nac", site["nac"])
    _set(h, "system.config.netId", site["wacn"])
    _set(h, "system.config.sysId", site["sys_id"])
    _set(h, "system.config.rfssId", site["rfss_id"])
    _set(h, "system.config.siteId", site["site_id"])
    _set(h, "system.cwId.enable", True)
    _set(h, "system.cwId.callsign", callsign)
    _set(h, "system.modem.protocol.type", modem["type"])
    _set(h, "system.modem.protocol.uart.port", modem["port"])
    _set(h, "system.modem.protocol.uart.speed", modem["speed"])
    _set(h, "system.iden_table.file", str(etc / "iden_table.dat"))
    _set(h, "system.radio_id.file", str(etc / "rid_acl.dat"))
    _set(h, "system.talkgroup_id.file", str(etc / "talkgroup_rules.yml"))


def _voice_list(h, entries: list[dict]) -> None:
    """Replace system.config.voiceChNo with `entries`, reusing the template's first item as prototype
    (keeps its comments)."""
    seq = h["system"]["config"]["voiceChNo"]
    proto = copy.deepcopy(seq[0])
    for k in ("rxChannelId", "rxChannelNo"):
        proto.pop(k, None)
    out = []
    for e in entries:
        item = copy.deepcopy(proto)
        item["channelId"] = e["channelId"]
        item["channelNo"] = e["channelNo"]
        item["rpcAddress"] = e["rpcAddress"]
        item["rpcPort"] = e.get("rpcPort", RPC_PORT)
        item["rpcPassword"] = e["rpcPassword"]
        out.append(item)
    if out:
        del seq[:]
        seq.extend(out)
    else:
        # ruamel can't emit an empty sequence that still carries the template's item comments
        # (it produces invalid YAML), so drop those comments for the empty case.
        h["system"]["config"].ca.items.pop("voiceChNo", None)
        h["system"]["config"]["voiceChNo"] = CommentedSeq()


def channel_step(entry: iden.IdenEntry) -> int:
    """Channel-number step so adjacent channels don't overlap (12.5 kHz bw / 6.25 kHz spacing = 2)."""
    return max(1, round(entry.bandwidth_khz / entry.spacing_khz))


def next_free_channel(cc_ch: int, used: set[int], step: int) -> int:
    n = cc_ch + step
    while n in used:
        n += step
    if n > 4095:
        raise ValueError("No free channel numbers left on this identity row")
    return n


def channel_for_freq(entry: iden.IdenEntry, tx_mhz: float) -> int:
    tx = iden.mhz_to_hz(tx_mhz)
    space = int(entry.spacing_khz * 1000)
    if (tx - entry.base_freq) % space:
        raise ValueError(f"{tx_mhz} MHz is not on the system's {entry.spacing_khz} kHz channel grid")
    ch = (tx - entry.base_freq) // space
    if not 1 <= ch <= 4095:
        raise ValueError(f"{tx_mhz} MHz is outside this system's band plan")
    return ch


def _rid_files(rid: Optional[dict]) -> tuple[str, bool]:
    from . import ridacl
    rid = rid or {}
    radios = [ridacl.Radio(**r) for r in rid.get("radios") or []]
    header = (settings.template_dir / "rid_acl.example.dat").read_text()
    return ridacl.render(radios, header), bool(rid.get("lockdown"))


def generate(w: WizardInput, existing_secrets: Optional[dict[str, str]] = None,
             voice_channels: Optional[list[dict]] = None, rid: Optional[dict] = None) -> Generated:
    if w.mode != "p25":
        raise ValueError(f"{w.mode.upper()} is coming next - only P25 is supported in this version")
    trunked = w.topology == "trunked"
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
    warnings: list[str] = []
    network_mode = w.network_mode
    if trunked and network_mode != "local_fne":
        network_mode = "local_fne"
        warnings.append("Trunked control channel always runs the network hub (dvmfne) so voice channels can join.")

    plan = iden.plan_conventional(w.tx_mhz, w.rx_mhz, tdir / "iden_table.example.dat")
    if trunked and plan.simplex_same_freq:
        raise ValueError("A trunked control channel needs a duplex frequency pair (different TX and RX).")
    if not plan.reused_template_row:
        warnings.append(f"Created a custom identity-table row (id {plan.entry.channel_id}) so "
                        f"{w.tx_mhz:.5f} MHz is channel {plan.channel_no}.")
    if plan.simplex_same_freq:
        warnings.append("Simplex (same TX/RX frequency): duplex is turned off and simplexSameFrequency is on; "
                        "the iden offset is a non-zero placeholder because dvmhost rejects a 0 MHz offset.")

    site = {"nac": w.nac, "wacn": w.wacn, "sys_id": w.sys_id, "rfss_id": w.rfss_id, "site_id": w.site_id}
    h = _yaml().load((tdir / "config.example.yml").read_text())
    _common_host(h, ack_terms=w.ack_terms, identity=(f"{w.system_name}-CC" if trunked else w.system_name),
                 callsign=w.callsign, peer_id=w.peer_id,
                 fne_address="127.0.0.1" if network_mode == "local_fne" else w.fne_address,
                 fne_port=w.fne_port, fne_password=sec["fne_password"], net_on=network_mode != "standalone",
                 sec=sec, plan_channel_id=plan.entry.channel_id, channel_no_hex=plan.channel_no_hex,
                 duplex=plan.duplex, simplex_same=plan.simplex_same_freq, site=site,
                 modem={"type": w.modem_type, "port": w.modem_port, "speed": w.modem_speed},
                 info={"latitude": w.latitude, "longitude": w.longitude, "power_w": w.power_w, "location": w.location},
                 rpc_listen="0.0.0.0" if trunked else "127.0.0.1")
    vcs = sorted(voice_channels or [], key=lambda v: v["channelNo"])
    if trunked:
        _set(h, "protocols.p25.control.enable", True)
        _set(h, "protocols.p25.control.dedicated", True)
        _set(h, "system.config.authoritative", True)
        _set(h, "system.config.supervisor", True)
        _set(h, "system.config.controlCh.rpcPort", 0)  # example: "left default for dedicated control channels"
        _voice_list(h, [{"channelId": plan.entry.channel_id, "channelNo": format(v["channelNo"], "X"),
                         "rpcAddress": v["rpc_address"], "rpcPort": v.get("rpc_port", RPC_PORT),
                         "rpcPassword": sec["rpc_password"]} for v in vcs])
        if not vcs:
            warnings.append("No voice channels paired yet - dvmhost will not start on the control channel until "
                            "at least one voice-channel Pi joins (Trunking page).")
    else:
        _set(h, "protocols.p25.control.enable", False)
        _set(h, "protocols.p25.control.dedicated", False)
        _set(h, "system.config.controlCh.rpcPort", 0)  # 0 = CC notifications disabled (conventional)
        # conventional: like the example, voiceChNo lists our own channel (must not be empty)
        _voice_list(h, [{"channelId": plan.entry.channel_id, "channelNo": plan.channel_no_hex,
                         "rpcAddress": "127.0.0.1", "rpcPort": RPC_PORT, "rpcPassword": sec["rpc_password"]}])

    files: dict[str, str] = {"config.yml": _dump(h)}
    files["iden_table.dat"] = iden.render_table(tdir / "iden_table.example.dat", [plan.entry])
    tg_text = tgmod.build(tdir / "talkgroup_rules.example.yml", [t.model_dump() for t in w.talkgroups])
    files["talkgroup_rules.yml"] = tg_text
    errors = [f"talkgroup_rules.yml: {e}" for e in tgmod.validate(tg_text, tdir / "talkgroup_rules.yaml_schema.json")
              if e != "schema not found; skipped"]
    # Radios page list (rid_acl.dat) + lockdown -> system.radio_id.acl (verified keys: file/time/acl)
    files["rid_acl.dat"], lockdown = _rid_files(rid)
    _set(h, "system.radio_id.acl", lockdown)
    files["config.yml"] = _dump(h)

    if network_mode == "local_fne":
        f = _yaml().load((tdir / "fne-config.example.yml").read_text())
        _set(f, "iAgreeNotToBeStupid", bool(w.ack_terms))
        _set(f, "log.filePath", logdir)
        _set(f, "log.activityFilePath", logdir)
        _set(f, "log.fileRoot", "DVMFNE")
        _set(f, "master.address", "0.0.0.0")  # so voice-channel Pis can peer over Tailscale
        _set(f, "master.port", w.fne_port)
        _set(f, "master.password", sec["fne_password"])
        if w.fne_peer_id:
            _set(f, "master.peerId", w.fne_peer_id)
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

    e = plan.entry
    radio = {
        "trunked": trunked,
        "mode": "P25 Phase 1 trunked (FDMA, 12.5 kHz)" if trunked else "P25 Phase 1 conventional (FDMA, 12.5 kHz)",
        "radio_rx_mhz": plan.tx_hz / 1e6,
        "radio_tx_mhz": plan.rx_hz / 1e6,
        "offset_mhz": (plan.rx_hz - plan.tx_hz) / 1e6,
        "nac_hex": w.nac, "nac_dec": int(w.nac, 16),
        "wacn": w.wacn, "sys_id": w.sys_id, "rfss_id": w.rfss_id, "site_id": w.site_id,
        "band_plan": {"iden": e.channel_id, "base_mhz": e.base_freq / 1e6, "spacing_khz": e.spacing_khz,
                      "offset_mhz": e.offset_mhz, "bandwidth_khz": e.bandwidth_khz},
        "voice_channels": [{"name": v["name"], "channelNo": v["channelNo"],
                            "tx_mhz": e.tx_for(v["channelNo"]) / 1e6, "rx_mhz": e.rx_for(v["channelNo"]) / 1e6}
                           for v in vcs],
        "talkgroups": [t.model_dump() for t in w.talkgroups],
        "system_name": w.system_name,
        "callsign": w.callsign,
    }
    return Generated(files=files, plan=plan, warnings=warnings, errors=errors, secrets=sec, radio=radio)


class VoiceLocal(BaseModel):
    """What the joining voice-channel Pi supplies itself."""
    name: str = Field(min_length=1, max_length=24)
    modem_type: Literal["uart", "null"] = "uart"
    modem_port: str = "/dev/ttyUSB0"
    modem_speed: int = 115200
    ack_license: bool = False
    ack_terms: bool = False


def generate_voice_channel(bundle: dict, local: VoiceLocal, rest_password: Optional[str] = None) -> Generated:
    """Voice-channel config from the control Pi's pairing bundle (see app/pairing.py)."""
    tdir = settings.template_dir
    sec = {"rpc_password": bundle["rpc"]["password"], "rest_password": rest_password or secrets.token_urlsafe(18)}
    entry = iden.IdenEntry(**bundle["iden"])
    ch = int(bundle["channelNo"])
    tx, rx = entry.tx_for(ch), entry.rx_for(ch)
    plan = iden.ChannelPlan(entry, ch, True, False, tx, rx, False)
    h = _yaml().load((tdir / "config.example.yml").read_text())
    _common_host(h, ack_terms=local.ack_terms, identity=f"{bundle['system_name']}-VC{ch}", callsign=bundle["callsign"],
                 peer_id=bundle["network"]["peerId"], fne_address=bundle["network"]["address"],
                 fne_port=bundle["network"]["port"], fne_password=bundle["network"]["password"], net_on=True,
                 sec=sec, plan_channel_id=entry.channel_id, channel_no_hex=format(ch, "X"),
                 duplex=True, simplex_same=False, site=bundle["site"],
                 modem={"type": local.modem_type, "port": local.modem_port, "speed": local.modem_speed},
                 info={"location": bundle.get("location")}, rpc_listen="0.0.0.0")
    _set(h, "protocols.p25.control.enable", False)
    _set(h, "protocols.p25.control.dedicated", False)
    _set(h, "system.config.authoritative", False)
    _set(h, "system.config.supervisor", False)
    _set(h, "system.config.controlCh.rpcAddress", bundle["rpc"]["address"])
    _set(h, "system.config.controlCh.rpcPort", bundle["rpc"]["port"])
    _set(h, "system.config.controlCh.rpcPassword", bundle["rpc"]["password"])
    _set(h, "system.config.controlCh.notifyEnable", True)
    _voice_list(h, [{"channelId": entry.channel_id, "channelNo": format(ch, "X"), "rpcAddress": "127.0.0.1",
                     "rpcPort": RPC_PORT, "rpcPassword": bundle["rpc"]["password"]}])
    # RIDs are managed on the control Pi and pushed here (bundle at join, /api/radios/sync afterwards).
    rid_text, lockdown = _rid_files(bundle.get("rid"))
    _set(h, "system.radio_id.acl", lockdown)
    files = {"config.yml": _dump(h), "iden_table.dat": bundle["iden_table"],
             "talkgroup_rules.yml": tgmod.build(tdir / "talkgroup_rules.example.yml", bundle["talkgroups"]),
             "rid_acl.dat": rid_text}
    warnings = []
    if not local.ack_terms:
        warnings.append("dvmhost will refuse to start until you accept the DVMProject terms (iAgreeNotToBeStupid).")
    radio = {"trunked": True, "voice_channel": True, "channelNo": ch, "tx_mhz": tx / 1e6, "rx_mhz": rx / 1e6}
    return Generated(files=files, plan=plan, warnings=warnings, errors=[], secrets=sec, radio=radio)


def write_files(gen: Generated) -> list[str]:
    """Write generated files to dvm_etc (backing up existing ones). Returns written paths.
    Real mode: /usr/local/etc (and the log dir) are created if dvmhost isn't installed yet."""
    etc = settings.dvm_etc
    etc.mkdir(parents=True, exist_ok=True)
    settings.dvm_log_dir.mkdir(parents=True, exist_ok=True)
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
        for pth in written + [str(settings.dvm_log_dir)]:
            os.chown(pth, u.pw_uid, u.pw_gid)
    except Exception:
        pass
    _record_write(written)
    return written


def _record_write(written: list[str]) -> None:
    """Remember where/how configs were last written (dry-run configs must be re-written in real mode)."""
    # separate file so callers holding a loaded state dict can't overwrite it on save
    import json, time
    (settings.data_dir / "config_written.json").write_text(json.dumps(
        {"dry_run": settings.dry_run, "etc": str(settings.dvm_etc), "at": time.time(), "files": written}))


def last_write() -> Optional[dict]:
    import json
    try:
        return json.loads((settings.data_dir / "config_written.json").read_text())
    except Exception:
        return None
