import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <section className="py-24">
      <div className="container mx-auto px-4 text-center max-w-xl">
        <h1 className="text-4xl font-bold mb-4">Page not found</h1>
        <p className="text-muted-foreground mb-8">
          Either this page moved during the astral.us → presidioautonomy.com rebuild, or it
          never existed. Try the autonomy explainer or the stack overview.
        </p>
        <div className="flex flex-wrap gap-3 justify-center">
          <Button asChild>
            <Link href="/">Home</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/autonomy">What is autonomy?</Link>
          </Button>
        </div>
      </div>
    </section>
  );
}
