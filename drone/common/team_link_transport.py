"""Datagram transports for ``team_link``.

The team link only needs "send this frame to every teammate in radio range"
and "give me the next frame anyone sent". Everything above that — framing,
HMAC, replay rejection, peer state — lives in ``team_link.py`` and is the same
whichever transport carries it.

Transports:
  UdpBroadcastTransport   — IPv4 UDP broadcast on a shared LAN (needs an AP, or
                            an IPv4 address on the mesh interface). The original
                            behaviour of TeamLink.
  Ipv6MulticastTransport  — IPv6 link-local multicast on a named interface.
                            Needs no DHCP, no AP and no address config: the
                            kernel assigns fe80:: as soon as the interface is up,
                            so it works on a bare 802.11s / IBSS link
                            (``drone/platforms/orin/mesh_setup.sh``).
  MemoryHub/MemoryTransport — in-process, for tests and SITL.

Python 3.8, stdlib only: this ships to the aircraft.
"""
from __future__ import annotations

import logging
import queue
import socket
import struct
import threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

TEAM_PORT = 5760
BCAST_ADDR = "255.255.255.255"
# Link-local scope (ff02::/16), not an IANA-assigned group. Every vehicle joins
# it on the mesh interface; hop limit 1 keeps it on the link.
MCAST6_GROUP = "ff02::7e4d:5760"
RECV_BUF = 2048          # > MAX_FRAME_BYTES; anything bigger is dropped by the codec


class TeamTransport:
    """One-to-all datagram carrier. Implementations must be safe to ``send``
    from several threads while one thread sits in ``recv``."""

    name = "abstract"

    def open(self) -> None:
        raise NotImplementedError

    def send(self, data: bytes) -> None:
        """Deliver ``data`` to every listener on the link. Raises OSError on failure."""
        raise NotImplementedError

    def recv(self, timeout: float) -> Optional[Tuple[bytes, str]]:
        """Next ``(datagram, source_address)``, or None after ``timeout`` seconds."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


class _SocketTransport(TeamTransport):
    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._rx_ok = False

    def recv(self, timeout: float) -> Optional[Tuple[bytes, str]]:
        sock = self._sock
        if sock is None or not self._rx_ok:
            # Bind failed (or closed): behave like an idle link instead of
            # spinning the RX thread.
            threading.Event().wait(timeout)
            return None
        sock.settimeout(timeout)
        try:
            data, addr = sock.recvfrom(RECV_BUF)
        except socket.timeout:
            return None
        return data, str(addr[0])

    def close(self) -> None:
        sock, self._sock = self._sock, None
        self._rx_ok = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class UdpBroadcastTransport(_SocketTransport):
    """IPv4 UDP broadcast — the original TeamLink transport.

    ``bcast_addr``/``tx_port`` default to 255.255.255.255 and ``port``; tests
    point them at 127.0.0.1 and a second port to run two links on one host.
    """

    name = "udp_broadcast"

    def __init__(self, port: int = TEAM_PORT, bcast_addr: str = BCAST_ADDR,
                 bind_addr: str = "", tx_port: Optional[int] = None,
                 reuse_port: bool = False):
        super().__init__()
        self.port = int(port)
        self.bcast_addr = bcast_addr
        self.bind_addr = bind_addr
        self.tx_port = int(tx_port) if tx_port is not None else self.port
        self.reuse_port = reuse_port

    def open(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.reuse_port and hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock = sock
        try:
            sock.bind((self.bind_addr, self.port))
            self._rx_ok = True
        except OSError as e:
            logger.warning(f"TeamLink bind failed on {self.port}: {e} — RX disabled")

    def send(self, data: bytes) -> None:
        if self._sock is None:
            raise OSError("transport not open")
        self._sock.sendto(data, (self.bcast_addr, self.tx_port))

    def describe(self) -> str:
        return f"udp_broadcast {self.bcast_addr}:{self.tx_port} (rx :{self.port})"


class Ipv6MulticastTransport(_SocketTransport):
    """IPv6 link-local multicast on one interface (``mesh0``, ``wlan1`` …).

    Joins ``group`` on ``interface`` only, sends with hop limit 1, and pins
    outgoing multicast to that interface, so nothing leaks onto another
    network the vehicle happens to be on.
    """

    name = "ipv6_multicast"

    def __init__(self, interface: str, port: int = TEAM_PORT,
                 group: str = MCAST6_GROUP, hops: int = 1, loop: bool = True):
        super().__init__()
        if not interface:
            raise ValueError("Ipv6MulticastTransport needs an interface name")
        self.interface = interface
        self.port = int(port)
        self.group = group
        self.hops = int(hops)
        # Loopback of our own frames is harmless (TeamLink drops its own id)
        # and is what lets two links on one host talk in tests.
        self.loop = bool(loop)
        self._ifindex = 0

    def open(self) -> None:
        if not socket.has_ipv6:
            raise OSError("IPv6 not available")
        group_bin = socket.inet_pton(socket.AF_INET6, self.group)
        if group_bin[0] != 0xFF:
            raise ValueError(f"{self.group} is not a multicast address")
        self._ifindex = socket.if_nametoindex(self.interface)   # OSError if absent
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, self._ifindex)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, self.hops)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP, 1 if self.loop else 0)
            sock.bind(("::", self.port))
            mreq = group_bin + struct.pack("@I", self._ifindex)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._rx_ok = True

    def send(self, data: bytes) -> None:
        if self._sock is None:
            raise OSError("transport not open")
        self._sock.sendto(data, (self.group, self.port, 0, self._ifindex))

    def describe(self) -> str:
        return f"ipv6_multicast [{self.group}%{self.interface}]:{self.port}"


class MemoryHub:
    """In-process broadcast medium: every frame sent by one attached
    transport is queued to all of them (including the sender, like a real
    broadcast socket). ``drop`` lets a test lose frames deterministically."""

    def __init__(self):
        self._lock = threading.Lock()
        self._members: List["MemoryTransport"] = []
        self.sent: List[bytes] = []        # every frame, for tamper/replay tests
        self.drop = None                   # optional callable(data) -> bool

    def transport(self, addr: str) -> "MemoryTransport":
        return MemoryTransport(self, addr)

    def _attach(self, t: "MemoryTransport") -> None:
        with self._lock:
            if t not in self._members:
                self._members.append(t)

    def _detach(self, t: "MemoryTransport") -> None:
        with self._lock:
            if t in self._members:
                self._members.remove(t)

    def deliver(self, data: bytes, src: str) -> None:
        """Inject a frame as if ``src`` had sent it (attacker / replay tests)."""
        with self._lock:
            members = list(self._members)
        for m in members:
            m._q.put((bytes(data), src))

    def _send(self, data: bytes, src: str) -> None:
        with self._lock:
            self.sent.append(bytes(data))
        if self.drop is not None and self.drop(data):
            return
        self.deliver(data, src)


class MemoryTransport(TeamTransport):
    name = "memory"

    def __init__(self, hub: MemoryHub, addr: str):
        self.hub = hub
        self.addr = addr
        self._q = queue.Queue()  # type: queue.Queue
        self._open = False

    def open(self) -> None:
        self._open = True
        self.hub._attach(self)

    def send(self, data: bytes) -> None:
        if not self._open:
            raise OSError("transport not open")
        self.hub._send(data, self.addr)

    def recv(self, timeout: float) -> Optional[Tuple[bytes, str]]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._open = False
        self.hub._detach(self)

    def describe(self) -> str:
        return f"memory ({self.addr})"
