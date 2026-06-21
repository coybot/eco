import { Metadata } from "next";
import Link from "next/link";
import { Check, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { products, formatPrice } from "@/lib/products";
import { socialMeta } from "@/lib/social-metadata";

const DESC = "Compare the M1-A autonomous quadcopter and M1-G ground rover side-by-side — compute, sensors, runtime, payload, and use cases.";

export const metadata: Metadata = {
  title: "Compare Platforms",
  description: DESC,
  ...socialMeta("/compare", "Compare Platforms | Astral", DESC),
};

// Comparison data structure
const comparisonCategories = [
  {
    name: "Compute & AI",
    specs: [
      { label: "Processor", m1a: "Jetson Orin Nano 8GB", m1g: "Jetson Orin Nano 8GB" },
      { label: "AI Performance", m1a: "Up to 67 TOPS", m1g: "Up to 67 TOPS" },
      { label: "On-Device AI", m1a: true, m1g: true },
      { label: "Cloud LLM Support", m1a: true, m1g: true },
    ],
  },
  {
    name: "Navigation & Autonomy",
    specs: [
      { label: "GPS-Denied Operation", m1a: true, m1g: true },
      { label: "Comm-Denied Operation", m1a: true, m1g: true },
      { label: "Visual-Inertial Navigation", m1a: true, m1g: true },
      { label: "Autonomous Missions", m1a: true, m1g: true },
    ],
  },
  {
    name: "Sensors & Perception",
    specs: [
      { label: "Front Camera", m1a: "Intel RealSense D435", m1g: "Intel RealSense D435" },
      { label: "Resolution", m1a: "1920x1080 @ 30fps", m1g: "1920x1080 @ 30fps" },
      { label: "Depth Sensing", m1a: "1280x720 @ 90fps", m1g: "1280x720 @ 90fps" },
      { label: "Obstacle Avoidance", m1a: true, m1g: true },
    ],
  },
  {
    name: "Operation",
    specs: [
      { label: "Environment", m1a: "Indoor & Outdoor", m1g: "Indoor & Outdoor" },
      { label: "Mobility", m1a: "Aerial (Quadcopter)", m1g: "Ground (4WD Rover)" },
      { label: "Max Speed", m1a: "45 MPH", m1g: "15 MPH" },
      { label: "Runtime", m1a: "30 Minutes", m1g: "4 Hours" },
      { label: "Payload", m1a: "500g", m1g: "5kg" },
    ],
  },
  {
    name: "Physical",
    specs: [
      { label: "Weather Rating", m1a: "IP55", m1g: "IP65" },
      { label: "NDAA Compliant Compute", m1a: true, m1g: true },
    ],
  },
];

function SpecValue({ value }: { value: string | boolean | null }) {
  if (value === true) {
    return <Check className="h-5 w-5 text-success mx-auto" />;
  }
  if (value === false || value === null) {
    return <span className="text-muted-foreground/50">—</span>;
  }
  return <span>{value}</span>;
}

export default function ComparePage() {
  const m1a = products.find((p) => p.id === "m1a")!;
  const m1g = products.find((p) => p.id === "m1g")!;

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl font-bold mb-6">
                Compare Platforms
              </h1>
              <p className="text-lg text-muted-foreground">
                Both platforms share the same NDAA-compliant compute and sensors.
                Choose based on your mission requirements.
              </p>
            </div>
          </div>
        </section>

        {/* Comparison Table */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4">
            <div className="max-w-4xl mx-auto overflow-x-auto">
              <table className="w-full">
                {/* Product Headers */}
                <thead>
                  <tr>
                    <th className="text-left p-4 w-1/3"></th>
                    <th className="p-4 w-1/3">
                      <div className="space-y-3">
                        <Badge className="bg-amber-500/10 text-amber-500">
                          {m1a.badge}
                        </Badge>
                        <h3 className="text-xl font-bold">{m1a.name}</h3>
                        <p className="text-sm text-muted-foreground">Aerial Platform</p>
                        <div className="text-2xl font-bold">
                          {formatPrice(m1a.price)}
                        </div>
                        <Link href={`/products/${m1a.id}`}>
                          <Button variant="outline">Learn More</Button>
                        </Link>
                      </div>
                    </th>
                    <th className="p-4 w-1/3">
                      <div className="space-y-3">
                        <Badge className="bg-amber-500/10 text-amber-500">
                          {m1g.badge}
                        </Badge>
                        <h3 className="text-xl font-bold">{m1g.name}</h3>
                        <p className="text-sm text-muted-foreground">Ground Platform</p>
                        <div className="text-2xl font-bold">
                          {formatPrice(m1g.price)}
                        </div>
                        <Link href={`/products/${m1g.id}`}>
                          <Button variant="outline">Learn More</Button>
                        </Link>
                      </div>
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {comparisonCategories.map((category) => (
                    <>
                      {/* Category Header */}
                      <tr key={category.name}>
                        <td
                          colSpan={3}
                          className="bg-background px-4 py-3 font-semibold text-lg border-t border-border"
                        >
                          {category.name}
                        </td>
                      </tr>
                      {/* Specs */}
                      {category.specs.map((spec) => (
                        <tr
                          key={spec.label}
                          className="border-b border-border hover:bg-background/50 transition-colors"
                        >
                          <td className="p-4 text-muted-foreground">
                            {spec.label}
                          </td>
                          <td className="p-4 text-center">
                            <SpecValue value={spec.m1a} />
                          </td>
                          <td className="p-4 text-center">
                            <SpecValue value={spec.m1g} />
                          </td>
                        </tr>
                      ))}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* Recommendation Section */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4">
            <div className="max-w-4xl mx-auto">
              <h2 className="text-2xl font-bold mb-8 text-center">
                Which Platform is Right for You?
              </h2>
              <div className="grid md:grid-cols-2 gap-8">
                {/* M1-A */}
                <div className="p-6 bg-card rounded-lg border border-border">
                  <h3 className="text-xl font-bold mb-2">Choose M1-A if:</h3>
                  <ul className="space-y-2">
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>You need aerial surveillance or inspection</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Rapid deployment and repositioning is critical</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Operating over obstacles or rough terrain</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>You need a bird&apos;s eye view perspective</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Short mission duration is acceptable</span>
                    </li>
                  </ul>
                  <Link href="/products/m1a" className="block mt-6">
                    <Button variant="outline" className="w-full">
                      Learn More
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                  </Link>
                </div>

                {/* M1-G */}
                <div className="p-6 bg-card rounded-lg border border-border">
                  <h3 className="text-xl font-bold mb-2">Choose M1-G if:</h3>
                  <ul className="space-y-2">
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>You need persistent, long-duration missions</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Carrying heavier payloads is required</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Operating in noise-sensitive environments</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Ground-level perimeter security</span>
                    </li>
                    <li className="flex items-start space-x-2 text-muted-foreground">
                      <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                      <span>Indoor warehouse or facility patrol</span>
                    </li>
                  </ul>
                  <Link href="/products/m1g" className="block mt-6">
                    <Button variant="outline" className="w-full">
                      Learn More
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                  </Link>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4 text-center">
            <h2 className="text-2xl font-bold mb-4">Still have questions?</h2>
            <p className="text-muted-foreground mb-6">
              Our team is here to help you find the right solution.
            </p>
            <Link href="/enterprise">
              <Button size="lg">Contact Sales</Button>
            </Link>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
