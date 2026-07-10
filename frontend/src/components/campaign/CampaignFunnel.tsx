/**
 * Campaign detail redesign — full funnel card (brief item 2).
 *
 * Two data sources, both pre-existing:
 *  - GET /analytics/funnel?scope=campaign&id=…  → 5 stage counts
 *    (Sent → Replied → Engaged → Lead → Handoff), previously unused by the UI.
 *  - GET /analytics/campaigns/{id}              → KPI cards + campaign progress
 *    (contacts_messaged / registered_contacts).
 *
 * Bars are plain CSS (no recharts): width ∝ count / max(stage counts), with a
 * conversion % against the PREVIOUS stage. Counts are not guaranteed monotonic
 * (see FunnelResponse docstring) — the math guards against divide-by-zero and
 * >100% renders as-is (truthful, not clamped).
 */
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { components } from "@/types/api";

type FunnelResponse = components["schemas"]["FunnelResponse"];
type AnalyticsCards = components["schemas"]["AnalyticsCards"];

const STAGES: Array<{ key: keyof FunnelResponse; label: string }> = [
  { key: "sent", label: "Sent" },
  { key: "replied", label: "Replied" },
  { key: "engaged", label: "Engaged" },
  { key: "lead", label: "Lead" },
  { key: "handoff", label: "Handoff" },
];

function KpiTile({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-lg bg-muted/50 px-3.5 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className="mt-0.5 text-xl font-semibold tabular-nums"
        style={accent ? { color: accent } : undefined}
      >
        {value.toLocaleString()}
      </div>
    </div>
  );
}

export function CampaignFunnel({ campaignId }: { campaignId: string }) {
  const funnelQ = useQuery({
    queryKey: ["campaign-funnel", campaignId],
    queryFn: () =>
      api<FunnelResponse>("/api/v1/analytics/funnel", {
        query: { scope: "campaign", id: campaignId },
      }),
    staleTime: 30_000,
  });
  const statsQ = useQuery({
    queryKey: ["campaign-analytics", campaignId],
    queryFn: () => api<AnalyticsCards>(`/api/v1/analytics/campaigns/${campaignId}`),
    staleTime: 30_000,
  });

  const f = funnelQ.data;
  const s = statsQ.data;
  const max = f ? Math.max(...STAGES.map(({ key }) => f[key] as number), 1) : 1;
  const progressDen = s?.registered_contacts ?? 0;
  const progressNum = s?.contacts_messaged ?? 0;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-4">
        <CardTitle className="text-sm">Funnel</CardTitle>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground"
          aria-label="Обновить воронку"
          onClick={() => {
            void funnelQ.refetch();
            void statsQ.refetch();
          }}
        >
          <RefreshCw size={13} className={funnelQ.isFetching ? "animate-spin" : undefined} />
        </Button>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* KPI row */}
        {statsQ.isLoading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[68px] rounded-lg" />
            ))}
          </div>
        ) : statsQ.isError ? (
          <p className="text-xs text-[var(--danger)]">Не удалось загрузить метрики.</p>
        ) : s ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <KpiTile label="Chats" value={s.contacts_messaged} accent="var(--tg-blue)" />
            <KpiTile
              label="Replied"
              value={s.replied?.conversation_count ?? 0}
              accent="var(--ai-purple)"
            />
            <KpiTile label="Leads" value={s.leads} accent="var(--success)" />
            <KpiTile label="Finished" value={s.finishes} />
          </div>
        ) : null}

        {/* Campaign progress — доля reachable контактов, до которых дотянулись */}
        {progressDen > 0 && (
          <div>
            <div className="mb-1.5 flex items-baseline justify-between text-xs">
              <span className="text-muted-foreground">Прогресс по базе</span>
              <span className="tabular-nums text-muted-foreground">
                {progressNum.toLocaleString()} / {progressDen.toLocaleString()} контактов
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-[var(--tg-blue)] transition-[width] duration-300"
                style={{ width: `${Math.min(100, (progressNum / progressDen) * 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* Stage bars */}
        {funnelQ.isLoading ? (
          <div className="space-y-2.5">
            {STAGES.map(({ key }) => (
              <Skeleton key={key} className="h-7 rounded-md" />
            ))}
          </div>
        ) : funnelQ.isError ? (
          <div className="flex items-center gap-3 text-xs text-[var(--danger)]">
            Не удалось загрузить воронку.
            <Button variant="outline" size="sm" className="h-7" onClick={() => void funnelQ.refetch()}>
              Повторить
            </Button>
          </div>
        ) : f ? (
          <div className="space-y-1.5">
            {STAGES.map(({ key, label }, i) => {
              const value = f[key] as number;
              const prev = i > 0 ? (f[STAGES[i - 1].key] as number) : null;
              const conv = prev && prev > 0 ? Math.round((value / prev) * 100) : null;
              return (
                <div key={key} className="flex items-center gap-3">
                  <div className="w-16 shrink-0 text-xs text-muted-foreground">{label}</div>
                  <div className="relative h-7 flex-1 overflow-hidden rounded-md bg-muted/60">
                    <div
                      className="h-full rounded-md bg-[var(--tg-blue)]"
                      style={{
                        width: value > 0 ? `${Math.max((value / max) * 100, 2)}%` : 0,
                        opacity: 1 - i * 0.13,
                      }}
                    />
                    <span className="absolute inset-y-0 left-2 flex items-center text-xs font-semibold tabular-nums text-foreground mix-blend-luminosity">
                      {value.toLocaleString()}
                    </span>
                  </div>
                  <div className="w-12 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
                    {conv !== null ? `${conv}%` : ""}
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
