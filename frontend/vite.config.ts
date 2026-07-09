// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, cloudflare (build-only),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... } }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// Static SPA build (served from the monorepo VPS nginx at aimly.agsventurelab.com,
// mirroring the vitrina pattern). SSR here is inert — zero server functions, all data
// fetching is client-side — so we drop the Cloudflare Workers deploy plugin (nitro:false)
// and emit a static shell + client-only routing (spa mode).
export default defineConfig({
  nitro: false, // drop the Cloudflare Workers deploy plugin
  tanstackStart: {
    spa: { enabled: true }, // static shell + client-only routing
  },
});
