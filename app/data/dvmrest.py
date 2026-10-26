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
import time

import httpx


class RestUnavailable(Exception):
    """Quiet, classified REST failure. kind: refused | timeout | auth | http | backoff."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind, self.detail = kind, detail


def _classify(e: Exception) -> RestUnavailable:
    if isinstance(e, RestUnavailable):
        return e
    if isinstance(e, httpx.ConnectError):
        return RestUnavailable("refused", str(e))
    if isinstance(e, httpx.TimeoutException):
        return RestUnavailable("timeout", str(e))
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return RestUnavailable("auth" if code in (400, 401, 403) else "http", f"HTTP {code}")
    return RestUnavailable("http", str(e))

READ_ONLY_PATHS = {"/version", "/status", "/voice-ch", "/p25/report-affiliations", "/dmr/report-affiliations", "/nxdn/report-affiliations",
                   "/peer/query", "/report-affiliations", "/stats", "/peer/count"}


class DvmRest:
    def __init__(self, base_url: str, password: str, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.password = password
        self.token: str | None = None
        self.client = httpx.Client(timeout=timeout, verify=False)
        self.lock = threading.Lock()
        # quiet retry with exponential backoff (2 s, 4 s, ... max 30 s) so a stopped/starting dvmhost
        # isn't hammered every dashboard refresh and the UI shows one calm state instead of errors
        self.failures = 0
        self.next_try = 0.0
        self.last_error: RestUnavailable | None = None

    def _backoff(self, err: RestUnavailable) -> RestUnavailable:
        self.failures += 1
        self.last_error = err
        self.next_try = time.monotonic() + min(2 ** self.failures, 30)
        return err

    def reset_backoff(self) -> None:
        self.failures, self.next_try, self.last_error = 0, 0.0, None

    def _auth(self) -> None:
        h = hashlib.sha256(self.password.encode()).hexdigest()
        r = self.client.put(self.base + "/auth", json={"auth": h})
        if r.status_code in (400, 401, 403):  # RESTAPI.cpp: wrong password -> 400 "invalid password"
            raise RestUnavailable("auth", f"HTTP {r.status_code}: password rejected")
        r.raise_for_status()
        self.token = str(r.json()["token"])

    def get(self, path: str) -> dict:
        if path not in READ_ONLY_PATHS:
            raise PermissionError(f"{path} is not on the read-only allow-list")
        with self.lock:
            if self.last_error is not None and time.monotonic() < self.next_try:
                raise self.last_error  # still backing off: answer from the last known state, no request
            try:
                for attempt in (0, 1):
                    if not self.token:
                        self._auth()
                    r = self.client.get(self.base + path, headers={"X-DVM-Auth-Token": self.token or ""})
                    if r.status_code == 401 and attempt == 0:
                        self.token = None
                        continue
                    r.raise_for_status()
                    self.reset_backoff()
                    return r.json()
            except Exception as e:  # noqa: BLE001 - every failure becomes one classified, quiet state
                self.token = None
                raise self._backoff(_classify(e))
        raise RuntimeError("unreachable")
