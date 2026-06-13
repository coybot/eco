import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { SITE, SITE_DEFAULT_DESCRIPTION } from "@/lib/site";
import {
  HeroSection,
  ProductsSection,
  PlazaSimSection,
  ResearchTeaserSection,
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
        <PlazaSimSection />
        <ProductsSection />
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
