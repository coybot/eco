import { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { ArrowRight, Check } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { products, formatPrice } from "@/lib/products";

export const metadata: Metadata = {
  title: "Products",
  description:
    "Explore Astral's autonomous platforms. M1-A quadcopter and M1-G rover for missions in any environment.",
};

export default function ProductsPage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-20 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl font-bold mb-6">Our Drones</h1>
              <p className="text-lg text-muted-foreground">
                Agentic-enabled autonomous drones with modular, customizable
                hardware. Bring your own AI or use ours.
              </p>
            </div>
          </div>
        </section>

        {/* Products Grid */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4">
            <div className="grid md:grid-cols-2 gap-8 max-w-5xl mx-auto">
              {products.map((product) => (
                <Card
                  key={product.id}
                  className="h-full bg-background border-border hover:border-amber-500/50 transition-colors"
                >
                  <CardHeader className="pb-4">
                    <div className="flex items-center justify-between mb-4">
                      <Badge
                        variant="secondary"
                        className="bg-amber-500/10 text-amber-500"
                      >
                        {product.badge}
                      </Badge>
                      <span className="text-2xl font-bold">
                        {formatPrice(product.price)}
                      </span>
                    </div>

                    <div className="relative aspect-video bg-secondary/50 rounded-lg overflow-hidden">
                      <Image
                        src={product.image}
                        alt={product.name}
                        fill
                        sizes="(max-width: 768px) 100vw, 50vw"
                        className="object-cover"
                      />
                    </div>
                  </CardHeader>

                  <CardContent className="space-y-6">
                    <div>
                      <h2 className="text-2xl font-bold">{product.name}</h2>
                      <p className="text-muted-foreground">{product.tagline}</p>
                    </div>

                    <p className="text-sm text-muted-foreground line-clamp-3">
                      {product.description}
                    </p>

                    {/* Key Features */}
                    <ul className="space-y-2">
                      {product.features.slice(0, 5).map((feature) => (
                        <li
                          key={feature}
                          className="flex items-center text-sm text-muted-foreground"
                        >
                          <Check className="h-4 w-4 text-amber-500 mr-2 shrink-0" />
                          {feature}
                        </li>
                      ))}
                    </ul>
                  </CardContent>

                  <CardFooter className="pt-4">
                    <Link href={`/products/${product.id}`} className="w-full">
                      <Button variant="outline" className="w-full">
                        View Details
                      </Button>
                    </Link>
                  </CardFooter>
                </Card>
              ))}
            </div>

            {/* Compare Link */}
            <div className="text-center mt-12">
              <Link href="/compare">
                <Button
                  variant="ghost"
                  className="text-muted-foreground hover:text-foreground"
                >
                  Compare all specifications
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            </div>
          </div>
        </section>

        {/* Trust Section */}
        <section className="py-16 bg-background">
          <div className="container mx-auto px-4">
            <div className="max-w-4xl mx-auto">
              <div className="grid sm:grid-cols-3 gap-8 text-center">
                <div>
                  <div className="text-3xl font-bold text-amber-500 mb-2">NDAA</div>
                  <p className="text-sm text-muted-foreground">
                    Compliant compute & sensors for government and defense
                  </p>
                </div>
                <div>
                  <div className="text-3xl font-bold text-amber-500 mb-2">Denied</div>
                  <p className="text-sm text-muted-foreground">
                    Operates in GPS and comm-denied environments
                  </p>
                </div>
                <div>
                  <div className="text-3xl font-bold text-amber-500 mb-2">Open</div>
                  <p className="text-sm text-muted-foreground">
                    Open source software stack and SDK
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
