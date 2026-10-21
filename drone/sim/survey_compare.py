"""Side-by-side report of survey_sitl.py runs (each an --out directory), e.g. old vs new code.

    python3 drone/sim/survey_compare.py out-old out-new
"""
import json
import sys
from pathlib import Path

ROWS = [
    ("mission succeeded", "success"),
    ("start heading, deg", "start_heading"),
    ("photos / cells", lambda s: f"{s.get('photos')} / {s.get('cells')}"),
    ("cells hit", "cells_hit"),
    ("photo error mean, max m", "photo_err_mean_max_m"),
    ("photo heading off start, deg", lambda s: _span(s.get("photo_heading_off_start_deg"))),
    ("heading worst during survey, deg", "heading_max_off_during_survey_deg"),
    ("fastest at a photo, m/s", lambda s: max(s.get("speed_at_photos_mps") or [0])),
    ("ground path flown, m", "ground_path_airborne_m"),
    ("landed from takeoff, m", "end_dist_from_takeoff_m"),
    ("armed at end", "armed_at_end"),
    ("mission time, s", "elapsed_s"),
    ("pack set (app)", "pack_set"),
    ("pack the mission loop used", "mission_loop_battery"),
    ("phase cost the loop priced, %", "mission_loop_phase_cost_pct"),
    ("heartbeat hover %/min", lambda s: (s.get("heartbeat_energy_model") or {}).get("hover_pct_per_min")),
    ("failure", "failure"),
]


def _span(xs):
    return f"{min(xs):+.1f} .. {max(xs):+.1f}" if xs else None


def main(dirs):
    runs = [(Path(d).name, json.loads((Path(d) / "summary.json").read_text())) for d in dirs]
    def cell(v, limit=48):
        v = str(v)
        return v if len(v) <= limit else v[:limit - 3] + "..."
    table = [(label, [cell(key(s) if callable(key) else s.get(key)) for _, s in runs]) for label, key in ROWS]
    width = max(len(r[0]) for r in ROWS) + 2
    col = max([len(n) for n, _ in runs] + [len(v) for _, vals in table for v in vals]) + 3
    print("".ljust(width) + "".join(n.ljust(col) for n, _ in runs))
    for label, vals in table:
        print(label.ljust(width) + "".join(v.ljust(col) for v in vals))
    for name, s in runs:
        order = s.get("photo_order_fwd_right_m")
        if order:
            print(f"\n{name} photo order (forward, right m): " + " ".join(f"({f},{r})" for f, r in order))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
