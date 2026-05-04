import { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";

const solutions = {
  defense: {
    name: "Defense",
    description:
      "NDAA-compliant autonomous systems for ISR, perimeter security, and tactical operations.",
    useCases: [
      "Perimeter monitoring and threat detection",
      "Rapid ISR mission deployment",
      "GPS and comm-denied autonomy",
    ],
  },
  agriculture: {
    name: "Agriculture",
    description:
      "Precision farming with crop health monitoring, spraying, and yield optimization.",
    useCases: [
      "Field health imaging and analysis",
      "Autonomous crop scouting missions",
      "Operational planning for seasonal coverage",
    ],
  },
  infrastructure: {
    name: "Infrastructure",
    description:
      "Automated inspection of power lines, pipelines, bridges, and critical assets.",
    useCases: [
      "Routine linear asset inspection",
      "Condition tracking over time",
      "Safer remote visual verification",
    ],
  },
  "public-safety": {
    name: "Public Safety",
    description:
      "Search and rescue, emergency response, crowd monitoring, and law enforcement support.",
    useCases: [
      "Search support in wide-area incidents",
      "Faster situational awareness",
      "Mission replay and reporting workflows",
    ],
  },
} as const;

type SolutionSlug = keyof typeof solutions;

export function generateStaticParams() {
  return Object.keys(solutions).map((slug) => ({ slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const solution = solutions[slug as SolutionSlug];
  if (!solution) return { title: "Solution Not Found" };
  return {
    title: `${solution.name} Solutions`,
    description: solution.description,
  };
}

export default async function SolutionPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const solution = solutions[slug as SolutionSlug];
  if (!solution) notFound();

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        <section className="py-16 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4 max-w-4xl">
            <Link
              href="/"
              className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-6"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Home
            </Link>
            <h1 className="text-4xl sm:text-5xl font-bold mb-4">
              {solution.name}
            </h1>
            <p className="text-lg text-muted-foreground">{solution.description}</p>
          </div>
        </section>

        <section className="py-12 bg-background">
          <div className="container mx-auto px-4 max-w-4xl">
            <h2 className="text-2xl font-bold mb-6">Common use cases</h2>
            <div className="space-y-3">
              {solution.useCases.map((useCase) => (
                <div key={useCase} className="flex items-center text-sm">
                  <Check className="h-4 w-4 text-amber-500 mr-2" />
                  <span className="text-muted-foreground">{useCase}</span>
                </div>
              ))}
            </div>
            <div className="mt-10">
              <a href="mailto:hello@astral.us">
                <Button>
                  Email Us
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </a>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
