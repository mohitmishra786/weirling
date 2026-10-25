"use client";
import { useState } from "react";
import type { Source } from "../../lib/types";
import { api } from "../../lib/api";
import { NewsletterSenderHelp } from "./NewsletterSenderHelp";

// Reddit ends RSS on this date (#107); the backend pauses its feeds then.
const REDDIT_RSS_END = Date.UTC(2026, 10, 13);

function isRedditUrl(url: string | null | undefined): boolean {
  try {
    const host = new URL(url ?? "").hostname.toLowerCase().replace(/^www\./, "");
    return host === "reddit.com" || host.endsWith(".reddit.com");
  } catch {
    return false;
  }
}

interface SourceListProps {
  sources: Source[];
  onUpdated: (source: Source) => void;
  onDeleted: (id: string) => void;
}

export function SourceList({ sources, onUpdated, onDeleted }: SourceListProps) {
  // Read the clock once per mount, not on every render (react-hooks/purity).
  const [redditStillLive] = useState(() => Date.now() < REDDIT_RSS_END);
  const toggle = async (source: Source) => {
    const updated = await api.sources.update(source.id, { is_active: !source.is_active });
    onUpdated(updated);
  };

  const [fixNote, setFixNote] = useState<Record<string, string>>({});
  const fix = async (source: Source) => {
    setFixNote((n) => ({ ...n, [source.id]: "Looking for this site's feed…" }));
    try {
      const result = await api.sources.fix(source.id);
      if (result.status === "fixed") {
        setFixNote((n) => ({ ...n, [source.id]: `Fixed: now following ${result.feed_url}` }));
        const refreshed = (await api.sources.list()).find((x) => x.id === source.id);
        if (refreshed) onUpdated(refreshed);
      } else {
        setFixNote((n) => ({
          ...n,
          [source.id]: "No working feed found on this site. You can remove the source.",
        }));
      }
    } catch {
      setFixNote((n) => ({ ...n, [source.id]: "Couldn't check the site right now. Try again later." }));
    }
  };

  const del = async (id: string) => {
    if (!confirm("Remove this source?")) return;
    await api.sources.delete(id);
    onDeleted(id);
  };

  if (sources.length === 0) {
    return <p className="text-sm text-stone-500 dark:text-stone-400">No sources added yet.</p>;
  }

  return (
    <div className="space-y-2.5">
      {sources.map((s) => (
        <div
          key={s.id}
          className={`card p-4 ${s.is_active ? "" : "opacity-60"}`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium text-stone-900 dark:text-stone-100">
                  {s.name || s.url}
                </span>
                {s.health !== "healthy" && (
                  <span
                    title={
                      s.health === "platform_outage"
                        ? "YouTube's feed service is having an outage. Weirling keeps retrying; nothing to fix on your side."
                        : s.last_error || "This source is having trouble updating."
                    }
                    className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      s.health === "degraded" || s.health === "platform_outage"
                        ? "state-warning border"
                        : "state-negative border"
                    }`}
                  >
                    {s.health === "degraded"
                      ? "Fetch issues"
                      : s.health === "platform_outage"
                        ? "Platform outage"
                        : s.health === "failing"
                          ? "Not updating"
                          : "Turned off"}
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
                <span className="font-mono uppercase tracking-wide">{s.source_type}</span>
                {" · "}
                {s.last_fetched_at
                  ? `Last success ${new Date(s.last_fetched_at).toLocaleDateString()}`
                  : "No successful fetch yet"}
                {s.last_error_at ? ` · Last error ${new Date(s.last_error_at).toLocaleDateString()}` : ""}
                {" · "}
                {s.items_per_week} this week
                {" · "}
                Relevance {Math.round(s.trust_weight * 100)}%
              </div>
              {redditStillLive && isRedditUrl(s.feed_url || s.url) && (
                <div className="mt-1.5 text-xs text-amber-800 bg-amber-50 rounded px-2 py-1 dark:text-amber-200 dark:bg-amber-950/40">
                  Reddit stops serving RSS on 13 Nov 2026. This source keeps updating until
                  then and pauses after. Consider a blog, Substack, Bluesky or Mastodon feed instead.
                </div>
              )}
              {s.last_error && s.health !== "healthy" && (
                <div className="mt-1.5 text-xs text-amber-800 bg-amber-50 rounded px-2 py-1 dark:text-amber-200 dark:bg-amber-950/40">
                  {s.last_error}
                </div>
              )}
              <NewsletterSenderHelp source={s} />
              {(s.health === "dead" || s.health === "failing") && s.source_type !== "newsletter" && (
                <div className="mt-1.5 flex items-center gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => fix(s)}
                    className="rounded border border-stone-300 px-2 py-0.5 font-medium text-stone-700 hover:bg-stone-100 dark:border-white/20 dark:text-stone-200 dark:hover:bg-white/10"
                  >
                    Fix this feed
                  </button>
                  {fixNote[s.id] && <span className="text-stone-500 dark:text-stone-400">{fixNote[s.id]}</span>}
                </div>
              )}
              {/* Trust-weight bar */}
              <div className="mt-2.5 h-1 w-24 overflow-hidden rounded-full bg-stone-200 dark:bg-white/10">
                <div
                  className="reading-progress h-full rounded-full"
                  style={{ width: `${s.trust_weight * 100}%` }}
                />
              </div>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button
                onClick={() => toggle(s)}
                className="btn-ghost border border-stone-200 px-2.5 py-1 text-xs dark:border-white/10"
              >
                {s.is_active ? "Pause" : "Resume"}
              </button>
              <button
                onClick={() => del(s.id)}
                className="border border-rose-200 px-2.5 py-1 text-xs text-rose-600 transition-colors hover:bg-rose-50 dark:border-rose-900 dark:text-rose-300"
              >
                Remove
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
