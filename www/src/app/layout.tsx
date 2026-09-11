import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider } from "@/components/theme-provider";
import { Header, Footer } from "@/components/layout";
import { SITE } from "@/lib/site";
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
    process.env.NEXT_PUBLIC_BASE_URL ?? "https://presidioautonomy.com"
  ),
  title: {
    default: "Presidio Autonomy - Open-Source Drone Autonomy Stack",
    template: "%s | Presidio Autonomy",
  },
  description:
    "Waypoint following isn't autonomy. Presidio Autonomy is an open-source stack — and an honest benchmark — for what actually happens when GPS drops, comms die, and the mission changes mid-flight.",
  keywords: [
    "drone autonomy",
    "autonomous drone software",
    "GPS-denied navigation",
    "open source drone SDK",
    "ArduPilot SDK",
    "drone autonomy levels",
    "vision language model drone",
    "drone simulation benchmark",
    "ROS 2 drone",
    "autonomy stack",
  ],
  authors: [{ name: "Presidio Autonomy" }],
  creator: "Presidio Autonomy",
  openGraph: {
    type: "website",
    locale: "en_US",
    url: SITE.origin,
    siteName: "Presidio Autonomy",
    title: "Presidio Autonomy - Open-Source Drone Autonomy Stack",
    description:
      "Waypoint following isn't autonomy. An open-source stack — and an honest benchmark — for what happens when GPS drops and the mission changes mid-flight.",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "Presidio Autonomy",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Presidio Autonomy - Open-Source Drone Autonomy Stack",
    description:
      "Waypoint following isn't autonomy. An open-source stack — and an honest benchmark — for real drone autonomy.",
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
          <div className="min-h-screen flex flex-col">
            <Header />
            <main className="flex-1">{children}</main>
            <Footer />
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
