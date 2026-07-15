import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CROSSUB Voice Agent — Tester",
  description: "Phase 0 tester for the CROSSUB phone AI voice agent (English + 中文)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
