<p align="center"><img src="app/static/malilink-logo-800.jpg" width="560" alt="Malinois Link Communications Network"></p>

# Malinois Link Control

**Brand:** Malinois Link Communication Network — a friendly web app for setting up and running a
[DVMProject](https://github.com/DVMProject/dvmhost) (`dvmhost` / `dvmfne`) radio system on a
Raspberry Pi 5, without editing YAML by hand. Reach it from your phone over Tailscale.

> **Licensing & use.** Transmitting requires an appropriate license for your frequencies (e.g. an
> amateur radio license). DVMProject software is for personal, non-commercial, hobbyist use and must
> never be used for public-safety or life-safety communications. This app never keys a transmitter by
> itself: it writes configs, starts/stops services when you click, and *reads* status.

![dashboard](docs/screenshots/dashboard.png)

## What it does (v0.5.1)

| Area | Status |
|---|---|
| **Install DVM** (wizard step 2, after Welcome): apt deps → clone → `cmake`/`make` → `make install` → `dvmhost` user → systemd units, as a background job with live progress | ✅ real on any Pi with systemd (simulated only in dry-run) |
| **P25, DMR and NXDN** wizard — conventional or trunked for each (see the matrix in [DMR & NXDN](#dmr--nxdn)) → `config.yml`, `iden_table.dat`, `talkgroup_rules.yml`, `fne-config.yml` generated from the repo's example files with ruamel.yaml (comments preserved), talkgroups validated against the repo JSON schema | ✅ real |
| **Start/stop** dvmhost / dvmfne via systemd | ✅ real (simulated in dry-run) |
| **Program your radio** page per protocol (P25: NAC/WACN/sys; DMR: color code, timeslots, TG contacts; NXDN: RAN, group IDs, location ID) | ✅ real |
| **Dashboard**: per-Pi status (service, uptime, mode, TX, modem, freqs), who's talking, grants, affiliations, recent calls, live logs | ✅ live data layer + demo data layer |
| **Trunked, multi-Pi (P25 / DMR TSCC / NXDN CC)**: wizard "Trunked" makes this Pi the control channel; extra Pis use "Add this Pi as a voice channel" + a pairing code. The control Pi assigns the channel, rewrites its `voiceChNo` list and restarts dvmhost; the voice Pi writes its own VC config pointing back at the control Pi | ✅ implemented, tested in dry-run (not yet on RF hardware) |
| **Radios page** (RID manager): add / edit / delete radio IDs with a name, Allowed/Blocked, search, CSV import/export, **Lockdown** toggle → writes dvmhost's real `rid_acl.dat` + `system.radio_id.acl` | ✅ real; trunked: control Pi pushes the list to every voice Pi |
| **"Load my previous system values"** preset on the Setup welcome screen (ML105 trunked system) | ✅ fills the wizard (see mapping below) |
| **DMR / NXDN** (new in 0.4.0) | ✅ generated + parsed with dvmhost's own YAML parser; dry-run tested; not yet tested on RF hardware |
| Branding: Malinois Link logo in the sidebar, favicon, About and the setup welcome screen | ✅ |
| **Modern UI** (new in 0.5.0): sidebar nav, dark/light theme, live on-the-air channel tiles, sortable/filterable tables, toasts, mobile layout | ✅ no frameworks, no CDN — works offline on the Pi |

## What's new in 0.5.1 — modem port detection & calm service status

**MMDVM hat / modem serial port** (read-only — the app never opens the port or sends anything to the modem):

- **About → Modem** card and every **Pi page** show the *configured* port (from Setup, or the voice-channel join, plus
  what `config.yml` on disk says), the **detected serial ports** (`/dev/ttyAMA*`, `/dev/ttyS*`, `/dev/ttyACM*`,
  `/dev/ttyUSB*`, `/dev/serial0`, `/dev/serial1`, symlinks resolved, e.g. `serial0 → ttyAMA10`), whether each exists and
  is readable/writable by the `dvmhost` user, and which one is the **likely hat port** for this Pi model.
- **Setup → Modem → "Detect serial ports"** lists them; click **Use** to fill the port field.
- **Live modem status** when dvmhost runs, read from dvmhost's `GET /status` `modem` object (port, speed, firmware
  protocol, effective RX/TX frequency, RX level, DC offsets, …).
- Demo mode and dry-run on a non-Pi machine show **sample data, labelled SAMPLE DATA**.

Raspberry Pi UART facts used (from the official Raspberry Pi UART documentation):

| Pi | MMDVM hat (GPIO 14/15) | Enable it |
|---|---|---|
| **Pi 5** | `/dev/ttyAMA0` (UART0). `/dev/ttyAMA10` / default `serial0` is the 3-pin *debug* header, not the hat | `dtparam=uart0` in `/boot/firmware/config.txt` (newer EEPROMs: `enable_uart=1` also moves `serial0` there) |
| **Pi 4 / 3** | `/dev/ttyAMA0` with `dtoverlay=disable-bt` (or `miniuart-bt`); otherwise the mini UART `/dev/ttyS0` | `dtoverlay=disable-bt` + `sudo systemctl disable hciuart`, or `enable_uart=1` for the mini UART |

On every model, turn off the serial **login console** on that UART (raspi-config → Interface Options → Serial Port:
login shell *No*, hardware *Yes*; i.e. no `console=serial0,115200` in `cmdline.txt`) and reboot. The app reads
`config.txt`/`cmdline.txt` and shows these hints when something is missing.

**No more "REST: [Errno 111] Connection refused"** on the dashboard. Each Pi card shows one plain-language state
with a matching button:

| State | Meaning | Button |
|---|---|---|
| not installed | `dvmhost`/`dvmfne` binary missing | Install DVM |
| not configured | no `config.yml` / `fne-config.yml` yet | Open Setup |
| stopped / failed | installed but the service isn't running | Start dvmhost (same licence confirmation) · View logs |
| starting | service just started; status API not up yet (dvmhost opens the **modem before** it starts its REST API) | — |
| running but not answering | REST on 127.0.0.1:9990 (dvmfne: 9991) still refused after 25 s — usually the modem port is wrong | View logs · Check modem port |
| status password doesn't match / live status off | REST password or `restEnable` in the file on disk differs | Open Setup (re-save) |
| healthy | live status OK | — |

The REST client now reads `restEnable/restAddress/restPort/restPassword` from the config files actually on disk
(`config.yml` `network.*`, `fne-config.yml` `system.*`) — the values Setup/pairing write: REST enabled, bound to
`127.0.0.1`, ports 9990/9991, the app's own passwords — so control and voice Pis use exactly what dvmhost was started
with. Failed REST calls back off quietly (2 s → 30 s) with no error toasts. The client is still GET-only (nothing that
can key the transmitter). In dry-run, simulated services show "simulated" instead of trying a REST API that can't exist.

![modem detect](docs/screenshots/setup-modem-detect.png)
![pi page modem](docs/screenshots/node-modem.png)
![dashboard health](docs/screenshots/dashboard-health.png)

## What's new in 0.5.0 — UI redesign

A full visual refresh with **no behaviour, API or config-generation changes**. Still plain CSS + vanilla JS,
no build step and no CDN calls (Pis may be offline).

- **Design system**: CSS-variable tokens in the logo's blue/silver palette, refined dark theme plus an optional
  **light theme** (toggle in the top bar, remembered per browser), consistent cards, pills, buttons and spacing.
- **Navigation**: left sidebar with icons, mode pills and version; becomes a slide-in drawer on phones.
  The **DRY-RUN / REAL MODE / DEMO** banners and pills and the licensing notice stay visible on every page.
- **Dashboard**: KPI strip, **"On the air now"** tiles per channel (TS1/TS2 slots for DMR) with a live glow,
  per-Pi cards, and sortable/filterable grants, affiliations and recent-activity tables.
- **Setup wizard**: stepper with "Step N of 10" progress bar, done ticks, keyboard-selectable choice cards;
  horizontal stepper on mobile.
- **Toasts and dialogs** instead of browser `alert`/`confirm` (unpair, delete, lockdown, start dvmhost).
- **Accessibility**: skip link, visible focus rings, `aria-current`/`aria-sort`, keyboard nav, reduced-motion support.
- Empty, loading (skeleton) and error states everywhere.

| Desktop | Mobile |
|---|---|
| ![dashboard](docs/screenshots/dashboard.png) | ![mobile dashboard](docs/screenshots/mobile-dashboard.png) |
| ![setup](docs/screenshots/setup-review.png) | ![mobile setup](docs/screenshots/mobile-setup-review.png) |
| ![radios](docs/screenshots/radios.png) | ![mobile radios](docs/screenshots/mobile-radios.png) |
| ![program](docs/screenshots/program-trunked.png) | ![mobile program](docs/screenshots/mobile-program-trunked.png) |
| ![logs](docs/screenshots/logs.png) | ![mobile logs](docs/screenshots/mobile-logs.png) |
| ![trunking](docs/screenshots/trunking.png) | ![mobile trunking](docs/screenshots/mobile-trunking.png) |
| ![about](docs/screenshots/about.png) | ![mobile about](docs/screenshots/mobile-about.png) |
| ![light theme](docs/screenshots/dashboard-light.png) | ![mobile light](docs/screenshots/mobile-dashboard-light.png) |

Other screenshots: `node.png`, `setup-welcome.png`, `program.png` (conventional), `voice-pi-trunking.png`,
`real-mode-banner.png`, `program-light.png`, `setup-light.png`, and the DMR/NXDN ones below.

## Install on the Pi (Raspberry Pi OS Lite, Debian trixie, arm64)

```bash
sudo apt-get install -y git
git clone <this repo> malilink-control && cd malilink-control
sudo ./scripts/install-app.sh          # venv + deps + systemd unit "malilink-control" on :8080
```

**Update:** `git pull && sudo ./scripts/install-app.sh` — the script restarts `malilink-control` if it is
already running, so the new code is loaded (a stale process gives errors like "'mode' is undefined").

Then open `http://<pi-tailscale-name>:8080` and follow **Setup**:
1. **Install DVM** – builds dvmhost/dvmfne (≈15–30 min on a Pi 5; safe to re-run to update). Build commands run
   with `LC_ALL=C.UTF-8` (`cmake -S src -B src/build`, `make`), and their output is decoded with invalid bytes replaced,
   so odd bytes from git/cmake (e.g. while FetchContent clones WebSocket++) can't crash the job (fixed in 0.3.2).
   Source: `/opt/src/dvmhost` (`MALILINK_SRC_DIR`); build dir `/opt/src/dvmhost/build` is **deleted before every
   configure** (a clone of WebSocket++ interrupted by an app restart leaves a corrupt `build/_deps`), and a failed
   configure wipes it and retries once. A broken/half-cloned checkout is re-cloned; tick **clean source checkout**
   to run `git clean -fdx` first. If the app is restarted mid-install, the job shows as *failed / interrupted* —
   just run it again. Manual fallback: `sudo rm -rf /opt/src/dvmhost/build` (or the whole `/opt/src/dvmhost`).
   **Install dvmfne service only** (same step; chips show whether the dvmhost binary, dvmfne binary and dvmfne
   service are present): never rebuilds dvmhost, never runs `make install`, never touches `/usr/local/etc`.
   If `/usr/local/bin/dvmfne` exists it only installs `dvmfne.service` to `/lib/systemd/system`, creates the
   `dvmhost` user/log dir if missing and runs `systemctl enable dvmfne` (not started). If the binary is missing but
   `/opt/src/dvmhost/build` exists it runs `make dvmfne` there and copies `build/dvmfne` to `/usr/local/bin`.
   If neither exists it refuses and asks you to run Install / update DVM first. (`POST /api/install/fne`)
2. Pick **P25 → Conventional**, enter name/callsign, frequencies, NAC, modem port, network, talkgroups.
3. **Review & save** – see every generated file, tick the two licence/terms boxes, save, start.
4. **Program radio** – values to type into your radio's CPS.

Settings live in `/etc/default/malilink-control` (bind address, port, `MALILINK_PASSWORD` for HTTP
basic auth user `admin`, demo/dry-run). Recommended: `MALILINK_BIND=<your Tailscale 100.x IP>`.
The app runs as **root** (it runs apt, writes `/usr/local/etc`, calls `systemctl`) — keep it on your tailnet.

**Using the Pi's GPIO UART (`/dev/ttyAMA0`)?** Per the dvmhost README, first run:
```bash
sudo systemctl disable bluetooth.service serial-getty@ttyAMA0.service
sudo systemctl mask serial-getty@ttyAMA0.service
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt   # trixie path; README says /boot/config.txt
sudo sed -i 's/console=serial0,115200 *//' /boot/firmware/cmdline.txt && sudo reboot
```

## Run anywhere in demo mode

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
MALILINK_DEMO=1 .venv/bin/uvicorn app.main:app --port 8080     # http://localhost:8080
.venv/bin/pip install pytest && .venv/bin/python -m pytest -q tests
```
`MALILINK_DEMO=1` = simulated 3-Pi trunked site on the dashboard (implies dry-run unless `MALILINK_DRY_RUN=0`).
`MALILINK_DEMO_PROTOCOL=p25|dmr|nxdn` picks the demo protocol (otherwise it follows the protocol saved in Setup; default P25).
DMR demo voice channels carry two calls at once (TS1/TS2).

### Real mode vs dry-run

A bar under the header always says which one you're in (plus a **LIVE** / **DRY-RUN** pill).

| Situation | Mode |
|---|---|
| Linux with `systemctl` (a real Pi) — **even if dvmhost isn't installed yet** | **REAL**: Install DVM runs apt/cmake/make for real, configs go to `/usr/local/etc` (created if missing), services via systemctl |
| no `systemctl` / not Linux (laptop, container, CI) | dry-run automatically |
| `MALILINK_DRY_RUN=1`, or `MALILINK_DEMO=1` | dry-run (configs → `var/dryrun/etc`, installer + services simulated) |
| `MALILINK_DRY_RUN=0` (or `MALILINK_FORCE_REAL=1`) | real, no matter what |

Real mode needs root (the systemd unit from `scripts/install-app.sh` runs as root). Running as a normal user shows
a "Not running as root" warning, and Install DVM / config writes are refused with a clear message.

Earlier versions (≤ 0.3.0) switched to dry-run on a fresh Pi just because dvmhost wasn't installed, so the
installer did nothing and Setup/pairing configs went to `var/dryrun/etc`. After upgrading:
* if `/usr/local/etc/config.yml` doesn't exist and the app runs as root, the saved setup (wizard answers,
  paired voice channels, Radios list; voice Pis: their saved pairing bundle) is **re-written there automatically at start-up**;
* otherwise a **Re-write needed** bar offers *Write configs to /usr/local/etc now* (`POST /api/mode/rewrite`,
  existing files backed up as `*.bak`), or just run Setup again.
dvmhost/dvmfne won't be started while dvmhost isn't installed or the config is still only in dry-run.
`GET /api/mode` reports the mode, reason, root, installed binaries and last config write.

## Layout

```
app/
  main.py               FastAPI routes (pages + JSON API, SSE stream)
  settings.py, state.py env settings, JSON state (var/state.json)
  configgen/            generate.py (config.yml/fne-config.yml), iden.py (freq math), talkgroups.py (+schema)
  services/             systemd.py (start/stop/status), installer.py (Install DVM background job)
  data/                 base.py (interface), live.py, demo.py, dvmrest.py (read-only REST client), logparse.py
  pairing.py            trunking pairing API (codes, claim, join, remove)
  mode.py               real vs dry-run status, re-writing configs saved in dry-run
  radios.py             Radios page API (RID list, lockdown, CSV, sync to voice Pis); configgen/ridacl.py = file format
  templates/, static/   Jinja pages + vanilla JS/CSS (no build step)
dvm_templates/          example configs + schema + dvmhost.service copied from dvmhost (SOURCE.txt = commit)
systemd/                malilink-control.service, dvmfne.service
scripts/                install-app.sh, refresh_templates.sh
tests/                  test_smoke.py, test_radios.py, test_mode.py, test_pairing.py (2-3 live app processes), test_dvm_yaml.py,
                        test_dmr_nxdn.py (generator/validation/yaml/logparse), test_dmr_nxdn_pairing.py (live DMR/NXDN pairing),
                        dvmyaml/ (dvmhost YAML parser checker), fake_dvmhost.py (fake REST server)
docs/screenshots/       UI screenshots
```

## How it talks to DVM (verified in dvmhost source)

* REST (`src/host/restapi`, `src/fne/restapi`): `PUT /auth {"auth": sha256_hex(restPassword)}` → `token`;
  then header `X-DVM-Auth-Token`. Used read-only: `GET /status`, `/p25|dmr|nxdn/report-affiliations` (picked from
  `p25Enabled`/`dmrEnabled`/`nxdnEnabled` in `/status`), `/voice-ch`.
  The client refuses any path not on a read-only allow-list (no `/mdm/mode`, `/grant-tg`, `/p25/rid`, `/p25/raw-tsbk`,
  `/dmr/beacon`, `/dmr/cc-enable`, `/dmr/cc-broadcast`, `/dmr/rid`, `/nxdn/cc`, `/nxdn/cc-enable` …).
* dvmhost REST on `127.0.0.1:9990`; dvmfne REST moved to `9991` (both default to 9990).
* Logs: `<filePath>/DVM-YYYY-MM-DD.log` and `.activity.log` (activity lines `A: <ts> P25|DMR|NXDN RF|Net …`;
  DMR lines start `Slot N …` and use "voice header" / "end of voice transmission"; see `app/data/logparse.py`).
* Config parsing quirks honoured: `channelNo`, `nac`, `netId`, `sysId`, `rfssId`, `siteId` are hex strings;
  `voiceChNo` must not be empty; iden offset must be non-zero (simplex uses `simplexSameFrequency`).

## DMR & NXDN

Pick the protocol on the wizard's **System type** step; the **System IDs**, **Talkgroups** and **Review** steps change
with it. Exactly one protocol is enabled in `config.yml` (`protocols.<p>.enable`) with `system.fixedMode: true` —
dvmhost refuses more than one protocol in fixed mode and a dedicated control channel forbids the other two
(`Host.cpp`).

| | P25 | DMR | NXDN |
|---|---|---|---|
| Conventional | ✅ NAC, site/sys IDs | ✅ Tier II: color code 0–15, both/either timeslot (`network.slot1/slot2`), per-slot talkgroups, optional roaming beacons (duplex only) | ✅ RAN 0–63, group IDs (16-bit), location ID shown |
| Trunked (control + voice Pis) | ✅ P25 CC | ✅ DMR TSCC dedicated (`protocols.dmr.control.enable/dedicated/slot`) | ✅ NXDN CC dedicated (`protocols.nxdn.control.enable/dedicated`) ⚠️ less exercised upstream |
| Voice-channel pairing / auto-join | ✅ | ✅ (bundle carries `mode` + color code/slots) | ✅ (bundle carries `mode` + RAN; 6.25 kHz steps) |
| Channel bandwidth / step | 12.5 kHz / 2 × 6.25 kHz | 12.5 kHz / 2 × 6.25 kHz | 6.25 kHz / 1 × 6.25 kHz (NXDN48) |
| Channel numbers | 1–4095 | 1–4095 | 1–1023 (NXDN `SiteData` clamp) |
| Talkgroup / radio IDs | TG 1–65535 / RID 24-bit | TG + RID 24-bit, TG per slot | 1–65535 for both |
| Site IDs | NAC, WACN, sys, RFSS, site | `dmrNetId` 1–7F, site 1–1F (small site model) | sys 1–3FF, site; location ID = `(sys & 0x3FF) << 12 \| site` |
| Dashboard / who's talking | ✅ | ✅ per timeslot (TS1/TS2), grants, affiliations (`/dmr/report-affiliations`) | ✅ (`/nxdn/report-affiliations`) |
| Program radio page | ✅ | ✅ color code, TS per TG, contacts | ✅ RAN, group IDs, location ID, 6.25 kHz |

Every key comes from `configs/config.example.yml` and is read in `Host.Config.cpp` / `Host.cpp` /
`dmr/Control.cpp` / `nxdn/Control.cpp`: `system.config.colorCode` (uint, clamped 0–15), `system.config.dmrNetId`
(hex string), `system.config.ran` (uint), `network.slot1/slot2`, `protocols.dmr.beacons.enable`,
`protocols.dmr.control.{enable,dedicated,slot}`, `protocols.nxdn.control.{enable,dedicated}`.
`talkgroup_rules.yml` `source.slot` only matters for DMR (P25/NXDN look up with slot 0, which `TalkgroupRulesLookup::find` treats as "any slot");
we write 1 for P25/NXDN like the example. `tests/dvmyaml/yamlcheck` parses the generated files with dvmhost's own
YAML parser and prints these values the way dvmhost reads them.

**Honest limitations**
* Nothing here has been tested on RF hardware for DMR or NXDN yet (dry-run + dvmhost YAML parser only).
* DMR trunking is dvmhost's TSCC dedicated control channel (Tier III-style). Upstream doesn't document which
  commercial Tier III radios interoperate — bench-test first.
* NXDN trunking: dvmhost supports a dedicated NXDN CC and grants via RPC like P25/DMR, but the repo's own
  `dvmcfggen` only ships P25/DMR trunking templates, so it is less exercised. Radio trunking-type compatibility is
  not documented upstream.
* NXDN is NXDN48 only (dvmhost frames are 384 bits / 4800 bps). The 6.25 kHz bandwidth we put in the NXDN iden row
  is our choice; it isn't clear that dvmhost uses the iden bandwidth for NXDN.
* The NXDN location ID is computed by dvmhost; the app only displays the same math (`nxdn/Control.cpp`).
* DMR/NXDN *system*-log lines in demo mode are illustrative; activity-log lines use the real dvmhost strings.
* dvmhost's `/status` has no color code / RAN, so the dashboard shows them from the saved config.
* `fne-config.yml` keeps `allowDMRTraffic` / `allowP25Traffic` / `allowNXDNTraffic` at the example's `true`.

![setup DMR](docs/screenshots/setup-dmr-ids.png)
![program NXDN](docs/screenshots/program-nxdn.png)
![dashboard DMR](docs/screenshots/dashboard-dmr.png)

## Radios (RID access list)

![radios](docs/screenshots/radios.png)

Nav → **Radios**. Each entry is a radio ID (1–16777215), a name, and **Allowed**/**Blocked**.

| You do | What is written |
|---|---|
| add / edit / delete / block a radio | `rid_acl.dat` next to `config.yml` (`/usr/local/etc`), one line per radio: `RID,1|0,Name,IP,` + the repo's comment header + trailing blank line |
| Lockdown **off** | `system.radio_id.acl: false` → everybody may talk **except** radios marked Blocked |
| Lockdown **on** | `system.radio_id.acl: true` → **only** radios listed as Allowed may talk/register |
| CSV import | merge or replace; columns `rid,alias,enabled,ip` (also accepts `Radio ID,Name` sheets and pasted `rid_acl.dat` lines) |

Verified in dvmhost source:
* format — `configs/rid_acl.example.dat` + `RadioIdLookup::load()` (comma split, `#` comments, enabled = field 2 `== 1`,
  no quoting → commas are stripped from names);
* config keys — host reads only `system.radio_id.file`, `time`, `acl` (`Host.cpp`); there is **no `enable` key**.
  dvmfne reads `system.radio_id.file`/`time` and always builds its table with ACL on (`HostFNE.cpp`);
  both point at the same file on the hub Pi, so one write covers both;
* allow/block logic — `AccessControl::validateSrcId()` (P25/DMR/NXDN): ACL off → only listed+disabled are refused;
  ACL on → anything not listed+enabled is refused;
* reloads — the lookup thread re-reads the file every `time` minutes (`LookupTable.h`; template value 2), so list
  edits apply **without a restart**. `acl` is read only at start-up, so the Lockdown switch shows
  **Restart dvmhost now** when dvmhost is running (restart = a few seconds off-air; it never keys the transmitter).
  The app refuses Lockdown with zero allowed radios.

Trunked: the control Pi is the source of truth. The list + lockdown go to a new voice Pi in its pairing bundle and
are pushed to every paired voice Pi (`POST /api/radios/sync`, cluster key) on each change; voice Pis show it
read-only. (DVM's own mechanism — the FNE pushing whitelist/blacklist to peers with `network.updateLookups` —
is not used because it only toggles IDs and drops names; `updateLookups` stays off.) The FNE REST
`/rid/*`, `/reload-rids` endpoints exist but aren't called (the app's REST client is read-only).

API: `GET/POST /api/radios`, `PUT/DELETE /api/radios/{rid}`, `POST /api/radios/lockdown {enabled, restart?}`,
`POST /api/radios/import {csv, mode}`, `GET /api/radios/export.csv`, `GET /api/radios/rid_acl.dat`,
`POST /api/radios/apply {restart}`.

## "Load my previous system values" preset

| Value | Wizard field → config key |
|---|---|
| P25 trunked, this Pi = control channel | topology `trunked` |
| WACN A1055 / System ID 105 / RFSS 1 / Site 1 | `system.config.netId: A1055`, `sysId: 105` (hex, as written), `rfssId: 1`, `siteId: 1` |
| NAC 105 decimal | `nac: 069` (hex) |
| Channel ID 2, 450 MHz, 6.25 kHz | matches row `2,450000000,6.25,5.000,12.5` of the repo iden table (+5 MHz offset) → `channelId: 2` |
| Control 2-385 | `channelNo: 385` → TX 455.63125 / RX 460.63125 MHz (hex, how dvmhost parses it). The preset lets you pick **decimal** instead → 452.40625 / 457.40625 MHz |
| Voice 2-457 | voice Pi join: "Use my previous voice channel" → TX 456.94375 MHz (hex) or 452.85625 (decimal), peer 900003 |
| TG 101 "ML105 Main" | `talkgroup_rules.yml` |
| CW ID ML105 | `system.cwId.callsign` (also used as system name) |
| FNE peer 900001 | `fne-config.yml master.peerId` (new "Hub (FNE) peer ID" field) |
| CC host peer 900002 | `network.id` |
| VC host peer 900003 | new "First voice-channel peer ID" field → handed out to the first paired VC (then 900004… if free) |
| Console peer 900004 | **not configured by the app** — enter it in your console software; it connects to dvmfne with the hub password (peer ACL off) |
| RIDs 800001, 800002 + lockdown | Radios list replaced and `radio_id.acl: true` when you save Setup |

**Unverified:** whether your old "105" system ID and "385/457" channel numbers were hex (config.yml) or decimal
(radio programming) — check the frequencies shown before saving. Note the VC peer 900003 and an auto-assigned 2nd VC
would collide with console peer 900004; give a 2nd VC an explicit peer ID on its join screen.

## Trunked P25 / DMR / NXDN across several Pis

**Unpairing**
* **Voice Pi → "Unpair this Pi"** (Dashboard, Trunking, Setup welcome / Review): stops dvmhost/dvmfne, moves its
  configs to `*.bak`, copies `state.json` to `state.json.bak`, resets to a fresh unconfigured state, and best-effort
  tells the control Pi (`POST /api/pairing/vc-release`, cluster key) to drop the channel. Works even if the control
  Pi is offline (then remove it there later).
* **Control Pi → "Remove / unpair"** next to each voice channel on the **Trunking** tab (also on its Dashboard):
  removes it from the channel list, rewrites the control Pi's configs (`voiceChNo`; dvmhost restarts if running)
  and best-effort tells the voice Pi to release itself (`POST /api/pairing/released`, cluster key).
* **"Unpair & reset this Pi to a fresh start"** (control Pi, Trunking/Dashboard; "Reset this Pi" for conventional):
  same reset as above, releases every reachable voice Pi, works with them offline.
API: `POST /api/pairing/voice-channels/{chNo}/unpair`, `POST /api/pairing/reset {notify}`. Safe in dry-run
(services simulated, files under `var/dryrun/etc`).

1. **Control Pi:** Setup → P25, DMR or NXDN → **Trunked** → finish the wizard (it always runs dvmfne). dvmhost will
   not start until at least one voice channel is paired (dvmhost refuses an empty `voiceChNo`).
2. **Trunking → Create pairing code** (10 min, single use).
3. **Each voice Pi:** install the app, Setup → **Add this Pi as a voice channel**, enter
   `http://<control-pi>:8080`, the code, its modem port and (optionally) a specific TX frequency.
4. The control Pi gives it the next free channel (`step = bandwidth / spacing`, i.e. 12.5 kHz apart; 6.25 kHz for NXDN) on
   the same identity row, regenerates its config with the new `voiceChNo` entry and restarts dvmhost if
   running. The voice Pi writes its config, `iden_table.dat` and `talkgroup_rules.yml`; you start it from
   its dashboard. Remove a VC from the control Pi's Trunking page.
5. The control Pi's dashboard pulls every voice Pi's status/logs via `/api/local/*` (cluster key).

Generated key combinations:

| | Control channel | Voice channel |
|---|---|---|
| `protocols.p25.control.enable / dedicated` | true / true | false / false |
| `system.config.authoritative / supervisor` | true / true | false / false |
| `system.config.controlCh` | left at default (`rpcPort: 0`) | `rpcAddress`=control Pi IP, `rpcPort: 9890`, `rpcPassword`, `notifyEnable: true` |
| `system.config.voiceChNo` | one entry per VC (`channelId`, hex `channelNo`, VC `rpcAddress`, 9890, `rpcPassword`) | own channel only |
| `network` | → local dvmfne, `rpcAddress: 0.0.0.0` | → control Pi's dvmfne, `id` = "First voice-channel peer ID" (counting up) or peerId + chNo, `rpcAddress: 0.0.0.0` |

Verified in source: the keys and how they're parsed (`Host.Config.cpp`, `p25/Control.cpp`), VC→CC
notification needs address + non-zero port + `notifyEnable`, `dedicated` only applies with
`control.enable`, `supervisor` only read when `authoritative`, and RPC uses SHA-256(`rpcPassword`) as the
pre-shared key (`NetRPC.cpp`) → **one shared rpcPassword** across the system. The CC/VC
authoritative/supervisor/dedicated values match the repo's own `tools/dvmcfggen` templates.
**Unverified:** that this combination trunks correctly on air (not run on hardware), whether a VC needs
its own channel in `voiceChNo`, and Tailscale/UDP RPC behaviour between Pis.

`tests/dvmyaml/` builds a tiny checker against dvmhost's own YAML parser
(`tests/dvmyaml/build.sh /path/to/dvmhost`); `tests/test_dvm_yaml.py` then parses the generated CC, VC
and conventional configs with it (skipped if not built).
