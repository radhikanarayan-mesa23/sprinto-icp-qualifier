import Link from "next/link";
export default function NotFound() {
  return (
    <div className="panel empty">
      <b>No company with that domain.</b>
      It may not have been run yet. <Link href="/">Back to the review queue</Link>
    </div>
  );
}
