import { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ArrowLeft, Smartphone, MessageSquare, Radio, Wifi } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { socialMeta } from "@/lib/social-metadata";

const DESC = "Use the Astral iOS or Android app to onboard, command, and monitor your autonomous drones from anywhere via the cloud.";

export const metadata: Metadata = {
  title: "Mobile Apps",
  description: DESC,
  ...socialMeta("/docs/mobile-app", "Mobile Apps | Astral", DESC),
};

const steps = [
  {
    n: "1",
    title: "Download the app",
    body: "Get Drone Operator from the App Store (iOS) or Google Play (Android). Sign in with your Astral account.",
  },
  {
    n: "2",
    title: "Power on your drone",
    body: "Connect the drone to your Wi-Fi hotspot or your local network. The drone auto-registers with AWS IoT Core on first boot.",
  },
  {
    n: "3",
    title: 'Onboard via "Already configured"',
    body: "Tap + → Already configured, enter the drone ID printed on the label (or shown in the companion terminal), and tap Add. The card turns Online within 5–10 seconds once the daemon heartbeats.",
  },
  {
    n: "4",
    title: "Send a command",
    body: "Tap the drone card to open its detail, switch to the Chat tab, type a natural-language command, and hit Send. The cloud pipeline routes it to the drone over MQTT.",
  },
  {
    n: "5",
    title: "Watch the response",
    body: "The drone executes the command, uploads photos or telemetry to S3, and the assistant reply arrives in-chat with embedded images.",
  },
];

export default function MobileAppPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <Link
              href="/docs"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-6"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Documentation
            </Link>
            <div className="flex items-center gap-3 mb-4">
              <div className="inline-flex p-2 rounded-lg bg-amber-500/10">
                <Smartphone className="h-6 w-6 text-amber-500" />
              </div>
              <h1 className="text-4xl font-bold">Mobile Apps</h1>
            </div>
            <p className="text-muted-foreground text-lg max-w-2xl">
              Command any Astral drone from your phone. The iOS and Android apps
              use the same cloud pipeline as the SDK — AWS IoT Core for
              real-time MQTT, S3 for media, and Bedrock for natural-language
              mission planning.
            </p>
          </div>
        </section>

        {/* Platform badges */}
        <section className="py-6 bg-card border-y border-border">
          <div className="container mx-auto px-4 max-w-4xl flex flex-wrap gap-4">
            {[
              { icon: Smartphone, label: "iOS 17+ (iPhone & iPad)" },
              { icon: Smartphone, label: "Android 9+ (Pixel, Samsung)" },
              { icon: Radio, label: "Real-time MQTT over AWS IoT" },
              { icon: Wifi, label: "Works over LTE — no local network needed" },
            ].map(({ icon: Icon, label }) => (
              <div
                key={label}
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <Icon className="h-4 w-4 text-amber-500 shrink-0" />
                {label}
              </div>
            ))}
          </div>
        </section>

        {/* Steps */}
        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-8">Getting started</h2>
            <div className="space-y-4">
              {steps.map((s) => (
                <Card key={s.n} className="bg-card border-border">
                  <CardContent className="p-6 flex gap-5">
                    <div className="flex-none w-9 h-9 rounded-full bg-amber-500/10 flex items-center justify-center text-amber-500 font-bold text-sm">
                      {s.n}
                    </div>
                    <div>
                      <h3 className="font-semibold mb-1">{s.title}</h3>
                      <p className="text-sm text-muted-foreground">{s.body}</p>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* iOS screenshots */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4 max-w-5xl">
            <div className="flex items-center gap-2 mb-2">
              <Smartphone className="h-5 w-5 text-amber-500" />
              <h2 className="text-2xl font-bold">iOS — iPhone 17 Simulator</h2>
            </div>
            <p className="text-muted-foreground text-sm mb-8">
              Captured end-to-end against a live sim drone on this Mac — same
              AWS IoT pipeline as a real flight.
            </p>

            {/* Row 1: sign-in + empty missions + add drone */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              {[
                { file: "ios_00_sign_in.png", caption: "Sign in" },
                { file: "ios_01_missions_empty.png", caption: "Missions (empty)" },
                { file: "ios_02_add_drone_sheet.png", caption: "Add drone sheet" },
              ].map(({ file, caption }) => (
                <figure key={file} className="space-y-2">
                  <div className="rounded-2xl overflow-hidden border border-border bg-background">
                    <Image
                      src={`/media/mobile-app/${file}`}
                      alt={caption}
                      width={390}
                      height={844}
                      className="w-full h-auto"
                    />
                  </div>
                  <figcaption className="text-xs text-center text-muted-foreground">
                    {caption}
                  </figcaption>
                </figure>
              ))}
            </div>

            {/* Row 2: entry + online + drone detail */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              {[
                { file: "ios_03_already_configured_entry.png", caption: "Enter drone ID" },
                { file: "ios_04_missions_online.png", caption: "Drone online" },
                { file: "ios_05_drone_detail.png", caption: "Drone detail" },
              ].map(({ file, caption }) => (
                <figure key={file} className="space-y-2">
                  <div className="rounded-2xl overflow-hidden border border-border bg-background">
                    <Image
                      src={`/media/mobile-app/${file}`}
                      alt={caption}
                      width={390}
                      height={844}
                      className="w-full h-auto"
                    />
                  </div>
                  <figcaption className="text-xs text-center text-muted-foreground">
                    {caption}
                  </figcaption>
                </figure>
              ))}
            </div>

            {/* Row 3: chat sent + reply — wider */}
            <div className="grid grid-cols-2 gap-6 max-w-md mx-auto">
              {[
                { file: "ios_06_chat_sent.png", caption: "Command sent" },
                { file: "ios_07_chat_reply.png", caption: "Assistant reply" },
              ].map(({ file, caption }) => (
                <figure key={file} className="space-y-2">
                  <div className="rounded-2xl overflow-hidden border border-border bg-background">
                    <Image
                      src={`/media/mobile-app/${file}`}
                      alt={caption}
                      width={390}
                      height={844}
                      className="w-full h-auto"
                    />
                  </div>
                  <figcaption className="text-xs text-center text-muted-foreground">
                    {caption}
                  </figcaption>
                </figure>
              ))}
            </div>
          </div>
        </section>

        {/* Android screenshots */}
        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-5xl">
            <div className="flex items-center gap-2 mb-2">
              <Smartphone className="h-5 w-5 text-amber-500" />
              <h2 className="text-2xl font-bold">Android — Pixel 10 Emulator</h2>
            </div>
            <p className="text-muted-foreground text-sm mb-8">
              Same cloud pipeline, Material 3 design. Runs on Android 9+.
            </p>
            <div className="grid grid-cols-3 gap-4 max-w-lg">
              <figure className="space-y-2">
                <div className="rounded-2xl overflow-hidden border border-border bg-background">
                  <Image
                    src="/media/mobile-app/android_sign_in.png"
                    alt="Android — Sign in"
                    width={360}
                    height={800}
                    className="w-full h-auto"
                  />
                </div>
                <figcaption className="text-xs text-center text-muted-foreground">
                  Sign in
                </figcaption>
              </figure>
            </div>
          </div>
        </section>

        {/* Chat feature callout */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-6">Natural-language chat</h2>
            <div className="grid sm:grid-cols-3 gap-6">
              {[
                {
                  icon: MessageSquare,
                  title: "Plain English commands",
                  body: "Type \"take a photo and tell me what you see\" — no API calls required. Bedrock routes to the right drone function.",
                },
                {
                  icon: Radio,
                  title: "Cloud pipeline",
                  body: "Commands travel via MQTT over AWS IoT Core. Replies (with S3 image links) arrive in seconds.",
                },
                {
                  icon: Wifi,
                  title: "Works on cellular",
                  body: "The drone connects to IoT Core directly. You and the drone don't need to be on the same network.",
                },
              ].map(({ icon: Icon, title, body }) => (
                <Card key={title} className="bg-background border-border">
                  <CardContent className="p-6">
                    <div className="inline-flex p-2 rounded-lg bg-amber-500/10 mb-3">
                      <Icon className="h-5 w-5 text-amber-500" />
                    </div>
                    <h3 className="font-semibold mb-2">{title}</h3>
                    <p className="text-sm text-muted-foreground">{body}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* Deep links to other docs */}
        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-xl font-bold mb-6">Related</h2>
            <div className="grid sm:grid-cols-2 gap-4">
              <Link href="/docs/simulation">
                <Card className="bg-card border-border hover:border-amber-500/50 transition-colors cursor-pointer">
                  <CardHeader>
                    <CardTitle className="text-base">Run in simulation</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground">
                      Test the mobile app against a Godot or Isaac sim drone
                      before flying real hardware.
                    </p>
                  </CardContent>
                </Card>
              </Link>
              <Link href="/docs/api">
                <Card className="bg-card border-border hover:border-amber-500/50 transition-colors cursor-pointer">
                  <CardHeader>
                    <CardTitle className="text-base">REST API reference</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground">
                      The same endpoints the app uses — drone registry,
                      conversation history, video start/stop.
                    </p>
                  </CardContent>
                </Card>
              </Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
