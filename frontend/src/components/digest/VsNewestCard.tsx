"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";

type Result = Awaited<ReturnType<typeof api.digest.vsNewest>>;

export function winLine(i: NonNullable<Result["interleaving"]>): string | null {
  const decided = i.prs_wins + i.newest_wins;
  if (decided === 0) return null;
  return `In ${decided} head-to-head digest${decided === 1 ? "" : "s"} this week, your ranking won ${i.prs_wins} and newest-first won ${i.newest_wins}.`;
}

// Day-7 proof of value (#237): articles you finished that a newest-first
// feed would have buried, from your own last 7 days. Hidden until there are
// real examples.
export function VsNewestCard() {
  const [result, setResult] = useState<Result | null>(null);
  useEffect(() => {
    api.digest.vsNewest().then(setResult).catch(() => setResult(null));
  }, []);
  if (!result?.available) return null;
  const line = result.interleaving ? winLine(result.interleaving) : null;
  return (
    <section aria-labelledby="vs-newest-heading" className="rounded-lg border border-stone-200 px-4 py-3 dark:border-stone-700">
      <h2 id="vs-newest-heading" className="text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-stone-400">
        Your digest vs newest-first
      </h2>
      <p className="mt-1 text-sm text-stone-600 dark:text-stone-300">
        Articles you finished this week that a newest-first feed would have buried:
      </p>
      <ul className="mt-2 space-y-1 text-sm">
        {result.examples.map((e) => (
          <li key={e.content_id}>
            <a href={`/read/${e.content_id}`} className="font-medium underline-offset-2 hover:underline">{e.title}</a>{" "}
            <span className="text-stone-500 dark:text-stone-400">
              · #{e.your_position} in your digest, #{e.newest_first_position} by date
            </span>
          </li>
        ))}
      </ul>
      {line && <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">{line}</p>}
    </section>
  );
}
