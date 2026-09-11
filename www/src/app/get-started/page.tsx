import type { Metadata } from "next";
import Link from "next/link";
import { Github } from "lucide-react";
import { JsonLd } from "@/components/json-ld";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const TITLE = "Run the Autonomy Stack in Simulation — No Hardware Required";
const DESCRIPTION =
  "Three paths to a running autonomy stack: install the SDK, fly it against ArduPilot SITL, or go full perception-in-the-loop with Isaac Sim. No hardware needed for any of them.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  ...socialMeta("/get-started", TITLE, DESCRIPTION),
};

const jsonLd = [
  {
    "@context": "https://schema.org",
    "@type": "HowTo",
    name: TITLE,
    description: DESCRIPTION,
    step: [
      { "@type": "HowToStep", name: "Install the SDK", text: "pip install git+https://github.com/presidio-autonomy/presidio-sdk" },
      { "@type": "HowToStep", name: "Run against SITL", text: "Point the SDK at a running ArduPilot SITL instance over a TCP MAVLink endpoint." },
      { "@type": "HowToStep", name: "Run the benchmark", text: "Run the 16-scenario adversarial benchmark in eco and read the scorecard." },
    ],
  },
  {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Home", item: SITE.origin },
      { "@type": "ListItem", position: 2, name: "Get Started", item: `${SITE.origin}/get-started` },
    ],
  },
];

export default function GetStartedPage() {
  return (
    <>
      <JsonLd data={jsonLd} />
      <section className="py-16 bg-gradient-to-b from-background to-card">
        <div className="container mx-auto px-4 max-w-3xl">
          <h1 className="text-4xl sm:text-5xl font-bold mb-6">{TITLE}</h1>
          <p className="text-lg text-muted-foreground mb-10">
            Why simulation first?{" "}
            <Link href="/autonomy#sim-first" className="text-amber-500 hover:underline">
              Offline metrics lie
            </Link>
            {" "}— closed-loop trials in sim are the only honest test, and you can run
            thousands of them before anything touches hardware.
          </p>

          <Tabs defaultValue="sdk" className="w-full">
            <TabsList className="grid grid-cols-3 w-full">
              <TabsTrigger value="sdk">SDK quickstart</TabsTrigger>
              <TabsTrigger value="sitl">SITL simulation</TabsTrigger>
              <TabsTrigger value="isaac">Isaac Sim</TabsTrigger>
            </TabsList>

            <TabsContent value="sdk" className="space-y-4 pt-6">
              <h2 className="text-xl font-semibold">Install the SDK</h2>
              <p className="text-muted-foreground">
                Not on PyPI yet — install straight from GitHub:
              </p>
              <pre className="rounded-md bg-background border border-border p-3 text-sm overflow-x-auto">
                <code>pip install git+https://github.com/presidio-autonomy/presidio-sdk</code>
              </pre>
              <p className="text-muted-foreground">A minimal takeoff-and-land script:</p>
              <pre className="rounded-md bg-background border border-border p-3 text-sm overflow-x-auto">
                <code>{`from presidio_sdk import Drone

drone = Drone("tcp:127.0.0.1:5760")  # or a real serial port
drone.arm()
drone.takeoff(altitude_m=5)
drone.land()`}</code>
              </pre>
            </TabsContent>

            <TabsContent value="sitl" className="space-y-4 pt-6">
              <h2 className="text-xl font-semibold">Run against ArduPilot SITL</h2>
              <p className="text-muted-foreground">
                No drone, no GPU. You need Python 3.10+ and ArduPilot SITL
                (<code>sim_vehicle.py</code> — see{" "}
                <a href="https://ardupilot.org/dev/docs/sitl-with-gazebo.html" target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
                  ardupilot.org/dev
                </a>
                ).
              </p>
              <p className="text-muted-foreground font-medium">1. Start the simulator:</p>
              <pre className="rounded-md bg-background border border-border p-3 text-sm overflow-x-auto">
                <code>sim_vehicle.py -v ArduCopter --console --map</code>
              </pre>
              <p className="text-muted-foreground font-medium">2. Point the SDK at it:</p>
              <pre className="rounded-md bg-background border border-border p-3 text-sm overflow-x-auto">
                <code>{`export PRESIDIO_SDK_SERIAL_PORT=tcp:127.0.0.1:5760`}</code>
              </pre>
              <p className="text-sm text-muted-foreground">
                Full walkthrough:{" "}
                <a href={`${SITE.presidioDocs}/blob/main/simulation.mdx`} target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
                  presidio-docs/simulation.mdx
                </a>
              </p>
            </TabsContent>

            <TabsContent value="isaac" className="space-y-4 pt-6">
              <h2 className="text-xl font-semibold">Full perception-in-the-loop with Isaac Sim</h2>
              <p className="text-muted-foreground">
                Needs an NVIDIA RTX GPU and ~50 GB disk — heavier than the SITL path above.
                Pairs Isaac Sim with ArduPilot SITL and the ROS 2 package shipped in
                presidio-sdk (<code>ros2_ws/src/presidio_drone/</code>) so you fly the same
                stack you&rsquo;d run on real hardware.
              </p>
              <p className="text-muted-foreground">Requirements: Isaac Sim 4.0+, ROS 2 Humble or Jazzy with Nav2, ArduPilot SITL with the Isaac plugin or mavros bridge.</p>
              <p className="text-sm text-muted-foreground">
                Full walkthrough:{" "}
                <a href={`${SITE.presidioDocs}/blob/main/isaac-sim.mdx`} target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">
                  presidio-docs/isaac-sim.mdx
                </a>
              </p>
            </TabsContent>
          </Tabs>

          <div className="mt-12 border-t border-border pt-8">
            <h2 className="text-xl font-semibold mb-3">Run the benchmark</h2>
            <p className="text-muted-foreground">
              Once the stack is running, the real test is the 16-scenario adversarial
              benchmark in <a href={SITE.eco} target="_blank" rel="noopener noreferrer" className="text-amber-500 hover:underline">eco</a> —
              sensor dropout, GPS spoofing, dynamic intruders, chokepoints — and reading
              your own scorecard against the numbers in{" "}
              <Link href="/autonomy#metric-gap" className="text-amber-500 hover:underline">
                what we measured
              </Link>
              .
            </p>
          </div>

          <div className="mt-8 border-t border-border pt-8">
            <h2 className="text-xl font-semibold mb-3">Something broken?</h2>
            <p className="text-muted-foreground mb-4">
              Open an issue or discussion on GitHub — that&rsquo;s the fastest way to reach us.
            </p>
            <Button asChild variant="outline">
              <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
                <Github className="mr-2 h-4 w-4" />
                github.com/presidio-autonomy
              </a>
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
