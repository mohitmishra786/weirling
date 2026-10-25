"use client";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { createSim, fit, nodeAt, radiusFor, reheat, settle, step, type Sim } from "../../lib/graphSim";

export type GraphNode = {
  id: string;
  label: string;
  weight: number;
  /** Core stable interest: drawn in the accent colour. */
  core?: boolean;
  /** Reinforced recently: drawn with a ring. */
  recent?: boolean;
  /** Muted: drawn dim. */
  muted?: boolean;
};
export type GraphLink = { source: string; target: string; weight: number };

export type ForceGraphHandle = {
  /** The current picture as an SVG document, for "Export image". */
  toSvg: () => string;
  /** Where each topic is drawn, in CSS pixels relative to the canvas. */
  screenPositions: () => Record<string, { x: number; y: number }>;
};

const COLORS = {
  node: "#8f8f89",
  core: "#f08a24",
  muted: "#3a3a3a",
  ring: "#fbbf24",
  edge: "rgba(255,255,255,0.10)",
  edgeLit: "rgba(240,138,36,0.75)",
  label: "#e7e5e4",
  labelDim: "#a8a29e",
  selected: "#fde3bd",
};
const LABEL_MAX = 26;
const LABELS_AT_REST = 12;

function clip(text: string, full: boolean) {
  return full || text.length <= LABEL_MAX ? text : `${text.slice(0, LABEL_MAX - 1).trimEnd()}…`;
}

function escapeXml(s: string) {
  return s.replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[c]!);
}

/** Canvas height for a given width: the prop is the most it may be, and
 *  phones get a shorter graph so the page around it stays reachable. */
function heightFor(width: number, max: number) {
  return width > 0 ? Math.min(max, Math.max(300, Math.round(width * 0.8))) : max;
}

function reducedMotion() {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Topic graph drawn on a canvas, after Obsidian's graph view: a live force
 * layout that springs open, labels that fade in as you zoom, and hovering a
 * topic lights up it and its neighbours. Drag to pan, scroll or pinch to
 * zoom, drag a topic to move it, click to select, drop one topic on another
 * to merge (when `onDropOnto` is given). The lists next to it carry the same
 * actions for keyboard and screen-reader users.
 */
export const ForceGraph = forwardRef<ForceGraphHandle, {
  nodes: GraphNode[];
  links: GraphLink[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  onDropOnto?: (from: string, into: string) => void;
  height?: number;
  label: string;
}>(function ForceGraph({ nodes, links, selected = null, onSelect, onDropOnto, height = 460, label }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const key = useMemo(
    () => JSON.stringify([nodes.map((n) => [n.id, n.weight]), links.map((l) => [l.source, l.target])]),
    [nodes, links],
  );
  const simRef = useRef<Sim | null>(null);
  const view = useRef({ k: 1, x: 0, y: 0, fitK: 1, userMoved: false });
  const hover = useRef(-1);
  const props = useRef({ nodes, selected, onSelect, onDropOnto });
  props.current = { nodes, selected, onSelect, onDropOnto };
  const frame = useRef(0);
  const draw = useRef<() => void>(() => {});
  // Zoom buttons for touch screens, which have no wheel.
  const zoomBy = useRef<(factor: number) => void>(() => {});
  const fitView = useRef<() => void>(() => {});

  // Rebuild the simulation when the graph itself changes (not on selection).
  useEffect(() => {
    simRef.current = createSim(
      nodes.map((n) => ({ id: n.id, r: radiusFor(n.weight) })),
      links,
    );
    view.current.userMoved = false;
    hover.current = -1;
    const w = wrapRef.current?.clientWidth ?? 0;
    if (w > 0) simRef.current.aspect = Math.max(0.5, Math.min(3, w / heightFor(w, height)));
    if (reducedMotion()) settle(simRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, height]);

  useImperativeHandle(ref, () => ({
    screenPositions() {
      const { k, x, y } = view.current;
      return Object.fromEntries(
        (simRef.current?.nodes ?? []).map((p) => [p.id, { x: p.x * k + x, y: p.y * k + y }]),
      );
    },
    toSvg() {
      const sim = simRef.current;
      if (!sim) return "";
      settle(sim);
      const f = fit(sim, 1000, 700, 60, 2);
      const tx = (x: number) => (x * f.k + f.x).toFixed(1);
      const ty = (y: number) => (y * f.k + f.y).toFixed(1);
      const parts = [
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700" font-family="Inter, sans-serif">`,
        `<rect width="1000" height="700" fill="#0f0f0f"/>`,
      ];
      for (const l of sim.links) {
        const a = sim.nodes[l.s], b = sim.nodes[l.t];
        parts.push(`<line x1="${tx(a.x)}" y1="${ty(a.y)}" x2="${tx(b.x)}" y2="${ty(b.y)}" stroke="#ffffff" stroke-opacity="0.15"/>`);
      }
      sim.nodes.forEach((p, i) => {
        const n = props.current.nodes[i];
        const fill = n?.muted ? COLORS.muted : n?.core ? COLORS.core : COLORS.node;
        parts.push(`<circle cx="${tx(p.x)}" cy="${ty(p.y)}" r="${(p.r * f.k).toFixed(1)}" fill="${fill}"/>`);
        parts.push(
          `<text x="${tx(p.x)}" y="${(p.y * f.k + f.y + p.r * f.k + 14).toFixed(1)}" fill="${COLORS.label}" font-size="12" text-anchor="middle">${escapeXml(n?.label ?? p.id)}</text>`,
        );
      });
      parts.push("</svg>");
      return parts.join("");
    },
  }));

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // jsdom and very old browsers: the lists still work

    let width = 0;
    let h = height;
    const applyFit = (sim: Sim) => {
      const f = fit(sim, width, h);
      Object.assign(view.current, f, { fitK: f.k || 1 });
    };
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      width = wrap.clientWidth;
      h = heightFor(width, height);
      if (simRef.current && width > 0) simRef.current.aspect = Math.max(0.5, Math.min(3, width / h));
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${h}px`;
      if (!view.current.userMoved && simRef.current) applyFit(simRef.current);
      draw.current();
    };

    draw.current = () => {
      const sim = simRef.current;
      if (!sim) return;
      const { k, x, y } = view.current;
      const { nodes: meta, selected: sel } = props.current;
      const lit = hover.current >= 0 ? hover.current : sel ? sim.nodes.findIndex((p) => p.id === sel) : -1;
      const near = lit >= 0 ? sim.neighbors[lit] : null;
      const isLit = (i: number) => lit < 0 || i === lit || Boolean(near?.has(i));

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, h);
      ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * x, dpr * y);

      // Links: faint, the hovered topic's links in the accent.
      for (const l of sim.links) {
        const a = sim.nodes[l.s], b = sim.nodes[l.t];
        const on = lit >= 0 && (l.s === lit || l.t === lit);
        ctx.strokeStyle = on ? COLORS.edgeLit : COLORS.edge;
        ctx.globalAlpha = lit >= 0 && !on ? 0.35 : 1;
        ctx.lineWidth = (on ? 1.6 : 0.6 + l.w) / k;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }

      // Nodes.
      sim.nodes.forEach((p, i) => {
        const n = meta[i];
        ctx.globalAlpha = isLit(i) ? 1 : 0.18;
        if (n?.recent) {
          ctx.strokeStyle = COLORS.ring;
          ctx.lineWidth = 2 / k;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r + 3.5 / k, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.fillStyle = n?.muted ? COLORS.muted : i === lit || n?.core ? COLORS.core : COLORS.node;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
        if (n && n.id === sel) {
          ctx.strokeStyle = COLORS.selected;
          ctx.lineWidth = 2 / k;
          ctx.stroke();
        }
      });

      // Labels: constant on-screen size; small topics fade in as you zoom in,
      // and the hovered topic and its neighbours always show theirs in full.
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.font = `${12 / k}px Inter, -apple-system, sans-serif`;
      // Like Obsidian: at the fitted zoom only the strongest topics are
      // named; the rest fade in as you zoom in, or when you hover nearby.
      const radii = sim.nodes.map((p) => p.r).sort((a, b) => b - a);
      // About one name per 90 px of width: 12 on a laptop, 4 on a phone.
      const atRest = Math.max(4, Math.min(LABELS_AT_REST, Math.round(width / 90)));
      const named = radii[Math.min(radii.length - 1, atRest - 1)] ?? 0;
      const zoomFade = Math.max(0, Math.min(1, (k / view.current.fitK - 1.15) / 0.5));
      // Place labels by priority (hovered topic, its neighbours, then the
      // biggest topics) and skip any that would overlap one already placed:
      // a clean picture beats a complete one, and hover shows the rest.
      const order = sim.nodes
        .map((p, i) => ({ p, i, rank: i === lit ? 3 : lit >= 0 && isLit(i) ? 2 : 0 }))
        .sort((a, b) => b.rank - a.rank || b.p.r - a.p.r);
      // Circles count as taken space too, so a name never covers a topic.
      const placed: { x: number; y: number; w: number; h: number; owner?: number }[] = sim.nodes.map(
        (p, i) => ({ x: p.x - p.r, y: p.y - p.r, w: p.r * 2, h: p.r * 2, owner: i }),
      );
      const lineH = 15 / k;
      for (const { p, i } of order) {
        const n = meta[i];
        if (!n) continue;
        const focus = lit >= 0 && isLit(i);
        const fade = focus || p.r >= named ? 1 : zoomFade;
        const alpha = (lit >= 0 && !isLit(i) ? 0.12 : 1) * fade;
        if (alpha <= 0.02) continue;
        const text = clip(n.label, i === lit);
        const w = ctx.measureText(text).width;
        const box = { x: p.x - w / 2 - 3 / k, y: p.y + p.r + 3 / k, w: w + 6 / k, h: lineH };
        const hits = placed.some(
          (b) => b.owner !== i && box.x < b.x + b.w && b.x < box.x + box.w && box.y < b.y + b.h && b.y < box.y + box.h,
        );
        if (hits && i !== lit) continue;
        placed.push(box);
        ctx.globalAlpha = alpha;
        ctx.fillStyle = i === lit ? COLORS.label : COLORS.labelDim;
        ctx.fillText(text, p.x, p.y + p.r + 4 / k);
      }
      ctx.globalAlpha = 1;
    };

    const loop = () => {
      const sim = simRef.current;
      if (sim && step(sim)) {
        if (!view.current.userMoved) applyFit(sim);
        draw.current();
        frame.current = requestAnimationFrame(loop);
      } else {
        draw.current();
        frame.current = 0;
      }
    };
    const wake = () => {
      if (!frame.current) frame.current = requestAnimationFrame(loop);
    };

    // ---- interaction ----
    const toGraph = (cx: number, cy: number) => {
      const r = canvas.getBoundingClientRect();
      const { k, x, y } = view.current;
      return { x: (cx - r.left - x) / k, y: (cy - r.top - y) / k };
    };
    let drag: { mode: "node" | "pan"; index: number; sx: number; sy: number; moved: boolean; ox: number; oy: number } | null = null;

    const onDown = (e: PointerEvent) => {
      const sim = simRef.current;
      if (!sim) return;
      canvas.setPointerCapture?.(e.pointerId);
      const g = toGraph(e.clientX, e.clientY);
      const i = nodeAt(sim, g.x, g.y, 4 / view.current.k);
      drag = { mode: i >= 0 ? "node" : "pan", index: i, sx: e.clientX, sy: e.clientY, moved: false, ox: view.current.x, oy: view.current.y };
      if (i >= 0) sim.nodes[i].fixed = true;
    };
    const onMove = (e: PointerEvent) => {
      const sim = simRef.current;
      if (!sim) return;
      if (!drag) {
        const g = toGraph(e.clientX, e.clientY);
        const i = nodeAt(sim, g.x, g.y, 4 / view.current.k);
        if (i !== hover.current) {
          hover.current = i;
          canvas.style.cursor = i >= 0 ? "pointer" : "grab";
          setHint(i >= 0 ? props.current.nodes[i]?.label ?? null : null);
          draw.current();
        }
        return;
      }
      const dist = Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy);
      if (dist > 4) drag.moved = true;
      if (drag.mode === "pan") {
        view.current.x = drag.ox + (e.clientX - drag.sx);
        view.current.y = drag.oy + (e.clientY - drag.sy);
        view.current.userMoved = true;
        canvas.style.cursor = "grabbing";
        draw.current();
      } else if (drag.moved) {
        const g = toGraph(e.clientX, e.clientY);
        const p = sim.nodes[drag.index];
        p.x = g.x;
        p.y = g.y;
        view.current.userMoved = true;
        reheat(sim, 0.25);
        wake();
      }
    };
    const onUp = (e: PointerEvent) => {
      const sim = simRef.current;
      const d = drag;
      drag = null;
      canvas.style.cursor = hover.current >= 0 ? "pointer" : "grab";
      if (!sim || !d || d.mode !== "node") return;
      sim.nodes[d.index].fixed = false;
      const id = sim.nodes[d.index].id;
      if (!d.moved) {
        props.current.onSelect?.(id);
        return;
      }
      const g = toGraph(e.clientX, e.clientY);
      const target = nodeAt(sim, g.x, g.y, 2 / view.current.k, d.index);
      if (target >= 0 && props.current.onDropOnto) props.current.onDropOnto(id, sim.nodes[target].id);
      reheat(sim, 0.2);
      wake();
    };
    const onLeave = () => {
      if (hover.current !== -1) {
        hover.current = -1;
        setHint(null);
        draw.current();
      }
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const v = view.current;
      const k = Math.max(0.25, Math.min(4, v.k * Math.exp(-e.deltaY * 0.0015)));
      v.x = mx - ((mx - v.x) * k) / v.k;
      v.y = my - ((my - v.y) * k) / v.k;
      v.k = k;
      v.userMoved = true;
      draw.current();
    };
    const onDouble = () => {
      if (!simRef.current) return;
      view.current.userMoved = false;
      applyFit(simRef.current);
      draw.current();
    };
    fitView.current = onDouble;
    zoomBy.current = (factor: number) => {
      const v = view.current;
      const k = Math.max(0.25, Math.min(4, v.k * factor));
      const cx = width / 2;
      const cy = h / 2;
      v.x = cx - ((cx - v.x) * k) / v.k;
      v.y = cy - ((cy - v.y) * k) / v.k;
      v.k = k;
      v.userMoved = true;
      draw.current();
    };

    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", onDouble);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(wrap);
    resize();
    wake();
    return () => {
      cancelAnimationFrame(frame.current);
      frame.current = 0;
      observer?.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("dblclick", onDouble);
    };
  }, [key, height]);

  // Selection changes only need a redraw.
  useEffect(() => {
    draw.current();
  }, [selected, nodes]);

  return (
    <div ref={wrapRef} className="relative overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)]">
      {/* Touch: vertical swipes still scroll the page (a full-width graph
          must not trap the reader); a mouse gets the whole canvas. */}
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={label}
        className="block w-full touch-pan-y select-none [@media(pointer:fine)]:touch-none"
        style={{ height: Math.min(height, 420), cursor: "grab" }}
      />
      <div className="absolute right-2 top-2 flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)]/90 backdrop-blur">
        {[
          { label: "Zoom in", text: "+", run: () => zoomBy.current(1.35) },
          { label: "Zoom out", text: "−", run: () => zoomBy.current(1 / 1.35) },
          { label: "Fit the graph", text: "⤢", run: () => fitView.current() },
        ].map((b) => (
          <button
            key={b.label}
            type="button"
            aria-label={b.label}
            title={b.label}
            onClick={b.run}
            className="h-9 w-9 border-b border-[var(--border)] text-base leading-none text-[var(--text-secondary)] last:border-b-0 hover:bg-[var(--bg-sunken)] hover:text-[var(--text)]"
          >
            {b.text}
          </button>
        ))}
      </div>
      <div className="pointer-events-none absolute bottom-2 left-3 right-3 flex justify-between gap-3 text-[11px] text-[var(--text-tertiary)]">
        <span className="truncate text-[var(--text-secondary)]">{hint ?? ""}</span>
        <span className="hidden shrink-0 sm:inline">Scroll to zoom · drag to move · double-click to fit</span>
      </div>
    </div>
  );
});
