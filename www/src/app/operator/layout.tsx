import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Operator | Coybot",
  description: "Coybot drone operator console",
  robots: { index: false, follow: false },
}

export default function OperatorLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground font-sans antialiased">
      {children}
    </div>
  )
}
