import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeft, Edit3, Pause, Play, StopCircle } from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { EditCampaignModal } from "@/components/EditCampaignModal";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];

export const Route = createFileRoute("/_authenticated/campaigns/$id")({
  component: CampaignDetailPage,
});

const STATUS_PILL: Record<string, { label: string; pill: string; dot: string }> = {
  running:   { label: "Running",   pill: "pill--green",  dot: "var(--success)" },
  paused:    { label: "Paused",    pill: "pill--orange", dot: "var(--warning)" },
  draft:     { label: "Draft",     pill: "pill--ghost",  dot: "var(--text-faint)" },
  scheduled: { label: "Scheduled", pill: "pill--blue",   dot: "var(--tg-blue)" },
  finished:  { label: "Finished",  pill: "pill--ghost",  dot: "var(--text-faint)" },
  stopped:   { label: "Stopped",   pill: "pill--red",    dot: "var(--danger)" },
};

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function StatusPill({ status }: { status: string }) {
  const s = STATUS_PILL[status] ?? STATUS_PILL.draft;
  return (
    <span className={`pill ${s.pill}`}>
      <span className="pill__dot" style={{ background: s.dot }} />
      {s.label}
    </span>
  );
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function maskToDays(mask: number): string {
  const days: string[] = [];
  for (let i = 0; i < 7; i++) {
    if (mask & (1 << i)) days.push(DAY_NAMES[i]);
  }
  return days.length ? days.join(", ") : "—";
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
  const statsQ = useQuery({
    queryKey: ["campaign-analytics", id],
    queryFn: () =>
      api<{
        sent?: number;
        replied?: { conversation_count?: number; message_count?: number };
        leads?: number;
        finishes?: number;
        handoffs?: number;
      }>(`/api/v1/analytics/campaigns/${id}`),
    staleTime: 30_000,
    retry: false,
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

  const c = campaignQ.data;

  return (
    <>
      <Topbar
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              className="tb__icon-btn"
              onClick={() => navigate({ to: "/campaigns" })}
              aria-label="Back to campaigns"
              style={{ width: 32, height: 32 }}
            >
              <ArrowLeft size={16} />
            </button>
            <span>{c?.name ?? "Campaign"}</span>
            {c && <StatusPill status={c.status} />}
          </div>
        }
        right={
          c && (
            <>
              {c.status === "draft" && (
                <button
                  className="btn btn--primary btn--sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("start");
                  }}
                >
                  <Play size={14} /> Start
                </button>
              )}
              {c.status === "running" && (
                <button
                  className="btn btn--ghost btn--sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("pause");
                  }}
                >
                  <Pause size={14} /> Pause
                </button>
              )}
              {c.status === "paused" && (
                <button
                  className="btn btn--primary btn--sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("resume");
                  }}
                >
                  <Play size={14} /> Resume
                </button>
              )}
              {(c.status === "running" || c.status === "paused") && (
                <button
                  className="btn btn--ghost btn--sm"
                  disabled={lifecycleMut.isPending}
                  onClick={() => {
                    setActionError(null);
                    lifecycleMut.mutate("stop");
                  }}
                >
                  <StopCircle size={14} /> Stop
                </button>
              )}
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => setEditing(true)}
              >
                <Edit3 size={14} /> Edit
              </button>
            </>
          )
        }
      />

      <div className="scroll" style={{ flex: 1, padding: 24 }}>
        {actionError && (
          <div
            className="card"
            style={{
              padding: 12,
              marginBottom: 14,
              color: "var(--danger)",
              fontSize: 13,
            }}
            role="alert"
          >
            {actionError}{" "}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setActionError(null)}
              style={{ marginLeft: 8 }}
            >
              Dismiss
            </button>
          </div>
        )}

        {campaignQ.isLoading && (
          <div className="muted" style={{ padding: 24 }}>Loading campaign…</div>
        )}
        {campaignQ.error && (
          <div className="card" style={{ padding: 16, color: "var(--danger)" }}>
            {errMsg(campaignQ.error)}{" "}
            <Link to="/campaigns" style={{ marginLeft: 8 }}>Back to list</Link>
          </div>
        )}

        {c && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)",
              gap: 16,
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <section className="card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                  Funnel
                </h3>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(4, 1fr)",
                    gap: 12,
                  }}
                >
                  <Metric
                    label="Sent"
                    value={statsQ.data?.sent ?? 0}
                    color="var(--tg-blue)"
                  />
                  <Metric
                    label="Replied"
                    value={statsQ.data?.replied?.conversation_count ?? 0}
                    color="var(--ai-purple)"
                  />
                  <Metric
                    label="Leads"
                    value={statsQ.data?.leads ?? 0}
                    color="var(--success)"
                  />
                  <Metric
                    label="Finished"
                    value={statsQ.data?.finishes ?? 0}
                    color="var(--text-soft)"
                  />
                </div>
              </section>

              <section className="card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                  Message template
                </h3>
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontFamily: "inherit",
                    fontSize: 13,
                    color: "var(--text)",
                    background: "var(--bg-soft)",
                    padding: 12,
                    borderRadius: 8,
                    margin: 0,
                  }}
                >
                  {c.message_template || "—"}
                </pre>
              </section>

              <section className="card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                  Triggers & webhooks
                </h3>
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
              </section>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <section className="card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                  Overview
                </h3>
                <DefList
                  rows={[
                    [
                      "Agent",
                      agentsQ.data?.agents.find((a) => a.id === c.agent_id)
                        ?.name ?? "—",
                    ],
                    [
                      "Folder",
                      foldersQ.data?.find((f) => f.id === c.folder_id)?.name ??
                        "—",
                    ],
                    ["Description", c.description],
                    ["Audience", c.audience_hints],
                    ["Primary goal", c.primary_goal],
                    ["Success criteria", c.success_criteria],
                    [
                      "Created",
                      new Date(c.created_at).toLocaleString(),
                    ],
                    [
                      "Updated",
                      new Date(c.updated_at).toLocaleString(),
                    ],
                  ]}
                />
              </section>

              <section className="card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                  Schedule
                </h3>
                <DefList
                  rows={[
                    [
                      "Hours",
                      `${String(c.work_hour_start).padStart(2, "0")}:00 – ${String(c.work_hour_end).padStart(2, "0")}:00`,
                    ],
                    ["Timezone", c.timezone],
                    ["Days", maskToDays(c.work_days_mask)],
                    [
                      "Start",
                      c.start_date
                        ? new Date(c.start_date).toLocaleString()
                        : "—",
                    ],
                    [
                      "Stop",
                      c.stop_date
                        ? new Date(c.stop_date).toLocaleString()
                        : "—",
                    ],
                  ]}
                />
              </section>

              <section className="card" style={{ padding: 20 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                  Senders ({c.attached_senders?.length ?? 0})
                </h3>
                {(c.attached_senders?.length ?? 0) === 0 ? (
                  <div className="muted" style={{ fontSize: 13 }}>
                    No senders attached
                  </div>
                ) : (
                  <ul
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 6,
                      fontSize: 13,
                      margin: 0,
                      padding: 0,
                      listStyle: "none",
                    }}
                  >
                    {c.attached_senders!.map((s) => (
                      <li
                        key={s.sender_id}
                        style={{
                          padding: "8px 10px",
                          background: "var(--bg-soft)",
                          borderRadius: 8,
                        }}
                      >
                        <div style={{ fontFamily: "monospace", fontSize: 12 }}>
                          {s.sender_id.slice(0, 8)}…
                        </div>
                        {s.locked_by_campaign_name && (
                          <div
                            className="muted text-xs"
                            style={{ color: "var(--danger)" }}
                          >
                            Locked by {s.locked_by_campaign_name}
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          </div>
        )}
      </div>

      {editing && c && (
        <EditCampaignModal campaign={c} onClose={() => setEditing(false)} />
      )}
    </>
  );
}

function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div
      style={{
        background: "var(--bg-soft)",
        borderRadius: 10,
        padding: 14,
      }}
    >
      <div className="muted text-xs" style={{ marginBottom: 4 }}>
        {label}
      </div>
      <div
        className="num"
        style={{ fontSize: 22, fontWeight: 600, color }}
      >
        {value.toLocaleString()}
      </div>
    </div>
  );
}

function DefList({ rows }: { rows: Array<[string, string | null | undefined]> }) {
  return (
    <dl
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(120px, max-content) 1fr",
        gap: "8px 16px",
        margin: 0,
        fontSize: 13,
      }}
    >
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt className="muted" style={{ fontSize: 12.5 }}>{k}</dt>
          <dd
            style={{
              margin: 0,
              color: "var(--text)",
              wordBreak: "break-word",
            }}
          >
            {v || <span className="muted">—</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}
