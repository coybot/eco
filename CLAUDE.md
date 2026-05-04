# Ecosystem — Claude working notes

Internal developer notes. **Do not put hostnames, passwords, AWS account IDs, bucket names, or device certificates in this file** — use a private scratchpad for those.

## Test hardware

- Document companion hostname, Tailscale name, and LAN IP in your private notes, not here.
- Prefer SSH keys over password auth for Jetson access.
- Flight controller: document the stable USB path from `/dev/serial/by-id/` on the aircraft, and set that path in `config.yaml` (`serial_port`).

## On-device layout

Install scripts copy `drone/common/*.py` flat into `~/drone-api/` (or your chosen `INSTALL_DIR`). `config.yaml` and `certs/` live next to `daemon.py` and `drone_sdk.py`.

## Cloud vs local control

- **Cloud**: `daemon.py` connects to AWS IoT Core using `certs/` + `iot_endpoint` in `config.yaml`, subscribes to command topics, executes sandboxed Python that calls `drone_sdk`.
- **Local**: `run_prompt.py` runs the Track A perception loop over SSH; it uses the same `config.yaml` defaults for MAVLink when you do not pass `--mav-port` / `--mav-baud`.

`drone_sdk._connect()` resolves the flight controller MAVLink `target_system` / `target_component` the same way as `run_prompt.py` (prefer ArduPilot heartbeats; avoid bogus `sys=0`).

## Safety defaults

- Confirm disarmed and props-off before motor tests.
- Prefer conservative throttle and duration for ESC checks.
- Always use a stable serial by-id path, not a reorder-sensitive `/dev/ttyACM*`.

## Tooling

- Prefer `uv` for Python environments where applicable (see team conventions).

## Papers / perception stack

Design references live under `~/code/ys/a/papers/` on some workstations (not shipped in this repo). Product docs for the VLM + planner flow are under `docs/`.

## Public / private split (github.com/astral-us)

As of 2026-04-28, `astral-us/astral-sdk` is the public Apache-2.0 SDK. Keep the split:

**Public** (extracted into `ecosystem/astral-sdk/`, pushed to `astral-us/astral-sdk`):
- `drone/common/drone_sdk.py` → `astral_sdk.drone` — MAVLink primitives, **stripped of S3/IoT/cred code** (`_get_iot_credentials`, `_get_s3`, `upload_photo` deleted; `capture_photo` is local-only).
- `drone/common/arm_disarm.py`, `motor_test.py` — CLI utilities.
- `drone/camera/` (oakdlite, intelD435i, base) — camera abstraction.
- `drone/ros2_ws/src/astral_drone/` — ROS 2 Nav2 bridge.

**Public docs** (`ecosystem/astral-docs/` → `astral-us/astral-docs`, Apache-2.0): Mintlify source for astral.us/docs. Currently only `introduction.mdx` + `quickstart.mdx`; copies of these also live in `ecosystem/www/docs/` for the in-site renderer. Keep them in sync when editing — or pick one as the source of truth and script the copy.

**Private — never push to astral-us**:
- `aws/` — Lambda handlers, system prompts, SAM template (the cloud product moat).
- `client/ios/` — closed-source iOS app.
- `drone/common/{daemon,fleet_provisioning,provisioning,wifi_manager,network_manager,factory_reset}.py` — cloud-coupled / operational.
- `drone/common/{vlm,reasoning_loop,target_selector,where,grounding,spatial_memory,reactive_planner,mission_runner,perception}.py` — on-device VLM / autonomy IP.
- `drone/common/{video_producer,video_stream,local_control_api,run_prompt,test_telemetry}.py` — couple to AWS KVS / IoT topics.
- `drone/installer/`, `drone/platforms/`, `drone/models/` — operational/installer/cloud-coupled.

When extracting more SDK pieces, sanitize: no AWS account IDs, no bucket/topic names, no cert paths, no `~/drone-api/` or `/home/orin-admin/` hardcoded paths. Use env-var-overridable defaults (`ASTRAL_SDK_*`).

**Why no separate `astral-camera` or `astral-examples` repos:** the camera drivers and examples already ship inside `astral-sdk` (`astral_sdk.camera` + `examples/`). Creating separate repos would either duplicate code or be empty pointers. If demand emerges (e.g., camera drivers used outside Astral hardware), revisit.

**Why a separate `astral-docs` repo (vs. just `www/docs/`):** Mintlify expects its own repo with `mint.json` at the root and is the standard way to render at `astral.us/docs`. The repo also doubles as a low-friction PR target for community typo/clarity fixes that wouldn't justify cloning the full website.

## Marketing positioning (Option C, as of 2026-04-28)

Live tagline on astral.us: **"Drones that finish the mission when the network doesn't."**

Subhead: *Astral runs vision-language models on-device, so missions complete with no GPS, no comms, no operator. NDAA-compliant compute, open Python SDK.*

Properties to preserve when editing copy:
- Single audience focus (defense / public-safety wedge primary; developer wedge secondary on `/developers`-style pages).
- Specific differentiators: NDAA, GPS-denied, comms-denied, on-device Qwen3-VL.
- Avoid: "Built on Open Source", "Empowering Developers, Enterprises, and Innovators", "Agentic, Agile Unmanned Systems", or any three-audience tagline.

## Website deploy (SST + OpenNext)

- AWS profile: `astral`. Region: us-east-1.
- Stages: `prod` = live astral.us (custom domain). Any other stage = preview at `*.cloudfront.net`.
- Always preview before prod for marketing copy changes.

```bash
cd ecosystem/www
npm run build                                                    # verify build
AWS_PROFILE=astral node_modules/.bin/sst deploy --stage preview  # preview URL
AWS_PROFILE=astral node_modules/.bin/sst deploy --stage prod     # live astral.us
```

`sst.config.ts` sets `protect: true` and `removal: retain` only on prod stage — preview stages can be torn down with `sst remove --stage <stage>`.

## GitHub identity & auth

- Astral org: https://github.com/astral-us. Active gh CLI account for Astral work: `yusuf-astral` (org admin).
- Never use `jsaib_achr` for astral-us — that's the user's employer account.
- Default SSH key on this laptop is registered to `jsaib_achr`, so SSH remotes to astral-us repos **will fail**. **Use HTTPS remotes.**
- Git identity is auto-set under `~/code/ys/a/` via `~/.gitconfig`'s `[includeIf "gitdir:~/code/ys/a/"]` → `~/.gitconfig-astral`. That file pins `user.name = yusuf-astral`, `user.email = 218167113+yusuf-astral@users.noreply.github.com`, and configures `gh auth git-credential` as the credential helper for github.com.
- For new astral-us repos: create the working directory under `~/code/ys/a/`, `git init`, push over HTTPS — identity and credentials Just Work via the includeIf.

## Mistakes to avoid (learned the hard way)

- **Never embed `gh auth token` in a git remote URL** — token leaks to terminal output and `.git/config`. Use the `gh auth git-credential` helper instead (already configured under `ys/a`).
- **`gh repo create --source --push` can half-succeed**: repo gets created on GitHub, but if push fails (e.g. SSH key mismatch), you have to `git remote set-url` to HTTPS and `git push -u origin main` manually.
- **For marketing copy changes, always deploy to a preview SST stage first.** Preview is cheap (~$0/month if torn down after) and saves the "oops the homepage tagline is broken on astral.us" Twitter post.
