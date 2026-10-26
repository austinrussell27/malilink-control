"""Minimal read-only client for the dvmhost / dvmfne REST API.

Verified in src/host/restapi/RESTAPI.cpp + RESTDefines.h (and src/fne/restapi/*):
  PUT  /auth   body {"auth": "<hex sha256 of restPassword>"} -> {"status":200,"token":"<n>"}
  then header  X-DVM-Auth-Token: <token>  on every request (401 -> re-auth)
  GET  /version, /status, /voice-ch, /p25/report-affiliations        (dvmhost)
  GET  /dmr/report-affiliations, /nxdn/report-affiliations           (dvmhost; {"affiliations":[{srcId,grpId}]})
  GET  /version, /status, /peer/query, /report-affiliations, /stats  (dvmfne)
Plain HTTP unless restSsl=true.

SAFETY: this client deliberately exposes GET + /auth only. Endpoints that can key the
transmitter or change RF behaviour (/mdm/mode, /grant-tg, /p25/rid, /p25/raw-tsbk,
/p25/cc-broadcast, /dmr/beacon, /dmr/cc-enable, /dmr/cc-broadcast, /dmr/rid, /nxdn/cc,
/nxdn/cc-enable, ...) are intentionally NOT implemented.
"""
from __future__ import annotations

import hashlib
import threading

import httpx

READ_ONLY_PATHS = {"/version", "/status", "/voice-ch", "/p25/report-affiliations", "/dmr/report-affiliations", "/nxdn/report-affiliations",
                   "/peer/query", "/report-affiliations", "/stats", "/peer/count"}


class DvmRest:
    def __init__(self, base_url: str, password: str, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.password = password
        self.token: str | None = None
        self.client = httpx.Client(timeout=timeout, verify=False)
        self.lock = threading.Lock()

    def _auth(self) -> None:
        h = hashlib.sha256(self.password.encode()).hexdigest()
        r = self.client.put(self.base + "/auth", json={"auth": h})
        r.raise_for_status()
        self.token = str(r.json()["token"])

    def get(self, path: str) -> dict:
        if path not in READ_ONLY_PATHS:
            raise PermissionError(f"{path} is not on the read-only allow-list")
        with self.lock:
            for attempt in (0, 1):
                if not self.token:
                    self._auth()
                r = self.client.get(self.base + path, headers={"X-DVM-Auth-Token": self.token or ""})
                if r.status_code == 401 and attempt == 0:
                    self.token = None
                    continue
                r.raise_for_status()
                return r.json()
        raise RuntimeError("unreachable")
