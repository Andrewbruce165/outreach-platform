import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Calendar,
  Edit3,
  Filter,
  Flag,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  Search,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { EditCampaignModal } from "@/components/EditCampaignModal";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type CampaignList = components["schemas"]["CampaignListResponse"];
type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];

export const Route = createFileRoute("/_authenticated/campaigns/")({
  component: CampaignsPage,
});

const TABS = [
  { id: "all", label: "All" },
  { id: "running", label: "Running" },
  { id: "paused", label: "Paused" },
  { id: "scheduled", label: "Scheduled" },
  { id: "draft", label: "Drafts" },
  { id: "finished", label: "Finished" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const STATUS_PILL: Record<
  string,
  { label: string; pill: string; dot: string }
> = {
  running:   { label: "Running",   pill: "pill--green",  dot: "var(--success)" },
  paused:    { label: "Paused",    pill: "pill--orange", dot: "var(--warning)" },
  draft:     { label: "Draft",     pill: "pill--ghost",  dot: "var(--text-faint)" },
  scheduled: { label: "Scheduled", pill: "pill--blue",   dot: "var(--tg-blue)" },
  finished:  { label: "Finished",  pill: "pill--ghost",  dot: "var(--text-faint)" },
  stopped:   { label: "Stopped",   pill: "pill--red",    dot: "var(--danger)" },
};

const AVATAR_COLORS: [string, string][] = [
  ["#3390ec", "#6cb8ff"], ["#8774e1", "#c4b5fd"], ["#4dcd5e", "#94e8a0"],
  ["#f5a623", "#fcd57f"], ["#e13b30", "#f59289"], ["#5eaef4", "#a4d2fa"],
  ["#34a4a4", "#7cd3d3"], ["#b069dc", "#dab1f3"],
];
function avatarStyle(seed: string): React.CSSProperties {
  const i =
    seed.split("").reduce((a, c) => a + c.charCodeAt(0), 0) %
    AVATAR_COLORS.length;
  const [a, b] = AVATAR_COLORS[i];
  return { background: `linear-gradient(135deg, ${a}, ${b})` };
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function CampaignsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabId>("all");
  const [search, setSearch] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<Campaign | null>(null);

  const listQ = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<CampaignList>("/api/v1/campaigns"),
  });

  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () =>
      api<{ agents: Agent[]; total: number }>("/api/v1/agents"),
    staleTime: 60_000,
  });

  const foldersQ = useQuery({
    queryKey: ["folders"],
    queryFn: () => api<Folder[]>("/api/v1/folders"),
    staleTime: 60_000,
  });

  const agentName = useMemo(() => {
    const m = new Map<string, string>();
    agentsQ.data?.agents.forEach((a) => m.set(a.id, a.name));
    return m;
  }, [agentsQ.data]);

  const folderName = useMemo(() => {
    const m = new Map<string, string>();
    foldersQ.data?.forEach((f) => m.set(f.id, f.name));
    return m;
  }, [foldersQ.data]);

  const all = listQ.data?.items ?? [];

  const counts: Record<TabId, number> = useMemo(() => {
    return {
      all: all.length,
      running: all.filter((c) => c.status === "running").length,
      paused: all.filter((c) => c.status === "paused").length,
      scheduled: all.filter((c) => c.status === "scheduled").length,
      draft: all.filter((c) => c.status === "draft").length,
      finished: all.filter((c) => c.status === "finished").length,
    };
  }, [all]);

  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all
      .filter((c) => tab === "all" || c.status === tab)
      .filter((c) => !q || c.name.toLowerCase().includes(q));
  }, [all, tab, search]);

  const lifecycleMut = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "start" | "pause" | "resume" | "stop";
    }) =>
      api<Campaign>(`/api/v1/campaigns/${id}/${action}`, { method: "POST" }),
    onSuccess: (_d, v) => {
      const map = {
        start: "campaign_launched",
        pause: "campaign_paused",
        resume: "campaign_resumed",
      } as const;
      const e = map[v.action as keyof typeof map];
      if (e) track(e, { campaign_id: v.id });
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  const duplicateMut = useMutation({
    mutationFn: (id: string) =>
      api<Campaign>(`/api/v1/campaigns/${id}/duplicate`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaigns"] }),
    onError: (e) => setActionError(errMsg(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/campaigns/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaigns"] }),
    onError: (e) => setActionError(errMsg(e)),
  });

  const busy =
    lifecycleMut.isPending || duplicateMut.isPending || deleteMut.isPending;

  const toggleOne = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  const allVisibleSelected =
    items.length > 0 && items.every((c) => selected.has(c.id));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) items.forEach((c) => next.delete(c.id));
      else items.forEach((c) => next.add(c.id));
      return next;
    });

  const runBulk = async (
    action: "pause" | "stop" | "delete",
  ) => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (action === "delete" && !confirm(`Delete ${ids.length} campaign(s)?`)) return;
    setActionError(null);
    try {
      await Promise.all(
        ids.map((id) =>
          action === "delete"
            ? deleteMut.mutateAsync(id)
            : lifecycleMut.mutateAsync({ id, action }),
        ),
      );
      setSelected(new Set());
    } catch (e) {
      setActionError(errMsg(e));
    }
  };

  return (
    <>
      <Topbar
        title="Campaigns"
        right={
          <>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "0 12px",
                height: 36,
                background: "var(--bg-soft)",
                borderRadius: 9,
                color: "var(--text-muted)",
              }}
            >
              <Search size={14} />
              <input
                placeholder="Search campaigns…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  background: "none",
                  border: "none",
                  outline: "none",
                  width: 220,
                  fontSize: 13,
                  color: "var(--text)",
                }}
              />
            </div>
            <button className="btn btn--ghost btn--sm" disabled title="Filters (v2)">
              <Filter size={14} /> Filters
            </button>
            <Link to="/campaigns/new">
              <button className="btn btn--primary btn--sm">
                <Plus size={14} /> New campaign
              </button>
            </Link>
          </>
        }
      />

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? "is-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label} <span className="count">{counts[t.id]}</span>
          </button>
        ))}
      </div>

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

        {listQ.isLoading && (
          <div className="muted" style={{ padding: 24 }}>
            Loading campaigns…
          </div>
        )}
        {listQ.error && (
          <div
            className="card"
            style={{ padding: 16, color: "var(--danger)" }}
          >
            {errMsg(listQ.error)}
          </div>
        )}

        {listQ.data && items.length === 0 && (
          <EmptyState filtered={tab !== "all" || search.length > 0} />
        )}

        {selected.size > 0 && (
          <div
            className="card"
            style={{
              padding: "10px 14px",
              marginBottom: 12,
              display: "flex",
              alignItems: "center",
              gap: 12,
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 500 }}>
              {selected.size} selected
            </span>
            <div style={{ flex: 1 }} />
            <button
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={() => runBulk("pause")}
            >
              <Pause size={14} /> Pause
            </button>
            <button
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={() => runBulk("stop")}
            >
              Stop
            </button>
            <button
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={() => runBulk("delete")}
              style={{ color: "var(--danger)" }}
            >
              Delete
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setSelected(new Set())}
            >
              Clear
            </button>
          </div>
        )}

        {items.length > 0 && (
          <div className="card">
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={toggleAll}
                      aria-label="Select all"
                    />
                  </th>
                  <th>Campaign</th>
                  <th>Status</th>
                  <th>Agent · Folder</th>
                  <th>Senders</th>
                  <th>Progress</th>
                  <th style={{ textAlign: "right" }}>Funnel (sent → leads)</th>
                  <th style={{ width: 40 }} />
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <CampaignRow
                    key={c.id}
                    campaign={c}
                    agentLabel={agentName.get(c.agent_id) ?? "—"}
                    folderLabel={folderName.get(c.folder_id) ?? "—"}
                    busy={busy}
                    selected={selected.has(c.id)}
                    onToggleSelect={() => toggleOne(c.id)}
                    onLifecycle={(action) => {
                      setActionError(null);
                      lifecycleMut.mutate({ id: c.id, action });
                    }}
                    onDuplicate={() => {
                      setActionError(null);
                      duplicateMut.mutate(c.id);
                    }}
                    onDelete={() => {
                      if (confirm(`Delete campaign "${c.name}"?`)) {
                        setActionError(null);
                        deleteMut.mutate(c.id);
                      }
                    }}
                    onEdit={() => setEditing(c)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {editing && (
        <EditCampaignModal
          campaign={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function statusIcon(status: string) {
  switch (status) {
    case "running":
      return <Play size={15} />;
    case "paused":
      return <Pause size={15} />;
    case "scheduled":
      return <Calendar size={15} />;
    case "draft":
      return <Edit3 size={15} />;
    case "finished":
      return <Flag size={15} />;
    default:
      return <Play size={15} />;
  }
}

function statusIconStyle(status: string): React.CSSProperties {
  const map: Record<string, { bg: string; fg: string }> = {
    running: { bg: "var(--success-soft)", fg: "#1e8a3a" },
    paused: { bg: "var(--warning-soft)", fg: "#a86200" },
    scheduled: { bg: "var(--tg-blue-soft)", fg: "var(--tg-blue)" },
    draft: { bg: "var(--bg-soft)", fg: "var(--text-muted)" },
    finished: { bg: "var(--bg-soft)", fg: "var(--text-muted)" },
  };
  const s = map[status] ?? map.draft;
  return {
    width: 36,
    height: 36,
    borderRadius: 9,
    background: s.bg,
    color: s.fg,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };
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

function SenderAvatars({ campaign }: { campaign: Campaign }) {
  const senders = campaign.attached_senders ?? [];
  const n = senders.length;
  if (n === 0) return <span className="muted text-xs">—</span>;
  const shown = senders.slice(0, 3);
  return (
    <div style={{ display: "flex", alignItems: "center" }}>
      {shown.map((s, i) => (
        <div
          key={s.sender_id}
          style={{
            width: 22,
            height: 22,
            borderRadius: "50%",
            marginLeft: i ? -6 : 0,
            ...avatarStyle(s.sender_id),
            border: "2px solid white",
            fontSize: 9,
            color: "white",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 600,
          }}
        >
          {String.fromCharCode(65 + i)}
        </div>
      ))}
      {n > 3 && (
        <span
          style={{ marginLeft: 6, fontSize: 11.5, color: "var(--text-muted)" }}
        >
          +{n - 3}
        </span>
      )}
    </div>
  );
}

function FunnelMini({
  sent,
  replied,
  leads,
}: {
  sent: number;
  replied: number;
  leads: number;
}) {
  const max = Math.max(sent, 1);
  const bar = (v: number, color: string) => (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div
        style={{
          width: 50,
          height: 4,
          background: "var(--bg-soft)",
          borderRadius: 999,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${(v / max) * 100}%`,
            height: "100%",
            background: color,
            borderRadius: 999,
          }}
        />
      </div>
      <span
        className="num text-xs"
        style={{ minWidth: 36, textAlign: "right", color: "var(--text-soft)" }}
      >
        {v.toLocaleString()}
      </span>
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {bar(sent, "var(--tg-blue)")}
      {bar(replied, "var(--ai-purple)")}
      {bar(leads, "var(--success)")}
    </div>
  );
}

function CampaignRow({
  campaign: c,
  agentLabel,
  folderLabel,
  busy,
  selected,
  onToggleSelect,
  onLifecycle,
  onDuplicate,
  onDelete,
  onEdit,
}: {
  campaign: Campaign;
  agentLabel: string;
  folderLabel: string;
  busy: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onLifecycle: (a: "start" | "pause" | "resume" | "stop") => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  // Per-campaign analytics (sent / replied / leads, progress)
  const statsQ = useQuery({
    queryKey: ["campaign-analytics", c.id],
    queryFn: () =>
      api<{
        sent?: number;
        replied?: { conversation_count?: number; message_count?: number };
        leads?: number;
        finishes?: number;
      }>(`/api/v1/analytics/campaigns/${c.id}`),
    staleTime: 30_000,
    retry: false,
  });

  const sent = statsQ.data?.sent ?? 0;
  const replied = statsQ.data?.replied?.conversation_count ?? 0;
  const leads = statsQ.data?.leads ?? 0;
  const finishes = statsQ.data?.finishes ?? 0;
  const progress = sent > 0 ? Math.min(1, finishes / sent) : 0;

  const startedAt =
    c.start_date
      ? new Date(c.start_date).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        })
      : c.status === "draft"
        ? "—"
        : new Date(c.created_at).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          });

  const hours = `${String(c.work_hour_start).padStart(2, "0")}:00 – ${String(
    c.work_hour_end,
  ).padStart(2, "0")}:00 ${c.timezone}`;

  return (
    <tr style={{ cursor: "pointer" }}>
      <td onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          aria-label={`Select ${c.name}`}
        />
      </td>
      <td>
        <Link
          to="/campaigns/$id"
          params={{ id: c.id }}
          style={{ display: "flex", alignItems: "center", gap: 12 }}
        >

          <div style={statusIconStyle(c.status)}>{statusIcon(c.status)}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div
              style={{
                fontWeight: 500,
                fontSize: 13.5,
                color: "var(--text)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                maxWidth: 260,
              }}
            >
              {c.name}
              {c.is_exhausted && (
                <span
                  style={{
                    marginLeft: 8,
                    fontSize: 11,
                    color: "var(--text-faint)",
                    fontWeight: 400,
                  }}
                >
                  (exhausted)
                </span>
              )}
            </div>
            <div
              className="muted text-xs"
              style={{
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {startedAt} · {hours}
            </div>
          </div>
        </Link>
      </td>
      <td>
        <StatusPill status={c.status} />
      </td>
      <td>
        <div style={{ fontSize: 12.5 }}>{agentLabel}</div>
        <div className="muted text-xs">{folderLabel}</div>
      </td>
      <td>
        <SenderAvatars campaign={c} />
      </td>
      <td>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            minWidth: 100,
          }}
        >
          <div
            style={{
              flex: 1,
              height: 5,
              background: "var(--bg-soft)",
              borderRadius: 999,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${progress * 100}%`,
                height: "100%",
                background: "var(--tg-blue)",
                borderRadius: 999,
              }}
            />
          </div>
          <span className="num text-xs muted">
            {Math.round(progress * 100)}%
          </span>
        </div>
      </td>
      <td>
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
          }}
        >
          <FunnelMini sent={sent} replied={replied} leads={leads} />
        </div>
      </td>
      <td onClick={(e) => e.stopPropagation()}>
        <RowMenu
          campaign={c}
          busy={busy}
          onLifecycle={onLifecycle}
          onDuplicate={onDuplicate}
          onDelete={onDelete}
          onEdit={onEdit}
        />
      </td>
    </tr>
  );
}

function RowMenu({
  campaign,
  busy,
  onLifecycle,
  onDuplicate,
  onDelete,
  onEdit,
}: {
  campaign: Campaign;
  busy: boolean;
  onLifecycle: (a: "start" | "pause" | "resume" | "stop") => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onEdit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const s = campaign.status;
  const can = {
    start: s === "draft",
    pause: s === "running",
    resume: s === "paused",
    stop: s === "running" || s === "paused",
    delete: true,
  };

  const itemStyle: React.CSSProperties = {
    display: "block",
    width: "100%",
    textAlign: "left",
    padding: "8px 12px",
    fontSize: 13,
    color: "var(--text)",
    background: "none",
  };

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        className="tb__icon-btn"
        style={{ width: 28, height: 28 }}
        onClick={() => setOpen((v) => !v)}
        aria-label="Campaign actions"
        disabled={busy}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div
          className="card"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            minWidth: 160,
            padding: 4,
            zIndex: 30,
            boxShadow: "var(--shadow-lg)",
          }}
        >
          <button
            style={itemStyle}
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
          >
            Edit
          </button>
          {can.start && (
            <button
              style={itemStyle}
              onClick={() => {
                setOpen(false);
                onLifecycle("start");
              }}
            >
              Start
            </button>
          )}
          {can.pause && (
            <button
              style={itemStyle}
              onClick={() => {
                setOpen(false);
                onLifecycle("pause");
              }}
            >
              Pause
            </button>
          )}
          {can.resume && (
            <button
              style={itemStyle}
              onClick={() => {
                setOpen(false);
                onLifecycle("resume");
              }}
            >
              Resume
            </button>
          )}
          {can.stop && (
            <button
              style={itemStyle}
              onClick={() => {
                setOpen(false);
                onLifecycle("stop");
              }}
            >
              Stop
            </button>
          )}
          <button
            style={itemStyle}
            onClick={() => {
              setOpen(false);
              onDuplicate();
            }}
          >
            Duplicate
          </button>
          {can.delete && (
            <button
              style={{ ...itemStyle, color: "var(--danger)" }}
              onClick={() => {
                setOpen(false);
                onDelete();
              }}
            >
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div
      style={{
        textAlign: "center",
        padding: "64px 24px",
        maxWidth: 460,
        margin: "0 auto",
      }}
    >
      <div style={{ fontSize: 40, marginBottom: 12 }}>📣</div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
        {filtered ? "No matching campaigns" : "No campaigns yet"}
      </h3>
      <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>
        {filtered
          ? "Try a different filter or search term."
          : "A campaign sends your agent's message from your accounts to a folder of contacts."}
      </p>
      <Link to="/campaigns/new">
        <button className="btn btn--primary">
          <Plus size={14} /> New campaign
        </button>
      </Link>
    </div>
  );
}
