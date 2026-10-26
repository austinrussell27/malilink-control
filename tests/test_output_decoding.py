"""Subprocess output with non-UTF-8 bytes (e.g. git via cmake FetchContent) must never crash."""
import io
import subprocess
import sys

from app.services import systemd
from app.services.installer import iter_output, subprocess_env

BAD = b"-- Cloning WebSocket++\n" + b"x" * 141 + b"\x86\xff tail\n" + b"Receiving objects:  50%\rReceiving objects: 100%\r\n" + b"no newline \xc3"


def test_iter_output_replaces_invalid_bytes():
    lines = list(iter_output(io.BytesIO(BAD)))
    assert lines[0] == "-- Cloning WebSocket++"
    assert lines[1].endswith("\ufffd\ufffd tail") and lines[1].startswith("x" * 141)
    assert lines[2] == "Receiving objects: 100%"  # \r progress collapsed to the last update
    assert lines[3] == "no newline \ufffd"


def test_iter_output_from_real_process():
    p = subprocess.Popen([sys.executable, "-c", f"import sys; sys.stdout.buffer.write({BAD!r})"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=subprocess_env())
    lines = list(iter_output(p.stdout))
    assert p.wait() == 0 and len(lines) == 4 and "\ufffd" in lines[1]
    assert subprocess_env()["LC_ALL"] == "C.UTF-8"


def test_systemd_run_tolerates_invalid_bytes():
    rc, out = systemd._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\x86\\n')"])
    assert rc == 0 and out == "ok \ufffd"
