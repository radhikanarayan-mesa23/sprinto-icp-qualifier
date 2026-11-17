import Link from "next/link";
import { listCompanies } from "../lib/db";
import { fmtDate } from "../lib/format";
import { Dash, DecisionPill } from "../components/ui";

export const metadata = { title: "Review queue" };

const DRAFT = { drafted: "Ready to review", blocked: "Not drafted", failed_provenance: "Failed the provenance gate", failed: "Draft failed" };

function bucket(r) {
  if (r.decision === "no_go") return "out";
  return r.draft_status === "drafted" ? "ready" : "human";
}

export default async function Page({ searchParams }) {
  const { view = "all" } = await searchParams;
  const all = await listCompanies();
  const n = { all: all.length, ready: 0, human: 0, out: 0 };
  for (const r of all) n[bucket(r)] += 1;
  const rows = view === "all" ? all : all.filter((r) => bucket(r) === view);
  const fixtures = all.length > 0 && all.every((r) => r.run_source !== "live");
  const tabs = [
    ["all", "All companies", n.all],
    ["ready", "Ready to review", n.ready],
    ["human", "Needs a human", n.human],
    ["out", "Disqualified", n.out],
  ];

  return (
    <>
      <div className="titlerow">
        <div>
          <h1>Review queue</h1>
          <p className="sub">
            {n.ready} draft{n.ready === 1 ? " is" : "s are"} ready to review. {n.human} compan{n.human === 1 ? "y needs" : "ies need"} a human
            and {n.out} {n.out === 1 ? "was" : "were"} disqualified by a hard rule. Nothing is sent from here.
          </p>
        </div>
        <Link className="btn" href="/how">How it works</Link>
      </div>

      {fixtures ? (
        <p className="notice">
          <b>Seeded demo data.</b> These {all.length} companies are test fixtures, scored by test stand-ins rather than a live model.
          Their source links open the fixture pages on this site. Real runs will appear here labelled Live.
        </p>
      ) : null}

      <nav className="tabs" aria-label="Queue views">
        {tabs.map(([key, label, count]) => (
          <Link key={key} href={key === "all" ? "/" : `/?view=${key}`} aria-current={view === key ? "page" : undefined}>
            {label} <span className="n">{count}</span>
          </Link>
        ))}
      </nav>

      <div className="listmeta"><span>{rows.length} {rows.length === 1 ? "company" : "companies"}</span><span className="small">Sorted by decision, then rank (1 is best)</span></div>

      <div className="panel">
        {rows.length === 0 ? (
          <div className="empty"><b>Nothing in this view.</b>No company currently falls in this queue.</div>
        ) : (
          <table className="q">
            <thead>
              <tr>
                <th scope="col">Company</th>
                <th scope="col">Decision</th>
                <th scope="col" className="r">Rank</th>
                <th scope="col">Contact</th>
                <th scope="col">Draft</th>
                <th scope="col" className="r">Flags</th>
                <th scope="col" className="r">Fetched</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const c = r.contact;
                return (
                  <tr key={r.domain}>
                    <td className="c-name name">
                      <Link href={`/c/${r.domain}`}>{r.name || r.domain}</Link>
                      <span className="second mono">{r.domain}{r.run_source !== "live" ? " · fixture" : ""}</span>
                    </td>
                    <td className="c-decision"><DecisionPill decision={r.decision} /></td>
                    <td className="c-rank r">{r.rank ?? <Dash />}</td>
                    <td className="c-contact">
                      {c ? <>{c.first_name} {c.last_name}<span className="second">{c.title}</span></> : <Dash />}
                    </td>
                    <td className="c-draft">{DRAFT[r.draft_status] ?? <Dash />}</td>
                    <td className="c-flags r">{r.flags?.length ?? 0}</td>
                    <td className="c-date r nowrap">{fmtDate(r.fetched_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
