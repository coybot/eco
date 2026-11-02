"""Peer-to-peer team link — authenticated datagrams for IRL multi-vehicle coordination.

Replaces cloud MQTT for vehicle-to-vehicle state sharing when operating in
comm-denied / cloud-unreachable conditions. Needs no server: frames go to every
teammate on the link, over either

  * IPv4 UDP broadcast on a shared LAN (TEAM_PORT 5760) — the original
    transport, needs an AP or an IPv4 address on the link; or
  * IPv6 link-local multicast on a named interface — works on a bare 802.11s
    mesh / IBSS link with no AP, DHCP or address config
    (``drone/platforms/orin/mesh_setup.sh``, ``MESH.md``).

Every frame is HMAC-SHA256 signed with a pre-shared team key read from a key
file (see TeamKeyring). Receivers silently drop — and count — frames that are
malformed, unsigned, signed with an unknown key, tampered, replayed, or carry a
timestamp outside the clock-skew window. Unsigned operation exists only for
bench testing, behind the explicit ``insecure_allow_unsigned=True``.

Frames are small (<500 bytes, MAX_FRAME_BYTES) and lossy-tolerant; the
coordinator treats them as "last-known" state, not a reliable stream.

Wire format (version 2, network byte order):

  off  size  field
  0    2     magic  b"TL"
  2    1     version 2                (a frame starting with "{" is v1 bare JSON)
  3    1     flags  bit0 = signed
  4    1     key_id 0-255             (selects the key; covered by the MAC)
  5    4     epoch  uint32            sender session, unix seconds at link start
  9    4     seq    uint32            per-session counter, +1 per frame
  13   8     ts     float64           sender wall clock, unix seconds
  21   1     id_len
  22   n     sender drone_id, UTF-8
  22+n m     body   canonical JSON (sorted keys, no whitespace)
  end-32 32  tag    HMAC-SHA256(key, every byte before the tag) — signed only

Replay rejection: per sender, a frame is accepted once per (epoch, seq). A
higher epoch starts a new session (sender restarted); a lower one is stale.
Within a session a 64-frame sliding window (IPsec-style) tolerates
reordering and rejects duplicates. The timestamp window bounds how long a
recorded frame stays replayable to a receiver that has never heard the
sender; it needs vehicle clocks within ``max_clock_skew_s`` (GPS / chrony).
Set it to None on clockless benches — epoch+seq still reject replays.

Message types (mirrors sim comms.py MsgType):
  POSITION   — pos_enu [x,y,z], yaw_deg, vel [vx,vy,vz], confidence,
               battery_pct, rtl, vclass (optional)
  TASK_CLAIM — claim_id
  TASK_DONE  — claim_id
  SIGHTING   — target_id, pos_enu [x,y,z], stamp
  BATTERY    — pct, voltage, rtl=bool
  HEARTBEAT  — alive

Each vehicle broadcasts POSITION every 1/BCAST_HZ. Received messages are
dicts as before ("type", payload fields, "from", "t", "_addr") plus "_seq"
and "_auth" (True when the HMAC verified).

Python 3.8, stdlib only: this ships to the aircraft.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import stat
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from .team_link_transport import (
        TEAM_PORT, BCAST_ADDR, MCAST6_GROUP, TeamTransport, UdpBroadcastTransport,
        Ipv6MulticastTransport, MemoryHub, MemoryTransport,
    )
except ImportError:  # pragma: no cover - device flat-install path
    from team_link_transport import (
        TEAM_PORT, BCAST_ADDR, MCAST6_GROUP, TeamTransport, UdpBroadcastTransport,
        Ipv6MulticastTransport, MemoryHub, MemoryTransport,
    )

logger = logging.getLogger(__name__)

BCAST_HZ = 2.0          # position broadcasts per second
MAX_PEER_AGE_S = 5.0    # drop peer if silent this long
MAX_FRAME_BYTES = 500   # TX refuses, RX drops anything larger
MAX_CLOCK_SKEW_S = 30.0  # default timestamp window (+/-); None disables
REPLAY_WINDOW = 64      # sliding-window size; also the recent-loss window
MIN_KEY_BYTES = 16      # 32 recommended
MAX_ID_BYTES = 64
REOPEN_S = 5.0          # retry a transport that failed to open (iface not up yet)
WARN_EVERY_S = 60.0     # rate limit for drop warnings
MAX_INBOX = 1024        # oldest messages dropped if nobody drains the inbox

MAGIC = b"TL"
VERSION = 2
FLAG_SIGNED = 0x01
TAG_LEN = 32
_HDR = struct.Struct("!2sBBBIIdB")
_U32 = 0xFFFFFFFF

COUNTER_NAMES = (
    "tx_frames", "tx_err", "tx_oversize",
    "rx_frames", "rx_ok", "rx_own",
    "drop_oversize", "drop_malformed", "drop_bad_magic", "drop_bad_version",
    "drop_unsigned", "drop_unknown_key", "drop_bad_mac", "drop_bad_body",
    "drop_stale_ts", "drop_replay", "drop_stale_epoch", "inbox_overflow",
)


class TeamLinkConfigError(ValueError):
    """Bad key file / config. Messages never contain key material."""


class FrameError(ValueError):
    """A received datagram was rejected. ``reason`` names the drop counter."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- keys
class TeamKeyring:
    """Pre-shared team keys, by key id. Never logs or reprs key bytes.

    Key file: one key per line, ``<key_id> <hex secret>``; ``#`` comments and
    blank lines ignored; key_id 0-255; secret >= 16 bytes (32 recommended).
    The first listed key signs (override with ``tx_key_id``); every listed key
    verifies. Rotation: add the new key on every vehicle (accepted), then move
    it to the first line (signing), then delete the old line.

      python3 -c "import secrets; print(1, secrets.token_hex(32))" > team.key
      chmod 600 team.key
    """

    def __init__(self, keys: Dict[int, bytes], tx_key_id: Optional[int] = None):
        if not keys:
            raise TeamLinkConfigError("team keyring is empty")
        for kid, key in keys.items():
            if not isinstance(kid, int) or not 0 <= kid <= 255:
                raise TeamLinkConfigError(f"key id {kid!r} must be an int 0-255")
            if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY_BYTES:
                raise TeamLinkConfigError(
                    f"key id {kid}: secret must be >= {MIN_KEY_BYTES} bytes")
        self._keys = {int(k): bytes(v) for k, v in keys.items()}
        if tx_key_id is None:
            tx_key_id = next(iter(self._keys))
        if tx_key_id not in self._keys:
            raise TeamLinkConfigError(f"tx_key_id {tx_key_id} is not in the keyring")
        self._tx = int(tx_key_id)

    @classmethod
    def parse(cls, text: str, tx_key_id: Optional[int] = None,
              source: str = "<keyring>") -> "TeamKeyring":
        keys = {}  # type: Dict[int, bytes]
        for n, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                raise TeamLinkConfigError(f"{source}:{n}: expected '<key_id> <hex secret>'")
            try:
                kid = int(parts[0])
            except ValueError:
                raise TeamLinkConfigError(f"{source}:{n}: key id is not an integer") from None
            try:
                secret = bytes.fromhex(parts[1])
            except ValueError:
                raise TeamLinkConfigError(f"{source}:{n}: secret is not hex") from None
            if kid in keys:
                raise TeamLinkConfigError(f"{source}:{n}: duplicate key id {kid}")
            if len(secret) < MIN_KEY_BYTES:
                raise TeamLinkConfigError(
                    f"{source}:{n}: key id {kid} secret is {len(secret)} bytes, "
                    f"need >= {MIN_KEY_BYTES}")
            keys[kid] = secret
        return cls(keys, tx_key_id)

    @classmethod
    def from_file(cls, path: str, tx_key_id: Optional[int] = None) -> "TeamKeyring":
        try:
            st = os.stat(path)
            with open(path, "r") as fh:
                text = fh.read()
        except OSError as e:
            raise TeamLinkConfigError(f"cannot read team key file {path}: {e.strerror}") from None
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            logger.warning(f"team key file {path} is accessible by group/others — chmod 600 it")
        return cls.parse(text, tx_key_id, source=path)

    def get(self, key_id: int) -> Optional[bytes]:
        return self._keys.get(key_id)

    @property
    def key_ids(self) -> List[int]:
        return list(self._keys)

    @property
    def tx_key_id(self) -> int:
        return self._tx

    @property
    def tx_key(self) -> bytes:
        return self._keys[self._tx]

    def __repr__(self) -> str:
        return f"TeamKeyring(key_ids={self.key_ids}, tx_key_id={self._tx})"


# --------------------------------------------------------------------------- codec
@dataclass
class Frame:
    sender: str
    epoch: int
    seq: Optional[int]       # None for a legacy v1 frame
    ts: Optional[float]      # sender wall clock; None for legacy
    key_id: Optional[int]
    signed: bool             # True only when the HMAC verified
    body: Dict[str, Any]
    legacy: bool = False


def canonical_body(body: Dict[str, Any]) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, ASCII only."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def encode_frame(body: Dict[str, Any], sender: str, epoch: int, seq: int, ts: float,
                 key: Optional[bytes] = None, key_id: int = 0) -> bytes:
    """Build one wire frame. Signed iff ``key`` is given."""
    sid = sender.encode("utf-8")
    if not 0 < len(sid) <= MAX_ID_BYTES:
        raise ValueError(f"drone_id must be 1-{MAX_ID_BYTES} UTF-8 bytes")
    flags = FLAG_SIGNED if key is not None else 0
    head = _HDR.pack(MAGIC, VERSION, flags, key_id & 0xFF, epoch & _U32, seq & _U32,
                     float(ts), len(sid))
    msg = head + sid + canonical_body(body)
    if key is None:
        return msg
    return msg + hmac.new(key, msg, hashlib.sha256).digest()


def _decode_legacy(data: bytes) -> Frame:
    try:
        body = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FrameError("bad_body") from None
    if not isinstance(body, dict) or not isinstance(body.get("from"), str) or not body["from"]:
        raise FrameError("bad_body")
    sender = body.pop("from")
    return Frame(sender=sender, epoch=0, seq=None, ts=None, key_id=None,
                 signed=False, body=body, legacy=True)


def decode_frame(data: bytes, keyring: Optional[TeamKeyring],
                 allow_unsigned: bool = False) -> Frame:
    """Parse and authenticate one datagram; raise FrameError(reason) to drop it.

    The body is only JSON-parsed after the MAC has verified. A frame that
    claims a signature must verify even when unsigned frames are allowed.
    """
    if len(data) > MAX_FRAME_BYTES:
        raise FrameError("oversize")
    if data[:1] == b"{":
        if not allow_unsigned:
            raise FrameError("unsigned")
        return _decode_legacy(data)
    if len(data) < _HDR.size:
        raise FrameError("malformed")
    magic, ver, flags, key_id, epoch, seq, ts, id_len = _HDR.unpack_from(data)
    if magic != MAGIC:
        raise FrameError("bad_magic")
    if ver != VERSION:
        raise FrameError("bad_version")
    signed = bool(flags & FLAG_SIGNED)
    body_start = _HDR.size + id_len
    end = len(data) - (TAG_LEN if signed else 0)
    if id_len == 0 or id_len > MAX_ID_BYTES or end <= body_start:
        raise FrameError("malformed")
    if signed:
        key = keyring.get(key_id) if keyring is not None else None
        if key is None:
            raise FrameError("unknown_key")
        want = hmac.new(key, data[:end], hashlib.sha256).digest()
        if not hmac.compare_digest(want, data[end:]):
            raise FrameError("bad_mac")
    elif not allow_unsigned:
        raise FrameError("unsigned")
    try:
        sender = data[_HDR.size:body_start].decode("utf-8")
        body = json.loads(data[body_start:end].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FrameError("bad_body") from None
    if not isinstance(body, dict) or not isinstance(body.get("type"), str):
        raise FrameError("bad_body")
    return Frame(sender=sender, epoch=epoch, seq=seq, ts=ts,
                 key_id=key_id if signed else None, signed=signed, body=body)


# --------------------------------------------------------------------------- per-sender state
class _SenderState:
    """Replay window + link statistics for one authenticated sender."""

    __slots__ = ("epoch", "top", "bitmap", "first_seq", "received", "last_heard",
                 "addr", "key_id", "signed", "last_warn")

    def __init__(self):
        self.epoch = -1
        self.top = -1
        self.bitmap = 0          # bit i set => seq (top - i) accepted
        self.first_seq = 0
        self.received = 0
        self.last_heard = 0.0
        self.addr = ""
        self.key_id = None       # type: Optional[int]
        self.signed = False
        self.last_warn = -1e9

    def check(self, epoch: int, seq: int) -> str:
        """'ok', 'replay' or 'stale_epoch' — does not modify state."""
        if epoch < self.epoch:
            return "stale_epoch"
        if epoch > self.epoch or seq > self.top:
            return "ok"
        back = self.top - seq
        if back >= REPLAY_WINDOW or (self.bitmap >> back) & 1:
            return "replay"
        return "ok"

    def commit(self, epoch: int, seq: int) -> None:
        if epoch != self.epoch:
            self.epoch, self.top, self.bitmap = epoch, seq, 1
            self.first_seq, self.received = seq, 1
            return
        if seq > self.top:
            shift = seq - self.top
            self.bitmap = ((self.bitmap << shift) | 1) & ((1 << REPLAY_WINDOW) - 1)
            self.top = seq
        else:
            self.bitmap |= 1 << (self.top - seq)
        self.received += 1

    def loss_recent(self) -> Optional[float]:
        span = min(REPLAY_WINDOW, self.top - self.first_seq + 1)
        if span < 2:
            return None
        got = bin(self.bitmap & ((1 << span) - 1)).count("1")
        return 1.0 - got / span

    def loss_total(self) -> Optional[float]:
        span = self.top - self.first_seq + 1
        if span < 2:
            return None
        return max(0.0, 1.0 - self.received / span)


@dataclass
class PeerLinkStats:
    """Link quality for one teammate, for the task allocator.

    Loss comes from sequence gaps, so it only covers frames up to the last one
    heard: a peer that has gone silent shows its old loss — use ``age_s``.
    """
    drone_id: str
    addr: str
    last_heard: float                # local time.monotonic() of the last accepted frame
    age_s: float
    rx_packets: int                  # accepted this session
    loss_recent: Optional[float]     # over the last REPLAY_WINDOW seq numbers; None < 2
    loss_total: Optional[float]      # since this session was first heard
    epoch: int
    last_seq: int
    authenticated: bool
    key_id: Optional[int]
    rssi_dbm: Optional[float] = None  # from rssi_provider; None when unavailable


@dataclass
class PeerState:
    drone_id: str
    pos_enu: list[float]       # [x, y, z] metres (ENU from takeoff origin)
    yaw_deg: float
    vel: list[float]           # [vx, vy, vz] m/s
    confidence: float          # localization confidence 0-1
    stamp: float               # time.monotonic() on THIS vehicle when last heard
    battery_pct: float = 100.0
    rtl: bool = False
    task_claim: str | None = None  # current claimed goal key "x,y,z"
    vclass_name: str | None = None  # "quad" | "rover" | … when the sender says


def _r(v: Any, nd: int) -> float:
    return round(float(v), nd)


# --------------------------------------------------------------------------- link
class TeamLink:
    """Authenticated peer-to-peer team link.

    Usage:
        link = TeamLink(drone_id="drone-001", key_file="/home/jetson/drone-api/certs/team.key")
        # or: TeamLink.from_config("drone-001", cfg["team_link"])
        link.start()
        # update my state each tick:
        link.update_self(pos_enu, yaw_deg, vel, confidence, battery_pct, rtl)
        # read peers:
        peers = link.peers()          # dict[drone_id, PeerState]
        stats = link.link_stats()     # dict[drone_id, PeerLinkStats]
        link.send_task_claim(goal_key)
        link.send_sighting(target_id, pos_enu)
        link.stop()

    Signed by default: construction fails without a key unless
    ``insecure_allow_unsigned=True`` (bench only). With that flag and no key,
    frames go out unsigned; unsigned and legacy v1 JSON frames are accepted;
    signed frames are still verified if a key is configured.
    """

    def __init__(self, drone_id: str, port: int = TEAM_PORT,
                 bcast_hz: float = BCAST_HZ, *,
                 key_file: Optional[str] = None,
                 keyring: Optional[TeamKeyring] = None,
                 tx_key_id: Optional[int] = None,
                 insecure_allow_unsigned: bool = False,
                 transport: Optional[TeamTransport] = None,
                 max_clock_skew_s: Optional[float] = MAX_CLOCK_SKEW_S,
                 epoch_file: Optional[str] = None,
                 rssi_provider: Optional[Callable[[str, str], Optional[float]]] = None,
                 vclass_name: Optional[str] = None):
        sid = drone_id.encode("utf-8") if isinstance(drone_id, str) else b""
        if not 0 < len(sid) <= MAX_ID_BYTES:
            raise TeamLinkConfigError(f"drone_id must be 1-{MAX_ID_BYTES} UTF-8 bytes")
        if keyring is not None and key_file is not None:
            raise TeamLinkConfigError("pass key_file or keyring, not both")
        if key_file is not None:
            keyring = TeamKeyring.from_file(key_file, tx_key_id)
        elif keyring is not None and tx_key_id is not None:
            keyring = TeamKeyring({k: keyring.get(k) for k in keyring.key_ids}, tx_key_id)
        if keyring is None and not insecure_allow_unsigned:
            raise TeamLinkConfigError(
                "TeamLink needs a team key (key_file / team_link.key_file); "
                "unsigned operation requires insecure_allow_unsigned=True")
        self.drone_id = drone_id
        self.port = port
        self._period = 1.0 / bcast_hz
        self._keyring = keyring
        self.insecure_allow_unsigned = bool(insecure_allow_unsigned)
        self.max_clock_skew_s = max_clock_skew_s
        self._transport = transport if transport is not None else UdpBroadcastTransport(port)
        self._epoch_file = epoch_file
        self._rssi_provider = rssi_provider
        self.vclass_name = vclass_name

        self._peers: dict[str, PeerState] = {}
        self._senders = {}  # type: Dict[str, _SenderState]
        self._counters = dict.fromkeys(COUNTER_NAMES, 0)
        self._warned = {}  # type: Dict[str, float]
        self._lock = threading.Lock()
        self._inbox: list[dict] = []
        self._my_state: dict[str, Any] = {
            "pos_enu": [0.0, 0.0, 0.0], "yaw_deg": 0.0,
            "vel": [0.0, 0.0, 0.0], "confidence": 1.0,
            "battery_pct": 100.0, "rtl": False,
        }
        self._epoch = int(time.time()) & _U32
        self._seq = 0
        self._transport_up = False
        self._running = False
        self._stop_evt = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None

    @classmethod
    def from_config(cls, drone_id: str, cfg: Optional[Dict[str, Any]],
                    base_dir: Optional[str] = None) -> "TeamLink":
        """Build from the ``team_link:`` section of config.yaml.

        Keys (all optional except key_file, unless insecure_allow_unsigned):
          transport: udp_broadcast | ipv6_multicast   (default udp_broadcast)
          port: 5760            bcast_addr: 255.255.255.255   bcast_hz: 2.0
          interface: mesh0      group: ff02::7e4d:5760        (ipv6_multicast)
          key_file: certs/team.key   tx_key_id: <int>
          max_clock_skew_s: 30  (null disables the timestamp window)
          epoch_file: team_link.epoch
          rssi: iw              (signal from `iw station dump` on ``interface``)
          vclass: quad
          insecure_allow_unsigned: false
        Relative paths resolve against ``base_dir`` (default: cwd).
        """
        cfg = dict(cfg or {})
        known = {"transport", "port", "bcast_addr", "bcast_hz", "interface", "group",
                 "key_file", "tx_key_id", "max_clock_skew_s", "epoch_file", "rssi",
                 "vclass", "insecure_allow_unsigned", "enabled"}
        unknown = sorted(set(cfg) - known)
        if unknown:
            logger.warning(f"team_link config: ignoring unknown keys {unknown}")

        def _path(p):
            if p is None or os.path.isabs(str(p)) or base_dir is None:
                return p
            return os.path.join(base_dir, str(p))

        port = int(cfg.get("port", TEAM_PORT))
        kind = str(cfg.get("transport", "udp_broadcast"))
        iface = cfg.get("interface")
        if kind == "udp_broadcast":
            transport = UdpBroadcastTransport(port, bcast_addr=str(cfg.get("bcast_addr", BCAST_ADDR)))
        elif kind == "ipv6_multicast":
            if not iface:
                raise TeamLinkConfigError("team_link.transport ipv6_multicast needs team_link.interface")
            transport = Ipv6MulticastTransport(str(iface), port, group=str(cfg.get("group", MCAST6_GROUP)))
        else:
            raise TeamLinkConfigError(f"team_link.transport {kind!r}: use udp_broadcast or ipv6_multicast")

        rssi_provider = None
        if cfg.get("rssi") == "iw":
            if not iface:
                raise TeamLinkConfigError("team_link.rssi iw needs team_link.interface")
            try:
                from .team_link_rssi import IwStationRssi
            except ImportError:  # pragma: no cover - device flat-install path
                from team_link_rssi import IwStationRssi
            rssi_provider = IwStationRssi(str(iface))

        tx_key_id = cfg.get("tx_key_id")
        return cls(
            drone_id, port, float(cfg.get("bcast_hz", BCAST_HZ)),
            key_file=_path(cfg.get("key_file")),
            tx_key_id=int(tx_key_id) if tx_key_id is not None else None,
            insecure_allow_unsigned=bool(cfg.get("insecure_allow_unsigned", False)),
            transport=transport,
            max_clock_skew_s=(None if cfg.get("max_clock_skew_s", MAX_CLOCK_SKEW_S) is None
                              else float(cfg.get("max_clock_skew_s", MAX_CLOCK_SKEW_S))),
            epoch_file=_path(cfg.get("epoch_file")),
            rssi_provider=rssi_provider,
            vclass_name=cfg.get("vclass"),
        )

    # ------------------------------------------------------------------ public API
    @property
    def signed(self) -> bool:
        """True when outgoing frames are signed."""
        return self._keyring is not None

    @property
    def transport(self) -> TeamTransport:
        return self._transport

    @property
    def transport_up(self) -> bool:
        return self._transport_up

    def start(self):
        self._epoch = self._next_epoch()
        self._seq = 0
        self._try_open()
        self._running = True
        self._stop_evt.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="team-rx")
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True, name="team-tx")
        self._rx_thread.start()
        self._tx_thread.start()
        if self._keyring is None:
            logger.warning("TeamLink running UNSIGNED (insecure_allow_unsigned) — bench use only")
        logger.info(f"TeamLink started (id={self.drone_id} {self._transport.describe()} "
                    f"signed={self.signed} epoch={self._epoch})")

    def stop(self):
        self._running = False
        self._stop_evt.set()
        self._transport_up = False
        try:
            self._transport.close()
        except Exception as e:
            logger.debug(f"TeamLink close error: {e}")
        for t in (self._tx_thread, self._rx_thread):
            if t is not None and t is not threading.current_thread():
                t.join(timeout=2.0)

    def update_self(self, pos_enu: list[float], yaw_deg: float,
                    vel: list[float] | None = None,
                    confidence: float = 1.0,
                    battery_pct: float = 100.0,
                    rtl: bool = False):
        with self._lock:
            self._my_state.update({
                "pos_enu": list(pos_enu),
                "yaw_deg": float(yaw_deg),
                "vel": list(vel) if vel else [0.0, 0.0, 0.0],
                "confidence": float(confidence),
                "battery_pct": float(battery_pct),
                "rtl": bool(rtl),
            })

    def peers(self) -> dict[str, PeerState]:
        now = time.monotonic()
        with self._lock:
            return {k: v for k, v in self._peers.items()
                    if now - v.stamp < MAX_PEER_AGE_S}

    def link_stats(self, max_age_s: Optional[float] = None) -> Dict[str, PeerLinkStats]:
        """Per-peer link quality. ``max_age_s`` drops peers silent longer than that.

        RSSI comes from ``rssi_provider(drone_id, addr)`` (outside the lock;
        errors read as None)."""
        now = time.monotonic()
        with self._lock:
            out = {}
            for pid, s in self._senders.items():
                age = now - s.last_heard
                if max_age_s is not None and age > max_age_s:
                    continue
                out[pid] = PeerLinkStats(
                    drone_id=pid, addr=s.addr, last_heard=s.last_heard, age_s=age,
                    rx_packets=s.received, loss_recent=s.loss_recent(),
                    loss_total=s.loss_total(), epoch=s.epoch, last_seq=s.top,
                    authenticated=s.signed, key_id=s.key_id,
                )
        if self._rssi_provider is not None:
            for st in out.values():
                try:
                    v = self._rssi_provider(st.drone_id, st.addr)
                    st.rssi_dbm = None if v is None else float(v)
                except Exception as e:
                    logger.debug(f"TeamLink RSSI hook error: {e}")
        return out

    def counters(self) -> Dict[str, int]:
        """TX/RX/drop counters (see COUNTER_NAMES). Drops are otherwise silent."""
        with self._lock:
            return dict(self._counters)

    def send_task_claim(self, goal_key: str):
        self._broadcast({"type": "TASK_CLAIM", "claim_id": goal_key})

    def send_task_done(self, claim_id: str):
        self._broadcast({"type": "TASK_DONE", "claim_id": claim_id})

    def send_sighting(self, target_id: str, pos_enu: list[float]):
        self._broadcast({"type": "SIGHTING", "target_id": target_id,
                         "pos_enu": [_r(v, 3) for v in pos_enu],
                         "stamp": time.monotonic()})

    def send(self, msg: Dict[str, Any]) -> bool:
        """Broadcast any message dict with a string ``type`` (e.g. BATTERY,
        HEARTBEAT, or a new allocator type). Receive-side keys (``from``, ``t``,
        ``_*``) are stripped; the header carries sender and time. Returns
        False if it was not sent (link down, too big, not encodable)."""
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            raise ValueError("team_link message needs a string 'type'")
        body = {k: v for k, v in msg.items()
                if k not in ("from", "t") and not str(k).startswith("_")}
        return self._broadcast(body)

    def drain_inbox(self) -> list[dict]:
        with self._lock:
            msgs, self._inbox = self._inbox, []
        return msgs

    def receive_datagram(self, data: bytes, addr: str = "") -> bool:
        """Process one received datagram (the RX thread calls this; custom
        transports and tests may too). Returns True if it was accepted."""
        try:
            return self._receive(data, addr)
        except Exception as e:   # never let one packet kill the RX thread
            self._count("drop_malformed")
            logger.debug(f"TeamLink RX error: {e}")
            return False

    # ------------------------------------------------------------------ internals
    def _count(self, name: str, n: int = 1):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def _warn(self, key: str, text: str):
        now = time.monotonic()
        if now - self._warned.get(key, -1e9) >= WARN_EVERY_S:
            self._warned[key] = now
            logger.warning(text)

    def _next_epoch(self) -> int:
        """Session id: wall-clock seconds, kept strictly increasing across
        restarts via ``epoch_file`` so a clock that steps back after a reboot
        does not make teammates reject us as stale."""
        epoch = int(time.time())
        path = self._epoch_file
        if path:
            try:
                with open(path) as fh:
                    epoch = max(epoch, int(fh.read().strip() or 0) + 1)
            except (OSError, ValueError):
                pass
            try:
                tmp = f"{path}.tmp"
                with open(tmp, "w") as fh:
                    fh.write(f"{epoch}\n")
                os.replace(tmp, path)
            except OSError as e:
                logger.warning(f"TeamLink cannot write epoch file {path}: {e}")
        return epoch & _U32

    def _try_open(self) -> bool:
        try:
            self._transport.open()
            self._transport_up = True
        except Exception as e:
            self._transport_up = False
            self._warn("open", f"TeamLink transport {self._transport.describe()} "
                               f"not up: {e} — retrying every {REOPEN_S:.0f}s")
        return self._transport_up

    def _build_frame(self, payload: Dict[str, Any]) -> bytes:
        with self._lock:
            if self._seq > _U32:
                self._epoch, self._seq = (self._epoch + 1) & _U32, 0
            seq, epoch = self._seq, self._epoch
            self._seq += 1
        kr = self._keyring
        return encode_frame(payload, self.drone_id, epoch, seq, time.time(),
                            key=kr.tx_key if kr is not None else None,
                            key_id=kr.tx_key_id if kr is not None else 0)

    def _broadcast(self, payload: dict) -> bool:
        if not self._transport_up:
            self._count("tx_err")   # don't burn a seq number: peers would count it as loss
            return False
        try:
            data = self._build_frame(payload)
        except Exception as e:
            self._count("tx_err")
            logger.debug(f"TeamLink encode error: {e}")
            return False
        if len(data) > MAX_FRAME_BYTES:
            self._count("tx_oversize")
            self._warn("tx_oversize", f"TeamLink {payload.get('type')} frame is {len(data)} B "
                                      f"> {MAX_FRAME_BYTES} — not sent")
            return False
        try:
            self._transport.send(data)
        except Exception as e:
            self._count("tx_err")
            logger.debug(f"TeamLink TX error: {e}")
            return False
        self._count("tx_frames")
        return True

    def _position_payload(self) -> Dict[str, Any]:
        with self._lock:
            s = dict(self._my_state)
        p = {
            "type": "POSITION",
            "pos_enu": [_r(v, 3) for v in s["pos_enu"]],
            "yaw_deg": _r(s["yaw_deg"], 2),
            "vel": [_r(v, 3) for v in s["vel"]],
            "confidence": _r(s["confidence"], 3),
            "battery_pct": _r(s["battery_pct"], 1),
            "rtl": s["rtl"],
        }
        if self.vclass_name:
            p["vclass"] = self.vclass_name
        return p

    def _tx_loop(self):
        while self._running:
            if self._transport_up:
                self._broadcast(self._position_payload())
            self._stop_evt.wait(self._period)

    def _rx_loop(self):
        last_try = time.monotonic()
        while self._running:
            if not self._transport_up:
                if time.monotonic() - last_try >= REOPEN_S:
                    last_try = time.monotonic()
                    if self._try_open():
                        logger.info(f"TeamLink transport up: {self._transport.describe()}")
                        continue
                self._stop_evt.wait(0.5)
                continue
            try:
                got = self._transport.recv(0.25)   # short: stop() joins this thread
            except OSError as e:
                if self._running:
                    logger.debug(f"TeamLink RX error: {e}")
                    self._stop_evt.wait(0.1)
                continue
            if got is not None:
                self.receive_datagram(got[0], got[1])

    def _receive(self, data: bytes, addr: str) -> bool:
        self._count("rx_frames")
        try:
            frame = decode_frame(data, self._keyring, self.insecure_allow_unsigned)
        except FrameError as e:
            self._count("drop_" + e.reason)
            if e.reason in ("bad_mac", "unknown_key", "unsigned"):
                self._warn("drop_" + e.reason,
                           f"TeamLink dropping {e.reason} frames (from {addr or '?'}) — "
                           f"wrong team key or a forger; see counters()")
            return False
        sender = frame.sender
        if sender == self.drone_id:
            self._count("rx_own")
            return False

        if frame.ts is not None and self.max_clock_skew_s is not None:
            skew = time.time() - frame.ts
            if not math.isfinite(skew) or abs(skew) > self.max_clock_skew_s:
                self._count("drop_stale_ts")
                self._warn("ts:" + sender,
                           f"TeamLink dropping frames from {sender}: clock off by {skew:+.1f}s "
                           f"(window {self.max_clock_skew_s:.0f}s) — check time sync")
                return False

        now = time.monotonic()
        with self._lock:
            st = self._senders.get(sender)
            if st is None:
                st = _SenderState()
            if frame.seq is not None:
                verdict = st.check(frame.epoch, frame.seq)
                if (verdict == "stale_epoch" and self.max_clock_skew_s is not None
                        and now - st.last_heard > self.max_clock_skew_s):
                    # The newer session has been silent longer than the
                    # timestamp window, so none of its frames (or anything
                    # recorded before it) can pass the window any more:
                    # accepting the lower epoch is safe, and it is what lets
                    # a teammate whose clock stepped back rejoin.
                    st = _SenderState()
                    verdict = st.check(frame.epoch, frame.seq)
                if verdict != "ok":
                    self._counters["drop_" + verdict] += 1
                    if verdict == "stale_epoch":
                        self._warn("epoch:" + sender,
                                   f"TeamLink dropping {sender}: session {frame.epoch} older than "
                                   f"{st.epoch} (restarted with its clock behind? set epoch_file)")
                    return False
                st.commit(frame.epoch, frame.seq)
            else:
                st.received += 1
            st.last_heard = now
            st.addr = addr
            st.key_id = frame.key_id
            st.signed = frame.signed
            self._senders[sender] = st
            self._counters["rx_ok"] += 1

        msg = dict(frame.body)
        msg["from"] = sender
        if frame.ts is not None:
            msg["t"] = frame.ts
        msg["_addr"] = addr
        msg["_seq"] = frame.seq
        msg["_auth"] = frame.signed
        self._handle(sender, msg)
        return True

    def _handle(self, sender: str, msg: dict):
        mtype = msg.get("type")
        with self._lock:
            if mtype == "POSITION":
                prev = self._peers.get(sender)
                vc = msg.get("vclass")
                self._peers[sender] = PeerState(
                    drone_id=sender,
                    pos_enu=msg.get("pos_enu", [0, 0, 0]),
                    yaw_deg=msg.get("yaw_deg", 0.0),
                    vel=msg.get("vel", [0, 0, 0]),
                    confidence=msg.get("confidence", 1.0),
                    stamp=time.monotonic(),
                    battery_pct=msg.get("battery_pct", 100.0),
                    rtl=msg.get("rtl", False),
                    task_claim=prev.task_claim if prev is not None else None,
                    vclass_name=vc if isinstance(vc, str) else None,
                )
            elif mtype == "TASK_CLAIM":
                if sender in self._peers:
                    self._peers[sender].task_claim = msg.get("claim_id")
            elif mtype == "TASK_DONE":
                if sender in self._peers:
                    self._peers[sender].task_claim = None
            self._inbox.append(msg)
            if len(self._inbox) > MAX_INBOX:
                del self._inbox[0]
                self._counters["inbox_overflow"] += 1


__all__ = [
    "TEAM_PORT", "BCAST_ADDR", "MCAST6_GROUP", "BCAST_HZ", "MAX_PEER_AGE_S",
    "MAX_FRAME_BYTES", "MAX_CLOCK_SKEW_S", "REPLAY_WINDOW", "COUNTER_NAMES",
    "TeamLink", "PeerState", "PeerLinkStats", "TeamKeyring", "TeamLinkConfigError",
    "Frame", "FrameError", "canonical_body", "encode_frame", "decode_frame",
    "TeamTransport", "UdpBroadcastTransport", "Ipv6MulticastTransport",
    "MemoryHub", "MemoryTransport",
]
