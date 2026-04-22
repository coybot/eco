import Stripe from "stripe";

// Initialize Stripe only if secret key is available
export const stripe = process.env.STRIPE_SECRET_KEY
  ? new Stripe(process.env.STRIPE_SECRET_KEY, {
      apiVersion: "2025-12-15.clover",
      typescript: true,
    })
  : null;

// Map product IDs to Stripe price IDs (configure these in Stripe Dashboard)
export const STRIPE_PRICE_IDS: Record<string, string> = {
  mothership: process.env.STRIPE_MOTHERSHIP_PRICE_ID || "price_mothership",
  scout: process.env.STRIPE_SCOUT_PRICE_ID || "price_scout",
};

export function getStripePriceId(productId: string): string | undefined {
  return STRIPE_PRICE_IDS[productId];
}
