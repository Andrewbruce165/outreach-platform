import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect } from "react";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { supabase, hasSupabaseEnv } from "@/lib/supabase";

import appCss from "../styles.css?url";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center" style={{ background: "var(--bg-soft)" }}>
      <div className="max-w-md text-center">
        <h1 style={{ fontSize: 72, fontWeight: 600 }}>404</h1>
        <h2 style={{ fontSize: 18, fontWeight: 600, marginTop: 8 }}>Page not found</h2>
        <p className="muted" style={{ marginTop: 8 }}>
          The page you're looking for doesn't exist.
        </p>
        <div style={{ marginTop: 20 }}>
          <Link to="/" className="btn btn--primary">Go home</Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  const router = useRouter();
  if (import.meta.env.DEV) console.error(error);
  return (
    <div className="flex min-h-screen items-center justify-center" style={{ background: "var(--bg-soft)" }}>
      <div className="max-w-md text-center">
        <h1 style={{ fontSize: 18, fontWeight: 600 }}>This page didn't load</h1>
        <p className="muted" style={{ marginTop: 8 }}>Something went wrong. Try again or head home.</p>
        <div style={{ marginTop: 20, display: "flex", gap: 8, justifyContent: "center" }}>
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="btn btn--primary"
          >
            Try again
          </button>
          <a href="/" className="btn btn--ghost">Go home</a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "aimly" },
      { name: "description", content: "AI-powered Telegram outreach for B2B teams." },
      { property: "og:title", content: "aimly" },
      { name: "twitter:title", content: "aimly" },
      { property: "og:description", content: "AI-powered Telegram outreach for B2B teams." },
      { name: "twitter:description", content: "AI-powered Telegram outreach for B2B teams." },
      { property: "og:image", content: "https://pub-bb2e103a32db4e198524a2e9ed8f35b4.r2.dev/e686d743-07dc-45ed-b7b3-1e378ac40f9e/id-preview-24ab421b--4324a3e0-88a5-4664-b4aa-65f98232e671.lovable.app-1779543554674.png" },
      { name: "twitter:image", content: "https://pub-bb2e103a32db4e198524a2e9ed8f35b4.r2.dev/e686d743-07dc-45ed-b7b3-1e378ac40f9e/id-preview-24ab421b--4324a3e0-88a5-4664-b4aa-65f98232e671.lovable.app-1779543554674.png" },
      { name: "twitter:card", content: "summary_large_image" },
      { property: "og:type", content: "website" },
    ],
    links: [{ rel: "stylesheet", href: appCss }],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function AuthSync() {
  const router = useRouter();
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!hasSupabaseEnv) return;
    const { data: { subscription } } = supabase.auth.onAuthStateChange(() => {
      router.invalidate();
      queryClient.invalidateQueries();
    });
    const onExpire = () => {
      toast.error("Your session expired. Sign in again.");
      void supabase.auth.signOut();
      router.navigate({ to: "/login" });
    };
    window.addEventListener("aimly:auth-expired", onExpire);
    return () => {
      subscription.unsubscribe();
      window.removeEventListener("aimly:auth-expired", onExpire);
    };
  }, [router, queryClient]);
  return null;
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthSync />
      <Outlet />
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  );
}
