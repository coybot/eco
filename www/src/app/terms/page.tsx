import { Metadata } from "next";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "Terms of Service – Astral",
  description: "Astral terms of service",
};

export default function TermsPage() {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-3xl px-6 py-24">
        <h1 className="text-4xl font-bold tracking-tight mb-4">Terms of Service</h1>
        <p className="text-muted-foreground mb-12">Effective date: June 6, 2026</p>

        <Section title="1. Acceptance of Terms">
          <p>By accessing or using Astral's services, including the Drone Operator platform, you agree to be bound by these Terms of Service. If you do not agree, do not use our services.</p>
        </Section>

        <Section title="2. Use of the Service">
          <p>You may use the Astral platform only for lawful purposes and in accordance with these Terms. You are responsible for ensuring your use of the platform complies with all applicable laws and regulations, including aviation regulations in your jurisdiction.</p>
        </Section>

        <Section title="3. Account Responsibilities">
          <p>You are responsible for maintaining the confidentiality of your account credentials and for all activity that occurs under your account. Notify us immediately of any unauthorized access.</p>
        </Section>

        <Section title="4. Safety and Compliance">
          <p>You are solely responsible for the safe operation of any drone or autonomous vehicle used with our platform. Astral does not assume liability for any injury, property damage, or regulatory violation resulting from drone operations. Always comply with local aviation authority regulations.</p>
        </Section>

        <Section title="5. Intellectual Property">
          <p>All content, software, and technology provided by Astral is owned by Astral or its licensors and is protected by applicable intellectual property laws. You may not copy, modify, or distribute our software or content without prior written consent.</p>
        </Section>

        <Section title="6. Disclaimers">
          <p>The service is provided "as is" without warranties of any kind. Astral does not warrant that the service will be uninterrupted, error-free, or suitable for any particular purpose.</p>
        </Section>

        <Section title="7. Limitation of Liability">
          <p>To the fullest extent permitted by law, Astral shall not be liable for any indirect, incidental, special, or consequential damages arising from your use of the service.</p>
        </Section>

        <Section title="8. Termination">
          <p>We reserve the right to suspend or terminate your account at our discretion if you violate these Terms or engage in conduct we determine to be harmful to the platform or other users.</p>
        </Section>

        <Section title="9. Changes to Terms">
          <p>We may modify these Terms at any time. Continued use of the service after changes constitutes acceptance of the updated Terms.</p>
        </Section>

        <Section title="10. Contact">
          <p>Questions about these Terms? Contact us at <a href="mailto:legal@astral.us" className="underline">legal@astral.us</a>.</p>
        </Section>
      </main>
      <Footer />
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="text-xl font-semibold mb-3">{title}</h2>
      <div className="text-muted-foreground leading-relaxed space-y-3">{children}</div>
    </section>
  );
}
