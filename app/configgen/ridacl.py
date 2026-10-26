"""rid_acl.dat read/write - mirrors dvmhost src/common/lookups/RadioIdLookup.cpp.

VERIFIED format (configs/rid_acl.example.dat + RadioIdLookup::load):
  "RID,Enabled(1/0),Optional Alias,Optional IP Address,<newline>"
  - lines starting with '#' are comments; fields split on ',' (no quoting -> aliases can't contain commas)
  - fewer than 2 fields -> entry rejected; enabled is `atoi(field) == 1`
  - file "should always end with an empty line" (example header)
VERIFIED semantics (src/common/p25/acl/AccessControl.cpp validateSrcId, same for DMR/NXDN):
  - system.radio_id.acl: false -> every RID allowed EXCEPT listed entries with Enabled=0 (blocklist)
  - system.radio_id.acl: true  -> ONLY listed entries with Enabled=1 allowed ("lockdown"/allowlist)
  - system.radio_id.time: minutes between automatic re-reads of the file (LookupTable.h timer;
    0 = never); `acl` itself is only read at startup (Host.cpp) -> changing it needs a restart.
  - there is no `radio_id.enable` key in config.example.yml (keys: file, time, acl).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

MAX_RID = 16777215  # 24-bit unit IDs (P25 WUIDs; FFFFFC-FFFFFF are reserved/special)


class Radio(BaseModel):
    rid: int = Field(ge=1, le=MAX_RID)
    alias: str = ""
    enabled: bool = True
    ip: str = ""

    @field_validator("alias", "ip")
    @classmethod
    def _clean(cls, v: str) -> str:
        # commas/newlines would break the comma-split parser in RadioIdLookup::load
        return " ".join(str(v or "").replace(",", " ").split())[:64]


def render(radios: list[Radio], header: str) -> str:
    lines = [l for l in header.splitlines() if l.startswith("#")]
    for r in sorted(radios, key=lambda r: r.rid):
        line = f"{r.rid},{1 if r.enabled else 0},"
        if r.alias or r.ip:
            line += f"{r.alias},"
        if r.ip:
            line += f"{r.ip},"
        lines.append(line)
    return "\n".join(lines) + "\n\n"


def parse(text: str) -> list[Radio]:
    out: dict[int, Radio] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split(",")
        if len(p) < 2:
            continue
        try:
            rid = int(p[0].strip() or 0)
        except ValueError:
            continue
        if not 1 <= rid <= MAX_RID:
            continue
        try:
            en = int(p[1].strip() or 0) == 1
        except ValueError:
            en = False
        out[rid] = Radio(rid=rid, enabled=en, alias=p[2] if len(p) >= 3 else "", ip=p[3] if len(p) >= 4 else "")
    return list(out.values())


CSV_FIELDS = ["rid", "alias", "enabled", "ip"]


def to_csv(radios: list[Radio]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    for r in sorted(radios, key=lambda r: r.rid):
        w.writerow([r.rid, r.alias, 1 if r.enabled else 0, r.ip])
    return buf.getvalue()


_TRUE = {"1", "true", "yes", "y", "enabled", "on", "allow", "allowed"}


def from_csv(text: str) -> tuple[list[Radio], list[str]]:
    """Accepts our CSV export (header rid,alias,enabled,ip) or header-less 'rid,alias[,enabled]' rows,
    or a pasted rid_acl.dat. Returns (radios, errors)."""
    text = text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(text)))
    errors: list[str] = []
    out: dict[int, Radio] = {}
    if rows and rows[0] and rows[0][0].strip().lower() in ("rid", "radio id", "id", "radio_id"):
        hdr = [h.strip().lower() for h in rows[0]]
        rows = rows[1:]
        idx = {k: hdr.index(k) if k in hdr else None for k in CSV_FIELDS}
        if idx["rid"] is None:
            idx["rid"] = 0
        if idx["alias"] is None:
            idx["alias"] = next((hdr.index(a) for a in ("name", "callsign", "alias") if a in hdr), None)
    else:
        idx = None
    for n, row in enumerate(rows, start=2 if idx else 1):
        if not row or not "".join(row).strip() or row[0].strip().startswith("#"):
            continue
        try:
            if idx:
                g = lambda k: (row[idx[k]].strip() if idx[k] is not None and idx[k] < len(row) else "")
                rid, alias, en, ip = g("rid"), g("alias"), g("enabled"), g("ip")
            else:  # dat-style "rid,enabled,alias,ip" if 2nd col is 0/1, else "rid,alias,enabled"
                c = [x.strip() for x in row] + ["", "", ""]
                if c[1] in ("0", "1"):
                    rid, en, alias, ip = c[0], c[1], c[2], c[3]
                else:
                    rid, alias, en, ip = c[0], c[1], c[2], c[3]
            r = Radio(rid=int(rid), alias=alias, enabled=(en.lower() in _TRUE) if en else True, ip=ip)
            out[r.rid] = r
        except Exception as e:
            errors.append(f"line {n}: {','.join(row)[:60]} - {str(e).splitlines()[0][:80]}")
    return list(out.values()), errors


def set_acl_flag(config_path: Path, lockdown: bool) -> bool:
    """Flip system.radio_id.acl in an existing config.yml (round-trip, comments kept)."""
    from .generate import _dump, _set, _yaml
    if not config_path.exists():
        return False
    doc = _yaml().load(config_path.read_text())
    _set(doc, "system.radio_id.acl", bool(lockdown))
    config_path.write_text(_dump(doc))
    return True
