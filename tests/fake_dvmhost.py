"""Fake dvmhost REST server for tests: mimics /auth + X-DVM-Auth-Token semantics from
src/host/restapi/RESTAPI.cpp (sha256 hex of password; token returned as string)."""
import hashlib, json
from http.server import BaseHTTPRequestHandler, HTTPServer

PW = "secret"; TOKEN = "12345678901234"

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/auth" and body.get("auth") == hashlib.sha256(PW.encode()).hexdigest():
            return self._send(200, {"status": 200, "token": TOKEN})
        self._send(400, {"status": 400, "message": "invalid password"})
    def do_GET(self):
        if self.headers.get("X-DVM-Auth-Token") != TOKEN:
            return self._send(401, {"status": 401, "message": "invalid authentication token"})
        if self.path == "/status":
            return self._send(200, {"status": 200, "state": 2, "tx": True, "channelId": 1, "channelNo": 1, "lastSrcId": 3101001,
                                    "lastDstId": 1, "peerId": 100000, "p25NAC": 0x293, "p25Enabled": True, "vcChannels": [],
                                    "modem": {"portType": "uart", "modemPort": "/dev/ttyUSB0", "rxFrequency": 449500000, "txFrequency": 444500000}})
        if self.path == "/p25/report-affiliations":
            return self._send(200, {"status": 200, "affiliations": [{"srcId": 3101001, "grpId": 1}]})
        self._send(404, {"status": 404})
    def log_message(self, *a): pass

if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9990), H).serve_forever()
