"use client";
export default function Error({ reset }) {
  return (
    <div className="panel empty">
      <b>The results database could not be reached.</b>
      The viewer reads from Postgres on the server and nothing was returned. This is a connection problem, not an empty queue.
      <p style={{ marginTop: 12 }}><button className="btn" onClick={() => reset()}>Try again</button></p>
    </div>
  );
}
