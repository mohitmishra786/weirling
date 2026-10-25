"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
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
  const [menuOpen, setMenuOpen] = useState(false);

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

  const items = [...NAV, ...(owner ? OWNER_NAV : [])];
  const signOut = async () => {
    // Revokes the refresh token and clears the session cookies.
    await api.auth.logout().catch(() => {});
    router.replace("/login");
  };

  return (
    <div className="min-h-screen bg-[var(--bg)]">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      {/* Same chrome as the landing page (#159): wordmark, glass pill nav,
          pill action on the right. Below lg the links move into a menu. */}
      <header className="sticky top-0 z-40 border-b border-white/10 bg-black/70 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4 sm:px-6">
          <Logo href="/digest" />
          <nav
            aria-label="Main"
            className="mx-auto hidden items-center gap-0.5 rounded-full border border-white/15 bg-white/[0.06] p-1 lg:flex"
          >
            {items.map((item) => {
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
            onClick={signOut}
            className="hidden whitespace-nowrap rounded-full border border-white/20 px-4 py-1.5 text-sm text-white/80 transition-colors hover:bg-white/10 hover:text-white lg:block"
          >
            Sign out
          </button>
          <button
            type="button"
            className="ml-auto inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/15 text-white/90 lg:hidden"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
          </button>
        </div>
        {menuOpen && (
          <nav id="mobile-nav" aria-label="Main" className="border-t border-white/10 px-4 pb-4 pt-2 lg:hidden">
            <ul className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {items.map((item) => {
                const active = pathname === item.href;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      onClick={() => setMenuOpen(false)}
                      className={`block rounded-xl px-4 py-3 text-[15px] font-medium ${
                        active ? "bg-white text-black" : "bg-white/[0.05] text-white/85 active:bg-white/10"
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
            <button
              onClick={signOut}
              className="mt-3 w-full rounded-xl border border-white/15 px-4 py-3 text-[15px] text-white/80"
            >
              Sign out
            </button>
          </nav>
        )}
      </header>
      <AccountNotices />
      <main id="main-content" className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>
    </div>
  );
}
