import type { Metadata } from "next";
import Link from "next/link";
import { Github, Mail } from "lucide-react";
import { JsonLd } from "@/components/json-ld";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const TITLE = "About Presidio Autonomy";
const DESCRIPTION =
  "Why Presidio Autonomy exists: the industry ships waypoint-followers labeled autonomous, and we think that's worth calling out — with open code and published closed-loop numbers, including the ones that didn't work.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  ...socialMeta("/about", TITLE, DESCRIPTION),
};

const pillars: Array<{ title: string; body: string }> = [
  {
    title: "Sim-first",
    body: "Every capability is measured in closed-loop simulation before it reaches hardware. Offline metrics can look great and still not move closed-loop performance at all — we've measured that directly, more than once.",
  },
  {
    title: "Modular by default",
    body: "Semantic understanding (VLM) and metric geometry (detector, depth, planner) are separate, independently-testable components — not one model asked to do everything end to end.",
  },
  {
    title: "Negative results published",
    body: "A 9.7× detection improvement that didn't move closed-loop navigation. Mock evaluations that ran 6.5× optimistic. An L5 result that dropped to L3 under a more realistic sensor model. We write these up too.",
  },
];

export default function AboutPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "AboutPage",
    name: TITLE,
    description: DESCRIPTION,
    url: `${SITE.origin}/about`,
    isPartOf: { "@type": "WebSite", name: "Presidio Autonomy", url: SITE.origin },
    mainEntity: {
      "@type": "Organization",
      name: "Presidio Autonomy",
      url: SITE.origin,
      sameAs: [SITE.githubOrg],
      contactPoint: {
        "@type": "ContactPoint",
        email: SITE.email,
        contactType: "general inquiries",
      },
    },
  };

  return (
    <>
      <JsonLd data={jsonLd} />
      <section className="py-16 bg-gradient-to-b from-background to-card">
        <div className="container mx-auto px-4 max-w-4xl">
          <h1 className="text-4xl sm:text-5xl font-bold mb-6">{TITLE}</h1>
          <p className="text-lg text-muted-foreground mb-8">
            We started this project on a simple observation: most products that call themselves
            &ldquo;autonomous drones&rdquo; are waypoint-followers with a nice app. That&rsquo;s
            fine as automation, but it isn&rsquo;t autonomy, and calling it that sets the wrong
            expectation for anyone who actually needs a system that keeps working when the plan
            breaks. We publish the closed-loop numbers — including the ones where our own
            approach didn&rsquo;t work — because that&rsquo;s the only way to know the
            difference.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button asChild>
              <Link href="/autonomy">Read the argument</Link>
            </Button>
            <Button variant="outline" asChild>
              <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
                <Github className="mr-2 h-4 w-4" />
                GitHub
              </a>
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
          <h2 className="text-2xl font-bold mb-4">Contact</h2>
          <p className="text-muted-foreground mb-6">
            No sales team, no ticketing system — just a mailbox and GitHub.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button variant="secondary" size="sm" asChild>
              <a href={`mailto:${SITE.email}`}>
                <Mail className="mr-2 h-4 w-4" />
                {SITE.email}
              </a>
            </Button>
            <Button variant="secondary" size="sm" asChild>
              <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
                <Github className="mr-2 h-4 w-4" />
                Issues & discussions
              </a>
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
