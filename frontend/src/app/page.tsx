"use client";
import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { Menu, X } from "lucide-react";
import { hasSession } from "../lib/api";
import { Logo } from "../components/ui/Logo";

// Atmospheric hero imagery the spotlight reveals, served from this origin:
// the hotlinked image proxy set a third-party cookie, told it about every
// visitor, and sent the 4.3 MB original PNG for image 1 (WebP, 99 KB now).
// Swap here to retheme without touching the mechanic.
const BG_IMAGE_1 = "/landing/hero-1.webp";
const BG_IMAGE_2 = "/landing/hero-2.webp";

// Radius of the soft circular mask that reveals the second image.
const SPOTLIGHT_R = 260;

// Center-nav links point at the real existing app routes.
const NAV_LINKS = [
  { label: "Digest", href: "/digest", active: true },
  { label: "Sources", href: "/sources", active: false },
  { label: "Creators", href: "/creators", active: false },
  { label: "Search", href: "/search", active: false },
  { label: "Feed", href: "/feed", active: false },
];

/**
 * RevealLayer — draws a soft radial-gradient mask onto a hidden canvas each
 * frame, then applies it (as a data URL) to the reveal <div>. The second image
 * (BG_IMAGE_2) is only visible inside the glowing circle that trails the cursor.
 */
function RevealLayer({
  image,
  cursorX,
  cursorY,
  revealAll = false,
}: {
  image: string;
  cursorX: number;
  cursorY: number;
  revealAll?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const revealRef = useRef<HTMLDivElement | null>(null);

  // Size the hidden canvas to the viewport on mount + resize.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const size = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    size();
    window.addEventListener("resize", size);
    return () => window.removeEventListener("resize", size);
  }, []);

  // Rebuild the radial-gradient mask every render (cursor moved).
  useEffect(() => {
    const canvas = canvasRef.current;
    const reveal = revealRef.current;
    if (!canvas || !reveal) return;
    // Static-reveal mode (touch / reduced-motion): show the whole hero, no
    // per-frame canvas work (audit 09-5).
    if (revealAll) {
      reveal.style.maskImage = "none";
      reveal.style.webkitMaskImage = "none";
      return;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const grad = ctx.createRadialGradient(
      cursorX,
      cursorY,
      0,
      cursorX,
      cursorY,
      SPOTLIGHT_R,
    );
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.4, "rgba(255,255,255,1)");
    grad.addColorStop(0.6, "rgba(255,255,255,0.75)");
    grad.addColorStop(0.75, "rgba(255,255,255,0.4)");
    grad.addColorStop(0.88, "rgba(255,255,255,0.12)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cursorX, cursorY, SPOTLIGHT_R, 0, Math.PI * 2);
    ctx.fill();

    const url = canvas.toDataURL();
    reveal.style.maskImage = `url(${url})`;
    reveal.style.webkitMaskImage = `url(${url})`;
    reveal.style.maskSize = "100% 100%";
    reveal.style.webkitMaskSize = "100% 100%";
  }, [cursorX, cursorY, revealAll]);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 pointer-events-none"
        style={{ display: "none" }}
      />
      <div
        ref={revealRef}
        className="absolute inset-0 bg-center bg-cover bg-no-repeat z-30 pointer-events-none"
        style={{ backgroundImage: image ? `url(${image})` : "none" }}
      />
    </>
  );
}

export default function RootPage() {
  const router = useRouter();

  // Spotlight tracking: raw mouse ref + eased (lerped) ref, RAF-driven.
  const mouse = useRef({ x: -999, y: -999 });
  const smooth = useRef({ x: -999, y: -999 });
  const rafRef = useRef<number | null>(null);
  const [cursorPos, setCursorPos] = useState({ x: -999, y: -999 });

  const [menuOpen, setMenuOpen] = useState(false);
  const [staticReveal, setStaticReveal] = useState(false);
  // The spotlight image is only visible under the cursor (or fully on touch /
  // reduced motion): load it when it can first be seen, not with the page.
  const [revealReady, setRevealReady] = useState(false);

  useEffect(() => {
    hasSession().then((signedIn) => {
      if (signedIn) router.replace("/digest");
    });
  }, [router]);

  useEffect(() => {
    // Degrade gracefully (audit 09-5): on touch devices (no fine pointer) or
    // when the user prefers reduced motion, skip the RAF spotlight loop and its
    // per-frame canvas work entirely — just reveal the hero statically.
    const reduce =
      typeof window !== "undefined" &&
      (window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
        window.matchMedia("(pointer: coarse)").matches);

    if (reduce) {
      queueMicrotask(() => setStaticReveal(true));
      return;
    }

    const onMove = (e: MouseEvent) => {
      mouse.current.x = e.clientX;
      mouse.current.y = e.clientY;
      setRevealReady(true);
    };
    window.addEventListener("mousemove", onMove);

    const tick = () => {
      smooth.current.x += (mouse.current.x - smooth.current.x) * 0.1;
      smooth.current.y += (mouse.current.y - smooth.current.y) * 0.1;
      setCursorPos({ x: smooth.current.x, y: smooth.current.y });
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener("mousemove", onMove);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return (
    <div
      className="min-h-screen bg-white tracking-[-0.02em]"
      style={{ fontFamily: "var(--font-sans)" }}
    >
      {/* ===== Navigation (fixed, over hero) ===== */}
      <nav className="fixed top-0 left-0 right-0 z-[100] flex items-center justify-between p-4 sm:p-5">
        {/* Left: prism logo + wordmark */}
        <Logo />

        {/* Center pill (desktop) */}
        <div className="hidden md:flex absolute left-1/2 -translate-x-1/2 bg-white/20 backdrop-blur-md border border-white/30 rounded-full px-2 py-2 items-center gap-1">
          {NAV_LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                link.active
                  ? "text-white"
                  : "text-white/80 hover:bg-white/20 hover:text-white"
              }`}
            >
              {link.label}
            </a>
          ))}
        </div>

        {/* Right (desktop) */}
        <a
          href="/register"
          className="hidden md:block bg-white text-gray-900 text-sm font-semibold px-6 py-2.5 rounded-full hover:bg-gray-100"
        >
          Get started
        </a>

        {/* Right (mobile) — hamburger */}
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="md:hidden text-white p-2"
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
        >
          {menuOpen ? <X size={24} /> : <Menu size={24} />}
        </button>
      </nav>

      {/* Mobile dropdown */}
      {menuOpen && (
        <div className="fixed top-16 left-4 right-4 z-[100] md:hidden bg-black/80 backdrop-blur-md border border-white/20 rounded-2xl p-4 flex flex-col gap-1">
          {NAV_LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              onClick={() => setMenuOpen(false)}
              className={`px-4 py-2.5 rounded-full text-sm font-medium ${
                link.active ? "text-white" : "text-white/80 hover:text-white"
              }`}
            >
              {link.label}
            </a>
          ))}
          <div className="my-1 h-px bg-white/15" />
          <a
            href="/login"
            onClick={() => setMenuOpen(false)}
            className="px-4 py-2.5 rounded-full text-sm font-medium text-white/80 hover:text-white"
          >
            Sign in
          </a>
          <a
            href="/register"
            onClick={() => setMenuOpen(false)}
            className="px-4 py-2.5 rounded-full text-sm font-semibold text-center bg-white text-gray-900"
          >
            Get started
          </a>
        </div>
      )}

      {/* ===== Hero section ===== */}
      <section
        className="relative w-full overflow-hidden bg-black"
        style={{ height: "100dvh" }}
      >
        {/* 1. Base image */}
        <div
          className="absolute inset-0 bg-center bg-cover bg-no-repeat hero-zoom z-10"
          style={{ backgroundImage: `url(${BG_IMAGE_1})` }}
        />

        {/* 2. Reveal layer (cursor spotlight) */}
        <RevealLayer
          image={revealReady || staticReveal ? BG_IMAGE_2 : ""}
          cursorX={cursorPos.x}
          cursorY={cursorPos.y}
          revealAll={staticReveal}
        />

        {/* 3. Heading */}
        <div className="absolute top-[14%] left-0 right-0 flex flex-col items-center text-center px-5 pointer-events-none z-50">
          <h1 className="text-white leading-[0.95]">
            <span
              className="block font-playfair italic font-normal text-5xl sm:text-7xl md:text-8xl hero-anim hero-reveal"
              style={{ letterSpacing: "-0.05em", animationDelay: "0.25s" }}
            >
              Everything you follow,
            </span>
            <span
              className="block font-serif font-normal not-italic text-5xl sm:text-7xl md:text-8xl -mt-1 hero-anim hero-reveal"
              style={{ letterSpacing: "-0.08em", animationDelay: "0.42s" }}
            >
              ranked for you.
            </span>
          </h1>
        </div>

        {/* 4. Bottom-left paragraph */}
        <div className="hidden sm:block absolute bottom-14 left-10 md:left-14 max-w-[260px] z-50 hero-anim hero-fade" style={{ animationDelay: "0.7s" }}>
          <p className="text-sm text-white/80 leading-relaxed">
            Every signal (scroll depth, source trust, the topics you return to)
            feeds a model that learns what&apos;s worth your attention. Not
            chronology. Not popularity. Relevance.
          </p>
        </div>

        {/* 5. Bottom-right block */}
        <div
          className="absolute bottom-10 sm:bottom-24 left-5 right-5 sm:left-auto sm:right-10 md:right-14 max-w-full sm:max-w-[260px] flex flex-col items-start gap-4 sm:gap-5 z-50 hero-anim hero-fade"
          style={{ animationDelay: "0.85s" }}
        >
          <p className="text-xs sm:text-sm text-white/80 leading-relaxed">
            Tap “Why this?” on any item to see exactly which of the eight signals
            drove its ranking, and how strongly. One score, and it&apos;s yours.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <a
              href="/register"
              className="bg-[#b45309] hover:bg-[#9a4207] text-white text-sm font-medium px-7 py-3 rounded-full transition-all hover:scale-[1.03] active:scale-95 hover:shadow-lg hover:shadow-[#b45309]/30"
            >
              Start free trial
            </a>
            <a
              href="https://mohitmishra786.github.io/weirling/DEPLOYMENT/"
              className="text-sm text-white/80 underline-offset-4 hover:text-white hover:underline"
            >
              Self-host instead
            </a>
          </div>
        </div>
      </section>

      <LandingSections />
    </div>
  );
}

const SELF_HOST_URL = "https://mohitmishra786.github.io/weirling/DEPLOYMENT/";

// Below the hero (#146): who it's for, a real digest, prices, and the
// honest limits. Copy avoids the PRODUCT_STRATEGY "claims to avoid": no
// proven-better claims, no "every platform", no paywall bypass.
function LandingSections() {
  const steps = [
    {
      title: "Bring everything you follow",
      body: "Forward your newsletters to your own Weirling address, import your feeds, and add the writers you follow on Substack, YouTube, Medium, Bluesky, Mastodon and GitHub.",
    },
    {
      title: "It reads all of it",
      body: "Overnight, Weirling fetches and de-duplicates it, groups stories that several sources cover into one card, and summarizes what's worth your time.",
    },
    {
      title: "You get one short digest",
      body: "Each morning: the few items you're most likely to finish, ranked by how you actually read, with a plain reason for every pick.",
    },
  ];
  return (
    <div className="bg-black text-white">
      <section className="mx-auto max-w-5xl px-6 py-24" aria-labelledby="how-heading">
        <p className="text-xs font-semibold uppercase tracking-widest text-[#f08a24]">
          For people drowning in newsletters and feeds
        </p>
        <h2 id="how-heading" className="mt-3 font-playfair text-4xl italic sm:text-5xl">
          Read less. Keep what matters.
        </h2>
        <ol className="mt-12 grid gap-8 sm:grid-cols-3">
          {steps.map((step, i) => (
            <li key={step.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <span className="font-playfair text-3xl italic text-[#f08a24]">{i + 1}</span>
              <h3 className="mt-3 text-lg font-semibold">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-white/70">{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24" aria-labelledby="video-heading">
        <h2 id="video-heading" className="font-playfair text-4xl italic sm:text-5xl">
          See it in 40 seconds
        </h2>
        <DemoVideo />
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24" aria-labelledby="example-heading">
        <h2 id="example-heading" className="font-playfair text-4xl italic sm:text-5xl">
          A real digest, with its reasons
        </h2>
        <p className="mt-4 max-w-2xl text-white/70">
          From the demo account: ranked items with summaries, and the &ldquo;Why this?&rdquo;
          panel showing which signals put each one there. It learns from what you open,
          finish, skip and rate. You can see and edit what it has learned.
        </p>
        <div className="mt-10 grid gap-6 md:grid-cols-2 [&>*]:min-w-0">
          <Image
            src="/landing/example-digest.png"
            alt="A Weirling digest: ranked articles with AI summaries and scores"
            width={1280}
            height={800}
            sizes="(min-width: 768px) 488px, 92vw"
            className="h-auto w-full rounded-2xl border border-white/10"
          />
          <Image
            src="/landing/example-why.png"
            alt="The Why this? panel: the share of each reading signal behind an item's rank"
            width={1280}
            height={800}
            sizes="(min-width: 768px) 488px, 92vw"
            className="h-auto w-full rounded-2xl border border-white/10"
          />
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24" aria-labelledby="pricing-heading">
        <h2 id="pricing-heading" className="font-playfair text-4xl italic sm:text-5xl">
          Simple pricing
        </h2>
        <p className="mt-4 text-white/70">
          30 days free with everything in Pro. No card needed to start.
        </p>
        <div className="mt-10 grid gap-6 md:grid-cols-3">
          <PlanCard
            name="Reader"
            price="$6"
            yearly="or $54 a year"
            points={["500 feeds", "200 AI summaries a day", "100 newsletters a day", "Daily or weekly digest"]}
          />
          <PlanCard
            name="Pro"
            price="$12"
            yearly="or $108 a year"
            points={[
              "2,000 feeds",
              "1,000 AI summaries a day",
              "Twice-daily digests and story synthesis",
              "API and MCP access, priority fetching",
            ]}
            highlight
          />
          <PlanCard
            name="Self-hosted"
            price="Free"
            yearly="MIT licensed, on your server"
            points={["Every feature, no limits", "Your own LLM key, or local models", "One Docker Compose file"]}
            href={SELF_HOST_URL}
            cta="Self-host it"
          />
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24" aria-labelledby="honest-heading">
        <h2 id="honest-heading" className="font-playfair text-3xl italic">
          What it won&apos;t do
        </h2>
        <ul className="mt-6 grid gap-4 text-sm text-white/70 md:grid-cols-3">
          <li className="rounded-2xl border border-white/10 p-5">
            X, LinkedIn and Reddit have no public feeds, so they aren&apos;t supported. We say so
            instead of failing quietly.
          </li>
          <li className="rounded-2xl border border-white/10 p-5">
            No paywall tricks. You get the feed text, a summary and a link to the original.
          </li>
          <li className="rounded-2xl border border-white/10 p-5">
            No ads, no selling your reading data, and no third-party trackers.
          </li>
        </ul>
        <div className="mt-12 flex flex-wrap items-center gap-6">
          <a
            href="/register"
            className="bg-[#b45309] hover:bg-[#9a4207] text-white text-sm font-medium px-7 py-3 rounded-full"
          >
            Start free trial
          </a>
          <a href={SELF_HOST_URL} className="text-sm text-white/80 underline-offset-4 hover:underline">
            Self-host instead
          </a>
        </div>
      </section>

      <footer className="border-t border-white/10">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-8 text-sm text-white/60">
          <Logo />
          <nav aria-label="Footer" className="flex flex-wrap gap-x-3 gap-y-1">
            {[
              ["/how-it-works", "How it works"],
              ["/vs/feedly", "Compare"],
              ["/privacy", "Privacy"],
              ["/terms", "Terms"],
              ["https://github.com/mohitmishra786/weirling", "GitHub"],
            ].map(([href, label]) => (
              <a key={href} href={href} className="inline-block px-2 py-2 hover:text-white">
                {label}
              </a>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  );
}

// #213: the product in use, narrated, captions burned in (works muted). The
// poster is a lazy image and the video loads only on click, so the section
// costs nothing on page load.
function DemoVideo() {
  const [playing, setPlaying] = useState(false);
  if (playing) {
    return (
      <video
        className="mt-8 w-full rounded-2xl border border-white/10"
        controls
        autoPlay
        playsInline
        width={1920}
        height={1080}
        src="/landing/weirling-demo.mp4"
      >
        A 40-second tour of Weirling: the digest, the reasons behind each pick, and what it learns.
      </video>
    );
  }
  return (
    <button
      type="button"
      onClick={() => setPlaying(true)}
      className="group relative mt-8 block w-full overflow-hidden rounded-2xl border border-white/10"
      aria-label="Play the 40-second Weirling tour"
    >
      <Image
        src="/landing/weirling-demo.jpg"
        alt=""
        width={1280}
        height={720}
        sizes="(min-width: 1024px) 976px, 100vw"
        className="w-full"
      />
      <span className="absolute inset-0 flex items-center justify-center">
        <span className="flex h-20 w-20 items-center justify-center rounded-full bg-[#b45309] shadow-lg shadow-black/40 transition-transform group-hover:scale-105">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="#fff" aria-hidden="true">
            <path d="M8 5v14l11-7z" />
          </svg>
        </span>
      </span>
    </button>
  );
}

function PlanCard({
  name,
  price,
  yearly,
  points,
  highlight = false,
  href = "/register",
  cta = "Start free trial",
}: {
  name: string;
  price: string;
  yearly: string;
  points: string[];
  highlight?: boolean;
  href?: string;
  cta?: string;
}) {
  return (
    <div
      className={`flex flex-col rounded-2xl border p-6 ${
        highlight ? "border-[#b45309] bg-[#b45309]/10" : "border-white/10 bg-white/[0.03]"
      }`}
    >
      <h3 className="text-lg font-semibold">{name}</h3>
      <p className="mt-3">
        <span className="font-playfair text-4xl italic">{price}</span>
        {price.startsWith("$") && <span className="text-white/60"> a month</span>}
      </p>
      <p className="text-sm text-white/60">{yearly}</p>
      <ul className="mt-5 flex-1 space-y-2 text-sm text-white/80">
        {points.map((pt) => (
          <li key={pt}>{pt}</li>
        ))}
      </ul>
      <a
        href={href}
        className={`mt-6 rounded-full px-5 py-2.5 text-center text-sm font-medium ${
          highlight ? "bg-[#b45309] text-white hover:bg-[#9a4207]" : "bg-white text-black hover:bg-white/90"
        }`}
      >
        {cta}
      </a>
    </div>
  );
}
