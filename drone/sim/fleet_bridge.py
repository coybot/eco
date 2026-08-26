#!/usr/bin/env python3
"""Eco sim fleet host: many drones in ONE Isaac world, each IRL-identical.

Runs N vehicles (mixed Crazyflie quads + Nova Carter rovers) in a single Isaac
world, served by one MQTT connection (one fleet cert). Each vehicle is a normal
drone to the cloud/app — same topics, heartbeats, on-demand KVS video. "Sim is
just sim": no coordination here; the cloud + per-drone command logic drive it.

    python3 fleet_bridge.py --fleet quad:10,rover:10 --env office \
        --certs-dir ~/eco-certs --thing-name sim-quadcopter-test

Register the same fleet in the cloud first (on a host with AWS creds):
    python3 fleet.py --fleet quad:10,rover:10 --user-sub <COGNITO_SUB>
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fleet import parse_roster          # noqa: E402
from fleet_worker import FleetWorker     # noqa: E402

try:  # packaged (drone.sim) in the repo; flat (sim/ on path) when run as a script
    from .endpoints import iot_endpoint, credentials_endpoint
except ImportError:
    from endpoints import iot_endpoint, credentials_endpoint

WORKER: FleetWorker | None = None


def _start_mqtt(ids, args):
    from fleet_mqtt import FleetMqtt
    WORKER.ready.wait()
    mq = FleetMqtt(
        WORKER, ids, iot_endpoint=args.iot_endpoint, certs_base=args.certs_base,
        region=args.region, credentials_endpoint=args.credentials_endpoint,
        video_role_alias=args.video_role_alias, s3_role_alias=args.s3_role_alias,
        images_bucket=args.images_bucket)
    try:
        mq.connect()
    except Exception as e:
        print(f"[fleet] mqtt connect failed: {e}", flush=True)


def main():
    global WORKER
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True, help="roster, e.g. quad:10,rover:10")
    ap.add_argument("--env", default="office")
    ap.add_argument("--certs-base", required=True,
                    help="dir containing per-drone cert folders {drone_id}/device.pem,…")
    ap.add_argument("--iot-endpoint",
                    default=iot_endpoint())
    ap.add_argument("--credentials-endpoint",
                    default=credentials_endpoint())
    ap.add_argument("--video-role-alias", default="drone-video-role-alias-dev")
    ap.add_argument("--s3-role-alias", default="drone-s3-access-role-alias-dev")
    ap.add_argument("--images-bucket",
                    default="drone-images-dev-us-west-2-041686205727")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    ap.add_argument("--gui", action="store_true")
    args = ap.parse_args()

    roster = parse_roster(args.fleet)
    ids = [r["id"] for r in roster]
    print(f"== eco fleet == {len(roster)} vehicles env={args.env}: "
          f"{ids[0]} … {ids[-1]}", flush=True)

    WORKER = FleetWorker(environment=args.env, roster=roster, headless=not args.gui)

    threading.Thread(target=_start_mqtt, args=(ids, args), name="mqtt",
                     daemon=True).start()

    print("[fleet] loading Isaac…", flush=True)
    try:
        WORKER.run()  # main thread owns Isaac; blocks
    except KeyboardInterrupt:
        pass
    finally:
        WORKER.stop()


if __name__ == "__main__":
    main()
