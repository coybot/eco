#!/usr/bin/env python3
"""Phase 4, capability #6 (learning from experience) — real CloudBrain only, no scripted
brain anywhere (see LearnPriorsTests.swift's doc comment): N training missions build an
empirical room-level prior for the red toolbox, then M held-out seeds are each run twice
(bare vs. primed with that prior) with the real production brain.

One xcodebuild invocation runs all N + 2M episodes inside a single Swift test method
(resetting/respawning the Depot between them) — avoids repeated simulator-boot overhead
for what would otherwise be 22 separate xcodebuild invocations.

This makes real, billed Bedrock calls (Claude Sonnet, via AWS_PROFILE=presidio) across ~22
episodes — meant to be run once, not iterated on.

Usage: python3 run_learn_priors.py [--port 9999] [--device "iPhone 17"]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from statistics import median

from godot_launcher import launch_depot

SDK_DIR = Path(__file__).resolve().parents[3] / "sdk"
ECO_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parents[1] / "models"
BRIDGE_READY_TIMEOUT = 30

TRAIN_RE = re.compile(r"^PRIOR_TRAIN (\{.*\})$")
HELDOUT_RE = re.compile(r"^PRIOR_HELDOUT (\{.*\})$")
PRIOR_SUMMARY_RE = re.compile(r"^=== learned prior: room counts (\[.*?\]), hint = (.*) ===$")


class Bridge:
    def __init__(self, proc: subprocess.Popen, url: str):
        self.proc = proc
        self.url = url

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def launch_bridge() -> Bridge:
    env = {**os.environ, "AWS_PROFILE": "presidio"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "e2e.harness.live_rover_act_bridge"],
        cwd=ECO_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url_holder: dict[str, str] = {}
    ready = threading.Event()

    def _tee():
        for line in proc.stdout:
            print(f"[bridge] {line}", end="", flush=True)
            if line.startswith("BRIDGE_URL="):
                url_holder["url"] = line.strip().split("=", 1)[1]
                ready.set()
        ready.set()

    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(timeout=BRIDGE_READY_TIMEOUT) or "url" not in url_holder:
        proc.terminate()
        raise RuntimeError("live_rover_act_bridge did not report BRIDGE_URL in time")
    return Bridge(proc, url_holder["url"])


def write_results(train_rows: list[dict], heldout_rows: list[dict], hint_line: str) -> None:
    baseline = [r for r in heldout_rows if not r["primed"]]
    primed = [r for r in heldout_rows if r["primed"]]
    by_seed_baseline = {r["seed"]: r for r in baseline}
    by_seed_primed = {r["seed"]: r for r in primed}

    lines = [
        "# Learning from experience (capability #6) — real CloudBrain, no scripted brain",
        "",
        f"**Model**: `us.anthropic.claude-sonnet-4-6` (Bedrock, us-west-2) · "
        f"**Runner**: `eco/rover/sim/run_learn_priors.py` -> `LearnPriorsTests`",
        "",
        "Every episode below — training and held-out, baseline and primed — is a real, "
        "separately-billed CloudBrain mission. No scripted brain was used anywhere in this "
        "capability, including for generating the training corpus: that would have proven "
        "nothing about whether a learned prior helps the actual model search faster.",
        "",
        "## Training corpus",
        "",
        f"{len(train_rows)} real missions, no hint, one per seed. Ground-truth room "
        "(Workshop/A vs Storage/B — `red_toolbox`'s candidate slots genuinely straddle both, "
        "see LearnPriorsTests.swift) queried via the oracle `prop_truth` op, never fed to "
        "the brain.",
        "",
        "| seed | room | found at (s) | reached done |",
        "|---|---|---|---|",
    ]
    for r in train_rows:
        found = f"{r['foundAtSeconds']:.1f}" if r["foundAtSeconds"] is not None else "not found"
        lines.append(f"| {r['seed']} | {r['room'] or '?'} | {found} | {r['reachedDone']} |")

    lines += [
        "",
        f"**Learned prior**: {hint_line}",
        "",
        "## Held-out comparison (same seed, bare vs. primed, both real missions)",
        "",
        "| seed | room | baseline found (s) | primed found (s) | delta |",
        "|---|---|---|---|---|",
    ]
    deltas = []
    for seed in sorted(by_seed_baseline):
        b = by_seed_baseline[seed]
        p = by_seed_primed.get(seed)
        bfound = b["foundAtSeconds"]
        pfound = p["foundAtSeconds"] if p else None
        delta_str = "n/a"
        if bfound is not None and pfound is not None:
            delta = bfound - pfound
            deltas.append(delta)
            delta_str = f"{delta:+.1f}s"
        bfound_str = f"{bfound:.1f}" if bfound is not None else "not found"
        pfound_str = f"{pfound:.1f}" if pfound is not None else "not found"
        lines.append(f"| {seed} | {b['room'] or '?'} | {bfound_str} | {pfound_str} | {delta_str} |")

    lines += ["", "## Summary", ""]
    if deltas:
        med = median(deltas)
        improved = sum(1 for d in deltas if d > 0)
        lines.append(
            f"Median improvement (baseline - primed) across {len(deltas)} paired held-out "
            f"seeds with both a baseline and primed find-time: **{med:+.1f}s**. "
            f"{improved}/{len(deltas)} seeds found faster when primed."
        )
    else:
        lines.append(
            "No held-out seed had both a baseline and a primed find-time to compare — see "
            "the raw table above; some episodes likely hit the tick cap before detecting "
            "the toolbox."
        )
    lines += [
        "",
        "## Caveats",
        "",
        "- Small N/M by design (real billed Bedrock calls, not free scripted iteration) — "
        "this is a documentation-level signal, not a statistically powered study.",
        "- Baseline always run before primed for a given held-out seed (not randomized order) "
        "— a real study would counterbalance order to rule out any systematic drift.",
        "- `red_toolbox`'s room is genuinely uncertain per seed (2 of 4 slots in each of "
        "Workshop/Storage) — a degenerate always-room-A prior was ruled out for exactly this "
        "reason.",
        "",
    ]

    out_path = RESULTS_DIR / "RESULTS_learning_priors.md"
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--device", default="iPhone 17")
    args = ap.parse_args()

    print(f"--- launching Godot Depot (port={args.port}) ---")
    godot = launch_depot(seed=0, port=args.port, gui=False)  # reset{seed} per-episode overrides this

    print("--- launching live_rover_act_bridge (real Bedrock, billed, 22 episodes) ---")
    bridge = launch_bridge()
    print(f"--- bridge ready at {bridge.url} ---")

    train_rows: list[dict] = []
    heldout_rows: list[dict] = []
    hint_line = "(not captured)"

    returncode = 1
    try:
        cmd = [
            "xcodebuild", "test",
            "-scheme", "presidio-sdk-Package",
            "-destination", f"platform=iOS Simulator,name={args.device}",
            "-only-testing:PhroverSimTests/LearnPriorsTests",
        ]
        env_overrides = {
            "TEST_RUNNER_GODOT_HOST": "127.0.0.1",
            "TEST_RUNNER_GODOT_PORT": str(args.port),
            "TEST_RUNNER_LIVE_ROVER_ACT_URL": bridge.url,
        }
        print("running:", " ".join(cmd), "with", env_overrides)
        proc = subprocess.Popen(
            cmd, cwd=SDK_DIR, env={**os.environ, **env_overrides},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            line = line.strip()
            if m := TRAIN_RE.match(line):
                train_rows.append(json.loads(m.group(1)))
            elif m := HELDOUT_RE.match(line):
                heldout_rows.append(json.loads(m.group(1)))
            elif m := PRIOR_SUMMARY_RE.match(line):
                hint_line = m.group(2)
        returncode = proc.wait()
    finally:
        bridge.stop()
        godot.stop()

    print(f"--- parsed {len(train_rows)} training rows, {len(heldout_rows)} held-out rows ---")
    if train_rows or heldout_rows:
        write_results(train_rows, heldout_rows, hint_line)
    else:
        print("no PRIOR_TRAIN/PRIOR_HELDOUT lines captured — check the xcodebuild output above")

    return returncode


if __name__ == "__main__":
    sys.exit(main())
