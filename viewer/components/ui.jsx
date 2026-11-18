import { DECISIONS, source } from "@/lib/format";

export function Pill({ tone = "neutral", children }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function DecisionPill({ decision }) {
  const d = DECISIONS[decision] ?? { label: decision, tone: "neutral" };
  return <Pill tone={d.tone}>{d.label}</Pill>;
}

// Scraped URLs are data: only http(s) links are made clickable, text is escaped by React, new tabs get noopener.
export function Src({ url }) {
  const s = source(url);
  if (!s) return <span className="nullv">{"–"}</span>;
  if (!s.href) return <span className="src plain">{s.label}</span>;
  return s.internal
    ? <a className="src" href={s.href} target="_blank" rel="noopener">{s.label}</a>
    : <a className="src" href={s.href} target="_blank" rel="noopener noreferrer nofollow">{s.label}</a>;
}

export function Dash() {
  return <span className="nullv" aria-label="none">{"–"}</span>;
}

export function Mark() {
  // The product's mark: a funnel drawn as three shrinking bars (qualification narrows the list).
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <rect x="1" y="3" width="20" height="4" rx="2" fill="var(--blue-dark)" />
      <rect x="4" y="9" width="14" height="4" rx="2" fill="var(--blue-dark)" />
      <rect x="7.5" y="15" width="7" height="4" rx="2" fill="var(--blue-light)" />
    </svg>
  );
}
