// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => vi.fn());
const checkout = vi.hoisted(() => vi.fn(() => Promise.reject(new Error("no"))));
vi.mock("../src/lib/api", () => ({ api: { billing: { state, checkout, portal: vi.fn() } } }));

import { PlanSection } from "../src/components/PlanSection";

const base = {
  billing_enabled: false,
  status: "active",
  trial_ends_at: null,
  trial_days_left: null,
  plan_expires_at: null,
  can_manage: false,
};

describe("plan quotas (#172)", () => {
  it("lists limits and use, flagging a used-up allowance", async () => {
    state.mockResolvedValueOnce({
      ...base,
      plan: "pro",
      quotas: [
        { quota: "sources", limit: 500, used: 42, daily: false },
        { quota: "llm_summaries_per_day", limit: 200, used: 200, daily: true },
        { quota: "creators", limit: 0, used: 3, daily: false },
      ],
    });
    render(<PlanSection />);
    expect(await screen.findByText("42 of 500")).toBeInTheDocument();
    expect(screen.getByText("200 of 200")).toHaveClass("text-red-400");
    expect(screen.getByText("3 (no limit)")).toBeInTheDocument();
    expect(screen.getByText(/reset at midnight UTC/)).toBeInTheDocument();
  });

  it("renders nothing on self-host", async () => {
    state.mockResolvedValueOnce({ ...base, plan: "selfhost", quotas: [] });
    const { container } = render(<PlanSection />);
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });

  it("offers Reader and Pro side by side and buys the chosen one", async () => {
    const q = { sources: 500, creators: 50, newsletters_per_day: 100, newsletter_mb_per_day: 50, llm_summaries_per_day: 200, digest_builds_per_day: 6 };
    state.mockResolvedValueOnce({
      ...base,
      billing_enabled: true,
      plan: "trial",
      status: null,
      quotas: [],
      plans: [
        { plan: "reader", name: "Reader", intervals: ["month", "year"], quotas: q },
        {
          plan: "pro",
          name: "Pro",
          intervals: ["year"],
          quotas: { ...q, sources: 2000 },
          features: ["Twice-daily digests", "API and MCP access"],
        },
      ],
    });
    render(<PlanSection />);
    const pro = await screen.findByTestId("plan-pro");
    expect(within(pro).getByText("2,000 feeds")).toBeInTheDocument();
    expect(within(pro).getByText("API and MCP access")).toBeInTheDocument(); // Pro-only (#246)
    expect(within(screen.getByTestId("plan-reader")).queryByText("API and MCP access")).toBeNull();
    expect(within(screen.getByTestId("plan-reader")).getByText("500 feeds")).toBeInTheDocument();
    expect(within(pro).queryByRole("button", { name: "Pro monthly" })).toBeNull();
    await userEvent.click(within(pro).getByRole("button", { name: "Pro yearly" }));
    expect(checkout).toHaveBeenCalledWith("year", "pro");
  });

  it("providers without a portal show the operator's email; others keep Manage billing (#342)", async () => {
    state.mockResolvedValueOnce({
      ...base,
      billing_enabled: true,
      plan: "pro",
      can_manage: true,
      portal_url: null,
      manage_via: "email",
      support_email: "help@example.com",
      quotas: [],
    });
    const { unmount } = render(<PlanSection />);
    const link = await screen.findByRole("link", { name: "Email to change or cancel" });
    expect(link).toHaveAttribute("href", "mailto:help@example.com?subject=Manage%20subscription");
    expect(screen.queryByRole("button", { name: "Manage billing" })).toBeNull();
    unmount();

    // Paddle stores no portal URL (it makes one on demand): still the button.
    state.mockResolvedValueOnce({
      ...base,
      billing_enabled: true,
      plan: "pro",
      can_manage: true,
      portal_url: null,
      manage_via: "portal",
      quotas: [],
    });
    render(<PlanSection />);
    expect(await screen.findByRole("button", { name: "Manage billing" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Email to change or cancel" })).toBeNull();
  });
});
