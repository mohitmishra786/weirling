"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import { Logo } from "../../../components/ui/Logo";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [inviteCode, setInviteCode] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  // What this instance allows (#153): closed instances accept only their owner.
  const [registration, setRegistration] = useState<{
    mode: string;
    open: boolean;
    invite_required: boolean;
    first_account?: boolean;
    hosted?: boolean;
  } | null>(null);

  useEffect(() => {
    api.auth.registration().then(setRegistration).catch(() => setRegistration(null));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      // Credentials arrive as httpOnly cookies (#103); nothing to store here.
      await api.auth.register(
        email,
        password,
        displayName || undefined,
        inviteCode || undefined,
        acceptTerms
      );
      // The first account owns the instance: finish setup next (#220).
      router.replace(registration?.first_account ? "/admin/setup" : "/digest");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 dark:bg-white/[0.03]">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <Logo size="lg" />
          <p className="mt-2 text-sm text-stone-500 dark:text-stone-400">
            {registration?.first_account
              ? "Create the owner account for this instance"
              : "Create your account"}
          </p>
        </div>

        {registration && !registration.open && !registration.invite_required ? (
          <div className="card space-y-3 p-6 text-sm text-stone-600 dark:text-stone-300">
            <p>Registration is closed on this instance.</p>
            <p>
              Ask its owner for an account, or{" "}
              <a href="/login" className="font-medium text-prism-600 hover:text-prism-700 dark:text-prism-300 dark:hover:text-prism-200">
                sign in
              </a>
              .
            </p>
          </div>
        ) : (
        <form onSubmit={handleSubmit} className="card space-y-4 p-6">
          <div>
            <label htmlFor="reg-display-name" className="mb-1.5 block text-sm font-medium text-stone-700 dark:text-stone-200">
              Display name <span className="text-stone-400">(optional)</span>
            </label>
            <input
              id="reg-display-name"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input"
              placeholder="Your name"
            />
          </div>
          <div>
            <label htmlFor="reg-email" className="mb-1.5 block text-sm font-medium text-stone-700 dark:text-stone-200">
              Email
            </label>
            <input
              id="reg-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="input"
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label htmlFor="reg-password" className="mb-1.5 block text-sm font-medium text-stone-700 dark:text-stone-200">
              Password <span className="text-stone-400">(min 8 chars)</span>
            </label>
            <input
              id="reg-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
              className="input"
            />
          </div>
          {registration?.invite_required && (
            <div>
              <label htmlFor="reg-invite" className="mb-1.5 block text-sm font-medium text-stone-700 dark:text-stone-200">
                Invite code
              </label>
              <input
                id="reg-invite"
                type="text"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                required
                className="input"
              />
            </div>
          )}
          {error && (
            <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
              {error}
            </p>
          )}
          {registration?.hosted && (
            <div className="flex items-start gap-2 pt-1 text-sm text-stone-600 dark:text-stone-300">
              <input
                id="reg-accept-terms"
                type="checkbox"
                checked={acceptTerms}
                onChange={(e) => setAcceptTerms(e.target.checked)}
                required
                className="mt-0.5 h-4 w-4 rounded border-stone-300 text-prism-600 focus:ring-prism-500 dark:border-white/20 dark:text-prism-300"
              />
              <label htmlFor="reg-accept-terms" className="text-xs text-stone-600 dark:text-stone-300">
                I agree to the{" "}
                <a
                  href="/terms"
                  className="underline hover:text-stone-800 dark:hover:text-white"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Terms
                </a>{" "}
                and the{" "}
                <a
                  href="/privacy"
                  className="underline hover:text-stone-800 dark:hover:text-white"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Privacy policy
                </a>
                .
              </label>
            </div>
          )}
          <button type="submit" disabled={loading} className="btn-primary w-full py-2.5">
            {loading ? "Creating account…" : "Create account"}
          </button>
          {!registration?.hosted && (
            <p className="text-center text-xs text-stone-500 dark:text-stone-400">
              By creating an account, you agree to the{" "}
              <a href="/terms" className="underline hover:text-stone-700 dark:hover:text-white" target="_blank" rel="noopener noreferrer">
                Terms of Service
              </a>{" "}
              and{" "}
              <a href="/privacy" className="underline hover:text-stone-700 dark:hover:text-white" target="_blank" rel="noopener noreferrer">
                Privacy Policy
              </a>
              .
            </p>
          )}
        </form>
        )}

        <p className="mt-6 text-center text-sm text-stone-500 dark:text-stone-400">
          Have an account?{" "}
          <a href="/login" className="font-medium text-prism-600 hover:text-prism-700 dark:text-prism-300 dark:hover:text-prism-200">
            Sign in
          </a>
        </p>
      </div>
    </main>
  );
}
