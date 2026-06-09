import { Metadata } from "next";
import { Header, Footer } from "@/components/layout";

export const metadata: Metadata = {
  title: "Privacy Policy – Astral",
  description: "Astral privacy policy",
};

export default function PrivacyPage() {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-3xl px-6 py-24">
        <h1 className="text-4xl font-bold tracking-tight mb-4">Privacy Policy</h1>
        <p className="text-muted-foreground mb-12">Effective date: June 6, 2026</p>

        <Section title="1. Information We Collect">
          <p>We collect information you provide when creating an account, including your name and email address. When you use the Astral Drone Operator app, we may collect operational data such as drone telemetry, flight logs, and usage statistics to provide and improve the service.</p>
        </Section>

        <Section title="2. How We Use Your Information">
          <p>We use the information we collect to operate and improve our services, authenticate your identity, send you service-related communications, and ensure the safety and security of the platform.</p>
        </Section>

        <Section title="3. Data Sharing">
          <p>We do not sell your personal information. We may share data with third-party service providers (such as AWS) solely to operate the service. We may disclose information if required by law.</p>
        </Section>

        <Section title="4. Data Retention">
          <p>We retain your account information for as long as your account is active. Drone telemetry and log data may be retained for up to 90 days. You may request deletion of your account and associated data by contacting us.</p>
        </Section>

        <Section title="5. Security">
          <p>We use industry-standard security measures including encryption in transit and at rest. However, no method of transmission over the internet is 100% secure.</p>
        </Section>

        <Section title="6. Your Rights">
          <p>You may access, update, or delete your personal information at any time by contacting us at privacy@astral.us. Users in certain jurisdictions may have additional rights under applicable law.</p>
        </Section>

        <Section title="7. Changes to This Policy">
          <p>We may update this policy from time to time. We will notify you of significant changes via email or in-app notice.</p>
        </Section>

        <Section title="8. Contact">
          <p>Questions about this policy? Contact us at <a href="mailto:privacy@astral.us" className="underline">privacy@astral.us</a>.</p>
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
