"use client";

import { useEffect } from "react";
import Link from "next/link";
import { CheckCircle, Package, Mail, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { useCart } from "@/lib/cart";

export default function CheckoutSuccessPage() {
  const { clearCart } = useCart();

  // Clear cart on success
  useEffect(() => {
    clearCart();
  }, [clearCart]);

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1 py-20">
        <div className="container mx-auto px-4">
          <div className="max-w-lg mx-auto text-center">
            {/* Success Icon */}
            <div className="mb-8">
              <div className="w-20 h-20 mx-auto bg-success/10 rounded-full flex items-center justify-center">
                <CheckCircle className="h-10 w-10 text-success" />
              </div>
            </div>

            {/* Success Message */}
            <h1 className="text-3xl font-bold mb-4">Order Confirmed!</h1>
            <p className="text-lg text-muted-foreground mb-8">
              Thank you for your purchase. Your order has been received and is
              being processed.
            </p>

            {/* Order Details */}
            <div className="bg-card border border-border rounded-lg p-6 mb-8 text-left">
              <h2 className="font-semibold mb-4">What&apos;s Next?</h2>
              <div className="space-y-4">
                <div className="flex items-start space-x-3">
                  <Mail className="h-5 w-5 text-amber-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="font-medium">Confirmation Email</p>
                    <p className="text-sm text-muted-foreground">
                      You&apos;ll receive an order confirmation email shortly with
                      your order details and receipt.
                    </p>
                  </div>
                </div>
                <div className="flex items-start space-x-3">
                  <Package className="h-5 w-5 text-amber-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="font-medium">Shipping Updates</p>
                    <p className="text-sm text-muted-foreground">
                      We&apos;ll send you shipping notifications with tracking
                      information once your order ships (typically within 3-5
                      business days).
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <Link href="/products">
                <Button variant="outline">Continue Shopping</Button>
              </Link>
              <Link href="/docs/quickstart">
                <Button className="glow">
                  Get Started
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            </div>

            <p className="text-sm text-muted-foreground mt-8">
              Questions about your order?{" "}
              <Link href="/contact" className="text-amber-500 hover:underline">
                Contact support
              </Link>
            </p>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
