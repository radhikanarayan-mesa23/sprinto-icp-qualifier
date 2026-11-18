"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Nav({ human }) {
  const path = usePathname();
  const items = [
    { href: "/", label: "Review queue", count: human > 0 ? human : null, on: path === "/" || path.startsWith("/c/") },
    { href: "/run", label: "Run a domain", on: path === "/run" },
    { href: "/how", label: "How it works", on: path === "/how" },
  ];
  return (
    <nav className="nav" aria-label="Primary">
      {items.map((i) => (
        <Link key={i.href} href={i.href} aria-current={i.on ? "page" : undefined}>
          <span>{i.label}</span>
          {i.count ? <span className="count" title={`${i.count} companies need a human`}>{i.count}</span> : null}
        </Link>
      ))}
    </nav>
  );
}
