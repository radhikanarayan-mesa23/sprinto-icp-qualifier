import "./scraper-hero.css";
import ScraperHero from "@/components/ui/scraper-hero";
import config from "@/lib/generated/config";
import { RUN_LIMITS, usedToday, utcDay } from "@/lib/run/guards";
import { liveDb } from "@/lib/run/db";
import { modelKey } from "@/lib/run/core";

export const metadata = { title: "Run a domain" };
export const dynamic = "force-dynamic";

// Open demo: no passcode and no sign-in. The model key and database URL stay on the server; the browser only ever
// receives these booleans and counters.
export default async function Page() {
  const env = process.env;
  const budget = Number.parseInt(env.LIVE_DAILY_CALL_BUDGET ?? "", 10);
  let used = null;
  try { used = await usedToday(liveDb(), utcDay()); } catch { /* shown as unavailable */ }
  return (
    <ScraperHero
      config={{
        disabled: env.LIVE_RUNS_DISABLED === "1",
        modelReady: !!modelKey(env, config) && budget > 0,
        perVisitor: RUN_LIMITS.perVisitorPerHour, global: RUN_LIMITS.globalPerHour,
        budget: budget > 0 ? budget : null, used, model: config.model.name, delay: config.request_delay_seconds,
      }}
    />
  );
}
