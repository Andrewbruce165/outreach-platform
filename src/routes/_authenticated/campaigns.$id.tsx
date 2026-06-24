import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeft, Edit3, Lock, Pause, Play, Plus, StopCircle, X } from "lucide-react";
import { Topbar } from "@/components/Topbar";

import { EditCampaignModal } from "@/components/EditCampaignModal";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];
type Sender = components["schemas"]["SenderResponse"];
type PoolHealth = components["schemas"]["PoolHealth"];
type AttachedSender = components["schemas"]["CampaignSenderAttach"];

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

function fmtUntil(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * POOLV-03 / D-09: 3-state pool badge derived ON THE FRONTEND from the numeric
 * pool_health aggregate (the API stays presentation-free).
 *   paused === 0                  → 🟢 "пул активен"
 *   0 < paused < total            → 🟡 "K из N на паузе до проверки в T"
 *   paused === total && total > 0 → 🔴 "весь пул на паузе"
 * earliest_resume_at is a recheck horizon (OQ#4) → wording "до проверки в T".
 */
function PoolBadge({ health }: { health: PoolHealth | null | undefined }) {
  if (!health || health.total === 0) return null;
  const { active, paused, total, earliest_resume_at } = health;

  if (paused === 0) {
    return (
      <span className="pill pill--green" title={`${active} из ${total} аккаунтов активны`}>
        <span className="pill__dot" style={{ background: "var(--success)" }} />
        Пул активен
      </span>
    );
  }
  if (paused === total) {
    return (
      <span
        className="pill pill--red"
        title={
          earliest_resume_at
            ? `Все ${total} аккаунтов на паузе · проверка ${fmtUntil(earliest_resume_at)}`
            : `Все ${total} аккаунтов на паузе`
        }
      >
        <span className="pill__dot" style={{ background: "var(--danger)" }} />
        Весь пул на паузе
      </span>
    );
  }
  // Partial pause — the key UX signal of this phase.
  return (
    <span
      className="pill pill--orange"
      title={`${paused} из ${total} аккаунтов на паузе${
        earliest_resume_at ? ` · проверка ${fmtUntil(earliest_resume_at)}` : ""
      }`}
    >
      <span className="pill__dot" style={{ background: "var(--warning)" }} />
      {paused} из {total} на паузе
      {earliest_resume_at ? ` · до проверки в ${fmtUntil(earliest_resume_at)}` : ""}
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

  // Workspace senders — source for the "add to pool" multiselect (mirrors the wizard).
  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
    staleTime: 60_000,
  });

  // Attach a sender to this campaign's pool. Server enforces lock/isolation; the
  // UI just renders the 409/404 envelope through the existing actionError banner.
  const attachMut = useMutation({
    mutationFn: (sender_id: string) =>
      api<Campaign>(`/api/v1/campaigns/${id}/senders`, {
        method: "POST",
        body: { sender_id },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["campaign", id] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  // Detach a sender from this campaign's pool. Server enforces MIN_POOL_GUARD /
  // DETACH_BLOCKED_PENDING — surfaced via the same banner.
  const detachMut = useMutation({
    mutationFn: (sender_id: string) =>
      api<Campaign>(`/api/v1/campaigns/${id}/senders/${sender_id}`, {
        method: "DELETE",
      }),
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
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => navigate({ to: "/campaigns" })}
          >
            <ArrowLeft size={14} /> Back
          </button>
          {c && <StatusPill status={c.status} />}
          {c && <PoolBadge health={c.pool_health} />}
        </div>

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
                    // Phase 11 D-13: audience_hints relabeled "Кому пишем"
                    ["Кому пишем", c.audience_hints],
                    ["Primary goal", c.primary_goal],
                    // Phase 11 D-12/D-14: new campaign fields
                    ["Аргументы и факты", c.arguments_facts],
                    ["Правила кампании", c.campaign_rules],
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

              <SendersPanel
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
              <p
                className="muted text-xs"
                style={{ margin: "-6px 4px 0", lineHeight: 1.5 }}
              >
                Sender selection in the campaign wizard only seeds the initial
                pool — manage the live pool here.
              </p>
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

/**
 * POOLV-02: per-sender restriction chip for the attached-pool list. Reads the
 * enriched attached_senders[].restriction_status / restricted_until (Plan 03).
 */
const RESTRICTION_CHIP: Record<
  Exclude<AttachedSender["restriction_status"], "none">,
  { label: string; pill: string; dot: string }
> = {
  spam_limited: { label: "Спам-лимит", pill: "pill--orange", dot: "var(--warning)" },
  frozen: { label: "Заморожен", pill: "pill--red", dot: "var(--danger)" },
};

function RestrictionChip({ sender }: { sender: AttachedSender }) {
  if (!sender.restriction_status || sender.restriction_status === "none") return null;
  const meta = RESTRICTION_CHIP[sender.restriction_status];
  const until = sender.restricted_until ? fmtUntil(sender.restricted_until) : null;
  return (
    <span
      className={`pill ${meta.pill}`}
      style={{ height: 16, fontSize: 10, padding: "0 6px", marginTop: 4 }}
      title={until ? `${meta.label} · проверка ${until}` : meta.label}
    >
      <span className="pill__dot" style={{ background: meta.dot }} />
      {meta.label}
      {until ? ` · до ${until}` : ""}
    </span>
  );
}

/* ---------------- Senders / Пул panel (D-10/D-11) ---------------- */
function SendersPanel({
  campaign,
  senders,
  attaching,
  detaching,
  onAttach,
  onDetach,
}: {
  campaign: Campaign;
  senders: Sender[];
  attaching: boolean;
  detaching: boolean;
  onAttach: (senderId: string) => void;
  onDetach: (senderId: string) => void;
}) {
  const attached = campaign.attached_senders ?? [];
  const attachedIds = new Set(attached.map((s) => s.sender_id));
  const byId = new Map(senders.map((s) => [s.id, s]));
  const busy = attaching || detaching;

  // Eligible to add: workspace senders not already attached and not in error.
  // Locked senders are still listed but their add control is disabled.
  const eligible = senders.filter(
    (s) => !attachedIds.has(s.id) && s.status !== "error",
  );

  return (
    <section className="card" style={{ padding: 20 }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
        Senders ({attached.length})
      </h3>

      {/* Attached pool */}
      {attached.length === 0 ? (
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
          {attached.map((s) => {
            const sender = byId.get(s.sender_id);
            const locked = !!s.locked_by_campaign_name;
            const label = sender ? sender.name || sender.slug : `${s.sender_id.slice(0, 8)}…`;
            return (
              <li
                key={s.sender_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 10px",
                  background: "var(--bg-soft)",
                  borderRadius: 8,
                }}
              >
                <div
                  className="avatar avatar--sm"
                  style={{ background: "var(--tg-blue)", color: "white", flexShrink: 0 }}
                >
                  {label.slice(0, 1).toUpperCase()}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500 }}>{label}</div>
                  {sender?.phone && (
                    <div className="muted text-xs">{sender.phone}</div>
                  )}
                  {locked && (
                    <div
                      className="muted text-xs"
                      style={{
                        color: "var(--danger)",
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                      }}
                    >
                      <Lock size={11} /> Locked by {s.locked_by_campaign_name}
                    </div>
                  )}
                  <RestrictionChip sender={s} />
                </div>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label={`Remove ${label}`}
                  title={
                    locked
                      ? "Locked by another running campaign"
                      : "Remove from pool"
                  }
                  disabled={locked || busy}
                  onClick={() => onDetach(s.sender_id)}
                  style={{ color: "var(--danger)", flexShrink: 0 }}
                >
                  <X size={14} />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* Add to pool */}
      <div style={{ marginTop: 14 }}>
        <div
          className="muted text-xs"
          style={{ marginBottom: 8, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}
        >
          Add account
        </div>
        {eligible.length === 0 ? (
          <div className="muted text-xs">
            No more accounts available to add.
          </div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {eligible.map((s) => {
              const active = s.status === "active";
              // POOL-09: a sender held by another running campaign cannot be
              // attached (backend returns 409 SENDER_LOCK_CONFLICT). Surface the
              // lock here so the account isn't offered as if it were free.
              const locked = !!s.locked_by_campaign_name;
              return (
                <button
                  key={s.id}
                  type="button"
                  className="pill"
                  disabled={busy || locked}
                  onClick={() => onAttach(s.id)}
                  title={
                    locked
                      ? `Locked by running campaign: ${s.locked_by_campaign_name}. Pause it first.`
                      : `Add ${s.name || s.slug} to the pool`
                  }
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    cursor: busy || locked ? "default" : "pointer",
                    opacity: busy || locked ? 0.6 : 1,
                  }}
                >
                  <div
                    className="avatar avatar--sm"
                    style={{
                      background: "var(--tg-blue)",
                      color: "white",
                      width: 18,
                      height: 18,
                      fontSize: 10,
                    }}
                  >
                    {(s.name || s.slug).slice(0, 1).toUpperCase()}
                  </div>
                  <span>{s.name || s.slug}</span>
                  {locked ? (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        color: "var(--danger)",
                        fontSize: 10,
                      }}
                    >
                      <Lock size={11} /> {s.locked_by_campaign_name}
                    </span>
                  ) : (
                    <>
                      <span
                        className={`pill ${active ? "pill--green" : "pill--red"}`}
                        style={{ height: 16, fontSize: 10, padding: "0 6px" }}
                      >
                        {s.status}
                      </span>
                      <Plus size={12} />
                    </>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </section>
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
