import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "ARIE Internal Command Center | N8iV Promotions",
  description:
    "Internal operations control plane for attribution execution, tenant readiness, reporting, cost governance, and incident response.",
  robots: { index: false, follow: false }
};

export default function RootLayout({
  children
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
