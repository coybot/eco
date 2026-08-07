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
    default: "Presidio - Open Autonomy Stack for Drones & Rovers",
    template: "%s | Presidio",
  },
  description:
    "Presidio builds the open autonomy stack for uncrewed systems. In 10,200 closed-loop trials, most vision-language models couldn't beat a hovering drone — our modular stack reached 1.04 m. Open SDK, simulation, datasets, and NDAA-compliant hardware.",
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
  authors: [{ name: "Presidio" }],
  creator: "Presidio",
  openGraph: {
    type: "website",
    locale: "en_US",
    url: "https://astral.us",
    siteName: "Presidio",
    title: "Presidio - The Autonomous Drone Fleet Platform",
    description:
      "Empowering Developers, Enterprises, and Innovators with Agentic, Agile Unmanned Systems.",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "Presidio - Autonomous Drone Platform",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Presidio - The Autonomous Drone Fleet Platform",
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
