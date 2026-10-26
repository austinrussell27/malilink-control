"""Runtime settings, all overridable with environment variables (MALILINK_*)."""
from __future__ import annotations

import os
import shutil
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

    # Optional single shared password for the web UI (HTTP Basic, user "admin").
    ui_password: str = os.environ.get("MALILINK_PASSWORD", "")

    node_name: str = os.environ.get("MALILINK_NODE_NAME", os.uname().nodename)

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        dvmhost_present = (self.dvm_bin / "dvmhost").exists() or shutil.which("dvmhost") is not None
        has_systemctl = shutil.which("systemctl") is not None
        # Auto dry-run when there is nothing real to drive (e.g. a laptop or this dev box).
        if not self.dry_run and (not dvmhost_present or not has_systemctl) and not _bool("MALILINK_FORCE_REAL"):
            self.auto_dry_run = True
            self.dry_run = True
        else:
            self.auto_dry_run = False
        if self.dry_run:
            self.dvm_etc = self.data_dir / "dryrun" / "etc"
            self.dvm_log_dir = self.data_dir / "dryrun" / "log"
            self.dvm_etc.mkdir(parents=True, exist_ok=True)
            self.dvm_log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def dvmhost_installed(self) -> bool:
        return (self.dvm_bin / "dvmhost").exists() or shutil.which("dvmhost") is not None


settings = Settings()
