import { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "Accessories",
  description: "Recommended accessories for Astral autonomous platforms.",
};

export default function AccessoriesPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <Link
              href="/products"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-6"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Products
            </Link>
            <h1 className="text-4xl font-bold mb-4">Accessories</h1>
            <p className="text-muted-foreground">
              Accessory bundles and field kits are available by request. Contact
              our team for current options compatible with your mission profile.
            </p>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
