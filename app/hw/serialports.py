"""Serial-port / MMDVM-hat modem detection (read-only).

Nothing here opens a serial port or talks to the modem: we only list device nodes, resolve symlinks,
stat() them, and read /proc/device-tree/model, /boot/firmware/config.txt and cmdline.txt.

Raspberry Pi facts used below (verified against raspberrypi/documentation
`computers/configuration/uart.adoc` and the RPi forum/issue threads it links, Oct 2026):
  * /dev/ttyAMA0 = first PL011 (UART0); /dev/ttyS0 = mini UART; /dev/serial0 / serial1 are symlinks to
    the primary / secondary (Bluetooth) UART. Bookworm+ normally has no /dev/serial1.
  * Pi 5: no mini UART. /dev/ttyAMA10 is the 3-pin *Debug* header UART and the default target of
    serial0. The 40-pin GPIO 14/15 UART (where an MMDVM hat sits) is UART0 = /dev/ttyAMA0, enabled with
    `dtparam=uart0` (or `dtparam=uart0_console`, which also makes it serial0/console). With EEPROM
    2025-02-12 or newer, `enable_uart=1` also moves serial0/the console to GPIO 14/15 (ttyAMA0).
  * Pi 4 (and 3 / Zero W / Zero 2 W): the primary (GPIO 14/15) UART is the mini UART /dev/ttyS0, which is
    only enabled with `enable_uart=1` (and then has its baud rate tied to a fixed core clock). With
    `dtoverlay=disable-bt` or `dtoverlay=miniuart-bt` the PL011 /dev/ttyAMA0 moves to GPIO 14/15 —
    the usual MMDVM hat setup.
  * The serial *console* (`console=serial0,115200` in cmdline.txt) must not run on the hat's UART.
UNVERIFIED (marked in the UI as a guess): exact model-string matching beyond "Raspberry Pi 5"/"4"/"3".
"""
from __future__ import annotations

import glob
import grp
import os
import pwd
import re
import stat
from pathlib import Path

PATTERNS = ["/dev/ttyAMA*", "/dev/ttyS*", "/dev/ttyACM*", "/dev/ttyUSB*", "/dev/serial0", "/dev/serial1"]
GPIO_HAT_OVERLAYS_PI4 = ("disable-bt", "miniuart-bt", "pi3-disable-bt", "pi3-miniuart-bt")


def _r(root: str, p: str) -> str:
    return os.path.join(root, p.lstrip("/")) if root not in ("", "/") else p


def pi_model(root: str = "/") -> str | None:
    for p in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            return Path(_r(root, p)).read_bytes().rstrip(b"\x00").decode(errors="replace").strip() or None
        except OSError:
            continue
    return None


def pi_generation(model: str | None) -> int | None:
    m = re.search(r"Raspberry Pi (\d)", model or "")
    if m:
        return int(m.group(1))
    if model and "Raspberry Pi Zero 2" in model:
        return 3  # same UART layout as Pi 3
    return None


def read_boot_config(root: str = "/") -> dict:
    """Parse the bits of config.txt / cmdline.txt that matter for the hat UART (all sections, naive)."""
    out = {"config_path": None, "enable_uart": None, "dtparams": [], "dtoverlays": [],
           "cmdline_path": None, "console": []}
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        f = Path(_r(root, p))
        if f.is_file():
            out["config_path"] = p
            for line in f.read_text(errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k == "enable_uart":
                    out["enable_uart"] = v == "1"
                elif k == "dtparam":
                    out["dtparams"] += [x.strip() for x in v.split(",") if x.strip()]
                elif k == "dtoverlay":
                    out["dtoverlays"].append(v.split(",")[0].strip())
            break
    for p in ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt"):
        f = Path(_r(root, p))
        if f.is_file():
            out["cmdline_path"] = p
            out["console"] = [t.split("=", 1)[1].split(",")[0] for t in f.read_text(errors="replace").split()
                              if t.startswith("console=")]
            break
    return out


def _user_access(st: os.stat_result, user: str) -> tuple[bool | None, bool | None]:
    """Can `user` read / write a device with this stat? None when the user doesn't exist (yet)."""
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return None, None
    if pw.pw_uid == 0:
        return True, True
    gids = {pw.pw_gid} | {g.gr_gid for g in grp.getgrall() if user in g.gr_mem}
    m = st.st_mode
    if st.st_uid == pw.pw_uid:
        return bool(m & stat.S_IRUSR), bool(m & stat.S_IWUSR)
    if st.st_gid in gids:
        return bool(m & stat.S_IRGRP), bool(m & stat.S_IWGRP)
    return bool(m & stat.S_IROTH), bool(m & stat.S_IWOTH)


def _describe(dev: str, gen: int | None) -> tuple[str, str]:
    """(kind, description) for a resolved /dev/tty* name."""
    name = os.path.basename(dev)
    if name == "ttyAMA10":
        return "debug", "Pi 5 3-pin Debug header UART (not the GPIO hat pins)"
    if name == "ttyAMA0":
        if gen == 5:
            return "gpio", "PL011 UART0 on GPIO 14/15 (pins 8/10) — MMDVM hat port on Pi 5"
        if gen in (3, 4):
            return "gpio", "PL011 UART0 — on GPIO 14/15 only with dtoverlay=disable-bt / miniuart-bt, otherwise Bluetooth"
        return "gpio", "PL011 UART0 (first hardware UART)"
    if name.startswith("ttyAMA"):
        return "uart", "extra PL011 UART (needs its own dtoverlay pins)"
    if name == "ttyS0":
        if gen in (3, 4):
            return "gpio", "mini UART on GPIO 14/15 (Pi 3/4 default primary UART, needs enable_uart=1)"
        return "uart", "8250/mini UART"
    if name.startswith("ttyS"):
        return "uart", "8250-style UART (often a placeholder with no hardware)"
    if name.startswith("ttyACM"):
        return "usb", "USB CDC-ACM modem (e.g. STM32 repeater board / MMDVM over USB)"
    if name.startswith("ttyUSB"):
        return "usb", "USB-serial adapter (FTDI/CP210x/CH340)"
    return "other", ""


def likely_hat_port(gen: int | None, boot: dict) -> tuple[str | None, str]:
    ov, dp = set(boot.get("dtoverlays") or []), set(boot.get("dtparams") or [])
    if gen == 5:
        return "/dev/ttyAMA0", "Pi 5: the GPIO 14/15 UART is /dev/ttyAMA0 (enable it with dtparam=uart0)."
    if gen in (3, 4):
        if ov & set(GPIO_HAT_OVERLAYS_PI4):
            return "/dev/ttyAMA0", f"Pi {gen}: dtoverlay={sorted(ov & set(GPIO_HAT_OVERLAYS_PI4))[0]} puts the PL011 /dev/ttyAMA0 on GPIO 14/15."
        return "/dev/ttyS0", (f"Pi {gen}: without dtoverlay=disable-bt the GPIO UART is the mini UART /dev/ttyS0 "
                              "(needs enable_uart=1). Most MMDVM guides add dtoverlay=disable-bt to use /dev/ttyAMA0 instead.")
    return None, "Not a Raspberry Pi (or model unknown) — pick the USB or serial port your modem is on."


def hints(gen: int | None, boot: dict, ports: list[dict], hat: str | None) -> list[str]:
    out = []
    cfg = boot.get("config_path") or "/boot/firmware/config.txt"
    dp, ov = set(boot.get("dtparams") or []), set(boot.get("dtoverlays") or [])
    hat_found = hat and any(p["path"] == hat and p["exists"] for p in ports)
    if gen == 5 and not ({"uart0", "uart0=on", "uart0_console"} & dp or "uart0-pi5" in ov or boot.get("enable_uart")):
        out.append(f"Pi 5 GPIO UART looks disabled: add `dtparam=uart0` to {cfg} and reboot.")
    if gen in (3, 4) and not (ov & set(GPIO_HAT_OVERLAYS_PI4)):
        out.append(f"For an MMDVM hat on a Pi {gen}, add `dtoverlay=disable-bt` (and `enable_uart=1`) to {cfg}, "
                   "run `sudo systemctl disable hciuart`, and reboot — the hat is then on /dev/ttyAMA0.")
    cons = [c for c in boot.get("console") or [] if c.startswith(("serial", "ttyAMA", "ttyS"))]
    if cons:
        out.append(f"A serial login console is enabled on {', '.join(cons)} ({boot.get('cmdline_path')}). It will fight "
                   "with the modem: remove `console=serial0,115200` from cmdline.txt (raspi-config → Interface Options → "
                   "Serial Port: login shell No, serial hardware Yes) and reboot.")
    if gen and not hat_found:
        out.append("No hat UART found right now — after changing config.txt a reboot is required.")
    return out


def scan(root: str = "/", user: str = "dvmhost") -> dict:
    model = pi_model(root)
    gen = pi_generation(model)
    boot = read_boot_config(root)
    found = sorted({p for pat in PATTERNS for p in glob.glob(_r(root, pat))})
    ports = []
    for full in found:
        path = "/" + os.path.relpath(full, root) if root not in ("", "/") else full
        is_link = os.path.islink(full)
        real = os.path.realpath(full)
        real_path = "/" + os.path.relpath(real, root) if root not in ("", "/") else real
        exists = os.path.exists(full)
        rd = wr = None
        if exists:
            try:
                rd, wr = _user_access(os.stat(full), user)
            except OSError:
                pass
        kind, desc = _describe(real_path, gen)
        ports.append({"path": path, "link": is_link, "target": real_path if is_link else None, "exists": exists,
                      "readable": rd, "writable": wr, "kind": kind, "description": desc})
    # hide the 32 placeholder 8250 ports most x86/VM kernels create unless one is the target of serial0/1
    targets = {p["target"] for p in ports if p["target"]}
    ports = [p for p in ports if not (re.fullmatch(r"/dev/ttyS([1-9]\d*)", p["path"]) and p["path"] not in targets)]
    hat, why = likely_hat_port(gen, boot)
    for p in ports:
        p["likely_hat"] = bool(hat) and (p["path"] == hat or p["target"] == hat)
    return {"model": model, "pi_generation": gen, "boot": boot, "user": user, "ports": ports,
            "likely_hat": hat, "likely_hat_reason": why, "hints": hints(gen, boot, ports, hat), "sample": False}


def sample(reason: str) -> dict:
    """Clearly-labelled sample result for demo / dry-run machines that aren't a Pi."""
    boot = {"config_path": "/boot/firmware/config.txt", "enable_uart": None, "dtparams": ["uart0"],
            "dtoverlays": [], "cmdline_path": "/boot/firmware/cmdline.txt", "console": ["tty1"]}
    mk = lambda path, kind, desc, target=None, hat=False: {  # noqa: E731
        "path": path, "link": target is not None, "target": target, "exists": True, "readable": True,
        "writable": True, "kind": kind, "description": desc, "likely_hat": hat}
    ports = [mk("/dev/serial0", "debug", "Pi 5 3-pin Debug header UART (not the GPIO hat pins)", "/dev/ttyAMA10"),
             mk("/dev/ttyAMA0", "gpio", "PL011 UART0 on GPIO 14/15 (pins 8/10) — MMDVM hat port on Pi 5", hat=True),
             mk("/dev/ttyAMA10", "debug", "Pi 5 3-pin Debug header UART (not the GPIO hat pins)"),
             mk("/dev/ttyACM0", "usb", "USB CDC-ACM modem (e.g. STM32 repeater board / MMDVM over USB)")]
    return {"model": "Raspberry Pi 5 Model B Rev 1.0 (sample)", "pi_generation": 5, "boot": boot, "user": "dvmhost",
            "ports": ports, "likely_hat": "/dev/ttyAMA0",
            "likely_hat_reason": "Pi 5: the GPIO 14/15 UART is /dev/ttyAMA0 (enable it with dtparam=uart0).",
            "hints": [], "sample": True, "sample_reason": reason}
