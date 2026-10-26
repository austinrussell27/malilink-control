"""Tiny JSON-file persistence for app state (wizard answers, nodes, pairing)."""
from __future__ import annotations

import json
import threading
from typing import Any

from .settings import settings

_LOCK = threading.Lock()
_PATH = settings.data_dir / "state.json"

DEFAULT: dict[str, Any] = {
    "setup_complete": False,
    "role": None,             # "conventional" | "control" | "voice"
    "wizard": {},             # last submitted wizard answers
    "nodes": [],              # other Pis: {"name","url","role","chNo"}
    "pairing": {"codes": {}, "voice_channels": []},
    "rest_password": None,    # generated dvmhost REST password (used by our live data layer)
    "fne_rest_password": None,
}


def load() -> dict[str, Any]:
    with _LOCK:
        if _PATH.exists():
            try:
                data = json.loads(_PATH.read_text())
                return {**DEFAULT, **data}
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT))


def save(data: dict[str, Any]) -> None:
    with _LOCK:
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_PATH)


def update(**kw: Any) -> dict[str, Any]:
    data = load()
    data.update(kw)
    save(data)
    return data
