import { Metadata } from "next";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "Careers",
  description: "Join Astral and help shape autonomous systems.",
};

export default function CareersPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl font-bold mb-4">Careers</h1>
            <p className="text-muted-foreground">
              We are hiring mission-driven engineers, operators, and product
              builders. Reach out to discuss open roles.
            </p>
            <p className="mt-4">
              <a
                href="mailto:hello@astral.us"
                className="text-amber-500 hover:underline"
              >
                hello@astral.us
              </a>
            </p>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
