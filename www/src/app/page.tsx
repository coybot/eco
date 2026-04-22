import { Header, Footer } from "@/components/layout";
import {
  HeroSection,
  ProductsSection,
  SolutionsSection,
  DeveloperSection,
  AppShowcaseSection,
  BlogPreviewSection,
  CTASection,
} from "@/components/sections";

export default function HomePage() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <HeroSection />
        <ProductsSection />
        <SolutionsSection />
        <DeveloperSection />
        <AppShowcaseSection />
        <BlogPreviewSection />
        <CTASection />
      </main>
      <Footer />
    </div>
  );
}
