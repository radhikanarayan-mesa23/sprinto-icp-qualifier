import Link from "next/link";

export const metadata = { title: "How it works" };

const LANES = ["Owner", "Cache", "Scraper", "Loop", "Qualify", "Hunter", "Writer", "Store"];
const COLS = ["Trigger", "Input", "Context", "Processing", "AI", "Output"];
const X0 = 96, CW = 150, Y0 = 34, LH = 58, BW = 124, BH = 40;
const cx = (c) => X0 + CW * c + CW / 2;
const cy = (l) => Y0 + LH * l + LH / 2;

// kind: human | code | ai | cache | queue
const BOX = [
  { n: "1", t: "Domains", s: "+ ICP rules", c: 0, l: 0, kind: "human" },
  { n: "2", t: "Cache?", s: "skip if fresh", c: 1, l: 1, kind: "cache" },
  { n: "3", t: "Fetch", s: "plain first", c: 1, l: 2, kind: "code" },
  { n: "4", t: "Clean", s: "to fields", c: 2, l: 2, kind: "code" },
  { n: "5", t: "Check", s: "code decides", c: 3, l: 2, kind: "code" },
  { n: "6", t: "More", s: "max 4 pages", c: 1, l: 3, kind: "code" },
  { n: "7", t: "Qualify", s: "go / no / flag", c: 4, l: 4, kind: "ai" },
  { n: "", t: "Queue", s: "no / review", c: 5, l: 4, kind: "queue" },
  { n: "8", t: "Find contact", s: "go + flagged", c: 3, l: 5, kind: "code" },
  { n: "9", t: "Draft", s: "pains + hook", c: 4, l: 6, kind: "ai" },
  { n: "10", t: "Save", s: "source + why", c: 5, l: 7, kind: "code" },
  { n: "11", t: "Review", s: "you approve", c: 5, l: 0, kind: "human" },
];
const FILL = { human: "#141414", code: "#e4e7ec", ai: "#e8f0fb", cache: "#fffbeb", queue: "#ffedea" };
const INK = { human: "#ffffff", code: "#0a0a0a", ai: "#1d5fbf", cache: "#92400e", queue: "#831c1e" };

function Arrow({ d, color = "#141414", dash }) {
  return <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeDasharray={dash} markerEnd={`url(#a-${color.slice(1)})`} />;
}

export default function Page() {
  const W = X0 + CW * 6 + 8, H = Y0 + LH * 8 + 8;
  const L = (c, l) => ({ x: cx(c) - BW / 2, y: cy(l) - BH / 2, r: cx(c) + BW / 2, b: cy(l) + BH / 2, mx: cx(c), my: cy(l) });
  const b = (i) => L(BOX[i].c, BOX[i].l);
  return (
    <div className="how">
      <div className="titlerow">
        <div>
          <h1>How it works</h1>
          <p className="sub">One run per company. Code decides what to fetch and whether the evidence is enough. The model only reads and judges what was found. Nothing is sent.</p>
        </div>
        <Link className="btn" href="/">Review queue</Link>
      </div>

      <section className="sec">
        <h2>Components map</h2>
        <div className="panel mapwrap" style={{ padding: 16 }}>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby="map-t map-d" fontFamily="var(--font-geist), system-ui, sans-serif">
            <title id="map-t">Components map of the ICP Qualifier</title>
            <desc id="map-d">Domains and ICP rules go to a cache check, then fetch, clean and check. Missing evidence loops back for at most four more pages. Checked evidence goes to qualify; no go and review go to a queue; go goes to find contact, then draft, then save, then human review.</desc>
            <defs>
              {["141414", "92400e", "831c1e", "2e7d32"].map((c) => (
                <marker key={c} id={`a-${c}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0 0 L8 4 L0 8 z" fill={`#${c}`} />
                </marker>
              ))}
            </defs>
            {COLS.map((c, i) => <text key={c} x={cx(i)} y="16" textAnchor="middle" fontSize="12" fill="#5f6672">{c}</text>)}
            {LANES.map((l, i) => (
              <g key={l}>
                <text x="0" y={cy(i) + 4} fontSize="12" fill="#5f6672">{l}</text>
                <line x1="0" x2={W} y1={Y0 + LH * i} y2={Y0 + LH * i} stroke="#e3e6ea" />
              </g>
            ))}

            {/* main run */}
            <Arrow d={`M${b(0).mx} ${b(0).b} V${b(1).my} H${b(1).x}`} />
            <Arrow d={`M${b(1).mx} ${b(1).b} V${b(2).y}`} />
            <Arrow d={`M${b(2).r} ${b(2).my} H${b(3).x}`} />
            <Arrow d={`M${b(3).r} ${b(3).my} H${b(4).x}`} />
            <Arrow d={`M${b(4).mx} ${b(4).b} V${b(6).my} H${b(6).x}`} />
            <Arrow d={`M${b(6).mx} ${b(6).b} V${b(8).my} H${b(8).r}`} />
            <Arrow d={`M${b(8).mx} ${b(8).b} V${b(9).my} H${b(9).x}`} />
            <Arrow d={`M${b(9).r} ${b(9).my} H${b(10).mx} V${b(10).y}`} />
            {/* loop: missing fields */}
            <Arrow color="#92400e" dash="5 4" d={`M${b(4).x + 20} ${b(4).b} V${b(5).my} H${b(5).r}`} />
            <Arrow color="#92400e" dash="5 4" d={`M${b(5).mx} ${b(5).y} V${b(5).y - 10} H${b(3).mx} V${b(3).b}`} />
            {/* no go / review */}
            <Arrow color="#831c1e" dash="4 4" d={`M${b(6).r} ${b(6).my} H${b(7).x}`} />
            {/* output to review */}
            <Arrow color="#2e7d32" dash="5 4" d={`M${b(10).r} ${b(10).my} H${W - 6} V${b(11).my} H${b(11).r}`} />

            {BOX.map((x) => {
              const p = L(x.c, x.l);
              return (
                <g key={x.t}>
                  <rect x={p.x} y={p.y} width={BW} height={BH} rx="7" fill={FILL[x.kind]} />
                  <text x={p.mx} y={p.y + 17} textAnchor="middle" fontSize="12.5" fontWeight="600" fill={INK[x.kind]}>{x.n ? `${x.n} ` : ""}{x.t}</text>
                  <text x={p.mx} y={p.y + 32} textAnchor="middle" fontSize="11" fill={INK[x.kind]} opacity="0.85">{x.s}</text>
                </g>
              );
            })}
          </svg>
          <ul className="small muted" style={{ listStyle: "none", display: "flex", flexWrap: "wrap", gap: "6px 20px", margin: "8px 0 0", padding: 0 }}>
            <li>Solid line: main run</li><li>Amber dashed: loop for missing fields</li><li>Red dashed: no go or review</li>
            <li>Green dashed: output</li><li>Grey boxes: code</li><li>Blue boxes: AI</li><li>Black boxes: you</li>
          </ul>
        </div>
      </section>

      <section className="sec">
        <h2>The rules it will not break</h2>
        <div className="rules">
          <div className="card"><h3>Hard parameters stop the run</h3><p>B2B SaaS and not-already-certified are hard rows. Either one failing ends the run for that company. If a hard row cannot be answered, it goes to a human. It is never guessed.</p></div>
          <div className="card"><h3>The loop is capped at four fetches</h3><p>Missing evidence sends the agent to the next page on that row&rsquo;s fixed list, never a link it found. After four pages beyond the homepage it applies the row&rsquo;s fallback and stops. It never loops on model confidence.</p></div>
          <div className="card"><h3>It fails loudly</h3><p>A 404, a CAPTCHA, a login wall or a page that is too thin is logged with its reason and queued for a human, and the run continues. A flag is a correct outcome. No result is ever silently empty.</p></div>
          <div className="card"><h3>A human reviews before anything is sent</h3><p>It writes drafts only. Every email is addressed to a named person and rests on a scraped fact that passes a provenance check in code. The Growth Lead approves every draft.</p></div>
          <div className="card"><h3>Scraped text is data, never instructions</h3><p>Page text reaches the model inside delimited blocks. Text addressed to an AI is logged and ignored, and nothing on a page can change a URL, a rule or a cap.</p></div>
          <div className="card"><h3>Spend is capped and shared</h3><p>A daily model-call budget, a monthly Hunter search cap and a cache by domain apply to every run, whether it comes from the command line or a live run here.</p></div>
        </div>
      </section>
    </div>
  );
}
