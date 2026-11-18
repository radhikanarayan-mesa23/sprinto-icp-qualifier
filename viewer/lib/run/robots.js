// robots.txt check. No robots.txt (or an unreadable one) means allowed, exactly like the Python fetcher.
import robotsParser from "robots-parser";
import { safeFetch } from "./net.js";

const TOKEN = "SprintoICPQualifier"; // the product token in the honest User-Agent

export async function checkRobots(domain, pageUrl, { fetchFn = safeFetch, userAgent, timeoutMs = 4000, ...rest } = {}) {
  const robotsUrl = `${pageUrl.replace(/\/?$/, "/")}robots.txt`; // https://acme.com/ -> https://acme.com/robots.txt
  const r = await fetchFn(robotsUrl, { timeoutMs, maxBytes: 500_000, userAgent, baseDomain: domain, expectHtml: false, ...rest });
  if (r.error) return { allowed: true, note: `robots.txt unreachable (${r.error}); treated as allowed`, requested: true };
  if (r.status !== 200) return { allowed: true, note: `no robots.txt (HTTP ${r.status})`, requested: true };
  const allowed = robotsParser(robotsUrl, r.text).isAllowed(pageUrl, TOKEN);
  return { allowed: allowed !== false, note: allowed === false ? "robots.txt disallows this page" : "robots.txt allows it", requested: true };
}
