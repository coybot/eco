"""'Do an arm test' reaches the drone, and the planner knows which autopilot it is talking to.

An operator asked the PX4 test aircraft to "Do an Arm test" and got a refusal plus ArduPilot
parameter advice: the planner had no arm-only phase and was never told the
autopilot. Run: python3 -m pytest aws/src/test_arm_test_prompt.py -v
"""
import conversations as c


def test_planner_prompt_offers_the_arm_test_phase_and_an_example():
    p = c.MISSION_SYSTEM_PROMPT
    assert '{"type": "arm_test"' in p
    assert 'User: "Do an arm test"' in p
    assert "never takes off" in p.lower() or "never take off" in p.lower()


def test_prompt_exempts_the_arm_test_from_the_always_takeoff_rule():
    assert "an arm test is\n  the exception" in c.MISSION_SYSTEM_PROMPT


def test_prompt_forbids_guessing_autopilot_specific_advice():
    p = c.MISSION_SYSTEM_PROMPT
    assert "SRx_EXT_STAT" in p and "unless DRONE STATE names that autopilot" in p


def _state(**kw):
    import time
    return dict({'lastUpdate': time.time() * 1000, 'armed': False}, **kw)


def test_drone_state_names_the_autopilot_when_known():
    assert "runs PX4" in c.describe_drone_state(_state(autopilot='px4'))
    assert "runs ArduPilot" in c.describe_drone_state(_state(autopilot='ardupilot'))


def test_drone_state_says_unknown_for_an_older_drone():
    assert "autopilot unknown" in c.describe_drone_state(_state())


def test_a_successful_mission_reply_says_what_happened_not_just_done():
    msg = c.success_message({'success': True, 'total_phases': 1, 'phases_completed': 1,
                             'findings': ['Arm test passed: armed, held 3 s, then disarmed']})
    assert msg == "Done. Arm test passed: armed, held 3 s, then disarmed."


def test_code_execution_replies_are_unchanged():
    assert c.success_message({'success': True, 'stdout': 'battery 82%'}) == "Done! battery 82%"
    assert c.success_message({'success': True}) == "Done!"


def test_a_successful_flight_reply_has_no_step_count():
    msg = c.success_message({'success': True, 'total_phases': 3, 'phases_completed': 3, 'findings': []})
    assert msg == "Done."
