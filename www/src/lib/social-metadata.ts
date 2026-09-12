import type { Metadata } from "next";
import { SITE } from "./site";

type OgImage = {
  url: string;
  width?: number;
  height?: number;
  alt?: string;
};

/**
 * Per-route Open Graph + Twitter cards with correct canonical URL.
 * Relies on root `metadataBase` to resolve relative image paths.
 */
export function socialMeta(
  pathname: string,
  ogTitle: string,
  description: string,
  options?: {
    type?: "website" | "article";
    publishedTime?: string;
    images?: OgImage[];
  }
): Pick<Metadata, "openGraph" | "twitter" | "alternates"> {
  const canonical =
    pathname === "/" ? SITE.origin : `${SITE.origin}${pathname}`;
  const images: OgImage[] =
    options?.images && options.images.length > 0
      ? options.images
      : [
          {
            url: "/og-image.png",
            width: 1200,
            height: 630,
            alt: ogTitle,
          },
        ];
  const type = options?.type ?? "website";
  return {
    alternates: { canonical },
    openGraph: {
      title: ogTitle,
      description,
      url: canonical,
      siteName: "Coybot",
      locale: "en_US",
      type,
      images,
      ...(type === "article" && options?.publishedTime
        ? { publishedTime: options.publishedTime }
        : {}),
    },
    twitter: {
      card: "summary_large_image",
      title: ogTitle,
      description,
      images: images.map((i) => i.url),
    },
  };
}
