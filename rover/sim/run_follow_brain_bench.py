"""Benchmark "follow me" with different brains in the loop.

Runs sdk's PhroverSimTests/FollowBrainBenchmarkTests once per brain and tabulates the
`BENCH {json}` lines it prints:

  - decisions: the brain alone against scripted mission contexts (follow requests,
    attribute follows with a decoy in view, non-follow controls, follow review ticks)
  - closed loop: the real MissionAgent + FollowController following the Godot Depot
    sim's walkers, with the brain deciding ("follow me"; "follow the guy with the hat"
    with a hatless decoy walking alongside)

Every brain runs the same OnDeviceBrain prompt, schema and decision mapping; only the
model changes. Brains:

  apple              Apple Intelligence's on-device model (needs it enabled on this Mac,
                     and an iOS 27 simulator — the iOS 26.0 runtime cannot load the
                     model's safety guardrail, so every call fails there)
  ollama:<model>     a local Ollama model, e.g. ollama:qwen3.5:9b

Usage:
  python rover/sim/run_follow_brain_bench.py --device-id <iOS 27 simulator udid>
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from godot_launcher import launch_depot  # noqa: E402

ECO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRAINS = ["apple", "ollama:qwen3.5:9b", "ollama:qwen3.5:2b-q4_K_M", "ollama:qwen3.5:0.8b"]
SUITE = "PhroverSimTests/FollowBrainBenchmarkTests"


def xcodebuild(sdk: Path, action: list[str], device: str, derived: Path,
               env: dict[str, str] | None = None) -> tuple[int, str]:
    cmd = ["xcodebuild", *action, "-scheme", "coybot-sdk-Package",
           "-destination", f"id={device}", "-derivedDataPath", str(derived)]
    proc = subprocess.run(cmd, cwd=sdk, env={**os.environ, **(env or {})},
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def bench_lines(output: str) -> list[dict]:
    records = []
    for line in output.splitlines():
        if line.startswith("BENCH "):
            try:
                records.append(json.loads(line[len("BENCH "):]))
            except json.JSONDecodeError:
                pass
    # xcodebuild echoes a test's stdout more than once; keep the first copy of each.
    seen, unique = set(), []
    for r in records:
        key = json.dumps(r, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summarize(records: list[dict]) -> dict:
    by_brain: dict[str, dict] = defaultdict(lambda: {"decisions": [], "loops": [], "warmup": None})
    for r in records:
        slot = by_brain[r["brain"]]
        if r["tier"] == "decision":
            slot["decisions"].append(r)
        elif r["tier"] == "loop":
            slot["loops"].append(r)
        elif r["tier"] == "warmup":
            slot["warmup"] = r["ms"]

    out = {}
    for brain, slot in by_brain.items():
        d = slot["decisions"]
        groups = defaultdict(list)
        for r in d:
            groups[r["group"]].append(r)
        latencies = [r["ms"] for r in d]
        errors = [r for r in d if r["why"].startswith("error:")]
        summary = {
            "decisions": len(d),
            "pass_rate": sum(r["grade"] == "pass" for r in d) / len(d) if d else None,
            "by_group": {
                g: {"pass": sum(r["grade"] == "pass" for r in rs) / len(rs),
                    "not_broken": sum(r["grade"] in ("pass", "ok") for r in rs) / len(rs),
                    "n": len(rs)}
                for g, rs in sorted(groups.items())
            },
            "by_case": {
                c: {"pass": sum(r["grade"] == "pass" for r in rs) / len(rs),
                    "examples": sorted({r["why"][:80] for r in rs})[:3]}
                for c, rs in sorted(defaultdict(list, {
                    k: [r for r in d if r["case"] == k] for k in {r["case"] for r in d}
                }).items())
            },
            "latency_ms": {"p50": pct(latencies, 0.5), "p95": pct(latencies, 0.95)},
            "output_errors": len(errors),
            "warmup_ms": slot["warmup"],
        }
        loops = defaultdict(list)
        for r in slot["loops"]:
            loops[r["scenario"]].append(r)
        summary["loops"] = {}
        for scenario, runs in sorted(loops.items()):
            started = [r["follow_started_s"] for r in runs if r["follow_started_s"] is not None]
            reviews = [x for r in runs for x in r["decisions"][1:]]
            entry = {
                "runs": len(runs),
                "follow_started": len(started),
                "time_to_follow_s_median": statistics.median(started) if started else None,
                "follow_active_frac_mean": statistics.mean(r["follow_active_frac"] for r in runs),
                "in_band_frac_mean": statistics.mean(
                    [r["in_band_frac"] for r in runs if r["in_band_frac"] is not None] or [0]),
                "collisions_with_person": sum(r["collisions_with_person"] for r in runs),
                "review_decisions": len(reviews),
                "review_kept_following": sum(x.startswith("follow") for x in reviews),
                "decision_ms_median": statistics.median(
                    [x for r in runs for x in r["decision_ms"]] or [0]),
            }
            if scenario == "hat_and_decoy":
                on_hat = [r["on_hat_frac"] for r in runs if r.get("on_hat_frac") is not None]
                entry["on_hat_frac_mean"] = statistics.mean(on_hat) if on_hat else None
            summary["loops"][scenario] = entry
        out[brain] = summary
    return out


def fmt(x, pctg=False, digits=1):
    if x is None:
        return "—"
    return f"{x * 100:.0f}%" if pctg else f"{x:.{digits}f}"


def markdown(summary: dict, meta: dict) -> str:
    brains = list(summary)
    lines = [f"# Follow-me brain benchmark — {meta['when']}", "",
             f"Host: {meta['host']}. Simulator: {meta['device']}. "
             f"Decision repeats: {meta['repeats']}; closed-loop runs per scenario: {meta['loop_runs']}.",
             "", "## Decisions (brain alone)", "",
             "| | " + " | ".join(brains) + " |", "|---|" + "---|" * len(brains)]

    def row(label, get):
        lines.append(f"| {label} | " + " | ".join(get(summary[b]) for b in brains) + " |")

    row("overall pass", lambda s: fmt(s["pass_rate"], True))
    for g in ["follow", "follow-attribute", "not-follow", "review"]:
        row(f"{g} pass", lambda s, g=g: fmt(s["by_group"].get(g, {}).get("pass"), True))
    row("review: follow not broken", lambda s: fmt(s["by_group"].get("review", {}).get("not_broken"), True))
    row("latency p50 (ms)", lambda s: fmt(s["latency_ms"]["p50"], digits=0))
    row("latency p95 (ms)", lambda s: fmt(s["latency_ms"]["p95"], digits=0))
    row("invalid outputs", lambda s: str(s["output_errors"]))

    lines += ["", "## Closed loop (Godot Depot sim)", ""]
    for scenario in ["follow_me", "hat_and_decoy"]:
        lines += [f"### {scenario}", "", "| | " + " | ".join(brains) + " |", "|---|" + "---|" * len(brains)]

        def lrow(label, key, pctg=False, digits=1):
            lines.append(f"| {label} | " + " | ".join(
                fmt(summary[b]["loops"].get(scenario, {}).get(key), pctg, digits) for b in brains) + " |")

        lines.append("| follow started | " + " | ".join(
            f"{summary[b]['loops'].get(scenario, {}).get('follow_started', '—')}/"
            f"{summary[b]['loops'].get(scenario, {}).get('runs', '—')}" for b in brains) + " |")
        lrow("time to follow (s, median)", "time_to_follow_s_median")
        lrow("follow active (of window)", "follow_active_frac_mean", True)
        lrow("in stand-off band", "in_band_frac_mean", True)
        if scenario == "hat_and_decoy":
            lrow("on the hat walker", "on_hat_frac_mean", True)
        lines.append("| reviews that kept following | " + " | ".join(
            f"{summary[b]['loops'].get(scenario, {}).get('review_kept_following', '—')}/"
            f"{summary[b]['loops'].get(scenario, {}).get('review_decisions', '—')}" for b in brains) + " |")
        lrow("collisions with a person", "collisions_with_person", digits=0)
        lrow("decision latency (ms, median)", "decision_ms_median", digits=0)
        lines.append("")
    lines += ["## Per case", ""]
    for b in brains:
        lines.append(f"**{b}**")
        for case, v in summary[b]["by_case"].items():
            lines.append(f"- {case}: {fmt(v['pass'], True)} — e.g. {'; '.join(v['examples'])}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-id", required=True, help="iOS 27 simulator UDID")
    ap.add_argument("--sdk", type=Path, default=ECO_ROOT.parent / "sdk")
    ap.add_argument("--brains", nargs="+", default=DEFAULT_BRAINS)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--loop-runs", type=int, default=3)
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--derived-data", type=Path, default=Path("/tmp/follow-brain-bench-dd"))
    ap.add_argument("--out", type=Path, default=ECO_ROOT / "rover" / "sim" / "results")
    ap.add_argument("--skip-loop", action="store_true")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_path = args.out / f"follow_brain_bench_{stamp}.jsonl"

    print("--- building tests ---", flush=True)
    rc, log = xcodebuild(args.sdk, ["build-for-testing"], args.device_id, args.derived_data)
    if rc != 0:
        print(log[-4000:])
        return rc

    godot = None if args.skip_loop else launch_depot(seed=7, port=args.port, gui=False)
    records: list[dict] = []
    try:
        for brain in args.brains:
            env = {"TEST_RUNNER_BENCH_BRAIN": brain, "TEST_RUNNER_BENCH_REPEATS": str(args.repeats),
                   "TEST_RUNNER_GODOT_HOST": "127.0.0.1", "TEST_RUNNER_GODOT_PORT": str(args.port)}
            print(f"--- {brain}: decisions ---", flush=True)
            _, log = xcodebuild(args.sdk, ["test-without-building", f"-only-testing:{SUITE}/testDecisions"],
                                args.device_id, args.derived_data, env)
            got = bench_lines(log)
            if not got:
                print(log[-3000:])
            records += got
            if not args.skip_loop:
                for test in ["testClosedLoopFollowMe", "testClosedLoopFollowTheGuyWithTheHat"]:
                    print(f"--- {brain}: {test} x{args.loop_runs} ---", flush=True)
                    _, log = xcodebuild(args.sdk, ["test-without-building", f"-only-testing:{SUITE}/{test}",
                                                   "-test-iterations", str(args.loop_runs)],
                                        args.device_id, args.derived_data, env)
                    got = bench_lines(log)
                    if not got:
                        print(log[-3000:])
                    records += got
            with raw_path.open("w") as fh:
                for r in records:
                    fh.write(json.dumps(r) + "\n")
    finally:
        if godot:
            godot.stop()

    summary = summarize(records)
    meta = {"when": stamp, "host": os.uname().nodename, "device": args.device_id,
            "repeats": args.repeats, "loop_runs": 0 if args.skip_loop else args.loop_runs}
    (args.out / f"follow_brain_bench_{stamp}.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=2))
    md = markdown(summary, meta)
    (args.out / f"follow_brain_bench_{stamp}.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
