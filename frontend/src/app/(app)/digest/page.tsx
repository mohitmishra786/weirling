"use client";
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import type { Digest } from "../../../lib/types";
import { DigestView } from "../../../components/digest/DigestView";
import { OnboardingWizard } from "../../../components/onboarding/OnboardingWizard";
import { FirstDigestProgress } from "../../../components/onboarding/FirstDigestProgress";
import { LogoMark } from "../../../components/ui/Logo";

export default function DigestPage() {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [needsOnboarding, setNeedsOnboarding] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [userCreatedAt, setUserCreatedAt] = useState<string | undefined>(undefined);
  const [isNewUser, setIsNewUser] = useState(false);

  const loadDigest = async () => {
    setLoading(true);
    try {
      const user = await api.auth.me();
      setUserCreatedAt(user.created_at);
      const created = user.created_at ? new Date(user.created_at).getTime() : 0;
      setIsNewUser(created > 0 && Date.now() - created < 6 * 60 * 60 * 1000);
      if (!user.onboarding_complete) {
        setNeedsOnboarding(true);
        setLoading(false);
        return;
      }
      const d = await api.digest.latest();
      setDigest(d);
    } catch (err: unknown) {
      // 404 means no digest yet — that's a valid state, not an error.
      setError("");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    queueMicrotask(() => {
      void loadDigest();
    });
  }, []);

  const generate = async () => {
    setGenerating(true);
    setError("");
    try {
      await api.digest.generate();
      // Poll for the digest to appear (worker builds it async).
      for (let i = 0; i < 12; i++) {
        await new Promise((r) => setTimeout(r, 4000));
        try {
          const d = await api.digest.latest();
          if (d) {
            setDigest(d);
            setGenerating(false);
            return;
          }
        } catch {}
      }
      setGenerating(false);
      setError("Digest is still building; refresh in a moment.");
    } catch (err: unknown) {
      setGenerating(false);
      setError(err instanceof Error ? err.message : "Failed to generate digest");
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-48 rounded" />
        <div className="skeleton h-4 w-32 rounded" />
        <div className="mt-8 space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card p-5">
              <div className="skeleton mb-3 h-5 w-3/4 rounded" />
              <div className="skeleton mb-2 h-3 w-full rounded" />
              <div className="skeleton h-3 w-5/6 rounded" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (needsOnboarding) {
    return (
      <OnboardingWizard
        onDone={() => {
          setNeedsOnboarding(false);
          void loadDigest();
        }}
      />
    );
  }

  // First run (#102): a brand-new user's sources are still being fetched and
  // read. Show real progress and load the digest the moment it exists, instead
  // of an empty or near-random digest at the make-or-break moment.
  if (isNewUser && !digest && !generating) {
    return <FirstDigestProgress onReady={loadDigest} />;
  }

  if (!digest && !generating) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <div className="mb-4 text-white"><LogoMark size={44} /></div>
        <h2 className="font-playfair text-2xl font-bold text-stone-900 dark:text-stone-100">
          No digest yet
        </h2>
        <p className="mt-2 max-w-sm text-sm text-stone-500 dark:text-stone-400">
          Generate your first personalized digest. We’ll rank everything from your
          sources by personal relevance.
        </p>
        <button
          onClick={generate}
          disabled={generating}
          className="btn-primary mt-6 px-6 py-2.5"
        >
          Generate digest
        </button>
        {error && <p className="mt-4 text-sm text-red-600 dark:text-red-300">{error}</p>}
      </div>
    );
  }

  if (generating) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <div className="relative mb-6 h-12 w-12">
          <div className="absolute inset-0 animate-spin rounded-full border-2 border-stone-200 border-t-prism-600 dark:border-white/10" />
        </div>
        <h2 className="font-playfair text-xl font-semibold text-stone-900 dark:text-stone-100">
          Building your digest…
        </h2>
        <p className="mt-2 max-w-sm text-sm text-stone-500 dark:text-stone-400">
          Ranking items by personal relevance, deduplicating, and sectioning. This
          takes a few seconds.
        </p>
      </div>
    );
  }

  if (digest) return <DigestView digest={digest} userCreatedAt={userCreatedAt} />;
  return null;
}
