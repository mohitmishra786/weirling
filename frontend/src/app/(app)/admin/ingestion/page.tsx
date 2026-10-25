"use client";

import { useEffect, useState } from "react";
import { api, type IngestionReport } from "../../../../lib/api";

const METHOD_LABEL: Record<string, string> = {
  feed: "From the feed",
  trafilatura: "Article extractor",
  readability: "Readability",
  playwright: "Rendered page",
  browserless: "Rendered page",
  newsletter: "Newsletter",
  failed: "Failed",
  unknown: "Not yet extracted",
};

function pct(rate: number) {
  return `${(rate * 100).toFixed(rate >= 0.995 || rate === 0 ? 0 : 1)}%`;
}

function duration(seconds: number | null) {
  if (seconds == null) return "No data yet";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function tone(rate: number) {
  return rate >= 0.9 ? "state-positive" : rate >= 0.7 ? "state-warning" : "state-negative";
}

function Stat({ label, value, hint, badge }: { label: string; value: string; hint?: string; badge?: string }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="eyebrow">{label}</p>
        {badge && <span className={`chip border text-[10px] ${badge}`}>{badge.includes("positive") ? "Good" : badge.includes("warning") ? "Watch" : "Low"}</span>}
      </div>
      <p className="mt-2 font-mono text-2xl text-[var(--text)]">{value}</p>
      {hint && <p className="mt-1 text-xs text-[var(--text-tertiary)]">{hint}</p>}
    </div>
  );
}

export default function IngestionAdminPage() {
  const [report, setReport] = useState<IngestionReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.metrics
      .ingestion()
      .then((body) => !cancelled && setReport(body))
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const methods = report
    ? Object.entries(report.extraction_by_method).sort(([, a], [, b]) => b - a)
    : [];
  const methodTotal = methods.reduce((n, [, c]) => n + c, 0);

  return (
    <section>
      <p className="eyebrow">Instance</p>
      <h1 className="mt-1 text-3xl italic">Ingestion</h1>
      <p className="mt-2 text-sm text-[var(--text-secondary)]">
        Is fetching healthy, how articles were extracted, and how long new items wait to be ranked.
        Visible to the instance owner only.
      </p>

      {error && (
        <p role="alert" className="card mt-6 p-4 text-sm text-[var(--text-secondary)]">
          Couldn&apos;t load ingestion health: {error}.
        </p>
      )}
      {!report && !error && (
        <div className="mt-6 grid gap-3 sm:grid-cols-2" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-24 rounded-xl" />
          ))}
        </div>
      )}

      {report && (
        <>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <Stat label="Active sources" value={report.sources.toLocaleString()} />
            <Stat
              label="Fetch success"
              value={pct(report.fetch_success_rate)}
              hint="Sources whose last fetch worked or only partly failed."
              badge={tone(report.fetch_success_rate)}
            />
            <Stat
              label="Extraction success"
              value={pct(report.extraction_success_rate)}
              hint="Items with readable text."
              badge={tone(report.extraction_success_rate)}
            />
            <Stat
              label="Fetched to ranked (median)"
              value={duration(report.ingest_to_scored_p50_seconds)}
              hint={
                report.ingest_to_scored_samples
                  ? `Across ${report.ingest_to_scored_samples.toLocaleString()} items.`
                  : undefined
              }
            />
          </div>

          <div className="card mt-3 p-4">
            <p className="eyebrow">How articles were extracted</p>
            {methods.length === 0 ? (
              <p className="mt-2 text-sm text-[var(--text-tertiary)]">No items yet.</p>
            ) : (
              <ul className="mt-3 space-y-2.5">
                {methods.map(([method, count]) => (
                  <li key={method}>
                    <div className="flex justify-between text-sm">
                      <span>{METHOD_LABEL[method] ?? method}</span>
                      <span className="font-mono text-[var(--text-secondary)]">
                        {count.toLocaleString()} · {pct(count / methodTotal)}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--bg-sunken)]">
                      <div
                        className={`h-full rounded-full ${method === "failed" ? "bg-rose-400/70" : "bg-[var(--accent)]"}`}
                        style={{ width: `${Math.max(2, (count / methodTotal) * 100)}%` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {report.storage && (
            <p className="mt-3 text-xs text-[var(--text-tertiary)]">
              {report.storage.items.toLocaleString()} items stored ·{" "}
              {(report.storage.full_text_chars / 1_000_000).toFixed(1)} M characters of article text
              (pruned to an excerpt after the retention period).
            </p>
          )}
        </>
      )}
    </section>
  );
}
