#!/usr/bin/env python3
"""On-host session server for the web simulator (runs on the EC2 sim host).

Lifecycle (Option 2 cold start):
  * BOOT (on instance start / pre-warm): launch Godot with an empty default world
    + a pool of `sim_drone_daemon.py` processes (each its own MQTT connection,
    already heartbeating to AWS IoT). This overlaps the user's config/typing time.
  * POST /session: RECONFIGURE the already-running Godot — lease pool drones,
    register them in drone-registry-dev under the user, spawn their vehicles +
    swap the environment over IPC, set up the "scene" vantage cam. ~2-5 s.
  * WS  /stream?session=<id>: stream JPEG frames for a requested cam ("scene" or
    a drone id), grabbed from Godot only while a viewer socket is attached.
  * POST /session/{id}/end: despawn vehicles, deregister, release the lease.
  * Idle self-stop: if no viewer for IDLE_TIMEOUT (and SIM_SELF_STOP=1), stop the
    EC2 instance so an abandoned pre-warm doesn't leak GPU cost.

Control + autonomy ride the existing AWS-IoT / Bedrock path (the per-drone daemons
behave exactly like IRL). This server only owns session lifecycle + video frames.

    python3 sim_session_server.py --port 8080 --certs-base ~/eco-certs-fleet \
        --pool quad:5,rover:5 --default-env office --xvfb

Local dev against an already-running Godot (skip launching Godot + the pool):
    python3 sim_session_server.py --no-launch --certs-base ~/eco-certs-fleet
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from aiohttp import web, WSMsgType

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from engine_client import EngineClient  # noqa: E402
from fleet import parse_roster  # noqa: E402

# Browser origins allowed to open the frame WebSocket / call the server.
ALLOWED_ORIGINS = {"https://presidioautonomy.com", "http://localhost:3000"}
IDLE_TIMEOUT = float(os.environ.get("SIM_IDLE_TIMEOUT", "120"))   # s, no-viewer → teardown
MAX_LIFETIME = float(os.environ.get("SIM_MAX_LIFETIME", "1200"))  # s, hard session cap
FRAME_FPS = float(os.environ.get("SIM_FRAME_FPS", "13"))
# Shared secret for the orchestrator Lambda → this server hop (the browser never
# calls /session directly; it goes through the Cognito-authorized orchestrator).
SIM_SECRET = os.environ.get("SIM_SHARED_SECRET", "")


def _cors(resp: web.StreamResponse, origin: str | None) -> web.StreamResponse:
    allow = origin if origin in ALLOWED_ORIGINS else "https://presidioautonomy.com"
    resp.headers["Access-Control-Allow-Origin"] = allow
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-Sim-Secret"
    resp.headers["Access-Control-Allow-Methods"] = "POST,GET,OPTIONS"
    return resp


class SimHost:
    """Owns the Godot process, the daemon pool, and the single active session."""

    def __init__(self, args):
        self.args = args
        self.sock = args.sock                       # engine IPC addr (tcp://127.0.0.1:9999)
        self.certs_base = os.path.expanduser(args.certs_base)
        self.default_env = args.default_env
        self.pool = parse_roster(args.pool)         # [{id,type}, ...] daemons we pre-boot
        self.region = args.region

        self._procs: list[subprocess.Popen] = []
        self._engine: EngineClient | None = None    # reconfigure/control connection
        self._stream_engine: EngineClient | None = None  # separate conn for frame grabs

        self._lock = asyncio.Lock()                 # single-session guard
        self.session: dict | None = None            # active session (or None)
        self._leased: set[str] = set()
        self._viewers = 0
        self._last_viewer_ts = time.time()

    # -- boot -----------------------------------------------------------------
    def boot(self):
        """Launch Xvfb (optional) + Godot (empty world) + the daemon pool."""
        env = dict(os.environ)
        if self.args.xvfb and not self.args.no_launch:
            subprocess.Popen(["Xvfb", self.args.display, "-screen", "0", "1280x720x24", "-ac"],
                             stdout=open("/tmp/xvfb.log", "w"), stderr=subprocess.STDOUT)
            time.sleep(2)
            env["DISPLAY"] = self.args.display
            print(f"[host] Xvfb on {self.args.display}", flush=True)

        if not self.args.no_launch:
            self._launch_godot(env)
            self._launch_pool(env)

        # Control + stream connections (separate so frame grabs don't serialize
        # behind reconfigure calls).
        self._engine = EngineClient(self.sock)
        self._stream_engine = EngineClient(self.sock)
        print("[host] engine connected", flush=True)

    def _launch_godot(self, env):
        sock_file = "/tmp/sim_engine.sock"
        if os.path.exists(sock_file):
            os.remove(sock_file)
        cmd = [self.args.python, str(HERE / "godot_engine.py"),
               "--fleet", "quad:0", "--env", self.default_env,
               "--sock", sock_file, "--tcp-port", str(self.args.tcp_port),
               "--gui"]   # --gui = no --headless; real renderer via Xvfb
        if self.args.godot:
            cmd += ["--godot", self.args.godot]
        if self.args.rendering_driver:
            cmd += ["--rendering-driver", self.args.rendering_driver]
        eng = subprocess.Popen(cmd, stdout=open("/tmp/sim_engine.log", "w"),
                               stderr=subprocess.STDOUT, env=env)
        self._procs.append(eng)
        # godot_engine.py creates the unix socket once Godot prints "IPC ready".
        for _ in range(120):
            if os.path.exists(sock_file):
                break
            if eng.poll() is not None:
                raise RuntimeError("Godot engine exited early; see /tmp/sim_engine.log")
            time.sleep(1)
        else:
            raise RuntimeError("Godot IPC socket never appeared")
        print("[host] Godot ready (empty world)", flush=True)

    def _launch_pool(self, env):
        for spec in self.pool:
            did = spec["id"]
            p = subprocess.Popen(
                [self.args.python, str(HERE / "sim_drone_daemon.py"),
                 "--drone-id", did, "--vehicle", spec["type"],
                 "--sock", self.sock, "--certs-dir", f"{self.certs_base}/{did}"],
                stdout=open(f"/tmp/daemon-{did}.log", "w"),
                stderr=subprocess.STDOUT, env=env)
            self._procs.append(p)
            time.sleep(0.1)
        print(f"[host] daemon pool up: {len(self.pool)} drones", flush=True)

    # -- session lifecycle ----------------------------------------------------
    async def start_session(self, fleet: dict, env_name: str, user_id: str) -> dict:
        async with self._lock:
            if self.session is not None:
                raise web.HTTPConflict(reason="simulator busy")

            leased = self._lease(fleet)
            if leased is None:
                raise web.HTTPConflict(reason="not enough free drones in pool")

            session_id = uuid.uuid4().hex
            _leased_ids = [d[0] for d in leased]  # for rollback on error
            loop = asyncio.get_event_loop()

            # Reconfigure Godot: swap env (if needed) + spawn the leased vehicles.
            await loop.run_in_executor(None, self._engine.load_env, env_name)
            drones = []
            counts = {"quadcopter": 0, "rover": 0}
            for did, vtype in leased:
                await loop.run_in_executor(None, self._engine.spawn, did, vtype, None)
                counts[vtype] += 1
                label = ("Quadcopter" if vtype == "quadcopter" else "Rover") + f" {counts[vtype]}"
                conv = f"sim-{session_id}-{did}"
                drones.append({"droneId": did, "vehicleType": vtype,
                               "label": label, "conversationId": conv})

            # Scene/overhead vantage cam (after env load so framing matches).
            await loop.run_in_executor(None, self._engine.auto_overhead, "scene")

            # Register the leased drones to the user so the existing cloud
            # conversations pipeline accepts commands for them.
            try:
                if not getattr(self.args, 'no_register', False):
                    self._register(drones, env_name, user_id)
            except Exception as e:
                for did in _leased_ids:
                    self._leased.discard(did)
                raise web.HTTPInternalServerError(reason=f"registration failed: {e}")

            self.session = {
                "sessionId": session_id, "userId": user_id, "env": env_name,
                "drones": drones, "leased": [d[0] for d in leased],
                "created": time.time()}
            self._last_viewer_ts = time.time()
            print(f"[host] session {session_id}: {[d['droneId'] for d in drones]}", flush=True)
            return {"sessionId": session_id, "drones": drones}

    def _lease(self, fleet: dict):
        """Lease the lowest-index free pool drones matching the requested counts."""
        want = {"quadcopter": int(fleet.get("quadcopter", 0)),
                "rover": int(fleet.get("rover", 0))}
        leased: list[tuple[str, str]] = []
        for spec in self.pool:
            vt = spec["type"]
            if want.get(vt, 0) > 0 and spec["id"] not in self._leased:
                leased.append((spec["id"], vt))
                want[vt] -= 1
        if any(v > 0 for v in want.values()):
            return None
        for did, _ in leased:
            self._leased.add(did)
        return leased

    def _register(self, drones: list[dict], env_name: str, user_id: str):
        import boto3
        from datetime import datetime, timezone
        table = boto3.resource("dynamodb", region_name=self.region).Table("drone-registry-dev")
        now = datetime.now(timezone.utc).isoformat()
        with table.batch_writer() as bw:
            for d in drones:
                bw.put_item(Item={
                    "userId": user_id, "droneId": d["droneId"], "name": d["label"],
                    "registeredAt": now, "status": "registered", "droneType": "sim",
                    "vehicleType": d["vehicleType"], "simEnvironment": env_name,
                    "isaacHost": "ec2"})

    async def end_session(self, session_id: str):
        async with self._lock:
            s = self.session
            if not s or s["sessionId"] != session_id:
                return
            loop = asyncio.get_event_loop()
            for did in s["leased"]:
                try:
                    await loop.run_in_executor(None, self._engine.despawn, did)
                except Exception:
                    pass
            self._deregister(s["drones"], s["userId"])
            for did in s["leased"]:
                self._leased.discard(did)
            self.session = None
            print(f"[host] session {session_id} ended", flush=True)

    def _deregister(self, drones: list[dict], user_id: str):
        import boto3
        table = boto3.resource("dynamodb", region_name=self.region).Table("drone-registry-dev")
        for d in drones:
            try:
                table.delete_item(Key={"userId": user_id, "droneId": d["droneId"]})
            except Exception:
                pass

    # -- idle / cost backstop -------------------------------------------------
    async def idle_gc(self):
        while True:
            await asyncio.sleep(15)
            s = self.session
            now = time.time()
            if s is None:
                # Abandoned pre-warm: nobody started a session for a while.
                if self._viewers == 0 and (now - self._last_viewer_ts) > IDLE_TIMEOUT:
                    self._maybe_self_stop("idle pre-warm")
                continue
            expired = (self._viewers == 0 and (now - self._last_viewer_ts) > IDLE_TIMEOUT)
            too_old = (now - s["created"]) > MAX_LIFETIME
            if expired or too_old:
                print(f"[host] GC session {s['sessionId']} ({'idle' if expired else 'max-life'})",
                      flush=True)
                await self.end_session(s["sessionId"])
                self._maybe_self_stop("idle after session")

    def _maybe_self_stop(self, why: str):
        if os.environ.get("SIM_SELF_STOP") != "1":
            return
        try:
            import urllib.request
            import boto3
            tok = urllib.request.Request(
                "http://169.254.169.254/latest/api/token", method="PUT",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
            token = urllib.request.urlopen(tok, timeout=2).read().decode()
            req = urllib.request.Request(
                "http://169.254.169.254/latest/meta-data/instance-id",
                headers={"X-aws-ec2-metadata-token": token})
            iid = urllib.request.urlopen(req, timeout=2).read().decode()
            print(f"[host] self-stop ({why}) instance {iid}", flush=True)
            boto3.client("ec2", region_name=self.region).stop_instances(InstanceIds=[iid])
        except Exception as e:
            print(f"[host] self-stop failed: {e}", flush=True)

    # -- frame streaming ------------------------------------------------------
    def grab(self, cam: str) -> bytes | None:
        if cam == "scene":
            return self._stream_engine.grab_vantage_jpeg("scene")
        return self._stream_engine.grab_jpeg(cam)


# ---------------------------------------------------------------------------
# HTTP / WS routes
# ---------------------------------------------------------------------------
def make_app(host: SimHost) -> web.Application:
    app = web.Application()

    async def healthz(request):
        return web.json_response({"ok": True, "session": bool(host.session)})

    async def options(request):
        return _cors(web.Response(), request.headers.get("Origin"))

    async def session_start(request):
        if SIM_SECRET and request.headers.get("X-Sim-Secret") != SIM_SECRET:
            raise web.HTTPUnauthorized(reason="bad sim secret")
        body = await request.json()
        fleet = body.get("fleet", {})
        env_name = body.get("env", host.default_env)
        user_id = body.get("userId")
        if not user_id:
            raise web.HTTPBadRequest(reason="userId required")
        result = await host.start_session(fleet, env_name, user_id)
        # Absolute WS url the browser should open.
        base = os.environ.get("SIM_PUBLIC_WSS", "wss://sim.presidioautonomy.com")
        result["wsUrl"] = f"{base}/stream?session={result['sessionId']}"
        return _cors(web.json_response(result), request.headers.get("Origin"))

    async def session_end(request):
        sid = request.match_info["id"]
        await host.end_session(sid)
        return _cors(web.json_response({"ok": True}), request.headers.get("Origin"))

    async def stream(request):
        origin = request.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            raise web.HTTPForbidden(reason="bad origin")
        sid = request.query.get("session")
        if not host.session or host.session["sessionId"] != sid:
            raise web.HTTPNotFound(reason="unknown session")

        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        host._viewers += 1
        host._last_viewer_ts = time.time()
        loop = asyncio.get_event_loop()

        enc: asyncio.subprocess.Process | None = None
        feed_task: asyncio.Task | None = None
        send_task: asyncio.Task | None = None

        async def _feed(proc: asyncio.subprocess.Process, cam: str) -> None:
            period = 1.0 / FRAME_FPS
            while not ws.closed:
                t0 = time.time()
                jpg = await loop.run_in_executor(None, host.grab, cam)
                if jpg and proc.stdin and not proc.stdin.is_closing():
                    try:
                        proc.stdin.write(jpg)
                        await proc.stdin.drain()
                    except Exception:
                        break
                host._last_viewer_ts = time.time()
                elapsed = time.time() - t0
                sleep_left = period - elapsed
                if sleep_left > 0:
                    await asyncio.sleep(sleep_left)

        async def _send(proc: asyncio.subprocess.Process) -> None:
            while not ws.closed:
                try:
                    chunk = await proc.stdout.read(65536)
                except Exception:
                    break
                if not chunk:
                    break
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    break

        async def _start_encoder(cam: str) -> None:
            nonlocal enc, feed_task, send_task
            if feed_task:
                feed_task.cancel()
            if send_task:
                send_task.cancel()
            if enc:
                try:
                    enc.stdin.close()
                    enc.kill()
                except Exception:
                    pass
            fps = max(1, int(FRAME_FPS))
            enc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error",
                "-f", "mjpeg", "-r", str(fps), "-i", "pipe:0",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "baseline", "-level", "3.1",
                "-g", "1", "-bf", "0",
                "-f", "mp4",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            feed_task = asyncio.ensure_future(_feed(enc, cam))
            send_task = asyncio.ensure_future(_send(enc))

        try:
            await _start_encoder("scene")
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        d = json.loads(msg.data)
                    except Exception:
                        continue
                    if d.get("type") == "subscribe":
                        new_cam = d.get("cam") or "scene"
                        await ws.send_str(json.dumps({"type": "cam_reset"}))
                        await _start_encoder(new_cam)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            if feed_task:
                feed_task.cancel()
            if send_task:
                send_task.cancel()
            if enc:
                try:
                    enc.stdin.close()
                    enc.kill()
                except Exception:
                    pass
            host._viewers -= 1
            host._last_viewer_ts = time.time()
        return ws

    app.router.add_get("/healthz", healthz)
    app.router.add_post("/session", session_start)
    app.router.add_route("OPTIONS", "/session", options)
    app.router.add_post("/session/{id}/end", session_end)
    app.router.add_get("/stream", stream)

    async def _on_start(_app):
        asyncio.ensure_future(host.idle_gc())
    app.on_startup.append(_on_start)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--sock", default="tcp://127.0.0.1:9999",
                    help="engine IPC addr (tcp://host:port or unix path)")
    ap.add_argument("--tcp-port", type=int, default=9999)
    ap.add_argument("--certs-base", required=True)
    ap.add_argument("--pool", default="quad:5,rover:5",
                    help="daemon pool to pre-boot (ids must have certs under --certs-base)")
    ap.add_argument("--default-env", default="office")
    ap.add_argument("--godot", default=None)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--xvfb", action="store_true")
    ap.add_argument("--display", default=":99")
    ap.add_argument("--no-launch", action="store_true",
                    help="don't launch Godot/pool (assume already running) — local dev")
    ap.add_argument("--no-register", action="store_true",
                    help="skip DynamoDB registration — dev/test without AWS creds")
    ap.add_argument("--rendering-driver", default=None,
                    help="Godot rendering driver override (e.g. opengl3 for CPU instances)")
    ap.add_argument("--region", default="us-west-2")
    args = ap.parse_args()

    host = SimHost(args)
    host.boot()
    web.run_app(make_app(host), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
