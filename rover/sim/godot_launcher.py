"""Launch Godot 4 directly for the Depot phrover sim (no fleet, no Unix-socket proxy).

Godot binary resolution mirrors eco/drone/sim/godot_engine.py:_find_godot (checked
via --godot arg / GODOT_BIN env / PATH godot4|godot), duplicated here rather than
imported so this harness has no import-path dependency on the drone/sim package.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from shutil import which

GODOT_PROJECT = Path(__file__).resolve().parents[2] / "drone" / "sim" / "godot"
READY_TIMEOUT = 90


def find_godot(hint: str | None = None) -> str:
    if hint:
        return hint
    env = os.environ.get("GODOT_BIN")
    if env:
        return env
    for name in ("godot4", "godot"):
        p = which(name)
        if p:
            return p
    raise RuntimeError("Godot 4 binary not found. Install it or set GODOT_BIN=/path/to/godot4")


class GodotProcess:
    def __init__(self, proc: subprocess.Popen, port: int):
        self.proc = proc
        self.port = port

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def launch_depot(seed: int = 0, port: int = 9999, gui: bool = False,
                  godot_bin: str | None = None) -> GodotProcess:
    """Launch Godot with the Depot env, empty fleet roster (no quad/rover spawned),
    and the phrover IPC port. Blocks until 'IPC ready' is printed or times out."""
    godot = find_godot(godot_bin)
    args = [godot, "--path", str(GODOT_PROJECT)]
    if not gui:
        args += ["--headless"]
    args += ["--", "--fleet=", "--env=depot", f"--seed={seed}", f"--ipc-port={port}"]

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = threading.Event()
    lines: list[str] = []

    def _tee():
        for line in proc.stdout:
            lines.append(line)
            print(f"[godot] {line}", end="", flush=True)
            if "IPC ready" in line:
                ready.set()
        ready.set()

    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(timeout=READY_TIMEOUT):
        proc.terminate()
        raise RuntimeError("Godot did not print 'IPC ready' within timeout")
    if proc.poll() is not None:
        raise RuntimeError(f"Godot exited early (rc={proc.returncode}):\n" + "".join(lines))
    return GodotProcess(proc, port)
