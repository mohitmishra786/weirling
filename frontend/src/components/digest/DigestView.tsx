"use client";
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import type { Digest, DigestItem } from "../../lib/types";
import { DigestSection } from "./DigestSection";
import { api } from "../../lib/api";
import { EmergingTopics } from "./EmergingTopics";
import { VsNewestCard } from "./VsNewestCard";
import { Top5Question } from "./Top5Question";

const SECTION_ORDER = ["lead", "creator", "deep_reads", "more", "discovery"];

interface Prompt {
  id: string;
  prompt_text: string;
  prompt_type: string;
  answered: boolean;
  dismissed: boolean;
  answer: string | null;
}

function FeedbackPrompts({ digestId }: { digestId: string }) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  useEffect(() => {
    api.digest.prompts(digestId).then(setPrompts).catch(() => {});
  }, [digestId]);

  const submit = async (promptId: string) => {
    const answer = answers[promptId]?.trim();
    if (!answer) return;
    try {
      await api.digest.answerPrompt(digestId, promptId, answer);
      setPrompts((prev) =>
        prev.map((p) => (p.id === promptId ? { ...p, answered: true, answer } : p)),
      );
    } catch {}
  };

  const dismiss = async (promptId: string) => {
    try {
      await api.digest.dismissPrompt(digestId, promptId);
      // Only hide after the server confirms — a failed POST must not swallow
      // the prompt silently (CodeRabbit).
      setPrompts((prev) => prev.map((p) => (p.id === promptId ? { ...p, dismissed: true } : p)));
    } catch {}
  };

  const unanswered = prompts.filter((p) => !p.answered && !p.dismissed);
  if (unanswered.length === 0) return null;

  return (
    <div className="rounded-xl border border-prism-200 bg-prism-50 p-5 dark:border-prism-800 dark:bg-prism-900/30">
      <p className="mb-3 text-sm font-semibold text-prism-800 dark:text-prism-200">
        Help us calibrate your feed
      </p>
      {unanswered.map((p) => (
        <div key={p.id} className="mb-3 last:mb-0">
          <p className="mb-2 text-sm text-stone-700 dark:text-stone-200">{p.prompt_text}</p>
          <div className="flex gap-2">
            <input
              type="text"
              value={answers[p.id] || ""}
              onChange={(e) => setAnswers((prev) => ({ ...prev, [p.id]: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && submit(p.id)}
              placeholder="Your answer..."
              className="input flex-1"
            />
            <button onClick={() => submit(p.id)} className="btn-primary">
              Send
            </button>
            <button
              onClick={() => dismiss(p.id)}
              aria-label={`Dismiss prompt: ${p.prompt_text}`}
              className="text-stone-400 transition-colors hover:text-stone-700 dark:hover:text-white"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

interface DigestViewProps {
  digest: Digest;
  userCreatedAt?: string;
}

function FirstDigestBanner({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="mb-8 overflow-hidden rounded-xl border border-prism-200 bg-gradient-to-br from-prism-50 to-cyan-50 p-5 dark:border-prism-800 dark:from-prism-900/30 dark:to-transparent">
      <div className="flex items-start justify-between">
        <p className="mb-2 text-sm font-semibold text-prism-800 dark:text-prism-200">
          Your first digest
        </p>
        <button
          onClick={onDismiss}
          className="text-stone-400 transition-colors hover:text-stone-700 dark:hover:text-white"
          aria-label="Dismiss"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <p className="text-sm leading-relaxed text-prism-900/80 dark:text-stone-300">
        Items below are <strong>ranked by personal relevance</strong>, not chronological.
        The ranking learns from what you read fully, what you skip, and what you rate,
        so it gets noticeably sharper over the next few weeks. Tap{" "}
        <em>“Why this?”</em> on any item to see why it ranked where it did.
      </p>
    </div>
  );
}

export function DigestView({ digest, userCreatedAt }: DigestViewProps) {
  const [showFirstDigest, setShowFirstDigest] = useState(true);

  const grouped = SECTION_ORDER.reduce<Record<string, DigestItem[]>>((acc, s) => {
    acc[s] = digest.items.filter((i) => i.section === s);
    return acc;
  }, {});

  const [isNewUser, setIsNewUser] = useState(false);
  // Quiet-day catch-up (#142): the builder labels fallback items.
  const quietDay = digest.items.some((i) => Boolean(i.signal_breakdown?.fallback_reason));

  useEffect(() => {
    if (userCreatedAt) {
      queueMicrotask(() => {
        setIsNewUser(Date.now() - new Date(userCreatedAt).getTime() < 14 * 24 * 60 * 60 * 1000);
      });
    }
  }, [userCreatedAt]);

  return (
    <div className="animate-fade-in">
      <header className="mb-8 border-b border-stone-200 pb-6 dark:border-white/10">
        <div className="eyebrow text-prism-700 dark:text-prism-300">Your Daily Edition</div>
        <h1 className="mt-1 font-playfair text-4xl font-bold tracking-tight text-stone-900 dark:text-stone-100">
          Your Digest
        </h1>
        <p className="mt-2 text-sm text-stone-500 dark:text-stone-400">
          {new Date(digest.generated_at).toLocaleDateString("en-US", {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
          })}{" "}
          · {digest.total_items} items
        </p>
      </header>

      {/* Articles first on every screen; the extras sit beside them on wide
          screens and after them on phones, so a reader never scrolls past
          questions to reach today's reading. */}
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem] xl:gap-10">
        <div className="min-w-0">
          {isNewUser && showFirstDigest && (
            <FirstDigestBanner onDismiss={() => setShowFirstDigest(false)} />
          )}

          {quietDay && (
            <div
              role="status"
              className="mb-6 rounded-lg border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-stone-300"
            >
              Your sources were quiet today, so this edition catches you up on unread
              items from the last two weeks.
            </div>
          )}

          {SECTION_ORDER.map((section) => (
            <DigestSection key={section} name={section} items={grouped[section] || []} />
          ))}

          {digest.ask_top5 && <Top5Question digestId={digest.id} />}
        </div>

        <aside className="min-w-0 space-y-6 lg:sticky lg:top-24 lg:max-h-[calc(100vh-7rem)] lg:self-start lg:overflow-y-auto">
          <EmergingTopics />
          <VsNewestCard />
          <FeedbackPrompts digestId={digest.id} />
        </aside>
      </div>
    </div>
  );
}
