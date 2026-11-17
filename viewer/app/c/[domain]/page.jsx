import Link from "next/link";
import { notFound } from "next/navigation";
import { getCompany } from "../../../lib/db";
import { ROWS, fmtDate, fmtSeconds, flagText, isGraceful, outcome, valueText } from "../../../lib/format";
import { Dash, DecisionPill, Pill, Src } from "../../../components/ui";

export async function generateMetadata({ params }) {
  const { domain } = await params;
  return { title: domain };
}

const TABS = [["evidence", "Evidence"], ["draft", "Contact and draft"], ["log", "Run log"]];
const FIELD_NAMES = ["company_name", "certifications_found", "product_description", "buyer_type", "funding_stage",
  "open_roles_count", "enterprise_signal", "compliance_pressure_signal"];

function highlight(text, anchor) {
  if (!anchor) return [text];
  const i = text.indexOf(anchor);
  if (i < 0) return [text];
  return [text.slice(0, i), <mark key="m" className="q">{anchor}</mark>, text.slice(i + anchor.length)];
}

export default async function Page({ params, searchParams }) {
  const { domain } = await params;
  const { tab = "evidence" } = await searchParams;
  if (!/^[a-z0-9.-]{3,253}$/.test(domain)) notFound();
  const r = await getCompany(domain);
  if (!r) notFound();
  const active = TABS.some(([k]) => k === tab) ? tab : "evidence";
  const name = r.extracted_fields?.company_name?.value || r.domain;
  const fixture = r.run_source !== "live";
  const cap = 4;

  return (
    <>
      <p className="crumbs"><Link href="/">Review queue</Link> / <span>{r.domain}</span></p>
      <div className="titlerow">
        <div>
          <h1>{name}</h1>
          <p className="sub">{r.defence}</p>
        </div>
        <span className="chip">{fixture ? "Fixture, scored by test stand-ins" : "Live run"}</span>
      </div>

      <div className="strip">
        <div><div className="l">Decision</div><div className="v"><DecisionPill decision={r.decision} /></div>
          <div className="s">{r.queue ? `Queue: ${r.queue.replace("_", " ")}` : "No review queue"}</div></div>
        <div><div className="l">Rank</div><div className="v">{r.rank ?? <Dash />} {r.rank ? <small>of 4</small> : null}</div>
          <div className="s">{r.rank_basis ?? "Only companies that qualify are ranked"}</div></div>
        <div><div className="l">Fetches beyond the homepage</div><div className="v">{r.fetches_beyond_homepage} <small>of&nbsp;{cap} allowed</small></div>
          <div className="s">{r.stop_reason?.replaceAll("_", " ")}</div></div>
        <div><div className="l">Model calls</div><div className="v">{r.model_calls} <small>of&nbsp;7 allowed</small></div>
          <div className="s">{fmtSeconds(r.elapsed_seconds)} (target: under 120&nbsp;s)</div></div>
      </div>

      <nav className="tabs" aria-label="Company sections">
        {TABS.map(([k, label]) => (
          <Link key={k} href={`/c/${r.domain}?tab=${k}`} aria-current={active === k ? "page" : undefined}>{label}</Link>
        ))}
      </nav>

      {active === "evidence" && <Evidence r={r} />}
      {active === "draft" && <Draft r={r} />}
      {active === "log" && <Log r={r} />}
    </>
  );
}

function Evidence({ r }) {
  const ORDER = ["already_certified", "b2b_saas", "right_stage", "compliance_pressure"]; // the spec's order
  const reasons = [...(r.reasons ?? [])].sort((a, b) => ORDER.indexOf(a.row) - ORDER.indexOf(b.row));
  const f = r.extracted_fields;
  return (
    <>
      <section className="sec">
        <h2>The four ICP rows</h2>
        <p className="lead">Code decides whether each row is resolved. The model only judges what was found. Every value below cites the page it came from and a short quote copied from that page.</p>
        <div className="panel">
          {reasons.length === 0 ? (
            <div className="empty"><b>No evidence rows.</b>{r.failure_reason ? `The run stopped before extraction: ${r.failure_reason}.` : "The run stopped before any row was evaluated."}</div>
          ) : (
            <table className="ev">
              <thead><tr><th scope="col">Row</th><th scope="col">Outcome</th><th scope="col">What was found</th><th scope="col">Source</th><th scope="col">Quote from the page</th></tr></thead>
              <tbody>
                {reasons.map((x) => {
                  const o = outcome(x.outcome);
                  const meta = ROWS[x.row];
                  return (
                    <tr key={x.row}>
                      <td className="rowname">{meta?.label ?? x.row}<span className="kind">{meta?.kind} row</span></td>
                      <td><Pill tone={o.tone}>{o.label}</Pill></td>
                      <td>
                        {x.field ? <><span>{valueText(x.value)}</span> <span className="muted small mono">{x.field}</span></> : <Dash />}
                        <p className="why">{x.reason}</p>
                      </td>
                      <td><Src url={x.source_url} /></td>
                      <td className="quote">{x.evidence_quote ? <mark className="q">{"“"}{x.evidence_quote}{"”"}</mark> : <Dash />}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {f ? (
          <details className="more">
            <summary>All eight extracted fields</summary>
            <div className="panel">
              <table className="ev">
                <thead><tr><th scope="col">Field</th><th scope="col">Value</th><th scope="col">Source</th><th scope="col">Quote</th></tr></thead>
                <tbody>
                  {FIELD_NAMES.map((n) => (
                    <tr key={n}>
                      <td className="mono">{n}</td>
                      <td>{f[n]?.value == null ? <span className="nullv">null, not found</span> : valueText(f[n].value)}</td>
                      <td><Src url={f[n]?.source_url} /></td>
                      <td className="quote">{f[n]?.evidence_quote ? `“${f[n].evidence_quote}”` : <Dash />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ) : null}
      </section>
    </>
  );
}

function Draft({ r }) {
  const d = r.draft ?? {};
  const c = r.contact;
  const meta = r.contact_meta ?? {};
  return (
    <>
      <section className="sec">
        <h2>Contact</h2>
        {c ? (
          <div className="card">
            <dl className="facts">
              <dt>Person</dt><dd><b style={{ fontWeight: 500 }}>{c.first_name} {c.last_name}</b>, {c.title}</dd>
              <dt>Work email</dt><dd className="mono">{c.email}</dd>
              <dt>Hunter confidence</dt><dd>{c.confidence}</dd>
              <dt>Why chosen</dt><dd>{c.why_chosen}</dd>
            </dl>
          </div>
        ) : (
          <div className="panel empty">
            <b>No contact searched or found.</b>
            {r.decision === "no_go" || r.decision === "manual_review"
              ? "No contact search is made for no go or manual review decisions, so no search was spent."
              : "Nobody on file met the title and confidence rules, so the agent flagged it instead of drafting to “Hi there”."}
            {meta.rejected?.length ? <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>{meta.rejected.map((x, i) => <li key={i}>{x.title || "No title"}: {x.reason.replaceAll("_", " ")}</li>)}</ul> : null}
          </div>
        )}
      </section>

      <section className="sec">
        <h2>Draft</h2>
        {d.status === "drafted" ? (
          <div className="grid2">
            <div className="card">
              <p className="subject">Subject: {d.subject}</p>
              <p className="email">{highlight(d.email, d.hook?.anchor)}</p>
              <p className="small muted" style={{ marginTop: 14 }}>{d.word_count} words (limit: under 120). Draft only. Nothing is sent.</p>
            </div>
            <div style={{ display: "grid", gap: "var(--gap)" }}>
              <div className="card">
                <h3>Two likely pain points</h3>
                <ul className="pains">
                  {r.pain_points.map((p, i) => (
                    <li key={i}>{p.text}<small>tied to <span className="mono">{p.field}</span> <Src url={p.source_url} /></small></li>
                  ))}
                </ul>
              </div>
              <div className="card">
                <h3>The hook</h3>
                <p>The opening line rests on <span className="mono">{d.hook.field}</span>: <mark className="q">{d.hook.anchor}</mark></p>
                <p className="small muted" style={{ marginTop: 6 }}>Source <Src url={d.hook.source_url} /></p>
              </div>
            </div>
          </div>
        ) : (
          <div className="panel empty">
            <b>{d.status === "failed_provenance" ? "The draft failed the provenance gate, so no draft stands." : "No draft was written."}</b>
            {d.status === "failed_provenance"
              ? `A fluent email built on an unverified fact is the failure this agent is built to prevent. ${d.failure_reason ?? ""}`
              : d.status === "failed" ? `The draft step failed: ${d.failure_reason}.`
              : r.decision === "no_go" || r.decision === "manual_review" ? "Only go decisions get a draft."
              : "There was no suitable contact or no company-specific fact to open with."}
          </div>
        )}
      </section>

      {d.status === "drafted" || d.status === "failed_provenance" ? (
        <>
          {d.status === "drafted" ? (
            <section className="sec">
              <h2>Provenance: claim, then field, then source</h2>
              <p className="lead">Every company-specific claim in the email maps to a stored field with its source page and quote.</p>
              <div className="panel">
                <table className="ev">
                  <thead><tr><th scope="col">Claim in the email</th><th scope="col">Field</th><th scope="col">Source</th><th scope="col">Quote on the page</th></tr></thead>
                  <tbody>
                    {d.provenance.map((p, i) => (
                      <tr key={i}>
                        <td>{p.claim}</td><td className="mono">{p.field}</td><td><Src url={p.source_url} /></td>
                        <td className="quote"><mark className="q">{"“"}{p.evidence_quote}{"”"}</mark></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
          <section className="sec">
            <h2>Provenance gate</h2>
            <p className="lead">Checked in code, not by the model. Every check must pass for a draft to stand.</p>
            <div className="card">
              <ul className="checks">
                {Object.entries(d.gate ?? {}).map(([k, v]) => (
                  <li key={k}><span className={v.ok ? "ok" : "bad"} aria-label={v.ok ? "passed" : "failed"}>{v.ok ? "✓" : "✗"}</span>
                    <span>{k.replaceAll("_", " ")}{!v.ok ? <span className="muted">: {v.detail}</span> : null}</span></li>
                ))}
              </ul>
            </div>
          </section>
        </>
      ) : null}
    </>
  );
}

function Log({ r }) {
  const pages = r.pages_fetched ?? [];
  const trace = r.loop_trace ?? [];
  const cells = [{ home: true, path: "/ (homepage)", result: pages[0]?.failure_reason ?? `${pages[0]?.status} via ${pages[0]?.method}`, bad: !!pages[0]?.failure_reason, sub: "Not counted against the cap" }];
  for (const t of trace) {
    const barrier = ["captcha", "cloudflare_challenge", "login_wall", "robots_disallowed"].includes(t.result);
    cells.push({ path: t.path, result: t.result === "ok" ? "fetched" : t.result.replaceAll("_", " "), bad: barrier, ok: t.result === "ok",
      sub: `for ${t.for_rows.map((x) => ROWS[x]?.label.replace("?", "") ?? x).join(", ")}` });
  }
  while (cells.length < 5) cells.push({ off: true, path: "unused", result: "", sub: "" });
  const flags = r.flags ?? [];

  return (
    <>
      <section className="sec">
        <h2>Fetch trail</h2>
        <p className="lead">The homepage is request one. The loop may add up to {4} more pages from each row&rsquo;s fixed list, and stops when the evidence is enough, the caps are hit or a barrier appears.</p>
        <div className="trail">
          {cells.map((c, i) => (
            <div key={i} className={`cell ${c.home ? "home" : ""} ${c.ok || (c.home && !c.bad) ? "ok" : ""} ${c.bad ? "bad" : ""} ${c.off ? "off" : ""}`}>
              <div className="p">{c.path}</div>
              <div className="r">{c.result}</div>
              <div className="r">{c.sub}</div>
            </div>
          ))}
        </div>
        <p className="trailcap">Stopped because: {r.stop_reason?.replaceAll("_", " ")}. {r.fetches_beyond_homepage} of 4 extra fetches used.</p>
      </section>

      <section className="sec">
        <h2>Pages</h2>
        <div className="panel">
          <table className="plain">
            <thead><tr><th scope="col">Page</th><th scope="col">Status</th><th scope="col">Method</th><th scope="col">Fetched</th></tr></thead>
            <tbody>
              {pages.map((p, i) => (
                <tr key={i}>
                  <td><Src url={p.url} /></td>
                  <td>{p.failure_reason ? <Pill tone="negative">{p.failure_reason.replaceAll("_", " ")}</Pill> : <Pill tone="positive">{p.status} ok</Pill>}</td>
                  <td>{p.method}{p.from_cache ? ", cache" : ""}</td>
                  <td className="nowrap">{fmtDate(p.fetched_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="sec">
        <h2>Flags and failures</h2>
        <p className="lead">A flag is a correct outcome when the agent stops and says so instead of guessing. Those are marked.</p>
        {r.failure_reason ? <div className="card" style={{ marginBottom: "var(--gap)" }}><h3>Failure</h3><p>{r.failure_reason.replaceAll("_", " ")}. The company was queued for a human and the run continued.</p></div> : null}
        <div className="panel">
          {flags.length === 0 ? <div className="empty"><b>No flags.</b>This company ran clean.</div> : (
            <ul className="flaglist">
              {flags.map((f, i) => (
                <li key={i}>
                  <code>{f}</code>
                  <span>{flagText(f) || "Recorded by the agent."}{isGraceful(f) ? <span className="good">Flagged, not guessed</span> : null}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        {r.injection_flags?.length ? (
          <div className="card" style={{ marginTop: "var(--gap)" }}>
            <h3>Text on the page that addressed an AI</h3>
            <p className="muted" style={{ marginBottom: 8 }}>Logged and ignored. It never reached the model and could not change any rule.</p>
            <ul className="pains">{r.injection_flags.map((t, i) => <li key={i} className="ignored">{t}</li>)}</ul>
          </div>
        ) : null}
      </section>

      <section className="sec">
        <h2>Cost and time</h2>
        <div className="card">
          <dl className="facts">
            <dt>Model calls</dt><dd>{r.model_calls} of 7 allowed for one company</dd>
            <dt>Tokens in and out</dt><dd>{r.prompt_tokens} / {r.output_tokens}</dd>
            <dt>Estimated cost</dt><dd>{r.est_cost_usd == null ? "Not configured (no prices in config)" : `$${r.est_cost_usd}`}</dd>
            <dt>Time for this company</dt><dd>{fmtSeconds(r.elapsed_seconds)} against a target of under 120 s and a manual baseline of 15 to 20 minutes</dd>
            <dt>Fetch method</dt><dd>{r.fetch_method}</dd>
          </dl>
        </div>
      </section>
    </>
  );
}
