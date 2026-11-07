#!/usr/bin/env python3
"""Live check of the tilt-rotor VTOL airframe (fw_spawn airframe="vtol").

Flies one full sortie against a headless Godot and checks each regime:
spawn landed -> vertical take-off -> hover (holds position, yaws on the spot)
-> transition (rotors tilt, speed builds no faster than the tilt allows)
-> wing-borne cruise -> stop() back-transitions to a hover -> vertical landing.
Also checks the fixed-wing default is untouched when no airframe is given.

Runs headless: telemetry only, no camera.

Usage:
    python3 tests/test_vtol.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(SIM_DIR.parents[1] / "rover" / "sim"))
sys.path.insert(0, str(SIM_DIR.parents[0] / "common"))

from depot_client import DepotClient  # noqa: E402
from fw_eval import launch_flightline  # noqa: E402

RID = "vtol-test"
FW = "fw-default-test"
PORT = 9985


def _hold(c, airspeed, yaw_rate, climb, seconds):
    """Hold one command, returning the fw_state trace (10 Hz)."""
    trace = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        c.fw_drive(RID, airspeed, yaw_rate, climb)
        time.sleep(0.1)
        st = c.fw_state(RID)
        st["t"] = time.time()   # wall clock: each sample costs more than the 0.1 s sleep
        trace.append(st)
    return trace


def _horiz(a, b):
    return math.hypot(a["position"][0] - b["position"][0], a["position"][1] - b["position"][1])


def main() -> int:
    failures: list[str] = []

    def check(ok: bool, msg: str) -> None:
        print(("  ok  " if ok else "  FAIL ") + msg)
        if not ok:
            failures.append(msg)

    proc = launch_flightline(seed=0, port=PORT, gui=False, env="flightline")
    try:
        c = DepotClient(port=PORT)

        # --- spawn on the ground ------------------------------------------
        c.fw_spawn(RID, (0.0, 0.0, 0.0), 0.0, airframe="vtol")
        time.sleep(0.3)
        s = c.fw_state(RID)
        print(f"spawn    : mode={s['flight_mode']} z={s['position'][2]:.2f} airframe={s['airframe']}")
        check(s["airframe"] == "vtol", "airframe reported as vtol")
        check(s["landed"] and s["flight_mode"] == "landed", "spawned at ground level -> landed")
        check(not s["crashed"], "ground spawn is not a crash")

        # Landed: forward/yaw commands alone must not move it (no taxiing).
        tr = _hold(c, 10.0, 0.5, 0.0, 1.5)
        check(_horiz(tr[-1], s) < 0.05 and tr[-1]["landed"], "landed ignores airspeed/yaw without a climb")

        # --- vertical take-off ---------------------------------------------
        tr = _hold(c, 0.0, 0.0, 2.5, 8.0)
        z = tr[-1]["position"][2]
        print(f"takeoff  : z={z:.1f} m after 8 s at +2.5 m/s, drift {_horiz(tr[-1], s):.2f} m")
        check(not tr[-1]["landed"], "climb command takes off")
        check(z > 12.0, f"vertical climb reaches altitude (z={z:.1f} m)")
        check(_horiz(tr[-1], s) < 0.5, "take-off is vertical (no forward drift)")
        check(max(abs(t["pitch"]) for t in tr) < 0.05, "fuselage stays level climbing in hover")

        # --- hover and pirouette -------------------------------------------
        a = tr[-1]
        tr = _hold(c, 0.0, 1.0, 0.0, 3.0)
        b = tr[-1]
        dyaw = abs(math.remainder(b["yaw"] - a["yaw"], math.tau))
        print(f"hover    : yawed {math.degrees(dyaw):.0f} deg in 3 s, moved {_horiz(a, b):.2f} m, "
              f"mode={b['flight_mode']}")
        check(dyaw > math.radians(90), "yaws on the spot in hover")
        check(_horiz(a, b) < 0.5, "hover holds position while yawing")
        check(b["flight_mode"] == "hover", "reports hover")
        check(max(abs(t["roll"]) for t in tr) < 0.05, "pirouette stays wings-level")

        # --- transition to cruise -------------------------------------------
        tr = _hold(c, 18.0, 0.0, 0.0, 12.0)
        speeds = [t["airspeed"] for t in tr]
        t0 = tr[0]["t"] - 0.1
        t_wing = next((t["t"] - t0 for t in tr if t["flight_mode"] == "cruise"), None)
        print(f"transit  : wing-borne after {t_wing:.1f} s, final {speeds[-1]:.1f} m/s, "
              f"tilt {math.degrees(tr[-1]['tilt']):.0f} deg")
        check(t_wing is not None, "reaches wing-borne cruise")
        # 0 -> 13 m/s at 3 m/s^2 is >= 4.3 s; anything faster is the cap not working.
        check(t_wing is not None and t_wing >= 4.0, "transition takes real time, not instant")
        check(abs(speeds[-1] - 18.0) < 0.5, "settles at commanded cruise speed")
        check(abs(tr[-1]["tilt"] - math.pi / 2) < 0.02, "rotors fully forward in cruise")
        mid = [t for t in tr if 0.05 < t["tilt"] < math.pi / 2 - 0.05]
        check(any(t["flight_mode"] == "transition" for t in mid), "reports transition while tilting")

        # --- cruise turn behaves like the fixed-wing ------------------------
        tr = _hold(c, 18.0, 0.5, 0.0, 3.0)
        roll = max(abs(t["roll"]) for t in tr)
        print(f"turn     : peak bank {math.degrees(roll):.0f} deg")
        check(roll > math.radians(20), "banks into a turn when wing-borne")
        tr = _hold(c, 18.0, 1.2, 0.0, 1.0)
        rates = [abs(math.remainder(tr[i + 1]["yaw"] - tr[i]["yaw"], math.tau))
                 / (tr[i + 1]["t"] - tr[i]["t"]) for i in range(len(tr) - 1)]
        check(max(rates) < 0.75, f"cruise yaw rate bank-limited (peak {max(rates):.2f} rad/s)")

        # --- stop() back-transitions to a hover -----------------------------
        c.fw_stop(RID)
        tr = []
        for _ in range(140):
            time.sleep(0.1)
            st = c.fw_state(RID)
            st["t"] = time.time()
            tr.append(st)
        b = tr[-1]
        print(f"stop     : airspeed {b['airspeed']:.2f} m/s, tilt {math.degrees(b['tilt']):.0f} deg, "
              f"mode={b['flight_mode']}")
        check(b["airspeed"] < 0.05 and b["flight_mode"] == "hover", "fw_stop brings it to a hover")
        decel = max((tr[i]["airspeed"] - tr[i + 1]["airspeed"]) / (tr[i + 1]["t"] - tr[i]["t"])
                    for i in range(len(tr) - 1))
        check(decel < 3.0, f"back-transition decel is gradual (peak {decel:.1f} m/s^2)")

        # --- vertical landing -------------------------------------------------
        a = b
        tr = _hold(c, 0.0, 0.0, -1.5, 20.0)
        b = tr[-1]
        print(f"land     : z={b['position'][2]:.2f} mode={b['flight_mode']} drift {_horiz(a, b):.2f} m")
        check(b["landed"] and not b["crashed"], "descends and lands without crashing")
        check(b["position"][2] < 0.2, "rests on the ground")
        check(min(t["climb_rate"] for t in tr) > -1.6, "rotor-borne sink rate limited")
        ev = [e.get("kind") for e in c.fw_events(RID)]
        check("vtol_takeoff" in ev and "vtol_touchdown" in ev, "take-off and touchdown events logged")
        check("envelope_protection" not in ev, "landing is not an envelope intervention")

        # --- the fixed-wing default is unchanged ----------------------------
        c.fw_spawn(FW, (0.0, 50.0, 30.0), 0.0)
        time.sleep(0.5)
        f = c.fw_state(FW)
        print(f"default  : airframe={f['airframe']} airspeed={f['airspeed']:.1f}")
        check(f["airframe"] == "fixedwing" and f["airspeed"] >= 12.0,
              "fw_spawn without airframe is still the fixed-wing at >= 12 m/s")

        c.fw_despawn(RID)
        c.fw_despawn(FW)
        c.close()
    finally:
        proc.stop()

    print()
    if failures:
        print(f"VTOL FAIL — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VTOL PASS — take-off, hover, transition, cruise, back-transition and landing verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
