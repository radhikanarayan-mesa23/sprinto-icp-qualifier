// robots.txt check. No robots.txt (or an unreadable one) means allowed, exactly like the Python fetcher.
// loadRobots reads the file ONCE; isAllowed() then answers for any later page of the same site without another request.
import robotsParser from "robots-parser";
import { safeFetch } from "./net.js";

const TOKEN = "SprintoICPQualifier"; // the product token in the honest User-Agent

export async function loadRobots(domain, pageUrl, { fetchFn = safeFetch, userAgent, timeoutMs = 4000, ...rest } = {}) {
  const robotsUrl = `${pageUrl.replace(/\/?$/, "/")}robots.txt`; // https://acme.com/ -> https://acme.com/robots.txt
  const r = await fetchFn(robotsUrl, { timeoutMs, maxBytes: 500_000, userAgent, baseDomain: domain, expectHtml: false, ...rest });
  if (r.error) return { isAllowed: () => true, allowed: true, note: `robots.txt unreachable (${r.error}); treated as allowed`, requested: true };
  if (r.status !== 200) return { isAllowed: () => true, allowed: true, note: `no robots.txt (HTTP ${r.status})`, requested: true };
  const parser = robotsParser(robotsUrl, r.text);
  const isAllowed = (url) => parser.isAllowed(url, TOKEN) !== false;
  const allowed = isAllowed(pageUrl);
  return { isAllowed, allowed, note: allowed ? "robots.txt allows it" : "robots.txt disallows this page", requested: true };
}

export async function checkRobots(domain, pageUrl, opts = {}) {
  const { allowed, note, requested } = await loadRobots(domain, pageUrl, opts);
  return { allowed, note, requested };
}
