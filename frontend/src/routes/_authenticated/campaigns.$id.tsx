/**
 * Campaign detail — redesigned (see .planning/notes/campaign-detail-redesign-brief.md).
 *
 * Layout: Topbar actions (lifecycle + Duplicate + Edit) → meta row (status +
 * pool health) → Tabs: Overview | Logs.
 *  - Overview: full funnel viz (existing /analytics/funnel), message template
 *    with attachment previews, triggers/webhooks, overview/schedule def-lists,
 *    senders pool panel.
 *  - Logs: chronological event timeline (new GET /campaigns/{id}/logs).
 * attach_warnings from POST /senders are surfaced as toasts (brief item 6);
 * Duplicate wires the pre-existing POST /duplicate endpoint (brief item 7).
 */
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Copy,
  Edit3,
  Pause,
  Play,
  StopCircle,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Topbar } from "@/components/Topbar";
import { EditCampaignModal } from "@/components/EditCampaignModal";
import { PoolBadge, StatusBadge } from "@/components/campaign/badges";
import { CampaignAttachments } from "@/components/campaign/CampaignAttachments";
import { CampaignFunnel } from "@/components/campaign/CampaignFunnel";
import { CampaignLogs } from "@/components/campaign/CampaignLogs";
import { CampaignSenders } from "@/components/campaign/CampaignSenders";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];
type Sender = components["schemas"]["SenderResponse"];
type PoolHealth = components["schemas"]["PoolHealth"];

export const Route = createFileRoute("/_authenticated/campaigns/$id")({
  component: CampaignDetailPage,
});

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function maskToDays(mask: number): string {
  const days: string[] = [];
  for (let i = 0; i < 7; i++) {
    if (mask & (1 << i)) days.push(DAY_NAMES[i]);
  }
  return days.length ? days.join(", ") : "—";
}

/**
 * Soft advisory: campaign has exactly one active sender (no failover).
 * Shown only when has_backup === false AND at least one sender is active.
 */
function NoBackupNotice({ health }: { health: PoolHealth | null | undefined }) {
  if (!health || health.has_backup || health.active < 1) return null;
  return (
    <Alert className="border-[var(--warning)]/50 bg-[var(--warning-soft)] text-foreground">
      <AlertTriangle size={15} className="text-[var(--warning)]" />
      <AlertTitle className="text-sm">No backup account</AlertTitle>
      <AlertDescription className="text-xs text-muted-foreground">
        If this single connected account hits a restriction, the campaign will stop sending.
        Attach a second account as a failover.
      </AlertDescription>
    </Alert>
  );
}

function CampaignDetailPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const campaignQ = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => api<Campaign>(`/api/v1/campaigns/${id}`),
  });
  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<{ agents: Agent[]; total: number }>("/api/v1/agents"),
    staleTime: 60_000,
  });
  const foldersQ = useQuery({
    queryKey: ["folders"],
    queryFn: () => api<Folder[]>("/api/v1/folders"),
    staleTime: 60_000,
  });
  // Workspace senders — source for the "add to pool" chips (mirrors the wizard).
  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
    staleTime: 60_000,
  });

  const lifecycleMut = useMutation({
    mutationFn: (action: "start" | "pause" | "resume" | "stop") =>
      api<Campaign>(`/api/v1/campaigns/${id}/${action}`, { method: "POST" }),
    onSuccess: (_d, action) => {
      const map = {
        start: "campaign_launched",
        pause: "campaign_paused",
        resume: "campaign_resumed",
      } as const;
      const e = map[action as keyof typeof map];
      if (e) track(e, { campaign_id: id });
      void qc.invalidateQueries({ queryKey: ["campaign", id] });
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  // Brief item 7: POST /duplicate existed on the backend, had no button.
  const duplicateMut = useMutation({
    mutationFn: () => api<Campaign>(`/api/v1/campaigns/${id}/duplicate`, { method: "POST" }),
    onSuccess: (d) => {
      toast.success("Кампания скопирована как черновик");
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
      void navigate({ to: "/campaigns/$id", params: { id: d.id } });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  // Attach a sender. Server enforces lock/isolation (409/404 envelope → banner);
  // PFH-01 attach_warnings[] are advisory — surfaced as toasts (brief item 6).
  const attachMut = useMutation({
    mutationFn: (sender_id: string) =>
      api<Campaign>(`/api/v1/campaigns/${id}/senders`, {
        method: "POST",
        body: { sender_id },
      }),
    onSuccess: (resp) => {
      for (const w of resp.attach_warnings ?? []) {
        toast.warning("Предупреждение при подключении", {
          description: w.message,
          duration: 8000,
        });
      }
      void qc.invalidateQueries({ queryKey: ["campaign", id] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  // Detach — MIN_POOL_GUARD / DETACH_BLOCKED_PENDING bubble through the banner.
  const detachMut = useMutation({
    mutationFn: (sender_id: string) =>
      api<Campaign>(`/api/v1/campaigns/${id}/senders/${sender_id}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["campaign", id] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  const c = campaignQ.data;

  return (
    <>
      <Topbar
        title={c?.name ?? "Campaign"}
        crumbs={[{ label: "Campaigns", href: "/campaigns" }, { label: c?.name ?? "…" }]}
        right={
          c && (
            <>
              {c.status === "draft" && (
                <Button
                  size="sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("start");
                  }}
                >
                  <Play size={14} /> Start
                </Button>
              )}
              {c.status === "running" && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("pause");
                  }}
                >
                  <Pause size={14} /> Pause
                </Button>
              )}
              {c.status === "paused" && (
                <Button
                  size="sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("resume");
                  }}
                >
                  <Play size={14} /> Resume
                </Button>
              )}
              {(c.status === "running" || c.status === "paused") && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("stop");
                  }}
                >
                  <StopCircle size={14} /> Stop
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                disabled={duplicateMut.isPending}
                title="Создать черновик-копию кампании (пул аккаунтов не копируется)"
                onClick={() => {
                  setActionError(null);
                  duplicateMut.mutate();
                }}
              >
                <Copy size={14} /> Duplicate
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
                <Edit3 size={14} /> Edit
              </Button>
            </>
          )
        }
      />

      <div className="scroll flex-1 p-6">
        {/* Meta row */}
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate({ to: "/campaigns" })}>
            <ArrowLeft size={14} /> Back
          </Button>
          {c && <StatusBadge status={c.status} />}
          {c && <PoolBadge health={c.pool_health} />}
        </div>

        {c && (
          <div className="mb-4 empty:hidden">
            <NoBackupNotice health={c.pool_health} />
          </div>
        )}

        {actionError && (
          <Alert className="mb-4 border-[var(--danger)]/40 text-[var(--danger)]" role="alert">
            <AlertTriangle size={15} />
            <AlertDescription className="flex items-center gap-2 text-[var(--danger)]">
              <span className="flex-1">{actionError}</span>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                aria-label="Dismiss error"
                onClick={() => setActionError(null)}
              >
                <X size={13} />
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {/* Explicit loading state (brief item 5) */}
        {campaignQ.isLoading && (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <div className="space-y-4">
              <Skeleton className="h-72 rounded-xl" />
              <Skeleton className="h-40 rounded-xl" />
            </div>
            <div className="space-y-4">
              <Skeleton className="h-56 rounded-xl" />
              <Skeleton className="h-40 rounded-xl" />
            </div>
          </div>
        )}

        {campaignQ.isError && (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
              <AlertTriangle size={22} className="text-[var(--danger)]" />
              <p className="text-sm text-[var(--danger)]">{errMsg(campaignQ.error)}</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => void campaignQ.refetch()}>
                  Повторить
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <Link to="/campaigns">Back to list</Link>
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {c && (
          <Tabs defaultValue="overview">
            <TabsList className="mb-4">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="logs">Logs</TabsTrigger>
            </TabsList>

            <TabsContent value="overview">
              <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                <div className="space-y-4">
                  <CampaignFunnel campaignId={id} />

                  <Card>
                    <CardHeader className="pb-4">
                      <CardTitle className="text-sm">Message template</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <pre className="m-0 whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-3 font-[inherit] text-[13px] leading-relaxed">
                        {c.message_template || "—"}
                      </pre>
                      <CampaignAttachments campaignId={id} />
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-4">
                      <CardTitle className="text-sm">Triggers & webhooks</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <DefList
                        rows={[
                          ["Lead hint", c.lead_trigger_hint],
                          ["Handoff hint", c.handoff_trigger_hint],
                          ["Finish hint", c.finish_trigger_hint],
                          ["Lead webhook", c.lead_webhook_url],
                          ["Handoff webhook", c.handoff_webhook_url],
                          ["Finish webhook", c.finish_webhook_url],
                        ]}
                      />
                    </CardContent>
                  </Card>
                </div>

                <div className="space-y-4">
                  <Card>
                    <CardHeader className="pb-4">
                      <CardTitle className="text-sm">Overview</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <DefList
                        rows={[
                          [
                            "Agent",
                            agentsQ.data?.agents.find((a) => a.id === c.agent_id)?.name ?? "—",
                          ],
                          ["Folder", foldersQ.data?.find((f) => f.id === c.folder_id)?.name ?? "—"],
                          ["Description", c.description],
                          // Phase 11 D-13: audience_hints relabeled "Кому пишем"
                          ["Кому пишем", c.audience_hints],
                          ["Primary goal", c.primary_goal],
                          // Phase 11 D-12/D-14
                          ["Аргументы и факты", c.arguments_facts],
                          ["Правила кампании", c.campaign_rules],
                          ["Created", new Date(c.created_at).toLocaleString()],
                          ["Updated", new Date(c.updated_at).toLocaleString()],
                        ]}
                      />
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-4">
                      <CardTitle className="text-sm">Schedule</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <DefList
                        rows={[
                          [
                            "Hours",
                            `${String(c.work_hour_start).padStart(2, "0")}:00 – ${String(c.work_hour_end).padStart(2, "0")}:00`,
                          ],
                          ["Timezone", c.timezone],
                          ["Days", maskToDays(c.work_days_mask)],
                          ["Start", c.start_date ? new Date(c.start_date).toLocaleString() : "—"],
                          ["Stop", c.stop_date ? new Date(c.stop_date).toLocaleString() : "—"],
                        ]}
                      />
                    </CardContent>
                  </Card>

                  <CampaignSenders
                    campaign={c}
                    senders={sendersQ.data?.senders ?? []}
                    attaching={attachMut.isPending}
                    detaching={detachMut.isPending}
                    onAttach={(sid) => {
                      setActionError(null);
                      attachMut.mutate(sid);
                    }}
                    onDetach={(sid) => {
                      setActionError(null);
                      detachMut.mutate(sid);
                    }}
                  />
                </div>
              </div>
            </TabsContent>

            <TabsContent value="logs">
              <CampaignLogs campaignId={id} />
            </TabsContent>
          </Tabs>
        )}
      </div>

      {editing && c && <EditCampaignModal campaign={c} onClose={() => setEditing(false)} />}
    </>
  );
}

function DefList({ rows }: { rows: Array<[string, string | null | undefined]> }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,max-content)_1fr] gap-x-4 gap-y-2 text-[13px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-xs leading-[1.6] text-muted-foreground">{k}</dt>
          <dd className="m-0 break-words">
            {v || <span className="text-muted-foreground">—</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}
