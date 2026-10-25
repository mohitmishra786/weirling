"use client";

import { useEffect, useState } from "react";
import { api, type SetupConfig, type SetupField } from "../../../../lib/api";

// First-boot setup (#220): the instance owner sets the LLM key, email and
// sign-ups here instead of editing .env. Secrets are write-only: the API only
// says whether one is saved, so the inputs start empty.

const GROUPS: { id: SetupField["group"]; title: string; intro: string }[] = [
  {
    id: "llm",
    title: "AI summaries (LLM)",
    intro:
      "Any OpenAI-compatible provider. Without a key Weirling still works, with extract summaries.",
  },
  {
    id: "smtp",
    title: "Email",
    intro: "SMTP with STARTTLS on port 587, or TLS on 465, for digests and account emails.",
  },
  {
    id: "registration",
    title: "Who can sign up",
    intro: "closed: nobody else. invite: people with a code below. open: anyone.",
  },
];

type Result = { ok: boolean; message: string } | null;

export default function SetupPage() {
  const [config, setConfig] = useState<SetupConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, Result>>({});

  useEffect(() => {
    api.setup
      .config()
      .then(setConfig)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Unavailable"));
  }, []);

  if (error) {
    return (
      <section className="max-w-2xl">
        <h1 className="font-playfair text-2xl">Instance setup</h1>
        <p className="mt-3 text-sm text-stone-600 dark:text-stone-300">{error}</p>
      </section>
    );
  }
  if (!config) return <p className="text-sm text-stone-500 dark:text-stone-400">Loading…</p>;

  const fields = (group: SetupField["group"]) => config.fields.filter((f) => f.group === group);
  const change = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value }));

  const save = async (group: SetupField["group"], extra: Record<string, string | null> = {}) => {
    const values: Record<string, string | null> = { ...extra };
    for (const f of fields(group)) {
      if (!(f.key in draft) || f.source === "env") continue;
      const v = draft[f.key];
      if (f.secret && v === "") continue; // blank keeps the saved secret
      values[f.key] = v;
    }
    if (Object.keys(values).length === 0) return;
    try {
      const next = await api.setup.save(values);
      setConfig(next);
      setDraft((d) => {
        const rest = { ...d };
        for (const k of Object.keys(values)) delete rest[k];
        return rest;
      });
      setSaved(group);
    } catch (e: unknown) {
      setResults((r) => ({
        ...r,
        [group]: { ok: false, message: e instanceof Error ? e.message : "Couldn't save" },
      }));
    }
  };

  const test = async (group: "llm" | "smtp") => {
    setResults((r) => ({ ...r, [group]: { ok: true, message: "Testing…" } }));
    const pick = (k: string) => draft[k] || undefined;
    try {
      const res =
        group === "llm"
          ? await api.setup.testLlm({
              api_key: pick("llm_api_key"),
              base_url: pick("llm_base_url"),
              model: pick("llm_model_fast"),
            })
          : await api.setup.testSmtp({
              host: pick("smtp_host"),
              port: pick("smtp_port") ? Number(pick("smtp_port")) : undefined,
              username: pick("smtp_user"),
              password: pick("smtp_password"),
            });
      setResults((r) => ({ ...r, [group]: res }));
    } catch (e: unknown) {
      setResults((r) => ({
        ...r,
        [group]: { ok: false, message: e instanceof Error ? e.message : "Test failed" },
      }));
    }
  };

  return (
    <section className="max-w-2xl space-y-8">
      <div>
        <h1 className="font-playfair text-2xl text-stone-900 dark:text-stone-100">Instance setup</h1>
        <p className="mt-2 text-sm text-stone-600 dark:text-stone-300">
          You own this Weirling instance. Settings saved here are encrypted on your server. Anything
          set in <code>.env</code> takes precedence and is shown as locked.
        </p>
        {!config.can_store_secrets && (
          <p role="alert" className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            Saving is off: <code>SECRETS_ENCRYPTION_KEY</code> isn&apos;t set. Run{" "}
            <code>./scripts/init_env.sh</code> and restart, or set these in <code>.env</code>.
          </p>
        )}
      </div>

      {GROUPS.map((g) => {
        const result = results[g.id];
        return (
          <form
            key={g.id}
            aria-labelledby={`setup-${g.id}`}
            className="card space-y-3 p-5"
            onSubmit={(e) => {
              e.preventDefault();
              void save(g.id);
            }}
          >
            <h2 id={`setup-${g.id}`} className="text-lg font-semibold">
              {g.title}
              {g.id === "llm" && (
                <span className="ml-2 text-xs font-normal text-stone-500 dark:text-stone-400">
                  {config.llm_configured ? "Configured" : "Not configured"}
                </span>
              )}
              {g.id === "smtp" && (
                <span className="ml-2 text-xs font-normal text-stone-500 dark:text-stone-400">
                  {config.email_configured ? "Configured" : "Not configured"}
                </span>
              )}
            </h2>
            <p className="text-sm text-stone-600 dark:text-stone-300">{g.intro}</p>
            {g.id === "registration" && (
              <p className="text-sm">
                Right now: <strong>{config.effective_registration_mode}</strong>
              </p>
            )}
            {fields(g.id).map((f) => (
              <FieldInput
                key={f.key}
                field={f}
                value={draft[f.key]}
                onChange={(v) => change(f.key, v)}
                onClear={() => void save(g.id, { [f.key]: null })}
              />
            ))}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <button type="submit" className="btn-primary" disabled={!config.can_store_secrets}>
                Save
              </button>
              {(g.id === "llm" || g.id === "smtp") && (
                <button type="button" className="btn-secondary" onClick={() => void test(g.id as "llm" | "smtp")}>
                  {g.id === "llm" ? "Test the key" : "Send me a test email"}
                </button>
              )}
              {saved === g.id && <span className="text-sm text-emerald-700 dark:text-emerald-300">Saved.</span>}
            </div>
            {result && (
              <p role="status" className={`text-sm ${result.ok ? "text-emerald-700" : "text-rose-700"}`}>
                {result.message}
              </p>
            )}
          </form>
        );
      })}
    </section>
  );
}

function FieldInput({
  field,
  value,
  onChange,
  onClear,
}: {
  field: SetupField;
  value: string | undefined;
  onChange: (v: string) => void;
  onClear: () => void;
}) {
  const id = `setup-field-${field.key}`;
  const locked = field.source === "env";
  const note = locked
    ? "Set in .env"
    : field.secret && field.is_set
      ? "Saved. Leave blank to keep it."
      : null;
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {field.label}
        {note && <span className="ml-2 text-xs font-normal text-stone-500 dark:text-stone-400">{note}</span>}
      </label>
      <div className="flex gap-2">
        {field.choices ? (
          <select
            id={id}
            className="input"
            disabled={locked}
            value={value ?? String(field.value ?? "")}
            onChange={(e) => onChange(e.target.value)}
          >
            <option value="">Default for this mode</option>
            {field.choices.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : (
          <input
            id={id}
            className="input"
            type={field.secret ? "password" : field.key.endsWith("port") ? "number" : "text"}
            autoComplete={field.secret ? "new-password" : "off"}
            disabled={locked}
            value={value ?? (field.secret ? "" : String(field.value ?? ""))}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
        {field.secret && field.source === "saved" && (
          <button type="button" className="btn-secondary text-sm" onClick={onClear}>
            Remove
          </button>
        )}
      </div>
    </div>
  );
}
