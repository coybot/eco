"use client";

import Link from "next/link";
import Image from "next/image";
import { motion } from "framer-motion";
import { Cpu, Battery, Gauge, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const products = [
  {
    id: "m1a",
    name: "M1-A",
    tagline: "Autonomous Quadcopter",
    price: "$5,299",
    image: "/quadcopter.jpg",
    badge: "Quadcopter",
    specs: [
      { icon: Cpu, label: "Jetson Orin Nano 8GB", detail: "67 TOPS AI" },
      { icon: Battery, label: "30 min flight", detail: "Aerial platform" },
      { icon: Gauge, label: "GPS & Comm Denied", detail: "Full autonomy" },
      { icon: Shield, label: "IP55", detail: "Indoor & outdoor" },
    ],
    features: [
      "Intel RealSense D435 camera",
      "Visual-inertial navigation",
      "On-device AI reasoning",
      "NDAA compliant compute",
    ],
  },
  {
    id: "m1g",
    name: "M1-G",
    tagline: "Autonomous Ground Rover",
    price: "$7,499",
    image: "/rover.jpg",
    badge: "Rover",
    specs: [
      { icon: Cpu, label: "Jetson Orin Nano 8GB", detail: "67 TOPS AI" },
      { icon: Battery, label: "4 hr runtime", detail: "Ground platform" },
      { icon: Gauge, label: "GPS & Comm Denied", detail: "Full autonomy" },
      { icon: Shield, label: "IP65", detail: "All-weather" },
    ],
    features: [
      "Intel RealSense D435 camera",
      "Visual-inertial navigation",
      "On-device AI reasoning",
      "NDAA compliant compute",
    ],
  },
];

export function ProductsSection() {
  return (
    <section className="py-24 bg-background">
      <div className="container mx-auto px-4">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold">Our software will run on your drone, or you can buy our hardware</h2>
          <p className="mt-4 text-lg text-muted-foreground max-w-3xl mx-auto">
            Autonomous systems with on-device AI. GPS and comm-denied capable, indoor and outdoor.
          </p>
        </motion.div>

        {/* Products Grid */}
        <div className="grid md:grid-cols-2 gap-8 max-w-5xl mx-auto">
          {products.map((product, index) => (
            <motion.div
              key={product.id}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
            >
              <Card className="h-full bg-card border-border hover:border-amber-500/50 transition-colors group">
                <CardHeader className="pb-4">
                  <div className="flex items-center justify-between mb-4">
                    <Badge variant="secondary" className="bg-amber-500/10 text-amber-500">
                      {product.badge}
                    </Badge>
                    <span className="text-2xl font-bold">{product.price}</span>
                  </div>
                  
                  {/* Product Image */}
                  <div className="aspect-video bg-secondary/50 rounded-lg flex items-center justify-center overflow-hidden relative">
                    <Image
                      src={product.image}
                      alt={product.name}
                      fill
                      className="object-cover group-hover:scale-105 transition-transform"
                    />
                  </div>
                </CardHeader>

                <CardContent className="space-y-6">
                  <div>
                    <h3 className="text-2xl font-bold">{product.name}</h3>
                    <p className="text-muted-foreground">{product.tagline}</p>
                  </div>

                  {/* Specs Grid */}
                  <div className="grid grid-cols-2 gap-3">
                    {product.specs.map((spec) => (
                      <div
                        key={spec.label}
                        className="flex items-start space-x-2 p-2 rounded-md bg-secondary/30"
                      >
                        <spec.icon className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
                        <div>
                          <p className="text-sm font-medium leading-tight">{spec.label}</p>
                          <p className="text-xs text-muted-foreground">{spec.detail}</p>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Features List */}
                  <ul className="space-y-2">
                    {product.features.map((feature) => (
                      <li key={feature} className="flex items-center text-sm text-muted-foreground">
                        <span className="mr-2 h-1.5 w-1.5 rounded-full bg-amber-500" />
                        {feature}
                      </li>
                    ))}
                  </ul>
                </CardContent>

                <CardFooter className="pt-4">
                  <Link href={`/products/${product.id}`} className="w-full">
                    <Button variant="outline" className="w-full">
                      Learn More
                    </Button>
                  </Link>
                </CardFooter>
              </Card>
            </motion.div>
          ))}
        </div>

      </div>
    </section>
  );
}
