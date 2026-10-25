"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";

type Emerging = { topic: string; recent_sources: number; baseline_mean: number };

export function emergingLine(e: Emerging): string {
  const usual = Math.round(e.baseline_mean);
  return usual > 0
    ? `${e.recent_sources} sources in the last 3 days (usually about ${usual} a week)`
    : `${e.recent_sources} sources in the last 3 days, new this month`;
}

// Emerging topics (#218): a burst of coverage across sources in the last 72 h
// against the 4-week baseline. Hidden when nothing is emerging.
export function EmergingTopics() {
  const [topics, setTopics] = useState<Emerging[]>([]);

  useEffect(() => {
    api.digest.emerging().then(setTopics).catch(() => setTopics([]));
  }, []);

  if (topics.length === 0) return null;
  return (
    <section
      aria-labelledby="emerging-heading"
      className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-900 dark:bg-amber-950/40"
    >
      <h2 id="emerging-heading" className="text-xs font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
        Emerging this week
      </h2>
      <ul className="mt-2 space-y-1 text-sm">
        {topics.map((t) => (
          <li key={t.topic}>
            <a
              href={`/search?q=${encodeURIComponent(t.topic)}`}
              className="font-medium text-stone-900 underline-offset-2 hover:underline dark:text-stone-100"
            >
              {t.topic}
            </a>{" "}
            <span className="text-stone-600 dark:text-stone-300">· {emergingLine(t)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
