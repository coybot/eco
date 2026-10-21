"""Run the aircraft's real daemon.py against ArduCopter SITL on the live cloud control plane.

The end-to-end test that unit tests and plain SITL runs cannot replace: the real iOS app ->
the live AWS stack (planner, "OK to fly?" confirmation, IoT) -> this daemon, with the
quadcopter's own code, as a registered sim drone -> ArduCopter 4.7.1 SITL with the
quadcopter's nav params -> photos to S3 -> the album in the app. In the app, pick the sim
drone, never the real "quadcopter".

Deviations from the aircraft: a synthetic downward camera (sitl_camera.py), a models stub so
the cloud routes chat to the mission planner (the aircraft gets there via its VLM), WiFi
provisioning skipped. A second MAVLink link records the flight to track.jsonl for scoring.

1. Prepare a drone directory: this checkout's drone/common, the synthetic camera, the models
   stub, and the sim drone's own identity - its IoT certs (provision_sim_certs.sh makes
   them) and a config.yaml (drone_id, control_plane, iot_endpoint, region,
   credentials_endpoint, s3_role_alias, video_role_alias, images_bucket, battery; copy
   the keys from config.yaml.example). Neither is in git.

    python3 drone/sim/cloud_sim_drone.py --prepare ~/sim-drone \\
        --certs ~/eco-certs-fleet/sim-quadcopter-test --config ~/sim-drone-config.yaml

   Re-run --prepare after changing drone code; it refreshes the code and keeps certs/config.

2. Run it (blocks; start it detached so a dropped SSH session does not stop it):

    setsid nohup python3 -u drone/sim/cloud_sim_drone.py --drone-dir ~/sim-drone \\
        --arducopter-bin ~/ardupilot-4.7.1/build/sitl/bin/arducopter > ~/sim-drone/drone.log 2>&1 &

   It is ready when drone.log shows "Published heartbeat". Stop it by PID (python and its
   arducopter child), not with a pkill pattern.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMON = HERE.parent / "common"
NAV_PARAMS = HERE / "sitl_params" / "quadcopter_4.7.1_nav.parm"
MODELS_STUB = ("# SITL stand-in: report an AGX so chat goes to the mission planner, as on the real aircraft.\n"
               "def detect_orin_variant():\n"
               "    return 'agx64'\n")


def prepare(dest: Path, certs: Path, config: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for f in COMMON.glob("*.py"):
        shutil.copy2(f, dest / f.name)
    shutil.copytree(COMMON / "vehicles", dest / "vehicles", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(HERE / "sitl_camera.py", dest / "camera.py")
    shutil.copy2(HERE / "sitl_flight_check.py", dest / "sitl_flight_check.py")
    (dest / "models").mkdir(exist_ok=True)
    (dest / "models" / "setup_models.py").write_text(MODELS_STUB)
    if certs:
        (dest / "certs").mkdir(exist_ok=True)
        for name in ("device.pem", "private.key", "root-ca.pem"):
            shutil.copy2(certs.expanduser() / name, dest / "certs" / name)
        os.chmod(dest / "certs" / "private.key", 0o600)
    if config:
        shutil.copy2(config.expanduser(), dest / "config.yaml")
    missing = [p for p in ("certs/device.pem", "certs/private.key", "certs/root-ca.pem", "config.yaml")
               if not (dest / p).exists()]
    print(f"prepared {dest}" + (f"; still missing: {', '.join(missing)}" if missing else ""))


def run(args):
    d = args.drone_dir.expanduser().resolve()
    sys.path.insert(0, str(d))
    import sitl_flight_check as sfc
    home = sfc.HOME
    binary = args.arducopter_bin.expanduser()
    defaults = args.defaults or ",".join([
        str(binary.parents[3] / "Tools" / "autotest" / "default_params" / "copter.parm"),
        str(NAV_PARAMS)])
    work = d / "sitl"
    work.mkdir(exist_ok=True)
    proc = subprocess.Popen([str(binary), "--model", "quad", "--speedup", "1", f"-I{args.instance}",
                             "--wipe", "--defaults", defaults,
                             "--home", f"{home[0]},{home[1]},{home[2]},{args.heading}"],
                            cwd=work, stdout=open(work / "ardupilot.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(2)
    base = 5760 + 10 * args.instance
    os.environ["COYBOT_SDK_SERIAL_PORT"] = f"tcp:127.0.0.1:{base + 2}"
    truth = sfc.Truth(f"tcp:127.0.0.1:{base}", "ardupilot")
    try:
        assert truth.wait_for_heartbeat(90), "no SITL heartbeat"
        assert truth.wait_ready(180), "no position estimate"
        print("SITL ready; waiting for the EKF...", flush=True)
        time.sleep(45)

        def record():
            with open(d / "track.jsonl", "a") as f:
                while True:
                    m = truth.conn.messages.get("GLOBAL_POSITION_INT")
                    if m is not None and m.lat:
                        f.write(json.dumps({
                            "t": round(time.time(), 2),
                            "n": round((m.lat / 1e7 - home[0]) * 111319.5, 3),
                            "e": round((m.lon / 1e7 - home[1]) * 111319.5 * math.cos(math.radians(home[0])), 3),
                            "alt": round(truth.alt, 3), "hdg": m.hdg / 100,
                            "spd": round(math.hypot(m.vx, m.vy) / 100, 3),
                            "mode": truth.mode, "armed": truth.armed}) + "\n")
                        f.flush()
                    time.sleep(0.25)
        threading.Thread(target=record, daemon=True).start()

        import daemon
        daemon.check_provisioning = lambda: True   # no WiFi hotspot onboarding in a sim
        daemon.main()
    finally:
        proc.terminate()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prepare", type=Path, metavar="DIR", help="build or refresh a sim drone directory")
    ap.add_argument("--certs", type=Path, help="with --prepare: dir holding device.pem, private.key, root-ca.pem")
    ap.add_argument("--config", type=Path, help="with --prepare: the sim drone's config.yaml")
    ap.add_argument("--drone-dir", type=Path, help="run the daemon from this prepared directory")
    ap.add_argument("--arducopter-bin", type=Path)
    ap.add_argument("--defaults", help="comma-separated param files (default: copter.parm + "
                                       "sitl_params/quadcopter_4.7.1_nav.parm)")
    ap.add_argument("--instance", type=int, default=0, help="SITL instance (-I): ports 5760+10N")
    ap.add_argument("--heading", type=float, default=0.0, help="spawn heading, deg")
    args = ap.parse_args()
    if args.prepare:
        prepare(args.prepare.expanduser(), args.certs, args.config)
    elif args.drone_dir and args.arducopter_bin:
        run(args)
    else:
        ap.error("give --prepare DIR, or --drone-dir DIR with --arducopter-bin")


if __name__ == "__main__":
    main()
