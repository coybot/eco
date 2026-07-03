"""Peer-to-peer team link — UDP broadcast for IRL multi-vehicle coordination.

Replaces cloud MQTT for vehicle-to-vehicle state sharing when operating in
comm-denied / cloud-unreachable conditions.  Works on any shared WiFi LAN
with no pairing, no server, no config beyond port number.

Protocol: JSON datagrams broadcast on TEAM_PORT (5760).
Messages are small (<500 bytes) and lossy-tolerant; the coordinator treats
them as "last-known" state, not a reliable stream.

Message types (mirrors sim comms.py MsgType):
  POSITION   — pos_enu [x,y,z], yaw_deg, vel [vx,vy,vz], confidence, stamp
  TASK_CLAIM — goal [lat,lon,alt] or [x,y,z], claim_id
  TASK_DONE  — claim_id
  SIGHTING   — target_id, pos_enu [x,y,z], stamp
  BATTERY    — pct, voltage, rtl=bool
  HEARTBEAT  — alive

Each vehicle broadcasts POSITION+HEARTBEAT every BCAST_HZ.
On receiving any message the coordinator's inbox is updated.
"""
from __future__ import annotations

import json
import logging
import math
import socket
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)

TEAM_PORT = 5760
BCAST_ADDR = "255.255.255.255"
BCAST_HZ = 2.0          # position broadcasts per second
MAX_PEER_AGE_S = 5.0    # drop peer if silent this long


@dataclass
class PeerState:
    drone_id: str
    pos_enu: list[float]       # [x, y, z] metres (ENU from takeoff origin)
    yaw_deg: float
    vel: list[float]           # [vx, vy, vz] m/s
    confidence: float          # localization confidence 0-1
    stamp: float               # time.monotonic() on sender
    battery_pct: float = 100.0
    rtl: bool = False
    task_claim: str | None = None  # current claimed goal key "x,y,z"


class TeamLink:
    """UDP broadcast peer-to-peer link.

    Usage:
        link = TeamLink(drone_id="drone-001")
        link.start()
        # update my state each tick:
        link.update_self(pos_enu, yaw_deg, vel, confidence, battery_pct, rtl)
        # read peers:
        peers = link.peers()          # dict[drone_id, PeerState]
        link.send_task_claim(goal_key)
        link.send_sighting(target_id, pos_enu)
        link.stop()
    """

    def __init__(self, drone_id: str, port: int = TEAM_PORT,
                 bcast_hz: float = BCAST_HZ):
        self.drone_id = drone_id
        self.port = port
        self._period = 1.0 / bcast_hz
        self._peers: dict[str, PeerState] = {}
        self._lock = threading.Lock()
        self._inbox: list[dict] = []
        self._my_state: dict[str, Any] = {
            "pos_enu": [0.0, 0.0, 0.0], "yaw_deg": 0.0,
            "vel": [0.0, 0.0, 0.0], "confidence": 1.0,
            "battery_pct": 100.0, "rtl": False,
        }
        self._sock: socket.socket | None = None
        self._running = False
        self._rx_thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public API
    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.settimeout(1.0)
        try:
            self._sock.bind(("", self.port))
        except OSError as e:
            logger.warning(f"TeamLink bind failed on {self.port}: {e} — RX disabled")
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="team-rx")
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True, name="team-tx")
        self._rx_thread.start()
        self._tx_thread.start()
        logger.info(f"TeamLink started (id={self.drone_id} port={self.port})")

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()

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

    def send_task_claim(self, goal_key: str):
        self._broadcast({"type": "TASK_CLAIM", "claim_id": goal_key})

    def send_task_done(self, claim_id: str):
        self._broadcast({"type": "TASK_DONE", "claim_id": claim_id})

    def send_sighting(self, target_id: str, pos_enu: list[float]):
        self._broadcast({"type": "SIGHTING", "target_id": target_id,
                         "pos_enu": pos_enu, "stamp": time.monotonic()})

    def drain_inbox(self) -> list[dict]:
        with self._lock:
            msgs, self._inbox = self._inbox, []
        return msgs

    # ------------------------------------------------------------------ internals
    def _broadcast(self, payload: dict):
        payload["from"] = self.drone_id
        payload["t"] = time.monotonic()
        try:
            data = json.dumps(payload).encode()
            self._sock.sendto(data, (BCAST_ADDR, self.port))
        except Exception as e:
            logger.debug(f"TeamLink TX error: {e}")

    def _tx_loop(self):
        while self._running:
            with self._lock:
                s = dict(self._my_state)
            self._broadcast({
                "type": "POSITION",
                "pos_enu": s["pos_enu"],
                "yaw_deg": s["yaw_deg"],
                "vel": s["vel"],
                "confidence": s["confidence"],
                "battery_pct": s["battery_pct"],
                "rtl": s["rtl"],
            })
            time.sleep(self._period)

    def _rx_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
                msg = json.loads(data.decode())
                sender = msg.get("from")
                if not sender or sender == self.drone_id:
                    continue  # ignore own broadcasts
                msg["_addr"] = addr[0]
                self._handle(sender, msg)
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"TeamLink RX error: {e}")

    def _handle(self, sender: str, msg: dict):
        mtype = msg.get("type")
        with self._lock:
            if mtype == "POSITION":
                self._peers[sender] = PeerState(
                    drone_id=sender,
                    pos_enu=msg.get("pos_enu", [0, 0, 0]),
                    yaw_deg=msg.get("yaw_deg", 0.0),
                    vel=msg.get("vel", [0, 0, 0]),
                    confidence=msg.get("confidence", 1.0),
                    stamp=time.monotonic(),
                    battery_pct=msg.get("battery_pct", 100.0),
                    rtl=msg.get("rtl", False),
                    task_claim=self._peers.get(sender, PeerState(
                        sender, [0,0,0], 0, [0,0,0], 1, 0)).task_claim
                    if sender in self._peers else None,
                )
            elif mtype == "TASK_CLAIM":
                if sender in self._peers:
                    self._peers[sender].task_claim = msg.get("claim_id")
            elif mtype == "TASK_DONE":
                if sender in self._peers:
                    self._peers[sender].task_claim = None
            self._inbox.append(msg)
