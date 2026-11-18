import Link from "next/link";
export default function NotFound() {
  return (
    <main style={{ padding: "64px 24px", maxWidth: 560, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22 }}>Page not found</h1>
      <p className="muted" style={{ marginTop: 8 }}>That address does not exist. <Link href="/">Back to the review queue</Link></p>
    </main>
  );
}
