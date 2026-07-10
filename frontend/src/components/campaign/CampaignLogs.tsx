/**
 * Campaign detail redesign — Logs tab (brief item 1, топ приоритет).
 *
 * GET /campaigns/{id}/logs — union of message_queue outcomes and
 * llm_calls.tool_calls trigger events, newest-first, cursor pagination
 * (?before=). Rendered as a reui <Timeline>, grouped by day, with client-side
 * type filters. "Load more" pulls the next (older) page via next_before.
 *
 * Filters apply to ALREADY-LOADED events only (MVP) — the counter chip shows
 * how many of the loaded events match, not a server-side total.
 */
import { useMemo, useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Check,
  ChevronDown,
  Clock,
  Flag,
  Inbox,
  RefreshCw,
  Trophy,
  UserRound,
} from "lucide-react";

import { Badge } from "@/components/reui/badge";
import {
  Timeline,
  TimelineContent,
  TimelineHeader,
  TimelineIndicator,
  TimelineItem,
  TimelineSeparator,
  TimelineTitle,
} from "@/components/reui/timeline";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { components } from "@/types/api";

type LogEvent = components["schemas"]["CampaignLogEvent"];
type LogsResponse = components["schemas"]["CampaignLogsResponse"];

type BadgeVariant = React.ComponentProps<typeof Badge>["variant"];

const EVENT_META: Record<
  string,
  {
    label: string;
    icon: React.ComponentType<{ size?: number | string; className?: string }>;
    variant: BadgeVariant;
    /** indicator circle colors — aimly palette */
    indicator: string;
  }
> = {
  message_sent: {
    label: "Отправлено",
    icon: Check,
    variant: "success-light",
    indicator: "bg-[var(--success-soft)] text-[var(--success)]",
  },
  message_failed: {
    label: "Ошибка отправки",
    icon: AlertTriangle,
    variant: "destructive-light",
    indicator: "bg-[var(--danger-soft)] text-[var(--danger)]",
  },
  message_queued: {
    label: "В очереди",
    icon: Clock,
    variant: "secondary",
    indicator: "bg-muted text-muted-foreground",
  },
  message_cancelled: {
    label: "Отменено",
    icon: Ban,
    variant: "secondary",
    indicator: "bg-muted text-muted-foreground",
  },
  lead: {
    label: "Лид",
    icon: Trophy,
    variant: "success",
    indicator: "bg-[var(--success)] text-white",
  },
  handoff: {
    label: "Передан менеджеру",
    icon: UserRound,
    variant: "info-light",
    indicator: "bg-[var(--tg-blue-soft)] text-[var(--tg-blue)]",
  },
  dialog_finished: {
    label: "Диалог завершён",
    icon: Flag,
    variant: "secondary",
    indicator: "bg-muted text-muted-foreground",
  },
};

const FILTERS: Array<{ key: string; label: string; match: (t: string) => boolean }> = [
  { key: "all", label: "Все", match: () => true },
  {
    key: "messages",
    label: "Сообщения",
    match: (t) => t === "message_sent" || t === "message_queued" || t === "message_cancelled",
  },
  { key: "errors", label: "Ошибки", match: (t) => t === "message_failed" },
  {
    key: "triggers",
    label: "Лиды и триггеры",
    match: (t) => t === "lead" || t === "handoff" || t === "dialog_finished",
  },
];

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

function dayLabel(ts: string): string {
  const d = new Date(ts);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const same = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (same(d, today)) return "Сегодня";
  if (same(d, yesterday)) return "Вчера";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

export function CampaignLogs({ campaignId }: { campaignId: string }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("all");

  const logsQ = useInfiniteQuery({
    queryKey: ["campaign-logs", campaignId],
    queryFn: ({ pageParam }) =>
      api<LogsResponse>(`/api/v1/campaigns/${campaignId}/logs`, {
        query: { limit: 50, before: pageParam },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
    staleTime: 15_000,
  });

  const all = useMemo(
    () => (logsQ.data?.pages ?? []).flatMap((p) => p.events),
    [logsQ.data],
  );
  const activeFilter = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
  const events = all.filter((e) => activeFilter.match(e.type));

  // Group by calendar day, preserving newest-first order.
  const groups = useMemo(() => {
    const out: Array<{ day: string; items: LogEvent[] }> = [];
    for (const ev of events) {
      const day = dayLabel(ev.ts);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(ev);
      else out.push({ day, items: [ev] });
    }
    return out;
  }, [events]);

  return (
    <div className="space-y-4">
      {/* Filter chips + refresh */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const count = f.key === "all" ? all.length : all.filter((e) => f.match(e.type)).length;
          const active = filter === f.key;
          return (
            <Button
              key={f.key}
              variant={active ? "default" : "outline"}
              size="sm"
              className="h-7 rounded-full text-xs"
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              <span className={cn("tabular-nums", active ? "opacity-80" : "text-muted-foreground")}>
                {count}
              </span>
            </Button>
          );
        })}
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs text-muted-foreground"
          onClick={() => void qc.invalidateQueries({ queryKey: ["campaign-logs", campaignId] })}
        >
          <RefreshCw size={12} className={logsQ.isFetching ? "animate-spin" : undefined} />
          Обновить
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          {logsQ.isLoading ? (
            <div className="space-y-6">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-start gap-4">
                  <Skeleton className="size-7 rounded-full" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-52" />
                    <Skeleton className="h-3 w-32" />
                  </div>
                </div>
              ))}
            </div>
          ) : logsQ.isError ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <AlertTriangle size={22} className="text-[var(--danger)]" />
              <p className="text-sm text-[var(--danger)]">{errMsg(logsQ.error)}</p>
              <Button variant="outline" size="sm" onClick={() => void logsQ.refetch()}>
                Повторить
              </Button>
            </div>
          ) : events.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <Inbox size={26} className="text-muted-foreground/60" />
              <p className="text-sm font-medium">
                {all.length === 0 ? "Пока нет событий" : "Нет событий по этому фильтру"}
              </p>
              <p className="max-w-sm text-xs text-muted-foreground">
                {all.length === 0
                  ? "Логи появятся после запуска кампании: отправки, ошибки, лиды и передачи менеджеру."
                  : "Попробуйте другой фильтр или загрузите более старые события."}
              </p>
            </div>
          ) : (
            <div className="space-y-8">
              {groups.map((g) => (
                <div key={g.day}>
                  <div className="mb-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {g.day}
                  </div>
                  <Timeline defaultValue={0}>
                    {g.items.map((ev, i) => {
                      const meta = EVENT_META[ev.type] ?? EVENT_META.message_queued;
                      const Icon = meta.icon;
                      const contact = ev.contact_name || ev.contact_phone || "—";
                      const isLast = i === g.items.length - 1;
                      return (
                        <TimelineItem
                          key={`${ev.ts}-${ev.type}-${ev.contact_phone ?? i}`}
                          step={i + 1}
                          className={cn("ms-10", isLast ? "pb-1" : "pb-6")}
                        >
                          <TimelineHeader>
                            {!isLast && (
                              <TimelineSeparator className="group-data-[orientation=vertical]/timeline:-left-7 group-data-[orientation=vertical]/timeline:h-[calc(100%-1.75rem-0.25rem)] group-data-[orientation=vertical]/timeline:translate-y-8" />
                            )}
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                              <TimelineTitle className="text-sm font-medium">
                                {contact}
                              </TimelineTitle>
                              <Badge variant={meta.variant} size="sm" radius="full">
                                {meta.label}
                              </Badge>
                              {ev.contact_name && ev.contact_phone && (
                                <span className="text-xs tabular-nums text-muted-foreground">
                                  {ev.contact_phone}
                                </span>
                              )}
                              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                                {timeFmt.format(new Date(ev.ts))}
                              </span>
                            </div>
                            <TimelineIndicator
                              className={cn(
                                "flex size-7 items-center justify-center border-none group-data-[orientation=vertical]/timeline:-left-7",
                                meta.indicator,
                              )}
                            >
                              <Icon size={13} />
                            </TimelineIndicator>
                          </TimelineHeader>
                          {ev.detail && (
                            <TimelineContent className="mt-1.5">
                              <div
                                className="max-w-xl rounded-md border border-[var(--danger)]/25 bg-[var(--danger-soft)]/60 px-3 py-2 font-mono text-xs leading-relaxed text-[var(--danger)]"
                                title={ev.detail}
                              >
                                {ev.detail}
                              </div>
                            </TimelineContent>
                          )}
                        </TimelineItem>
                      );
                    })}
                  </Timeline>
                </div>
              ))}

              {logsQ.hasNextPage && (
                <div className="flex justify-center pt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={logsQ.isFetchingNextPage}
                    onClick={() => void logsQ.fetchNextPage()}
                  >
                    <ChevronDown size={14} />
                    {logsQ.isFetchingNextPage ? "Загружаю…" : "Показать более старые"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
