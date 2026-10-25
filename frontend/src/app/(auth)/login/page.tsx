"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import { Logo } from "../../../components/ui/Logo";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [magicSent, setMagicSent] = useState(false);
  const [demoMode, setDemoMode] = useState(false);

  useEffect(() => {
    api.auth
      .registration()
      .then((reg) => {
        if (reg?.demo_mode) setDemoMode(true);
      })
      .catch(() => {});
  }, []);

  const handleDemoSignIn = async () => {
    setLoading(true);
    setError("");
    try {
      await api.auth.demo();
      router.replace("/digest");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Demo sign-in failed");
    } finally {
      setLoading(false);
    }
  };

  const sendMagicLink = async () => {
    if (!email) {
      setError("Enter your email first.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await api.auth.magicLinkRequest(email);
      setMagicSent(true);
    } catch {
      // Non-enumerating: show the same success either way.
      setMagicSent(true);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      // Credentials arrive as httpOnly cookies (#103); nothing to store here.
      await api.auth.login(email, password);
      router.replace("/digest");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 dark:bg-white/[0.03]">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <Logo size="lg" />
          <p className="mt-2 text-sm text-stone-500 dark:text-stone-400">Sign in to your account</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="card space-y-4 p-6"
        >
          <div>
            <label htmlFor="login-email" className="mb-1.5 block text-sm font-medium text-stone-700 dark:text-stone-200">
              Email
            </label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="input"
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label htmlFor="login-password" className="mb-1.5 block text-sm font-medium text-stone-700 dark:text-stone-200">
              Password
            </label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="input"
            />
          </div>
          {error && (
            <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full py-2.5"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>

          <div className="pt-1 text-center">
            {magicSent ? (
              <p className="text-sm text-emerald-700 dark:text-emerald-300">
                Check your email for a sign-in link.
              </p>
            ) : (
              <button
                type="button"
                onClick={sendMagicLink}
                disabled={loading}
                className="text-sm text-stone-500 underline-offset-2 hover:text-prism-600 hover:underline dark:text-stone-400 dark:hover:text-prism-200"
              >
                Email me a sign-in link instead
              </button>
            )}
          </div>

          {demoMode && (
            <div className="pt-1">
              <div className="relative my-3">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-stone-200 dark:border-white/10" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-white px-2 text-stone-400 dark:bg-[var(--bg-elevated)]">or</span>
                </div>
              </div>
              <button
                type="button"
                onClick={handleDemoSignIn}
                disabled={loading}
                className="btn-secondary w-full py-2.5 text-center font-medium"
              >
                Try the demo
              </button>
            </div>
          )}
        </form>

        <p className="mt-6 text-center text-sm text-stone-500 dark:text-stone-400">
          No account?{" "}
          <a href="/register" className="font-medium text-prism-600 hover:text-prism-700 dark:text-prism-300 dark:hover:text-prism-200">
            Register
          </a>
        </p>
      </div>
    </main>
  );
}
