"""Real mode vs dry-run: status for the UI banner, and re-writing configs that were generated while
the app was in dry-run (they live under <data_dir>/dryrun/etc and dvmhost never sees them)."""
from __future__ import annotations

import shutil
from typing import Optional

from . import radios, state
from .configgen.generate import (VoiceLocal, WizardInput, generate, generate_voice_channel, last_write,
                                 write_files)
from .settings import settings

auto_rewrite_result: Optional[dict] = None  # set at start-up if we re-wrote automatically


def needs_rewrite(st: dict) -> bool:
    if settings.dry_run or not st.get("setup_complete"):
        return False
    lw = last_write()
    real_cfg = (settings.dvm_etc / "config.yml").exists()
    return (not real_cfg) or bool(lw and lw.get("dry_run"))


def status(st: Optional[dict] = None) -> dict:
    st = st or state.load()
    lw = last_write()
    return {
        "real": not settings.dry_run, "dry_run": settings.dry_run, "reason": settings.mode_reason,
        "demo": settings.demo, "etc": str(settings.dvm_etc), "log_dir": str(settings.dvm_log_dir),
        "has_systemctl": settings.has_systemctl, "root": settings.is_root,
        "dvmhost_installed": settings.dvmhost_installed, "dvmfne_installed": settings.fne_installed,
        "last_write": lw, "needs_rewrite": needs_rewrite(st), "auto_rewrite": auto_rewrite_result,
    }


def rewrite(st: Optional[dict] = None) -> dict:
    """Regenerate this Pi's configs into the real /usr/local/etc from saved state."""
    st = st or state.load()
    role = st.get("role")
    if role in ("conventional", "control"):
        w = WizardInput(**st["wizard"])
        vcs = st["pairing"]["voice_channels"] if w.topology == "trunked" else None
        g = generate(w, st.get("secrets"), vcs, rid=radios.rid_bundle(st))
        return {"ok": True, "how": "regenerated", "written": write_files(g)}
    if role == "voice":
        j = st["pairing"].get("joined") or {}
        if j.get("bundle_full") and j.get("local"):
            b = {**j["bundle_full"], "rid": radios.rid_bundle(st)}
            g = generate_voice_channel(b, VoiceLocal(**j["local"]), st.get("rest_password"))
            return {"ok": True, "how": "regenerated", "written": write_files(g)}
        return _copy_dryrun()
    return {"ok": False, "error": "Nothing to re-write yet - run Setup."}


def _copy_dryrun() -> dict:
    """Fallback for voice Pis paired with an older version: copy the dry-run files, fixing paths."""
    src = settings.dryrun_etc
    if not (src / "config.yml").exists():
        return {"ok": False, "error": "No dry-run configs found - pair this voice channel again."}
    settings.dvm_etc.mkdir(parents=True, exist_ok=True)
    settings.dvm_log_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for f in sorted(src.iterdir()):
        if f.is_file() and not f.name.endswith(".bak"):
            text = f.read_text().replace(str(settings.dryrun_etc), str(settings.dvm_etc)) \
                                .replace(str(settings.dryrun_log_dir), str(settings.dvm_log_dir))
            dst = settings.dvm_etc / f.name
            if dst.exists():
                shutil.copyfile(dst, dst.with_suffix(dst.suffix + ".bak"))
            dst.write_text(text)
            written.append(str(dst))
    from .configgen.generate import _record_write
    _record_write(written)
    return {"ok": True, "how": "copied from dry-run", "written": written}


def startup() -> None:
    """Real mode + root + no config in /usr/local/etc yet -> re-write automatically (nothing to clobber).
    If a real config already exists we only show the banner and let the user decide."""
    global auto_rewrite_result
    try:
        st = state.load()
        if needs_rewrite(st) and settings.is_root and not (settings.dvm_etc / "config.yml").exists():
            auto_rewrite_result = rewrite(st)
    except Exception as e:  # never block start-up
        auto_rewrite_result = {"ok": False, "error": str(e)}
