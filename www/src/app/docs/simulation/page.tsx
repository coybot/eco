import { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Run in Simulation",
  description:
    "Run the Astral SDK against a simulator — ArduPilot SITL or Isaac Sim — without a drone.",
};

export default function SimulationPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <Link
              href="/docs"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-6"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Documentation
            </Link>
            <h1 className="text-4xl font-bold mb-4">Run in Simulation</h1>
            <p className="text-muted-foreground">
              Two ways to run the Astral SDK without hardware: lightweight
              ArduPilot SITL for API-level work, and Isaac Sim for full
              perception-in-the-loop autonomy.
            </p>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl space-y-6">
            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Option 1 — ArduPilot SITL (lightweight)</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 text-muted-foreground">
                <p>
                  No GPU, no Astral hardware. ~5 min setup. Best for trying
                  the SDK API and validating flight logic before real flight.
                </p>
                <p>
                  Start SITL in one terminal, then point the SDK at it via
                  an env var:
                </p>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto">
{`# Terminal 1
sim_vehicle.py -v ArduCopter --console --map

# Terminal 2
pip install astral-sdk
export ASTRAL_SDK_SERIAL_PORT=tcp:127.0.0.1:5760
python -c "
import time, astral_sdk as drone
drone.takeoff(2.0)
time.sleep(5)
drone.land()
drone.disconnect()
"`}
                </pre>
                <p>
                  Runnable example:{" "}
                  <a
                    className="underline hover:text-foreground"
                    href="https://github.com/astral-us/astral-sdk/tree/main/examples/sitl"
                  >
                    astral-sdk/examples/sitl
                  </a>
                  .
                </p>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Option 2 — Isaac Sim (perception-in-the-loop)</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 text-muted-foreground">
                <p>
                  Full 3D world with cameras, physics, and the same MAVLink
                  + ROS 2 + Nav2 stack you'd run on real hardware. Requires
                  an NVIDIA RTX GPU and ~50 GB of disk.
                </p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="overflow-hidden rounded-md border border-border bg-black/40">
                    <video
                      className="aspect-video w-full object-cover"
                      autoPlay
                      muted
                      loop
                      playsInline
                      preload="metadata"
                      poster="/docs/simulation/isaac-sim-scene-1.jpg"
                      aria-label="Simulated warehouse scene with aerial and ground vehicles"
                    >
                      <source
                        src="/docs/simulation/isaac-sim-scene-1.mp4"
                        type="video/mp4"
                      />
                    </video>
                  </div>
                  <div className="overflow-hidden rounded-md border border-border bg-black/40">
                    <video
                      className="aspect-video w-full object-cover"
                      autoPlay
                      muted
                      loop
                      playsInline
                      preload="metadata"
                      poster="/docs/simulation/isaac-sim-scene-2.jpg"
                      aria-label="Simulated outdoor scene with multiple autonomous vehicles"
                    >
                      <source
                        src="/docs/simulation/isaac-sim-scene-2.mp4"
                        type="video/mp4"
                      />
                    </video>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground/80">
                  Recorded in Isaac Sim — representative of the environments
                  and vehicle mix this stack is built for.
                </p>
                <p>
                  How the pieces connect: your Python calls{" "}
                  <code>astral_sdk</code>, which talks MAVLink to ArduPilot
                  SITL, which is driven by Isaac Sim's physics. The{" "}
                  <code>astral_drone</code> ROS 2 package bridges Nav2 goals
                  into MAVLink velocity setpoints.
                </p>
                <p>
                  Bring up Isaac with an ArduPilot bridge (
                  <a
                    className="underline hover:text-foreground"
                    href="https://github.com/PegasusSimulator/PegasusSimulator"
                  >
                    Pegasus Simulator
                  </a>{" "}
                  is a good starting scene), then connect:
                </p>
                <pre className="rounded-md border border-border bg-background p-4 text-sm overflow-x-auto">
{`export ASTRAL_SDK_SERIAL_PORT=tcp:127.0.0.1:5760

# Build and launch the ROS 2 package
cd ros2_ws
colcon build --packages-select astral_drone
source install/setup.bash
ros2 launch astral_drone bringup.launch.py`}
                </pre>
                <p>
                  Send Nav2 goals from RViz or{" "}
                  <code>ros2 action send_goal</code> and the drone moves in
                  Isaac. Pin a known-good Isaac Sim version — NVIDIA breaks
                  APIs on a roughly quarterly cadence.
                </p>
              </CardContent>
            </Card>

            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle>Why bother</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-muted-foreground">
                <p>
                  Real drones are expensive and slow to iterate against.
                  Most autonomy bugs (frame conventions, mode-switch races,
                  velocity-clamp surprises) surface in sim on a laptop, in
                  minutes, with no risk to hardware.
                </p>
                <p>
                  The SDK API is identical between sim and real flight —
                  the only thing that changes is{" "}
                  <code>ASTRAL_SDK_SERIAL_PORT</code>. What flies in sim
                  flies on the drone.
                </p>
              </CardContent>
            </Card>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
