import type { Metadata } from "next";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { SITE, SITE_DEFAULT_DESCRIPTION } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const DESC =
  "Astral builds the autonomy stack for uncrewed systems — open SDK, closed-loop simulation, operator apps, and datasets. NDAA-compliant M1-A quadcopter and M1-G rover available.";

export const metadata: Metadata = {
  title: "Autonomous Drone Platform & SDK",
  description: DESC,
  ...socialMeta("/", "Autonomous Drone Platform & SDK | Astral", DESC),
};
import {
  HeroSection,
  VideoDemoSection,
  ProductsSection,
  PlazaSimSection,
  ResearchTeaserSection,
  BenchmarkTeaserSection,
  SolutionsSection,
  DeveloperSection,
  AppShowcaseSection,
  CTASection,
} from "@/components/sections";

const organizationJsonLd = {
  "@context": "https://schema.org",
  "@type": "Organization",
  name: "Astral",
  url: SITE.origin,
  logo: `${SITE.origin}/logo-black.png`,
  description: SITE_DEFAULT_DESCRIPTION,
  sameAs: [SITE.linkedin, SITE.githubOrg, SITE.youtube],
};

const websiteJsonLd = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: "Astral",
  url: SITE.origin,
};

export default function HomePage() {
  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={[organizationJsonLd, websiteJsonLd]} />
      <Header />
      <main className="flex-1">
        <HeroSection />
        <VideoDemoSection />
        <PlazaSimSection />
        <ProductsSection />
        <BenchmarkTeaserSection />
        <SolutionsSection />
        <DeveloperSection />
        <AppShowcaseSection />
        <ResearchTeaserSection />
        <CTASection />
      </main>
      <Footer />
    </div>
  );
}
