"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Building2, Shield, Users, Headphones, ArrowRight } from "lucide-react";
import { Header, Footer } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";

const benefits = [
  {
    icon: Shield,
    title: "NDAA Compliant",
    description: "All components meet federal compliance requirements for government and defense applications.",
  },
  {
    icon: Users,
    title: "Fleet Management",
    description: "Manage hundreds of drones from a single dashboard with real-time telemetry and control.",
  },
  {
    icon: Building2,
    title: "Custom Integration",
    description: "API-first architecture integrates with your existing systems and workflows.",
  },
  {
    icon: Headphones,
    title: "Dedicated Support",
    description: "Priority support with dedicated account management and SLAs.",
  },
];

const useCases = [
  "Defense & Security",
  "Critical Infrastructure",
  "Agriculture at Scale",
  "Emergency Response",
  "Asset Inspection",
  "Research & Development",
];

export default function EnterprisePage() {
  const [formData, setFormData] = useState({
    firstName: "",
    lastName: "",
    email: "",
    company: "",
    jobTitle: "",
    fleetSize: "",
    useCase: "",
    message: "",
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSubmitted, setIsSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);

    const fullName = `${formData.firstName} ${formData.lastName}`.trim();
    const params = new URLSearchParams({
      subject: `Enterprise inquiry from ${fullName} at ${formData.company}`,
      body: `Name: ${fullName}\nCompany: ${formData.company}\nEmail: ${formData.email}\nFleet size: ${formData.fleetSize}\nUse case: ${formData.useCase}\nMessage: ${formData.message}`,
    });
    window.open(`mailto:hello@astral.us?${params}`);
    setIsSubmitted(true);
    setIsSubmitting(false);
  };

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) => {
    setFormData((prev) => ({
      ...prev,
      [e.target.name]: e.target.value,
    }));
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1">
        {/* Hero */}
        <section className="py-20 bg-gradient-to-b from-background to-card">
          <div className="container mx-auto px-4">
            <div className="max-w-3xl mx-auto text-center">
              <h1 className="text-4xl sm:text-5xl font-bold mb-6">
                Enterprise Drone Solutions
              </h1>
              <p className="text-lg text-muted-foreground mb-8">
                Deploy autonomous drone fleets at scale with dedicated support,
                custom integrations, and NDAA-compliant hardware.
              </p>
              <div className="flex flex-wrap justify-center gap-4">
                <a href="#contact-form">
                  <Button size="lg" className="glow">
                    Contact Sales
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </a>
                <Link href="/products">
                  <Button size="lg" variant="outline">
                    View Products
                  </Button>
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* Benefits */}
        <section className="py-16 bg-card">
          <div className="container mx-auto px-4">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6 max-w-6xl mx-auto">
              {benefits.map((benefit) => (
                <Card key={benefit.title} className="bg-background border-border">
                  <CardContent className="p-6">
                    <div className="inline-flex p-3 rounded-lg bg-amber-500/10 mb-4">
                      <benefit.icon className="h-6 w-6 text-amber-500" />
                    </div>
                    <h3 className="text-lg font-semibold mb-2">{benefit.title}</h3>
                    <p className="text-sm text-muted-foreground">
                      {benefit.description}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* Contact Form */}
        <section id="contact-form" className="py-20 bg-background scroll-mt-20">
          <div className="container mx-auto px-4">
            <div className="max-w-5xl mx-auto">
              <div className="grid lg:grid-cols-2 gap-12">
                {/* Form */}
                <div>
                  <h2 className="text-3xl font-bold mb-4">Talk to Sales</h2>
                  <p className="text-muted-foreground mb-8">
                    Fill out the form and our enterprise team will get back to you
                    within 24 hours.
                  </p>

                  {isSubmitted ? (
                    <Card className="bg-card border-success/50">
                      <CardContent className="p-8 text-center">
                        <div className="w-16 h-16 mx-auto bg-success/10 rounded-full flex items-center justify-center mb-4">
                          <Check className="h-8 w-8 text-success" />
                        </div>
                        <h3 className="text-xl font-semibold mb-2">
                          Thank you for reaching out!
                        </h3>
                        <p className="text-muted-foreground">
                          Our enterprise team will contact you within 24 hours.
                        </p>
                      </CardContent>
                    </Card>
                  ) : (
                    <form onSubmit={handleSubmit} className="space-y-6">
                      <div className="grid sm:grid-cols-2 gap-4">
                        <div className="space-y-2">
                          <Label htmlFor="firstName">First Name *</Label>
                          <Input
                            id="firstName"
                            name="firstName"
                            value={formData.firstName}
                            onChange={handleChange}
                            required
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="lastName">Last Name *</Label>
                          <Input
                            id="lastName"
                            name="lastName"
                            value={formData.lastName}
                            onChange={handleChange}
                            required
                          />
                        </div>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="email">Work Email *</Label>
                        <Input
                          id="email"
                          name="email"
                          type="email"
                          value={formData.email}
                          onChange={handleChange}
                          required
                        />
                      </div>

                      <div className="grid sm:grid-cols-2 gap-4">
                        <div className="space-y-2">
                          <Label htmlFor="company">Company *</Label>
                          <Input
                            id="company"
                            name="company"
                            value={formData.company}
                            onChange={handleChange}
                            required
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="jobTitle">Job Title</Label>
                          <Input
                            id="jobTitle"
                            name="jobTitle"
                            value={formData.jobTitle}
                            onChange={handleChange}
                          />
                        </div>
                      </div>

                      <div className="grid sm:grid-cols-2 gap-4">
                        <div className="space-y-2">
                          <Label htmlFor="fleetSize">Expected Fleet Size</Label>
                          <select
                            id="fleetSize"
                            name="fleetSize"
                            value={formData.fleetSize}
                            onChange={handleChange}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          >
                            <option value="">Select...</option>
                            <option value="1-10">1-10 drones</option>
                            <option value="11-50">11-50 drones</option>
                            <option value="51-100">51-100 drones</option>
                            <option value="100+">100+ drones</option>
                          </select>
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="useCase">Primary Use Case</Label>
                          <select
                            id="useCase"
                            name="useCase"
                            value={formData.useCase}
                            onChange={handleChange}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          >
                            <option value="">Select...</option>
                            {useCases.map((uc) => (
                              <option key={uc} value={uc}>
                                {uc}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="message">Tell us about your project</Label>
                        <Textarea
                          id="message"
                          name="message"
                          value={formData.message}
                          onChange={handleChange}
                          rows={4}
                          placeholder="Describe your use case, requirements, timeline..."
                        />
                      </div>

                      <Button
                        type="submit"
                        size="lg"
                        className="w-full glow"
                        disabled={isSubmitting}
                      >
                        {isSubmitting ? "Submitting..." : "Submit Request"}
                      </Button>

                      <p className="text-xs text-muted-foreground text-center">
                        By submitting this form, you agree to our{" "}
                        <Link href="/privacy" className="underline">
                          Privacy Policy
                        </Link>
                        .
                      </p>
                    </form>
                  )}
                </div>

                {/* Info Column */}
                <div className="lg:pl-8">
                  <div className="sticky top-24 space-y-8">
                    <div>
                      <h3 className="text-lg font-semibold mb-4">
                        Enterprise includes:
                      </h3>
                      <ul className="space-y-3">
                        {[
                          "Volume pricing discounts",
                          "Priority manufacturing queue",
                          "Custom hardware configurations",
                          "Dedicated account manager",
                          "24/7 technical support",
                          "On-site training available",
                          "Custom API integrations",
                          "SLA guarantees",
                        ].map((item) => (
                          <li key={item} className="flex items-center text-sm">
                            <Check className="h-4 w-4 text-amber-500 mr-2 shrink-0" />
                            {item}
                          </li>
                        ))}
                      </ul>
                    </div>

                    <div className="p-6 bg-card rounded-lg border border-border">
                      <h3 className="font-semibold mb-2">
                        Prefer to talk now?
                      </h3>
                      <p className="text-sm text-muted-foreground mb-4">
                        Email our enterprise team and we&apos;ll get back to
                        you.
                      </p>
                      <Button variant="outline" className="w-full" asChild>
                        <a href="mailto:hello@astral.us">Email Us</a>
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
