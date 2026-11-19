"""Run the existing scenes and vehicles after a phase and record the result.

    python3 drone/sim/docking/run_regression.py --out drone/sim/docking/results/phaseN

1. pytest: drone/sim/tests and drone/common/tests (the existing suites + the new ones).
2. The CI fast tier: python3 -m eco.e2e.run_e2e --type {quadcopter,rover,fixedwing} --tier fast
   (needs the repo importable as package ``eco``; this script makes a temporary symlink).
3. Every seed scenario (scenarios/, scenarios_fw/, scenarios_indoor/) on the default
   kinematic backend, compared with the numbers the same scenarios give on the base
   commit (``--base`` worktree, default: none -> just records them), plus the quad and
   fixed-wing scenarios on the opt-in rigid-body backend as a smoke test.
Writes regression.md and regression.json; exits 1 if pytest or e2e fails, or a kinematic
scenario result differs from the base.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.dirname(HERE)
ECO = os.path.dirname(os.path.dirname(SIM))

SCEN_CODE = r"""
import sys, json, glob
sys.path.insert(0, sys.argv[1])
from scenario import Scenario, ScenarioRunner
out = {}
backend = sys.argv[2]
for f in sorted(glob.glob(sys.argv[1] + '/scenarios*/*.yaml')):
    scn = Scenario.from_yaml(f)
    kinds = {m['type'] if isinstance(m, dict) else str(m) for m in scn.team}
    if backend == 'dynamic' and not any(k in ('quad', 'quadcopter', 'fixedwing', 'fw') for k in kinds):
        continue
    kw = {} if backend == 'kinematic_legacy' else {'backend': 'dynamic' if backend == 'dynamic' else 'kinematic'}
    r = ScenarioRunner(scn, **kw).run()
    out[f.split('/')[-2] + '/' + f.split('/')[-1]] = {k: r[k] for k in ('t_end', 'mission_complete', 'targets_found', 'targets_total', 'collisions')}
print(json.dumps(out))
"""


def _scen(sim_dir: str, backend: str) -> dict:
    p = subprocess.run([sys.executable, "-c", SCEN_CODE, sim_dir, backend], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-3000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="", help="path to an eco checkout of the base commit")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    rep = {}
    ok = True

    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "drone/sim/tests", "drone/common/tests"], cwd=ECO, capture_output=True, text=True)
    rep["pytest"] = {"ok": p.returncode == 0, "tail": p.stdout.strip().splitlines()[-1:]}
    ok &= p.returncode == 0

    with tempfile.TemporaryDirectory() as td:
        os.symlink(ECO, os.path.join(td, "eco"))
        rep["e2e"] = {}
        for vt in ("quadcopter", "rover", "fixedwing"):
            p = subprocess.run([sys.executable, "-m", "eco.e2e.run_e2e", "--type", vt, "--tier", "fast",
                                "--report", os.path.join(td, f"e2e_{vt}.json")],
                               cwd=td, capture_output=True, text=True)
            lines = [ln for ln in p.stdout.splitlines() if ln.startswith("[")]
            rep["e2e"][vt] = {"ok": p.returncode == 0, "lines": lines}
            ok &= p.returncode == 0

    kin = _scen(SIM, "kinematic")
    rep["scenarios_kinematic"] = kin
    if args.base:
        base = _scen(os.path.join(args.base, "drone", "sim"), "kinematic_legacy")
        diffs = {k: (base.get(k), kin.get(k)) for k in set(base) | set(kin) if base.get(k) != kin.get(k)}
        rep["scenarios_vs_base"] = {"n_base": len(base), "n_now": len(kin), "diffs": diffs}
        ok &= not diffs
    rep["scenarios_dynamic_smoke"] = _scen(SIM, "dynamic")
    rep["ok"] = bool(ok)
    with open(os.path.join(args.out, "regression.json"), "w") as f:
        json.dump(rep, f, indent=1)

    L = ["# Regression: existing scenes and vehicles", ""]
    L.append(f"pytest (drone/sim/tests + drone/common/tests): {'PASS' if rep['pytest']['ok'] else 'FAIL'} "
             f"-- {' '.join(rep['pytest']['tail'])}")
    L.append("")
    for vt, r in rep["e2e"].items():
        L.append(f"e2e fast tier `{vt}`: {'PASS' if r['ok'] else 'FAIL'} ({'; '.join(r['lines'])})")
    L.append("")
    if args.base:
        d = rep["scenarios_vs_base"]
        L.append(f"Seed scenarios on the default kinematic backend vs the base commit: {d['n_now']} run, "
                 f"{len(d['diffs'])} differ.")
    L += ["", "| scenario | kinematic: complete / targets / collisions / t_end s | rigid-body (opt-in): complete / targets / collisions / t_end s |",
          "|---|---|---|"]
    dyn = rep["scenarios_dynamic_smoke"]
    for k, v in kin.items():
        dv = dyn.get(k)
        ds = f"{dv['mission_complete']} / {dv['targets_found']}/{dv['targets_total']} / {dv['collisions']} / {dv['t_end']}" if dv else "n/a (no dynamics model for this class)"
        L.append(f"| {k} | {v['mission_complete']} / {v['targets_found']}/{v['targets_total']} / {v['collisions']} / {v['t_end']} | {ds} |")
    L += ["", "The rigid-body column is a smoke test: the scenarios' reactive controller was tuned on the "
          "kinematic model, so different numbers there are expected and are not a regression.",
          "", f"OVERALL: {'PASS' if ok else 'FAIL'}"]
    with open(os.path.join(args.out, "regression.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
