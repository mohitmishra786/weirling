"use client";
import { useEffect, useMemo, useState } from "react";
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
  Network,
  Search,
} from "lucide-react";
import { api } from "../../lib/api";
import type { StarterPack } from "../../lib/types";

const ICONS: Record<string, LucideIcon> = {
  "academic-computing": GraduationCap,
  "ai-machine-learning": Bot,
  "backend-distributed": Network,
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

/** Packs matching `query` (title, scope, or any feed's name), with the feeds
 *  that matched so the card can say why it is shown. Empty query: all packs,
 *  the ones suggested for the reader first. */
export function searchPacks(
  packs: StarterPack[],
  query: string,
): { pack: StarterPack; matchedFeeds: string[] }[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const ordered = [...packs].sort((a, b) => Number(b.suggested) - Number(a.suggested));
  if (words.length === 0) return ordered.map((pack) => ({ pack, matchedFeeds: [] }));
  const hit = (text: string) => words.every((w) => text.toLowerCase().includes(w));
  const out: { pack: StarterPack; matchedFeeds: string[] }[] = [];
  for (const pack of ordered) {
    const matchedFeeds = pack.feeds.map((f) => f.name).filter(hit);
    if (hit(`${pack.title} ${pack.description ?? ""} ${pack.id}`) || matchedFeeds.length) {
      out.push({ pack, matchedFeeds });
    }
  }
  return out;
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
  const [query, setQuery] = useState("");
  const topicKey = topics.join(",");
  const shown = useMemo(() => searchPacks(packs ?? [], query), [packs, query]);

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
      <label className="relative mb-3 block">
        <span className="sr-only">Search starter packs</span>
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400"
          aria-hidden="true"
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search packs or sources, e.g. kubernetes, Dan Luu"
          className="w-full pl-9"
        />
      </label>
      {shown.length === 0 && (
        <p className="py-6 text-center text-sm text-stone-500 dark:text-stone-400" role="status">
          No pack matches “{query}”. You can add any site or feed on the Sources page.
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {shown.map(({ pack: p, matchedFeeds }) => {
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
              {p.description && (
                <p className="mt-1 text-xs text-[var(--text-secondary)]">{p.description}</p>
              )}
              <p className="mt-1 text-xs text-stone-400">
                {following
                  ? "Already following"
                  : `${p.feed_count} sources · ${(matchedFeeds.length
                      ? matchedFeeds
                      : p.feeds.map((f) => f.name)
                    )
                      .slice(0, 3)
                      .join(", ")}`}
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
