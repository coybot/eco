import { Metadata } from "next";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "Cookie Policy",
  description: "Astral cookie policy.",
};

export default function CookiesPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl font-bold mb-4">Cookie Policy</h1>
            <p className="text-muted-foreground">
              Astral uses essential cookies for functionality and service
              reliability. Additional analytics cookies may be used where
              enabled.
            </p>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
