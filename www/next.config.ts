import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for SST/OpenNext deployment
  output: "standalone",

  // Image optimization
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "astral.us",
      },
      {
        protocol: "https",
        hostname: "*.astral.us",
      },
    ],
  },

  // SEO redirects from old WordPress URLs
  async redirects() {
    return [
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
      // Old checkout URLs → products (no e-commerce on site)
      {
        source: "/checkout/:path*",
        destination: "/products",
        permanent: true,
      },
      {
        source: "/cart",
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
