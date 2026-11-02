"""A fake ArduPilot vehicle behind drone_sdk's helper surface, for the L5 adapter tests.

``FakeArduPilot`` stands in for the ``drone_sdk`` module as ardupilot_l5 uses it
(``_mav_send``, ``_fc_cached``, ``_pump``, ``flight_home``, ``pilot_control``,
``_set_mode_confirmed``, the speed/altitude limits) and integrates the setpoints
it is sent on a simulated clock, so a closed loop can be flown with no pymavlink
and no hardware.

Its MAVLink semantics, written from the spec rather than from ardupilot_l5:
  * LOCAL_POSITION_NED is north/east/down of the EKF origin, which is NOT the
    takeoff point (``home_ned`` here, deliberately offset).
  * ATTITUDE.yaw is the heading, 0 = north, clockwise.
  * SET_POSITION_TARGET_LOCAL_NED in MAV_FRAME_BODY_OFFSET_NED is velocity
    (x fwd, y right, z down) relative to the heading; yaw_rate is clockwise.
    A copter flies all three axes; a rover takes vx as speed and yaw_rate as
    turn rate. Only obeyed armed and in GUIDED.
"""
import math
import types

COPTER_GUIDED, ROVER_GUIDED = 4, 15
ARMED_GUIDED_BASE = 128 | 1
M_PER_DEG = 111320.0


class Msg(types.SimpleNamespace):
    def __init__(self, src=1, **fields):
        super().__init__(**fields)
        self._src = src

    def get_srcSystem(self):
        return self._src


class _Mav:
    def __init__(self, veh):
        self.veh = veh

    def set_position_target_local_ned_send(self, time_boot_ms, target_system, target_component,
                                           frame, mask, x, y, z, vx, vy, vz, afx, afy, afz,
                                           yaw, yaw_rate):
        self.veh.setpoints.append(dict(frame=frame, mask=mask, vx=vx, vy=vy, vz=vz,
                                       yaw_rate=yaw_rate, target=(target_system, target_component)))

    def command_long_send(self, target_system, target_component, command, confirmation, *params):
        self.veh.commands.append((command, params))


class _Conn:
    def __init__(self, veh, sysid):
        self.target_system, self.target_component = sysid, 1
        self.mav = _Mav(veh)


class FakeArduPilot:
    MAX_VELOCITY = 5.0
    MAX_ALTITUDE = 20.0
    MIN_ALTITUDE = 0.5
    MAX_YAW_RATE = 45.0

    def __init__(self, kind="quad", home_ned=(3.0, -2.0, 0.0), pos_ned=None, heading=0.0,
                 armed=True, guided=True, sysid=1, origin_latlon=(37.0, -122.0)):
        self.kind = kind
        self.mav_type = 2 if kind == "quad" else 10
        self.guided_mode = COPTER_GUIDED if kind == "quad" else ROVER_GUIDED
        self.sysid = sysid
        self.home_ned = list(home_ned)
        self.ned = list(pos_ned if pos_ned is not None else home_ned)
        self.vel_ned = [0.0, 0.0, 0.0]
        self.heading = heading
        self.armed = armed
        self.custom_mode = self.guided_mode if guided else 5   # 5 = LOITER (copter)
        self.origin = origin_latlon
        self.t = 100.0
        self.cmd = None
        self.setpoints, self.commands, self.modes = [], [], []
        self.pilot = None
        self.ekf_flags = 2 | 8 | 16 | 1
        self.ekf_var = 0.1
        self.stale = set()          # message types the FC has stopped sending
        self.conn = _Conn(self, sysid)

    # ---------------------------------------------------------------- geometry
    def latlon(self, n, e):
        lat0, lon0 = self.origin
        return lat0 + n / M_PER_DEG, lon0 + e / (M_PER_DEG * math.cos(math.radians(lat0)))

    def enu(self):
        """Truth: ENU metres from the takeoff point."""
        n0, e0, d0 = self.home_ned
        return [self.ned[1] - e0, self.ned[0] - n0, -(self.ned[2] - d0)]

    # ---------------------------------------------------------------- drone_sdk surface
    def _mav_send(self, fn):
        return fn(self.conn)

    def _pump(self, seconds=0.2):
        pass

    def clock(self):
        return self.t

    def _fc_cached(self, msg_type, max_age=1.5):
        if msg_type in self.stale:
            return None
        tb = int(self.t * 1000)
        if msg_type == "HEARTBEAT":
            base = (128 if self.armed else 0) | 1
            return Msg(self.sysid, type=self.mav_type, autopilot=3, base_mode=base,
                       custom_mode=self.custom_mode)
        if msg_type == "LOCAL_POSITION_NED":
            return Msg(self.sysid, time_boot_ms=tb, x=self.ned[0], y=self.ned[1], z=self.ned[2],
                       vx=self.vel_ned[0], vy=self.vel_ned[1], vz=self.vel_ned[2])
        if msg_type == "ATTITUDE":
            return Msg(self.sysid, time_boot_ms=tb, roll=0.0, pitch=0.0, yaw=self.heading)
        if msg_type == "GLOBAL_POSITION_INT":
            lat, lon = self.latlon(self.ned[0], self.ned[1])
            return Msg(self.sysid, time_boot_ms=tb, lat=int(round(lat * 1e7)), lon=int(round(lon * 1e7)),
                       relative_alt=int(round(-(self.ned[2] - self.home_ned[2]) * 1000)))
        if msg_type == "EKF_STATUS_REPORT":
            return Msg(self.sysid, flags=self.ekf_flags, velocity_variance=self.ekf_var,
                       pos_horiz_variance=self.ekf_var, pos_vert_variance=0.1, compass_variance=0.1,
                       terrain_alt_variance=0.0)
        return None

    def flight_home(self):
        # Same shape as drone_sdk.flight_home(): a {"lat", "lon"} dict.
        lat, lon = self.latlon(self.home_ned[0], self.home_ned[1])
        return {"lat": lat, "lon": lon}

    def pilot_control(self):
        return self.pilot

    def is_armed(self):
        return self.armed

    def clear_abort(self):
        pass

    def release_camera(self):
        pass

    def _set_mode_confirmed(self, mode, timeout=8):
        self.modes.append(mode)
        self.custom_mode = {"GUIDED": self.guided_mode, "RTL": 6 if self.kind == "quad" else 11,
                            "LAND": 9, "HOLD": 4}.get(mode, -1)
        return True

    def land(self):
        self.modes.append("LAND")
        return True

    # ---------------------------------------------------------------- physics
    def advance(self, dt):
        """Integrate the newest setpoint for dt seconds of simulated time."""
        sp = self.setpoints[-1] if self.setpoints else None
        obey = sp is not None and self.armed and self.custom_mode == self.guided_mode
        if obey and sp["frame"] == 9:
            c, s = math.cos(self.heading), math.sin(self.heading)
            if self.kind == "quad":
                fwd, right, down = sp["vx"], sp["vy"], sp["vz"]
            else:
                fwd, right, down = sp["vx"], 0.0, 0.0
            self.vel_ned = [fwd * c - right * s, fwd * s + right * c, down]
            self.heading = (self.heading + sp["yaw_rate"] * dt) % (2 * math.pi)
        else:
            self.vel_ned = [0.0, 0.0, 0.0]
        for i in range(3):
            self.ned[i] += self.vel_ned[i] * dt
        if self.kind == "rover":
            self.ned[2] = self.home_ned[2]
        self.t += dt
