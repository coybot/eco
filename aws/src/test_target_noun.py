"""_target_noun: the detector noun a tasking is about.

Run: python3 -m pytest aws/src/test_target_noun.py -v
"""
import conversations as c


def test_people_is_person():
    assert c._target_noun("find all people in the area") == "person"
    assert c._target_noun("look for a person by the gate") == "person"


def test_a_count_of_aircraft_is_not_the_target():
    assert c._target_noun("send 2 aircraft to find people") == "person"
    assert c._target_noun("count the pickup trucks with three aircraft") == "pickup truck"
    assert c._target_noun("send 2 aircraft to the area") == "target"
    # an aircraft is still a target when it is what is looked for
    assert c._target_noun("find the aircraft on the runway") == "aircraft"


def test_unknown_nouns_stay_target():
    assert c._target_noun("count the widgets") == "target"
