// Live force simulation for the topic graphs, in the spirit of Obsidian's
// graph view: nodes start close together and spring apart ("big bang"),
// links pull related topics together, every pair repels, and a weak pull
// keeps the whole thing centred. Pure: no DOM, so it is unit-tested and the
// canvas component only draws what it computes.

export type SimNode = {
  id: string;
  r: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Pinned while the user drags it. */
  fixed: boolean;
};

export type SimLink = { s: number; t: number; w: number };

export type Sim = {
  nodes: SimNode[];
  links: SimLink[];
  alpha: number;
  neighbors: Set<number>[];
  /** Viewport width / height: the centring pull follows the canvas shape. */
  aspect: number;
};

const REPEL = 7000;
const SPRING = 0.08;
const CENTER = 0.006;
const DAMPING = 0.55;
const PADDING = 6;
export const ALPHA_MIN = 0.004;
const ALPHA_DECAY = 0.035;

export function createSim(
  nodes: { id: string; r: number }[],
  links: { source: string; target: string; weight: number }[],
): Sim {
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  // Deterministic start on a tight golden-angle spiral: the same graph always
  // opens the same way, and the spread-out is the animation.
  const simNodes = nodes.map((n, i) => {
    const angle = i * 2.399963;
    const dist = 4 * Math.sqrt(i + 1);
    return { id: n.id, r: n.r, x: Math.cos(angle) * dist, y: Math.sin(angle) * dist, vx: 0, vy: 0, fixed: false };
  });
  const simLinks: SimLink[] = [];
  for (const l of links) {
    const s = index.get(l.source);
    const t = index.get(l.target);
    if (s === undefined || t === undefined || s === t) continue;
    simLinks.push({ s, t, w: Math.max(0.05, Math.min(1, l.weight)) });
  }
  const neighbors = simNodes.map(() => new Set<number>());
  for (const l of simLinks) {
    neighbors[l.s].add(l.t);
    neighbors[l.t].add(l.s);
  }
  return { nodes: simNodes, links: simLinks, alpha: 1, neighbors, aspect: 1 };
}

/** Advance one tick. Returns false once the layout has settled. */
export function step(sim: Sim): boolean {
  const { nodes, links } = sim;
  const a = sim.alpha;
  if (a < ALPHA_MIN) return false;
  const n = nodes.length;

  // Every pair repels (inverse square), and overlapping pairs are pushed
  // apart outright so labels and circles never sit on each other.
  for (let i = 0; i < n; i++) {
    const p = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const q = nodes[j];
      let dx = q.x - p.x;
      let dy = q.y - p.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) {
        // Coincident: nudge apart deterministically.
        dx = (j - i) * 0.1;
        dy = 0.1;
        d2 = dx * dx + dy * dy;
      }
      const d = Math.sqrt(d2);
      let f = (REPEL * a) / d2;
      const min = p.r + q.r + PADDING;
      if (d < min) f += ((min - d) / d) * 0.5;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      p.vx -= fx;
      p.vy -= fy;
      q.vx += fx;
      q.vy += fy;
    }
  }

  // Links are springs whose rest length leaves room for both circles.
  for (const l of links) {
    const p = nodes[l.s];
    const q = nodes[l.t];
    const dx = q.x - p.x;
    const dy = q.y - p.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const rest = 34 + p.r + q.r;
    const f = (d - rest) * SPRING * a * (0.4 + l.w);
    const fx = (dx / d) * f;
    const fy = (dy / d) * f;
    p.vx += fx;
    p.vy += fy;
    q.vx -= fx;
    q.vy -= fy;
  }

  const xPull = CENTER / Math.sqrt(sim.aspect);
  const yPull = CENTER * Math.sqrt(sim.aspect);
  nodes.forEach((p, i) => {
    // Unlinked topics feel no springs; pull them in harder so one orphan
    // doesn't drift off and shrink everything else to fit.
    const lone = sim.neighbors[i].size === 0 ? 4 : 1;
    p.vx -= p.x * xPull * lone * a;
    p.vy -= p.y * yPull * lone * a;
    if (p.fixed) {
      p.vx = 0;
      p.vy = 0;
      return;
    }
    p.vx *= DAMPING;
    p.vy *= DAMPING;
    p.x += p.vx;
    p.y += p.vy;
  });
  sim.alpha = a * (1 - ALPHA_DECAY);
  return true;
}

/** Run to rest without animating (reduced motion, tests, export). */
export function settle(sim: Sim, maxTicks = 400): Sim {
  for (let i = 0; i < maxTicks && step(sim); i++);
  return sim;
}

/** Wake the layout after a drag, without restarting the big bang. */
export function reheat(sim: Sim, alpha = 0.3) {
  sim.alpha = Math.max(sim.alpha, alpha);
}

/** Index of the node under (x, y) in graph space, or -1. Nearest wins. */
export function nodeAt(sim: Sim, x: number, y: number, slop = 4, except = -1): number {
  let best = -1;
  let bestD = Infinity;
  sim.nodes.forEach((p, i) => {
    if (i === except) return;
    const d = Math.hypot(p.x - x, p.y - y);
    if (d <= p.r + slop && d < bestD) {
      best = i;
      bestD = d;
    }
  });
  return best;
}

export function bounds(sim: Sim, pad = 0) {
  if (sim.nodes.length === 0) return { x: -1, y: -1, w: 2, h: 2 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of sim.nodes) {
    minX = Math.min(minX, p.x - p.r);
    minY = Math.min(minY, p.y - p.r);
    maxX = Math.max(maxX, p.x + p.r);
    maxY = Math.max(maxY, p.y + p.r);
  }
  return { x: minX - pad, y: minY - pad, w: maxX - minX + 2 * pad, h: maxY - minY + 2 * pad };
}

/** Scale and offset that fit the graph in a width x height viewport. */
export function fit(sim: Sim, width: number, height: number, pad = 56, maxScale = 3) {
  const b = bounds(sim, pad);
  const k = Math.min(maxScale, width / b.w, height / b.h);
  return { k, x: width / 2 - (b.x + b.w / 2) * k, y: height / 2 - (b.y + b.h / 2) * k };
}

/** Circle radius for an interest weight (0..1). */
export function radiusFor(weight: number): number {
  return 4 + Math.sqrt(Math.max(0, Math.min(1, weight))) * 9;
}
