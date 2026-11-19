"""Headless batch runner with CSV logging.

    rows = run_batch(trial_fn, seeds, workers=8, out_csv="results.csv", common={...})

``trial_fn(seed, **common)`` must be a top-level (picklable) function returning one dict
(or a list of dicts) of scalar results. Rows are written sorted by seed, columns sorted,
floats in full ``repr`` precision, so the same seeds on the same machine give a
byte-identical CSV whatever the worker count (checks_phase1 verifies this). A sidecar
``<csv>.meta.json`` records seeds, versions, platform and the git commit.

Every column name must end in a unit suffix (``check_unit_names``); dimensionless values
use ``_nd``, fractions ``_frac``, counts ``_count``, flags ``_bool``, labels ``_label``,
identifiers ``_id``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import time
from functools import partial

import numpy as np

UNIT_SUFFIXES = (
    "_m", "_m_s", "_m_s2", "_m2", "_m3", "_deg", "_deg_s", "_rad", "_rad_s", "_kg", "_kg_m2",
    "_kg_m3", "_s", "_hz", "_n", "_nm", "_n_m", "_pa", "_j", "_w", "_nd", "_frac", "_pct",
    "_count", "_bool", "_label", "_id", "_per_s", "_n_s",
)


def check_unit_names(names) -> None:
    bad = [n for n in names if not any(n.endswith(s) for s in UNIT_SUFFIXES)]
    if bad:
        raise ValueError(f"columns without a unit suffix: {bad}")


def _call(fn, common, seed):
    out = fn(seed, **common)
    rows = out if isinstance(out, list) else [out]
    for r in rows:
        r.setdefault("seed_id", seed)
    return rows


def _fmt(v):
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return repr(float(v))
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def write_csv(rows: list, out_csv: str) -> None:
    cols = sorted({k for r in rows for k in r})
    check_unit_names(cols)
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k, "")) for k in cols})


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
    except Exception:
        return "unknown"


def run_batch(fn, seeds, workers: int = 1, out_csv: str | None = None, common: dict | None = None,
              progress: bool = False) -> list:
    common = common or {}
    seeds = list(seeds)
    t0 = time.time()
    call = partial(_call, fn, common)
    rows: list = []
    if workers <= 1:
        for i, s in enumerate(seeds):
            rows.extend(call(s))
            if progress and (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(seeds)}", file=sys.stderr, flush=True)
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(workers) as pool:
            for i, rs in enumerate(pool.imap_unordered(call, seeds, chunksize=1)):
                rows.extend(rs)
                if progress and (i + 1) % 25 == 0:
                    print(f"  {i + 1}/{len(seeds)}  {time.time() - t0:.0f} s", file=sys.stderr, flush=True)
    rows.sort(key=lambda r: (r["seed_id"], r.get("row_id", 0)))
    if out_csv:
        write_csv(rows, out_csv)
        meta = {
            "seeds": [seeds[0], seeds[-1], len(seeds)] if seeds else [],
            "workers": workers, "wall_s": round(time.time() - t0, 1),
            "python": sys.version.split()[0], "numpy": np.__version__,
            "platform": platform.platform(), "machine": platform.machine(),
            "git_commit": _git_commit(), "common": {k: repr(v) for k, v in common.items()},
            "csv_sha256": hashlib.sha256(open(out_csv, "rb").read()).hexdigest(),
        }
        with open(out_csv + ".meta.json", "w") as f:
            json.dump(meta, f, indent=1)
    return rows
