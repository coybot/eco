import Link from "next/link"
import { Header, Footer } from "@/components/layout"
import {
  Download,
  Globe,
  Smartphone,
  Zap,
  Cloud,
  Lock,
  Radio,
  Map,
  MessageSquare,
  ChevronRight,
} from "lucide-react"

export const metadata = {
  title: "Astral Operator — Web, iOS & Android | Astral",
  description: "Access Astral Operator on your preferred platform — web, iOS, or Android. Manage your drone fleet from anywhere.",
}

export default function AppsPage() {
  return (
    <div className="min-h-screen flex flex-col bg-black">
      <Header />
      <main className="flex-1">
        {/* Hero Section */}
        <section className="relative overflow-hidden px-4 py-20 sm:py-24 lg:py-32">
          <div className="max-w-4xl mx-auto">
            <div className="flex justify-center mb-8">
              <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/20 bg-amber-500/5 px-4 py-2">
                <Radio className="size-4 text-amber-500" />
                <span className="text-xs font-medium text-amber-500">Available Everywhere</span>
              </div>
            </div>
            <h1 className="text-center text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight text-white mb-6">
              Astral Operator
            </h1>
            <p className="text-center text-lg text-gray-400 max-w-2xl mx-auto">
              Control your drone fleet from any device. Web, iOS, or Android — choose what works best for you.
            </p>
          </div>
        </section>

        {/* Platform Cards */}
        <section className="px-4 py-12 max-w-5xl mx-auto">
          <div className="grid md:grid-cols-3 gap-6 mb-12">
            {/* Web Card */}
            <div className="group relative">
              <div className="absolute inset-0 bg-gradient-to-br from-amber-500/10 to-transparent rounded-2xl blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <div className="relative rounded-2xl border border-gray-800 bg-gray-900/50 backdrop-blur overflow-hidden hover:border-amber-500/30 transition-colors duration-300">
                <div className="p-8">
                  {/* Icon */}
                  <div className="inline-flex items-center justify-center size-14 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-6">
                    <Globe className="size-7 text-amber-500" />
                  </div>

                  {/* Content */}
                  <h3 className="text-xl font-semibold text-white mb-2">Web</h3>
                  <p className="text-sm text-gray-400 mb-6">
                    Access from any modern browser. Full fleet management from your desktop or tablet.
                  </p>

                  {/* Features */}
                  <ul className="space-y-3 mb-8">
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Works on Mac, Windows, Linux</span>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Real-time drone monitoring</span>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Mission planning & execution</span>
                    </li>
                  </ul>

                  {/* Requirements */}
                  <div className="bg-gray-800/50 rounded-lg p-4 mb-6">
                    <p className="text-xs font-semibold text-gray-400 uppercase mb-2">Requirements</p>
                    <p className="text-sm text-gray-300">Chrome, Firefox, Safari, or Edge (latest)</p>
                  </div>

                  {/* CTA */}
                  <Link
                    href="/operator"
                    className="flex items-center justify-center gap-2 w-full px-4 py-3 rounded-xl bg-amber-500 hover:bg-amber-600 text-black text-sm font-medium transition-colors duration-200"
                  >
                    Launch Web App
                    <ChevronRight className="size-4" />
                  </Link>
                </div>
              </div>
            </div>

            {/* iOS Card */}
            <div className="group relative">
              <div className="absolute inset-0 bg-gradient-to-br from-amber-500/10 to-transparent rounded-2xl blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <div className="relative rounded-2xl border border-gray-800 bg-gray-900/50 backdrop-blur overflow-hidden hover:border-amber-500/30 transition-colors duration-300">
                <div className="p-8">
                  {/* Icon */}
                  <div className="inline-flex items-center justify-center size-14 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-6">
                    <Smartphone className="size-7 text-amber-500" />
                  </div>

                  {/* Content */}
                  <h3 className="text-xl font-semibold text-white mb-2">iOS</h3>
                  <p className="text-sm text-gray-400 mb-6">
                    Native app for iPhone and iPad. Optimized for field operations.
                  </p>

                  {/* Features */}
                  <ul className="space-y-3 mb-8">
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">iPhone & iPad support</span>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Offline mode for setup</span>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Live video streaming</span>
                    </li>
                  </ul>

                  {/* Requirements */}
                  <div className="bg-gray-800/50 rounded-lg p-4 mb-6">
                    <p className="text-xs font-semibold text-gray-400 uppercase mb-2">Requirements</p>
                    <p className="text-sm text-gray-300">iOS 14.0 or later</p>
                  </div>

                  {/* CTA */}
                  <a
                    href="/enterprise"
                    className="flex items-center justify-center gap-2 w-full px-4 py-3 rounded-xl bg-amber-500 hover:bg-amber-600 text-black text-sm font-medium transition-colors duration-200"
                  >
                    Request Access
                    <ChevronRight className="size-4" />
                  </a>
                </div>
              </div>
            </div>

            {/* Android Card */}
            <div className="group relative">
              <div className="absolute inset-0 bg-gradient-to-br from-amber-500/10 to-transparent rounded-2xl blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <div className="relative rounded-2xl border border-gray-800 bg-gray-900/50 backdrop-blur overflow-hidden hover:border-amber-500/30 transition-colors duration-300">
                <div className="p-8">
                  {/* Icon */}
                  <div className="inline-flex items-center justify-center size-14 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-6">
                    <Radio className="size-7 text-amber-500" />
                  </div>

                  {/* Content */}
                  <h3 className="text-xl font-semibold text-white mb-2">Android</h3>
                  <p className="text-sm text-gray-400 mb-6">
                    Native app for Android phones and tablets. Same powerful features as iOS.
                  </p>

                  {/* Features */}
                  <ul className="space-y-3 mb-8">
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Wide device compatibility</span>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Offline setup support</span>
                    </li>
                    <li className="flex items-start gap-3">
                      <CheckIcon />
                      <span className="text-sm text-gray-300">Real-time telemetry</span>
                    </li>
                  </ul>

                  {/* Requirements */}
                  <div className="bg-gray-800/50 rounded-lg p-4 mb-6">
                    <p className="text-xs font-semibold text-gray-400 uppercase mb-2">Requirements</p>
                    <p className="text-sm text-gray-300">Android 10.0 or later</p>
                  </div>

                  {/* CTA */}
                  <a
                    href="/enterprise"
                    className="flex items-center justify-center gap-2 w-full px-4 py-3 rounded-xl bg-amber-500 hover:bg-amber-600 text-black text-sm font-medium transition-colors duration-200"
                  >
                    Request Access
                    <ChevronRight className="size-4" />
                  </a>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Key Features Section */}
        <section className="px-4 py-16 max-w-5xl mx-auto">
          <h2 className="text-3xl font-bold text-white mb-4 text-center">
            Consistent Experience Across Platforms
          </h2>
          <p className="text-center text-gray-400 mb-12 max-w-2xl mx-auto">
            Whether you choose web, iOS, or Android, you get the same powerful features and seamless integration with your fleet.
          </p>

          <div className="grid md:grid-cols-2 gap-6">
            <FeatureCard
              icon={<Radio className="size-5 text-amber-500" />}
              title="Real-Time Fleet Monitoring"
              description="Live telemetry, battery status, and location tracking for all your drones."
            />
            <FeatureCard
              icon={<Map className="size-5 text-amber-500" />}
              title="Mission Planning"
              description="Create and execute complex missions with visual waypoint planning."
            />
            <FeatureCard
              icon={<Cloud className="size-5 text-amber-500" />}
              title="Cloud Sync"
              description="All data automatically synced across your devices via secure AWS cloud."
            />
            <FeatureCard
              icon={<Lock className="size-5 text-amber-500" />}
              title="Enterprise Security"
              description="AWS Cognito authentication and encrypted communications throughout."
            />
            <FeatureCard
              icon={<MessageSquare className="size-5 text-amber-500" />}
              title="AI-Powered Commands"
              description="Natural language control with our intelligent mission planner."
            />
            <FeatureCard
              icon={<Zap className="size-5 text-amber-500" />}
              title="Instant Responsiveness"
              description="Sub-100ms latency for responsive control in the field."
            />
          </div>
        </section>

        {/* Getting Started Section */}
        <section className="px-4 py-16 max-w-5xl mx-auto">
          <div className="rounded-2xl border border-amber-500/20 bg-amber-500/5 p-12">
            <h2 className="text-3xl font-bold text-white mb-4">Getting Started</h2>
            <p className="text-gray-400 mb-8 max-w-2xl">
              Ready to start managing your drone fleet? Follow these steps to get up and running:
            </p>

            <div className="grid md:grid-cols-3 gap-8">
              <StepCard
                number="1"
                title="Sign Up"
                description="Create an Astral account using your email. Takes less than a minute."
              />
              <StepCard
                number="2"
                title="Download the App"
                description="Get the client for your preferred platform (web, iOS, or Android)."
              />
              <StepCard
                number="3"
                title="Register Your Drone"
                description="Use the iOS or Android app to set up and register your drone to your fleet."
              />
            </div>

            <div className="mt-10 pt-10 border-t border-amber-500/10">
              <p className="text-sm text-gray-400 mb-4">Need help? Check out our documentation or contact support:</p>
              <div className="flex gap-4 flex-wrap">
                <Link
                  href="/docs"
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-amber-500/30 hover:border-amber-500 text-amber-500 text-sm font-medium transition-colors"
                >
                  View Docs
                  <ChevronRight className="size-4" />
                </Link>
                <a
                  href="https://support.astral.us"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-amber-500/30 hover:border-amber-500 text-amber-500 text-sm font-medium transition-colors"
                >
                  Contact Support
                  <ChevronRight className="size-4" />
                </a>
              </div>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  )
}

function CheckIcon() {
  return (
    <div className="flex-shrink-0 rounded-full bg-amber-500/20 p-0.5">
      <svg
        className="size-4 text-amber-500"
        fill="currentColor"
        viewBox="0 0 20 20"
      >
        <path
          fillRule="evenodd"
          d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
          clipRule="evenodd"
        />
      </svg>
    </div>
  )
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode
  title: string
  description: string
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-6 hover:border-amber-500/20 transition-colors">
      <div className="inline-flex items-center justify-center size-10 rounded-lg bg-amber-500/10 border border-amber-500/20 mb-4">
        {icon}
      </div>
      <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
      <p className="text-sm text-gray-400">{description}</p>
    </div>
  )
}

function StepCard({
  number,
  title,
  description,
}: {
  number: string
  title: string
  description: string
}) {
  return (
    <div className="flex flex-col items-start">
      <div className="inline-flex items-center justify-center size-10 rounded-full bg-amber-500 text-black font-bold mb-4">
        {number}
      </div>
      <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
      <p className="text-sm text-gray-400">{description}</p>
    </div>
  )
}
