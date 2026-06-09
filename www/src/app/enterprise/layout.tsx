import type { Metadata } from "next";
import type { ReactNode } from "react";
import { socialMeta } from "@/lib/social-metadata";

const ENTERPRISE_DESCRIPTION =
  "Talk to Astral about autonomy software, simulation-backed evaluation, fleet programs, BYO-robot integration, and custom uncrewed vehicles.";

export const metadata: Metadata = {
  title: "Enterprise",
  description: ENTERPRISE_DESCRIPTION,
  ...socialMeta(
    "/enterprise",
    "Enterprise | Astral",
    ENTERPRISE_DESCRIPTION
  ),
};

export default function EnterpriseLayout({
  children,
}: {
  children: ReactNode;
}) {
  return children;
}
