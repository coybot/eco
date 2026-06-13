import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  robots: { index: true, follow: true },
};

export default function ProductsLayout({
  children,
}: {
  children: ReactNode;
}) {
  return children;
}
