import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import Nav from "../components/Nav";
import { Mark } from "../components/ui";
import { getSummary } from "../lib/db";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist", weight: ["400", "500", "700"] });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", weight: ["400", "500"] });

export const metadata = {
  title: { default: "ICP Qualifier review queue", template: "%s · ICP Qualifier" },
  description: "Read-only review queue for the Sprinto ICP Qualifier: every decision with its evidence, contact and draft.",
};
export const viewport = { width: "device-width", initialScale: 1, themeColor: "#f2f4f7" };
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }) {
  let s = { total: 0, human: 0, sources: [] };
  try { s = await getSummary(); } catch { /* pages render their own error state */ }
  const fixtures = s.sources.length > 0 && s.sources.every((x) => x !== "live");
  return (
    <html lang="en" className={`${geist.variable} ${mono.variable}`}>
      <body>
        <a className="skip" href="#main">Skip to content</a>
        <div className="app">
          <aside className="side">
            <Link className="brand" href="/"><Mark /> <span translate="no">ICP Qualifier</span></Link>
            <p className="scope">Workspace <b>Sprinto growth</b></p>
            <p className="navgroup">Review</p>
            <Nav human={s.human} />
            <div className="sidefoot">
              <b>Read-only viewer</b>
              {s.total} companies in the database{fixtures ? ", all fixtures" : ""}. Nothing here sends email.
            </div>
          </aside>
          <details className="mobilebar">
            <summary><span style={{ display: "flex", gap: 8, alignItems: "center" }}><Mark /> ICP Qualifier</span><span>Menu</span></summary>
            <div className="menu"><Nav human={s.human} /></div>
          </details>
          <main id="main" className="canvas">{children}</main>
        </div>
      </body>
    </html>
  );
}
