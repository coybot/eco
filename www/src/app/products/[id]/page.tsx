import { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { ArrowLeft, Check, Package, Shield, Truck } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { JsonLd } from "@/components/json-ld";
import { SITE } from "@/lib/site";
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

  const productUrl = `${SITE.origin}/products/${product.id}`;
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Product",
      name: product.name,
      description: product.description,
      image: `${SITE.origin}${product.image}`,
      url: productUrl,
      brand: { "@type": "Brand", name: "Astral" },
      offers: {
        "@type": "Offer",
        price: product.price,
        priceCurrency: "USD",
        availability: "https://schema.org/InStock",
        url: productUrl,
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Products", item: `${SITE.origin}/products` },
        { "@type": "ListItem", position: 2, name: product.name, item: productUrl },
      ],
    },
  ];

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
      {jsonLd.map((item, i) => <JsonLd key={i} data={item} />)}
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
                <div className="relative aspect-square bg-background rounded-lg overflow-hidden border border-border">
                  <Image
                    src={product.image}
                    alt={product.name}
                    fill
                    sizes="(max-width: 1024px) 100vw, 50vw"
                    priority
                    className="object-contain"
                  />
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
                    <span>30-Day Returns</span>
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

        {/* Benchmark callout */}
        <section className="py-12 bg-background border-y border-border">
          <div className="container mx-auto px-4 max-w-3xl text-center">
            <p className="text-sm font-medium text-amber-500 uppercase tracking-wider mb-2">
              Open Benchmark
            </p>
            <h2 className="text-2xl font-bold mb-3">
              0% collision rate. Verified in closed-loop simulation.
            </h2>
            <p className="text-muted-foreground mb-6">
              We benchmarked 25 vision-language models across 10,200 flight trials. The modular stack is the only architecture that clears the bar. Methodology and results are public.
            </p>
            <Link href="/benchmark">
              <Button variant="outline">View the benchmark →</Button>
            </Link>
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
