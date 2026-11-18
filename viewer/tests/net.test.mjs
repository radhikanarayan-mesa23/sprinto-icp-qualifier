import assert from "node:assert/strict";
import dns from "node:dns";
import http from "node:http";
import { test, mock } from "node:test";
import { guardedLookup, isPublicIp, safeFetch, validateDomain } from "../lib/run/net.js";
import { checkRobots } from "../lib/run/robots.js";

test("validateDomain accepts public domains and normalises pasted URLs", () => {
  for (const [input, want] of [["acme.com", "acme.com"], ["  ACME.com ", "acme.com"], ["https://www.acme.com/about?x=1", "acme.com"],
    ["sub.acme.co.uk", "sub.acme.co.uk"], ["xn--bcher-kva.example.org", "xn--bcher-kva.example.org"]]) {
    assert.deepEqual(validateDomain(input), { ok: true, domain: want }, input);
  }
});

test("validateDomain refuses IPs, ports, logins, internal and reserved names", () => {
  for (const bad of ["", "localhost", "127.0.0.1", "10.0.0.5", "[::1]", "169.254.169.254", "acme.com:8080", "user@acme.com", "acme", "a b.com",
    "intranet.local", "service.internal", "foo.test", "foo.localhost", "evil.com#x@127.0.0.1", "a".repeat(64) + ".com", "x".repeat(250) + ".com", "ex ample.com", "javascript:alert(1)"]) {
    assert.equal(validateDomain(bad).ok, false, `should refuse ${JSON.stringify(bad)}`);
  }
});

test("isPublicIp blocks every private, loopback, link-local, metadata and embedded-IPv4 range", () => {
  const blocked = ["127.0.0.1", "127.255.255.254", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "255.255.255.255", "198.18.0.1", "::1", "::", "fc00::1", "fd12:3456::1", "fe80::1", "ff02::1", "2001:db8::1", "::ffff:127.0.0.1",
    "::ffff:7f00:1", "::ffff:10.0.0.1", "::ffff:a9fe:a9fe", "64:ff9b::7f00:1", "2002:7f00:1::1", "not-an-ip", ""];
  for (const ip of blocked) assert.equal(isPublicIp(ip), false, ip);
  for (const ip of ["93.184.216.34", "8.8.8.8", "1.1.1.1", "172.15.0.1", "172.32.0.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]) assert.equal(isPublicIp(ip), true, ip);
});

test("the connect-time lookup rejects a hostname if ANY of its addresses is private (DNS rebinding)", async () => {
  const run = (addrs) => new Promise((resolve) => {
    mock.method(dns, "lookup", (h, o, cb) => cb(null, addrs));
    guardedLookup("evil.example", { all: true }, (err, out) => { mock.restoreAll(); resolve({ err, out }); });
  });
  assert.equal((await run([{ address: "93.184.216.34", family: 4 }, { address: "127.0.0.1", family: 4 }])).err.code, "BLOCKED_ADDRESS");
  assert.equal((await run([{ address: "::ffff:169.254.169.254", family: 6 }])).err.code, "BLOCKED_ADDRESS");
  const ok = await run([{ address: "93.184.216.34", family: 4 }]);
  assert.equal(ok.err, null);
  assert.equal(ok.out[0].address, "93.184.216.34");
});

test("real requests to loopback, private and metadata targets are refused before any connection", async () => {
  for (const url of ["https://127.0.0.1/", "https://localhost/", "https://169.254.169.254/latest/meta-data/", "https://[::1]/", "http://example.com/", "https://acme.com:8443/"]) {
    const r = await safeFetch(url, { timeoutMs: 3000 });
    assert.ok(["blocked_address", "not_https"].includes(r.error), `${url} -> ${JSON.stringify(r.error)}`);
  }
});

async function withServer(routes, fn) {
  const srv = http.createServer((req, res) => (routes[req.url.split("?")[0]] ?? ((_, r) => { r.writeHead(404); r.end("nf"); }))(req, res));
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  try { await fn(base); } finally { srv.closeAllConnections?.(); srv.close(); }
}
const T = { unsafeAllowPrivateHosts: true }; // tests only: lets us talk to our own local server

test("safeFetch: redirects stay on the same site, off-site and loops stop, size and type are capped, slow servers time out", async () => {
  await withServer({
    "/ok": (q, s) => { s.writeHead(200, { "content-type": "text/html; charset=utf-8" }); s.end("<p>hello world</p>"); },
    "/same": (q, s) => { s.writeHead(302, { location: "/ok" }); s.end(); },
    "/off": (q, s) => { s.writeHead(302, { location: `http://localhost:${q.socket.localPort}/ok` }); s.end(); },
    "/loop": (q, s) => { s.writeHead(302, { location: "/loop" }); s.end(); },
    "/big": (q, s) => { s.writeHead(200, { "content-type": "text/html" }); s.end("x".repeat(200000)); },
    "/json": (q, s) => { s.writeHead(200, { "content-type": "application/json" }); s.end("{}"); },
    "/slow": (q, s) => { setTimeout(() => { s.writeHead(200); s.end("late"); }, 1500); },
  }, async (base) => {
    const host = "127.0.0.1";
    const ok = await safeFetch(`${base}/ok`, { ...T, baseDomain: host });
    assert.equal(ok.status, 200); assert.match(ok.text, /hello world/);
    const same = await safeFetch(`${base}/same`, { ...T, baseDomain: host });
    assert.equal(same.status, 200); assert.equal(same.hops.length, 1);
    assert.equal((await safeFetch(`${base}/off`, { ...T, baseDomain: host })).error, "redirect_off_domain");
    assert.equal((await safeFetch(`${base}/loop`, { ...T, baseDomain: host })).error, "too_many_redirects");
    const big = await safeFetch(`${base}/big`, { ...T, maxBytes: 50000 });
    assert.equal(big.truncated, true); assert.ok(big.bytes <= 50000 + 70000);
    assert.equal((await safeFetch(`${base}/json`, T)).error, "not_html");
    assert.equal((await safeFetch(`${base}/slow`, { ...T, timeoutMs: 300 })).error, "timeout");
    assert.match((await safeFetch("http://127.0.0.1:1/", { ...T, timeoutMs: 1000 })).error, /^network:/);
  });
});

test("robots.txt: disallow blocks, allow passes, missing or unreachable means allowed", async () => {
  await withServer({
    "/closed/robots.txt": (q, s) => { s.writeHead(200, { "content-type": "text/plain" }); s.end("User-agent: *\nDisallow: /\n"); },
    "/open/robots.txt": (q, s) => { s.writeHead(200, { "content-type": "text/plain" }); s.end("User-agent: *\nDisallow: /private\n"); },
    "/bot/robots.txt": (q, s) => { s.writeHead(200, { "content-type": "text/plain" }); s.end("User-agent: SprintoICPQualifier\nDisallow: /\n\nUser-agent: *\nAllow: /\n"); },
  }, async (base) => {
    const c = (p) => checkRobots("x.com", `${base}/${p}/`, { ...T, userAgent: "SprintoICPQualifier/0.1" });
    assert.equal((await c("closed")).allowed, false);
    assert.equal((await c("open")).allowed, true);
    assert.equal((await c("bot")).allowed, false, "a rule naming our bot applies");
    assert.equal((await c("missing")).allowed, true);
  });
});
