"""Top-level e2e runner for all 4 drone types.

    python -m e2e.run_e2e --type all --tier fast    # CI on every push, no AWS/sim/phone
    python -m e2e.run_e2e --type all --tier live     # nightly, against Presidio Sim + real AWS

Fast tier = the vehicle-behavior regression gate (reusing drone/training/sim_validate.py
and the existing drone/sim/tests/test_vehicle_class.py + drone/common/tests/
test_l5_parity.py pytest suites — covers quad/rover/fixed-wing kinematics) PLUS a mocked
app-contract check per type (harness/mock_cloud.py, harness/mock_rover_converse.py — no AWS
needed). Live tier is the full-stack app-contract check against the real cloud + a drone
already brought up in Presidio Sim (see README.md) or on real hardware.

Must be run with this package importable, i.e. from the eco repo root (with `pip install
-e .` already done, so `drone`/`control` resolve as installed packages):
    cd eco && python -m e2e.run_e2e ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .harness import daemon_drones, phrover
from .harness.fixtures import ALL_TYPES, DAEMON_TYPES

_E2E_DIR = Path(__file__).resolve().parent
_ECO_DIR = _E2E_DIR.parent


def _run_behavior_gate() -> dict[str, Any]:
    """Vehicle kinematics regression gate — pure Python, no GPU/AWS/phone needed."""
    checks: dict[str, dict] = {}

    sv_out = _E2E_DIR / "_sim_validate_report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "drone.training.sim_validate", "--out", str(sv_out)],
        cwd=_ECO_DIR, capture_output=True, text=True)
    checks["sim_validate"] = {
        "ok": proc.returncode == 0,
        "detail": "\n".join(proc.stdout.splitlines()[-6:] + proc.stderr.splitlines()[-6:]),
    }

    for name, path in [
        ("test_vehicle_class", "drone/sim/tests/test_vehicle_class.py"),
        ("test_l5_parity", "drone/common/tests/test_l5_parity.py"),
    ]:
        proc = subprocess.run([sys.executable, "-m", "pytest", path, "-q"],
                              cwd=_ECO_DIR, capture_output=True, text=True)
        checks[name] = {"ok": proc.returncode == 0,
                        "detail": "\n".join(proc.stdout.splitlines()[-6:])}

    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}


def _run_type(vehicle_type: str, tier: str) -> dict[str, Any]:
    if vehicle_type in DAEMON_TYPES:
        fn = daemon_drones.run_fast if tier == "fast" else daemon_drones.run_live
        return fn(vehicle_type)
    fn = phrover.run_fast if tier == "fast" else phrover.run_live
    return fn()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", default="all", choices=["all", *ALL_TYPES])
    ap.add_argument("--tier", default="fast", choices=["fast", "live"])
    ap.add_argument("--report", default=str(_E2E_DIR / "scorecard.json"))
    args = ap.parse_args(argv)

    types = list(ALL_TYPES) if args.type == "all" else [args.type]
    scorecard: dict[str, Any] = {
        "tier": args.tier, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "types": {},
    }

    ok = True
    if args.tier == "fast":
        gate = _run_behavior_gate()
        scorecard["behavior_gate"] = gate
        ok = ok and gate["ok"]

    for vehicle_type in types:
        result = _run_type(vehicle_type, args.tier)
        scorecard["types"][vehicle_type] = result
        ok = ok and result["ok"]
        status = "PASS" if result["ok"] else "FAIL"
        print(f"[{status}] {vehicle_type} ({args.tier}): {result.get('reason', '')}")

    scorecard["ok"] = ok
    Path(args.report).write_text(json.dumps(scorecard, indent=2))
    print(f"\nscorecard -> {args.report}")
    print("OVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
