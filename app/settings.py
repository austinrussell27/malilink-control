"""Runtime settings, all overridable with environment variables (MALILINK_*)."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Where we keep our own state (wizard answers, pairing codes, install log).
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MALILINK_DATA_DIR", ROOT_DIR / "var")))
    # Example configs copied from the dvmhost repo (see dvm_templates/SOURCE.txt).
    template_dir: Path = field(default_factory=lambda: Path(os.environ.get("MALILINK_TEMPLATE_DIR", ROOT_DIR / "dvm_templates")))

    # Real install locations. These match the dvmhost CMake `install()` rules
    # (CMAKE_INSTALL_PREFIX=/usr/local -> /usr/local/bin, /usr/local/etc) and
    # linux/dvmhost.service (ExecStart=/usr/local/bin/dvmhost -c /usr/local/etc/config.yml).
    dvm_etc: Path = field(default_factory=lambda: Path(os.environ.get("MALILINK_DVM_ETC", "/usr/local/etc")))
    dvm_bin: Path = field(default_factory=lambda: Path(os.environ.get("MALILINK_DVM_BIN", "/usr/local/bin")))
    # Log directory we configure dvmhost/dvmfne to write to (our choice, created by the installer).
    dvm_log_dir: Path = field(default_factory=lambda: Path(os.environ.get("MALILINK_DVM_LOG_DIR", "/var/log/dvm")))
    # Where the installer clones/builds the dvmhost source.
    src_dir: Path = field(default_factory=lambda: Path(os.environ.get("MALILINK_SRC_DIR", "/opt/src/dvmhost")))
    repo_url: str = os.environ.get("MALILINK_DVM_REPO", "https://github.com/DVMProject/dvmhost.git")

    host_unit: str = os.environ.get("MALILINK_HOST_UNIT", "dvmhost")
    fne_unit: str = os.environ.get("MALILINK_FNE_UNIT", "dvmfne")

    # demo: synthetic dashboard data. dry_run: never touch the system (no apt, no systemctl,
    # config files are written under var/dryrun/ instead of /usr/local/etc).
    demo: bool = _bool("MALILINK_DEMO", False)
    dry_run: bool = _bool("MALILINK_DRY_RUN", False)
    # True when MALILINK_DRY_RUN is set at all (1 forces dry-run, 0 forces real mode).
    dry_run_explicit: bool = os.environ.get("MALILINK_DRY_RUN") is not None

    # Optional single shared password for the web UI (HTTP Basic, user "admin").
    ui_password: str = os.environ.get("MALILINK_PASSWORD", "")

    node_name: str = os.environ.get("MALILINK_NODE_NAME", os.uname().nodename)

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dryrun_etc = self.data_dir / "dryrun" / "etc"
        self.dryrun_log_dir = self.data_dir / "dryrun" / "log"
        self.real_etc, self.real_log_dir = self.dvm_etc, self.dvm_log_dir
        self.has_systemctl = shutil.which("systemctl") is not None
        self.is_linux = sys.platform.startswith("linux")
        # Auto dry-run ONLY when this machine can't drive services at all (no systemctl / not Linux,
        # e.g. a laptop or CI). A real Pi without dvmhost installed yet is REAL mode: the Install DVM
        # step runs for real and configs go to /usr/local/etc (created on first write).
        self.auto_dry_run = False
        self.demo_dry_run = False
        if self.demo and not self.dry_run_explicit:
            # demo = simulated system; never touch the real machine unless MALILINK_DRY_RUN=0 is given
            self.demo_dry_run = True
            self.dry_run = True
        elif not self.dry_run_explicit and not _bool("MALILINK_FORCE_REAL") and not (self.has_systemctl and self.is_linux):
            self.auto_dry_run = True
            self.dry_run = True
        if self.dry_run:
            self.dvm_etc = self.dryrun_etc
            self.dvm_log_dir = self.dryrun_log_dir
            self.dvm_etc.mkdir(parents=True, exist_ok=True)
            self.dvm_log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_root(self) -> bool:
        return hasattr(os, "geteuid") and os.geteuid() == 0

    @property
    def mode_reason(self) -> str:
        if not self.dry_run:
            return "real"
        if self.demo_dry_run:
            return "MALILINK_DEMO=1"
        if self.auto_dry_run:
            return "auto: systemctl not found" if self.is_linux else "auto: not a Linux host"
        return "MALILINK_DRY_RUN=1"

    @property
    def fne_installed(self) -> bool:
        return (self.dvm_bin / "dvmfne").exists() or shutil.which("dvmfne") is not None

    @property
    def dvmhost_installed(self) -> bool:
        return (self.dvm_bin / "dvmhost").exists() or shutil.which("dvmhost") is not None


settings = Settings()
