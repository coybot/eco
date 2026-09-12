import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const ABOUT_DESCRIPTION =
  "Coybot builds autonomy for uncrewed aircraft, rovers, and other robots: an open SDK, high-fidelity simulation, operator apps, and public datasets—plus custom vehicles when a mission needs purpose-built hardware.";

export const metadata: Metadata = {
  title: "About",
  description: ABOUT_DESCRIPTION,
  ...socialMeta("/about", "About | Coybot", ABOUT_DESCRIPTION),
};

const pillars: Array<{ title: string; body: string }> = [
  {
    title: "Modular autonomy",
    body: "We separate semantic understanding from metric geometry so perception, planning, and control can each be evaluated and improved independently—the foundation of a deployable stack that generalizes across aircraft and ground robots.",
  },
  {
    title: "Closed-loop evaluation",
    body: "Every capability is measured in high-fidelity simulation before it reaches hardware. Honest, reproducible benchmarks—not demos—decide what ships.",
  },
  {
    title: "Open artifacts",
    body: "Our SDK, documentation, and large-scale datasets like Yonder are public, so teams can build on our work, reproduce our results, and integrate their own platforms and models.",
  },
];

const proof: Array<{ label: string; href: string; external?: boolean }> = [
  { label: "Open SDK on GitHub", href: SITE.githubOrg, external: true },
  { label: "Yonder dataset", href: "/datasets/yonder" },
  { label: "Research", href: "/research" },
  { label: "Documentation", href: SITE.docs, external: true },
];

export default function AboutPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "AboutPage",
    name: "About Coybot",
    description: ABOUT_DESCRIPTION,
    url: `${SITE.origin}/about`,
    isPartOf: { "@type": "WebSite", name: "Coybot", url: SITE.origin },
    mainEntity: {
      "@type": "Organization",
      name: "Coybot",
      url: SITE.origin,
      logo: `${SITE.origin}/logo-black.png`,
      sameAs: [SITE.linkedin, SITE.githubOrg, SITE.youtube],
    },
  };

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={jsonLd} />
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl sm:text-5xl font-bold mb-6">About Coybot</h1>
            <p className="text-lg text-muted-foreground mb-8">
              Coybot builds the autonomy stack for uncrewed systems. We treat
              autonomous robots as a systems problem—perception, geometry,
              planning, safety, simulation fidelity, and honest evaluation—and
              ship it as an open SDK, high-fidelity simulation, operator apps,
              and public datasets. Bring your own platform and models, or work
              with us on custom quadcopters, fixed-wing aircraft, rovers, and
              beyond.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button asChild>
                <Link href="/products">Explore platforms</Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/enterprise">Talk to us</Link>
              </Button>
              <Button variant="outline" asChild>
                <a href={SITE.githubOrg}>GitHub</a>
              </Button>
            </div>
          </div>
        </section>

        <section className="py-12 bg-background border-t border-border">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-8">How we work</h2>
            <div className="grid gap-6 sm:grid-cols-3">
              {pillars.map((p) => (
                <Card key={p.title} className="bg-card border-border">
                  <CardHeader>
                    <CardTitle className="text-lg leading-snug">{p.title}</CardTitle>
                  </CardHeader>
                  <CardContent className="text-muted-foreground">
                    <p>{p.body}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        <section className="py-12 bg-card border-t border-border">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-4">Built in the open</h2>
            <p className="text-muted-foreground mb-6">
              Our software, research, and datasets are public. The fastest way to
              understand Coybot is to use them.
            </p>
            <div className="flex flex-wrap gap-3">
              {proof.map((l) =>
                l.external ? (
                  <Button key={l.href} variant="secondary" size="sm" asChild>
                    <a href={l.href} target="_blank" rel="noopener noreferrer">
                      {l.label}
                    </a>
                  </Button>
                ) : (
                  <Button key={l.href} variant="secondary" size="sm" asChild>
                    <Link href={l.href}>{l.label}</Link>
                  </Button>
                )
              )}
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
