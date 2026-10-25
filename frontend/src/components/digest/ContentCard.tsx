"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { ContentItem } from "../../lib/types";
import { topicFromWhy, whySegments } from "../../lib/why";
import { FeedbackBar } from "./FeedbackBar";
import { SummaryLabel } from "../SummaryLabel";

// Section accent colors — the prism splits content into ranked bands.
const SECTION_ACCENTS: Record<string, string> = {
  lead: "from-prism-600 to-prism-400",
  creator: "from-spectrum-violet to-prism-500",
  deep_reads: "from-spectrum-cyan to-prism-500",
  discovery: "from-spectrum-amber to-spectrum-rose",
};

interface ContentCardProps {
  content: ContentItem;
  prsScore?: number | null;
  signalBreakdown?: Record<string, unknown>;
  isDiscovery?: boolean;
  section?: string;
  position?: number;
}

export function ContentCard({
  content,
  prsScore,
  signalBreakdown,
  isDiscovery,
  section,
}: ContentCardProps) {
  const router = useRouter();
  const [showWhy, setShowWhy] = useState(false);
  const whyRef = useRef<HTMLDivElement | null>(null);
  const whyButton = useRef<HTMLButtonElement | null>(null);
  // The explanation is a popover: a click anywhere else or Escape closes it,
  // so it never sits over the next item.
  useEffect(() => {
    if (!showWhy) return;
    const onDown = (e: PointerEvent) => {
      if (whyRef.current && !whyRef.current.contains(e.target as Node)) setShowWhy(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Focus inside the popover would vanish with it: hand it back to the
      // button so keyboard users keep their place.
      if (whyRef.current?.contains(document.activeElement)) whyButton.current?.focus();
      setShowWhy(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [showWhy]);
  const [summaryLevel, setSummaryLevel] = useState<"brief" | "detailed">("brief");

  const discoveredVia =
    typeof signalBreakdown?.discovered_via === "string" ? signalBreakdown.discovered_via : null;

  // Explainability (#185): each signal's share of this item's score, from the
  // contributions the ranker stored (they sum to the score).
  const breakdown = signalBreakdown as Record<string, unknown> | undefined;
  const whyTopics = typeof breakdown?.why_topics === "string" ? breakdown.why_topics : null;
  const segments = whySegments(breakdown, topicFromWhy(whyTopics));
  const explanation = typeof breakdown?.explanation === "string" ? breakdown.explanation : null;
  const whySummary = explanation || whyTopics;

  const accent = section ? SECTION_ACCENTS[section] : "from-prism-600 to-prism-400";

  const handleRead = (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    router.push(`/read/${content.id}`);
  };

  const hasSummary = !!(content.summary_brief || content.summary_detailed);

  return (
    <article className="card card-hover group relative overflow-hidden p-5">
      {/* Left accent stripe — visual marker for the section/rank band */}
      <div
        className={`absolute left-0 top-0 h-full w-1 bg-gradient-to-b ${accent}`}
        aria-hidden
      />

      {typeof signalBreakdown?.fallback_reason === "string" && (
        <span className="mb-3 mr-2 inline-flex items-center gap-1.5 rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-600 ring-1 ring-stone-200 dark:bg-white/[0.06] dark:text-stone-300">
          Catch-up · from your feeds this fortnight
        </span>
      )}
      {isDiscovery && (
        <span className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 ring-1 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          {discoveredVia
            ? `Outside your usual sources (found via ${discoveredVia})`
            : "Outside your usual sources (based on your interests)"}
        </span>
      )}

      <div className="flex items-start justify-between gap-3">
        <h3 className="flex-1 font-serif text-lg font-semibold leading-snug">
          <a
            href={`/read/${content.id}`}
            onClick={handleRead}
            className="text-stone-900 transition-colors hover:text-prism-700 dark:text-stone-100 dark:hover:text-prism-200"
          >
            {content.title}
          </a>
        </h3>
        {prsScore != null && (
          <span
            className="shrink-0 font-mono text-xs font-medium text-stone-400 dark:text-stone-500"
            title="Personalized Relevance Score"
          >
            {prsScore.toFixed(2)}
          </span>
        )}
      </div>

      {/* Metadata row */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-500 dark:text-stone-400">
        {content.paywalled && (
          <span className="rounded-full bg-stone-200 px-2 py-0.5 font-medium text-stone-700 dark:bg-stone-800 dark:text-stone-200">
            Paywalled
          </span>
        )}
        {content.author && (
          <span className="font-medium text-stone-600 dark:text-stone-300">{content.author}</span>
        )}
        {content.reading_time_minutes && (
          <span>{content.reading_time_minutes} min read</span>
        )}
        {content.published_at && (
          <span>{new Date(content.published_at).toLocaleDateString()}</span>
        )}
      </div>

      {/* Summary */}
      {content.summary_headline && (
        <p className="mt-3 font-serif text-sm italic text-stone-500 dark:text-stone-400">
          {content.summary_headline}
        </p>
      )}

      {summaryLevel === "brief" && content.summary_brief && (
        <p className="mt-2 text-sm leading-relaxed text-stone-700 dark:text-stone-300">
          <SummaryLabel source={content.summary_source} fromTranscript={Boolean(content.transcript_url)} />
          {content.summary_brief}
        </p>
      )}
      {summaryLevel === "detailed" && (content.summary_detailed || content.summary_brief) && (
        <p className="mt-2 text-[0.95rem] leading-relaxed text-stone-700 dark:text-stone-300">
          <SummaryLabel source={content.summary_source} fromTranscript={Boolean(content.transcript_url)} />
          {content.summary_detailed || content.summary_brief}
        </p>
      )}

      {hasSummary && (
        <button
          onClick={() =>
            setSummaryLevel(summaryLevel === "brief" ? "detailed" : "brief")
          }
          className="mt-2 text-xs font-medium text-stone-500 underline-offset-2 transition-colors hover:text-prism-600 hover:underline dark:text-stone-400 dark:hover:text-prism-200"
        >
          {summaryLevel === "brief" ? "Show detailed takeaway" : "Show brief summary"}
        </button>
      )}

      {/* Footer: why-this + feedback */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-stone-100 pt-3 dark:border-stone-800">
        {whySummary || segments.length ? (
          <div className="relative" ref={whyRef}>
            <button
              type="button"
              ref={whyButton}
              onClick={() => setShowWhy((v) => !v)}
              aria-expanded={showWhy}
              aria-controls={`why-${content.id}`}
              className="inline-flex items-center gap-1.5 rounded-md border border-stone-200 px-2 py-1 text-xs text-stone-500 transition-colors hover:border-stone-300 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:border-white/30"
            >
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-gradient-to-r from-prism-500 to-spectrum-violet" />
              Why this?
            </button>
            {showWhy && (
              <div
                id={`why-${content.id}`}
                role="region"
                aria-label="Why this is ranked here"
                className="absolute bottom-full left-0 z-20 mb-2 w-72 max-w-[calc(100vw-3rem)] rounded-lg bg-stone-900 p-3 text-xs text-white shadow-xl"
              >
                <div className="mb-2 font-semibold">Why this is ranked here</div>
                {whySummary && <p className="mb-2 text-stone-100">{whySummary}</p>}
                {segments.length > 0 && (
                  <>
                    {/* One bar, 100% = this item's score. */}
                    <div className="mb-2 flex h-2 w-full overflow-hidden rounded-full bg-white/10" aria-hidden>
                      {segments.map((s, i) => (
                        <span
                          key={s.key}
                          className={s.noData ? "bg-white/25" : ["bg-prism-400", "bg-spectrum-violet", "bg-spectrum-cyan", "bg-stone-400"][i] ?? "bg-stone-400"}
                          style={{ width: `${s.pct}%` }}
                        />
                      ))}
                    </div>
                    <ul aria-label="Share of this item's score">
                      {segments.map((s) => (
                        <li key={s.key} className="flex justify-between gap-3 py-0.5">
                          <span className={s.noData ? "opacity-70" : ""}>
                            {s.key === "semantic" ? (
                              <a href="/interests" className="underline decoration-white/40 underline-offset-2 hover:decoration-white">
                                {s.label}
                              </a>
                            ) : (
                              s.label
                            )}
                          </span>
                          <span className="tabular-nums opacity-90">{s.pct}%</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>
        ) : (
          <span />
        )}
        <FeedbackBar contentItemId={content.id} topics={content.topic_clusters} />
      </div>
    </article>
  );
}
