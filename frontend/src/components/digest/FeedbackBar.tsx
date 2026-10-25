"use client";
import { useEffect, useState } from "react";
import { ThumbsUp, ThumbsDown, Check } from "lucide-react";
import { api } from "../../lib/api";

interface FeedbackBarProps {
  contentItemId: string;
  /** Item topic labels; enables the More/Less like-this quick actions (UX-06). */
  topics?: string[];
  onFeedback?: () => void;
}

const REASONS = [
  { value: "too_basic", label: "Too basic" },
  { value: "already_knew", label: "Already knew this" },
  { value: "off_topic", label: "Off-topic" },
  { value: "wrong_depth", label: "Wrong depth" },
  { value: "clickbait", label: "Clickbait" },
];

export function FeedbackBar({ contentItemId, topics, onFeedback }: FeedbackBarProps) {
  const [rated, setRated] = useState<number | null>(null);
  const [showReasons, setShowReasons] = useState(false);
  const [saved, setSaved] = useState(false);
  const [quickAdjusted, setQuickAdjusted] = useState<string | null>(null);

  const topTopic = topics && topics.length > 0 ? topics[0] : null;

  const quickAdjust = async (action: "boost" | "suppress") => {
    if (!topTopic) return;
    setQuickAdjusted(action);
    await api.feedback.adjustInterests(topTopic, action);
  };

  useEffect(() => {
    api.feedback
      .getInteraction(contentItemId)
      .then((interaction) => {
        if (interaction) {
          setSaved(interaction.saved);
          if (interaction.explicit_rating !== null) setRated(interaction.explicit_rating);
        }
      })
      .catch(() => {});
  }, [contentItemId]);

  const rate = async (rating: number) => {
    setRated(rating);
    if (rating === -1) setShowReasons(true);
    await api.feedback.interaction({ content_item_id: contentItemId, explicit_rating: rating });
    onFeedback?.();
  };

  const rateWithReason = async (reason: string) => {
    setShowReasons(false);
    await api.feedback.interaction({
      content_item_id: contentItemId,
      explicit_rating: -1,
      explicit_rating_reason: reason,
    });
  };

  const save = async () => {
    setSaved(true);
    await api.feedback.interaction({ content_item_id: contentItemId, saved: true });
  };

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => rate(1)}
        title="Helpful"
        aria-label="Helpful"
        className={`flex h-8 w-8 items-center justify-center rounded-md border text-sm transition-colors ${
          rated === 1
            ? "state-positive"
            : "border-stone-200 text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
        }`}
      >
        <ThumbsUp className="h-4 w-4" aria-hidden="true" />
      </button>
      <button
        onClick={() => rate(-1)}
        title="Not helpful"
        aria-label="Not helpful"
        className={`flex h-8 w-8 items-center justify-center rounded-md border text-sm transition-colors ${
          rated === -1
            ? "state-negative"
            : "border-stone-200 text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
        }`}
      >
        <ThumbsDown className="h-4 w-4" aria-hidden="true" />
      </button>
      <button
        onClick={save}
        title="Save for later"
        aria-label="Save for later"
        className={`flex h-8 items-center gap-1 rounded-md border px-2.5 text-xs font-medium transition-colors ${
          saved
            ? "state-accent"
            : "border-stone-200 text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
        }`}
      >
        {saved ? (
          <>
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Saved</span>
          </>
        ) : (
          "Save"
        )}
      </button>

      {topTopic && (
        <>
          <button
            onClick={() => quickAdjust("boost")}
            title={`More like this (${topTopic})`}
            className={`flex h-8 items-center gap-1 rounded-md border px-2.5 text-xs font-medium transition-colors ${
              quickAdjusted === "boost"
                ? "state-positive"
                : "border-stone-200 text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
            }`}
          >
            More like this
          </button>
          <button
            onClick={() => quickAdjust("suppress")}
            title={`Less like this (${topTopic})`}
            className={`flex h-8 items-center gap-1 rounded-md border px-2.5 text-xs font-medium transition-colors ${
              quickAdjusted === "suppress"
                ? "state-negative"
                : "border-stone-200 text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
            }`}
          >
            Less like this
          </button>
        </>
      )}

      {showReasons && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-stone-500 dark:text-stone-400">Why?</span>
          {REASONS.map((r) => (
            <button
              key={r.value}
              onClick={() => rateWithReason(r.value)}
              className="rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-xs transition-colors hover:border-stone-300 dark:border-white/10 dark:bg-white/[0.03] dark:hover:border-white/30"
            >
              {r.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
