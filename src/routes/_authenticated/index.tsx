import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Send,
  MessageCircle,
  Flag,
  User,
  Calendar,
  Filter,
  Download,
  Bell,
  RefreshCw,
  AlertTriangle,
  Check,
  X,
  Rocket,
  ChevronDown,
  ArrowRight,
  CircleDollarSign,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type AnalyticsCards = components["schemas"]["AnalyticsCards"];
type Funnel = components["schemas"]["FunnelResponse"];
type CampaignList = components["schemas"]["CampaignListResponse"];
type Campaign = components["schemas"]["CampaignResponse"];
type Sender = components["schemas"]["SenderResponse"];
type ConversationList = components["schemas"]["ConversationListResponse"];
type Conversation = components["schemas"]["ConversationResponse"];

export const Route = createFileRoute("/_authenticated/")({
  component: Dashboard,
});

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

type PeriodKey = "24h" | "7d" | "30d" | "90d";
const PERIOD_LABELS: Record<PeriodKey, string> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
};
const PERIOD_MS: Record<PeriodKey, number> = {
  "24h": 24 * 3600_000,
  "7d": 7 * 24 * 3600_000,
  "30d": 30 * 24 * 3600_000,
  "90d": 90 * 24 * 3600_000,
};
// Backend `since` values for the analytics cards endpoints ("24h" maps to "1d").
const PERIOD_SINCE: Record<PeriodKey, "1d" | "7d" | "30d" | "90d"> = {
  "24h": "1d",
  "7d": "7d",
  "30d": "30d",
  "90d": "90d",
};

type DashFilters = {
  campaignId: string | null;
  senderId: string | null;
  status: string | null;
};

function Dashboard() {
  useEffect(() => {
    if (sessionStorage.getItem("dashboard_viewed_once") !== "1") {
      sessionStorage.setItem("dashboard_viewed_once", "1");
      track("dashboard_viewed", {});
    }
  }, []);

  const [period, setPeriod] = useState<PeriodKey>("7d");
  const [filters, setFilters] = useState<DashFilters>({
    campaignId: null,
    senderId: null,
    status: null,
  });

  // KPI cards follow the campaign/sender filter (campaign wins, mirroring the
  // funnel scope below) and the period selector via the backend `since` param.
  const cardsPath = filters.campaignId
    ? `/api/v1/analytics/campaigns/${filters.campaignId}`
    : filters.senderId
      ? `/api/v1/analytics/senders/${filters.senderId}`
      : "/api/v1/analytics/workspace";
  const analyticsQ = useQuery({
    queryKey: [
      "analytics",
      "cards",
      filters.campaignId ?? filters.senderId ?? "workspace",
      period,
    ],
    queryFn: () =>
      api<AnalyticsCards>(cardsPath, {
        query: { since: PERIOD_SINCE[period] },
      }),
    refetchInterval: 30_000,
  });

  const funnelScope: "workspace" | "campaign" | "sender" = filters.campaignId
    ? "campaign"
    : filters.senderId
      ? "sender"
      : "workspace";
  const funnelScopeId = filters.campaignId ?? filters.senderId ?? null;
  const funnelQ = useQuery({
    queryKey: ["analytics", "funnel", funnelScope, funnelScopeId],
    queryFn: () =>
      api<Funnel>("/api/v1/analytics/funnel", {
        query: {
          scope: funnelScope,
          ...(funnelScopeId ? { id: funnelScopeId } : {}),
        },
      }),
    refetchInterval: 30_000,
  });
  const campaignsQ = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<CampaignList>("/api/v1/campaigns"),
  });
  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
    refetchInterval: 30_000,
  });
  const conversationsQ = useQuery({
    queryKey: ["conversations", { dashboard: true }],
    queryFn: () =>
      api<ConversationList>("/api/v1/conversations", { query: { limit: 50 } }),
    refetchInterval: 30_000,
  });

  const a = analyticsQ.data;
  const f = funnelQ.data;
  const senders = sendersQ.data?.senders ?? [];
  const allCampaigns = campaignsQ.data?.items ?? [];
  const allConversations = conversationsQ.data?.conversations ?? [];

  const periodCutoff = Date.now() - PERIOD_MS[period];

  const filteredCampaigns = useMemo(() => {
    return allCampaigns.filter((c) => {
      const ts = new Date(c.updated_at || c.created_at).getTime();
      if (ts < periodCutoff && c.status !== "running") return false;
      if (filters.status && c.status !== filters.status) return false;
      if (filters.campaignId && c.id !== filters.campaignId) return false;
      if (filters.senderId) {
        const has = (c.attached_senders ?? []).some(
          (s) => s.sender_id === filters.senderId,
        );
        if (!has) return false;
      }
      return true;
    });
  }, [allCampaigns, filters, periodCutoff]);

  const filteredConversations = useMemo(() => {
    return allConversations.filter((c) => {
      const ts = c.last_message_at
        ? new Date(c.last_message_at).getTime()
        : new Date(c.updated_at).getTime();
      if (ts < periodCutoff) return false;
      if (filters.campaignId && c.campaign_id !== filters.campaignId) return false;
      if (filters.senderId && c.sender_id !== filters.senderId) return false;
      return true;
    });
  }, [allConversations, filters, periodCutoff]);

  // Per-sender analytics to power the daily-rate bars in Account health.
  const senderAnalyticsQs = useQueries({
    queries: senders.map((s) => ({
      queryKey: ["analytics", "sender", s.id],
      queryFn: () => api<AnalyticsCards>(`/api/v1/analytics/senders/${s.id}`),
      refetchInterval: 30_000,
      staleTime: 15_000,
    })),
  });
  const sentBySenderId = useMemo(() => {
    const m: Record<string, number> = {};
    senders.forEach((s, i) => {
      const d = senderAnalyticsQs[i]?.data;
      if (d) m[s.id] = d.sent ?? 0;
    });
    return m;
  }, [senders, senderAnalyticsQs]);

  const sent = a?.sent ?? 0;
  const replyCount = a?.replied.conversation_count ?? 0;
  const replyRate = sent > 0 ? Math.round((replyCount / sent) * 1000) / 10 : 0;
  const leads = a?.leads ?? 0;
  const finished = a?.finishes ?? 0;
  const llmSpendUsd = (a?.llm_spend_usd_cents ?? 0) / 100;

  const activeFilterCount =
    (filters.campaignId ? 1 : 0) +
    (filters.senderId ? 1 : 0) +
    (filters.status ? 1 : 0);

  const handleExport = () => {
    const headers = [
      "id",
      "name",
      "status",
      "primary_goal",
      "attached_senders",
      "created_at",
      "updated_at",
    ];
    const rows = filteredCampaigns.map((c) =>
      [
        c.id,
        c.name,
        c.status,
        c.primary_goal ?? "",
        (c.attached_senders ?? []).length,
        c.created_at,
        c.updated_at,
      ]
        .map((v) => {
          const s = String(v ?? "");
          return s.includes(",") || s.includes('"') || s.includes("\n")
            ? `"${s.replace(/"/g, '""')}"`
            : s;
        })
        .join(","),
    );
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `dashboard-${period}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const funnelSubtitle =
    funnelScope === "workspace"
      ? "all campaigns"
      : funnelScope === "campaign"
        ? `campaign: ${allCampaigns.find((c) => c.id === filters.campaignId)?.name ?? "—"}`
        : `sender: ${senders.find((s) => s.id === filters.senderId)?.name ?? "—"}`;

  return (
    <>
      <Topbar
        title="Welcome back"
        crumbs={[{ label: PERIOD_LABELS[period] }]}
        right={
          <>
            <PeriodMenu value={period} onChange={setPeriod} />
            <FiltersMenu
              filters={filters}
              onChange={setFilters}
              campaigns={allCampaigns}
              senders={senders}
              activeCount={activeFilterCount}
            />
            <button
              className="btn btn--ghost btn--sm"
              type="button"
              onClick={handleExport}
            >
              <Download size={14} /> Export
            </button>
            <button className="tb__icon-btn" type="button" aria-label="Notifications">
              <Bell size={18} />
            </button>
          </>
        }
      />

      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        {/* KPI cards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
            gap: 14,
            marginBottom: 14,
          }}
        >
          <KpiCard
            label="Messages sent"
            value={sent.toLocaleString()}
            sub={analyticsQ.isLoading ? "Loading…" : PERIOD_LABELS[period]}
            icon={<Send size={14} />}
            color="var(--tg-blue, #3390ec)"
            spark={[180, 210, 240, 260, 295, 310, 340, 360, 380, 395, 410, 420]}
          />
          <KpiCard
            label="Reply rate"
            value={`${replyRate}%`}
            sub={`${replyCount.toLocaleString()} of ${sent.toLocaleString()} replied`}
            icon={<MessageCircle size={14} />}
            color="var(--ai-purple, #8774e1)"
            spark={[18, 19, 21, 22, 23, 22, 24, 23, 24, 25, 24, 25]}
          />
          <KpiCard
            label="Leads"
            value={leads.toLocaleString()}
            sub={`Active leads · ${PERIOD_LABELS[period]}`}
            icon={<Flag size={14} />}
            color="var(--success, #4dcd5e)"
            spark={[3, 5, 7, 8, 12, 14, 18, 20, 24, 29, 34, 38]}
          />
          <KpiCard
            label="Finished"
            value={finished.toLocaleString()}
            sub={`Conversations closed · ${PERIOD_LABELS[period]}`}
            icon={<User size={14} />}
            color="var(--warning, #f59e0b)"
            spark={[2, 3, 4, 3, 5, 4, 5, 4, 4, 3, 2, 2]}
          />
          <KpiCard
            label="LLM spend"
            value={`$${llmSpendUsd.toFixed(2)}`}
            sub={`AI responder · ${PERIOD_LABELS[period]}`}
            icon={<CircleDollarSign size={14} />}
            color="var(--ai-purple, #8774e1)"
            spark={[1, 1, 2, 2, 3, 3, 4, 5, 5, 6, 7, 8]}
          />
        </div>

        {/* Funnel + Account health */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.6fr 1fr",
            gap: 14,
            marginBottom: 14,
          }}
        >
          <div className="card" style={{ display: "flex", flexDirection: "column" }}>
            <div className="card__header">
              <div>
                <div className="card__title">Conversion funnel</div>
                <div className="card__sub">Sent → Handoff · {funnelSubtitle}</div>
              </div>
              <div className="spacer" />
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--text-muted)",
                  fontSize: 11.5,
                }}
              >
                <span className="live-dot" />
                <span>updating live</span>
              </div>
            </div>
            <div
              style={{
                padding: "22px 26px 24px",
                flex: 1,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
              }}
            >
              {funnelQ.isLoading && <div className="muted">Loading…</div>}
              {funnelQ.error && (
                <div style={{ color: "var(--danger)", fontSize: 13 }}>{errMsg(funnelQ.error)}</div>
              )}
              {f && <SankeyFunnel funnel={f} />}
            </div>
          </div>

          <AccountHealthCard
            senders={senders}
            loading={sendersQ.isLoading}
            sentBySenderId={sentBySenderId}
            onRefresh={() => {
              void sendersQ.refetch();
              senderAnalyticsQs.forEach((q) => void q.refetch());
            }}
          />
        </div>

        {/* Campaign performance + Activity */}
        <div style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr", gap: 14 }}>
          <CampaignPerformanceCard
            items={filteredCampaigns}
            loading={campaignsQ.isLoading}
            periodLabel={PERIOD_LABELS[period]}
          />
          <ActivityFeedCard
            conversations={filteredConversations}
            campaigns={filteredCampaigns}
            senders={senders}
            loading={conversationsQ.isLoading}
          />
        </div>
      </div>
    </>
  );
}

/* ----- Period dropdown ----- */
function useOutsideClose(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open, close]);
  return ref;
}

function PeriodMenu({
  value,
  onChange,
}: {
  value: PeriodKey;
  onChange: (v: PeriodKey) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose(open, () => setOpen(false));
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        className="btn btn--ghost btn--sm"
        type="button"
        onClick={() => setOpen((o) => !o)}
      >
        <Calendar size={14} /> {PERIOD_LABELS[value]} <ChevronDown size={12} />
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            background: "white",
            border: "1px solid var(--border)",
            borderRadius: 10,
            boxShadow: "0 8px 24px rgba(15, 20, 25, 0.10)",
            minWidth: 180,
            padding: 4,
            zIndex: 30,
          }}
        >
          {(Object.keys(PERIOD_LABELS) as PeriodKey[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => {
                onChange(k);
                setOpen(false);
              }}
              style={{
                display: "flex",
                width: "100%",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 10px",
                fontSize: 13,
                border: "none",
                background: value === k ? "var(--bg-soft)" : "transparent",
                borderRadius: 6,
                cursor: "pointer",
                textAlign: "left",
              }}
            >
              <span>{PERIOD_LABELS[k]}</span>
              {value === k && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ----- Filters popover ----- */
function FiltersMenu({
  filters,
  onChange,
  campaigns,
  senders,
  activeCount,
}: {
  filters: DashFilters;
  onChange: (f: DashFilters) => void;
  campaigns: Campaign[];
  senders: Sender[];
  activeCount: number;
}) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose(open, () => setOpen(false));
  const statuses = ["running", "paused", "draft", "finished", "stopped"];
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        className="btn btn--ghost btn--sm"
        type="button"
        onClick={() => setOpen((o) => !o)}
      >
        <Filter size={14} /> Filters
        {activeCount > 0 && (
          <span
            style={{
              marginLeft: 4,
              background: "var(--tg-blue, #3390ec)",
              color: "white",
              borderRadius: 10,
              padding: "1px 6px",
              fontSize: 10.5,
              fontWeight: 600,
              minWidth: 16,
              textAlign: "center",
            }}
          >
            {activeCount}
          </span>
        )}
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            background: "white",
            border: "1px solid var(--border)",
            borderRadius: 10,
            boxShadow: "0 8px 24px rgba(15, 20, 25, 0.10)",
            width: 280,
            padding: 14,
            zIndex: 30,
            display: "grid",
            rowGap: 12,
          }}
        >
          <FilterSelect
            label="Campaign"
            value={filters.campaignId}
            onChange={(v) => onChange({ ...filters, campaignId: v })}
            options={campaigns.map((c) => ({ value: c.id, label: c.name }))}
          />
          <FilterSelect
            label="Sender"
            value={filters.senderId}
            onChange={(v) => onChange({ ...filters, senderId: v })}
            options={senders.map((s) => ({
              value: s.id,
              label: s.name || s.phone || s.slug,
            }))}
          />
          <FilterSelect
            label="Campaign status"
            value={filters.status}
            onChange={(v) => onChange({ ...filters, status: v })}
            options={statuses.map((s) => ({
              value: s,
              label: s[0].toUpperCase() + s.slice(1),
            }))}
          />
          <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 4 }}>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => onChange({ campaignId: null, senderId: null, status: null })}
            >
              <X size={12} /> Clear
            </button>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={() => setOpen(false)}
            >
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string | null;
  onChange: (v: string | null) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label style={{ display: "grid", rowGap: 4 }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </span>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        style={{
          height: 32,
          borderRadius: 7,
          border: "1px solid var(--border)",
          padding: "0 8px",
          fontSize: 13,
          background: "white",
        }}
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/* ----- KPI card ----- */
function KpiCard({
  label,
  value,
  sub,
  icon,
  color,
  spark,
}: {
  label: string;
  value: string;
  sub: string;
  icon: React.ReactNode;
  color: string;
  spark: number[];
}) {
  return (
    <div className="metric">
      <div className="metric__head">
        <div
          style={{
            width: 24,
            height: 24,
            borderRadius: 7,
            background: `color-mix(in oklab, ${color} 12%, transparent)`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color,
          }}
        >
          {icon}
        </div>
        {label}
      </div>
      <div className="metric__row">
        <div className="metric__value num">{value}</div>
      </div>
      <div className="metric__row" style={{ justifyContent: "space-between" }}>
        <div className="metric__sub">{sub}</div>
        <Sparkline data={spark} width={70} height={24} color={color} />
      </div>
    </div>
  );
}

/* ----- Sparkline ----- */
function Sparkline({
  data,
  width,
  height,
  color,
}: {
  data: number[];
  width: number;
  height: number;
  color: string;
}) {
  if (data.length === 0) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const points = data
    .map((v, i) => `${i * step},${height - ((v - min) / range) * height}`)
    .join(" ");
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* ----- Funnel ----- */
function SankeyFunnel({ funnel }: { funnel: Funnel }) {
  const stages = [
    { id: "sent", label: "Sent", value: funnel.sent, color: "#3390ec" },
    { id: "replied", label: "Replied", value: funnel.replied, color: "#5eaef4" },
    { id: "engaged", label: "Engaged", value: funnel.engaged, color: "#8774e1" },
    { id: "lead", label: "Lead", value: funnel.lead, color: "#4dcd5e" },
    { id: "handoff", label: "Handoff", value: funnel.handoff, color: "#16a34a" },
  ];
  const [hovered, setHovered] = useState<number | null>(null);
  const W = 720;
  const H = 200;
  const labelGap = 46;
  const colW = 46;
  const gap = (W - colW * stages.length) / (stages.length - 1);
  const max = Math.max(1, stages[0].value);
  const hFor = (v: number) => Math.max(2, (v / max) * (H - 16));
  const yTop = (v: number) => (H - hFor(v)) / 2;
  const yBot = (v: number) => yTop(v) + hFor(v);

  const ribbon = (
    a: { value: number },
    b: { value: number },
    ax: number,
    bx: number,
  ) => {
    const x1 = ax + colW;
    const x2 = bx;
    const cx = x1 + (x2 - x1) * 0.5;
    return [
      `M ${x1} ${yTop(a.value)}`,
      `C ${cx} ${yTop(a.value)}, ${cx} ${yTop(b.value)}, ${x2} ${yTop(b.value)}`,
      `L ${x2} ${yBot(b.value)}`,
      `C ${cx} ${yBot(b.value)}, ${cx} ${yBot(a.value)}, ${x1} ${yBot(a.value)}`,
      "Z",
    ].join(" ");
  };

  const totalSent = stages[0].value || 1;
  const h = hovered != null ? stages[hovered] : null;
  const hPrev = hovered != null && hovered > 0 ? stages[hovered - 1] : null;
  const stepPct = h && hPrev ? (hPrev.value > 0 ? (h.value / hPrev.value) * 100 : 0) : null;
  const overallPct = h ? (h.value / totalSent) * 100 : null;
  const dropoff = h && hPrev ? Math.max(0, hPrev.value - h.value) : null;
  // Tooltip horizontal position (% of card width)
  const tipLeft =
    hovered != null
      ? ((hovered * (colW + gap) + colW / 2) / W) * 100
      : 0;

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <svg
        viewBox={`0 0 ${W} ${H + labelGap}`}
        width="100%"
        style={{ display: "block", overflow: "visible" }}
        onMouseLeave={() => setHovered(null)}
      >
        <defs>
          {stages.slice(0, -1).map((s, i) => (
            <linearGradient key={s.id} id={`band-${s.id}`} x1="0" x2="1">
              <stop offset="0%" stopColor={s.color} />
              <stop offset="100%" stopColor={stages[i + 1].color} />
            </linearGradient>
          ))}
        </defs>

        {stages.slice(0, -1).map((s, i) => {
          const next = stages[i + 1];
          const ax = i * (colW + gap);
          const bx = (i + 1) * (colW + gap);
          const dimmed = hovered != null && hovered !== i && hovered !== i + 1;
          return (
            <path
              key={`flow-${s.id}`}
              d={ribbon(s, next, ax, bx)}
              fill={`url(#band-${s.id})`}
              opacity={dimmed ? 0.18 : 0.4}
              style={{ transition: "opacity 120ms ease" }}
            />
          );
        })}

        {stages.map((s, i) => {
          const x = i * (colW + gap);
          const isActive = hovered === i;
          return (
            <g key={s.id}>
              <rect
                x={x}
                y={yTop(s.value)}
                width={colW}
                height={hFor(s.value)}
                rx="4"
                fill={s.color}
                opacity={hovered != null && !isActive ? 0.55 : 1}
                style={{ transition: "opacity 120ms ease" }}
              />
              <text
                x={x + colW / 2}
                y={H + 20}
                textAnchor="middle"
                fontSize="10"
                fill="var(--text-faint)"
                fontWeight="600"
                style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}
              >
                {s.label}
              </text>
              <text
                x={x + colW / 2}
                y={H + 40}
                textAnchor="middle"
                fontSize="17"
                fontWeight="600"
                fill="var(--text)"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {s.value.toLocaleString()}
              </text>
            </g>
          );
        })}

        {stages.slice(0, -1).map((s, i) => {
          const next = stages[i + 1];
          const pct = s.value > 0 ? (next.value / s.value) * 100 : 0;
          const label = pct < 10 ? pct.toFixed(1) + "%" : Math.round(pct) + "%";
          const cx = i * (colW + gap) + colW + gap / 2;
          return (
            <g key={`cr-${s.id}`}>
              <rect
                x={cx - 22}
                y={yTop(next.value) - 22}
                width="44"
                height="18"
                rx="9"
                fill="white"
                stroke="var(--border)"
              />
              <text
                x={cx}
                y={yTop(next.value) - 9}
                textAnchor="middle"
                fontSize="10.5"
                fill="var(--text-soft)"
                fontWeight="600"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {label}
              </text>
            </g>
          );
        })}

        {/* Invisible hit-areas for hover per stage */}
        {stages.map((s, i) => {
          const x = i * (colW + gap);
          return (
            <rect
              key={`hit-${s.id}`}
              x={x - gap / 2}
              y={0}
              width={colW + gap}
              height={H + labelGap}
              fill="transparent"
              style={{ cursor: "pointer" }}
              onMouseEnter={() => setHovered(i)}
            />
          );
        })}
      </svg>

      {h && (
        <div
          style={{
            position: "absolute",
            left: `${tipLeft}%`,
            top: 0,
            transform: "translate(-50%, -100%)",
            background: "var(--text, #0f1419)",
            color: "white",
            borderRadius: 8,
            padding: "10px 12px",
            fontSize: 12,
            lineHeight: 1.45,
            minWidth: 180,
            boxShadow: "0 6px 20px rgba(15, 20, 25, 0.18)",
            pointerEvents: "none",
            zIndex: 5,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 6,
              fontWeight: 600,
              fontSize: 12.5,
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: 50,
                background: h.color,
                display: "inline-block",
              }}
            />
            {h.label}
            <span style={{ marginLeft: "auto", opacity: 0.65, fontWeight: 500 }}>
              {h.value.toLocaleString()}
            </span>
          </div>
          <div style={{ opacity: 0.8, display: "grid", rowGap: 3 }}>
            <Row k="% of sent" v={`${overallPct!.toFixed(1)}%`} />
            {stepPct != null && hPrev && (
              <Row
                k={`vs ${hPrev.label}`}
                v={`${stepPct < 10 ? stepPct.toFixed(1) : Math.round(stepPct)}%`}
              />
            )}
            {dropoff != null && hPrev && (
              <Row
                k="Drop-off"
                v={`−${dropoff.toLocaleString()} (${hPrev.label})`}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span style={{ opacity: 0.7 }}>{k}</span>
      <span style={{ fontWeight: 600 }}>{v}</span>
    </div>
  );
}

/* ----- Account health ----- */
function Donut({
  value,
  size = 86,
  stroke = 9,
  color,
  label,
}: {
  value: number;
  size?: number;
  stroke?: number;
  color: string;
  label: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const dash = Math.max(0, Math.min(1, value)) * c;
  return (
    <svg width={size} height={size}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--bg-soft)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${dash} ${c - dash}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x={size / 2}
        y={size / 2}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize="15"
        fontWeight="700"
        fill="var(--text)"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {label}
      </text>
    </svg>
  );
}

function CorridorBar({ value, limit }: { value: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (value / limit) * 100) : 0;
  const color =
    pct > 90 ? "var(--danger)" : pct > 70 ? "var(--warning)" : "var(--success)";
  return (
    <div
      style={{
        height: 6,
        borderRadius: 4,
        background: "var(--bg-soft)",
        overflow: "hidden",
      }}
    >
      <div style={{ width: `${pct}%`, height: "100%", background: color }} />
    </div>
  );
}

function AccountHealthCard({
  senders,
  loading,
  sentBySenderId,
  onRefresh,
}: {
  senders: Sender[];
  loading: boolean;
  sentBySenderId: Record<string, number>;
  onRefresh: () => void;
}) {
  const total = senders.length;
  const active = senders.filter((s) => s.status === "active").length;
  const warmup = senders.filter((s) => s.status === "warmup").length;
  const paused = senders.filter((s) => s.status === "paused").length;
  const err = senders.filter((s) => s.status === "error").length;
  const healthPct = total > 0 ? (active + warmup * 0.5) / total : 0;
  const errSender = senders.find((s) => s.status === "error");

  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Account health</div>
          <div className="card__sub">
            {loading ? "Loading…" : `${total} Telegram accounts connected`}
          </div>
        </div>
        <div className="spacer" />
        <button
          className="btn btn--sm btn--ghost"
          type="button"
          aria-label="Refresh"
          onClick={onRefresh}
        >
          <RefreshCw size={12} />
        </button>
      </div>

      {total === 0 && !loading ? (
        <div style={{ padding: 32, textAlign: "center" }}>
          <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
            No accounts connected yet.
          </p>
          <Link to="/accounts">
            <button className="btn btn--primary btn--sm" type="button">
              Connect a TG account
            </button>
          </Link>
        </div>
      ) : (
        <>
          <div
            style={{
              padding: "18px 18px 14px",
              display: "flex",
              alignItems: "center",
              gap: 18,
            }}
          >
            <Donut
              value={healthPct}
              color="var(--success)"
              label={`${Math.round(healthPct * 100)}%`}
            />
            <div
              style={{
                flex: 1,
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 10,
              }}
            >
              <HealthRow label="Active" count={active} color="var(--success)" />
              <HealthRow label="Warm-up" count={warmup} color="var(--warning)" />
              <HealthRow label="Paused" count={paused} color="var(--text-faint)" />
              <HealthRow label="Error" count={err} color="var(--danger)" />
            </div>
          </div>

          <div style={{ padding: "0 18px 18px" }}>
            <div
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                marginBottom: 10,
                fontWeight: 500,
              }}
            >
              Daily rate limit per account
            </div>
            {senders.slice(0, 4).map((s) => (
              <div
                key={s.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "120px 1fr 60px",
                  alignItems: "center",
                  gap: 10,
                  padding: "7px 0",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    minWidth: 0,
                  }}
                >
                  <div
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: 50,
                      background: "var(--bg-soft)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 10,
                      fontWeight: 600,
                      color: "var(--text-muted)",
                      flexShrink: 0,
                    }}
                  >
                    {(s.name || s.phone || "?").slice(0, 2).toUpperCase()}
                  </div>
                  <span
                    style={{
                      fontSize: 12,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {s.name || s.phone}
                  </span>
                </div>
                <CorridorBar
                  value={sentBySenderId[s.id] ?? 0}
                  limit={s.rate_limits?.per_day ?? 300}
                />
                <span
                  style={{
                    fontSize: 11.5,
                    color: "var(--text-muted)",
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {(sentBySenderId[s.id] ?? 0).toLocaleString()}/
                  {s.rate_limits?.per_day ?? 300}
                </span>
              </div>
            ))}
          </div>

          {errSender && (
            <div
              style={{
                borderTop: "1px solid var(--divider, var(--border))",
                padding: "10px 18px",
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 12,
                color: "var(--danger)",
              }}
            >
              <AlertTriangle size={14} />
              <span>
                <b>{errSender.name || errSender.phone}</b> needs re-auth
              </span>
              <div className="spacer" />
              <Link to="/accounts">
                <button
                  className="btn btn--sm"
                  type="button"
                  style={{
                    background: "var(--danger-soft)",
                    color: "var(--danger)",
                  }}
                >
                  Re-auth
                </button>
              </Link>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function HealthRow({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: 50,
          background: color,
        }}
      />
      <span style={{ fontSize: 12, color: "var(--text-soft)" }}>{label}</span>
      <span
        style={{
          marginLeft: "auto",
          fontWeight: 600,
          fontSize: 13,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {count}
      </span>
    </div>
  );
}

/* ----- Campaign performance ----- */
function statusDot(status: string) {
  const map: Record<string, string> = {
    running: "var(--success, #4dcd5e)",
    paused: "var(--warning, #f59e0b)",
    finished: "var(--text-muted)",
    draft: "var(--text-faint)",
    stopped: "var(--danger)",
  };
  return map[status] || "var(--text-muted)";
}

function CampaignPerformanceCard({
  items,
  loading,
  periodLabel,
}: {
  items: components["schemas"]["CampaignResponse"][];
  loading: boolean;
  periodLabel: string;
}) {
  const rows = items.slice(0, 5);
  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Campaign performance</div>
          <div className="card__sub">{periodLabel}</div>
        </div>
        <div className="spacer" />
        <Link to="/campaigns">
          <button className="btn btn--sm btn--ghost" type="button">
            See all <ArrowRight size={12} />
          </button>
        </Link>
      </div>
      {loading && (
        <div className="muted" style={{ padding: 18, fontSize: 13 }}>
          Loading…
        </div>
      )}
      {!loading && rows.length === 0 && (
        <div style={{ padding: 32, textAlign: "center" }}>
          <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
            No campaigns yet.
          </p>
          <Link to="/campaigns/new">
            <button className="btn btn--primary btn--sm" type="button">
              Create a campaign
            </button>
          </Link>
        </div>
      )}
      {!loading && rows.length > 0 && (
        <table className="tbl">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Status</th>
              <th>Senders</th>
              <th>Trend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 50,
                        background: statusDot(r.status),
                        flexShrink: 0,
                      }}
                    />
                    <div style={{ minWidth: 0 }}>
                      <div
                        style={{
                          fontWeight: 500,
                          fontSize: 13,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          maxWidth: 240,
                        }}
                      >
                        {r.name}
                      </div>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {r.primary_goal || "—"}
                      </div>
                    </div>
                  </div>
                </td>
                <td>
                  <span
                    style={{
                      fontSize: 12,
                      textTransform: "capitalize",
                      color: "var(--text-soft)",
                    }}
                  >
                    {r.status}
                  </span>
                </td>
                <td className="num">{r.attached_senders?.length ?? 0}</td>
                <td>
                  <Sparkline
                    data={[3, 5, 4, 7, 9, 8, 11, 13, 12, 14]}
                    width={80}
                    height={22}
                    color="var(--tg-blue, #3390ec)"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ----- Activity feed (derived from live workspace data) ----- */
type ActivityItem = {
  key: string;
  who: string;
  what: string;
  whom?: string;
  ts: number;
  icon: React.ReactNode;
  color: string;
};

function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d === 1) return "Yesterday";
  if (d < 7) return `${d}d ago`;
  return new Date(ts).toLocaleDateString();
}

function ActivityFeedCard({
  conversations,
  campaigns,
  senders,
  loading,
}: {
  conversations: Conversation[];
  campaigns: Campaign[];
  senders: Sender[];
  loading: boolean;
}) {
  const items = useMemo<ActivityItem[]>(() => {
    const out: ActivityItem[] = [];

    // Notable conversation events (lead / handoff / finished)
    for (const c of conversations) {
      const status = (c.status || "").toLowerCase();
      const ts = c.last_message_at
        ? new Date(c.last_message_at).getTime()
        : new Date(c.updated_at).getTime();
      const name = c.contact_name || c.contact_phone;
      const sender = senders.find((s) => s.id === c.sender_id);
      const senderName = sender?.name || sender?.slug || "Agent";
      if (status === "lead") {
        out.push({
          key: `lead-${c.id}`,
          who: senderName,
          what: "captured a lead:",
          whom: name,
          ts,
          icon: <Flag size={13} />,
          color: "var(--success)",
        });
      } else if (status === "handoff") {
        out.push({
          key: `handoff-${c.id}`,
          who: senderName,
          what: "handed off to manager:",
          whom: name,
          ts,
          icon: <User size={13} />,
          color: "var(--ai-purple, #8774e1)",
        });
      } else if (status === "finished") {
        out.push({
          key: `finished-${c.id}`,
          who: senderName,
          what: "marked finished:",
          whom: name,
          ts,
          icon: <Check size={13} />,
          color: "var(--text-muted)",
        });
      }
    }

    // Campaign lifecycle events
    for (const c of campaigns) {
      const ts = new Date(c.updated_at || c.created_at).getTime();
      if (c.status === "running") {
        out.push({
          key: `launched-${c.id}`,
          who: "You",
          what: "launched campaign",
          whom: c.name,
          ts,
          icon: <Rocket size={13} />,
          color: "var(--tg-blue, #3390ec)",
        });
      } else if (c.status === "paused") {
        out.push({
          key: `paused-${c.id}`,
          who: "System",
          what: "paused campaign",
          whom: c.name,
          ts,
          icon: <AlertTriangle size={13} />,
          color: "var(--warning, #f59e0b)",
        });
      } else if (c.status === "finished" || c.status === "stopped") {
        out.push({
          key: `done-${c.id}`,
          who: "You",
          what: c.status === "stopped" ? "stopped campaign" : "finished campaign",
          whom: c.name,
          ts,
          icon: <Check size={13} />,
          color: "var(--text-muted)",
        });
      }
    }

    // Sender errors surface as system events
    for (const s of senders) {
      if (s.status === "error") {
        const ts = s.last_used_at
          ? new Date(s.last_used_at).getTime()
          : Date.now();
        out.push({
          key: `sender-err-${s.id}`,
          who: "System",
          what: "needs re-auth:",
          whom: s.name || s.phone,
          ts,
          icon: <AlertTriangle size={13} />,
          color: "var(--danger)",
        });
      }
    }

    return out.sort((a, b) => b.ts - a.ts).slice(0, 8);
  }, [conversations, campaigns, senders]);

  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Activity</div>
          <div className="card__sub">Live signals &amp; events</div>
        </div>
      </div>
      {loading && items.length === 0 && (
        <div className="muted" style={{ padding: 18, fontSize: 13 }}>
          Loading…
        </div>
      )}
      {!loading && items.length === 0 && (
        <div className="muted" style={{ padding: 24, fontSize: 13, textAlign: "center" }}>
          No activity yet. Launch a campaign to see live signals here.
        </div>
      )}
      <div style={{ padding: "8px 4px" }}>
        {items.map((a) => (
          <div
            key={a.key}
            style={{
              display: "flex",
              gap: 12,
              padding: "10px 18px",
              alignItems: "flex-start",
            }}
          >
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: 8,
                background: `color-mix(in oklab, ${a.color} 12%, transparent)`,
                color: a.color,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              {a.icon}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13 }}>
                <b>{a.who}</b> <span className="muted">{a.what}</span>{" "}
                {a.whom && <span style={{ fontWeight: 500 }}>{a.whom}</span>}
              </div>
              <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                {relativeTime(a.ts)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
