#!/usr/bin/env python3
"""One GCS-connected fixed-wing sim drone = one process, one MQTT connection.

This is the bridge that lets the Godot fixed-wing simulator take part in the
Ground Control Station control plane as an ordinary aircraft. It marries two
things that already exist in this repo:

  * sim_drone_daemon.py's GCS mosquitto connection (username/password, plain
    TCP, the same drone/{id}/... topics a real drone uses), and
  * fw_swarm_demo.py's fixed-wing brain (a real MissionLoop over a SimBackend
    driving the Godot fixed-wing model, with on-device Qwen3-VL perception).

To the GCS this process is indistinguishable from a real AGX aircraft: it
heartbeats as VLM-capable, receives phased missions on
drone/{id}/chat/{conv}/command, runs them, and publishes mission_progress
messages on .../progress and the final result on .../response — exactly the
wire shapes control/conversations.py and the iOS app already speak. That means
the operator storyboard (plan -> select -> live status -> summary) drives this
sim fleet with zero storyboard-specific code on the drone side.

Godot must already be running (see run_fw_gcs_demo.py) with its IPC reachable on
--godot-port; this daemon connects a DepotClient to it and spawns its aircraft.

Heavy imports (the VLM stack, the Godot client, paho) are deferred into start()
so the pure message/geometry helpers below stay importable and unit-testable
with nothing installed.

Usage:
    python3 fw_gcs_daemon.py --drone-id alpha \
        --godot-port 9978 --home-enu -20,10 --spawn-alt 30 \
        --mqtt-host airlink-admin --mqtt-port 1883 \
        --mqtt-username alpha --mqtt-password <pairing-token> \
        --target-label "pickup truck" --datum-lat 37.4 --datum-lon -122.1
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parent
# Same path wiring fw_swarm_demo.py uses: sim dir, rover/sim (DepotClient),
# drone/common (reasoning_loop et al.). parents[1] is the eco submodule root.
for p in (SIM_DIR, SIM_DIR.parents[1] / "rover" / "sim", SIM_DIR.parent / "common"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

EARTH_RADIUS_M = 6_371_000.0


# --- pure helpers (unit-testable, no heavy deps) -----------------------------

def enu_to_latlon(datum, east_m, north_m):
    """Equirectangular ENU->lat/lon about a datum {'lat','lon'} at ENU origin.
    Matches control/planning.offset_to_coord so the coordinates the operator
    sees on the map agree with the frame the planner reasoned in."""
    lat = datum["lat"] + math.degrees(north_m / EARTH_RADIUS_M)
    lon = datum["lon"] + math.degrees(
        east_m / (EARTH_RADIUS_M * math.cos(math.radians(datum["lat"]))))
    return {"lat": lat, "lon": lon}


def extract_target_location(memory, target_labels, datum=None):
    """Pull the localized target out of a finished mission's spatial memory.

    `memory` is a SpatialMemory (duck-typed: .nearest(label) -> Landmark|None
    with .x=east, .y=north, .z, .score). Returns the first configured label
    that was actually localized as {label, east_m, north_m, score[, lat, lon]},
    or None if the target was never seen. ENU is always included (it's what the
    sim knows); lat/lon is added only when a datum is given."""
    for label in target_labels:
        lm = memory.nearest(label)
        if lm is None:
            continue
        out = {"label": label, "east_m": round(lm.x, 1), "north_m": round(lm.y, 1),
               "score": round(getattr(lm, "score", 0.0), 3)}
        if datum:
            out.update(enu_to_latlon(datum, lm.x, lm.y))
        return out
    return None


def build_progress_payload(drone_id, conv, mission, text):
    """The mission_progress wire shape the iOS app renders (see
    reasoning_loop._report_progress / ChatMessage.MissionProgress)."""
    phase = mission.current_phase
    total = len(mission.phases)
    cur = mission.get_current_phase() or {}
    objective = cur.get("objective") or cur.get("description") or cur.get("type") or ""
    return {
        "droneId": drone_id,
        "message_type": "mission_progress",
        "mission_id": mission.mission_id,
        "conversation_id": conv,
        "phase": phase,
        "total_phases": total,
        "objective": objective,
        "status": "in_progress",
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_response_payload(drone_id, conv, mission_id, result_dict,
                           target_location=None):
    """The mission-completion wire shape control.conversations.response_handler
    consumes. target_location (when present) is embedded in `result` so the
    server-side summary picks it up."""
    result = dict(result_dict)
    result["mission_id"] = mission_id
    if target_location:
        result["target_location"] = target_location
    return {
        "droneId": drone_id,
        "conversation_id": conv,
        "mission_id": mission_id,
        "result": result,
        "image_urls": result.get("photos", []) or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def parse_enu(s):
    """'-20,10' -> (-20.0, 10.0) east,north."""
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected 'east,north'")
    return parts[0], parts[1]


def _label_matches(detected: str, targets) -> bool:
    d = (detected or "").lower()
    return any(t.lower() in d or d in t.lower() for t in targets if t)


class OracleBrain:
    """A deterministic, VLM-free executor for the fixed-wing sim, used where the
    on-device Qwen3-VL isn't available (e.g. a laptop with no GPU/model).

    It flies the mission SEQUENCE the planner produced — takeoff, transit to the
    search area, sweep until the sim's ground-truth detector reports the target,
    localize it, return, land — using the sim's own `detect()` oracle for
    perception instead of a real vision model. It reuses SimBackend + SpatialMemory
    so the daemon's target-extraction and reporting are byte-identical to the VLM
    path; only the decision of WHERE to look is scripted rather than learned.

    This is the honest "sim oracle" perception mode — good enough to exercise the
    whole iOS -> GCS -> drone pipeline end to end without the GPU box."""

    def __init__(self, backend, client, agent_id, vehicle_class, target_labels,
                 on_progress):
        self.backend = backend
        self.client = client
        self.id = agent_id
        self.vc = vehicle_class
        self.targets = target_labels
        self.on_progress = on_progress
        from spatial_memory import SpatialMemory
        self.memory = SpatialMemory(
            merge_radius=1.5 * max(1.0, vehicle_class.sense_range_m / 10.0))

    def _p(self, msg):
        if self.on_progress:
            self.on_progress(msg)

    def _search_center(self):
        """Where to look. The oracle is allowed to read scene truth for the
        search-area centre (it's the 'oracle' brain); falls back to a point
        ahead of home if the env doesn't expose one."""
        try:
            env = self.client.fw_env_state()
            c = (env.get("search") or {}).get("center")
            if c:
                return float(c[0]), float(c[1])
        except Exception:
            pass
        return 400.0, 0.0

    def _scan_for_target(self):
        for d in self.backend.detect():
            if _label_matches(d.label, self.targets) and d.world_xyz:
                return d
        return None

    def run(self, mission):
        """Execute `mission.phases` and return a MissionResult-shaped dict."""
        import time as _t
        from search_patterns import expanding_orbit
        phases = mission.phases or []
        total = len(phases)
        done = 0
        found = None
        cruise_alt = 35.0

        for i, ph in enumerate(phases):
            mission.current_phase = i
            ptype = ph.get("type")
            if ptype == "arm_and_takeoff":
                alt = float(ph.get("altitude_m", 30.0))
                cruise_alt = max(alt, 30.0)
                self._p("Arming and taking off")
                self.backend.takeoff(alt)
            elif ptype == "land":
                self._p("Landing")
                self.backend.land(ph.get("heading_deg"))
            elif ptype == "return_home":
                self._p("Returning to base")
                self.backend.rtl(ph.get("alt_m"))
            else:
                # Any transit / search / objective phase: fly to the search
                # area and sweep until the target is seen.
                cx, cy = self._search_center()
                self._p(f"Transiting to search area ({cx:.0f}, {cy:.0f})")
                self.backend.goto(north_m=cy, east_m=cx - 120.0, alt_m=cruise_alt)
                if found is None:
                    self._p("On station — searching for the target")
                    waypoints = expanding_orbit((cx, cy), self.vc, laps=3)
                    for (wx, wy) in waypoints:
                        self.backend.goto(north_m=wy, east_m=wx, alt_m=cruise_alt)
                        self.backend.aim_sensor((cx, cy, 0.0))
                        det = self._scan_for_target()
                        if det:
                            x, y, z = det.world_xyz
                            self.memory.pin(det.label, x, y, z, det.score)
                            found = det
                            self._p(f"Target found: {det.label} at "
                                    f"east {x:.0f} m, north {y:.0f} m")
                            break
                    if found is None:
                        self._p("Target not yet located in this pass")
            done += 1
            _t.sleep(0.05)

        summary = ("Located and localized the target." if found
                   else "Completed the search; target not localized.")
        return {
            "success": True,
            "summary": summary,
            "phases_completed": done,
            "total_phases": total,
            "findings": ([f"{found.label} at east {found.world_xyz[0]:.0f} m, "
                          f"north {found.world_xyz[1]:.0f} m"] if found else []),
            "photos": [],
        }


# --- the daemon --------------------------------------------------------------

class FwGcsDaemon:
    def __init__(self, args):
        self.id = args.drone_id
        self.mqtt_host = args.mqtt_host
        self.mqtt_port = args.mqtt_port
        self.mqtt_username = args.mqtt_username or self.id
        self.mqtt_password = args.mqtt_password
        self.godot_host = args.godot_host
        self.godot_port = args.godot_port
        self.home_enu = args.home_enu           # (east, north)
        self.spawn_alt = args.spawn_alt
        self.target_labels = [t.strip() for t in args.target_label.split(",") if t.strip()]
        self.brain = getattr(args, "brain", "vlm")
        self._backend = None
        self.datum = None
        if args.datum_lat is not None and args.datum_lon is not None:
            self.datum = {"lat": args.datum_lat, "lon": args.datum_lon}
        self._q = queue.Queue()
        self._running = True
        self.client = None          # MQTT
        self.brain_client = None    # Godot DepotClient
        self._armed = False

    # -- MQTT plumbing (mirrors sim_drone_daemon.py's GCS path) --------------
    def start(self):
        import paho.mqtt.client as mqtt
        from depot_client import DepotClient

        # Godot IPC + spawn our aircraft.
        self.brain_client = DepotClient(host=self.godot_host, port=self.godot_port)
        self.brain_client.fw_spawn(self.id, (self.home_enu[0], self.home_enu[1],
                                             self.spawn_alt), 0.0)
        try:
            self.brain_client.fw_reset_camera(self.id)  # Godot render-target reuse guard
        except Exception:
            pass

        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=self.id,
                            clean_session=True)
        except (AttributeError, TypeError):
            c = mqtt.Client(client_id=self.id, clean_session=True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 30)
        if self.mqtt_username:
            c.username_pw_set(self.mqtt_username, self.mqtt_password)
        c.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
        self.client = c
        c.loop_start()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        print(f"[{self.id}] fw-gcs daemon up (godot {self.godot_host}:{self.godot_port}, "
              f"broker {self.mqtt_host}:{self.mqtt_port})", flush=True)

    def _on_connect(self, client, userdata, flags, rc, *a):
        if rc == 0:
            client.subscribe([(f"drone/{self.id}/chat/+/command", 1),
                              (f"drone/{self.id}/command", 1)])
            print(f"[{self.id}] subscribed to command topics", flush=True)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        parts = msg.topic.split("/")
        conv = parts[3] if len(parts) >= 5 and parts[2] == "chat" \
            else data.get("conversation_id", "c")
        if data.get("action") == "mission":
            self._q.put((conv, data.get("mission_id", "m"),
                         data.get("phases", []), data.get("original_message", "")))
            print(f"[{self.id}] queued mission {data.get('mission_id')} "
                  f"({len(data.get('phases', []))} phases) on conv {conv}", flush=True)

    def _worker(self):
        # One mission at a time — a fixed-wing can't fly two plans at once.
        while self._running:
            try:
                conv, mission_id, phases, original = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.run_mission(conv, mission_id, phases, original)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                self._publish(f"drone/{self.id}/chat/{conv}/response",
                              build_response_payload(self.id, conv, mission_id, {
                                  "success": False, "summary": "Mission crashed",
                                  "phases_completed": 0, "total_phases": len(phases),
                                  "failure_reason": repr(exc)}))

    def run_mission(self, conv, mission_id, phases, original):
        from guarded_backend import EnvelopeGuardedSimBackend
        from reasoning_loop import Mission, MissionLoop
        from vehicle_class import get_class

        # Reset the aircraft to a clean home state before EVERY mission. Without
        # this the 2nd/3rd mission misbehaves: after a landing the fixed-wing is
        # stopped at ~2 m altitude, so the next arm_and_takeoff "does not report
        # reaching altitude" and burns its replans. Re-spawning at home makes
        # every mission start exactly like the first.
        try:
            self.brain_client.fw_spawn(self.id, (self.home_enu[0], self.home_enu[1],
                                                 self.spawn_alt), 0.0)
            self.brain_client.fw_reset_camera(self.id)
        except Exception:
            pass

        phases = self._anchor_phases_to_sim(phases)
        mission = Mission(mission_id=mission_id, phases=phases,
                          conversation_id=conv, original_message=original)
        # ONE persistent guarded backend per drone, reused across missions. Its
        # watchdog loiters (circles) the aircraft when no mission is driving it —
        # a fixed-wing can't hover (min airspeed 12 m/s), so without this it flies
        # straight off the map forever between missions (observed at east<-9000).
        # Creating a fresh backend per mission would stack multiple watchdogs on
        # one aircraft, so it's built once here and kept.
        if self._backend is None:
            self._backend = EnvelopeGuardedSimBackend(self.brain_client, self.id)
            self._backend.hold_when_idle = True
        backend = self._backend
        vc = get_class("fixedwing")
        self._armed = True
        if self.brain == "oracle":
            brain = OracleBrain(backend, self.brain_client, self.id, vc,
                                self.target_labels,
                                on_progress=lambda m: self._on_progress(conv, mission, m))
            result = brain.run(mission)
            memory = brain.memory
        else:
            loop = MissionLoop(backend=backend, vehicle_class=vc,
                               conversation_id=conv,
                               on_progress=lambda m: self._on_progress(conv, mission, m))
            result = loop.run(mission)
            memory = loop.memory
        self._armed = False

        result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        target = extract_target_location(memory, self.target_labels, self.datum)
        if target:
            print(f"[{self.id}] target localized: {target}", flush=True)
        self._publish(f"drone/{self.id}/chat/{conv}/response",
                      build_response_payload(self.id, conv, mission_id,
                                             result_dict, target))
        print(f"[{self.id}] mission {mission_id} done "
              f"(success={result_dict.get('success')})", flush=True)

    def _anchor_phases_to_sim(self, phases):
        """Bridge the operator's lat/lon tasking to the sim's LOCAL ENU frame.

        The operator draws the operating area on a real-world lat/lon map, but
        the Godot world is a self-contained ENU sandbox whose search area /
        target live at fixed local metres. Those two frames are unrelated, so a
        search objective phrased only as "the area" gives the aircraft nothing
        to fly toward. Here we read the sim's own search-area centre (scene
        truth — legitimate: this is the sim bridge, not a perception shortcut)
        and make every VLM/objective phase name it explicitly, in the exact
        local-frame wording MISSION_SYSTEM_PROMPT tells the model to honour.
        This is what makes the drones converge on the target reliably every run.

        In a real deployment there is no bridge: the operating area IS real-world
        GPS and the aircraft flies there directly — this only fires in the sim.
        """
        try:
            env = self.brain_client.fw_env_state()
            center = (env.get("search") or {}).get("center")
            radius = (env.get("search") or {}).get("radius", 120)
        except Exception:
            center = None
        if not center:
            return phases
        cx, cy = float(center[0]), float(center[1])
        anchored = []
        for ph in phases:
            ph = dict(ph)
            is_objective = not ph.get("type") and ph.get("objective")
            if is_objective and "east=" not in ph["objective"]:
                ph["objective"] = (
                    f"{ph['objective'].rstrip('.')}, around local coordinates "
                    f"east={cx:.0f}, north={cy:.0f} (metres, local frame), "
                    f"within {radius:.0f} m of there")
            anchored.append(ph)
        return anchored

    def _on_progress(self, conv, mission, text):
        try:
            print(f"  [{self.id}] {text}", flush=True)
        except Exception:
            pass
        self._publish(f"drone/{self.id}/chat/{conv}/progress",
                      build_progress_payload(self.id, conv, mission, text))

    def _heartbeat_loop(self):
        while self._running:
            self._publish(f"drone/{self.id}/status", {
                "droneId": self.id, "status": "online",
                "lastUpdate": int(time.time() * 1000), "ttl": int(time.time()) + 30,
                "armed": self._armed, "battery": 100, "variant": "agx64",
                "capabilities": {"variant": "agx64", "vlm_available": True,
                                 "nav2_available": True, "vehicle_type": "fixedwing"},
            }, qos=0)
            time.sleep(5)

    def _publish(self, topic, payload, qos=1):
        if self.client is None:
            return
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def stop(self):
        self._running = False
        if self.client:
            self.client.loop_stop()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drone-id", required=True)
    ap.add_argument("--godot-host", default="127.0.0.1")
    ap.add_argument("--godot-port", type=int, default=9978)
    ap.add_argument("--home-enu", type=parse_enu, default=(-20.0, 10.0),
                    help="spawn position east,north in metres (default -20,10)")
    ap.add_argument("--spawn-alt", type=float, default=30.0)
    ap.add_argument("--mqtt-host", required=True)
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-username", default=None)
    ap.add_argument("--mqtt-password", default=None)
    ap.add_argument("--target-label", default="pickup truck",
                    help="comma-separated detector labels to report as the target")
    ap.add_argument("--brain", default="vlm", choices=["vlm", "oracle"],
                    help="vlm = real on-device Qwen3-VL (needs the model); "
                         "oracle = deterministic sim-truth executor (no VLM)")
    ap.add_argument("--datum-lat", type=float, default=None,
                    help="lat of ENU origin, to report the target in lat/lon too")
    ap.add_argument("--datum-lon", type=float, default=None)
    args = ap.parse_args()

    daemon = FwGcsDaemon(args)
    daemon.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
