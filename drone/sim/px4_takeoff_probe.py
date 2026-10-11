"""Which MAV_CMD_NAV_TAKEOFF encoding does a PX4 actually fly? (SITL, raw MAVLink.)

The PX4 test aircraft and the PX4 v1.17 SITL both ACKed our takeoff as ACCEPTED, did not
climb, and auto-disarmed 10 s later. This arms in Hold and tries one encoding
per run, recording nav_state, altitude and the autopilot's text.

    python3 drone/sim/px4_takeoff_probe.py --variant long_nan --connect udpin:0.0.0.0:14540
"""
import argparse
import math
import struct
import time

from pymavlink import mavutil

NAN = float('nan')
NAV = {1: 'MANUAL', 2: 'ALTCTL', 3: 'POSCTL', 4: 'MISSION', 5: 'LOITER', 6: 'RTL', 14: 'OFFBOARD',
       15: 'STAB', 17: 'TAKEOFF', 18: 'LAND', 0: 'MANUAL'}


def px4_mode(custom_mode):
    main, sub = (custom_mode >> 16) & 0xFF, (custom_mode >> 24) & 0xFF
    return f"{main}.{sub}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect", default="udpin:0.0.0.0:14540")
    ap.add_argument("--variant", required=True,
                    choices=["long_nan", "long_zero", "long_ourparams", "int_global", "int_relative",
                             "mode_takeoff_then_arm"])
    ap.add_argument("--climb", type=float, default=1.0)
    a = ap.parse_args()

    m = mavutil.mavlink_connection(a.connect, source_system=255)
    hb = m.wait_heartbeat(timeout=30)
    print("heartbeat", hb.autopilot, px4_mode(hb.custom_mode))
    m.mav.request_data_stream_send(m.target_system, m.target_component, mavutil.mavlink.MAV_DATA_STREAM_ALL, 5, 1)
    gpi = None
    end = time.time() + 20
    while time.time() < end and gpi is None:
        gpi = m.recv_match(type='GLOBAL_POSITION_INT', blocking=True, timeout=1)
    amsl, rel = gpi.alt / 1000.0, gpi.relative_alt / 1000.0
    print(f"start: amsl={amsl:.2f} rel={rel:.2f}")
    target_amsl = amsl + a.climb

    def cmd_long(cmd, *p):
        m.mav.command_long_send(m.target_system, m.target_component, cmd, 0, *p)

    # Hold, then arm
    if a.variant != "mode_takeoff_then_arm":
        cmd_long(176, 1, 4, 3, 0, 0, 0, 0)          # AUTO.LOITER
        time.sleep(1.5)
    else:
        cmd_long(176, 1, 4, 2, 0, 0, 0, 0)          # AUTO.TAKEOFF
        time.sleep(1.5)
    cmd_long(400, 1, 0, 0, 0, 0, 0, 0)
    t_arm = time.time()
    time.sleep(1.0)

    if a.variant == "long_nan":
        cmd_long(22, NAN, 0, 0, NAN, NAN, NAN, target_amsl)
    elif a.variant == "long_zero":
        cmd_long(22, 0, 0, 0, 0, 0, 0, target_amsl)
    elif a.variant == "long_ourparams":
        cmd_long(22, -1.0, 0, 0, NAN, NAN, NAN, target_amsl)
    elif a.variant == "int_global":
        m.mav.command_int_send(m.target_system, m.target_component, 0, 22, 0, 0,
                               NAN, 0, 0, NAN, int(gpi.lat), int(gpi.lon), target_amsl)
    elif a.variant == "int_relative":
        m.mav.command_int_send(m.target_system, m.target_component, 3, 22, 0, 0,
                               NAN, 0, 0, NAN, int(gpi.lat), int(gpi.lon), a.climb)
    t_cmd = time.time()

    max_rel, modes, texts, acks = rel, [], [], []
    end = time.time() + 15
    while time.time() < end:
        x = m.recv_match(blocking=True, timeout=0.5)
        if x is None:
            continue
        t = x.get_type()
        if t == 'HEARTBEAT' and x.autopilot == 12:
            md = (px4_mode(x.custom_mode), bool(x.base_mode & 128))
            if not modes or modes[-1] != md:
                modes.append(md)
                print(f"  +{time.time() - t_arm:4.1f}s mode={md[0]} armed={md[1]}")
        elif t == 'GLOBAL_POSITION_INT':
            max_rel = max(max_rel, x.relative_alt / 1000.0)
        elif t == 'STATUSTEXT':
            texts.append(x.text)
            print(f"  +{time.time() - t_arm:4.1f}s TEXT {x.text}")
        elif t == 'COMMAND_ACK':
            acks.append((x.command, x.result))
            print(f"  +{time.time() - t_arm:4.1f}s ACK cmd={x.command} result={x.result}")
    cmd_long(400, 0, 21196, 0, 0, 0, 0, 0)   # disarm at the end, whatever happened
    climbed = max_rel - rel
    print(f"RESULT variant={a.variant} climbed={climbed:.2f} m (asked {a.climb}) "
          f"{'FLEW' if climbed > 0.5 else 'NO CLIMB'}")


if __name__ == "__main__":
    main()
