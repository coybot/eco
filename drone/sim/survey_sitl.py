"""Fly a mission (by default a small area survey) through the aircraft's real mission code
against ArduCopter or PX4 SITL, and score what the autopilot itself reports.

The code under test is what the daemon runs for a chat mission - reasoning_loop.MissionLoop
-> backends.HardwareBackend -> drone_sdk over MAVLink - taken from the eco tree given by
--eco (default: this checkout), so an old commit and a new one can be flown on the same
mission and compared. Fakes: the camera (a noise frame, opened slowly like the RealSense) and
the S3 upload. Truth comes from a second MAVLink link (sitl_flight_check.Truth) and is
scored from the takeoff point and the heading the mission started with:

  photos / cells hit / distance of each photo from its cell centre / speed at each photo
  heading of each photo, and the worst heading anywhere between the first and last photo
  ground path flown, photo order, where it landed

    # ArduCopter 4.7.1 (the quadcopter's firmware) with the quadcopter's own nav params.
    # SITL instance 1 (ports 5770/5772); instance 0 is often held by the cloud sim drone.
    python3 drone/sim/survey_sitl.py --autopilot ardupilot --instance 1 \\
        --arducopter-bin ~/ardupilot-4.7.1/build/sitl/bin/arducopter --out out-new

    # PX4 v1.17 SIH, turned to 137 deg in the air first (proves the yaw units):
    python3 drone/sim/survey_sitl.py --autopilot px4 --px4-dir ~/sitl/PX4-Autopilot \\
        --pre-yaw 137 --out out-px4

    # A/B: the same mission on an older commit, then compare.
    git worktree add /tmp/eco-old <commit>
    python3 drone/sim/survey_sitl.py ... --instance 2 --eco /tmp/eco-old --out out-old
    python3 drone/sim/survey_compare.py out-old out-new

Writes summary.json, track.json and shots.json to --out. Exit status 0 if the mission
succeeded and every cell was photographed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NAV_PARAMS = HERE / "sitl_params" / "quadcopter_4.7.1_nav.parm"
M_PER_DEG = 111319.5

DEFAULT_PHASES = [
    {"type": "arm_and_takeoff", "altitude_m": 2},
    {"type": "survey_rect", "forward_m": 2, "right_m": 2, "origin_forward_m": 0,
     "origin_right_m": 0, "spacing_m": 1, "altitude_m": 2},
    {"type": "return_home"},
    {"type": "land"},
]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--autopilot", choices=["ardupilot", "px4"], required=True)
    ap.add_argument("--eco", type=Path, default=REPO, help="eco tree whose drone/common is under test")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--phases", help="mission phases as JSON (default: 2 x 2 m survey at 2 m, home, land)")
    ap.add_argument("--arducopter-bin", type=Path)
    ap.add_argument("--defaults", help="comma-separated ArduPilot param files (default: the build's "
                                       "copter.parm + sitl_params/quadcopter_4.7.1_nav.parm)")
    ap.add_argument("--instance", type=int, default=0, help="ArduPilot SITL instance (-I): ports 5760+10N")
    ap.add_argument("--heading", type=float, default=0.0, help="ArduPilot spawn heading, deg")
    ap.add_argument("--px4-dir", type=Path)
    ap.add_argument("--pre-yaw", type=float,
                    help="take off to 2 m and turn to this heading before the mission (its own "
                         "takeoff phase is then dropped). PX4 SIH always spawns facing north.")
    ap.add_argument("--pack", metavar="CELLS:MAH",
                    help="apply a battery pack the way the app does (drone_sdk.set_battery_pack) "
                         "before the mission; the summary records what the mission loop used")
    ap.add_argument("--cam-open-s", type=float, default=3.0, help="fake camera open time (RealSense ~3 s)")
    return ap.parse_args()


def launch_ardupilot(args, work: Path):
    binary = args.arducopter_bin.expanduser()
    defaults = args.defaults or ",".join([
        str(binary.parents[3] / "Tools" / "autotest" / "default_params" / "copter.parm"),
        str(NAV_PARAMS)])
    import sitl_flight_check as sfc
    h = sfc.HOME
    proc = subprocess.Popen([str(binary), "--model", "quad", "--speedup", "1", f"-I{args.instance}",
                             "--wipe", "--defaults", defaults,
                             "--home", f"{h[0]},{h[1]},{h[2]},{args.heading}"],
                            cwd=work, stdout=open(work / "ardupilot.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(2)
    # SITL boots once a client is on SERIAL0 and only then opens SERIAL1: truth takes
    # SERIAL0, the code under test SERIAL1.
    base = 5760 + 10 * args.instance
    return proc, f"tcp:127.0.0.1:{base + 2}", f"tcp:127.0.0.1:{base}"


class FakeCamera:
    CAMERA_TYPE = "fake-realsense"

    def __init__(self, open_s):
        time.sleep(open_s)

    def start(self):
        pass

    def get_frame(self, timeout_ms=0):
        import types
        import numpy as np
        return types.SimpleNamespace(rgb=(np.random.rand(720, 1280, 3) * 255).astype("uint8"))


def px4_ports_taken():
    """PX4 SITL's fixed UDP ports already bound by someone else: another PX4 SITL on this
    host, e.g. one in a host-network container. Both then run, and the code under test hears
    neither cleanly (no heartbeat, a nonsense position) - fail with that instead."""
    import socket
    taken = []
    for port in (14540, 14550):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            taken.append(port)
        finally:
            sock.close()
    return taken


def heading_of(truth):
    """Compass heading (deg) from ATTITUDE, else GLOBAL_POSITION_INT.hdg."""
    a = truth.conn.messages.get("ATTITUDE")
    if a is not None:
        return math.degrees(a.yaw) % 360.0
    g = truth.conn.messages.get("GLOBAL_POSITION_INT")
    if g is not None and g.hdg != 65535:
        return g.hdg / 100.0
    return None


def turn_to(truth, autopilot, heading_deg):
    """Point the aircraft at heading_deg where it is, on the truth link, independently of the
    code under test (so an old commit can be pre-yawed too). Yaw is radians on both
    autopilots: ArduPilot's position-target yaw and PX4's DO_REPOSITION param4 (checked in
    PX4 v1.17 SITL: 137 deg asked, 137.2 deg flown)."""
    g = truth.conn.messages["GLOBAL_POSITION_INT"]
    yaw = math.radians(heading_deg % 360.0)
    mav = truth.conn.mav
    if autopilot == "px4":
        mav.command_int_send(1, 1, 0, 192, 0, 0,       # MAV_FRAME_GLOBAL, MAV_CMD_DO_REPOSITION
                             -1.0, 1, 0.0, yaw, g.lat, g.lon, g.alt / 1000.0)
    else:
        mav.set_position_target_global_int_send(
            0, 1, 1, 6,                                # MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
            0b0000101111111000, g.lat, g.lon, g.relative_alt / 1000.0,
            0, 0, 0, 0, 0, 0, yaw, 0)


def main():
    args = parse_args()
    if args.autopilot == "ardupilot" and not args.arducopter_bin:
        sys.exit("--arducopter-bin is required for ArduPilot")
    if args.autopilot == "px4" and not args.px4_dir:
        sys.exit("--px4-dir is required for PX4")
    # The harness's own sitl_flight_check, then the code under test in front of everything:
    # importing sitl_flight_check puts THIS checkout's drone/common first on sys.path, which
    # silently replaced an older --eco tree (an old-vs-new run flew new code twice).
    under_test = args.eco.expanduser().resolve() / "drone" / "common"
    sys.path.insert(0, str(HERE))
    import sitl_flight_check as sfc
    sys.path[:] = [p for p in sys.path if Path(p).resolve() not in (under_test, sfc.COMMON.resolve())]
    sys.path.insert(0, str(under_test))

    phases = json.loads(args.phases) if args.phases else DEFAULT_PHASES
    if args.pre_yaw is not None:
        phases = [p for p in phases if p.get("type") != "arm_and_takeoff"]
    args.out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"survey-{args.autopilot}-"))
    if args.autopilot == "px4":
        taken = px4_ports_taken()
        if taken:
            print(f"FAIL: UDP {taken} already in use - another PX4 SITL is running on this host "
                  f"(check `ss -lunp` and `docker ps`); PX4 runs one at a time")
            return 2
        proc, sdk_url, truth_url = sfc.launch_px4(args.px4_dir.expanduser(), work)
    else:
        proc, sdk_url, truth_url = launch_ardupilot(args, work)
    cfg = work / "cfg"
    sfc.write_config(cfg, cells=4)
    os.environ["COYBOT_SDK_SERIAL_PORT"] = sdk_url
    os.environ["COYBOT_SDK_CONFIG_DIR"] = str(cfg)
    truth = sfc.Truth(truth_url, args.autopilot)

    home = sfc.HOME

    def ne(lat, lon):
        return ((lat - home[0]) * M_PER_DEG,
                (lon - home[1]) * M_PER_DEG * math.cos(math.radians(home[0])))

    track, shots, stop = [], [], threading.Event()

    def sample():
        while not stop.is_set():
            m = truth.conn.messages.get("GLOBAL_POSITION_INT")
            if m is not None and m.lat:
                n, e = ne(m.lat / 1e7, m.lon / 1e7)
                track.append((round(time.time(), 2), round(n, 3), round(e, 3), round(truth.alt, 3),
                              round(math.hypot(m.vx, m.vy) / 100, 3), truth.mode, truth.armed,
                              heading_of(truth)))
            time.sleep(0.2)

    def fake_upload(local_path, conversation_id, content_type=None):
        if Path(local_path).name.endswith(".csv"):
            import shutil
            shutil.copy(local_path, args.out / "survey_manifest.csv")
            return "https://example.invalid/survey_manifest.csv"
        m = truth.conn.messages.get("GLOBAL_POSITION_INT")
        n, e = ne(m.lat / 1e7, m.lon / 1e7)
        shots.append({"t": time.time(), "n": n, "e": e, "alt": truth.alt,
                      "spd": math.hypot(m.vx, m.vy) / 100, "hdg": heading_of(truth)})
        return f"https://example.invalid/{len(shots)}.jpg"

    cam = {}
    rc = 1
    try:
        if not truth.wait_for_heartbeat(90):
            print(f"FAIL: no heartbeat from SITL (logs in {work})")
            return 2
        if args.autopilot == "px4":
            truth.set_params(sfc.PX4_PARAMS)   # ArduPilot's come from --defaults
        if not truth.wait_ready(180):
            print(f"FAIL: no position estimate (logs in {work})")
            return 2
        time.sleep(45 if args.autopilot == "ardupilot" else 10)   # EKF settling on GPS

        import drone_sdk   # reads COYBOT_SDK_CONFIG_DIR / COYBOT_SDK_SERIAL_PORT set above
        if Path(drone_sdk.__file__).resolve().parent != under_test:
            print(f"FAIL: imported {drone_sdk.__file__}, not the tree under test {under_test}")
            return 2
        # Wait until the code under test has its own link up and its preflight says go, as
        # the daemon's would be long before a chat mission. Started cold, PX4's link took
        # ~10 s and the first takeoff ran with no battery reading at all.
        ready, end = None, time.time() + 90
        while time.time() < end:
            try:
                if drone_sdk.get_local_pose() is not None:
                    ready = drone_sdk.battery_status(planned_alt_m=2.0)
                    if ready.get("can_takeoff"):
                        break
            except Exception as e:
                ready = {"error": str(e)}
            time.sleep(2)
        print("code under test ready:", {k: (ready or {}).get(k) for k in ("can_takeoff", "takeoff_reason")},
              flush=True)
        pack, loop_battery = None, {}
        if args.pack:
            cells, mah = (int(x) for x in args.pack.split(":"))
            drone_sdk.set_battery_pack(cells=cells, capacity_mah=mah)
            pack = {"cells": cells, "capacity_mah": mah}
        # Record the pack each MissionLoop decides with (sfc.run_mission builds the loop).
        import reasoning_loop as _rl
        _init = _rl.MissionLoop.__init__

        def _spy(self, *a, **k):
            _init(self, *a, **k)
            g = getattr(self, "_battery_gov", None)
            if g is not None:
                loop_battery.update(cells=g.config.cells, capacity_mah=g.config.capacity_mah)
        _rl.MissionLoop.__init__ = _spy
        drone_sdk._get_camera = lambda: cam.setdefault("c", FakeCamera(args.cam_open_s))
        drone_sdk.release_camera = lambda: cam.pop("c", None)
        drone_sdk.upload_media = fake_upload
        threading.Thread(target=sample, daemon=True).start()
        time.sleep(1)

        pre = None
        if args.pre_yaw is not None:
            r0 = sfc.run_mission([{"type": "arm_and_takeoff", "altitude_m": 2}], "take off")
            turn_to(truth, args.autopilot, args.pre_yaw)
            time.sleep(10)
            pre = {"takeoff_ok": getattr(r0, "success", None), "asked": args.pre_yaw,
                   "got": round(heading_of(truth), 1)}
            print("pre-yaw:", pre, flush=True)

        h0 = heading_of(truth)
        if args.pre_yaw is None:
            truth.reset()
        t0 = time.time()
        result = sfc.run_mission(phases, "survey the area")
        elapsed = time.time() - t0
        time.sleep(5)
        sfc.settle(truth, 90)
        stop.set()

        # Score from the takeoff point the code under test measured its offsets from. PX4 SIH's
        # GPS puts the parked aircraft ~0.7 m from PX4_HOME, so the configured home is not it.
        fh = getattr(drone_sdk, "_flight_home", None)
        n0, e0 = ne(*fh) if fh else next(((p[1], p[2]) for p in track if p[6]), (0.0, 0.0))
        yaw = math.radians(h0)

        def fr(n, e):
            dn, de = n - n0, e - e0
            return dn * math.cos(yaw) + de * math.sin(yaw), de * math.cos(yaw) - dn * math.sin(yaw)

        def off(h):
            return (h - h0 + 180.0) % 360.0 - 180.0

        summary = {
            "autopilot": args.autopilot, "code_under_test": str(Path(drone_sdk.__file__).resolve().parent),
            "phases": phases,
            "success": getattr(result, "success", None),
            "failure": getattr(result, "failure_reason", None),
            "media_warnings": getattr(result, "media_warnings", None),
            "elapsed_s": round(elapsed, 1), "start_heading": round(h0, 1), "pre_yaw": pre,
            "pack_set": pack, "mission_loop_battery": loop_battery or None,
            "photos": len(shots),
        }
        sv = next((p for p in phases if p.get("type") == "survey_rect"), None)
        if sv is not None:
            F, R = float(sv.get("forward_m", 10)), float(sv.get("right_m", 10))
            of, orr = float(sv.get("origin_forward_m", 0)), float(sv.get("origin_right_m", 0))
            sp = float(sv.get("spacing_m") or 1)
            nf, nr = max(1, math.ceil(F / sp - 1e-9)), max(1, math.ceil(R / sp - 1e-9))
            cf, cr = F / nf, R / nr
            covered, errs = set(), []
            for s in shots:
                f, r = fr(s["n"], s["e"])
                s["f"], s["r"] = f, r
                i, j = math.floor((f - of) / cf), math.floor((r - orr) / cr)
                if 0 <= i < nf and 0 <= j < nr:
                    covered.add((i, j))
                ci, cj = min(max(i, 0), nf - 1), min(max(j, 0), nr - 1)
                errs.append(math.hypot(f - of - (ci + .5) * cf, r - orr - (cj + .5) * cr))
            during = [p for p in track if shots and shots[0]["t"] <= p[0] <= shots[-1]["t"]
                      and p[7] is not None]
            summary.update({
                "cells": nf * nr, "cells_hit": len(covered),
                "photo_err_mean_max_m": [round(sum(errs) / len(errs), 2), round(max(errs), 2)] if errs else None,
                "photo_alt_min_max": [round(min(s["alt"] for s in shots), 2),
                                      round(max(s["alt"] for s in shots), 2)] if shots else None,
                "photo_heading_off_start_deg": [round(off(s["hdg"]), 1) for s in shots if s["hdg"] is not None],
                "heading_max_off_during_survey_deg": round(max((abs(off(p[7])) for p in during), default=0.0), 1),
                "speed_at_photos_mps": [round(s["spd"], 2) for s in shots],
                "photo_order_fwd_right_m": [[round(s["f"], 1), round(s["r"], 1)] for s in shots],
            })
        fly = [p for p in track if p[6] and p[3] > 0.3]
        last = track[-1] if track else None
        summary.update({
            "ground_path_airborne_m": round(sum(math.hypot(b[1] - a[1], b[2] - a[2])
                                                for a, b in zip(fly, fly[1:])), 1),
            "max_alt_m": round(truth.max_alt, 2),
            "end_dist_from_takeoff_m": round(math.hypot(last[1] - n0, last[2] - e0), 2) if last else None,
            "armed_at_end": truth.armed, "mode_at_end": truth.mode,
            "autopilot_text": list(dict.fromkeys(truth.texts))[-12:],
        })
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
        (args.out / "track.json").write_text(json.dumps(track))
        (args.out / "shots.json").write_text(json.dumps(shots))
        print(json.dumps(summary, indent=1), flush=True)
        ok = summary["success"] and not summary["armed_at_end"]
        if sv is not None:
            ok = ok and summary["cells_hit"] == summary["cells"]
        rc = 0 if ok else 1
    finally:
        stop.set()
        truth.stop()
        try:
            import drone_sdk
            drone_sdk.disconnect()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        print("SITL logs:", work)
    return rc


if __name__ == "__main__":
    sys.exit(main())
