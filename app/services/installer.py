"""Background "Install DVM" job: apt deps -> clone -> cmake/make -> install -> user/dirs -> units.

Steps verified against the dvmhost repo (commit in dvm_templates/SOURCE.txt):
  * README "Build Instructions": deps `libasio-dev libncurses-dev libssl-dev` (+ optional
    `libdw-dev` for stack traces), `mkdir build && cd build && cmake .. && make`, `make strip`.
    A native build on the Pi needs NO cross-compile flags (-DCROSS_COMPILE_AARCH64 is only for
    cross-compiling from x86).
  * CMakeLists.txt: CMAKE_INSTALL_PREFIX=/usr/local; install() puts dvmhost/dvmfne/dvmcmd...
    in /usr/local/bin and the example configs in /usr/local/etc -> `make install`.
    (The README documents `make tarball`/`make old_install` (/opt/dvm); we use `make install`
    because linux/dvmhost.service expects /usr/local/bin + /usr/local/etc.)
  * CMakeLists.txt `old_install-service` target: `useradd --user-group -M --system dvmhost
    --shell /bin/false`, `usermod --groups dialout --append dvmhost`, copy linux/dvmhost.service
    to /lib/systemd/system/.
  * CMakeLists.txt: configure step FetchContent-clones websocketpp from GitHub (needs internet;
    or pass -DDISABLE_WEBSOCKETS=1).
UNVERIFIED / our additions: `git build-essential cmake` packages (implied, not listed in README),
the dvmfne.service unit (the repo ships none), /var/log/dvm, `make -j` job count.
Re-running is safe: apt is idempotent, the clone is fetched/reset, the build is incremental,
useradd/usermod tolerate existing users, and units are overwritten. Services are NOT started.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

from ..settings import ROOT_DIR, settings

LOG = settings.data_dir / "install.log"

STEPS = [
    ("deps", "Install build dependencies (apt)"),
    ("clone", "Download dvmhost source"),
    ("configure", "Configure build (cmake)"),
    ("build", "Compile (this takes a while on a Pi)"),
    ("install", "Install binaries to /usr/local"),
    ("user", "Create dvmhost user, log & config dirs"),
    ("units", "Install systemd units"),
]


def build_script(with_fne: bool = True) -> str:
    src = shlex.quote(str(settings.src_dir))
    repo = shlex.quote(settings.repo_url)
    logd = shlex.quote(str(settings.dvm_log_dir))
    fne_unit = shlex.quote(str(ROOT_DIR / "systemd" / "dvmfne.service"))
    jobs = "$(( $(nproc) > 3 ? 3 : $(nproc) ))"  # cap parallelism: 3 jobs is safe on a 4 GB Pi 5
    s = f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "##STEP deps"
apt-get update
apt-get install -y git build-essential cmake libasio-dev libncurses-dev libssl-dev libdw-dev elfutils
echo "##STEP clone"
if [ -d {src}/.git ]; then
  git -C {src} fetch --depth 1 origin
  git -C {src} reset --hard FETCH_HEAD
else
  mkdir -p "$(dirname {src})"
  git clone --depth 1 {repo} {src}
fi
git -C {src} log -1 --format='source commit %H (%cd)'
echo "##STEP configure"
mkdir -p {src}/build
cd {src}/build
cmake ..
echo "##STEP build"
make -j{jobs}
make strip
echo "##STEP install"
make install
echo "##STEP user"
useradd --user-group -M --system dvmhost --shell /bin/false || true
usermod --groups dialout --append dvmhost || true
mkdir -p {logd} /usr/local/etc
chown -R dvmhost:dvmhost {logd}
echo "##STEP units"
cp {src}/linux/dvmhost.service /lib/systemd/system/dvmhost.service
"""
    if with_fne:
        s += f"cp {fne_unit} /lib/systemd/system/dvmfne.service\n"
    s += "systemctl daemon-reload\necho '##DONE'\n"
    return s


DRY_OUTPUT = {
    "deps": ["Hit:1 http://deb.debian.org/debian trixie InRelease", "Reading package lists... Done",
             "libasio-dev is already the newest version.", "0 upgraded, 0 newly installed."],
    "clone": ["Cloning into '/opt/src/dvmhost'...", "source commit 435ff45b (demo)"],
    "configure": ["-- The C compiler identification is GNU 14.2.0", "-- Configuring done",
                  "-- Build files have been written to: /opt/src/dvmhost/build"],
    "build": [f"[{p:3d}%] Building CXX object src/host/CMakeFiles/dvmhost.dir/..." for p in range(5, 100, 9)]
             + ["[100%] Built target dvmhost", "[100%] Built target dvmfne"],
    "install": ["-- Installing: /usr/local/bin/dvmhost", "-- Installing: /usr/local/bin/dvmfne",
                "-- Installing: /usr/local/etc/config.example.yml"],
    "user": ["(dvmhost user created / already exists)"],
    "units": ["installed dvmhost.service, dvmfne.service", "systemctl daemon-reload"],
}


class InstallJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = "idle"   # idle | running | ok | failed
        self.step = None
        self.done_steps: list[str] = []
        self.lines: list[str] = []
        self.started = None
        self.finished = None
        self.dry_run = settings.dry_run
        self.proc: subprocess.Popen | None = None

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {"state": self.state, "step": self.step, "done_steps": list(self.done_steps),
                    "steps": [{"id": i, "label": l} for i, l in STEPS], "dry_run": self.dry_run,
                    "lines": self.lines[since:], "next": len(self.lines),
                    "started": self.started, "finished": self.finished,
                    "installed": settings.dvmhost_installed}

    def _emit(self, line: str) -> None:
        line = line.rstrip("\n")
        with self.lock:
            if line.startswith("##STEP "):
                if self.step:
                    self.done_steps.append(self.step)
                self.step = line.split(" ", 1)[1]
                self.lines.append(f"==> {dict(STEPS).get(self.step, self.step)}")
            elif line == "##DONE":
                if self.step:
                    self.done_steps.append(self.step)
                self.step = None
            else:
                self.lines.append(line)
                if len(self.lines) > 5000:
                    del self.lines[:1000]
        with LOG.open("a") as fh:
            fh.write(line + "\n")

    def start(self, with_fne: bool = True) -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.state, self.step, self.done_steps, self.lines = "running", None, [], []
            self.started, self.finished = time.time(), None
        LOG.write_text("")
        threading.Thread(target=self._run, args=(with_fne,), daemon=True).start()
        return True

    def _run(self, with_fne: bool) -> None:
        ok = False
        try:
            if self.dry_run:
                self._emit("[dry-run] no commands are executed; showing what a real install does")
                for sid, _ in STEPS:
                    self._emit(f"##STEP {sid}")
                    for l in DRY_OUTPUT[sid]:
                        time.sleep(0.25)
                        self._emit(l)
                self._emit("##DONE")
                ok = True
            else:
                if os.geteuid() != 0:
                    self._emit("ERROR: installing needs root. Run Malinois Link Control via its systemd unit (runs as root).")
                else:
                    script = build_script(with_fne)
                    self.proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE,
                                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
                    assert self.proc.stdout
                    for line in self.proc.stdout:
                        self._emit(line)
                    ok = self.proc.wait() == 0
        except Exception as e:  # pragma: no cover
            self._emit(f"ERROR: {e}")
        with self.lock:
            self.state = "ok" if ok else "failed"
            self.finished = time.time()


job = InstallJob()
