// @vitest-environment jsdom
import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ForceGraph, type ForceGraphHandle } from "../src/components/graph/ForceGraph";

beforeAll(() => {
  // jsdom has no canvas: a context whose methods do nothing lets the
  // component run its layout and pointer handling.
  const noop = () => {};
  const measure = (text: string) => ({ width: text.length * 7 });
  const ctx = new Proxy(
    {},
    { get: (_t, prop) => (prop === "measureText" ? measure : prop === "canvas" ? undefined : noop), set: () => true },
  );
  HTMLCanvasElement.prototype.getContext = (() => ctx) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 800 });
  // Reduced motion: lay out at once instead of animating.
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), addEventListener: noop, removeEventListener: noop })) as unknown as typeof window.matchMedia;
  if (typeof window.PointerEvent === "undefined") {
    class P extends MouseEvent {
      pointerId: number;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
      }
    }
    (window as unknown as { PointerEvent: typeof P }).PointerEvent = P;
  }
});

const nodes = [
  { id: "compilers", label: "compilers", weight: 0.9, core: true },
  { id: "rust", label: "rust", weight: 0.7 },
  { id: "gardening", label: "gardening", weight: 0.2, muted: true },
];
const links = [{ source: "compilers", target: "rust", weight: 0.6 }];

describe("ForceGraph", () => {
  it("is labelled for screen readers", () => {
    render(<ForceGraph nodes={nodes} links={links} label="Map of 3 interests" />);
    expect(screen.getByRole("img", { name: "Map of 3 interests" })).toBeInTheDocument();
  });

  it("selects a topic on click and merges one dropped onto another", () => {
    const ref = createRef<ForceGraphHandle>();
    const onSelect = vi.fn();
    const onDropOnto = vi.fn();
    render(<ForceGraph ref={ref} nodes={nodes} links={links} onSelect={onSelect} onDropOnto={onDropOnto} label="map" />);
    const canvas = screen.getByRole("img");
    const at = ref.current!.screenPositions();

    fireEvent.pointerDown(canvas, { clientX: at.rust.x, clientY: at.rust.y, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: at.rust.x, clientY: at.rust.y, pointerId: 1 });
    expect(onSelect).toHaveBeenCalledWith("rust");

    fireEvent.pointerDown(canvas, { clientX: at.rust.x, clientY: at.rust.y, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: at.compilers.x, clientY: at.compilers.y, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: at.compilers.x, clientY: at.compilers.y, pointerId: 1 });
    expect(onDropOnto).toHaveBeenCalledWith("rust", "compilers");
  });

  it("exports the picture as SVG with every topic", () => {
    const ref = createRef<ForceGraphHandle>();
    render(<ForceGraph ref={ref} nodes={nodes} links={links} label="map" />);
    const svg = ref.current!.toSvg();
    expect(svg.startsWith("<svg")).toBe(true);
    expect((svg.match(/<circle/g) ?? []).length).toBe(3);
    expect(svg).toContain(">gardening<");
  });
});
