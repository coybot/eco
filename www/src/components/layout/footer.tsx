import Link from "next/link";
import { Separator } from "@/components/ui/separator";

const footerLinks = {
  Products: [
    { name: "M1-A Quadcopter", href: "/products/m1a" },
    { name: "M1-G Ground Rover", href: "/products/m1g" },
    { name: "Compare", href: "/compare" },
    { name: "Pricing", href: "/pricing" },
  ],
  Developers: [
    { name: "Documentation", href: "/docs" },
    { name: "Run in Simulation", href: "/docs/simulation" },
    { name: "Research", href: "/research" },
    { name: "Blog", href: "/blog" },
  ],
  Legal: [
    { name: "Privacy Policy", href: "/privacy" },
    { name: "Terms of Service", href: "/terms" },
  ],
};

export function Footer() {
  return (
    <footer className="border-t border-border bg-card">
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
                    <Link
                      href={link.href}
                      className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {link.name}
                    </Link>
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
