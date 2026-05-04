import { Metadata } from "next";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "About",
  description: "About Astral and our autonomous systems mission.",
};

export default function AboutPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <h1 className="text-4xl font-bold mb-4">About Astral</h1>
            <p className="text-muted-foreground">
              Astral builds autonomous drone platforms and developer tooling for
              operators in defense, infrastructure, agriculture, and public
              safety environments.
            </p>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
