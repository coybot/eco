import type { Metadata } from "next";
import { Mail, MapPin, Phone } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { JsonLd } from "@/components/json-ld";
import { Card, CardContent } from "@/components/ui/card";
import { CONTACT, SITE } from "@/lib/site";
import { socialMeta } from "@/lib/social-metadata";

const CONTACT_DESCRIPTION =
  "Contact Coybot: email hello@coy.bot, call +1 408-908-8273, or visit us at 2150 N First St, San Jose, CA 95131.";

export const metadata: Metadata = {
  title: "Contact",
  description: CONTACT_DESCRIPTION,
  ...socialMeta("/contact", "Contact | Coybot", CONTACT_DESCRIPTION),
};

const { address: a } = CONTACT;

export default function ContactPage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "ContactPage",
    name: "Contact Coybot",
    description: CONTACT_DESCRIPTION,
    url: `${SITE.origin}/contact`,
    isPartOf: { "@type": "WebSite", name: "Coybot", url: SITE.origin },
    mainEntity: {
      "@type": "Organization",
      name: "Coybot",
      legalName: CONTACT.legalName,
      url: SITE.origin,
      logo: `${SITE.origin}/logo-black.png`,
      email: CONTACT.email,
      telephone: CONTACT.phoneE164,
      address: {
        "@type": "PostalAddress",
        streetAddress: a.street,
        addressLocality: a.city,
        addressRegion: a.region,
        postalCode: a.postalCode,
        addressCountry: a.country,
      },
    },
  };

  const items = [
    {
      icon: Mail,
      label: "Email",
      body: CONTACT.email,
      href: `mailto:${CONTACT.email}`,
    },
    {
      icon: MapPin,
      label: "Address",
      body: (
        <>
          {CONTACT.legalName}
          <br />
          {a.street}
          <br />
          {a.city}, {a.region} {a.postalCode}
        </>
      ),
      href: CONTACT.mapsUrl,
      external: true,
    },
    {
      icon: Phone,
      label: "Phone",
      body: CONTACT.phoneDisplay,
      href: `tel:${CONTACT.phoneE164}`,
    },
  ];

  return (
    <div className="min-h-screen flex flex-col">
      <JsonLd data={jsonLd} />
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl sm:text-5xl font-bold mb-6">Contact</h1>
            <p className="text-lg text-muted-foreground mb-10">
              Questions about the platform, a custom vehicle, or working with us?
              Get in touch.
            </p>
            <div className="grid gap-6 sm:grid-cols-3">
              {items.map(({ icon: Icon, label, body, href, external }) => (
                <Card key={label} className="bg-card border-border">
                  <CardContent className="pt-6">
                    <Icon className="h-5 w-5 text-amber-500 mb-3" aria-hidden />
                    <h2 className="font-semibold mb-2">{label}</h2>
                    <a
                      href={href}
                      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {body}
                    </a>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
