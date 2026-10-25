import { describe, expect, it } from "vitest";
import { createSim, fit, nodeAt, radiusFor, settle, step } from "../src/lib/graphSim";

function graph(n: number) {
  const nodes = Array.from({ length: n }, (_, i) => ({ id: `t${i}`, r: radiusFor(((i * 37) % 100) / 100) }));
  const links = Array.from({ length: n - 1 }, (_, i) => ({ source: `t${i}`, target: `t${(i * 7 + 3) % n}`, weight: 0.5 }));
  return { nodes, links };
}

describe("graph simulation", () => {
  it("springs open from a tight start and comes to rest", () => {
    const { nodes, links } = graph(40);
    const sim = createSim(nodes, links);
    const spreadBefore = Math.max(...sim.nodes.map((p) => Math.hypot(p.x, p.y)));
    settle(sim);
    expect(step(sim)).toBe(false); // settled
    const spreadAfter = Math.max(...sim.nodes.map((p) => Math.hypot(p.x, p.y)));
    expect(spreadAfter).toBeGreaterThan(spreadBefore * 3);
  });

  it("keeps circles from overlapping, even with 300 topics, quickly", () => {
    const { nodes, links } = graph(300);
    const started = performance.now();
    const sim = settle(createSim(nodes, links));
    expect(performance.now() - started).toBeLessThan(3000);
    for (let i = 0; i < sim.nodes.length; i++)
      for (let j = i + 1; j < sim.nodes.length; j++) {
        const p = sim.nodes[i], q = sim.nodes[j];
        expect(Math.hypot(p.x - q.x, p.y - q.y)).toBeGreaterThan((p.r + q.r) * 0.8);
      }
  });

  it("is deterministic for the same graph", () => {
    const { nodes, links } = graph(30);
    const a = settle(createSim(nodes, links)).nodes.map((p) => [p.x, p.y]);
    const b = settle(createSim(nodes, links)).nodes.map((p) => [p.x, p.y]);
    expect(a).toEqual(b);
  });

  it("pulls linked topics closer than unlinked ones", () => {
    const sim = settle(
      createSim(
        [{ id: "a", r: 10 }, { id: "b", r: 10 }, { id: "c", r: 10 }, { id: "d", r: 10 }],
        [{ source: "a", target: "b", weight: 1 }],
      ),
    );
    const d = (i: number, j: number) => Math.hypot(sim.nodes[i].x - sim.nodes[j].x, sim.nodes[i].y - sim.nodes[j].y);
    expect(d(0, 1)).toBeLessThan(d(0, 2));
    expect(sim.neighbors[0].has(1) && !sim.neighbors[0].has(2)).toBe(true);
  });

  it("finds the topic under the pointer and skips the one being dragged", () => {
    const sim = createSim([{ id: "a", r: 10 }, { id: "b", r: 10 }], []);
    sim.nodes[0].x = 0; sim.nodes[0].y = 0;
    sim.nodes[1].x = 100; sim.nodes[1].y = 0;
    expect(nodeAt(sim, 3, 2)).toBe(0);
    expect(nodeAt(sim, 50, 50)).toBe(-1);
    expect(nodeAt(sim, 2, 0, 4, 0)).toBe(-1);
  });

  it("fits the graph inside the viewport", () => {
    const sim = settle(createSim(graph(20).nodes, graph(20).links));
    const f = fit(sim, 800, 400);
    for (const p of sim.nodes) {
      expect(p.x * f.k + f.x).toBeGreaterThan(0);
      expect(p.x * f.k + f.x).toBeLessThan(800);
      expect(p.y * f.k + f.y).toBeGreaterThan(0);
      expect(p.y * f.k + f.y).toBeLessThan(400);
    }
  });
});
