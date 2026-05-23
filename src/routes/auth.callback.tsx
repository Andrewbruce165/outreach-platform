import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { supabase, hasSupabaseEnv } from "@/lib/supabase";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";

interface AuthMeResponse {
  workspace_id: string;
  user_id: string;
  email: string;
  senders: { slug: string }[];
  is_first_session?: boolean;
}

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
      // detectSessionInUrl on the Supabase client consumes the URL fragment.
      // We just wait for the session to materialize.
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
        const me = await api<AuthMeResponse>("/api/v1/auth/me", { method: "POST" });
        if (me.is_first_session) {
          track("signup_completed", { workspace_id: me.workspace_id, is_first_session: true });
        }
        if (!cancelled) {
          navigate({ to: me.senders.length === 0 ? "/onboarding" : "/" });
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
