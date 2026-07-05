import Link from "next/link";
import { Separator } from "@/components/ui/separator";
import { SITE } from "@/lib/site";

const footerLinks: Record<string, { name: string; href: string; external?: boolean }[]> = {
  Products: [
    { name: "Quadcopter", href: "/products/quadcopter" },
    { name: "Rover", href: "/products/rover" },
    { name: "Fixed-Wing", href: "/products/fixed-wing" },
    { name: "Phrover", href: "/products/phrover" },
    { name: "Compare", href: "/compare" },
    { name: "Pricing", href: "/pricing" },
  ],
  Developers: [
    { name: "Documentation", href: "/docs" },
    { name: "Run in Simulation", href: "/docs/simulation" },
    { name: "Yonder Dataset", href: "/datasets/yonder" },
    { name: "Research", href: "/research" },
    { name: "Blog", href: "/blog" },
    { name: "GitHub", href: SITE.githubOrg, external: true },
  ],
  Legal: [
    { name: "Privacy Policy", href: "/privacy" },
    { name: "Terms of Service", href: "/terms" },
  ],
};

export function Footer() {
  return (
    <footer className="border-t border-border bg-card" style={{ paddingLeft: 'env(safe-area-inset-left)', paddingRight: 'env(safe-area-inset-right)', paddingBottom: 'env(safe-area-inset-bottom)' }}>
      <div className="container mx-auto px-4 py-12">
        {/* Main Footer Content */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-8">
          {/* Brand Column */}
          <div className="col-span-2 md:col-span-3 lg:col-span-1">
            <Link href="/" className="inline-block">
              <img 
                src="/logo-white.png" 
                alt="Astral" 
                className="h-6 w-auto"
              />
            </Link>
            <p className="mt-4 text-sm text-muted-foreground">
              The Autonomous Drone Platform. Built on Open Source.
            </p>
          </div>

          {/* Link Columns */}
          {Object.entries(footerLinks).map(([category, links]) => (
            <div key={category}>
              <h3 className="font-semibold text-foreground mb-4">{category}</h3>
              <ul className="space-y-3">
                {links.map((link) => (
                  <li key={link.name}>
                    {link.external ? (
                      <a
                        href={link.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {link.name}
                      </a>
                    ) : (
                      <Link
                        href={link.href}
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {link.name}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <Separator className="my-8" />

        {/* Bottom Bar */}
        <div className="flex flex-col md:flex-row justify-between items-center space-y-4 md:space-y-0">
          <p className="text-sm text-muted-foreground">
            © {new Date().getFullYear()} Astral. All rights reserved.
          </p>
          <div className="flex items-center space-x-4">
            <span className="text-sm text-muted-foreground flex items-center">
              <span className="inline-block w-2 h-2 bg-success rounded-full mr-2"></span>
              All systems operational
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
}
