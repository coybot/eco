import { Fragment } from "react";
import { Metadata } from "next";
import Link from "next/link";
import { Check, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { products, formatPrice } from "@/lib/products";
import { socialMeta } from "@/lib/social-metadata";

const DESC = "Compare the Coybot autonomous platforms side-by-side — Quadcopter, Rover, Fixed-Wing, and Phrover. Compute, sensors, runtime, payload, and use cases.";

export const metadata: Metadata = {
  title: "Compare Platforms",
  description: DESC,
  ...socialMeta("/compare", "Compare Platforms | Coybot", DESC),
};

// Short platform-type label shown under each product name in the table header.
const platformType: Record<string, string> = {
  quadcopter: "Aerial Platform",
  rover: "Ground Platform",
  "fixed-wing": "Fixed-Wing Platform",
  phrover: "Phone-Powered Platform",
};

// Comparison rows. Each spec's `values` maps a product id → cell value
// (string, or boolean for a check / dash).
type CellValue = string | boolean;
interface ComparisonSpec {
  label: string;
  values: Record<string, CellValue>;
}
interface ComparisonCategory {
  name: string;
  specs: ComparisonSpec[];
}

const comparisonCategories: ComparisonCategory[] = [
  {
    name: "Compute & AI",
    specs: [
      {
        label: "Processor",
        values: {
          quadcopter: "Jetson Orin Nano 8GB",
          rover: "Jetson Orin Nano 8GB",
          "fixed-wing": "Jetson Orin NX 16GB",
          phrover: "Your smartphone (BYO)",
        },
      },
      {
        label: "AI Performance",
        values: {
          quadcopter: "Up to 67 TOPS",
          rover: "Up to 67 TOPS",
          "fixed-wing": "Up to 157 TOPS",
          phrover: "Phone NPU",
        },
      },
      {
        label: "On-Device AI",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: true },
      },
      {
        label: "Cloud LLM Support",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: true },
      },
    ],
  },
  {
    name: "Navigation & Autonomy",
    specs: [
      {
        label: "GPS-Denied Operation",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: false },
      },
      {
        label: "Comm-Denied Operation",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: false },
      },
      {
        label: "Visual-Inertial Navigation",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: false },
      },
      {
        label: "Autonomous Missions",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: true },
      },
    ],
  },
  {
    name: "Sensors & Perception",
    specs: [
      {
        label: "Front Camera",
        values: {
          quadcopter: "Intel RealSense D435",
          rover: "Intel RealSense D435",
          "fixed-wing": "Intel RealSense D435",
          phrover: "Smartphone camera (BYO)",
        },
      },
      {
        label: "Depth Sensing",
        values: {
          quadcopter: "1280x720 @ 90fps",
          rover: "1280x720 @ 90fps",
          "fixed-wing": "1280x720 @ 90fps",
          phrover: "Monocular (phone)",
        },
      },
      {
        label: "Obstacle Avoidance",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: true },
      },
    ],
  },
  {
    name: "Operation",
    specs: [
      {
        label: "Environment",
        values: {
          quadcopter: "Indoor & Outdoor",
          rover: "Indoor & Outdoor",
          "fixed-wing": "Outdoor",
          phrover: "Indoor & Outdoor",
        },
      },
      {
        label: "Mobility",
        values: {
          quadcopter: "Aerial (Quadcopter)",
          rover: "Ground (4WD Rover)",
          "fixed-wing": "Aerial (Fixed-Wing)",
          phrover: "Ground (4WD Rover)",
        },
      },
      {
        label: "Max Speed",
        values: {
          quadcopter: "45 MPH",
          rover: "15 MPH",
          "fixed-wing": "60 MPH",
          phrover: "12 MPH",
        },
      },
      {
        label: "Runtime / Endurance",
        values: {
          quadcopter: "30 Minutes",
          rover: "4 Hours",
          "fixed-wing": "90 Minutes",
          phrover: "4 Hours",
        },
      },
      {
        label: "Payload",
        values: {
          quadcopter: "500g",
          rover: "5kg",
          "fixed-wing": "1kg",
          phrover: "3kg",
        },
      },
    ],
  },
  {
    name: "Physical",
    specs: [
      {
        label: "Weather Rating",
        values: {
          quadcopter: "IP55",
          rover: "IP65",
          "fixed-wing": "IP54",
          phrover: "IP54",
        },
      },
      {
        label: "NDAA Compliant Compute",
        values: { quadcopter: true, rover: true, "fixed-wing": true, phrover: false },
      },
    ],
  },
];

// Per-product "choose this if" reasons for the recommendation cards.
const recommendations: Record<string, string[]> = {
  quadcopter: [
    "You need aerial surveillance or inspection",
    "Rapid deployment and repositioning is critical",
    "Operating over obstacles or rough terrain",
    "You need a bird's eye view perspective",
    "Short mission duration is acceptable",
  ],
  rover: [
    "You need persistent, long-duration missions",
    "Carrying heavier payloads is required",
    "Operating in noise-sensitive environments",
    "Ground-level perimeter security",
    "Indoor warehouse or facility patrol",
  ],
  "fixed-wing": [
    "You need to cover wide areas in one sortie",
    "Long-range mapping, survey, or ISR",
    "Maximum endurance and cruise efficiency matter",
    "You have room to hand- or bungee-launch",
    "The highest on-board compute (Orin NX 16GB) is needed",
  ],
  phrover: [
    "You want the lowest-cost way into autonomy",
    "You already have a capable iPhone or Android",
    "Education, research, or prototyping",
    "Indoor or light outdoor ground missions",
    "NDAA compute is not a requirement",
  ],
};

function SpecValue({ value }: { value: CellValue | null }) {
  if (value === true) {
    return <Check className="h-5 w-5 text-success mx-auto" />;
  }
  if (value === false || value === null) {
    return <span className="text-muted-foreground/50">—</span>;
  }
  return <span>{value}</span>;
}

export default function ComparePage() {
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
                Our platforms share the same autonomy stack and SDK. Choose
                based on your mission requirements.
              </p>
            </div>
          </div>
        </section>

        {/* Comparison Table */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4">
            <div className="max-w-6xl mx-auto overflow-x-auto">
              <table className="w-full min-w-[720px]">
                {/* Product Headers */}
                <thead>
                  <tr>
                    <th className="text-left p-4"></th>
                    {products.map((product) => (
                      <th key={product.id} className="p-4">
                        <div className="space-y-3">
                          <Badge className="bg-amber-500/10 text-amber-500">
                            {product.badge}
                          </Badge>
                          <h3 className="text-xl font-bold">{product.name}</h3>
                          <p className="text-sm text-muted-foreground">
                            {platformType[product.id] ?? "Platform"}
                          </p>
                          <div className="text-2xl font-bold">
                            {formatPrice(product.price)}
                          </div>
                          <Link href={`/products/${product.id}`}>
                            <Button variant="outline" size="sm">Learn More</Button>
                          </Link>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {comparisonCategories.map((category) => (
                    <Fragment key={category.name}>
                      {/* Category Header */}
                      <tr>
                        <td
                          colSpan={products.length + 1}
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
                          {products.map((product) => (
                            <td key={product.id} className="p-4 text-center">
                              <SpecValue value={spec.values[product.id] ?? null} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </Fragment>
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
                {products.map((product) => (
                  <div
                    key={product.id}
                    className="p-6 bg-card rounded-lg border border-border"
                  >
                    <h3 className="text-xl font-bold mb-2">
                      Choose {product.name} if:
                    </h3>
                    <ul className="space-y-2">
                      {(recommendations[product.id] ?? []).map((reason) => (
                        <li
                          key={reason}
                          className="flex items-start space-x-2 text-muted-foreground"
                        >
                          <Check className="h-4 w-4 text-amber-500 mt-1 shrink-0" />
                          <span>{reason}</span>
                        </li>
                      ))}
                    </ul>
                    <Link href={`/products/${product.id}`} className="block mt-6">
                      <Button variant="outline" className="w-full">
                        Learn More
                        <ArrowRight className="ml-2 h-4 w-4" />
                      </Button>
                    </Link>
                  </div>
                ))}
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
