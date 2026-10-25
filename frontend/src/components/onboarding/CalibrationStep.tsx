"use client";
import { useEffect, useState } from "react";
import { ThumbsUp, ThumbsDown } from "lucide-react";
import { api, type CalibrationItem } from "../../lib/api";

const WANT = 8;
const WAIT_MS = 30_000; // the user's feeds may still be arriving
const POLL_MS = 3000;

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

// Rate real headlines from the user's new sources (#149). Each tap nudges
// the matching topic immediately; the step is always skippable and says so
// when the feeds have not produced anything to rate yet.
export function CalibrationStep() {
  const [items, setItems] = useState<CalibrationItem[] | null>(null);
  const [ratings, setRatings] = useState<Record<string, 1 | -1>>({});
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const started = Date.now();
    const load = async () => {
      const found = await api.onboarding.calibration(WANT).catch(() => []);
      if (cancelled) return;
      if (found.length >= WANT || Date.now() - started >= WAIT_MS) {
        setItems(found.slice(0, WANT));
        setGaveUp(found.length === 0);
        return;
      }
      if (found.length) setItems(found.slice(0, WANT));
      setTimeout(load, POLL_MS);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const rate = (id: string, rating: 1 | -1) => {
    setRatings((r) => ({ ...r, [id]: rating }));
    api.onboarding.rateCalibration(id, rating).catch(() => {});
  };

  if (gaveUp) {
    return (
      <p className="text-sm text-stone-500 dark:text-stone-400">
        Your sources haven&apos;t delivered articles yet, so there&apos;s nothing to rate.
        Skip this step: the first digest learns from what you read.
      </p>
    );
  }
  if (items === null || items.length === 0) {
    return <p className="text-sm text-stone-500 dark:text-stone-400">Fetching a few headlines from your sources…</p>;
  }
  const rated = Object.keys(ratings).length;

  return (
    <div>
      <p className="mb-3 text-sm text-stone-500 dark:text-stone-400">
        Quick taste test: would you read these? {rated}/{items.length} rated.{" "}
        <strong>Optional</strong>.
      </p>
      <ul className="space-y-2">
        {items.map((item) => (
          <li key={item.id} className="flex items-center gap-3 rounded-lg border border-stone-200 p-3 dark:border-white/10">
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{item.title}</p>
              <p className="text-xs text-stone-500 dark:text-stone-400">
                {hostOf(item.url)}
                {item.topic_clusters[0] ? ` · ${item.topic_clusters[0]}` : ""}
              </p>
            </div>
            <button
              type="button"
              aria-label={`More like ${item.title}`}
              aria-pressed={ratings[item.id] === 1}
              onClick={() => rate(item.id, 1)}
              className={`rounded px-2 py-1 ${ratings[item.id] === 1 ? "state-positive" : "hover:bg-stone-100 text-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"}`}
            >
              <ThumbsUp className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label={`Less like ${item.title}`}
              aria-pressed={ratings[item.id] === -1}
              onClick={() => rate(item.id, -1)}
              className={`rounded px-2 py-1 ${ratings[item.id] === -1 ? "state-negative" : "hover:bg-stone-100 text-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"}`}
            >
              <ThumbsDown className="h-4 w-4" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
