"""Ishmael — natural-language test director for the Coybot Isaac sim.

Turns one English sentence ("2 rovers and 3 quadcopters search the office for a
chair and send a picture to the app") into a fully set-up simulated test: a
spawned fleet in a chosen scene, a dispatched mission, and a mobile client that
receives the result.

Modules
-------
- ``nlp``      : parse_test_spec(text) -> TestSpec (local vLLM + rule fallback)
- ``director`` : run(spec) — roster -> register -> launch fleet -> dispatch -> collect
- ``cli``      : ``python -m ishmael.cli "<sentence>"``

This package lives alongside the existing sim host (``eco/drone/sim``) and reuses
``fleet.parse_roster`` / ``fleet.register_fleet`` and ``launch_fleet.py``.
"""

from __future__ import annotations

from .nlp import TestSpec, VehicleReq, parse_test_spec

__all__ = ["TestSpec", "VehicleReq", "parse_test_spec"]
