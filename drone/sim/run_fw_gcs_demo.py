#!/usr/bin/env python3
"""Operator-storyboard demo runner (Part 2 — user/demo scenario).

Brings up the pieces on the SIM WORKSTATION side of the storyboard and points
them at the Orin GCS:

  1. Launches the Godot fixed-wing sim (GUI — required for camera capture) on
     the pickup-truck surveillance environment.
  2. Starts one fixed-wing GCS-bridge daemon per aircraft (alpha, bravo), each
     an ordinary GCS drone connected to the Orin's mosquitto broker.

After this is running, the operator drives everything from the iPad app: draw
the area + no-fly zones, request plans (POST /plan on the GCS), pick one and say
"go" (POST /plan/select), and watch live status + the localized truck + the
summary. Nothing storyboard-specific runs here — this is just the sim fleet.

Both daemons run in ONE process (one shared Qwen3-VL model, serialized by
VLMService's lock, exactly as fw_swarm_demo.py does) on their own threads.

PREREQUISITES (operator does these once):
  * The GCS server + mosquitto are running on the Orin (see gcs/README.md).
  * alpha and bravo are registered to the operator's account on the GCS
    (POST /drones), and their per-drone pairing tokens are passed below as
    --alpha-token / --bravo-token. Registration is what makes verify_ownership
    pass so /plan/select can dispatch to them.

Usage:
    python3 run_fw_gcs_demo.py \
        --mqtt-host airlink-admin --mqtt-port 1883 \
        --alpha-token <alpha-pairing-token> --bravo-token <bravo-pairing-token> \
        --datum-lat 37.4000 --datum-lon -122.1000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

SIM_DIR = Path(__file__).resolve().parent
for p in (SIM_DIR, SIM_DIR.parents[1] / "rover" / "sim", SIM_DIR.parent / "common"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from fw_eval import launch_flightline          # noqa: E402
from fw_gcs_daemon import FwGcsDaemon          # noqa: E402

# Same home pads as env_surveil_truck / env_sar.
DRONES = {
    "alpha": {"home_enu": (-20.0, 10.0)},
    "bravo": {"home_enu": (-20.0, -10.0)},
}


def _daemon_args(drone_id, home_enu, args):
    token = args.alpha_token if drone_id == "alpha" else args.bravo_token
    return SimpleNamespace(
        drone_id=drone_id,
        godot_host="127.0.0.1",
        godot_port=args.godot_port,
        home_enu=home_enu,
        spawn_alt=args.spawn_alt,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_username=drone_id,
        mqtt_password=token,
        target_label=args.target_label,
        datum_lat=args.datum_lat,
        datum_lon=args.datum_lon,
        brain=args.brain,
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt-host", required=True, help="Orin GCS broker host")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--alpha-token", required=True, help="alpha's GCS pairing token")
    ap.add_argument("--bravo-token", required=True, help="bravo's GCS pairing token")
    ap.add_argument("--godot-port", type=int, default=9978)
    ap.add_argument("--env", default="surveil_truck")
    ap.add_argument("--spawn-alt", type=float, default=30.0)
    ap.add_argument("--target-label", default="pickup truck,truck,car")
    ap.add_argument("--brain", default="vlm", choices=["vlm", "oracle"],
                    help="oracle = no-VLM deterministic sim-truth executor")
    ap.add_argument("--gui", default="true", choices=["true", "false"],
                    help="Godot GUI (needed for the VLM camera path; the oracle "
                         "brain uses the ground-truth detector and runs headless)")
    ap.add_argument("--datum-lat", type=float, default=None,
                    help="lat of the ENU origin, so the truck is reported in lat/lon")
    ap.add_argument("--datum-lon", type=float, default=None)
    ap.add_argument("--drones", type=int, default=2, choices=(1, 2))
    args = ap.parse_args()

    names = list(DRONES)[: args.drones]

    print(f"Launching Godot ({args.env}, GUI) on IPC port {args.godot_port}...")
    proc = launch_flightline(seed=0, port=args.godot_port,
                             gui=(args.gui == "true"), env=args.env)

    daemons = []
    try:
        for name in names:
            d = FwGcsDaemon(_daemon_args(name, DRONES[name]["home_enu"], args))
            d.start()
            daemons.append(d)
            time.sleep(1.0)  # stagger spawns/connections

        print(f"\n{len(daemons)} fixed-wing drone(s) live on the GCS "
              f"({args.mqtt_host}:{args.mqtt_port}). Drive the mission from the "
              f"iPad app. Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for d in daemons:
            d.stop()
        proc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
