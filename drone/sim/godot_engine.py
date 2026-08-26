#!/usr/bin/env python3
"""Godot sim engine: drop-in replacement for sim_engine.py using Godot 4.

Serves the IDENTICAL Unix-socket IPC protocol as sim_engine.py so that
engine_client.py, sim_drone_daemon.py, launch_fleet.py, and everything above
the seam work without any modification.

Architecture:
  - This process starts Godot 4 as a subprocess (headless GPU render via Vulkan).
  - Godot exposes a TCP control server (default :9999) that speaks the same
    newline-JSON protocol as the Unix socket (identical op/response shapes).
  - This process proxies the Unix socket (expected by engine_client.py) to that
    TCP port, adding nothing except the socket layer.
  - Frame readback: grab_frame / grab_vantage return base64 JPEG, same as Isaac.

Usage (identical to sim_engine.py):
    python3 godot_engine.py --fleet quad:3,rover:2 --env office \
        --sock /tmp/sim_engine.sock

Godot binary is resolved from:
  1. --godot CLI arg
  2. GODOT_BIN env var
  3. PATH: godot4, godot
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
GODOT_PROJECT = HERE / "godot"

# How long to wait for Godot to report "IPC ready" on stdout.
GODOT_READY_TIMEOUT = 90


def _find_godot(hint: str | None) -> str:
    if hint:
        return hint
    env = os.environ.get("GODOT_BIN")
    if env:
        return env
    for name in ("godot4", "godot"):
        from shutil import which
        p = which(name)
        if p:
            return p
    raise RuntimeError(
        "Godot 4 binary not found. Install it or set GODOT_BIN=/path/to/godot4"
    )


def _build_godot_args(godot_bin: str, fleet: str, env_name: str,
                      tcp_port: int, headless: bool,
                      rendering_driver: str | None = None,
                      gpu_index: int | None = None) -> list[str]:
    args = [godot_bin, "--path", str(GODOT_PROJECT)]
    if headless:
        args += ["--headless"]
    if rendering_driver:
        args += ["--rendering-driver", rendering_driver]
    if gpu_index is not None:
        # Pin Vulkan device selection away from whichever GPU a concurrent
        # training/serving job is saturating -- this host is multi-GPU and a
        # capture run sharing a GPU with an active training job can starve the
        # per-vehicle SubViewports (each grab_jpeg() is a real render), which
        # looks identical to a GT-projection bug (boxes vs. stale rendered
        # frames) unless controlled for. See godot_dataset_recorder.py's
        # --gpu-index for the caller-facing flag.
        args += ["--gpu-index", str(gpu_index)]
    # Pass fleet/env/port as scene-agnostic launch args (parsed by autoload).
    args += [
        "--",
        f"--fleet={fleet}",
        f"--env={env_name}",
        f"--ipc-port={tcp_port}",
    ]
    return args


# -- Unix-socket → TCP proxy ---------------------------------------------------

class _ProxyHandler(socketserver.StreamRequestHandler):
    """One Unix-socket client ↔ one TCP connection to Godot."""

    def handle(self):
        try:
            tcp = socket.create_connection(("127.0.0.1", self.server.tcp_port),
                                           timeout=5)
        except OSError as e:
            print(f"[godot-engine] proxy: could not connect to Godot TCP: {e}",
                  flush=True)
            return
        tcp_f = tcp.makefile("rwb")
        try:
            for line in self.rfile:
                if not line.strip():
                    continue
                tcp_f.write(line if line.endswith(b"\n") else line + b"\n")
                tcp_f.flush()
                resp = tcp_f.readline()
                if not resp:
                    break
                self.wfile.write(resp if resp.endswith(b"\n") else resp + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            tcp.close()


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def _run_proxy(sock_path: str, tcp_port: int) -> None:
    if os.path.exists(sock_path):
        os.remove(sock_path)
    srv = _UnixServer(sock_path, _ProxyHandler)
    srv.tcp_port = tcp_port
    os.chmod(sock_path, 0o770)
    print(f"[godot-engine] IPC proxy on {sock_path} → 127.0.0.1:{tcp_port}",
          flush=True)
    srv.serve_forever()


# -- main ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True)
    ap.add_argument("--env", default="office")
    ap.add_argument("--sock", default="/tmp/sim_engine.sock")
    ap.add_argument("--tcp-port", type=int, default=9999,
                    help="TCP port Godot listens on for IPC")
    ap.add_argument("--godot", default=None,
                    help="Path to godot4 binary (overrides GODOT_BIN and PATH)")
    ap.add_argument("--gui", action="store_true",
                    help="Show Godot window (not headless; useful for dev)")
    ap.add_argument("--rendering-driver", default=None,
                    help="Force a Godot rendering driver (e.g. opengl3 for CPU instances)")
    ap.add_argument("--photoreal", action="store_true",
                    help="Pass --photoreal to Godot scene (higher quality assets)")
    ap.add_argument("--gpu-index", type=int, default=None,
                    help="Pin Godot's Vulkan device (see --verbose device list); "
                         "use to avoid a GPU a concurrent training job is saturating")
    args = ap.parse_args()

    godot_bin = _find_godot(args.godot)
    headless = not args.gui
    fleet_arg = args.fleet
    if args.photoreal:
        fleet_arg += ":photoreal"  # Godot scene reads this suffix

    print(f"[godot-engine] fleet={args.fleet} env={args.env} "
          f"godot={godot_bin} headless={headless} driver={args.rendering_driver}", flush=True)

    cmd = _build_godot_args(godot_bin, fleet_arg, args.env, args.tcp_port, headless,
                            rendering_driver=args.rendering_driver, gpu_index=args.gpu_index)
    print(f"[godot-engine] launching: {' '.join(cmd)}", flush=True)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Wait for Godot to signal readiness.
    ready = threading.Event()

    def _tee_stdout():
        for line in proc.stdout:
            print(f"[godot] {line}", end="", flush=True)
            if "IPC ready" in line:
                ready.set()
        ready.set()  # unblock on EOF too (so we can detect early exit)

    threading.Thread(target=_tee_stdout, name="godot-stdout", daemon=True).start()

    print(f"[godot-engine] waiting for Godot IPC ready (up to {GODOT_READY_TIMEOUT}s)…",
          flush=True)
    if not ready.wait(timeout=GODOT_READY_TIMEOUT):
        proc.terminate()
        raise RuntimeError("Godot did not print 'IPC ready' within timeout")
    if proc.poll() is not None:
        raise RuntimeError(f"Godot exited early (rc={proc.returncode})")

    # Start the Unix-socket proxy (runs forever in background thread).
    threading.Thread(target=_run_proxy, args=(args.sock, args.tcp_port),
                     name="ipc-proxy", daemon=True).start()

    print("[godot-engine] ready", flush=True)

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    main()
