"use client";

import Link from "next/link";
import { useState } from "react";
import { Menu, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import {
  NavigationMenu,
  NavigationMenuContent,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
  NavigationMenuTrigger,
} from "@/components/ui/navigation-menu";
import { cn } from "@/lib/utils";

const products = [
  {
    title: "M1-A Quadcopter",
    href: "/products/m1a",
    description: "Autonomous quadcopter with NDAA-compliant compute. GPS & comm-denied capable.",
  },
  {
    title: "M1-G Ground Rover",
    href: "/products/m1g",
    description: "Autonomous ground rover for long-range patrol. Heavier payload, all-weather mobility.",
  },
  {
    title: "Compare",
    href: "/compare",
    description: "M1-A quadcopter vs M1-G rover, side-by-side.",
  },
];

const solutions = [
  {
    title: "Defense",
    href: "/solutions/defense",
    description: "NDAA-compliant autonomous systems for military applications.",
  },
  {
    title: "Agriculture",
    href: "/solutions/agriculture",
    description: "Precision farming and crop monitoring at scale.",
  },
  {
    title: "Infrastructure",
    href: "/solutions/infrastructure",
    description: "Asset inspection and monitoring for critical infrastructure.",
  },
  {
    title: "Public Safety",
    href: "/solutions/public-safety",
    description: "Search and rescue, emergency response, and surveillance.",
  },
];

const developers = [
  {
    title: "Documentation",
    href: "/docs",
    description: "Comprehensive guides and API reference.",
  },
  {
    title: "SDK & APIs",
    href: "/docs/sdk",
    description: "Build custom applications with our SDK.",
  },
  {
    title: "Run in Simulation",
    href: "/docs/simulation",
    description: "Try the autonomy stack in Isaac Sim before hardware.",
  },
];

export function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border/40 bg-background/80 backdrop-blur-xl">
      <div className="container mx-auto flex h-16 items-center justify-between px-4">
        {/* Logo */}
        <Link href="/" className="flex items-center">
          <img 
            src="/logo-white.png" 
            alt="Astral" 
            className="h-6 w-auto"
          />
        </Link>

        {/* Desktop Navigation */}
        <NavigationMenu className="hidden lg:flex">
          <NavigationMenuList>
            <NavigationMenuItem>
              <NavigationMenuTrigger className="bg-transparent">
                Products
              </NavigationMenuTrigger>
              <NavigationMenuContent>
                <ul className="grid w-[400px] gap-3 p-4 md:w-[500px] md:grid-cols-2">
                  {products.map((product) => (
                    <ListItem
                      key={product.title}
                      title={product.title}
                      href={product.href}
                    >
                      {product.description}
                    </ListItem>
                  ))}
                </ul>
              </NavigationMenuContent>
            </NavigationMenuItem>

            <NavigationMenuItem>
              <NavigationMenuTrigger className="bg-transparent">
                Solutions
              </NavigationMenuTrigger>
              <NavigationMenuContent>
                <ul className="grid w-[400px] gap-3 p-4 md:w-[500px] md:grid-cols-2">
                  {solutions.map((solution) => (
                    <ListItem
                      key={solution.title}
                      title={solution.title}
                      href={solution.href}
                    >
                      {solution.description}
                    </ListItem>
                  ))}
                </ul>
              </NavigationMenuContent>
            </NavigationMenuItem>

            <NavigationMenuItem>
              <NavigationMenuTrigger className="bg-transparent">
                Developers
              </NavigationMenuTrigger>
              <NavigationMenuContent>
                <ul className="grid w-[400px] gap-3 p-4 md:w-[500px] md:grid-cols-2">
                  {developers.map((item) => (
                    <ListItem
                      key={item.title}
                      title={item.title}
                      href={item.href}
                    >
                      {item.description}
                    </ListItem>
                  ))}
                </ul>
              </NavigationMenuContent>
            </NavigationMenuItem>

            <NavigationMenuItem>
              <NavigationMenuLink asChild>
                <Link 
                  href="/blog" 
                  className="group inline-flex h-10 w-max items-center justify-center rounded-md bg-transparent px-4 py-2 text-sm font-medium transition-colors hover:bg-accent/10 hover:text-accent-foreground focus:bg-accent/10 focus:text-accent-foreground focus:outline-none"
                >
                  Blog
                </Link>
              </NavigationMenuLink>
            </NavigationMenuItem>
          </NavigationMenuList>
        </NavigationMenu>

        {/* Desktop Actions */}
        <div className="hidden lg:flex items-center space-x-4">
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
                <MobileNavSection title="Products" items={products} onClose={() => setMobileOpen(false)} />
                <MobileNavSection title="Solutions" items={solutions} onClose={() => setMobileOpen(false)} />
                <MobileNavSection title="Developers" items={developers} onClose={() => setMobileOpen(false)} />
                <Link
                  href="/blog"
                  className="text-lg font-medium hover:text-amber-500 transition-colors"
                  onClick={() => setMobileOpen(false)}
                >
                  Blog
                </Link>
                <div className="pt-4 border-t border-border space-y-2">
                  <Link href="/docs" onClick={() => setMobileOpen(false)}>
                    <Button className="w-full">Get Started</Button>
                  </Link>
                </div>
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}

const ListItem = ({
  className,
  title,
  children,
  href,
  ...props
}: {
  className?: string;
  title: string;
  children: React.ReactNode;
  href: string;
}) => {
  return (
    <li>
      <NavigationMenuLink asChild>
        <Link
          href={href}
          className={cn(
            "block select-none space-y-1 rounded-md p-3 leading-none no-underline outline-none transition-colors hover:bg-accent/10 hover:text-accent-foreground focus:bg-accent/10 focus:text-accent-foreground",
            className
          )}
          {...props}
        >
          <div className="text-sm font-medium leading-none">{title}</div>
          <p className="line-clamp-2 text-sm leading-snug text-muted-foreground">
            {children}
          </p>
        </Link>
      </NavigationMenuLink>
    </li>
  );
};

function MobileNavSection({
  title,
  items,
  onClose,
}: {
  title: string;
  items: { title: string; href: string; description: string }[];
  onClose: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center justify-between w-full text-lg font-medium hover:text-amber-500 transition-colors"
      >
        {title}
        <ChevronDown
          className={cn("h-4 w-4 transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <div className="mt-2 ml-4 space-y-2">
          {items.map((item) => (
            <Link
              key={item.title}
              href={item.href}
              className="block text-muted-foreground hover:text-foreground transition-colors py-1"
              onClick={onClose}
            >
              {item.title}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
