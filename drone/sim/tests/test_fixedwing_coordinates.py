"""Guard the ENU <-> Godot convention that the sim scripts hand-inline everywhere.

Sim state is ENU: x=east, y=north, z=up. Godot is Y-up with -Z forward, so a
world position converts as Vector3(enu.x, enu.z, -enu.y). That expression is
written out by hand in ~15 places across fleet_manager.gd and
fixedwing_manager.gd rather than going through one helper, and a single
transposed component puts one vehicle type into a mirrored world -- invisible
until someone looks at a render, and cheap to catch here.

These tests read the GDScript as text on purpose. The conversion lives in
GDScript, which cannot be imported into Python, so the alternative is testing a
Python re-implementation that nothing actually runs.
"""

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "godot" / "scripts"

# Managers own world-space placement. fixedwing_visuals.gd is deliberately not
# here: its _to_local() permutes axes into the *model's* frame to point the
# mesh down its thrust axis, which is a different job with a different answer.
WORLD_SPACE_SCRIPTS = ("fleet_manager.gd", "fixedwing_manager.gd")

# Vector3 built from all three components of a single variable.
_VEC3 = re.compile(
    r"Vector3\(\s*(-?)\s*([\w.]+)\.([xyz])\s*,"
    r"\s*(-?)\s*([\w.]+)\.([xyz])\s*,"
    r"\s*(-?)\s*([\w.]+)\.([xyz])\s*\)"
)


_FUNC = re.compile(r"^\s*(?:static\s+)?func\s+(\w+)")


def _conversions(filename):
    """Yield (line_no, func_name, form, text) for each same-base Vector3."""
    path = SCRIPTS / filename
    assert path.exists(), f"{path} is missing; did the scripts move?"
    func = "<module>"
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        declared = _FUNC.match(line)
        if declared:
            func = declared.group(1)
        for m in _VEC3.finditer(line):
            s1, b1, c1, s2, b2, c2, s3, b3, c3 = m.groups()
            if b1 == b2 == b3:
                yield line_no, func, (s1 + c1, s2 + c2, s3 + c3), line.strip()


def _apply(form, vec):
    """Evaluate a parsed form like ('x', 'z', '-y') against a dict of components."""
    out = []
    for token in form:
        negate = token.startswith("-")
        value = vec[token.lstrip("-")]
        out.append(-value if negate else value)
    return tuple(out)


def test_enu_to_godot_mapping_is_uniform():
    """Every world-space conversion is the forward mapping.

    The inverse is legitimate only where a function says that is its job, i.e.
    a name ending in _to_enu. Accepting the inverse anywhere would let the two
    be swapped at a call site -- which mirrors the world just as thoroughly as
    a transposed component, and is exactly as quiet.
    """
    forward = ("x", "z", "-y")
    inverse = ("x", "-z", "y")

    found = []
    for filename in WORLD_SPACE_SCRIPTS:
        for line_no, func, form, text in _conversions(filename):
            found.append(form)
            expected = inverse if func.endswith("_to_enu") else forward
            direction = "Godot->ENU" if expected is inverse else "ENU->Godot"
            assert form == expected, (
                f"{filename}:{line_no} in {func}() converts with {form}, but a "
                f"{direction} conversion here must be {expected}:\n    {text}"
            )

    assert found, "no conversions found at all -- the regex or the scripts changed"
    forward_count = sum(1 for form in found if form == forward)
    assert forward_count >= 10, (
        "expected the forward ENU->Godot mapping to dominate; if placement was "
        "refactored behind a helper, this test should follow it there"
    )


def test_scalar_helper_agrees_with_the_inlined_form():
    """_enu_dir_to_godot() takes scalars, so the regex above cannot see it, but
    it is the one place the conversion is centralized -- keep it in step."""
    text = (SCRIPTS / "fixedwing_manager.gd").read_text()
    assert "return Vector3(east, up, -north)" in text, (
        "_enu_dir_to_godot no longer maps (east, north, up) -> (x, y, z) the same "
        "way the inlined Vector3(v.x, v.z, -v.y) conversions do"
    )


def test_forward_and_inverse_round_trip():
    """The two forms in use really are inverses, not two independent guesses."""
    enu = {"x": 10.0, "y": 20.0, "z": 5.0}          # 10m east, 20m north, 5m up

    godot = _apply(("x", "z", "-y"), enu)
    assert godot == (10.0, 5.0, -20.0), "up must land on Godot Y, north on -Z"

    back = _apply(("x", "-z", "y"), dict(zip("xyz", godot)))
    assert back == (enu["x"], enu["y"], enu["z"])


def test_altitude_is_the_enu_up_component():
    """Altitude and the envelope clamp read ENU z, not the Godot axis.

    The other half of the convention: state stays ENU and only the render
    converts. Anyone 'fixing' a mirrored world by rotating the stored state
    instead of the conversion would break this.
    """
    text = (SCRIPTS / "fixedwing_manager.gd").read_text()
    assert "st.altitude = st.position.z" in text
    assert "st.position.z = clamp(st.position.z, ALT_FLOOR_M, ALT_CEILING_M)" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
