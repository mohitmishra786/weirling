"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, hasSession } from "../../lib/api";
import { AccountNotices } from "../../components/AccountNotices";
import { Logo } from "../../components/ui/Logo";

const NAV = [
  { href: "/digest", label: "Digest" },
  { href: "/feed", label: "Feed" },
  { href: "/sources", label: "Sources" },
  { href: "/creators", label: "Creators" },
  { href: "/search", label: "Search" },
  { href: "/interests", label: "Interests" },
  { href: "/preferences", label: "Preferences" },
];
// Instance pages, shown to the instance owner only.
const OWNER_NAV = [{ href: "/admin/ingestion", label: "Ingestion" }];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);
  const [owner, setOwner] = useState(false);

  useEffect(() => {
    // Session lives in httpOnly cookies (#103): ask the API instead of
    // reading a token in JavaScript.
    let cancelled = false;
    hasSession().then((signedIn) => {
      if (cancelled) return;
      if (!signedIn) {
        router.replace("/login");
        return;
      }
      setMounted(true);
      api.metrics
        .instanceHealth()
        .then((h) => !cancelled && setOwner(h.owner))
        .catch(() => {});
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!mounted) return null;

  return (
    <div className="min-h-screen bg-[var(--bg)]">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      {/* Same chrome as the landing page (#159): wordmark, glass pill nav,
          pill action on the right. */}
      <header className="sticky top-0 z-40 border-b border-white/10 bg-black/70 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4">
          <Logo href="/digest" />
          <nav
            aria-label="Main"
            className="mx-auto flex items-center gap-0.5 overflow-x-auto rounded-full border border-white/15 bg-white/[0.06] p-1"
          >
            {[...NAV, ...(owner ? OWNER_NAV : [])].map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                    active ? "bg-white text-black" : "text-white/70 hover:bg-white/10 hover:text-white"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <button
            onClick={async () => {
              // Revokes the refresh token and clears the session cookies.
              await api.auth.logout().catch(() => {});
              router.replace("/login");
            }}
            className="whitespace-nowrap rounded-full border border-white/20 px-4 py-1.5 text-sm text-white/80 transition-colors hover:bg-white/10 hover:text-white"
          >
            Sign out
          </button>
        </div>
      </header>
      <AccountNotices />
      <main id="main-content" className="mx-auto max-w-3xl px-4 py-8">
        {children}
      </main>
    </div>
  );
}
