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
    <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
      {plans.map((p) => (
        <div
          key={p.plan}
          data-testid={`plan-${p.plan}`}
          style={{ flex: "1 1 220px", border: "1px solid var(--border)", borderRadius: 8, padding: 16 }}
        >
          <h3 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: 8 }}>
            {p.name}
            {current === p.plan && <span style={{ fontWeight: 400, color: "var(--text-tertiary)" }}> (your plan)</span>}
          </h3>
          <ul style={{ fontSize: 14, margin: "0 0 12px", paddingLeft: 18 }}>
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
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {p.intervals.map((i) => (
                  <button key={i} className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" onClick={() => onBuy(p.plan, i)}>
                    {p.name} {i === "month" ? "monthly" : "yearly"}
                  </button>
                ))}
              </div>
              <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 8 }}>
                By subscribing, you agree to the{" "}
                <a href="/terms" target="_blank" rel="noopener noreferrer" style={{ textDecoration: "underline" }}>
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
    <div style={{ marginBottom: 16 }}>
      <table style={{ fontSize: 14, borderCollapse: "collapse" }}>
        <tbody>
          {quotas.map((q) => {
            const full = q.limit > 0 && q.used >= q.limit;
            return (
              <tr key={q.quota}>
                <td style={{ padding: "2px 16px 2px 0", color: "var(--text-tertiary)" }}>{QUOTA_LABEL[q.quota]}</td>
                <td style={{ padding: "2px 0", color: full ? "#dc2626" : undefined }}>
                  {q.limit > 0 ? `${q.used} of ${q.limit}` : `${q.used} (no limit)`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p style={{ color: "var(--text-tertiary)", fontSize: 13, marginTop: 8 }}>
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
    <section id="plan" style={{ marginTop: 40, borderTop: "1px solid var(--border)", paddingTop: 24 }}>
      <h2 style={{ fontSize: "1.1rem", fontWeight: 600, marginBottom: 8 }}>Plan</h2>
      <p style={{ color: "var(--text-tertiary)", fontSize: 14, marginBottom: 16 }}>{describe(state)}</p>
      <Quotas quotas={state.quotas ?? []} />
      {state.billing_enabled && state.academic_discount && !paying && (
        <p style={{ fontSize: 14, marginBottom: 12, display: "flex", alignItems: "center", gap: 6 }} role="status">
          <GraduationCap style={{ width: 16, height: 16, flexShrink: 0 }} aria-hidden="true" />
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
        <p style={{ fontSize: 14, marginBottom: 12 }}>
          {state.manage_via === "email"
            ? "To switch plans, email us using the link below."
            : "To switch plans, use Manage billing."}
        </p>
      )}
      {state.billing_enabled && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
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
                <p style={{ fontSize: 14 }}>To change or cancel, contact the operator of this service.</p>
              )
            ) : (
              <button className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" onClick={() => go(api.billing.portal())}>
                Manage billing
              </button>
            ))}
        </div>
      )}
      {error && <p style={{ color: "#f87171", fontSize: 14, marginTop: 12 }}>{error}</p>}
    </section>
  );
}
