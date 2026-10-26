"""iden_table.dat generation + frequency math.

Verified against dvmhost src/host/Host.Config.cpp:
  txFreq = baseFrequency + (chSpaceKhz/0.125 * 125) * channelNo
  rxFreq = txFreq + txOffsetMhz * 1e6
  - channelNo is parsed as HEX, clamped to 1..4095
  - channelId is clamped to 0..15
  - txOffsetMhz == 0 is rejected ("invalid Tx offset"), so simplex uses a non-zero
    placeholder offset together with system.duplex=false + simplexSameFrequency=true.
Line format from configs/iden_table.example.dat:
  ChId,Base Freq,Spacing (khz),Input Offset (mhz),Bandwidth (khz),
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_FREQ_GAP_HZ = 30_000_000  # same limit used by the repo's tools/dvmcfggen/iden_table.py
SIMPLEX_PLACEHOLDER_OFFSET_MHZ = 5.0  # any non-zero value; ignored when simplexSameFrequency=true


@dataclass
class IdenEntry:
    channel_id: int
    base_freq: int
    spacing_khz: float
    offset_mhz: float
    bandwidth_khz: float

    def line(self) -> str:
        return f"{self.channel_id},{self.base_freq},{self.spacing_khz:.2f},{self.offset_mhz:.5f},{self.bandwidth_khz:.1f},"

    def tx_for(self, ch_no: int) -> int:
        space = int(self.spacing_khz / 0.125)
        return self.base_freq + (space * 125) * ch_no

    def rx_for(self, ch_no: int) -> int:
        return self.tx_for(ch_no) + int(self.offset_mhz * 1_000_000)


def parse_template(path: Path) -> tuple[list[str], list[IdenEntry]]:
    header, entries = [], []
    for raw in path.read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            header.append(raw)
            continue
        p = [x for x in s.split(",") if x != ""]
        entries.append(IdenEntry(int(p[0]), int(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return header, entries


@dataclass
class ChannelPlan:
    entry: IdenEntry
    channel_no: int
    duplex: bool
    simplex_same_freq: bool
    tx_hz: int   # repeater/hotspot transmit (radio receive)
    rx_hz: int   # repeater/hotspot receive (radio transmit)
    reused_template_row: bool

    @property
    def channel_no_hex(self) -> str:
        return format(self.channel_no, "X")


def mhz_to_hz(v: float | str) -> int:
    return int(round(float(v) * 1_000_000))


def plan_conventional(tx_mhz: float, rx_mhz: float, template: Path, channel_id: int = 1,
                      spacing_khz: float = 6.25, bandwidth_khz: float = 12.5) -> ChannelPlan:
    tx, rx = mhz_to_hz(tx_mhz), mhz_to_hz(rx_mhz)
    if tx <= 0 or rx <= 0:
        raise ValueError("Frequencies must be positive")
    space_hz = int(spacing_khz * 1000)
    simplex = tx == rx
    # 1) Try to reuse a row from the repo's example iden table (exact grid + offset match).
    _, rows = parse_template(template)
    for row in rows:
        if row.spacing_khz != spacing_khz or not (row.base_freq < tx <= row.base_freq + MAX_FREQ_GAP_HZ):
            continue
        if (tx - row.base_freq) % space_hz:
            continue
        ch = (tx - row.base_freq) // space_hz
        if not 1 <= ch <= 4095:
            continue
        if simplex or row.tx_for(ch) + int(row.offset_mhz * 1e6) == rx:
            return ChannelPlan(row, ch, not simplex, simplex, tx, rx, True)
    # 2) Otherwise build a custom row so this exact frequency is channel 1.
    if tx % 125:
        raise ValueError("TX frequency must be a multiple of 125 Hz")
    offset = SIMPLEX_PLACEHOLDER_OFFSET_MHZ if simplex else (rx - tx) / 1_000_000
    entry = IdenEntry(channel_id, tx - space_hz, spacing_khz, offset, bandwidth_khz)
    return ChannelPlan(entry, 1, not simplex, simplex, tx, rx, False)


def render_table(template: Path, entries: list[IdenEntry]) -> str:
    header, _ = parse_template(template)
    # keep the repo's comment header verbatim, then our rows
    head = [h for h in header if h.strip()]
    return "\n".join(head + [e.line() for e in entries]) + "\n"
