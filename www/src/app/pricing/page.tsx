import { Metadata } from "next";
import Link from "next/link";
import { Check, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonLd } from "@/components/json-ld";

export const metadata: Metadata = {
  title: "Pricing",
  description:
    "Transparent pricing for Astral drones and services. Hardware, software, and enterprise solutions.",
};

const hardwareProducts = [
  {
    name: "M1-A",
    price: "$9,000",
    description: "Autonomous quadcopter for aerial missions in any environment.",
    features: [
      "Jetson Orin Nano (67 TOPS)",
      "30 min flight time",
      "GPS & comm-denied capable",
      "Intel RealSense D435",
      "Indoor & outdoor operation",
      "IP55 weather rating",
      "NDAA compliant compute",
      "Open source software",
    ],
    cta: "Learn More",
    href: "/products/m1a",
    badge: "Quadcopter",
    highlighted: true,
  },
  {
    name: "M1-G",
    price: "$4,000",
    description: "Autonomous ground rover for persistent missions and patrol.",
    features: [
      "Jetson Orin Nano (67 TOPS)",
      "4 hour runtime",
      "GPS & comm-denied capable",
      "Intel RealSense D435",
      "All-terrain 4WD",
      "IP65 weather rating",
      "NDAA compliant compute",
      "Open source software",
    ],
    cta: "Learn More",
    href: "/products/m1g",
    badge: "Rover",
  },
];

const softwareTiers = [
  {
    name: "Free",
    price: "$0",
    period: "forever",
    description: "For individual developers and hobbyists.",
    features: [
      "1 drone",
      "Basic mission planning",
      "Community support",
      "Public SDK access",
      "Local simulator",
    ],
    cta: "Get Started",
    href: "/docs",
  },
  {
    name: "Pro",
    price: "$49",
    period: "/month",
    description: "For professional operators and small teams.",
    features: [
      "Up to 10 drones",
      "Advanced mission planning",
      "Real-time telemetry",
      "Cloud simulator",
      "Email support",
      "API access",
      "Analytics dashboard",
    ],
    cta: "Request Access",
    href: "/enterprise",
    highlighted: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    period: "",
    description: "For organizations with large fleet operations.",
    features: [
      "Unlimited drones",
      "Fleet management",
      "Custom integrations",
      "Dedicated support",
      "SLA guarantees",
      "On-premise option",
      "Training included",
      "Custom AI models",
    ],
    cta: "Contact Sales",
    href: "/enterprise",
  },
];

const faqs = [
  {
    q: "Do I need a software subscription to use the hardware?",
    a: "No! The drones work standalone with the free tier. Software subscriptions add cloud features, fleet management, and advanced mission planning.",
  },
  {
    q: "What's included with the hardware purchase?",
    a: "Each drone includes batteries, controller, charger, carrying case, spare propellers, and quick start guide. See individual product pages for full details.",
  },
  {
    q: "Do you offer volume discounts?",
    a: "Yes — contact our enterprise team for custom pricing on orders of 5+ drones.",
  },
  {
    q: "What's your return policy?",
    a: "30-day no-questions-asked returns on hardware. Software subscriptions can be cancelled anytime.",
  },
  {
    q: "Is the hardware NDAA compliant?",
    a: "Yes, all compute and sensor components (Jetson Orin Nano, Intel RealSense) are NDAA compliant, making the platform suitable for government and defense applications.",
  },
];

export default function PricingPage() {
  const faqJsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((faq) => ({
      "@type": "Question",
      name: faq.q,
      acceptedAnswer: { "@type": "Answer", text: faq.a },
    })),
  };

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={faqJsonLd} />
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-20 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl font-bold mb-6">
                Simple, Transparent Pricing
              </h1>
              <p className="text-lg text-muted-foreground">
                Choose the hardware and software that fits your mission. No hidden fees.
              </p>
            </div>
          </div>
        </section>

        {/* Hardware Pricing */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4">
            <div className="max-w-5xl mx-auto">
              <h2 className="text-2xl font-bold mb-8 text-center">Hardware</h2>
              <div className="grid md:grid-cols-2 gap-8">
                {hardwareProducts.map((product) => (
                  <Card
                    key={product.name}
                    className={`bg-background border-border ${
                      product.highlighted ? "ring-2 ring-primary" : ""
                    }`}
                  >
                    <CardHeader>
                      <div className="flex items-center justify-between mb-2">
                        <Badge
                          variant="secondary"
                          className="bg-amber-500/10 text-amber-500"
                        >
                          {product.badge}
                        </Badge>
                      </div>
                      <h3 className="text-2xl font-bold">{product.name}</h3>
                      <div className="text-3xl font-bold">{product.price}</div>
                      <p className="text-sm text-muted-foreground">
                        {product.description}
                      </p>
                    </CardHeader>
                    <CardContent>
                      <ul className="space-y-2">
                        {product.features.map((feature) => (
                          <li
                            key={feature}
                            className="flex items-center text-sm"
                          >
                            <Check className="h-4 w-4 text-amber-500 mr-2 shrink-0" />
                            {feature}
                          </li>
                        ))}
                      </ul>
                    </CardContent>
                    <CardFooter>
                      <Link href={product.href} className="w-full">
                        <Button
                          className={`w-full ${product.highlighted ? "glow" : ""}`}
                          variant={product.highlighted ? "default" : "outline"}
                        >
                          {product.cta}
                        </Button>
                      </Link>
                    </CardFooter>
                  </Card>
                ))}
              </div>
              <p className="text-center text-sm text-muted-foreground mt-6">
                Volume and government pricing available. <Link href="/enterprise" className="text-amber-500 hover:underline">Contact us.</Link>
              </p>
            </div>
          </div>
        </section>

        {/* Software Pricing */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4">
            <div className="max-w-6xl mx-auto">
              <h2 className="text-2xl font-bold mb-8 text-center">
                Software & Services
              </h2>
              <div className="grid md:grid-cols-3 gap-8">
                {softwareTiers.map((tier) => (
                  <Card
                    key={tier.name}
                    className={`bg-card border-border ${
                      tier.highlighted ? "ring-2 ring-primary" : ""
                    }`}
                  >
                    <CardHeader>
                      {tier.highlighted && (
                        <Badge className="w-fit bg-amber-500/10 text-amber-500 mb-2">
                          Most Popular
                        </Badge>
                      )}
                      <h3 className="text-xl font-bold">{tier.name}</h3>
                      <div>
                        <span className="text-3xl font-bold">{tier.price}</span>
                        <span className="text-muted-foreground">
                          {tier.period}
                        </span>
                      </div>
                      <p className="text-sm text-muted-foreground">
                        {tier.description}
                      </p>
                    </CardHeader>
                    <CardContent>
                      <ul className="space-y-2">
                        {tier.features.map((feature) => (
                          <li
                            key={feature}
                            className="flex items-center text-sm"
                          >
                            <Check className="h-4 w-4 text-amber-500 mr-2 shrink-0" />
                            {feature}
                          </li>
                        ))}
                      </ul>
                    </CardContent>
                    <CardFooter>
                      <Link href={tier.href} className="w-full">
                        <Button
                          className={`w-full ${tier.highlighted ? "glow" : ""}`}
                          variant={tier.highlighted ? "default" : "outline"}
                        >
                          {tier.cta}
                        </Button>
                      </Link>
                    </CardFooter>
                  </Card>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto">
              <h2 className="text-2xl font-bold mb-8 text-center">
                Frequently Asked Questions
              </h2>
              <div className="space-y-6">
                {faqs.map((faq) => (
                  <div key={faq.q} className="space-y-2">
                    <h3 className="font-semibold">{faq.q}</h3>
                    <p className="text-sm text-muted-foreground">{faq.a}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4 text-center">
            <h2 className="text-2xl font-bold mb-4">Ready to get started?</h2>
            <p className="text-muted-foreground mb-6">
              Questions? Our team is here to help.
            </p>
            <div className="flex flex-wrap justify-center gap-4">
              <Link href="/docs">
                <Button size="lg">
                  Get Started
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
              <Link href="/enterprise">
                <Button size="lg" variant="outline">
                  Contact Sales
                </Button>
              </Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
