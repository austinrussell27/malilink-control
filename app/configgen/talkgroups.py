"""talkgroup_rules.yml generation from configs/talkgroup_rules.example.yml, validated with
configs/schema/talkgroup_rules.yaml_schema.json (draft-04)."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import jsonschema
from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def build(template: Path, talkgroups: list[dict]) -> str:
    """talkgroups: [{"tgid": int, "name": str, "alias": str, "parrot": bool}]"""
    y = _yaml()
    doc = y.load(template.read_text())
    proto = None
    for item in doc["groupVoice"]:
        if "parrot" not in item["config"]:
            proto = item
            break
    proto = proto or doc["groupVoice"][0]
    out = []
    for tg in talkgroups:
        item = copy.deepcopy(proto)
        item["name"] = str(tg.get("name") or f"TG {tg['tgid']}")
        item["alias"] = str(tg.get("alias") or "")
        cfg = item["config"]
        cfg["active"] = True
        cfg["affiliated"] = False
        if tg.get("parrot"):
            # `parrot` key is real (example "Parrot" entry + schema)
            cfg.insert(2, "parrot", True)
        for k in ("inclusion", "exclusion", "rewrite", "always", "preferred", "rid_permitted"):
            if k in cfg:
                cfg[k] = []
        item["source"]["tgid"] = int(tg["tgid"])
        item["source"]["slot"] = int(tg.get("slot", 1))
        out.append(item)
    doc["groupVoice"].clear()
    doc["groupVoice"].extend(out)
    buf = io.StringIO()
    y.dump(doc, buf)
    return buf.getvalue()


def validate(text: str, schema_path: Path) -> list[str]:
    if not schema_path.exists():
        return ["schema not found; skipped"]
    data = YAML(typ="safe").load(text)
    schema = json.loads(schema_path.read_text())
    v = jsonschema.Draft4Validator(schema)
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(data)]
