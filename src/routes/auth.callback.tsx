import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { supabase, hasSupabaseEnv } from "@/lib/supabase";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";

type AuthMeResponse = components["schemas"]["AuthMeResponse"];
type SenderListResponse = components["schemas"]["SenderListResponse"];

export const Route = createFileRoute("/auth/callback")({
  ssr: false,
  component: AuthCallback,
});

function AuthCallback() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasSupabaseEnv) {
      setError("Supabase env vars not configured.");
      return;
    }
    let cancelled = false;
    (async () => {
      const code = new URLSearchParams(window.location.search).get("code");
      if (code) {
        const { error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
        if (exchangeError) {
          if (!cancelled) setError("Sign-in link was invalid or expired. Try again.");
          return;
        }
      }

      // Wait until the browser auth client has persisted the session before any backend calls.
      for (let i = 0; i < 30 && !cancelled; i++) {
        const { data } = await supabase.auth.getSession();
        if (data.session) break;
        await new Promise((r) => setTimeout(r, 100));
      }
      const { data: sessionData } = await supabase.auth.getSession();
      if (!sessionData.session) {
        if (!cancelled) setError("Sign-in link was invalid or expired. Try again.");
        return;
      }

      try {
        await api<AuthMeResponse>("/api/v1/auth/me", { method: "POST" });
        const senderList = await api<SenderListResponse>("/api/v1/senders");
        if (!cancelled) {
          navigate({ to: senderList.senders.length === 0 ? "/onboarding" : "/" });
        }
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Could not validate session.";
        if (!cancelled) setError(message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-soft)",
      }}
    >
      {error ? (
        <div className="card" style={{ maxWidth: 380, padding: 24, textAlign: "center" }}>
          <div style={{ color: "var(--danger)", marginBottom: 12, fontWeight: 600 }}>Sign-in failed</div>
          <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>{error}</p>
          <a href="/login" className="btn btn--primary">Back to sign in</a>
        </div>
      ) : (
        <div style={{ textAlign: "center" }}>
          <Loader2 className="animate-spin" size={28} style={{ color: "var(--tg-blue)" }} />
          <div className="muted" style={{ marginTop: 12, fontSize: 13 }}>Signing you in…</div>
        </div>
      )}
    </div>
  );
}
