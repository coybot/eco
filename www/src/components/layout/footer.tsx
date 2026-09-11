import Link from "next/link";
import { Separator } from "@/components/ui/separator";
import { SITE } from "@/lib/site";

const footerLinks: Record<string, { name: string; href: string; external?: boolean }[]> = {
  Learn: [
    { name: "What is autonomy?", href: "/autonomy" },
    { name: "Get Started", href: "/get-started" },
    { name: "Blog", href: "/blog" },
    { name: "About", href: "/about" },
  ],
  "Open Source": [
    { name: "presidio-sdk", href: SITE.presidioSdk, external: true },
    { name: "eco (sim + benchmark)", href: SITE.eco, external: true },
    { name: "presidio-docs", href: SITE.presidioDocs, external: true },
  ],
  Contact: [
    { name: "hello@presidioautonomy.com", href: `mailto:${SITE.email}` },
    { name: "GitHub", href: SITE.githubOrg, external: true },
  ],
};

export function Footer() {
  return (
    <footer className="border-t border-border bg-card" style={{ paddingLeft: 'env(safe-area-inset-left)', paddingRight: 'env(safe-area-inset-right)', paddingBottom: 'env(safe-area-inset-bottom)' }}>
      <div className="container mx-auto px-4 py-12">
        {/* Main Footer Content */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
          {/* Brand Column */}
          <div className="col-span-2 md:col-span-1">
            <Link href="/" className="inline-block">
              <span className="text-lg font-bold tracking-tight text-foreground">
                PRESIDIO<span className="text-amber-500">AUTONOMY</span>
              </span>
            </Link>
            <p className="mt-4 text-sm text-muted-foreground">
              Open-source autonomy for teams that know aerospace, hardware, embedded, or AI —
              and are ready to learn the part in between.
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
            © {new Date().getFullYear()} Presidio Autonomy. Code is MIT/Apache-2.0 per repo — see
            each repo's LICENSE file.
          </p>
        </div>
      </div>
    </footer>
  );
}
