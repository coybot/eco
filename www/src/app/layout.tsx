import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider } from "@/components/theme-provider";
import { CartProvider } from "@/lib/cart";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "Astral - The Autonomous Drone Fleet Platform",
    template: "%s | Astral",
  },
  description:
    "Empowering Developers, Enterprises, and Innovators with Agentic, Agile Unmanned Systems. Built on Open Source.",
  keywords: [
    "autonomous drones",
    "drone fleet management",
    "AI drones",
    "NDAA compliant drones",
    "drone platform",
    "mission control",
    "drone SDK",
  ],
  authors: [{ name: "Astral" }],
  creator: "Astral",
  openGraph: {
    type: "website",
    locale: "en_US",
    url: "https://astral.us",
    siteName: "Astral",
    title: "Astral - The Autonomous Drone Fleet Platform",
    description:
      "Empowering Developers, Enterprises, and Innovators with Agentic, Agile Unmanned Systems.",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "Astral - Autonomous Drone Platform",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Astral - The Autonomous Drone Fleet Platform",
    description:
      "Empowering Developers, Enterprises, and Innovators with Agentic, Agile Unmanned Systems.",
    images: ["/og-image.png"],
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}
      >
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem={false}
          disableTransitionOnChange
        >
          <CartProvider>{children}</CartProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
