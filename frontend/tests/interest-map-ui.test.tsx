// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const recent = new Date(Date.now() - 86_400_000).toISOString();
const graph = {
  nodes: [
    { label: "compilers", weight: 0.9, is_core: true, last_reinforced_at: recent, reinforcement_count: 12, suppressed_until: null },
    { label: "rust", weight: 0.7, is_core: false, last_reinforced_at: null, reinforcement_count: 3, suppressed_until: null },
    { label: "gardening", weight: 0.2, is_core: false, last_reinforced_at: null, reinforcement_count: 1,
      suppressed_until: new Date(Date.now() + 5 * 86_400_000).toISOString() },
  ],
  edges: [{ from_label: "compilers", to_label: "rust", weight: 0.6 }],
};
const api = vi.hoisted(() => ({
  preferences: { interestGraph: vi.fn() },
  feedback: {
    adjustInterests: vi.fn(() => Promise.resolve({})),
    renameInterest: vi.fn(() => Promise.resolve({})),
    mergeInterests: vi.fn(() => Promise.resolve({})),
  },
}));
vi.mock("../src/lib/api", () => ({ api }));

import { InterestMap, isMuted, isRecent } from "../src/components/InterestMap";

// jsdom has no PointerEvent; without this, pointer events carry no coordinates.
if (typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  (window as unknown as { PointerEvent: typeof PointerEventPolyfill }).PointerEvent = PointerEventPolyfill;
}

beforeEach(() => {
  api.preferences.interestGraph.mockResolvedValue(graph);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

async function select(label: string) {
  const list = await screen.findByRole("list");
  await userEvent.click(within(list).getByRole("button", { name: new RegExp(`^${label}`) }));
  return screen.getByRole("group", { name: `Edit ${label}` });
}

describe("Interest map (#179)", () => {
  it("draws every topic and asks for up to 300 of them", async () => {
    render(<InterestMap />);
    expect(await screen.findByRole("img", { name: /Map of 3 interests/ })).toBeInTheDocument();
    expect(api.preferences.interestGraph).toHaveBeenCalledWith(300);
    // Drawn on a canvas; the list beside it names every topic.
    expect(within(await screen.findByRole("list")).getAllByRole("button")).toHaveLength(3);
  });

  it("offers boost, mute, remove, rename and merge for a topic", async () => {
    render(<InterestMap />);
    let panel = await select("rust");
    await userEvent.click(within(panel).getByRole("button", { name: "Boost" }));
    expect(api.feedback.adjustInterests).toHaveBeenCalledWith("rust", "boost");

    panel = await select("rust");
    await userEvent.click(within(panel).getByRole("button", { name: "Mute 30 days" }));
    expect(api.feedback.adjustInterests).toHaveBeenCalledWith("rust", "suppress", 30);

    panel = await select("rust");
    await userEvent.click(within(panel).getByRole("button", { name: "Remove" }));
    expect(api.feedback.adjustInterests).toHaveBeenCalledWith("rust", "remove");

    panel = await select("rust");
    const name = within(panel).getByLabelText("New name");
    await userEvent.clear(name);
    await userEvent.type(name, "Rust language");
    await userEvent.click(within(panel).getByRole("button", { name: "Rename" }));
    expect(api.feedback.renameInterest).toHaveBeenCalledWith("rust", "Rust language");

    panel = await select("rust");
    await userEvent.selectOptions(within(panel).getByLabelText("Merge into"), "compilers");
    await userEvent.click(within(panel).getByRole("button", { name: "Merge" }));
    expect(api.feedback.mergeInterests).toHaveBeenCalledWith("rust", "compilers");
    expect(api.preferences.interestGraph.mock.calls.length).toBeGreaterThan(3); // reloads after edits
  });

  it("on a phone, every tap on a topic (even the selected one) brings its panel into view", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    window.matchMedia = ((q: string) => ({ matches: q.includes("reduce") })) as unknown as typeof window.matchMedia;
    render(<InterestMap />);
    const list = await screen.findByRole("list");
    const compilers = within(list).getByRole("button", { name: /^compilers/ });
    await userEvent.click(compilers);
    await userEvent.click(compilers);
    expect(scroll).toHaveBeenCalledTimes(2);
    // Reduced motion: jump, don't animate.
    expect(scroll).toHaveBeenLastCalledWith({ behavior: "auto", block: "nearest" });
  });

  it("highlights recent and muted topics", () => {
    expect(isRecent(graph.nodes[0])).toBe(true);
    expect(isRecent(graph.nodes[1])).toBe(false);
    expect(isMuted(graph.nodes[2])).toBe(true);
  });

  it("explains an empty map", async () => {
    api.preferences.interestGraph.mockResolvedValue({ nodes: [], edges: [] });
    render(<InterestMap />);
    expect(await screen.findByText(/No interests yet/)).toBeInTheDocument();
  });
});
