"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { LLMKeyState } from "../lib/types";



// Your own LLM key on hosted (#255). Renders nothing on self-hosted instances.
export function LlmKeySection() {
  const [state, setState] = useState<LLMKeyState | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.account
      .llmKey()
      .then((s) => {
        setState(s);
        setBaseUrl(s.base_url ?? s.allowed_base_urls?.[0] ?? "");
      })
      .catch(() => {});
  }, []);

  if (!state) return null;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const saved = await api.account.saveLlmKey({ api_key: apiKey, base_url: baseUrl, model: model || undefined });
      setState({ ...state, ...saved });
      setApiKey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the key.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.account.removeLlmKey();
      setState({ ...state, configured: false, last4: null, model: null });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-5 sm:p-6">
      <h2 className="mb-1 text-xl italic">Your own AI key</h2>
      <p className="mb-4 text-sm text-[var(--text-tertiary)]">
        Optional. AI summaries for your digest and the articles you open then run on your provider account, with no
        daily limit from your plan. The key is stored encrypted and is never shown again; only you are billed for it.
      </p>
      {state.configured ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm">
            Key ending in <code>{state.last4 || "····"}</code> at {state.base_url}
            {state.model ? ` (${state.model})` : ""}
          </span>
          <button className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" onClick={remove} disabled={busy}>
            Remove key
          </button>
        </div>
      ) : (
        <div className="grid gap-2.5">
          <label className="text-sm">
            Provider
            <select className="input mt-1 block" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}>
              {(state.allowed_base_urls ?? []).map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            API key
            <input
              className="input mt-1 block"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Model (optional)
            <input
              className="input mt-1 block"
              placeholder="the instance's summary model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </label>
          <div>
            <button className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" onClick={save} disabled={busy || apiKey.trim().length < 8}>
              {busy ? "Checking…" : "Check and save"}
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-red-400">
          {error}
        </p>
      )}
    </section>
  );
}
