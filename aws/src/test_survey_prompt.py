"""What the planner is told about area surveys.

Observed 2026-10-14: "scan the area 10 meters in front of you and 5 meters to the
right" planned spacing_m 2 (15 photos) because the prompt said to pick a spacing
giving 10-30 photos when no unit was given - a crop survey asking for coverage got
a quarter of it, and the operator was never told. The prompt now defaults to 1 m and
requires the confirmation to state the photo count and spacing.

The live behaviour is the model's; these pin what it is told.

Run: python3 -m pytest aws/src/test_survey_prompt.py -v
"""
import conversations


def _prompt():
    return conversations.mission_system_prompt("quadcopter", None)


def test_a_survey_with_no_stated_unit_defaults_to_one_meter():
    p = _prompt()
    assert "use spacing_m 1" in p
    assert "do NOT pick a coarser spacing" in " ".join(p.split())
    assert "pick spacing_m so the whole area is about 10-30 photos" not in p


def test_the_confirmation_must_state_photo_count_and_spacing():
    flat = " ".join(_prompt().split())
    assert "must state the photo count" in flat
    assert "ceil(forward_m / spacing_m) x ceil(right_m / spacing_m)" in flat


def test_the_planner_knows_photos_are_tagged_and_does_not_plan_tagging_as_a_step():
    flat = " ".join(_prompt().split())
    assert "stamped with its capture time, GPS position and heading" in flat
    assert "never promise tagging as a separate step" in flat


def test_the_code_prompts_default_to_one_meter_and_know_scan_is_a_survey():
    for p in (conversations.QUADCOPTER_CODE_SYSTEM_PROMPT, conversations.ROVER_CODE_SYSTEM_PROMPT):
        assert "spacing_m=1 if no unit is given" in p
        assert "cover/map/survey/scan the field" in p


def test_the_code_prompt_treats_a_survey_as_a_flight_with_takeoff_and_landing():
    """Observed 2026-10-14 through the legacy (non-VLM) path: for "scan the area 10 meters in
    front of you and 5 meters to the right" the code model wrote only survey_rect(...) - rule 4
    forbade takeoff() unless asked to fly - and the aircraft "surveyed" from the ground."""
    flat = " ".join(conversations.QUADCOPTER_CODE_SYSTEM_PROMPT.split())
    assert "or asks to survey, scan, map or photograph an AREA" in flat
    assert "ALWAYS fly the whole sortie: arm(), takeoff(5)" in flat
    assert "print(survey_rect(...)), return_home(), land()" in flat
