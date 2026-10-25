"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";
import { InterestInput } from "./InterestInput";
import { CalibrationStep } from "./CalibrationStep";
import { detectedTimezone, digestPreferencesPayload } from "./digestPrefs";
import { useInstallPrompt } from "../../lib/useInstallPrompt";
import { StarterPackPicker, subscribePacks } from "../sources/StarterPackPicker";
import { ImportPanel } from "./ImportPanel";
import { NewsletterAddressCard } from "../NewsletterAddressCard";
import { api } from "../../lib/api";

// Steps are addressed by name so new steps don't shift every index.
const STEPS = [
  "Interests",
  "Confirm Topics",
  "Starter Packs",
  "Add Sources",
  "Newsletters",
  "Add Creators",
  "Rate Headlines",
  "Digest Preferences",
] as const;
type StepName = (typeof STEPS)[number];

/** `onDone` lets the page that hosts the wizard (/digest) leave onboarding:
 *  navigating to the URL it is already on changes nothing. */
export function OnboardingWizard({ onDone }: { onDone?: () => void } = {}) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const current: StepName = STEPS[step];
  const [packIds, setPackIds] = useState<string[]>([]);
  const [subscribing, setSubscribing] = useState(false);
  const [importedCount, setImportedCount] = useState(0);
  const [interestText, setInterestText] = useState("");
  const [topics, setTopics] = useState<string[]>([]);
  const [topicInput, setTopicInput] = useState("");
  const [expanding, setExpanding] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [creatorInput, setCreatorInput] = useState("");
  const [digestFreq, setDigestFreq] = useState<"daily" | "twice_daily" | "weekly">("daily");
  const [digestTime, setDigestTime] = useState("07:00");
  const [emailOn, setEmailOn] = useState(true);
  const [timezone] = useState(detectedTimezone);
  const installer = useInstallPrompt();
  const [installed, setInstalled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const advance = () => setStep((s) => Math.min(s + 1, STEPS.length - 1));
  const next = async () => {
    // Leaving the pack step subscribes the chosen packs (#152).
    if (current === "Starter Packs" && packIds.length) {
      setSubscribing(true);
      setError("");
      try {
        await subscribePacks(packIds);
        setPackIds([]);
      } catch (err: unknown) {
        // Keep the selection so "Continue" retries; skipping is still possible
        // by deselecting the packs.
        setError(
          `Couldn't follow those packs${err instanceof Error && err.message ? `: ${err.message}` : ""}. Try again.`,
        );
        return;
      } finally {
        setSubscribing(false);
      }
    }
    setError("");
    advance();
  };
  const back = () => setStep((s) => Math.max(s - 1, 0));

  // Step 0 -> 1 runs interest expansion (LLM with keyword fallback) so the
  // user confirms/edits the subtopics that become their initial clusters.
  const expandAndContinue = async () => {
    if (!interestText.trim()) {
      setError("Tell us a little about what you read first.");
      return;
    }
    setError("");
    setExpanding(true);
    try {
      const { topics: found } = await api.onboarding.expandInterests(interestText.trim());
      setTopics(found);
      setStep(1);
    } catch {
      // Expansion is optional polish: continue with no confirmed topics and
      // let the backend extract from the raw text.
      setTopics([]);
      setStep(1);
    } finally {
      setExpanding(false);
    }
  };

  const addTopic = () => {
    const t = topicInput.trim();
    if (t && !topics.includes(t)) setTopics((prev) => [...prev, t]);
    setTopicInput("");
  };

  const addSource = () => {
    if (sourceUrl.trim()) {
      setSources((prev) => [...prev, sourceUrl.trim()]);
      setSourceUrl("");
    }
  };

  const finish = async () => {
    setLoading(true);
    setError("");
    try {
      // 1. Submit interest text and sample ratings to the onboarding endpoint
      try {
        await api.onboarding.complete({
          interest_text: interestText,
          // Real headlines are rated in the "Rate Headlines" step (#149).
          sample_ratings: [],
          source_opml: null,
          confirmed_topics: topics.length > 0 ? topics : undefined,
        });
      } catch (err: unknown) {
        // Already saved (a second click, or a retry after a slow first try):
        // carry on with the rest instead of showing an error.
        if (!(err instanceof Error && /already completed/i.test(err.message))) throw err;
      }

      // 2. Add sources and creator via their APIs
      for (const url of sources) {
        await api.sources.add(url).catch(() => {});
      }
      if (creatorInput.trim()) {
        await api.creators.add(creatorInput.trim()).catch(() => {});
      }

      // 3. Update preferences
      // Time + timezone + email choice (#201); an explicit time locks the send time.
      await api.preferences.update(
        digestPreferencesPayload({ frequency: digestFreq, emailOn, time: digestTime, timezone }),
      );

      // 4. Fetch the new sources now and build the first digest once articles
      // are in (#102). The digest page shows live progress until it exists.
      await api.onboarding.startFirstDigest().catch(() => {});

      if (onDone) onDone();
      else router.replace("/digest");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Setup failed");
    } finally {
      setLoading(false);
    }
  };

  const skipLabel =
    (current === "Starter Packs" && packIds.length === 0) ||
    (current === "Add Sources" && sources.length === 0 && importedCount === 0) ||
    (current === "Add Creators" && !creatorInput);

  return (
    <div className="mx-auto my-10 max-w-2xl px-4">
      <div className="mb-8">
        <h1 className="mb-1 text-3xl italic">Welcome to Weirling</h1>
        <p className="text-[var(--text-tertiary)]">
          Step {step + 1} of {STEPS.length}: {STEPS[step]}
        </p>
        <div className="mt-3 flex gap-1" aria-hidden="true">
          {STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1 flex-1 rounded-full ${i <= step ? "bg-[var(--accent)]" : "bg-[var(--border-strong)]"}`}
            />
          ))}
        </div>
      </div>

      {current === "Interests" && (
        <InterestInput value={interestText} onChange={setInterestText} />
      )}

      {current === "Confirm Topics" && (
        <div>
          <p className="mb-4 text-[var(--text-tertiary)]">
            We expanded your interests into these topics. They seed your first
            digest: remove any that are wrong and add what&apos;s missing.
          </p>
          <div className="mb-4 flex flex-wrap gap-2">
            {topics.map((t) => (
              <span
                key={t}
                className="chip border border-[var(--accent)]/50 bg-[var(--accent-soft)] text-sm text-prism-300"
              >
                {t}
                <button
                  type="button"
                  onClick={() => setTopics((prev) => prev.filter((x) => x !== t))}
                  aria-label={`Remove topic ${t}`}
                  className="inline-flex items-center rounded-full text-prism-300 hover:text-prism-200"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </span>
            ))}
            {topics.length === 0 && (
              <p className="text-sm text-[var(--text-secondary)]">
                No topics extracted. Add a few yourself below.
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              className="input flex-1"
              value={topicInput}
              onChange={(e) => setTopicInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addTopic()}
              placeholder="Add a topic…"
              aria-label="Add a topic"
            />
            <button type="button" className="btn-primary" onClick={addTopic}>
              Add
            </button>
          </div>
        </div>
      )}

      {current === "Starter Packs" && (
        <div>
          <p className="mb-4 text-[var(--text-tertiary)]">
            Pick a few starter packs to follow. The ones matching your topics are
            pre-selected. <strong className="text-[var(--text-secondary)]">Optional</strong>: you can
            add more from Sources later.
          </p>
          <StarterPackPicker topics={topics} selected={packIds} onChange={setPackIds} />
        </div>
      )}

      {current === "Rate Headlines" && <CalibrationStep />}

      {current === "Add Sources" && (
        <div>
          <p className="mb-4 text-[var(--text-tertiary)]">
            Coming from another reader? Import your feeds and saved articles in
            one go. <strong className="text-[var(--text-secondary)]">Optional</strong>: you can also
            add sites one by one below.
          </p>
          <div className="mb-5">
            <ImportPanel onImported={(feeds, saved) => setImportedCount((n) => n + feeds + saved)} />
          </div>
          <div className="mb-3 flex gap-2">
            <input
              type="url"
              className="input flex-1"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addSource()}
              placeholder="https://example.com/blog"
              aria-label="Site or feed URL"
            />
            <button type="button" className="btn-primary" onClick={addSource}>
              Add
            </button>
          </div>
          {sources.map((s) => (
            <div key={s} className="mb-1.5 rounded-lg bg-[var(--bg-sunken)] px-3 py-1.5 text-sm">
              {s}
            </div>
          ))}
        </div>
      )}

      {current === "Newsletters" && (
        <div>
          <p className="mb-4 text-[var(--text-tertiary)]">
            Newsletters go in your digest too. <strong className="text-[var(--text-secondary)]">Optional</strong>:
            the address is also in Preferences.
          </p>
          <NewsletterAddressCard />
        </div>
      )}

      {current === "Add Creators" && (
        <div>
          <p className="mb-4 text-[var(--text-tertiary)]">
            Add creators you follow (name or URL). <strong className="text-[var(--text-secondary)]">Optional</strong>:
            skip and add later from the Creators page.
          </p>
          <input
            type="text"
            className="input"
            value={creatorInput}
            onChange={(e) => setCreatorInput(e.target.value)}
            placeholder="https://simonwillison.substack.com or Simon Willison"
            aria-label="Creator name or URL"
          />
        </div>
      )}

      {current === "Digest Preferences" && (
        <div className="space-y-5">
          <div>
            <label className="mb-2 block font-medium" htmlFor="digest-frequency">
              Digest frequency
            </label>
            <select
              id="digest-frequency"
              className="input"
              value={digestFreq}
              onChange={(e) => setDigestFreq(e.target.value as typeof digestFreq)}
              disabled={!emailOn}
            >
              <option value="daily">Daily</option>
              <option value="twice_daily">Twice daily</option>
              <option value="weekly">Weekly</option>
            </select>
          </div>

          <div>
            <label className="mb-2 block font-medium" htmlFor="digest-time">
              Delivery time
            </label>
            <input
              id="digest-time"
              type="time"
              className="input w-auto"
              value={digestTime}
              onChange={(e) => setDigestTime(e.target.value)}
            />
            <p className="mt-1.5 text-sm text-[var(--text-tertiary)]">Your time zone: {timezone}</p>
          </div>

          <label className="flex items-center gap-2.5 text-sm">
            <input type="checkbox" checked={emailOn} onChange={(e) => setEmailOn(e.target.checked)} />
            Email me the digest (otherwise read it in the app)
          </label>

          {installer.available && !installed && (
            <div className="card p-4">
              <p className="mb-3 text-sm">
                Add Weirling to your home screen to open your digest in one tap.
              </p>
              <button
                type="button"
                className="btn-secondary"
                onClick={async () => setInstalled(await installer.install())}
              >
                Add to home screen
              </button>
            </div>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="mt-4 text-sm text-red-400">
          {error}
        </p>
      )}

      <div className="mt-8 flex justify-between">
        <button
          type="button"
          className="btn-secondary disabled:cursor-not-allowed disabled:opacity-40"
          onClick={back}
          disabled={step === 0 || loading || subscribing}
        >
          Back
        </button>
        {step < STEPS.length - 1 ? (
          <button
            type="button"
            className="btn-primary disabled:cursor-wait disabled:opacity-70"
            onClick={step === 0 ? expandAndContinue : next}
            disabled={expanding || subscribing}
          >
            {expanding
              ? "Finding your topics…"
              : subscribing
                ? "Subscribing…"
                : /* Optional steps read as "Skip" when nothing was chosen */
                  skipLabel
                  ? "Skip for now"
                  : "Continue"}
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary disabled:cursor-wait disabled:opacity-70"
            onClick={finish}
            disabled={loading}
          >
            {loading ? "Setting up your digest…" : "Start Weirling"}
          </button>
        )}
      </div>
    </div>
  );
}
