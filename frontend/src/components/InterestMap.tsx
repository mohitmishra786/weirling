"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { ForceGraph, type ForceGraphHandle } from "./graph/ForceGraph";
import type { InterestGraph, InterestGraphNode } from "../lib/types";

const RECENT_DAYS = 7;

export function isRecent(node: InterestGraphNode, now = Date.now()): boolean {
  return Boolean(node.last_reinforced_at && now - Date.parse(node.last_reinforced_at) < RECENT_DAYS * 86_400_000);
}

export function isMuted(node: InterestGraphNode, now = Date.now()): boolean {
  return Boolean(node.suppressed_until && Date.parse(node.suppressed_until) > now);
}

function download(name: string, type: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// The interest map (#179): every topic Weirling inferred, sized by weight,
// linked when read together. Drag a topic onto another to merge them;
// select one to rename, boost, mute or remove it. The list beside the map
// offers the same actions for keyboard and screen-reader users.
export function InterestMap() {
  const [graph, setGraph] = useState<InterestGraph | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [rename, setRename] = useState("");
  const [mergeInto, setMergeInto] = useState("");
  const [note, setNote] = useState("");
  const graphRef = useRef<ForceGraphHandle | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Below the two-column layout the edit panel sits under the graph: bring it
  // into view after every tap (also on the topic already selected), or the
  // tap looks like it did nothing. No animation for reduced motion.
  const [revealTick, setRevealTick] = useState(0);
  useEffect(() => {
    if (!selected || window.matchMedia?.("(min-width: 1024px)").matches) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    panelRef.current?.scrollIntoView?.({ behavior: still ? "auto" : "smooth", block: "nearest" });
  }, [selected, revealTick]);
  const [now] = useState(() => Date.now());

  const load = useCallback(() => {
    api.preferences.interestGraph(300).then(setGraph).catch(() => setGraph({ nodes: [], edges: [] }));
  }, []);
  useEffect(load, [load]);

  const nodes = useMemo(
    () =>
      (graph?.nodes ?? []).map((n) => ({
        id: n.label,
        label: n.label,
        weight: n.weight,
        core: n.is_core,
        recent: isRecent(n, now),
        muted: isMuted(n, now),
      })),
    [graph, now],
  );
  const links = useMemo(
    () => (graph?.edges ?? []).map((e) => ({ source: e.from_label, target: e.to_label, weight: e.weight })),
    [graph],
  );

  const act = async (label: string, run: () => Promise<unknown>, done: string) => {
    try {
      await run();
      setNote(done);
      setSelected(null);
      load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : `Couldn't update ${label}.`);
    }
  };
  const boost = (l: string) => act(l, () => api.feedback.adjustInterests(l, "boost"), `Boosted “${l}”.`);
  const mute = (l: string, days: number) =>
    act(l, () => api.feedback.adjustInterests(l, "suppress", days), `Muted “${l}” for ${days} days.`);
  const remove = (l: string) => act(l, () => api.feedback.adjustInterests(l, "remove"), `Removed “${l}”.`);
  const doRename = (l: string, to: string) =>
    to.trim() && to.trim() !== l
      ? act(l, () => api.feedback.renameInterest(l, to.trim()), `Renamed “${l}” to “${to.trim()}”.`)
      : undefined;
  const doMerge = (from: string, into: string) =>
    from !== into && window.confirm(`Merge “${from}” into “${into}”?`)
      ? act(from, () => api.feedback.mergeInterests(from, into), `Merged “${from}” into “${into}”.`)
      : undefined;
  const choose = (label: string) => {
    setSelected(label);
    setRevealTick((n) => n + 1);
    setRename(label);
    setMergeInto("");
  };

  if (!graph) return <div className="skeleton h-[460px] rounded-xl" aria-busy="true" aria-label="Loading your interests" />;
  if (graph.nodes.length === 0)
    return (
      <p className="card p-4 text-sm text-[var(--text-secondary)]">
        No interests yet. They appear as you read and rate articles.
      </p>
    );

  const current = graph.nodes.find((n) => n.label === selected) ?? null;

  return (
    // minmax(0, 1fr): the graph column may shrink below its canvas's width,
    // so the side panel always stays on screen.
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0">
        <ForceGraph
          ref={graphRef}
          nodes={nodes}
          links={links}
          selected={selected}
          onSelect={choose}
          onDropOnto={(from, into) => void doMerge(from, into)}
          height={520}
          label={`Map of ${graph.nodes.length} interests; the list beside it has the same topics and actions`}
        />
        <p className="mt-2 text-xs text-[var(--text-tertiary)]">
          Bigger = stronger interest. <span className="text-prism-300">Orange</span> = core interest.{" "}
          <span className="text-amber-300">Ring</span> = reinforced in the last week. Grey = muted.{" "}
          <span className="[@media(pointer:coarse)]:hidden">
            Hover a topic to see what it connects to; drag one onto another to merge them.
          </span>
          <span className="hidden [@media(pointer:coarse)]:inline">Tap a topic to see its links and edit it.</span>
        </p>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            onClick={() => download("weirling-interests.json", "application/json", JSON.stringify(graph, null, 2))}
          >
            Export JSON
          </button>
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            onClick={() => {
              const svg = graphRef.current?.toSvg();
              if (svg) download("weirling-interests.svg", "image/svg+xml", svg);
            }}
          >
            Export image (SVG)
          </button>
        </div>
      </div>

      <aside className="min-w-0 text-sm">
        {note && (
          <p role="status" className="mb-3 text-[var(--text-secondary)]">
            {note}
          </p>
        )}
        {current && (
          <div ref={panelRef} className="card mb-4 p-3" aria-label={`Edit ${current.label}`} role="group">
            <p className="font-medium">{current.label}</p>
            <p className="text-xs text-[var(--text-tertiary)]">
              Weight {Math.round(current.weight * 100)}% · reinforced {current.reinforcement_count ?? 0} times
              {isMuted(current, now) ? " · muted" : ""}
            </p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => boost(current.label)}>
                Boost
              </button>
              <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => mute(current.label, 7)}>
                Mute 7 days
              </button>
              <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => mute(current.label, 30)}>
                Mute 30 days
              </button>
              <button
                type="button"
                className="btn-secondary state-negative border px-2.5 py-1 text-xs"
                onClick={() => remove(current.label)}
              >
                Remove
              </button>
            </div>
            <form
              className="mt-3 flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                void doRename(current.label, rename);
              }}
            >
              <label className="sr-only" htmlFor="rename-topic">
                New name
              </label>
              <input
                id="rename-topic"
                className="input min-w-0 flex-1 px-2.5 py-1.5"
                value={rename}
                onChange={(e) => setRename(e.target.value)}
                placeholder="Rename to…"
              />
              <button type="submit" className="btn-secondary px-2.5 py-1 text-xs">
                Rename
              </button>
            </form>
            <form
              className="mt-2 flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                if (mergeInto) void doMerge(current.label, mergeInto);
              }}
            >
              <label className="sr-only" htmlFor="merge-into">
                Merge into
              </label>
              <select
                id="merge-into"
                className="input min-w-0 flex-1 py-1.5"
                value={mergeInto}
                onChange={(e) => setMergeInto(e.target.value)}
              >
                <option value="">Merge into…</option>
                {graph.nodes
                  .filter((n) => n.label !== current.label)
                  .map((n) => (
                    <option key={n.label} value={n.label}>
                      {n.label}
                    </option>
                  ))}
              </select>
              <button type="submit" className="btn-secondary px-2.5 py-1 text-xs">
                Merge
              </button>
            </form>
          </div>
        )}
        <h2 className="eyebrow mb-1.5">All topics</h2>
        <ul className="max-h-[22rem] space-y-0.5 overflow-auto lg:max-h-[30rem]">
          {graph.nodes.map((n) => (
            <li key={n.label}>
              <button
                type="button"
                aria-pressed={selected === n.label}
                onClick={() => choose(n.label)}
                className="flex w-full justify-between gap-2 rounded-md px-2 py-1 text-left hover:bg-[var(--bg-elevated)] aria-pressed:bg-[var(--accent-soft)]"
              >
                <span className={isMuted(n, now) ? "text-[var(--text-tertiary)] line-through" : ""}>
                  {n.label}
                  {isRecent(n, now) ? " •" : ""}
                </span>
                <span className="tabular-nums text-[var(--text-tertiary)]">{Math.round(n.weight * 100)}%</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
