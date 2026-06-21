import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#000000",
};

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_BASE_URL ?? "https://astral.us"
  ),
  title: {
    default: "Astral - The Autonomous Drone Fleet Platform",
    template: "%s | Astral",
  },
  description:
    "Astral builds the autonomy stack for uncrewed systems. Open SDK, simulation, operator apps, and datasets — integrate your own platform or work with our NDAA-compliant M1-A quadcopter and M1-G rover.",
  keywords: [
    "autonomous drones",
    "drone autonomy stack",
    "AI drone navigation",
    "NDAA compliant drones",
    "drone SDK",
    "GPS-denied drone",
    "drone fleet management",
    "vision-language model drone",
    "Isaac Sim drone",
    "Jetson Orin drone",
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
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
