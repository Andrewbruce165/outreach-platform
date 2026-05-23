import { createFileRoute, Outlet, redirect, useRouter } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppSidebar } from "@/components/AppSidebar";
import { supabase, hasSupabaseEnv } from "@/lib/supabase";

export const Route = createFileRoute("/_authenticated")({
  // Skip SSR for the whole authenticated subtree — Supabase auth state lives in
  // localStorage which doesn't exist server-side.
  ssr: false,
  beforeLoad: async ({ location }) => {
    if (typeof window === "undefined") return;
    if (!hasSupabaseEnv) {
      throw redirect({ to: "/login" });
    }
    const { data } = await supabase.auth.getSession();
    if (!data.session) {
      throw redirect({
        to: "/login",
        search: { redirect: location.href },
      });
    }
  },
  component: AuthLayout,
});

function AuthLayout() {
  const router = useRouter();
  const [email, setEmail] = useState<string>("");

  useEffect(() => {
    let active = true;
    supabase.auth.getUser().then(({ data }) => {
      if (active) setEmail(data.user?.email ?? "");
    });
    return () => {
      active = false;
    };
  }, [router]);

  return (
    <div className="app">
      <AppSidebar workspaceName={email || "Workspace"} workspaceUser={email} plan="Free" />
      <main className="app__main">
        <Outlet />
      </main>
    </div>
  );
}
