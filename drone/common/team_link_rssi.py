"""RSSI hook for ``team_link`` — per-teammate signal strength from ``iw``.

``TeamLink(rssi_provider=...)`` takes any ``callable(drone_id, addr) -> dBm or
None``. This is the stock one for a Linux mesh/IBSS interface: it reads
``iw dev <if> station dump`` (signal per neighbour MAC) and maps the frame's
source address to a MAC via the IPv6/IPv4 neighbour table, falling back to the
EUI-64 MAC embedded in an fe80:: address. Anything it cannot resolve is None,
so the allocator must treat RSSI as optional.

Results are cached for ``ttl_s`` so ``link_stats()`` stays cheap; it is
called from the allocator, never from the RX path.

Python 3.8, stdlib only: this ships to the aircraft.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

_MAC = r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}"
_STATION = re.compile(r"^Station\s+(" + _MAC + r")")
_SIGNAL = re.compile(r"^\s*signal:\s*(-?\d+)")
_NEIGH = re.compile(r"^(\S+)\s.*\blladdr\s+(" + _MAC + r")")


def parse_station_dump(text: str) -> Dict[str, float]:
    """``iw dev X station dump`` -> {mac (lower case): signal dBm}."""
    out = {}  # type: Dict[str, float]
    mac = None
    for line in text.splitlines():
        m = _STATION.match(line)
        if m:
            mac = m.group(1).lower()
            continue
        m = _SIGNAL.match(line)
        if m and mac is not None and mac not in out:
            out[mac] = float(m.group(1))
    return out


def parse_neigh(text: str) -> Dict[str, str]:
    """``ip neigh show dev X`` (v4 or v6) -> {ip: mac (lower case)}."""
    out = {}  # type: Dict[str, str]
    for line in text.splitlines():
        m = _NEIGH.match(line.strip())
        if m:
            out[m.group(1).lower()] = m.group(2).lower()
    return out


def mac_from_link_local(addr: str) -> Optional[str]:
    """MAC from an EUI-64 fe80:: address (``…ff:fe…`` in the interface id),
    or None for stable-privacy / random interface ids."""
    import socket
    addr = addr.split("%", 1)[0]
    try:
        b = socket.inet_pton(socket.AF_INET6, addr)
    except (OSError, ValueError):
        return None
    if b[:2] != b"\xfe\x80" or b[11:13] != b"\xff\xfe":
        return None
    mac = bytes([b[8] ^ 0x02]) + b[9:11] + b[13:16]
    return ":".join(f"{x:02x}" for x in mac)


def _run(cmd: List[str]) -> str:
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=2.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


class IwStationRssi:
    """``rssi_provider`` for TeamLink backed by ``iw`` + ``ip neigh``."""

    def __init__(self, interface: str, ttl_s: float = 2.0,
                 runner: Optional[Callable[[List[str]], str]] = None):
        self.interface = interface
        self.ttl_s = ttl_s
        self._run = runner or _run
        self._lock = threading.Lock()
        self._at = -1e9
        self._signal = {}  # type: Dict[str, float]
        self._neigh = {}   # type: Dict[str, str]

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._at < self.ttl_s:
            return
        self._at = now
        self._signal = parse_station_dump(
            self._run(["iw", "dev", self.interface, "station", "dump"]))
        neigh = parse_neigh(self._run(["ip", "-6", "neigh", "show", "dev", self.interface]))
        neigh.update(parse_neigh(self._run(["ip", "-4", "neigh", "show", "dev", self.interface])))
        self._neigh = neigh

    def __call__(self, drone_id: str, addr: str) -> Optional[float]:
        if not addr:
            return None
        with self._lock:
            self._refresh()
            ip = addr.split("%", 1)[0].lower()
            mac = self._neigh.get(ip) or mac_from_link_local(ip)
            if mac is None:
                return None
            return self._signal.get(mac)
