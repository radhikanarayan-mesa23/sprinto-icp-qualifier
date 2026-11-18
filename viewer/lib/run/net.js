// SSRF-safe networking for the live run. A public page lets anyone type a domain, so every request must
// refuse private, loopback, link-local and metadata addresses, even if DNS or a redirect tries to point there.
import dns from "node:dns";
import net from "node:net";
import { Agent, fetch as undiciFetch } from "undici";

const BLOCKED_TLDS = new Set(["local", "localhost", "internal", "test", "example", "invalid", "onion", "lan", "home",
  "corp", "intranet", "arpa", "localdomain"]);

// ---- domain validation: a public registrable-looking hostname only
export function validateDomain(input) {
  const raw = String(input ?? "").trim().toLowerCase();
  if (!raw) return { ok: false, reason: "Enter a company domain, for example acme.com." };
  let d = raw.replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^www\./, "");
  if (/[\s@:?#\\]/.test(d)) return { ok: false, reason: "Enter just the domain, without a port, login or query (for example acme.com)." };
  if (net.isIP(d) || /^\[.*\]$/.test(d)) return { ok: false, reason: "IP addresses are not allowed. Enter a company domain." };
  if (d.length > 253 || !/^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(d)) {
    return { ok: false, reason: "That does not look like a public domain. Use a name like acme.com." };
  }
  const tld = d.split(".").pop();
  if (BLOCKED_TLDS.has(tld)) return { ok: false, reason: "Internal and reserved domains are not allowed." };
  return { ok: true, domain: d };
}

// ---- address classification
const blocked = new net.BlockList();
[["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16], ["172.16.0.0", 12],
  ["192.0.0.0", 24], ["192.0.2.0", 24], ["192.88.99.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15], ["198.51.100.0", 24],
  ["203.0.113.0", 24], ["224.0.0.0", 4], ["240.0.0.0", 4]].forEach(([a, p]) => blocked.addSubnet(a, p, "ipv4"));
[["::", 128], ["::1", 128], ["fc00::", 7], ["fe80::", 10], ["ff00::", 8], ["2001:db8::", 32], ["64:ff9b::", 96],
  ["2002::", 16], ["2001::", 32], ["100::", 64]].forEach(([a, p]) => blocked.addSubnet(a, p, "ipv6"));

export function isPublicIp(ip) {
  const fam = net.isIP(ip);
  if (!fam) return false;
  if (fam === 6) {
    // ::ffff:a.b.c.d and ::ffff:xxxx:xxxx are IPv4 addresses in disguise
    const m = ip.toLowerCase().match(/^(?:0*:)*:?ffff:(?:(\d+\.\d+\.\d+\.\d+)|([0-9a-f]{1,4}):([0-9a-f]{1,4}))$/);
    if (m) {
      const v4 = m[1] ?? `${parseInt(m[2], 16) >> 8}.${parseInt(m[2], 16) & 255}.${parseInt(m[3], 16) >> 8}.${parseInt(m[3], 16) & 255}`;
      return isPublicIp(v4);
    }
  }
  return !blocked.check(ip, fam === 4 ? "ipv4" : "ipv6");
}

export function guardedLookup(hostname, options, callback) {
  const opts = typeof options === "function" ? {} : options;
  const cb = typeof options === "function" ? options : callback;
  dns.lookup(hostname, { all: true, verbatim: true }, (err, addrs) => {
    if (err) return cb(err);
    const bad = addrs.find((a) => !isPublicIp(a.address));
    if (!addrs.length || bad) {
      const e = new Error("blocked_address");
      e.code = "BLOCKED_ADDRESS";
      return cb(e);
    }
    if (opts.all) return cb(null, addrs);
    return cb(null, addrs[0].address, addrs[0].family);
  });
}

const safeAgent = new Agent({ connect: { lookup: guardedLookup }, headersTimeout: 8000, bodyTimeout: 8000 });
const testAgent = new Agent({ headersTimeout: 8000, bodyTimeout: 8000 });

function decode(buf, contentType) {
  const cs = /charset=([\w-]+)/i.exec(contentType || "")?.[1] ?? "utf-8";
  try { return new TextDecoder(cs).decode(buf); } catch { return new TextDecoder("utf-8").decode(buf); }
}

/**
 * Fetch one page safely. Never throws: returns {error} for anything that is not a usable response.
 * opts: timeoutMs, maxBytes, maxRedirects, userAgent, baseDomain (redirects may only stay on it or www.),
 *       expectHtml, unsafeAllowPrivateHosts (tests only; the route never sets it).
 */
export async function safeFetch(url, opts = {}) {
  const { timeoutMs = 6000, maxBytes = 1_500_000, maxRedirects = 3, userAgent, baseDomain, expectHtml = true,
    unsafeAllowPrivateHosts = false } = opts;
  const dispatcher = unsafeAllowPrivateHosts ? testAgent : safeAgent;
  const hops = [];
  let current = url;
  const deadline = AbortSignal.timeout(timeoutMs);
  for (let i = 0; i <= maxRedirects; i += 1) {
    let u;
    try { u = new URL(current); } catch { return { error: "bad_url", hops }; }
    if (!unsafeAllowPrivateHosts && u.protocol !== "https:") return { error: "not_https", hops };
    // any IP literal (IPv6 hostnames keep their brackets, so strip them) or explicit port is refused outright
    if (!unsafeAllowPrivateHosts && (net.isIP(u.hostname.replace(/^\[|\]$/g, "")) || u.hostname.startsWith("[") || u.port)) {
      return { error: "blocked_address", hops };
    }
    if (baseDomain && i > 0 && ![baseDomain, `www.${baseDomain}`].includes(u.hostname)) {
      return { error: "redirect_off_domain", hops, finalUrl: current };
    }
    let res;
    try {
      res = await undiciFetch(current, {
        dispatcher, redirect: "manual", signal: deadline,
        headers: { "user-agent": userAgent ?? "SprintoICPQualifier/0.1", accept: "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8" },
      });
    } catch (e) {
      const code = e?.cause?.code || e?.code || e?.name;
      if (code === "BLOCKED_ADDRESS" || e?.cause?.message === "blocked_address") return { error: "blocked_address", hops };
      if (e?.name === "TimeoutError" || code === "UND_ERR_HEADERS_TIMEOUT" || code === "UND_ERR_BODY_TIMEOUT") return { error: "timeout", hops };
      return { error: `network:${String(code).slice(0, 40)}`, hops };
    }
    if ([301, 302, 303, 307, 308].includes(res.status)) {
      const loc = res.headers.get("location");
      await res.body?.cancel().catch(() => {});
      if (!loc) return { error: "bad_redirect", hops };
      hops.push({ from: current, status: res.status });
      current = new URL(loc, current).href;
      continue;
    }
    const contentType = res.headers.get("content-type") || "";
    if (expectHtml && res.status === 200 && contentType && !/html|xml|text\/plain/i.test(contentType)) {
      await res.body?.cancel().catch(() => {});
      return { error: "not_html", status: res.status, contentType, hops, finalUrl: current };
    }
    const chunks = [];
    let size = 0;
    let truncated = false;
    try {
      const reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) { truncated = true; await reader.cancel().catch(() => {}); break; }
        chunks.push(value);
      }
    } catch (e) {
      if (e?.name === "TimeoutError") return { error: "timeout", hops };
      return { error: "network:body", hops };
    }
    const buf = Buffer.concat(chunks.map((c) => Buffer.from(c)));
    return { status: res.status, contentType, text: decode(buf, contentType), bytes: size, truncated, finalUrl: current, hops };
  }
  return { error: "too_many_redirects", hops };
}
