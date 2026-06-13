import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  robots: { index: true, follow: true },
};

export default function CompareLayout({
  children,
}: {
  children: ReactNode;
}) {
  return children;
}
