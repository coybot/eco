import { Metadata } from "next";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "Contact",
  description: "Contact the Astral team.",
};

export default function ContactPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl font-bold mb-4">Contact</h1>
            <p className="text-muted-foreground mb-2">
              For product, partnership, and general inquiries:
            </p>
            <a
              href="mailto:hello@astral.us"
              className="text-amber-500 hover:underline"
            >
              hello@astral.us
            </a>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
