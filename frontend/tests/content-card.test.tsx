// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeItem } from "./helpers";

const push = vi.fn();
const api = vi.hoisted(() => ({
  feedback: {
    interaction: vi.fn(() => Promise.resolve({})),
    adjustInterests: vi.fn(() => Promise.resolve({})),
    getInteraction: vi.fn(() => Promise.resolve(null)),
  },
}));
vi.mock("../src/lib/api", () => ({ api }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));

import { ContentCard } from "../src/components/digest/ContentCard";

const breakdown = {
  semantic: 0.6,
  source_trust: 0.3,
  novelty: 0.1,
  explanation: "Close to your interest in compilers",
  why_topics: "matches your interest in compilers",
  no_data: ["suggestion"],
  contributions: { semantic: 0.24, source_trust: 0.12, novelty: 0.04, suggestion: 0.1 },
};

describe("ContentCard", () => {
  it("shows the title, AI-labelled summary and a Why button", () => {
    render(<ContentCard content={makeItem()} signalBreakdown={breakdown} />);
    expect(screen.getByRole("link", { name: "How compilers lower loops" })).toBeInTheDocument();
    expect(screen.getByText("AI summary")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /why this/i })).toBeInTheDocument();
  });

  it("opens a why panel whose shares come from the stored contributions (#185)", async () => {
    render(<ContentCard content={makeItem()} signalBreakdown={breakdown} />);
    const toggle = screen.getByRole("button", { name: /why this/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("region", { name: "Why this is ranked here" });
    expect(panel).toHaveTextContent("Close to your interest in compilers");
    // score 0.5: semantic 0.24 -> 48%, trust 24%, novelty 8%, no-data 20%
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual([
      "Topic match: compilers48%",
      "Source you trust24%",
      "New angle8%",
      "No data yet (neutral)20%",
    ]);
    expect(screen.getByRole("link", { name: "Topic match: compilers" })).toHaveAttribute("href", "/interests");
  });

  it("Escape from inside the panel closes it and returns focus to the button", async () => {
    render(<ContentCard content={makeItem()} signalBreakdown={breakdown} />);
    const toggle = screen.getByRole("button", { name: /why this/i });
    await userEvent.click(toggle);
    screen.getByRole("link", { name: "Topic match: compilers" }).focus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Why this is ranked here" })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });

  it("closes the why panel on a click elsewhere", async () => {
    render(
      <div>
        <p>outside</p>
        <ContentCard content={makeItem()} signalBreakdown={breakdown} />
      </div>,
    );
    await userEvent.click(screen.getByRole("button", { name: /why this/i }));
    await userEvent.click(screen.getByText("outside"));
    expect(screen.queryByRole("region", { name: "Why this is ranked here" })).not.toBeInTheDocument();
  });

  it("opens and closes the why panel from the keyboard", async () => {
    render(<ContentCard content={makeItem()} signalBreakdown={breakdown} />);
    screen.getByRole("button", { name: /why this/i }).focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("region", { name: "Why this is ranked here" })).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(screen.queryByRole("region", { name: "Why this is ranked here" })).not.toBeInTheDocument();
  });

  it("toggles between the brief and detailed summary", async () => {
    render(<ContentCard content={makeItem()} />);
    expect(screen.getByText("A brief summary.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show detailed takeaway" }));
    expect(screen.getByText("A detailed takeaway.")).toBeInTheDocument();
  });

  it("navigates to the reader on title click", async () => {
    render(<ContentCard content={makeItem({ id: "abc" })} />);
    await userEvent.click(screen.getByRole("link", { name: "How compilers lower loops" }));
    expect(push).toHaveBeenCalledWith("/read/abc");
  });

  it("labels discovery items with where they came from", () => {
    render(
      <ContentCard
        content={makeItem()}
        isDiscovery
        signalBreakdown={{ discovered_via: "a link in Simon Willison's blog" }}
      />,
    );
    expect(screen.getByText(/found via a link in Simon Willison's blog/)).toBeInTheDocument();
  });

  it("labels quiet-day catch-up items", () => {
    render(<ContentCard content={makeItem()} signalBreakdown={{ fallback_reason: "quiet" }} />);
    expect(screen.getByText(/Catch-up/)).toBeInTheDocument();
  });

  it("rates and saves through the feedback bar", async () => {
    render(<ContentCard content={makeItem({ id: "x1" })} />);
    await userEvent.click(screen.getByTitle("Helpful"));
    expect(api.feedback.interaction).toHaveBeenCalledWith({ content_item_id: "x1", explicit_rating: 1 });
    await userEvent.click(screen.getByTitle("Save for later"));
    expect(api.feedback.interaction).toHaveBeenCalledWith({ content_item_id: "x1", saved: true });
  });

  it("adjusts the top topic from the card", async () => {
    render(<ContentCard content={makeItem()} />);
    await userEvent.click(screen.getByTitle("More like this (compilers)"));
    expect(api.feedback.adjustInterests).toHaveBeenCalledWith("compilers", "boost");
  });
});
