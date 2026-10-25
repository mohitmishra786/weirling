"use client";
import { useEffect, useState } from "react";
import { GraduationCap } from "lucide-react";
import { api } from "../lib/api";
import type { BillingState, PlanOffer, PlanQuota } from "../lib/types";


function day(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : "";
}

function describe(state: BillingState): string {
  switch (state.plan) {
    case "trial":
      // Billing off (hosted beta): nothing runs out, so don't count down.
      if (!state.billing_enabled || !state.trial_ends_at)
        return "Early access: everything is included while Weirling is in beta. No payment needed.";
      return `Free trial: ${state.trial_days_left ?? 0} days left (until ${day(state.trial_ends_at)}).`;
    case "reader":
    case "pro":
    case "team": {
      const name = { reader: "Reader", pro: "Pro", team: "Team" }[state.plan];
      if (state.status === "cancelled") return `${name}, cancelled: active until ${day(state.plan_expires_at)}.`;
      if (state.status === "past_due") return `${name}: the last payment failed. Update your card to keep access.`;
      return state.plan_expires_at ? `${name}: renews ${day(state.plan_expires_at)}.` : `${name}.`;
    }
    case "paused":
      return state.deletes_at
        ? `Your account is paused: no new digests or feed updates. Choose a plan to continue; otherwise it is deleted on ${day(state.deletes_at)}. You can export your data until then.`
        : "Your account is paused: no new digests or feed updates. Choose a plan to continue.";
    default:
      return "No active plan. Subscribe to keep your digests coming.";
  }
}

const QUOTA_LABEL: Record<PlanQuota["quota"], string> = {
  sources: "Feeds",
  creators: "Tracked creators",
  newsletters_per_day: "Newsletters today",
  newsletter_mb_per_day: "Newsletter volume today (MB)",
  llm_summaries_per_day: "AI summaries today",
  digest_builds_per_day: "Manual digest builds today",
};

const n = (v: number) => (v > 0 ? v.toLocaleString() : "Unlimited");

// The plans on sale, side by side (#172).
function PlanPicker({
  plans,
  current,
  onBuy,
}: {
  plans: PlanOffer[];
  current: string;
  onBuy: (plan: PlanOffer["plan"], interval: "month" | "year") => void;
}) {
  if (!plans.length) return null;
  return (
    <div className="mb-4 flex flex-wrap gap-4">
      {plans.map((p) => (
        <div
          key={p.plan}
          data-testid={`plan-${p.plan}`}
          className="min-w-[13rem] flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] p-4"
        >
          <h3 className="mb-2 text-base font-semibold">
            {p.name}
            {current === p.plan && <span className="font-normal text-[var(--text-tertiary)]"> (your plan)</span>}
          </h3>
          <ul className="mb-3 list-disc pl-5 text-sm">
            <li>{n(p.quotas.sources)} feeds</li>
            <li>{n(p.quotas.llm_summaries_per_day)} AI summaries a day</li>
            <li>{n(p.quotas.newsletter_mb_per_day)} MB of newsletters a day</li>
            <li>{n(p.quotas.creators)} tracked creators</li>
            {(p.features ?? []).map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
          {current !== p.plan && (
            <div>
              <div className="flex flex-wrap gap-2">
                {p.intervals.map((i) => (
                  <button key={i} className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" onClick={() => onBuy(p.plan, i)}>
                    {p.name} {i === "month" ? "monthly" : "yearly"}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                By subscribing, you agree to the{" "}
                <a href="/terms" target="_blank" rel="noopener noreferrer" className="underline">
                  Terms of Service
                </a>
                .
              </p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// What the plan includes and what is used (#172).
function Quotas({ quotas }: { quotas: PlanQuota[] }) {
  if (!quotas.length) return null;
  return (
    <div className="mb-4">
      <table className="border-collapse text-sm">
        <tbody>
          {quotas.map((q) => {
            const full = q.limit > 0 && q.used >= q.limit;
            return (
              <tr key={q.quota}>
                <td className="py-0.5 pr-4 text-[var(--text-tertiary)]">{QUOTA_LABEL[q.quota]}</td>
                <td className={`py-0.5 ${full ? "text-red-400" : ""}`}>
                  {q.limit > 0 ? `${q.used} of ${q.limit}` : `${q.used} (no limit)`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-[var(--text-tertiary)]">
        Daily allowances reset at midnight UTC. When AI summaries run out, digests use the shorter
        extract until the next day; scheduled digests are never skipped.
      </p>
    </div>
  );
}

// Hosted plan and billing (#173). Renders nothing on self-hosted instances.
export function PlanSection() {
  const [state, setState] = useState<BillingState | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.billing.state().then(setState).catch(() => {});
  }, []);

  if (!state || state.plan === "selfhost") return null;

  const go = (pending: Promise<{ url: string | null }>) =>
    pending
      .then(({ url }) => {
        if (!url) throw new Error("no url");
        window.location.href = url;
      })
      .catch(() => setError("Billing is unavailable right now. Try again in a few minutes."));

  const paying =
    (state.plan === "reader" || state.plan === "pro" || state.plan === "team") && state.status !== "cancelled";

  return (
    <section id="plan" className="card p-5 sm:p-6">
      <h2 className="mb-1 text-xl italic">Plan</h2>
      <p className="mb-4 text-sm text-[var(--text-tertiary)]">{describe(state)}</p>
      <Quotas quotas={state.quotas ?? []} />
      {state.billing_enabled && state.academic_discount && !paying && (
        <p className="mb-3 flex items-center gap-1.5 text-sm" role="status">
          <GraduationCap className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>Academic discount: your university email qualifies, and the discount is applied at checkout.</span>
        </p>
      )}
      {state.billing_enabled && !paying && (
        <PlanPicker
          plans={state.plans ?? []}
          current={state.plan}
          onBuy={(plan, interval) => go(api.billing.checkout(interval, plan))}
        />
      )}
      {state.billing_enabled && paying && (state.plans?.length ?? 0) > 1 && (
        <p className="mb-3 text-sm">
          {state.manage_via === "email"
            ? "To switch plans, email us using the link below."
            : "To switch plans, use Manage billing."}
        </p>
      )}
      {state.billing_enabled && (
        <div className="flex flex-wrap items-center gap-3">
          {state.can_manage &&
            (state.manage_via === "email" ? (
              state.support_email ? (
                <a
                  href={`mailto:${state.support_email}?subject=Manage%20subscription`}
                  className="btn-secondary no-underline"
                >
                  Email to change or cancel
                </a>
              ) : (
                <p className="text-sm">To change or cancel, contact the operator of this service.</p>
              )
            ) : (
              <button className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" onClick={() => go(api.billing.portal())}>
                Manage billing
              </button>
            ))}
        </div>
      )}
      {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
    </section>
  );
}
