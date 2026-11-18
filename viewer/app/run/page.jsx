import Link from "next/link";
import RunForm from "../../components/RunForm";
import config from "../../lib/generated/config";
import { RUN_LIMITS, hunterUsedThisMonth, usedToday, utcDay } from "../../lib/run/guards";
import { liveDb } from "../../lib/run/db";

export const metadata = { title: "Run a domain" };
export const dynamic = "force-dynamic";

export default async function Page() {
  const env = process.env;
  const budget = Number.parseInt(env.LIVE_DAILY_CALL_BUDGET ?? "", 10);
  let used = null;
  let hunter = null;
  try {
    const db = liveDb();
    used = await usedToday(db, utcDay());
    hunter = await hunterUsedThisMonth(db, utcDay().slice(0, 7));
  } catch { /* shown as "unavailable" below */ }
  const enabled = !!env.RUN_PASSCODE;
  const model = !!env.GEMINI_API_KEY && budget > 0;

  return (
    <>
      <div className="titlerow">
        <div>
          <h1>Run a domain</h1>
          <p className="sub">Run the scraper on one real company homepage and watch each step. A page that is thin, blocked or the wrong company is flagged and shown, which is a correct outcome.</p>
        </div>
        <Link className="btn" href="/how">How it works</Link>
      </div>

      {!enabled ? (
        <p className="notice"><b>Live runs are switched off.</b> No passcode is configured on the server, so the form is disabled. The seeded companies in the review queue are unaffected.</p>
      ) : null}

      <div className="runlayout">
        <div>
          <RunForm enabled={enabled} limit={`${RUN_LIMITS.perVisitorPerHour} live runs per visitor per hour`} />
        </div>
        <aside className="card" aria-label="What a live run does and its limits">
          <h3>What a live run does</h3>
          <ul className="plainlist">
            <li>Checks robots.txt, waits {config.request_delay_seconds}&nbsp;s, then makes <b>one</b> plain request for the homepage. No browser, no other pages, no links followed.</li>
            <li>Cleans the text, then asks the model to extract eight fields. Each value must quote the page, checked in code.</li>
            <li>Runs the code-side evidence check and the hard rules. It does not find a contact or write a draft; the command-line agent does that.</li>
            <li>Refuses private and internal addresses, and stops at CAPTCHAs, challenges and logins. It never tries to get past them.</li>
          </ul>
          <h3 style={{ marginTop: 16 }}>Limits and spend</h3>
          <dl className="facts">
            <dt>Per visitor</dt><dd>{RUN_LIMITS.perVisitorPerHour} runs per hour</dd>
            <dt>Whole demo</dt><dd>{RUN_LIMITS.globalPerHour} runs per hour</dd>
            <dt>Model calls today</dt><dd>{used == null ? "unavailable" : budget > 0 ? `${used} of ${budget} (shared with the command line, UTC day)` : `${used} used, no live budget configured`}</dd>
            <dt>Model</dt><dd>{model ? `${config.model.name} is configured` : "Not configured yet, so runs show the scraper only"}</dd>
            <dt>Hunter</dt><dd>Not used by live runs ({hunter ?? "unavailable"} of {config.hunter.hard_stop_at} searches this month)</dd>
            <dt>Cache</dt><dd>A domain run in the last {config.cache_days} days is reused for free</dd>
          </dl>
        </aside>
      </div>
    </>
  );
}
