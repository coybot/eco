"use client";

import Link from "next/link";
import { useState } from "react";
import { Menu, Github } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { SITE } from "@/lib/site";

// Flat, crawlable nav. Every entry is a real <a href> to an index page that
// exists and is rendered in the server HTML — no JS-only dropdowns. Sub-pages
// (Quadcopter/Rover/Fixed-Wing/Phrover/Compare, SDK/API/Simulation, etc.) are reachable from these index
// pages, the footer, and the sitemap.
const navLinks = [
  { title: "Products", href: "/products" },
  { title: "Benchmark", href: "/benchmark" },
  { title: "Docs", href: "/docs" },
  { title: "Research", href: "/research" },
  { title: "Pricing", href: "/pricing" },
  { title: "Enterprise", href: "/enterprise" },
];

export function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border/40 bg-background/80 backdrop-blur-xl" style={{ paddingLeft: 'env(safe-area-inset-left)', paddingRight: 'env(safe-area-inset-right)' }}>
      <div className="container mx-auto flex h-16 items-center justify-between px-4">
        {/* Logo */}
        <Link href="/" className="flex items-center">
          <img
            src="/logo-white.png"
            alt="Presidio"
            className="h-6 w-auto"
          />
        </Link>

        {/* Desktop Navigation — plain anchors */}
        <nav className="hidden lg:flex items-center gap-1">
          {navLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="inline-flex h-10 items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors hover:bg-accent/10 hover:text-amber-500 focus:bg-accent/10 focus:outline-none"
            >
              {link.title}
            </Link>
          ))}
        </nav>

        {/* Desktop Actions */}
        <div className="hidden lg:flex items-center space-x-2">
          <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">
            <Button variant="ghost" size="icon" aria-label="GitHub">
              <Github className="h-5 w-5" />
            </Button>
          </a>
          <Link href="/docs">
            <Button>Get Started</Button>
          </Link>
        </div>

        {/* Mobile Menu */}
        <div className="flex lg:hidden items-center space-x-2">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon">
                <Menu className="h-5 w-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-[300px] sm:w-[400px]">
              <nav className="flex flex-col space-y-4 mt-8">
                {navLinks.map((link) => (
                  <Link
                    key={link.href}
                    href={link.href}
                    className="text-lg font-medium hover:text-amber-500 transition-colors"
                    onClick={() => setMobileOpen(false)}
                  >
                    {link.title}
                  </Link>
                ))}
                <div className="pt-4 border-t border-border space-y-2">
                  <Link href="/docs" onClick={() => setMobileOpen(false)}>
                    <Button className="w-full">Get Started</Button>
                  </Link>
                  <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer" onClick={() => setMobileOpen(false)}>
                    <Button variant="outline" className="w-full">
                      <Github className="mr-2 h-4 w-4" />
                      GitHub
                    </Button>
                  </a>
                </div>
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}
