"""Background "Install DVM" job: apt deps -> clone -> cmake/make -> install -> user/dirs -> units.

Steps verified against the dvmhost repo (commit in dvm_templates/SOURCE.txt):
  * README "Build Instructions": deps `libasio-dev libncurses-dev libssl-dev` (+ optional
    `libdw-dev` for stack traces), `mkdir build && cd build && cmake .. && make`, `make strip`
    (we run the equivalent `cmake -S <src> -B <src>/build`).
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

import json
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

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


def clone_block(src: str, repo: str) -> str:
    """Fetch/update the source; a half-finished or broken checkout is removed and cloned fresh."""
    return f"""if [ -d {src}/.git ] && git -C {src} fetch --depth 1 origin && git -C {src} reset --hard FETCH_HEAD; then
  echo "source updated"
else
  if [ -e {src} ]; then echo "existing checkout at {src} is incomplete or broken - removing it and cloning again"; rm -rf {src}; fi
  mkdir -p "$(dirname {src})"
  git clone --depth 1 {repo} {src}
fi
"""


def configure_block(src: str, clean_src: bool = False, cmake: str = "cmake") -> str:
    """Always configure from an empty build dir (a FetchContent clone of websocketpp interrupted by an
    app restart leaves a corrupt build/_deps/websocketpp-src). If configure still fails, wipe the build
    dir once more and retry once (e.g. a flaky FetchContent download)."""
    b = f"{src}/build"
    s = ""
    if clean_src:
        s += f'echo "git clean -fdx in {src} (removes every untracked/ignored file, incl. build/)"\n'
        s += f"git -C {src} clean -fdx\n"
    s += f"""echo "removing old build directory {b} (clean configure, includes _deps)"
rm -rf {b}
if ! {cmake} -S {src} -B {b}; then
  echo "cmake configure failed - wiping {b} and retrying once"
  rm -rf {b}
  {cmake} -S {src} -B {b}
fi
"""
    return s


def build_script(with_fne: bool = True, clean_src: bool = False) -> str:
    if len(settings.src_dir.resolve().parts) < 3:  # we rm -rf <src>/build and maybe <src>: never near /
        raise ValueError(f"refusing to build in {settings.src_dir}: choose a deeper MALILINK_SRC_DIR")
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
{clone_block(src, repo)}git -C {src} log -1 --format='source commit %H (%cd)'
echo "##STEP configure"
{configure_block(src, clean_src)}cd {src}/build
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


FNE_STEPS = [
    ("fne_check", "Check what is already installed"),
    ("fne_build", "Build & install the dvmfne binary (only if missing)"),
    ("fne_user", "dvmhost user & log dir (if missing)"),
    ("fne_units", "Install & enable the dvmfne service"),
]
# where systemd units can live (repo's README copies to /lib/systemd/system); overridable for tests
UNIT_DIRS = [Path("/lib/systemd/system"), Path("/etc/systemd/system")]
DRY_DELAY = 0.25  # seconds between simulated output lines


def fne_only_script() -> str:
    """Install ONLY the dvmfne service. Never configures/rebuilds dvmhost, never runs `make install`
    (that would reinstall dvmhost + example configs), never touches /usr/local/etc.
    dvmfne target + build/dvmfne location verified: src/CMakeLists.txt add_executable(dvmfne),
    CMakeLists.txt include(src/CMakeLists.txt) + CMAKE_RUNTIME_OUTPUT_DIRECTORY "." -> <build>/dvmfne,
    install(TARGETS dvmfne DESTINATION ${CMAKE_INSTALL_PREFIX}/bin)."""
    if len(settings.src_dir.resolve().parts) < 3:
        raise ValueError(f"refusing to use {settings.src_dir}: choose a deeper MALILINK_SRC_DIR")
    b = shlex.quote(str(settings.src_dir / "build"))
    binp = shlex.quote(str(settings.dvm_bin / "dvmfne"))
    logd = shlex.quote(str(settings.dvm_log_dir))
    unit = shlex.quote(str(ROOT_DIR / "systemd" / "dvmfne.service"))
    udir = shlex.quote(str(UNIT_DIRS[0]))
    return f"""set -euo pipefail
echo "##STEP fne_check"
if [ -x {binp} ]; then echo "dvmfne binary already installed: {binp} - not rebuilding"; NEED_BUILD=0
elif [ -f {b}/Makefile ]; then echo "dvmfne binary missing; build tree found at {b}"; NEED_BUILD=1
else echo "ERROR: no dvmfne binary and no build tree at {b}. Run 'Install / update DVM' first."; exit 3; fi
echo "##STEP fne_build"
if [ "$NEED_BUILD" = 1 ]; then
  make -C {b} -j"$(( $(nproc) > 3 ? 3 : $(nproc) ))" dvmfne
  strip -s {b}/dvmfne || true
  install -m 0755 {b}/dvmfne {binp}
  echo "installed {binp}"
else
  echo "skipped (already installed)"
fi
echo "##STEP fne_user"
if id dvmhost >/dev/null 2>&1; then echo "user dvmhost exists"; else
  useradd --user-group -M --system dvmhost --shell /bin/false; usermod --groups dialout --append dvmhost; echo "created user dvmhost"; fi
mkdir -p {logd}
chown dvmhost:dvmhost {logd}
echo "##STEP fne_units"
mkdir -p {udir}
cp {unit} {udir}/dvmfne.service
systemctl daemon-reload
systemctl enable dvmfne
echo "dvmfne.service installed and enabled (not started - start it from the dashboard once fne-config.yml exists)"
echo '##DONE'
"""


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
    "fne_check": ["dvmfne binary already installed: /usr/local/bin/dvmfne - not rebuilding"],
    "fne_build": ["skipped (already installed)"],
    "fne_user": ["user dvmhost exists"],
    "fne_units": ["Created symlink /etc/systemd/system/multi-user.target.wants/dvmfne.service",
                  "dvmfne.service installed and enabled (not started)"],
}
DRY_FNE_BUILD = ["dvmfne binary missing; build tree found", "[100%] Built target dvmfne",
                 "installed /usr/local/bin/dvmfne"]


def subprocess_env() -> dict:
    """Environment for install subprocesses: UTF-8 C locale, non-interactive apt, no git prompts."""
    env = dict(os.environ)
    env.update(LC_ALL="C.UTF-8", LANG="C.UTF-8", DEBIAN_FRONTEND="noninteractive", GIT_TERMINAL_PROMPT="0")
    return env


def iter_output(stream) -> Iterator[str]:
    """Yield decoded lines from a BINARY stream. Never raises on bad bytes: tools such as git (via
    cmake FetchContent) can print non-UTF-8 bytes, which used to crash the job with a UnicodeDecodeError.
    Carriage-return progress updates ("Receiving objects: 45%\r...") keep only the last segment."""
    for raw in iter(stream.readline, b""):
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if "\r" in text:
            text = [seg for seg in text.split("\r") if seg.strip()][-1:] or [""]
            text = text[0]
        yield text


STATE_FILE = settings.data_dir / "install_state.json"


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
        self.clean_src = False
        self.kind = "full"  # full | fne
        self.sim = {"dvmhost_bin": False, "dvmfne_bin": False, "dvmfne_unit": False, "build_tree": False}
        self._recover()

    def steps(self) -> list:
        return FNE_STEPS if self.kind == "fne" else STEPS

    def pieces(self) -> dict:
        """What's installed (status chips). Dry-run reports the simulated state."""
        if self.dry_run:
            return dict(self.sim)
        return {"dvmhost_bin": settings.dvmhost_installed, "dvmfne_bin": settings.fne_installed,
                "dvmfne_unit": any((d / "dvmfne.service").exists() for d in UNIT_DIRS),
                "build_tree": (settings.src_dir / "build" / "Makefile").exists()}

    def fne_only_blocker(self) -> str | None:
        p = self.pieces()
        if not p["dvmfne_bin"] and not p["build_tree"]:
            return (f"dvmfne isn't built yet and there is no build tree at {settings.src_dir / 'build'}. "
                    "Run 'Install / update DVM' first (it builds dvmhost and dvmfne together).")
        return None

    def _save_state(self) -> None:
        try:
            STATE_FILE.write_text(json.dumps({"state": self.state, "step": self.step, "started": self.started,
                                              "finished": self.finished, "kind": self.kind,
                                              "pid": os.getpid()}))
        except Exception:
            pass

    def _recover(self) -> None:
        """App restarted (or was killed) while an install ran: report it as failed, never as running.
        systemd kills the build with the app (same cgroup); the next run wipes the build dir anyway."""
        try:
            prev = json.loads(STATE_FILE.read_text())
        except Exception:
            return
        if prev.get("state") == "running":
            self.state, self.started, self.finished = "failed", prev.get("started"), time.time()
            step = dict(STEPS + FNE_STEPS).get(prev.get("step") or "", prev.get("step") or "?")
            self.kind = prev.get("kind", "full")
            self.lines = [f"Previous install was interrupted during: {step} (the app was restarted or stopped).",
                          "Run Install / update DVM again - the build directory is wiped before configuring.",
                          "If the source checkout itself looks broken, tick 'clean source checkout'."]
            self._save_state()
        elif prev.get("state") in ("ok", "failed"):
            self.state, self.started, self.finished = prev["state"], prev.get("started"), prev.get("finished")

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {"state": self.state, "step": self.step, "done_steps": list(self.done_steps),
                    "kind": self.kind, "pieces": self.pieces(),
                    "steps": [{"id": i, "label": l} for i, l in self.steps()], "dry_run": self.dry_run,
                    "lines": self.lines[since:], "next": len(self.lines),
                    "started": self.started, "finished": self.finished,
                    "installed": settings.dvmhost_installed,
                    "src_dir": str(settings.src_dir), "build_dir": str(settings.src_dir / "build")}

    def _emit(self, line: str) -> None:
        line = line.rstrip("\n")
        with self.lock:
            if line.startswith("##STEP "):
                if self.step:
                    self.done_steps.append(self.step)
                self.step = line.split(" ", 1)[1]
                self._save_state()
                self.lines.append(f"==> {dict(STEPS + FNE_STEPS).get(self.step, self.step)}")
            elif line == "##DONE":
                if self.step:
                    self.done_steps.append(self.step)
                self.step = None
            else:
                self.lines.append(line)
                if len(self.lines) > 5000:
                    del self.lines[:1000]
        with LOG.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")

    def start(self, with_fne: bool = True, clean_src: bool = False, kind: str = "full") -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.kind = kind
            self.state, self.step, self.done_steps, self.lines = "running", None, [], []
            self.started, self.finished = time.time(), None
            self.clean_src = clean_src
            self._save_state()
        LOG.write_text("")
        threading.Thread(target=self._run, args=(with_fne,), daemon=True).start()
        return True

    def _run(self, with_fne: bool) -> None:
        ok = False
        try:
            if self.dry_run:
                ok = self._simulate(with_fne)
            else:
                if os.geteuid() != 0:
                    self._emit("ERROR: installing needs root. Run Malinois Link Control via its systemd unit (runs as root).")
                else:
                    script = fne_only_script() if self.kind == "fne" else build_script(with_fne, self.clean_src)
                    # binary pipe + our own tolerant decoding (see iter_output)
                    self.proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE,
                                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                                 env=subprocess_env())
                    assert self.proc.stdout
                    for line in iter_output(self.proc.stdout):
                        self._emit(line)
                    ok = self.proc.wait() == 0
        except Exception as e:  # pragma: no cover
            self._emit(f"ERROR: {e}")
        with self.lock:
            self.state = "ok" if ok else "failed"
            self.finished = time.time()
            self._save_state()


    def _simulate(self, with_fne: bool) -> bool:
        self._emit("[dry-run] no commands are executed; showing what a real install does")
        if self.kind == "fne":
            if self.fne_only_blocker():
                self._emit("##STEP fne_check")
                self._emit("ERROR: " + self.fne_only_blocker())
                return False
        out = dict(DRY_OUTPUT)
        if self.kind == "fne" and not self.sim["dvmfne_bin"]:
            out["fne_check"], out["fne_build"] = ["dvmfne binary missing; build tree found"], DRY_FNE_BUILD[1:]
        for sid, _ in self.steps():
            self._emit(f"##STEP {sid}")
            for l in out[sid]:
                time.sleep(DRY_DELAY)
                self._emit(l)
        self._emit("##DONE")
        if self.kind == "fne":
            self.sim.update(dvmfne_bin=True, dvmfne_unit=True)
        else:
            self.sim.update(dvmhost_bin=True, dvmfne_bin=True, build_tree=True, dvmfne_unit=with_fne)
        return True


job = InstallJob()
