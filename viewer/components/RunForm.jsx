"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { DECISIONS, ROWS, flagText, isGraceful, outcome, valueText } from "../lib/format";
import { Pill, Src } from "./ui";

const MARK = { ok: "✓", flag: "!", skip: "–", fail: "✗" };

export default function RunForm({ enabled, limit }) {
  const [state, setState] = useState({ phase: "idle" });
  const domainRef = useRef(null);
  const passRef = useRef(null);

  async function submit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setState({ phase: "running" });
    let res, data;
    try {
      res = await fetch("/api/run", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ domain: form.get("domain"), passcode: form.get("passcode") }),
      });
      data = await res.json();
    } catch {
      setState({ phase: "error", message: "The request did not reach the server. Check your connection and try again." });
      return;
    }
    if (!res.ok || !data.ok) {
      setState({ phase: "error", code: data?.error?.code, message: data?.error?.message ?? "The run failed. Try again in a minute." });
      (data?.error?.code === "bad_passcode" ? passRef : domainRef).current?.focus();
      return;
    }
    setState({ phase: "done", data });
  }

  const running = state.phase === "running";
  return (
    <>
      <form className="runform" onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor="domain">Company domain</label>
          <input id="domain" name="domain" ref={domainRef} type="text" inputMode="url" autoComplete="off" autoCapitalize="none"
            spellCheck={false} placeholder="acme.com…" required disabled={!enabled} aria-describedby="domain-help" />
          <p id="domain-help" className="small muted">Just the domain, for example acme.com. Internal and private addresses are refused.</p>
        </div>
        <div className="field">
          <label htmlFor="passcode">Passcode</label>
          <input id="passcode" name="passcode" ref={passRef} type="password" autoComplete="off" required disabled={!enabled} aria-describedby="pass-help" />
          <p id="pass-help" className="small muted">Live runs are gated so a crawler cannot spend the budget.</p>
        </div>
        <div className="actions">
          <button className="btn primary" type="submit" disabled={!enabled || running}>{running ? "Running…" : "Run scraper"}</button>
          <span className="small muted">{limit}</span>
        </div>
      </form>

      <div aria-live="polite" role="status" className="runout">
        {running ? <p className="muted">Checking robots.txt, fetching the homepage, cleaning and extracting. This takes up to 25 seconds…</p> : null}
        {state.phase === "error" ? (
          <p className="alert" role="alert"><b>Not run.</b> {state.message}</p>
        ) : null}
        {state.phase === "done" ? <Result d={state.data} /> : null}
      </div>
    </>
  );
}

function Result({ d }) {
  const dec = DECISIONS[d.decision] ?? { label: d.decision, tone: "neutral" };
  return (
    <section className="result" aria-label="Result of the run">
      <div className="resulthead">
        <Pill tone={dec.tone}>{dec.label}</Pill>
        <h2>{d.domain}</h2>
        {d.from_cache ? <span className="chip">From cache, nothing fetched or spent</span> : null}
      </div>
      <p className="lead">{d.defence}</p>

      {d.failure ? (
        <div className="card good-card">
          <h3>Flagged, not guessed</h3>
          <p>{d.explanation} This is a correct outcome, not a failure of the tool: the agent stopped at the barrier, recorded the reason and queued the company for a human.</p>
        </div>
      ) : null}

      <h3 className="stephead">What happened</h3>
      <ol className="steps">
        {d.steps.map((s) => (
          <li key={s.id} className={s.state}>
            <span className="mark" aria-label={s.state}>{MARK[s.state]}</span>
            <span><b>{s.label}.</b> {s.detail}</span>
          </li>
        ))}
      </ol>

      {d.rows.length ? (
        <>
          <h3 className="stephead">ICP rows from the homepage</h3>
          <div className="panel">
            <table className="ev">
              <thead><tr><th scope="col">Row</th><th scope="col">Outcome</th><th scope="col">Found</th><th scope="col">Source</th></tr></thead>
              <tbody>
                {d.rows.map((r) => {
                  const o = outcome(r.outcome);
                  return (
                    <tr key={r.row}>
                      <td className="rowname">{ROWS[r.row]?.label}<span className="kind">{ROWS[r.row]?.kind} row</span></td>
                      <td><Pill tone={o.tone}>{o.label}</Pill></td>
                      <td>{r.field ? <>{valueText(r.value)} <span className="muted small mono">{r.field}</span>{r.quote ? <p className="why"><mark className="q">{"“"}{r.quote}{"”"}</mark></p> : null}</> : <span className="nullv">{"–"}</span>}</td>
                      <td><Src url={r.source_url} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {d.flags.length ? (
        <>
          <h3 className="stephead">Flags</h3>
          <div className="panel">
            <ul className="flaglist">
              {d.flags.map((f) => (
                <li key={f}><code>{f}</code><span>{flagText(f) || "Recorded by the agent."}{isGraceful(f) ? <span className="good">Flagged, not guessed</span> : null}</span></li>
              ))}
            </ul>
          </div>
        </>
      ) : null}

      {d.cleaned ? (
        <details className="more">
          <summary>The cleaned text the agent read ({d.cleaned.words} words)</summary>
          <pre className="preview">{d.cleaned.preview}{d.cleaned.footer ? `\n\nFooter signals: ${d.cleaned.footer}` : ""}</pre>
        </details>
      ) : null}

      <p className="small muted spend">
        Model calls this run: {d.model.calls}. Shared daily budget: {d.guards.budget_limit == null ? "not configured" : `${d.guards.budget_used} of ${d.guards.budget_limit} used today (UTC)`}.
        Hunter is not used by live runs ({d.guards.hunter_used} of {d.guards.hunter_cap} searches this month).
        Your live runs this hour: {d.guards.visitor_runs_used} of {d.guards.visitor_runs_limit}. Took {d.elapsed_seconds.toFixed(1)}&nbsp;s.
      </p>
      {d.saved ? <p><Link className="btn" href={d.record_path}>Open the full record</Link></p> : <p className="small muted">Not saved, because the problem looks temporary and should be retried.</p>}
    </section>
  );
}
