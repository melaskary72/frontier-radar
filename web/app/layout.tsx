import type { Metadata, Viewport } from "next";
import { Inter, Source_Serif_4 } from "next/font/google";
import Nav from "@/components/Nav";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const serif = Source_Serif_4({ subsets: ["latin"], variable: "--font-serif", display: "swap", weight: ["400", "600", "700"] });

export const metadata: Metadata = {
  title: { default: "Frontier Radar", template: "%s | Frontier Radar" },
  description: "Sourcing radar for frontier technology founders: robotics, defense, energy, space, manufacturing, semiconductors.",
  robots: { index: false, follow: false },
};
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf8f3" },
    { media: "(prefers-color-scheme: dark)", color: "#141617" },
  ],
};

const FOOTER =
  "Frontier Radar is an independent work sample by Mohamed El Askary, built for the Anti Fund MTS / Associate application. Not affiliated with Anti Fund.";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable}`}>
      <body>
        <a href="#main" className="visually-hidden">Skip to content</a>
        <header className="topbar">
          <div className="shell topbar-inner">
            <a className="brand" href="/">Frontier <span>Radar</span></a>
            <Nav />
          </div>
        </header>
        <main id="main" className="shell">{children}</main>
        <footer className="footer">
          <div className="shell">{FOOTER}</div>
        </footer>
      </body>
    </html>
  );
}
