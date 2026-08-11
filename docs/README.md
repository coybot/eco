# Presidio Platform docs

Everything here ships in this repo — no external doc site, no dependency on
Presidio-hosted infrastructure to read or follow along.

## Getting started

- **[Quickstart: Ground Control Station](quickstart-gcs.md)** — the
  zero-dependency path and the app's default. No AWS account needed.
- **[Quickstart: bring your own AWS account](quickstart-aws.md)** — the
  cloud control plane, on your own infrastructure.
- **[Simulation (SITL)](simulation.md)** — try the SDK with no hardware at all.
- **[Install on a companion computer](install-companion.md)** — Orin, Raspberry Pi.

## Reference

- **[Config reference](config-reference.md)** — every `config.yaml` key.
- **[SDK reference](sdk-reference.md)** — `drone.common.drone_sdk` function reference.
- **[API reference](api-reference.md)** — the REST routes `control/` implements.
- **[Control plane](control-plane.md)** — AWS vs GCS backends, what degrades in GCS mode.

## How it fits together

- **[Command flow](command-flow.md)** — a chat message's full path from the
  app to a MAVLink command on the flight controller.
- **[WiFi provisioning](provisioning.md)** — the hotspot onboarding flow and
  the drone's `/info`/`/configure` HTTP contract.
- **[Certificates](certificates.md)** — minting your own AWS IoT claim
  certificate and provisioning template (AWS mode only).
- **[Offline-first](offline-first.md)** — the phone-hosted Core ML path, for
  when there's no other hardware and only structured commands are needed.

## Simulation & benchmarks

- **[L5 fleet autonomy benchmark](l5-benchmark.md)** — the zero-intervention
  reactive navigation stack; how to reproduce the results and run it on your
  own vehicle.
- **[Isaac Sim](isaac-sim.md)** — perception-in-the-loop simulation with
  cameras, ROS 2, and Nav2.

## What is and isn't true

The L5 benchmark and the simulation-validated results in this repo are
scoped precisely in their own docs — read
[`l5-benchmark.md`](l5-benchmark.md)'s "What is and isn't true" section
before flying anything based on them. Nothing here is a certification.
