import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for SST/OpenNext deployment
  output: "standalone",

  // Image optimization
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "coy.bot",
      },
      {
        protocol: "https",
        hostname: "*.coy.bot",
      },
    ],
  },

  // SEO redirects from old WordPress URLs
  async redirects() {
    return [
      // Research paper pages → canonical blog post (merged)
      { source: "/research/yonder", destination: "/blog/yonder-drone-navigation-dataset#paper", permanent: true },
      { source: "/research/metric-gap", destination: "/blog/metric-gap-vision-language-drone-navigation#paper", permanent: true },
      { source: "/research/engineering-separation", destination: "/blog/engineering-drone-autonomy-18-iterations#paper", permanent: true },
      { source: "/research/scaling-separation", destination: "/blog/drone-swarm-sensing-1000-drones#paper", permanent: true },
      { source: "/research/gemma4-pilot", destination: "/blog/why-vlm-drones-cant-beat-hovering#paper", permanent: true },
      { source: "/research/counter-uas", destination: "/blog/counter-uas-drone-attack-defense-simulation#paper", permanent: true },
      { source: "/research/droneport-atc", destination: "/blog/droneport-atc-tower-vs-selforg#paper", permanent: true },
      // Old WordPress blog URLs
      {
        source: "/blog/:slug/",
        destination: "/blog/:slug",
        permanent: true,
      },
      // Old product URLs
      {
        source: "/product/:slug",
        destination: "/products/:slug",
        permanent: true,
      },
      // Old solution pages
      {
        source: "/solutions/:slug/",
        destination: "/solutions/:slug",
        permanent: true,
      },
      // Redirect /shop to /products
      {
        source: "/shop",
        destination: "/products",
        permanent: true,
      },
      // Redirect /store to /products
      {
        source: "/store",
        destination: "/products",
        permanent: true,
      },
      // Resources to docs
      {
        source: "/resources",
        destination: "/docs",
        permanent: true,
      },
      // Company page
      {
        source: "/company",
        destination: "/about",
        permanent: true,
      },
    ];
  },

  // Security headers
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "X-DNS-Prefetch-Control",
            value: "on",
          },
          {
            key: "X-Frame-Options",
            value: "SAMEORIGIN",
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "Referrer-Policy",
            value: "origin-when-cross-origin",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
