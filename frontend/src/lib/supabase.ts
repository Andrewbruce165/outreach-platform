import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

// Lazy client — Supabase JS uses localStorage which is browser-only.
// During SSR we return a stub that throws on use.
function makeBrowserClient(): SupabaseClient {
  if (!url || !anon) {
    // Surfaced in the UI via login form's empty-env guard.
    return new Proxy({} as SupabaseClient, {
      get() {
        throw new Error(
          "Supabase env vars missing. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in project Settings.",
        );
      },
    });
  }
  return createClient(url, anon, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
      flowType: "pkce",
    },
  });
}

// Avoid touching browser-only globals on the server.
const stub = new Proxy({} as SupabaseClient, {
  get() {
    throw new Error("Supabase client accessed during SSR");
  },
});

export const supabase: SupabaseClient =
  typeof window === "undefined" ? stub : makeBrowserClient();

export const hasSupabaseEnv = Boolean(url && anon);
