import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
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
  Upload,
  Rocket,
  ChevronDown,
  ArrowRight,
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

function Dashboard() {
  useEffect(() => {
    if (sessionStorage.getItem("dashboard_viewed_once") !== "1") {
      sessionStorage.setItem("dashboard_viewed_once", "1");
      track("dashboard_viewed", {});
    }
  }, []);

  const analyticsQ = useQuery({
    queryKey: ["analytics", "workspace"],
    queryFn: () => api<AnalyticsCards>("/api/v1/analytics/workspace"),
    refetchInterval: 30_000,
  });
  const funnelQ = useQuery({
    queryKey: ["analytics", "funnel"],
    queryFn: () => api<Funnel>("/api/v1/analytics/funnel"),
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
  const campaigns = campaignsQ.data?.items ?? [];
  const conversations = conversationsQ.data?.conversations ?? [];

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

  return (
    <>
      <Topbar
        title="Welcome back"
        crumbs={[{ label: "Last 7 days" }]}
        right={
          <>
            <button className="btn btn--ghost btn--sm" type="button">
              <Calendar size={14} /> Last 7 days <ChevronDown size={12} />
            </button>
            <button className="btn btn--ghost btn--sm" type="button">
              <Filter size={14} /> Filters
            </button>
            <button className="btn btn--ghost btn--sm" type="button">
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
            gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
            gap: 14,
            marginBottom: 14,
          }}
        >
          <KpiCard
            label="Messages sent"
            value={sent.toLocaleString()}
            sub={analyticsQ.isLoading ? "Loading…" : `Across ${campaigns.length} campaigns`}
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
            sub={`From ${campaigns.filter((c) => c.status === "running").length} active campaigns`}
            icon={<Flag size={14} />}
            color="var(--success, #4dcd5e)"
            spark={[3, 5, 7, 8, 12, 14, 18, 20, 24, 29, 34, 38]}
          />
          <KpiCard
            label="Finished"
            value={finished.toLocaleString()}
            sub="Conversations closed"
            icon={<User size={14} />}
            color="var(--warning, #f59e0b)"
            spark={[2, 3, 4, 3, 5, 4, 5, 4, 4, 3, 2, 2]}
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
                <div className="card__sub">Sent → Handoff · last 7 days</div>
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
            items={campaigns}
            loading={campaignsQ.isLoading}
          />
          <ActivityFeedCard />
        </div>
      </div>
    </>
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

  return (
    <svg
      viewBox={`0 0 ${W} ${H + labelGap}`}
      width="100%"
      style={{ display: "block", overflow: "visible" }}
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
        return (
          <path
            key={`flow-${s.id}`}
            d={ribbon(s, next, ax, bx)}
            fill={`url(#band-${s.id})`}
            opacity="0.4"
          />
        );
      })}

      {stages.map((s, i) => {
        const x = i * (colW + gap);
        return (
          <g key={s.id}>
            <rect
              x={x}
              y={yTop(s.value)}
              width={colW}
              height={hFor(s.value)}
              rx="4"
              fill={s.color}
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
    </svg>
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
}: {
  items: components["schemas"]["CampaignResponse"][];
  loading: boolean;
}) {
  const rows = items.slice(0, 5);
  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Campaign performance</div>
          <div className="card__sub">Last 7 days</div>
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

/* ----- Activity feed (static demo content) ----- */
const ACTIVITY: {
  who: string;
  what: string;
  whom?: string;
  at: string;
  icon: React.ReactNode;
  color: string;
}[] = [
  {
    who: "Maya",
    what: "booked a meeting with",
    whom: "Sophie Turner · UpperCode",
    at: "2m ago",
    icon: <Flag size={13} />,
    color: "var(--success)",
  },
  {
    who: "Theo",
    what: "handed off to manager:",
    whom: "Noah Jansen · Bitline",
    at: "18m ago",
    icon: <User size={13} />,
    color: "var(--ai-purple, #8774e1)",
  },
  {
    who: "System",
    what: "session revoked for @hirot",
    at: "1h ago",
    icon: <AlertTriangle size={13} />,
    color: "var(--danger)",
  },
  {
    who: "Cleo",
    what: "marked finished:",
    whom: "Maya Iwata · Drifthouse",
    at: "3h ago",
    icon: <Check size={13} />,
    color: "var(--text-muted)",
  },
  {
    who: "System",
    what: "added 248 contacts to",
    whom: "SaaS founders · US",
    at: "5h ago",
    icon: <Upload size={13} />,
    color: "var(--tg-blue, #3390ec)",
  },
  {
    who: "Andrew",
    what: "launched campaign",
    whom: "Crypto YouTubers — sponsorship",
    at: "Yesterday",
    icon: <Rocket size={13} />,
    color: "var(--tg-blue, #3390ec)",
  },
];

function ActivityFeedCard() {
  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Activity</div>
          <div className="card__sub">Live signals &amp; events</div>
        </div>
      </div>
      <div style={{ padding: "8px 4px" }}>
        {ACTIVITY.map((a, i) => (
          <div
            key={i}
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
                <b>{a.who}</b>{" "}
                <span className="muted">{a.what}</span>{" "}
                {a.whom && <span style={{ fontWeight: 500 }}>{a.whom}</span>}
              </div>
              <div
                className="muted"
                style={{ fontSize: 11, marginTop: 2 }}
              >
                {a.at}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
