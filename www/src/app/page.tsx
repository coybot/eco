import type { Metadata } from "next";
import { JsonLd } from "@/components/json-ld";
import { SITE, SITE_DEFAULT_DESCRIPTION } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";
import {
  HeroSection,
  PlazaSimSection,
  EvidenceSection,
  StackCardsSection,
  AudienceSection,
  BlogPreviewSection,
  CTASection,
} from "@/components/sections";

export const metadata: Metadata = {
  title: "Open-Source Drone Autonomy Stack",
  description: SITE_DEFAULT_DESCRIPTION,
  ...socialMeta("/", "Presidio Autonomy — Open-Source Drone Autonomy Stack", SITE_DEFAULT_DESCRIPTION),
};

const organizationJsonLd = {
  "@context": "https://schema.org",
  "@type": "Organization",
  name: "Presidio Autonomy",
  url: SITE.origin,
  description: SITE_DEFAULT_DESCRIPTION,
  sameAs: [SITE.githubOrg],
};

const websiteJsonLd = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: "Presidio Autonomy",
  url: SITE.origin,
};

export default function HomePage() {
  return (
    <>
      <JsonLd data={organizationJsonLd} />
      <JsonLd data={websiteJsonLd} />
      <HeroSection />
      <PlazaSimSection />
      <EvidenceSection />
      <StackCardsSection />
      <AudienceSection />
      <BlogPreviewSection />
      <CTASection />
    </>
  );
}
