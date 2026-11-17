"""The sim's command namespace must accept the drone_sdk calls the cloud planner
writes. 2026-11-17: Claude's code did `ok = takeoff(2); if not ok: battery_status()`
and the sim drone failed with NameError, because sim takeoff() returned None and
battery_status did not exist there (the real drone_sdk has both)."""
import sim_control


class _Engine:
    """Moves the vehicle straight to each goal, so _goto_xyz returns at once."""
    def __init__(self):
        self.p = [0.0, 0.0, 0.0]

    def state(self, did):
        return {"position": list(self.p), "yaw": 0.0}

    def set_goal(self, did, xyz):
        self.p = list(xyz)


def _ns():
    ns, _, _ = sim_control.build_namespace(_Engine(), "sim-t", "quadcopter", "c", {}, {"v": False})
    return ns


def test_takeoff_and_land_report_success_like_drone_sdk():
    ns = _ns()
    assert ns["takeoff"](2) is True
    assert ns["land"]() is True


def test_battery_calls_exist_and_allow_takeoff():
    ns = _ns()
    status = ns["battery_status"](planned_alt_m=2)
    assert status["can_takeoff"] is True and status["remaining"] == 100
    assert ns["get_battery"]()["source"] == "sim"


def test_planner_code_from_the_failed_e2e_run_executes():
    code = ("takeoff_result = takeoff(2)\n"
            "if not takeoff_result:\n"
            "    print('Takeoff failed.')\n"
            "    battery_status()\n"
            "land()\n")
    ns = _ns()
    exec(compile(code, "<sim-t>", "exec"), ns, ns)  # noqa: S102
    assert ns["takeoff_result"] is True
