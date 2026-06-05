"""The Ishmael director: turn a TestSpec into a running, observed simulated test.

Pipeline (``run``):
  1. roster   — TestSpec.fleet_arg() -> fleet.parse_roster (deterministic ids)
  2. register — fleet.register_fleet (DynamoDB rows so the app/cloud see them);
                skipped when ``--no-register`` or when boto3/creds are absent
  3. launch   — start the process-separated fleet (launch_fleet.py) in the chosen
                scene; one Isaac engine + one daemon per drone, on Hoopoe
  4. wait     — block until every drone publishes an "online" heartbeat
  5. dispatch — hand the mission objective to the mobile client (the "iPhone"),
                which sends it through the cloud exactly like a real user
  6. collect  — gather the images/videos the drones send back

The director itself never talks to Isaac or Bedrock directly — it composes the
existing sim host (``launch_fleet.py``) and the mobile contract (``app_client``),
so a sim drone stays indistinguishable from an IRL drone end-to-end.

Everything heavy (boto3, awscrt, the fleet subprocess) is imported lazily so this
module imports cleanly on a laptop for unit-testing the orchestration logic.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# sibling sim modules (fleet.py, launch_fleet.py) live one dir up
_SIM_DIR = Path(__file__).resolve().parent.parent
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))

from .nlp import TestSpec, parse_test_spec  # noqa: E402


@dataclass
class DirectorConfig:
    certs_base: str = os.path.expanduser("~/eco-certs-fleet")
    python: str = os.environ.get("ISHMAEL_SIM_PYTHON",
                                 "/home/yusuf/isaac-sim-env/bin/python3")
    user_sub: Optional[str] = os.environ.get("ISHMAEL_USER_SUB")
    region: str = os.environ.get("AWS_REGION", "us-west-2")
    isaac_host: str = "hoopoe"
    register: bool = True
    online_timeout_s: float = 240.0
    mission_timeout_s: float = 240.0
    out_dir: str = os.path.expanduser("~/videos/ishmael")
    dry_run: bool = False


@dataclass
class TestResult:
    spec: TestSpec
    roster: list[dict] = field(default_factory=list)
    online: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    responses: list[dict] = field(default_factory=list)
    fleet_proc_pid: Optional[int] = None
    scene_ref: object = None
    ok: bool = False
    notes: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        print(f"[director] {msg}", flush=True)
        self.notes.append(msg)


def _mission_command(spec: TestSpec) -> str:
    """Phrase the objective as a user would type it to the drone."""
    obj = spec.objective or (f"search for a {spec.target}" if spec.target else "explore")
    parts = [obj]
    if spec.mobile_action == "send_video":
        parts.append("record and send the video")
    elif spec.mobile_action == "send_picture":
        parts.append("take a picture and send it")
    if spec.vantage:
        parts.append("also record an overhead vantage video with record_vantage()")
    return ", then ".join(parts)


def run(spec: TestSpec, cfg: Optional[DirectorConfig] = None) -> TestResult:
    cfg = cfg or DirectorConfig()
    spec = spec.normalized()
    result = TestResult(spec=spec)
    result.log(f"spec: {spec.fleet_arg()} in '{spec.scene}'; "
               f"mission='{_mission_command(spec)}'")

    from fleet import parse_roster  # noqa: E402  (sibling module)
    roster = parse_roster(spec.fleet_arg())
    result.roster = roster
    result.log(f"roster: {[r['id'] for r in roster]}")

    # resolve the described scene to a loadable USD (library now, generative later)
    from .scene_resolver import resolve_scene
    scene_ref = resolve_scene(spec.scene)
    result.scene_ref = scene_ref
    result.log(f"scene '{spec.scene}' -> {scene_ref.category}/{scene_ref.sid} "
               f"(conf={scene_ref.confidence})"
               + (f"; {scene_ref.notes}" if scene_ref.notes else ""))

    if cfg.dry_run:
        result.log("dry-run: stopping before launch")
        result.ok = True
        return result

    # 2. register fleet rows (best-effort)
    if cfg.register and cfg.user_sub:
        try:
            from fleet import register_fleet
            register_fleet(cfg.user_sub, roster, spec.scene, cfg.isaac_host, cfg.region)
            result.log(f"registered {len(roster)} drones to {cfg.user_sub}")
        except Exception as e:
            result.log(f"register skipped ({e})")
    elif cfg.register:
        result.log("register skipped: no ISHMAEL_USER_SUB set")

    # 3. launch the fleet (process-separated engine + daemons)
    proc = _launch_fleet(spec, cfg, result)
    result.fleet_proc_pid = proc.pid if proc else None

    # 4-6. observe + dispatch through the mobile client
    try:
        from sim.mobile.app_client import AppClient  # type: ignore
    except Exception:
        # allow running from the sim dir layout
        sys.path.insert(0, str(_SIM_DIR.parent.parent))  # repo/eco
        from sim.mobile.app_client import AppClient  # type: ignore

    client = AppClient(drone_ids=[r["id"] for r in roster], out_dir=cfg.out_dir)
    client.connect()
    try:
        result.online = client.wait_online(timeout=cfg.online_timeout_s)
        result.log(f"online: {result.online}")
        if not result.online:
            result.log("no drones came online; aborting dispatch")
            return result

        command = _mission_command(spec)
        responses = client.send_mission(command, timeout=cfg.mission_timeout_s)
        result.responses = responses
        for r in responses:
            result.images.extend(r.get("image_urls") or [])
            result.videos.extend(r.get("video_urls") or [])
        saved = client.download_media(result.images + result.videos)
        result.log(f"received {len(result.images)} image(s), "
                   f"{len(result.videos)} video(s); saved -> {cfg.out_dir}")
        result.ok = bool(result.images or result.videos)
    finally:
        client.close()
    return result


def _launch_fleet(spec: TestSpec, cfg: DirectorConfig, result: TestResult):
    """Spawn launch_fleet.py as a child process. Returns the Popen or None."""
    import subprocess
    launcher = _SIM_DIR / "launch_fleet.py"
    env_value = result.scene_ref.launch_value() if result.scene_ref else spec.scene
    cmd = [cfg.python, str(launcher),
           "--fleet", spec.fleet_arg(),
           "--env", env_value,
           "--certs-base", cfg.certs_base]
    env = dict(os.environ)
    env.setdefault("ISHMAEL_HARNESS",
                   os.path.expanduser("~/code/ishmael/swarm_eval/harness"))
    result.log("launch: " + " ".join(cmd))
    log_path = os.path.join("/tmp", "ishmael_launch.log")
    proc = subprocess.Popen(cmd, stdout=open(log_path, "w"),
                            stderr=subprocess.STDOUT, env=env)
    result.log(f"fleet launching (pid {proc.pid}); log -> {log_path}")
    return proc


def run_text(text: str, cfg: Optional[DirectorConfig] = None,
             use_llm: bool = True) -> TestResult:
    return run(parse_test_spec(text, use_llm=use_llm), cfg)
