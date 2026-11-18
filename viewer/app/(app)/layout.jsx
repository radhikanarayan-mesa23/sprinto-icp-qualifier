import Link from "next/link";
import Nav from "@/components/Nav";
import { Mark } from "@/components/ui";
import { getSummary } from "@/lib/db";

export const dynamic = "force-dynamic";

// The ledger shell: sidebar on the page ground, a folding menu on the phone. /run is outside this group, full-bleed.
export default async function AppLayout({ children }) {
  let s = { total: 0, human: 0, sources: [] };
  try { s = await getSummary(); } catch { /* pages render their own error state */ }
  const fixtures = s.sources.length > 0 && s.sources.every((x) => x !== "live");
  return (
    <>
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
    </>
  );
}
