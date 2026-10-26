"""Installer robustness: clean configure, wipe+retry on cmake failure, broken checkout, interrupted job."""
import json
import os
import stat
import subprocess
from pathlib import Path

from app.services import installer
from app.services.installer import clone_block, configure_block


def _fake_cmake(tmp: Path, fail_times: int) -> Path:
    """Fake cmake: records whether build/_deps junk existed, fails the first N calls (writing junk)."""
    f = tmp / "cmake"
    f.write_text(f"""#!/bin/bash
n=$(cat {tmp}/count 2>/dev/null || echo 0); n=$((n+1)); echo $n > {tmp}/count
B="${{@: -1}}"
if [ -e "$B/_deps/websocketpp-src/CMakeLists.txt" ]; then echo "SAW_STALE_DEPS" >> {tmp}/calls; else echo "clean" >> {tmp}/calls; fi
mkdir -p "$B/_deps/websocketpp-src"
if [ $n -le {fail_times} ]; then printf '\\x86\\x00garbage' > "$B/_deps/websocketpp-src/CMakeLists.txt"; echo "CMake Error: Parse error"; exit 1; fi
echo "-- Configuring done"; exit 0
""")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return f


def _run(script: str, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=tmp)


def _src_with_corrupt_deps(tmp: Path) -> Path:
    src = tmp / "src"
    d = src / "build/_deps/websocketpp-src"; d.mkdir(parents=True)
    (d / "CMakeLists.txt").write_bytes(b"\x86\x00binary garbage")  # left by a killed FetchContent clone
    return src


def test_stale_build_dir_is_wiped_before_configure(tmp_path):
    src = _src_with_corrupt_deps(tmp_path)
    r = _run(configure_block(str(src), cmake=str(_fake_cmake(tmp_path, 0))), tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "calls").read_text().split() == ["clean"]  # cmake never saw the corrupt _deps


def test_failed_configure_wipes_and_retries_once(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    r = _run(configure_block(str(src), cmake=str(_fake_cmake(tmp_path, 1))), tmp_path)
    assert r.returncode == 0 and "retrying once" in r.stdout
    assert (tmp_path / "calls").read_text().split() == ["clean", "clean"]  # retry also started from empty dir


def test_configure_gives_up_after_one_retry(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    r = _run(configure_block(str(src), cmake=str(_fake_cmake(tmp_path, 5))), tmp_path)
    assert r.returncode != 0 and (tmp_path / "count").read_text().strip() == "2"


def test_git_clean_option(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "junk.o").write_text("x")
    r = _run(configure_block(str(src), clean_src=True, cmake=str(_fake_cmake(tmp_path, 0))), tmp_path)
    assert r.returncode == 0 and not (src / "junk.o").exists()
    assert "git clean -fdx" not in configure_block(str(src))  # only when asked


def test_broken_checkout_is_recloned(tmp_path):
    origin = tmp_path / "origin"; origin.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    (origin / "CMakeLists.txt").write_text("project(x)\n")
    subprocess.run(["git", "-C", str(origin), "add", "."], check=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-qm", "x"], check=True, env=env)
    src = tmp_path / "src"; src.mkdir(); (src / "half-cloned").write_text("x")  # no .git: interrupted clone
    r = _run(clone_block(str(src), f"file://{origin}"), tmp_path)
    assert r.returncode == 0 and "removing it and cloning again" in r.stdout
    assert (src / "CMakeLists.txt").exists() and not (src / "half-cloned").exists()


def test_build_script_uses_clean_configure():
    from app.settings import settings
    s = installer.build_script(True)
    b = f"rm -rf {settings.src_dir}/build"
    assert b in s and s.index(b) < s.index("cmake -S") and "git clean" not in s
    assert "git -C %s clean -fdx" % settings.src_dir in installer.build_script(True, clean_src=True)


def test_interrupted_job_is_reported_failed_after_restart():
    installer.STATE_FILE.write_text(json.dumps({"state": "running", "step": "configure", "started": 1}))
    job = installer.InstallJob()
    snap = job.snapshot()
    assert snap["state"] == "failed" and "interrupted" in snap["lines"][0] and "Configure" in snap["lines"][0]
    assert json.loads(installer.STATE_FILE.read_text())["state"] == "failed"
    assert job.start is not None and snap["build_dir"].endswith("/build")
    installer.STATE_FILE.unlink()
