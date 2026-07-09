import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { Check, Mail, Loader2 } from "lucide-react";
import { supabase, hasSupabaseEnv } from "@/lib/supabase";
import { track } from "@/lib/telemetry";
import { PulseLogo } from "@/components/PulseLogo";

export const Route = createFileRoute("/login")({
  ssr: false,
  validateSearch: (s) => ({ redirect: (s.redirect as string) ?? "/" }),
  beforeLoad: async () => {
    if (typeof window === "undefined" || !hasSupabaseEnv) return;
    const { data } = await supabase.auth.getSession();
    if (data.session) throw redirect({ to: "/" });
  },
  component: LoginPage,
});

function LoginPage() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email) return;
    if (!hasSupabaseEnv) {
      setError("Supabase env vars not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.");
      setState("error");
      return;
    }
    setState("sending");
    setError(null);
    track("magic_link_requested", { method: "magic_link" });
    const { error: err } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    if (err) {
      setState("error");
      setError(err.message);
      return;
    }
    setState("sent");
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--bg-soft)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div className="card" style={{ width: "100%", maxWidth: 400, boxShadow: "var(--shadow-md)" }}>
        <div className="card__body" style={{ padding: 32 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 28 }}>
            <div className="sb__logo">
              <PulseLogo />
            </div>
            <div>
              <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>aimly</div>
              <div className="text-xs muted">AI Telegram outreach</div>
            </div>
          </div>

          {state === "sent" ? (
            <div style={{ textAlign: "center", padding: "20px 0" }}>
              <div
                style={{
                  width: 56,
                  height: 56,
                  borderRadius: "50%",
                  background: "var(--success-soft)",
                  color: "#1e8a3a",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  marginBottom: 16,
                }}
              >
                <Check size={28} />
              </div>
              <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Check your inbox</h1>
              <p className="muted" style={{ fontSize: 13 }}>
                We sent a magic link to <strong style={{ color: "var(--text)" }}>{email}</strong>. Open it on this device to finish signing in.
              </p>
              <button
                className="btn btn--ghost"
                style={{ marginTop: 20 }}
                onClick={() => {
                  setState("idle");
                  setEmail("");
                }}
              >
                Use a different email
              </button>
            </div>
          ) : (
            <form onSubmit={onSubmit}>
              <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 6, letterSpacing: "-0.01em" }}>
                Sign in to aimly
              </h1>
              <p className="muted" style={{ fontSize: 13, marginBottom: 24 }}>
                We'll email you a passwordless sign-in link.
              </p>
              <div className="field">
                <label className="field__label" htmlFor="email">Email address</label>
                <input
                  id="email"
                  type="email"
                  className="input"
                  required
                  autoFocus
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={state === "sending"}
                />
              </div>
              {error && (
                <div
                  style={{
                    marginTop: 12,
                    padding: "8px 12px",
                    borderRadius: 8,
                    background: "var(--danger-soft)",
                    color: "#b8332a",
                    fontSize: 12,
                  }}
                >
                  {error}
                </div>
              )}
              <button
                type="submit"
                className="btn btn--primary"
                style={{ width: "100%", marginTop: 20, height: 42, justifyContent: "center" }}
                disabled={state === "sending"}
              >
                {state === "sending" ? (
                  <>
                    <Loader2 size={16} className="animate-spin" /> Sending…
                  </>
                ) : (
                  <>
                    <Mail size={16} /> Send magic link
                  </>
                )}
              </button>
            </form>
          )}
        </div>
      </div>
      {/* navigate is intentionally available for future redirect-back wiring */}
      <span style={{ display: "none" }}>{String(Boolean(navigate))}</span>
    </div>
  );
}
