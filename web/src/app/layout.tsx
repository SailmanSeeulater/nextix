import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import { themeBootScript, themeCss } from "@/lib/themes";
import "./globals.css";

// Self-hosted at build time; the browser never requests Google.
const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });

export const metadata: Metadata = {
  title: "nexTix",
  description: "Agent task board",
};

export const viewport: Viewport = {
  themeColor: "#1c1c1a",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // The boot script sets data-theme before paint, so the server markup differs on purpose.
    <html lang="en" className={geist.variable} suppressHydrationWarning>
      <head>
        <style>{themeCss()}</style>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript() }} />
      </head>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
