"""Fly the real mission path against the real autopilot firmware before a drone does.

Every takeoff bug found on the PX4 test aircraft (2026-10-10) was flight-controller behaviour
that unit tests with a fake FC could not show: PX4's 10 s preflight auto-disarm,
"Already higher than takeoff altitude", the MIS_TAKEOFF_ALT clamp, no arming in
Land, a heartbeat read before PX4 had reported the arm. SITL runs the actual
PX4 / ArduPilot firmware with simulated physics, so it reproduces all of them.

The code under test is exactly what the daemon runs for a chat mission:
reasoning_loop.MissionLoop -> backends.HardwareBackend -> drone_sdk, over
MAVLink. The verdict does NOT trust that code: a second MAVLink link to the
autopilot records what the autopilot itself reports (armed, land detector,
altitude, mode).

    # PX4 (v1.17 SITL with the built-in SIH simulator; no Gazebo):
    python3 drone/sim/sitl_flight_check.py --autopilot px4 --px4-dir ~/sitl/PX4-Autopilot
    # ArduPilot (ArduCopter SITL binary):
    python3 drone/sim/sitl_flight_check.py --autopilot ardupilot \\
        --arducopter-bin ~/ardupilot/build/sitl/bin/arducopter \\
        --defaults ~/ardupilot/Tools/autotest/default_params/copter.parm

Exit status 0 only if every scenario passes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))

HOME = (-35.363261, 149.165230, 584.0)   # ArduPilot's standard SITL home (CMAC)

# Parameters that change what a takeoff does, set to what the aircraft carry
# (the PX4 test aircraft, read 2026-10-10) instead of SITL's defaults.
PX4_PARAMS = {
    'MIS_TAKEOFF_ALT': 1.0, 'COM_DISARM_PRFLT': 10.0, 'COM_DISARM_LAND': 2.0,
    'COM_RC_IN_MODE': 3, 'COM_RCL_EXCEPT': 7, 'NAV_RCL_ACT': 2, 'NAV_DLL_ACT': 0,
    'COM_ARM_WO_GPS': 1,
}
ARDUPILOT_PARAMS = {'DISARM_DELAY': 10, 'ARMING_CHECK': 1}


class Truth:
    """What the autopilot itself reports, on its own MAVLink link."""

    def __init__(self, url, autopilot):
        from pymavlink import mavutil
        import px4_compat
        self._mavutil, self._px4 = mavutil, px4_compat
        self.autopilot = autopilot
        self.conn = mavutil.mavlink_connection(url, source_system=254)
        self.lock = threading.Lock()
        self.reset()
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def reset(self):
        with getattr(self, 'lock', threading.Lock()):
            self.armed = False
            self.ever_armed = False
            self.ever_airborne = False   # the autopilot's land detector, not drifting altitude
            self.got_land_state = False  # ArduCopter sends no EXTENDED_SYS_STATE
            self.max_alt = 0.0
            self.alt = 0.0
            self.landed_state = None
            self.mode = None
            self.texts = []

    def _run(self):
        last_hb = 0.0
        while not self._stop:
            if time.time() - last_hb > 1.0:   # a GCS heartbeat, so the FC streams to us
                try:
                    self.conn.mav.heartbeat_send(6, 8, 0, 0, 0)
                    # ArduPilot streams nothing on a link until asked (SRx_*).
                    self.conn.mav.request_data_stream_send(
                        self.conn.target_system or 1, self.conn.target_component or 1,
                        self._mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1)
                except Exception:
                    pass
                last_hb = time.time()
            m = self.conn.recv_match(blocking=True, timeout=0.5)
            if m is None:
                continue
            t = m.get_type()
            with self.lock:
                if t == 'HEARTBEAT' and self._px4.is_fc_autopilot(m.autopilot) and m.get_srcSystem() != 0:
                    self.armed = bool(m.base_mode & 0x80)
                    self.ever_armed |= self.armed
                    if m.autopilot == self._px4.AUTOPILOT_PX4:
                        self.mode = self._px4.px4_mode_name(m.custom_mode)
                    else:
                        try:
                            self.mode = self._mavutil.mode_string_v10(m)
                        except Exception:
                            self.mode = f"MODE_{m.custom_mode}"
                elif t == 'GLOBAL_POSITION_INT':
                    self.alt = m.relative_alt / 1000.0
                    if self.armed:
                        self.max_alt = max(self.max_alt, self.alt)
                elif t == 'EXTENDED_SYS_STATE':
                    self.landed_state = m.landed_state
                    self.got_land_state = True
                    if m.landed_state in (2, 3, 4):     # IN_AIR, TAKEOFF, LANDING
                        self.ever_airborne = True
                elif t == 'STATUSTEXT':
                    self.texts.append(m.text)

    def airborne(self):
        """Flew, per the land detector; altitude only where the autopilot reports
        no land state (ArduCopter), as drone_sdk itself falls back."""
        if self.got_land_state:
            return self.ever_airborne
        return self.max_alt >= 0.5

    def wait_for_heartbeat(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if self.mode is not None:
                return True
            time.sleep(0.5)
        return False

    def wait_ready(self, timeout=120):
        """Until the autopilot has a position estimate (GLOBAL_POSITION_INT flowing)."""
        end = time.time() + timeout
        while time.time() < end:
            m = self.conn.messages.get('GLOBAL_POSITION_INT')
            if m is not None and m.lat != 0:
                return True
            time.sleep(1)
        return False

    def set_params(self, params):
        """Python ints go as INT32, floats as REAL32: PX4 rejects a type mismatch
        ("param types mismatch") and keeps its default. PX4 carries an INT32 in
        PARAM_SET's float field bit for bit; ArduPilot takes the numeric value."""
        import struct
        ml = self._mavutil.mavlink
        mav = self.conn.mav
        for name, value in params.items():
            if isinstance(value, int) and self.autopilot == "px4":
                wire = struct.unpack('<f', struct.pack('<i', value))[0]
                ptype = ml.MAV_PARAM_TYPE_INT32
            else:
                wire, ptype = float(value), ml.MAV_PARAM_TYPE_REAL32
            mav.param_set_send(self.conn.target_system or 1, self.conn.target_component or 1,
                               name.encode(), wire, ptype)
            time.sleep(0.2)

    def stop(self):
        self._stop = True


def launch_px4(px4_dir: Path, workdir: Path):
    build = px4_dir / "build" / "px4_sitl_default"
    env = dict(os.environ, PX4_SYS_AUTOSTART="10040", PX4_SIMULATOR="sihsim",
               PX4_HOME_LAT=str(HOME[0]), PX4_HOME_LON=str(HOME[1]), PX4_HOME_ALT=str(HOME[2]),
               HEADLESS="1")
    rootfs = workdir / "px4_rootfs"
    rootfs.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(build / "bin" / "px4"), "-d", str(build / "etc")],
                            cwd=rootfs, env=env, stdout=open(workdir / "px4.log", "w"),
                            stderr=subprocess.STDOUT)
    # PX4 SITL: onboard link sends to udp 14540, GCS link to udp 14550.
    return proc, "udpin:0.0.0.0:14540", "udpin:0.0.0.0:14550"


def launch_ardupilot(binary: Path, defaults: Path, workdir: Path):
    home = f"{HOME[0]},{HOME[1]},{HOME[2]},0"
    proc = subprocess.Popen([str(binary), "--model", "+", "--speedup", "1", "-I0",
                             "--defaults", str(defaults), "--home", home],
                            cwd=workdir, stdout=open(workdir / "ardupilot.log", "w"),
                            stderr=subprocess.STDOUT)
    time.sleep(2)
    # SITL waits for a client on SERIAL0 (tcp 5760) before it boots, and only
    # then listens on SERIAL1 (5762): the truth link takes 5760, the code under
    # test 5762.
    return proc, "tcp:127.0.0.1:5762", "tcp:127.0.0.1:5760"


def write_config(cfg_dir: Path, cells: int):
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        "vehicle_type: quadcopter\n"
        "battery:\n"
        f"  cells: {cells}\n"
        "  capacity_mah: 5000\n"
        "  low_voltage_per_cell: 3.5\n"
        "  critical_voltage_per_cell: 3.3\n"
        "  allow_unknown_battery: true\n")


def run_mission(phases, text):
    import reasoning_loop as rl
    loop = rl.MissionLoop(conversation_id="sitl")     # what daemon.py does for a quadcopter
    mission = rl.Mission(mission_id=f"sitl-{int(time.time())}", phases=phases,
                         conversation_id="sitl", original_message=text)
    return loop.run(mission)


def settle(truth, timeout=60):
    """Back on the ground and disarmed before the next scenario."""
    end = time.time() + timeout
    while time.time() < end and (truth.armed or truth.alt > 0.3):
        time.sleep(0.5)
    time.sleep(2)


def scenarios(autopilot):
    out = []

    def check(name, phases, text, expect, before=None):
        out.append((name, phases, text, expect, before))

    check("arm test", [{"type": "arm_test", "hold_s": 3}], "do an arm test",
          lambda r, t: (r.success and t.ever_armed and not t.armed and not t.airborne(),
                        "armed, stayed on the ground, ended disarmed"))
    check("take off 1 m, then land",
          [{"type": "arm_and_takeoff", "altitude_m": 1}, {"type": "return_home", "alt_m": 1},
           {"type": "land"}], "take off 1 meter, then land",
          lambda r, t: (r.success and t.airborne() and 0.7 <= t.max_alt <= 2.0 and not t.armed,
                        "airborne per the land detector, reached ~1 m, landed, disarmed"))
    check("arm test right after a landing",
          [{"type": "arm_test", "hold_s": 2}], "do an arm test",
          lambda r, t: (r.success and t.ever_armed and not t.armed and not t.airborne()
                        and t.mode not in ("LAND",),
                        "armed again after landing, stayed down, ended disarmed"))
    if autopilot == "px4":
        def raise_min(truth):
            truth.set_params({'MIS_TAKEOFF_ALT': 5.0})
            time.sleep(1)
        check("1 m takeoff below MIS_TAKEOFF_ALT = 5 m is refused",
              [{"type": "arm_and_takeoff", "altitude_m": 1}, {"type": "land"}], "take off 1 meter",
              lambda r, t: (not r.success and not t.ever_armed and not t.airborne(),
                            "refused before arming, never climbed"),
              before=raise_min)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--autopilot", choices=["px4", "ardupilot"], required=True)
    ap.add_argument("--px4-dir", type=Path)
    ap.add_argument("--arducopter-bin", type=Path)
    ap.add_argument("--defaults", type=Path)
    ap.add_argument("--connect", help="SITL already running: MAVLink URL for the code under test")
    ap.add_argument("--truth", help="SITL already running: second MAVLink URL for the truth link")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix=f"sitl-{args.autopilot}-"))
    proc = None
    if args.connect:
        sdk_url, truth_url = args.connect, args.truth
    elif args.autopilot == "px4":
        proc, sdk_url, truth_url = launch_px4(args.px4_dir.expanduser(), work)
    else:
        proc, sdk_url, truth_url = launch_ardupilot(args.arducopter_bin.expanduser(),
                                                    args.defaults.expanduser(), work)

    cfg = work / "cfg"
    write_config(cfg, cells=4 if args.autopilot == "px4" else 3)
    os.environ["COYBOT_SDK_SERIAL_PORT"] = sdk_url
    os.environ["COYBOT_SDK_CONFIG_DIR"] = str(cfg)

    truth = Truth(truth_url, args.autopilot)
    results = []
    try:
        if not truth.wait_for_heartbeat(90):
            print(f"FAIL: no heartbeat from the {args.autopilot} SITL (logs in {work})")
            return 2
        truth.set_params(PX4_PARAMS if args.autopilot == "px4" else ARDUPILOT_PARAMS)
        print("waiting for the simulated autopilot's position estimate...")
        if not truth.wait_ready(180):
            print(f"FAIL: no position estimate from the SITL (logs in {work})")
            return 2
        time.sleep(5)

        import drone_sdk   # reads COYBOT_SDK_CONFIG_DIR / COYBOT_SDK_SERIAL_PORT set above
        assert drone_sdk.DRONE_DIR == cfg.absolute(), drone_sdk.DRONE_DIR

        for name, phases, text, expect, before in scenarios(args.autopilot):
            settle(truth)
            if before:
                before(truth)
            truth.reset()
            t0 = time.time()
            try:
                result = run_mission(phases, text)
            except Exception as e:
                result = type("R", (), {"success": False, "failure_reason": f"exception: {e}"})()
            time.sleep(3)   # let the truth link see the end state
            ok, meaning = expect(result, truth)
            results.append((name, ok, meaning, result, truth.max_alt, truth.ever_armed,
                            truth.armed, truth.mode, time.time() - t0, list(truth.texts)))
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {meaning} | mission={result.success} "
                  f"reason={getattr(result, 'failure_reason', None)} max_alt={truth.max_alt:.2f} "
                  f"ever_armed={truth.ever_armed} armed_now={truth.armed} mode={truth.mode}")
            if not ok and truth.texts:
                print("    autopilot said: " + " | ".join(dict.fromkeys(truth.texts))[:600])
    finally:
        truth.stop()
        try:
            import drone_sdk
            drone_sdk.disconnect()
        except Exception:
            pass
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()

    passed = sum(1 for r in results if r[1])
    print(f"\n{args.autopilot}: {passed}/{len(results)} scenarios passed (SITL logs: {work})")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
