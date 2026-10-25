"use client";
import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  GraduationCap,
  Bot,
  Dna,
  Sprout,
  Link,
  Database,
  Palette,
  Cloud,
  Gamepad2,
  Plug,
  Smartphone,
  Wrench,
  Code,
  TrendingUp,
  Cpu,
  Microscope,
  Lock,
  Rocket,
  Briefcase,
  Globe,
  FileText,
  Landmark,
  FileSpreadsheet,
  BookOpen,
} from "lucide-react";
import { api } from "../../lib/api";
import type { StarterPack } from "../../lib/types";

const ICONS: Record<string, LucideIcon> = {
  "academic-computing": GraduationCap,
  "ai-machine-learning": Bot,
  "biotech-health": Dna,
  "climate-energy": Sprout,
  "crypto-web3": Link,
  "databases-data": Database,
  "design-ux": Palette,
  "devops-cloud": Cloud,
  gamedev: Gamepad2,
  "hardware-electronics": Plug,
  "mobile-development": Smartphone,
  "open-source-devtools": Wrench,
  "programming-languages": Code,
  "quant-finance": TrendingUp,
  "robotics-automation": Cpu,
  "science-research": Microscope,
  "security-privacy": Lock,
  "space-astronomy": Rocket,
  "startups-business": Briefcase,
  "systems-programming": Cpu,
  "web-development": Globe,
  "research-papers": FileText,
  "policy-research": Landmark,
  "filings-markets": FileSpreadsheet,
};

/** Subscribe to the selected packs; returns how many new feeds were added.
 *  One request for all of them: one at a time made five packs a minute-long
 *  wait, and parallel requests would race on feeds the packs share. Throws on
 *  failure so the caller can keep the selection and say what went wrong. */
export async function subscribePacks(ids: string[]): Promise<number> {
  if (ids.length === 0) return 0;
  const result = await api.sources.subscribePacks(ids);
  return result.subscribed;
}

export function packSummary(packs: StarterPack[], selected: string[]): string {
  const chosen = packs.filter((p) => selected.includes(p.id));
  const feeds = chosen.reduce((n, p) => n + p.feed_count, 0);
  return `${chosen.length} pack${chosen.length === 1 ? "" : "s"} · ${feeds} sources`;
}

// Grid of starter packs with multi-select (#152). Controlled: the parent owns
// the selection so the onboarding wizard can subscribe when the user moves on.
export function StarterPackPicker({
  topics = [],
  selected,
  onChange,
  footer,
}: {
  topics?: string[];
  selected: string[];
  onChange: (ids: string[]) => void;
  footer?: React.ReactNode;
}) {
  const [packs, setPacks] = useState<StarterPack[] | null>(null);
  const topicKey = topics.join(",");

  useEffect(() => {
    let cancelled = false;
    api.sources
      .starterPacks(topicKey ? topicKey.split(",") : [])
      .then((list) => {
        if (cancelled) return;
        setPacks(list);
        // Pre-select packs that match the user's topics, once.
        const suggested = list.filter((p) => p.suggested && p.subscribed === 0).map((p) => p.id);
        if (suggested.length && selected.length === 0) onChange(suggested);
      })
      .catch(() => setPacks([]));
    return () => {
      cancelled = true;
    };
    // Load once per topic set; selection changes must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topicKey]);

  if (packs === null) return <p className="text-sm text-stone-500 dark:text-stone-400">Loading starter packs…</p>;
  if (packs.length === 0) return <p className="text-sm text-stone-500 dark:text-stone-400">No starter packs available.</p>;

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {packs.map((p) => {
          const on = selected.includes(p.id);
          const following = p.subscribed > 0 && p.subscribed >= p.feed_count;
          return (
            <button
              key={p.id}
              type="button"
              aria-pressed={on}
              disabled={following}
              onClick={() => toggle(p.id)}
              className="choice"
            >
              <div className="flex items-center gap-2 font-medium">
                {(() => {
                  const Icon = ICONS[p.id] ?? BookOpen;
                  return <Icon
                      className={`h-4 w-4 shrink-0 ${on ? "text-prism-300" : "text-stone-400"}`}
                      aria-hidden="true"
                    />;
                })()}
                {p.title}
                {p.suggested && !following && (
                  <span className="badge-accent ml-auto shrink-0">For you</span>
                )}
              </div>
              <p className="mt-1 text-xs text-stone-400">
                {following
                  ? "Already following"
                  : `${p.feed_count} sources · ${p.feeds.slice(0, 3).map((f) => f.name).join(", ")}`}
              </p>
            </button>
          );
        })}
      </div>
      <div className="sticky bottom-0 mt-4 flex items-center justify-between border-t border-[var(--border)] bg-[var(--bg)]/95 py-3 text-sm text-[var(--text-secondary)] backdrop-blur">
        <span>{packSummary(packs, selected)}</span>
        {footer}
      </div>
    </div>
  );
}
