"use client";
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import type { Source } from "../../../lib/types";
import { AddSourceForm } from "../../../components/sources/AddSourceForm";
import { SourceList } from "../../../components/sources/SourceList";
import { StarterPackPicker, subscribePacks } from "../../../components/sources/StarterPackPicker";

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [showPacks, setShowPacks] = useState(false);
  const [packIds, setPackIds] = useState<string[]>([]);
  const [packNote, setPackNote] = useState("");

  const subscribeSelected = async () => {
    setPackNote("Subscribing…");
    try {
      const added = await subscribePacks(packIds);
      setPackNote(`Added ${added} new source${added === 1 ? "" : "s"}.`);
      setPackIds([]);
      setSources(await api.sources.list());
    } catch (err: unknown) {
      // Keep the selection so the user can retry.
      setPackNote(`Couldn't follow those packs${err instanceof Error && err.message ? `: ${err.message}` : ""}.`);
    }
  };

  useEffect(() => {
    api.sources.list().then((s) => { setSources(s); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  return (
    <div>
      <header className="mb-6">
        <div className="eyebrow text-prism-700 dark:text-prism-300">Ingestion</div>
        <h1 className="mt-1 font-playfair text-3xl font-bold tracking-tight">Sources</h1>
        <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
          Websites, blogs, and feeds you follow. RSS is detected automatically.
        </p>
      </header>
      <AddSourceForm onAdded={(s) => setSources((prev) => [s, ...prev])} />
      {/* Starter packs are reachable after onboarding too (#152). */}
      <section className="mb-6">
        <button
          type="button"
          onClick={() => setShowPacks((v) => !v)}
          className="text-sm font-medium text-prism-700 underline dark:text-prism-300"
        >
          {showPacks ? "Hide starter packs" : "Browse starter packs"}
        </button>
        {showPacks && (
          <div className="mt-3">
            <StarterPackPicker
              selected={packIds}
              onChange={setPackIds}
              footer={
                <button
                  type="button"
                  disabled={packIds.length === 0}
                  onClick={subscribeSelected}
                  className="btn-primary disabled:opacity-50"
                >
                  Subscribe
                </button>
              }
            />
            {packNote && <p className="mt-2 text-sm text-stone-500 dark:text-stone-400">{packNote}</p>}
          </div>
        )}
      </section>
      {loading ? (
        <div className="space-y-2.5">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card p-4">
              <div className="skeleton mb-2 h-4 w-2/3 rounded" />
              <div className="skeleton h-3 w-1/3 rounded" />
            </div>
          ))}
        </div>
      ) : (
        <SourceList
          sources={sources}
          onUpdated={(s) => setSources((prev) => prev.map((x) => (x.id === s.id ? s : x)))}
          onDeleted={(id) => setSources((prev) => prev.filter((x) => x.id !== id))}
        />
      )}
    </div>
  );
}
