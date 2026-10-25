// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { StarterPack } from "../src/lib/types";

const packs: StarterPack[] = [
  { id: "systems-programming", title: "Systems Programming", feed_count: 12, feeds: [{ name: "LWN", url: "u1" }], suggested: true, subscribed: 0 },
  { id: "design-ux", title: "Design & UX", feed_count: 8, feeds: [{ name: "Smashing", url: "u2" }], suggested: false, subscribed: 0 },
  { id: "databases-data", title: "Databases", feed_count: 5, feeds: [], suggested: false, subscribed: 5 },
];
const api = vi.hoisted(() => ({
  sources: {
    starterPacks: vi.fn(),
    subscribePacks: vi.fn(() => Promise.resolve({ status: "ok", packs: ["a", "b"], subscribed: 24 })),
  },
}));
vi.mock("../src/lib/api", () => ({ api }));

import { StarterPackPicker, subscribePacks } from "../src/components/sources/StarterPackPicker";

function Harness() {
  const [selected, setSelected] = useState<string[]>([]);
  return <StarterPackPicker topics={["Rust"]} selected={selected} onChange={setSelected} />;
}

describe("StarterPackPicker (#152)", () => {
  it("pre-selects packs matching the topics and summarises the choice", async () => {
    api.sources.starterPacks.mockResolvedValue(packs);
    render(<Harness />);
    expect(await screen.findByText("Systems Programming")).toBeInTheDocument();
    expect(api.sources.starterPacks).toHaveBeenCalledWith(["Rust"]);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Systems Programming/ })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByText("1 pack · 12 sources")).toBeInTheDocument();
  });

  it("toggles packs and disables ones already followed", async () => {
    api.sources.starterPacks.mockResolvedValue(packs);
    render(<Harness />);
    const design = await screen.findByRole("button", { name: /Design & UX/ });
    await userEvent.click(design);
    expect(design).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Databases/ })).toBeDisabled();
    expect(screen.getByText("Already following")).toBeInTheDocument();
  });

  it("subscribePacks sends every pack in one request", async () => {
    expect(await subscribePacks(["a", "b"])).toBe(24);
    expect(api.sources.subscribePacks).toHaveBeenCalledTimes(1);
    expect(api.sources.subscribePacks).toHaveBeenCalledWith(["a", "b"]);
    expect(await subscribePacks([])).toBe(0);
  });

  it("subscribePacks lets a failure reach the caller", async () => {
    api.sources.subscribePacks.mockRejectedValueOnce(new Error("Unknown starter pack: x"));
    await expect(subscribePacks(["x"])).rejects.toThrow("Unknown starter pack");
  });
});
