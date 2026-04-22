import { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Check, Package, Shield, Truck } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { products, getProduct, formatPrice } from "@/lib/products";

interface ProductPageProps {
  params: Promise<{ id: string }>;
}

export async function generateStaticParams() {
  return products.map((product) => ({
    id: product.id,
  }));
}

export async function generateMetadata({ params }: ProductPageProps): Promise<Metadata> {
  const { id } = await params;
  const product = getProduct(id);

  if (!product) {
    return {
      title: "Product Not Found",
    };
  }

  return {
    title: product.name,
    description: product.description,
    openGraph: {
      title: `${product.name} | Astral`,
      description: product.description,
      images: [product.image],
    },
  };
}

export default async function ProductPage({ params }: ProductPageProps) {
  const { id } = await params;
  const product = getProduct(id);

  if (!product) {
    notFound();
  }

  // Group specs by category
  const specsByCategory = product.specs.reduce(
    (acc, spec) => {
      if (!acc[spec.category]) {
        acc[spec.category] = [];
      }
      acc[spec.category].push(spec);
      return acc;
    },
    {} as Record<string, typeof product.specs>
  );

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        {/* Breadcrumb */}
        <div className="bg-card border-b border-border">
          <div className="container mx-auto px-4 py-4">
            <Link
              href="/products"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Products
            </Link>
          </div>
        </div>

        {/* Product Hero */}
        <section className="py-12 bg-card">
          <div className="container mx-auto px-4">
            <div className="grid lg:grid-cols-2 gap-12 max-w-6xl mx-auto">
              {/* Product Image */}
              <div className="space-y-4">
                <div className="aspect-square bg-background rounded-lg flex items-center justify-center border border-border">
                  <div className="text-6xl font-bold text-muted-foreground/20">
                    {product.name}
                  </div>
                </div>
                {/* Thumbnail strip placeholder */}
                <div className="flex gap-2">
                  {[1, 2, 3, 4].map((i) => (
                    <div
                      key={i}
                      className="w-20 h-20 bg-background rounded-md border border-border flex items-center justify-center cursor-pointer hover:border-amber-500/50 transition-colors"
                    >
                      <span className="text-xs text-muted-foreground">{i}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Product Info */}
              <div className="space-y-6">
                <div>
                  <Badge
                    variant="secondary"
                    className="bg-amber-500/10 text-amber-500 mb-4"
                  >
                    {product.badge}
                  </Badge>
                  <h1 className="text-4xl font-bold mb-2">{product.name}</h1>
                  <p className="text-lg text-muted-foreground">
                    {product.tagline}
                  </p>
                </div>

                <div className="text-4xl font-bold">
                  {formatPrice(product.price)}
                </div>

                <p className="text-muted-foreground">{product.description}</p>

                {/* Key Features */}
                <div className="space-y-2">
                  {product.features.slice(0, 4).map((feature) => (
                    <div
                      key={feature}
                      className="flex items-center text-sm"
                    >
                      <Check className="h-4 w-4 text-amber-500 mr-2 shrink-0" />
                      {feature}
                    </div>
                  ))}
                </div>

                {/* Actions */}
                <div className="flex gap-4">
                  <Link href="/enterprise" className="flex-1">
                    <Button size="lg" className="w-full">
                      Request Info
                    </Button>
                  </Link>
                </div>

                {/* Trust indicators */}
                <div className="grid grid-cols-3 gap-4 pt-4">
                  <div className="flex items-center space-x-2 text-sm text-muted-foreground">
                    <Shield className="h-4 w-4" />
                    <span>NDAA Compliant</span>
                  </div>
                  <div className="flex items-center space-x-2 text-sm text-muted-foreground">
                    <Package className="h-4 w-4" />
                    <span>Open Source</span>
                  </div>
                  <div className="flex items-center space-x-2 text-sm text-muted-foreground">
                    <Truck className="h-4 w-4" />
                    <span>Coming Soon</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Tabs Section */}
        <section className="py-12 bg-background">
          <div className="container mx-auto px-4">
            <div className="max-w-6xl mx-auto">
              <Tabs defaultValue="specs" className="w-full">
                <TabsList className="grid w-full grid-cols-3 max-w-md">
                  <TabsTrigger value="specs">Specifications</TabsTrigger>
                  <TabsTrigger value="features">Features</TabsTrigger>
                  <TabsTrigger value="included">What&apos;s Included</TabsTrigger>
                </TabsList>

                <TabsContent value="specs" className="mt-8">
                  <div className="grid md:grid-cols-2 gap-8">
                    {Object.entries(specsByCategory).map(([category, specs]) => (
                      <div key={category}>
                        <h3 className="text-lg font-semibold mb-4">{category}</h3>
                        <div className="space-y-3">
                          {specs.map((spec) => (
                            <div
                              key={spec.label}
                              className="flex justify-between py-2 border-b border-border"
                            >
                              <span className="text-muted-foreground">
                                {spec.label}
                              </span>
                              <span className="font-medium">{spec.value}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </TabsContent>

                <TabsContent value="features" className="mt-8">
                  <div className="grid sm:grid-cols-2 gap-4 max-w-3xl">
                    {product.features.map((feature) => (
                      <div
                        key={feature}
                        className="flex items-start space-x-3 p-4 bg-card rounded-lg border border-border"
                      >
                        <Check className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
                        <span>{feature}</span>
                      </div>
                    ))}
                  </div>
                </TabsContent>

                <TabsContent value="included" className="mt-8">
                  <div className="max-w-2xl">
                    <h3 className="text-lg font-semibold mb-4">
                      In the Box
                    </h3>
                    <ul className="space-y-3">
                      {product.includes.map((item) => (
                        <li
                          key={item}
                          className="flex items-center space-x-3 py-2 border-b border-border"
                        >
                          <Package className="h-4 w-4 text-amber-500" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </TabsContent>
              </Tabs>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4 text-center">
            <h2 className="text-2xl font-bold mb-4">Need help choosing?</h2>
            <p className="text-muted-foreground mb-6">
              Compare our drones side-by-side or talk to our team.
            </p>
            <div className="flex flex-wrap justify-center gap-4">
              <Link href="/compare">
                <Button variant="outline">Compare Drones</Button>
              </Link>
              <Link href="/enterprise">
                <Button variant="outline">Contact Sales</Button>
              </Link>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
