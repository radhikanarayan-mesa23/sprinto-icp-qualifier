// Server-only Postgres adapter for the live run. Same { query(text, params) -> rows } shape the tests use with PGlite.
import "server-only";
import { neon } from "@neondatabase/serverless";

export function liveDb() {
  const url = process.env.DATABASE_URL;
  if (!url) throw new Error("DATABASE_URL is not set on the server");
  const sql = neon(url);
  return { query: (text, params = []) => sql.query(text, params) };
}
