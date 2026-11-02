"""GPS-denied flight on visual odometry, proven in ArduCopter SITL.

ArduCopter SITL runs with its GPS switched off (SIM_GPS1_ENABLE 0 in 4.6+,
SIM_GPS_DISABLE 1 before) and the quadcopter's own nav params plus
drone/platforms/orin/params/gps_denied_extnav.param. The "VIO" is the
simulator's true pose (SIM_STATE on a second MAVLink link) with injected
white noise, random-walk position drift and heading drift
(vio_bridge.NoisyTruthSource), published in ROS conventions (ENU world, FLU
body) so the frame conversion is exercised, and streamed to the FC through
vio_bridge over drone_sdk's own link - exactly the aircraft's path. Then
the aircraft's real code flies: set_ekf_origin, takeoff (GUIDED), a local
square with goto_local, land.

Along the way, by default:
  * a 1.0 s VIO stall while hovering: the bridge must go unhealthy within
    ~0.3 s, stop sending, and resume with reset_counter bumped;
  * a flagged VIO relocalisation (the VIO frame jumps 0.4 m): the EKF must
    take the new position as a reset.

In 4.7.1 SIM_GPS1_ENABLE 0 is a receiver that is present but never locks
(SITL/SIM_GPS.cpp: have_lock = enabled && ...), so AP_GPS still detects a
u-blox and GPS_RAW_INT reports fix_type 1 (no fix) - a jammed / indoor GPS.

Asserts: no GPS fix ever, no EKF source set names GPS, the EKF announced
external nav and never GPS; every corner reached within --bound-m of its
target (truth); the EKF's estimate matches the VIO's belief (truth + its
known error) within --ekf-bound-m; the watchdog behaved; landed and disarmed.

    # on a Linux box with an ArduCopter 4.7.1 SITL build (the quadcopter's firmware), instance 3
    ~/sitl/venv/bin/python drone/sim/sitl_gps_denied_test.py \\
        --arducopter-bin ~/ardupilot-4.7.1/build/sitl/bin/arducopter --instance 3 --out out

Writes summary.json (all numbers) and track.json to --out. Exit 0 = passed.
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
ECO_DRONE = HERE.parent
COMMON = ECO_DRONE / "common"
NAV_PARAMS = HERE / "sitl_params" / "quadcopter_4.7.1_nav.parm"
EXTNAV_PARAMS = ECO_DRONE / "platforms" / "orin" / "params" / "gps_denied_extnav.param"
HOME = (-35.363261, 149.165230, 584.0)   # ArduPilot's standard SITL home (CMAC)
M_PER_DEG = 111319.5
EK3_GPS_SOURCES = {'POSXY': (3,), 'VELXY': (3,), 'POSZ': (3,), 'VELZ': (3,), 'YAW': (2, 3, 8)}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arducopter-bin", type=Path,
                    default=Path("~/ardupilot-4.7.1/build/sitl/bin/arducopter"))
    ap.add_argument("--instance", type=int, default=3, help="SITL instance (-I): ports 5760+10N")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--side-m", type=float, default=6.0, help="square side (m)")
    ap.add_argument("--alt-m", type=float, default=3.0, help="flight height above the origin (m)")
    ap.add_argument("--message", choices=["odometry", "vision"], default="odometry")
    ap.add_argument("--rate-hz", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--pos-noise-m", type=float, default=0.02)
    ap.add_argument("--drift", type=float, default=0.02, help="position random walk, m/sqrt(s)")
    ap.add_argument("--yaw-drift", type=float, default=0.05, help="heading random walk, deg/sqrt(s)")
    ap.add_argument("--bound-m", type=float, default=1.0, help="max horizontal truth-vs-target error at a corner")
    ap.add_argument("--ekf-bound-m", type=float, default=0.35,
                    help="max |EKF - VIO belief| (horizontal) at a corner")
    ap.add_argument("--no-stall-test", action="store_true")
    ap.add_argument("--no-reset-test", action="store_true")
    ap.add_argument("--keep-work", action="store_true", help="keep the SITL work dir")
    return ap.parse_args()


def ned_of(lat, lon, alt, ref):
    return ((lat - ref[0]) * M_PER_DEG,
            (lon - ref[1]) * M_PER_DEG * math.cos(math.radians(ref[0])),
            -(alt - ref[2]))


class Truth:
    """The simulator's own state and what the FC reports, on SITL's SERIAL0."""

    def __init__(self, url):
        os.environ.setdefault("MAVLINK20", "1")
        from pymavlink import mavutil
        self.mavutil = mavutil
        self.conn = mavutil.mavlink_connection(url, source_system=254)
        self.lock = threading.Lock()
        self.sim = None            # (t_monotonic, SIM_STATE)
        self.ground_alt = None     # SIM_STATE alt on the ground at start (AMSL)
        self.local = None          # LOCAL_POSITION_NED (the EKF)
        self.ekf_flags = None
        self.armed = False
        self.mode = None
        self.texts = []
        self.gps = []              # (t, fix_type, satellites_visible)
        self.params = {}
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        last_hb = 0.0
        asked = False
        while not self._stop:
            now = time.monotonic()
            if now - last_hb > 1.0:
                try:
                    self.conn.mav.heartbeat_send(6, 8, 0, 0, 0)
                    if self.conn.target_system:
                        # SIM_STATE at 50 Hz, the rest at 10 Hz, on this link only
                        for mid, hz in ((108, 50), (32, 10), (24, 5), (193, 5)):
                            self.conn.mav.command_long_send(
                                self.conn.target_system, 1, 511, 0, mid, 1e6 / hz, 0, 0, 0, 0, 0)
                        asked = True
                except Exception:
                    pass
                last_hb = now
            try:
                m = self.conn.recv_match(blocking=True, timeout=0.2)
            except TypeError:
                continue
            if m is None:
                continue
            t = m.get_type()
            with self.lock:
                if t == 'HEARTBEAT' and m.autopilot == 3 and m.get_srcComponent() == 1:
                    if not self.conn.target_system or not asked:
                        self.conn.target_system = m.get_srcSystem()
                    self.armed = bool(m.base_mode & 0x80)
                    try:
                        self.mode = self.mavutil.mode_string_v10(m)
                    except Exception:
                        self.mode = f"MODE_{m.custom_mode}"
                elif t == 'SIM_STATE':
                    self.sim = (time.monotonic(), m)
                    if self.ground_alt is None:
                        self.ground_alt = m.alt
                elif t == 'LOCAL_POSITION_NED':
                    self.local = m
                elif t == 'EKF_STATUS_REPORT':
                    self.ekf_flags = m.flags
                elif t == 'GPS_RAW_INT':
                    self.gps.append((time.time(), m.fix_type, m.satellites_visible))
                elif t == 'STATUSTEXT':
                    self.texts.append((time.time(), m.text))
                    print(f"  [FC] {m.text}", flush=True)
                elif t == 'PARAM_VALUE':
                    pid = m.param_id if isinstance(m.param_id, str) else m.param_id.decode()
                    self.params[pid.rstrip('\x00')] = m.param_value

    def stop(self):
        self._stop = True

    def wait_for(self, cond, timeout):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if cond(self):
                    return True
            time.sleep(0.2)
        return False

    def param(self, name, timeout=3.0):
        with self.lock:
            self.params.pop(name, None)
        for _ in range(3):
            self.conn.mav.param_request_read_send(self.conn.target_system, 1, name.encode(), -1)
            if self.wait_for(lambda s: name in s.params, timeout / 3):
                return self.params[name]
        return None

    def set_param(self, name, value):
        self.conn.mav.param_set_send(self.conn.target_system, 1, name.encode(), float(value), 9)
        return self.wait_for(lambda s: abs(s.params.get(name, value + 1) - value) < 1e-4, 3.0)

    def state(self):
        """(t_monotonic, pos_ned, q_body_to_ned (w,x,y,z), vel_ned) or None - the VIO's truth."""
        with self.lock:
            sim, ground = self.sim, self.ground_alt
        if sim is None or time.monotonic() - sim[0] > 0.2:
            return None
        t, m = sim
        lat = getattr(m, 'lat_int', 0) / 1e7 if getattr(m, 'lat_int', 0) else m.lat / 1e7
        lon = getattr(m, 'lon_int', 0) / 1e7 if getattr(m, 'lon_int', 0) else m.lon / 1e7
        pos = ned_of(lat, lon, m.alt, (HOME[0], HOME[1], ground))
        return t, pos, (m.q1, m.q2, m.q3, m.q4), (m.vn, m.ve, m.vd)

    def ekf(self):
        with self.lock:
            m = self.local
        return None if m is None else (m.x, m.y, m.z)

    def said(self, fragment):
        with self.lock:
            return [txt for _, txt in self.texts if fragment.lower() in txt.lower()]


def launch_sitl(args, work):
    binary = args.arducopter_bin.expanduser()
    extra = work / "sitl_gps_off.parm"
    # 4.6+ (and 4.7.1, the quadcopter's): SIM_GPS1_ENABLE (SITL/SIM_GPS.cpp GPSParms ENABLE;
    # SITL.cpp notes index 1 "was GPS_DISABLE"). Older builds: SIM_GPS_DISABLE 1, set at runtime.
    extra.write_text("SIM_GPS1_ENABLE 0\n")
    defaults = ",".join([str(binary.parents[3] / "Tools" / "autotest" / "default_params" / "copter.parm"),
                         str(NAV_PARAMS), str(EXTNAV_PARAMS), str(extra)])
    cmd = [str(binary), "--model", "quad", "--speedup", "1", f"-I{args.instance}", "--wipe",
           "--defaults", defaults, "--home", f"{HOME[0]},{HOME[1]},{HOME[2]},0"]
    print("SITL:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=work, stdout=open(work / "ardupilot.log", "w"),
                            stderr=subprocess.STDOUT)
    time.sleep(2)
    base = 5760 + 10 * args.instance
    return proc, f"tcp:127.0.0.1:{base}", f"tcp:127.0.0.1:{base + 2}"


def ports_taken(instance):
    import socket
    base = 5760 + 10 * instance
    taken = []
    for port in (base, base + 2):
        s = socket.socket()
        # REUSEADDR as SITL itself uses: a previous run's TIME_WAIT is not "taken", a listener is
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            taken.append(port)
        finally:
            s.close()
    return taken


def poll(cond, timeout, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


class Pumper:
    """SITL drops a TCP client that nobody reads: keep drone_sdk's link read
    while no SDK call is (before takeoff, after landing)."""

    def __init__(self, sdk):
        self.sdk, self.on = sdk, threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            if self.on.is_set():
                try:
                    self.sdk._pump(0.2)
                except Exception:
                    pass
            time.sleep(0.3)


def main():
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    taken = ports_taken(args.instance)
    if taken:
        print(f"FAIL: TCP {taken} in use - SITL instance {args.instance} is taken; pick another --instance")
        return 2
    work = Path(tempfile.mkdtemp(prefix="gps-denied-"))
    proc, truth_url, sdk_url = launch_sitl(args, work)
    cfg = work / "cfg"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        "vehicle_type: quadcopter\n"
        "battery:\n  cells: 4\n  capacity_mah: 5000\n  low_voltage_per_cell: 3.5\n"
        "  critical_voltage_per_cell: 3.3\n  allow_unknown_battery: true\n")
    os.environ["COYBOT_SDK_SERIAL_PORT"] = sdk_url
    os.environ["COYBOT_SDK_CONFIG_DIR"] = str(cfg)
    os.environ.setdefault("MAVLINK20", "1")
    sys.path.insert(0, str(COMMON))

    summary = {"args": {k: str(v) for k, v in vars(args).items()}, "checks": {}, "corners": [],
               "events": []}
    checks = summary["checks"]
    track, stop_track = [], threading.Event()
    truth = bridge = source = sdk = None

    def event(what, **kw):
        kw.update(what=what, t=round(time.time(), 2))
        summary["events"].append(kw)
        print(f"== {what} {kw if len(kw) > 2 else ''}", flush=True)

    def check(name, ok, **detail):
        checks[name] = dict(ok=bool(ok), **detail)
        print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}", flush=True)
        return ok

    try:
        truth = Truth(truth_url)
        if not truth.wait_for(lambda s: s.mode is not None and s.sim is not None, 90):
            print(f"FAIL: no heartbeat / SIM_STATE from SITL (logs in {work})")
            return 2
        event("sitl up")

        # -- configuration: GPS off, sources ExternalNav ----------------------------
        gps1 = truth.param("SIM_GPS1_ENABLE")
        if gps1 is None:   # pre-4.6 SITL
            truth.set_param("SIM_GPS_DISABLE", 1)
            gps_param = ("SIM_GPS_DISABLE", truth.param("SIM_GPS_DISABLE"))
        else:
            gps_param = ("SIM_GPS1_ENABLE", gps1)
        check("sim_gps_disabled", gps_param[1] == (1 if gps_param[0] == "SIM_GPS_DISABLE" else 0),
              param=gps_param[0], value=gps_param[1])
        srcs = {f"EK3_SRC{n}_{k}": truth.param(f"EK3_SRC{n}_{k}") for n in (1, 2, 3) for k in EK3_GPS_SOURCES}
        srcs["VISO_TYPE"] = truth.param("VISO_TYPE")
        summary["params"] = srcs
        gps_src = [k for k, v in srcs.items() if k.startswith("EK3")
                   and v is not None and int(v) in EK3_GPS_SOURCES[k.split("_", 2)[2]]]
        check("no_ekf_source_uses_gps", not gps_src and None not in srcs.values(), gps_sources=gps_src)
        check("extnav_configured", srcs["VISO_TYPE"] == 1 and srcs["EK3_SRC1_POSXY"] == 6
              and srcs["EK3_SRC1_VELXY"] == 6 and srcs["EK3_SRC1_YAW"] == 6,
              viso=srcs["VISO_TYPE"], posxy=srcs["EK3_SRC1_POSXY"])

        # -- the aircraft's code: drone_sdk link, VIO bridge -------------------------
        import drone_sdk
        import vio_bridge
        sdk = drone_sdk
        if Path(sdk.__file__).resolve().parent != COMMON.resolve():
            print(f"FAIL: imported {sdk.__file__}, not {COMMON}")
            return 2
        sdk._connect_locked()
        pumper = Pumper(sdk)
        pumper.on.set()
        conv = vio_bridge.FrameConvention("ENU", "FLU")
        health_log = []
        bridge = vio_bridge.VioBridge(
            conv, rate_hz=args.rate_hz, message=args.message,
            on_health_change=lambda ok, why: health_log.append((time.monotonic(), ok, why)))
        source = vio_bridge.NoisyTruthSource(
            bridge, truth.state, rate_hz=args.rate_hz, pos_noise_m=args.pos_noise_m,
            drift_m_per_sqrt_s=args.drift, yaw_drift_deg_per_sqrt_s=args.yaw_drift, seed=args.seed)
        source.start()
        bridge.start()
        if not truth.wait_for(lambda s: bridge.healthy, 10):
            check("vio_bridge_healthy", False, health=bridge.health())
            return 1
        event("vio streaming", health=bridge.health())

        def sample_track():
            while not stop_track.is_set():
                st, ekf = truth.state(), truth.ekf()
                if st is not None:
                    bias, ybias = source.drift_ned()
                    track.append({"t": round(time.time(), 2), "truth": [round(v, 3) for v in st[1]],
                                  "ekf": None if ekf is None else [round(v, 3) for v in ekf],
                                  "vio_bias": [round(v, 3) for v in bias],
                                  "vio_yaw_bias_deg": round(math.degrees(ybias), 3),
                                  "healthy": bridge.healthy, "reset_counter": bridge.reset_counter,
                                  "armed": truth.armed, "mode": truth.mode})
                time.sleep(0.1)
        threading.Thread(target=sample_track, daemon=True).start()

        ok = sdk.set_ekf_origin(HOME[0], HOME[1], HOME[2], timeout_s=60)
        check("ekf_origin_set", ok, origin=sdk._ekf_origin)
        if not ok:
            return 1
        # absolute horizontal position (0x10) + attitude (0x01) + vertical position (0x20)
        ready = truth.wait_for(lambda s: s.ekf_flags is not None and (s.ekf_flags & 0x31) == 0x31
                               and s.local is not None, 120)
        check("ekf_position_ready", ready, flags=truth.ekf_flags)
        if not ready:
            return 1
        time.sleep(10)   # let the EKF settle on the VIO before flying
        check("ekf_says_external_nav", bool(truth.said("external nav")), texts=truth.said("external nav"))
        event("ekf ready", local=sdk.get_local_position(), origin=sdk.get_ekf_origin())

        # -- fly ---------------------------------------------------------------
        pumper.on.clear()
        took_off = sdk.takeoff(args.alt_m)
        check("takeoff", took_off, local=sdk.get_local_position())
        if not took_off:
            return 1
        s = args.side_m
        corners = [(s, 0.0), (s, s), (0.0, s), (0.0, 0.0)]
        reset_offset = (0.4, -0.3, 0.0)
        for i, (n, e) in enumerate(corners):
            arrived = sdk.goto_local(n, e, args.alt_m, tol_m=0.3)
            time.sleep(3.0)      # settle, then score
            st, ekf = truth.state(), truth.ekf()
            belief = source.believed(st[1])
            corner = {"target": [n, e, args.alt_m], "arrived": arrived,
                      "truth": [round(v, 3) for v in st[1]],
                      "ekf": [round(v, 3) for v in ekf],
                      "vio_belief": [round(v, 3) for v in belief],
                      "truth_err_h_m": round(math.hypot(st[1][0] - n, st[1][1] - e), 3),
                      "truth_err_v_m": round(abs(-st[1][2] - args.alt_m), 3),
                      "ekf_vs_belief_h_m": round(math.hypot(ekf[0] - belief[0], ekf[1] - belief[1]), 3),
                      "ekf_vs_truth_h_m": round(math.hypot(ekf[0] - st[1][0], ekf[1] - st[1][1]), 3),
                      "vio_err_h_m": round(math.hypot(belief[0] - st[1][0], belief[1] - st[1][1]), 3)}
            summary["corners"].append(corner)
            event(f"corner {i + 1}", **corner)

            if i == 0 and not args.no_stall_test:
                rc0, before = bridge.reset_counter, truth.state()[1]
                t0 = time.monotonic()
                source.stall(1.0)
                went = poll(lambda: not bridge.healthy, 1.0)
                t_unhealthy = time.monotonic() - t0
                sent0 = bridge.stats['sent']
                time.sleep(max(0.0, 1.0 - t_unhealthy) * 0.8)
                sent_during = bridge.stats['sent'] - sent0
                back = truth.wait_for(lambda _: bridge.healthy, 5.0)
                time.sleep(2.0)
                after = truth.state()[1]
                summary["stall_test"] = {
                    "unhealthy_after_s": round(t_unhealthy, 3), "sent_while_unhealthy": sent_during,
                    "recovered": back, "reset_counter": [rc0, bridge.reset_counter],
                    "truth_moved_m": round(math.hypot(after[0] - before[0], after[1] - before[1]), 3)}
                check("watchdog_stall", went and t_unhealthy < 0.45 and sent_during == 0 and back
                      and bridge.reset_counter == (rc0 + 1) % 256
                      and summary["stall_test"]["truth_moved_m"] < 0.5, **summary["stall_test"])
            if i == 1 and not args.no_reset_test:
                rc0 = bridge.reset_counter
                source.inject_jump(reset_offset, flag_reset=True)
                time.sleep(3.0)
                st, ekf = truth.state(), truth.ekf()
                belief = source.believed(st[1])
                summary["reset_test"] = {
                    "offset": reset_offset, "reset_counter": [rc0, bridge.reset_counter],
                    "ekf_vs_belief_h_m": round(math.hypot(ekf[0] - belief[0], ekf[1] - belief[1]), 3),
                    "ekf_minus_truth": [round(ekf[k] - st[1][k], 3) for k in range(2)]}
                check("vio_reset_taken", bridge.reset_counter == (rc0 + 1) % 256
                      and summary["reset_test"]["ekf_vs_belief_h_m"] < args.ekf_bound_m,
                      **summary["reset_test"])

        sdk.land()
        landed = truth.wait_for(lambda s: not s.armed, 60)
        pumper.on.set()
        st = truth.state()
        belief = source.believed(st[1])
        summary["landed"] = {"disarmed": landed, "truth": [round(v, 3) for v in st[1]],
                             "vio_belief": [round(v, 3) for v in belief],
                             "dist_from_takeoff_m": round(math.hypot(st[1][0], st[1][1]), 3)}
        check("landed_and_disarmed", landed, **summary["landed"])

        # -- verdicts -------------------------------------------------------------
        jumped = not args.no_reset_test
        for i, c in enumerate(summary["corners"]):
            # after the relocalisation the VIO (so the aircraft) is off by the jump on purpose:
            # score truth against target only where the VIO's own error is not the point
            vio_err = c["vio_err_h_m"]
            check(f"corner{i + 1}_reached", c["arrived"] and c["truth_err_h_m"] <= args.bound_m + (
                vio_err if jumped and i >= 2 else 0.0) and c["truth_err_v_m"] <= 0.6,
                  truth_err_h_m=c["truth_err_h_m"], truth_err_v_m=c["truth_err_v_m"], vio_err_h_m=vio_err)
            check(f"corner{i + 1}_ekf_tracks_vio", c["ekf_vs_belief_h_m"] <= args.ekf_bound_m,
                  ekf_vs_belief_h_m=c["ekf_vs_belief_h_m"])
        fixes = [f for _, f, _ in truth.gps]
        check("no_gps_fix_ever", all(f < 2 for f in fixes), gps_raw_int_msgs=len(fixes),
              max_fix_type=max(fixes) if fixes else None,
              max_sats=max((s for _, _, s in truth.gps), default=None))
        check("ekf_never_used_gps", not truth.said("using gps"), texts=truth.said("using gps"))
        summary["bridge"] = bridge.health()
        summary["health_changes"] = [(round(t, 2), ok, why) for t, ok, why in health_log]
        rate = summary["bridge"]["rate_hz_measured"]
        check("stream_rate", abs(rate - args.rate_hz) < 0.2 * args.rate_hz, rate_hz=rate)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("no_exception", False, error=repr(e))
    finally:
        stop_track.set()
        for o in (source, bridge):
            try:
                if o is not None:
                    o.stop()
            except Exception:
                pass
        try:
            if sdk is not None:
                sdk.disconnect()
        except Exception:
            pass
        if truth is not None:
            truth.stop()
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
        summary["sitl_exit"] = proc.returncode
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        (args.out / "track.json").write_text(json.dumps(track))
        try:
            import shutil
            shutil.copy(work / "ardupilot.log", args.out / "ardupilot.log")
            if not args.keep_work:
                shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass
    failed = [k for k, v in checks.items() if not v["ok"]]
    print(f"\n{'PASSED' if not failed else 'FAILED: ' + ', '.join(failed)} "
          f"({len(checks) - len(failed)}/{len(checks)} checks) - {args.out / 'summary.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
