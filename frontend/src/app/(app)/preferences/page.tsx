"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, AlertTriangle, Brain, Pencil } from "lucide-react";
import { api } from "../../../lib/api";
import { PlanSection } from "../../../components/PlanSection";
import { LlmKeySection } from "../../../components/LlmKeySection";
import { NewsletterAddressCard } from "../../../components/NewsletterAddressCard";
import { CitationExport } from "../../../components/CitationExport";
import { PushAlerts } from "../../../components/PushAlerts";
import { ForceGraph } from "../../../components/graph/ForceGraph";
import type { InterestGraph, User } from "../../../lib/types";

function Section({ title, children, hint }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="card p-5 sm:p-6">
      <h2 className="text-xl italic">{title}</h2>
      {hint && <p className="mt-1 text-sm text-[var(--text-tertiary)]">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Field({ label, htmlFor, hint, children }: { label: React.ReactNode; htmlFor?: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1.5 text-xs text-[var(--text-tertiary)]">{hint}</p>}
    </div>
  );
}

function LlmStatus() {
  const [status, setStatus] = useState<{
    llm_configured: boolean;
    model_primary: string;
    model_fast: string;
    openai_fallback_enabled: boolean;
    embedding_model?: string;
    embedding_is_hash?: boolean;
    embedding_quality_note?: string;
    embedding_upgrade_hint?: string | null;
  } | null>(null);

  useEffect(() => {
    api.metrics.llmStatus().then(setStatus).catch(() => {});
  }, []);

  if (!status) return null;
  return (
    <div className="mt-5 space-y-1.5 rounded-lg bg-[var(--bg-sunken)] px-3 py-2.5 text-xs text-[var(--text-tertiary)]" role="status">
      <p className="flex items-start gap-2">
        {status.llm_configured ? (
          <Check className="mt-px h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden="true" />
        ) : (
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-400" aria-hidden="true" />
        )}
        <span>
          Summaries: {status.llm_configured ? "AI summaries on" : "extract summaries (no AI key)"} ·{" "}
          {status.model_primary} · fast: {status.model_fast}
          {status.openai_fallback_enabled ? " · OpenAI fallback on" : ""}
        </span>
      </p>
      {status.embedding_model && (
        <p className="flex items-start gap-2">
          {status.embedding_is_hash ? (
            <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-400" aria-hidden="true" />
          ) : (
            <Brain className="mt-px h-3.5 w-3.5 shrink-0 text-prism-400" aria-hidden="true" />
          )}
          <span>
            Embeddings: {status.embedding_model}
            {status.embedding_quality_note ? ` · ${status.embedding_quality_note}` : ""}
          </span>
        </p>
      )}
      {status.embedding_is_hash && status.embedding_upgrade_hint && (
        <p className="text-amber-300">{status.embedding_upgrade_hint}</p>
      )}
    </div>
  );
}

export default function PreferencesPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [graph, setGraph] = useState<InterestGraph | null>(null);
  const [now] = useState(() => Date.now());
  const graphNodes = useMemo(
    () =>
      (graph?.nodes ?? []).slice(0, 60).map((n) => ({
        id: n.label,
        label: n.label,
        weight: n.weight,
        core: n.is_core,
        muted: Boolean(n.suppressed_until && Date.parse(n.suppressed_until) > now),
      })),
    [graph, now],
  );
  const graphLinks = useMemo(
    () =>
      (graph?.edges ?? [])
        .filter((e) => e.weight > 0.1)
        .map((e) => ({ source: e.from_label, target: e.to_label, weight: e.weight })),
    [graph],
  );
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [busy, setBusy] = useState(false);

  const exportData = async () => {
    setBusy(true);
    try {
      const data = await api.account.export();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "weirling-export.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch {}
    setBusy(false);
  };

  const suppressTopic = async (label: string) => {
    if (!confirm(`Mute "${label}" for 30 days? It will gradually resurface after.`)) return;
    try {
      await api.feedback.adjustInterests(label, "suppress", 30);
      const g = await api.preferences.interestGraph();
      setGraph(g);
    } catch {}
  };

  const boostTopic = async (label: string) => {
    try {
      await api.feedback.adjustInterests(label, "boost");
      const g = await api.preferences.interestGraph();
      setGraph(g);
    } catch {}
  };

  const renameInterest = async (label: string) => {
    const next = prompt(`Rename "${label}" to:`, label);
    if (!next || next.trim() === label) return;
    try {
      await api.feedback.renameInterest(label, next.trim());
      const g = await api.preferences.interestGraph();
      setGraph(g);
    } catch {}
  };

  const mergeInterest = async (label: string) => {
    const into = prompt(`Merge "${label}" into which topic?`, "");
    if (!into || !into.trim()) return;
    try {
      await api.feedback.mergeInterests(label, into.trim());
      const g = await api.preferences.interestGraph();
      setGraph(g);
    } catch {}
  };

  const deleteAccount = async () => {
    if (
      !confirm(
        "Permanently delete your account and all your data? This cannot be undone."
      )
    )
      return;
    setBusy(true);
    try {
      await api.account.delete();
      await api.auth.logout().catch(() => {});
      router.replace("/register");
    } catch {
      setBusy(false);
    }
  };

  const [initial, setInitial] = useState<User | null>(null);
  // Languages kept as raw comma text while editing; parsed on save so a
  // trailing comma survives typing (CodeRabbit).
  const [languagesText, setLanguagesText] = useState("");

  useEffect(() => {
    api.preferences.get().then((u) => {
      setUser(u);
      setInitial(u);
      setLanguagesText((u.preferred_languages || []).join(", "));
    }).catch(() => {});
    api.preferences.interestGraph().then(setGraph).catch(() => {});
  }, []);

  const save = async () => {
    if (!user) return;
    setSaving(true);
    try {
      // Only send fields the user actually changed: an explicit digest_max_items
      // or digest_time_morning LOCKS the respective learner server-side (UX-02/
      // UX-03), so echoing unchanged values on every save would lock by accident.
      const payload: Partial<User> & { preferred_languages?: string[] } = {
        digest_frequency: user.digest_frequency,
        serendipity_percentage: user.serendipity_percentage,
        timezone: user.timezone,
      };
      if (initial && user.digest_max_items !== initial.digest_max_items) {
        payload.digest_max_items = user.digest_max_items;
      }
      if (initial && user.digest_time_morning !== initial.digest_time_morning) {
        payload.digest_time_morning = user.digest_time_morning;
      }
      if (initial && languagesText !== (initial.preferred_languages || []).join(", ")) {
        payload.preferred_languages = languagesText
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      }
      setSaveError("");
      setSaved(false);
      const updated = await api.preferences.update(payload);
      setUser(updated);
      setInitial(updated);
      setLanguagesText((updated.preferred_languages || []).join(", "));
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      // e.g. "Twice-daily digests are part of the Pro plan." (#246)
      setSaveError(e instanceof Error ? e.message : "Couldn't save your preferences.");
    }
    setSaving(false);
  };

  if (!user)
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading preferences">
        <div className="skeleton h-10 w-48 rounded-lg" />
        <div className="skeleton h-80 rounded-xl" />
      </div>
    );

  return (
    <div className="space-y-6">
      <header>
        <p className="eyebrow">Your account</p>
        <h1 className="mt-1 text-3xl italic">Preferences</h1>
      </header>

      {/* Two columns on wide screens: settings you change often on the
          left, mail, plan and keys on the right; the graph spans both. */}
      <div className="grid gap-6 xl:grid-cols-2 xl:items-start">
        <div className="min-w-0 space-y-6">
          <Section title="Digest">
            <div className="grid gap-5">
              <Field
                label="Frequency"
                htmlFor="frequency-input"
                hint="Lighter reader? Weekly bundles the best of your week; “email off” stops digest emails while keeping your in-app digest."
              >
                <select
                  id="frequency-input"
                  className="input"
                  value={user.digest_frequency}
                  onChange={(e) => setUser({ ...user, digest_frequency: e.target.value })}
                >
                  <option value="daily">Daily</option>
                  <option value="twice_daily">Twice daily</option>
                  <option value="weekly">Weekly (we’ll keep it short)</option>
                  <option value="in_app_only">Email off (read in-app only)</option>
                </select>
              </Field>
              <Field
                label={
                  <span className="flex justify-between">
                    <span>Max items per digest</span>
                    <span className="font-mono text-[var(--text-secondary)]">{user.digest_max_items}</span>
                  </span>
                }
                htmlFor="max-items-input"
                hint={
                  user.digest_length_locked
                    ? "Locked to your choice."
                    : "Auto: adapts to how much you actually open. Moving the slider locks it."
                }
              >
                <input
                  id="max-items-input"
                  type="range"
                  className="w-full"
                  min={5}
                  max={30}
                  value={user.digest_max_items}
                  onChange={(e) => setUser({ ...user, digest_max_items: Number(e.target.value) })}
                />
              </Field>
              <Field
                label={
                  <span className="flex justify-between">
                    <span>Discovery</span>
                    <span className="font-mono text-[var(--text-secondary)]">{user.serendipity_percentage}%</span>
                  </span>
                }
                htmlFor="discovery-input"
                hint="Share of each digest from outside your usual sources."
              >
                <input
                  id="discovery-input"
                  type="range"
                  className="w-full"
                  min={0}
                  max={50}
                  value={user.serendipity_percentage}
                  onChange={(e) => setUser({ ...user, serendipity_percentage: Number(e.target.value) })}
                />
              </Field>
              <Field
                label={
                  <>
                    Send time <span className="font-normal text-[var(--text-tertiary)]">(your time zone)</span>
                    <span className="ml-2 chip border border-[var(--border-strong)] text-[10px] text-[var(--text-secondary)]">
                      {user.send_time_locked ? "Locked" : "Auto-learned"}
                    </span>
                  </>
                }
                htmlFor="send-time-input"
                hint={
                  user.send_time_locked
                    ? "Locked to the time you set."
                    : "Auto: follows when you actually open digests. Changing it locks it."
                }
              >
                <input
                  id="send-time-input"
                  type="time"
                  className="input w-auto"
                  value={user.digest_time_morning.slice(0, 5)}
                  onChange={(e) => setUser({ ...user, digest_time_morning: `${e.target.value}:00` })}
                />
              </Field>
              <Field label="Languages" htmlFor="languages-input" hint="Comma-separated codes, for example en, de. Empty = every language.">
                {/* Raw text while editing: splitting/filtering per keystroke would
                    eat the trailing comma and block typing the next language. */}
                <input
                  id="languages-input"
                  type="text"
                  className="input"
                  value={languagesText}
                  onChange={(e) => setLanguagesText(e.target.value)}
                  placeholder="en, de, fr"
                />
              </Field>
            </div>
            <LlmStatus />
            <div className="mt-5 flex items-center gap-3">
              <button type="button" className="btn-primary disabled:cursor-wait disabled:opacity-70" onClick={save} disabled={saving}>
                {saving ? "Saving…" : "Save preferences"}
              </button>
              {saved && (
                <span role="status" className="flex items-center gap-1.5 text-sm text-emerald-400">
                  <Check className="h-4 w-4" aria-hidden="true" /> Saved
                </span>
              )}
            </div>
            {saveError && (
              <p role="alert" className="mt-2 text-sm text-red-400">
                {saveError}
              </p>
            )}
          </Section>

          <Section title="Export saved articles">
            <CitationExport />
          </Section>

          <Section
            title="Your data"
            hint={
              <>
                Download a portable copy of everything Weirling stores about you, or permanently delete your account.{" "}
                <a href="/privacy" className="underline hover:text-[var(--text)]">
                  What we collect and why
                </a>
                .
              </>
            }
          >
            <label className="mb-4 flex items-start gap-2.5 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={!user.collaborative_opt_out}
                onChange={async (e) => {
                  const updated = await api.preferences.update({ collaborative_opt_out: !e.target.checked }).catch(() => null);
                  if (updated) {
                    setUser(updated);
                    setInitial(updated);
                  }
                }}
              />
              <span>
                Count my reading in anonymous &ldquo;popular with readers&rdquo; picks for new users. Only articles at least
                20 readers finished are ever used, and never linked to you.
              </span>
            </label>
            <div className="flex flex-wrap gap-3">
              <button type="button" className="btn-secondary disabled:opacity-50" onClick={exportData} disabled={busy}>
                Export my data
              </button>
              <button type="button" className="btn-secondary state-negative border disabled:opacity-50" onClick={deleteAccount} disabled={busy}>
                Delete account
              </button>
            </div>
            <p className="mt-4 text-xs text-[var(--text-tertiary)]">
              Running this instance? <a href="/admin/setup" className="underline hover:text-[var(--text)]">Instance setup</a>,{" "}
              <a href="/admin/metrics" className="underline hover:text-[var(--text)]">product metrics</a> and{" "}
              <a href="/admin/ingestion" className="underline hover:text-[var(--text)]">ingestion</a> (owner only).
            </p>
          </Section>
        </div>
        <div className="min-w-0 space-y-6">
          <Section title="Newsletters">
            <NewsletterAddressCard />
          </Section>

          <Section title="Weekly summary">
            <label className="flex items-start gap-2.5 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={Boolean(user.weekly_email)}
                onChange={async (e) => {
                  const updated = await api.preferences.update({ weekly_email: e.target.checked }).catch(() => null);
                  if (updated) {
                    setUser(updated);
                    setInitial(updated);
                  }
                }}
              />
              <span>
                Email me once a week with what Weirling learned: topics rising and fading, and the source I finish most,
                each with a one-click undo.
              </span>
            </label>
          </Section>

          <Section title="Digest alerts">
            <PushAlerts />
          </Section>

          <PlanSection />
          <LlmKeySection />
        </div>
      </div>

      {graph && (
        <Section
          title="Your interest graph"
          hint="Topics you engage with. Bigger = stronger; orange = core interest; lines join topics you read together. Hover a topic to see its neighbours."
        >
          <ForceGraph
            nodes={graphNodes}
            links={graphLinks}
            height={380}
            label={`Graph of your ${Math.min(60, graph.nodes.length)} strongest interests`}
          />
          <p className="mt-4 text-xs text-[var(--text-tertiary)]">
            Click a topic to mute it for 30 days; ▲ asks for more of it. Rename, merge and the full map are on{" "}
            <Link href="/interests" className="underline hover:text-[var(--text)]">
              Interests
            </Link>
            .
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {graph.nodes.slice(0, 30).map((node) => (
              <span
                key={node.label}
                className={`chip border text-sm ${
                  node.is_core
                    ? "border-[var(--accent)]/60 bg-[var(--accent-soft)] text-prism-300"
                    : "border-[var(--border-strong)] bg-[var(--bg-sunken)] text-[var(--text-secondary)]"
                }`}
              >
                <button
                  type="button"
                  onClick={() => suppressTopic(node.label)}
                  title={`Mute "${node.label}" for 30 days`}
                  className="hover:text-[var(--text)]"
                >
                  {node.label}
                </button>
                <button
                  type="button"
                  onClick={() => boostTopic(node.label)}
                  title={`More of "${node.label}"`}
                  aria-label={`More of "${node.label}"`}
                  className="text-emerald-400 hover:text-emerald-300"
                >
                  ▲
                </button>
                <button
                  type="button"
                  onClick={() => renameInterest(node.label)}
                  title={`Rename "${node.label}"`}
                  aria-label={`Rename "${node.label}"`}
                  className="inline-flex items-center opacity-70 hover:opacity-100"
                >
                  <Pencil className="h-3 w-3" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => mergeInterest(node.label)}
                  title={`Merge "${node.label}" into another topic`}
                  aria-label={`Merge "${node.label}" into another topic`}
                  className="opacity-70 hover:opacity-100"
                >
                  ⇄
                </button>
              </span>
            ))}
          </div>
        </Section>
      )}

    </div>
  );
}
