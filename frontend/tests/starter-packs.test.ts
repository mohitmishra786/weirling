import { describe, expect, it } from "vitest";
import { packSummary, searchPacks } from "../src/components/sources/StarterPackPicker";
import type { StarterPack } from "../src/lib/types";

const pack = (id: string, feed_count: number): StarterPack => ({
  id,
  title: id,
  feed_count,
  feeds: [],
  suggested: false,
  subscribed: 0,
});

describe("starter pack picker (#152)", () => {
  it("summarises the selection for the sticky footer", () => {
    const packs = [pack("a", 10), pack("b", 21), pack("c", 5)];
    expect(packSummary(packs, ["a", "b"])).toBe("2 packs · 31 sources");
    expect(packSummary(packs, ["c"])).toBe("1 pack · 5 sources");
    expect(packSummary(packs, [])).toBe("0 packs · 0 sources");
  });

  it("search keeps suggested packs first and reports matching sources", () => {
    const packs: StarterPack[] = [
      { ...pack("a", 2), title: "Alpha", feeds: [{ name: "Dan Luu", url: "x" }] },
      { ...pack("b", 2), title: "Beta", suggested: true },
    ];
    expect(searchPacks(packs, "").map((r) => r.pack.id)).toEqual(["b", "a"]);
    expect(searchPacks(packs, "dan luu")).toEqual([{ pack: packs[0], matchedFeeds: ["Dan Luu"] }]);
    expect(searchPacks(packs, "beta")[0].matchedFeeds).toEqual([]);
  });
});
