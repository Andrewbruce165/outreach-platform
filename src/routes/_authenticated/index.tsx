import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type AnalyticsCards = components["schemas"]["AnalyticsCards"];
type Funnel = components["schemas"]["FunnelResponse"];
type CampaignList = components["schemas"]["CampaignListResponse"];

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

  const a = analyticsQ.data;
  const f = funnelQ.data;
  const hasNoData =
    !analyticsQ.isLoading &&
    !!a &&
    a.sent === 0 &&
    a.replied.message_count === 0 &&
    a.leads === 0;

  return (
    <>
      <Topbar title="Dashboard" />
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        {hasNoData ? (
          <EmptyDashboard />
        ) : (
          <>
            {/* 4 analytics cards */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                gap: 14,
                marginBottom: 20,
              }}
            >
              <Card
                label="Sent"
                value={a?.sent ?? "—"}
                loading={analyticsQ.isLoading}
                error={analyticsQ.error ? errMsg(analyticsQ.error) : null}
              />
              <Card
                label="Replied"
                value={a?.replied.conversation_count ?? "—"}
                sub={a ? `${a.replied.message_count} messages` : undefined}
                loading={analyticsQ.isLoading}
              />
              <Card
                label="Active leads"
                value={a?.leads ?? "—"}
                sub="Not yet finished"
                loading={analyticsQ.isLoading}
              />
              <Card
                label="Finished"
                value={a?.finishes ?? "—"}
                loading={analyticsQ.isLoading}
              />
            </div>

            {/* Funnel */}
            <section className="card" style={{ padding: 18, marginBottom: 20 }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 14 }}>
                Conversion funnel
              </h3>
              {funnelQ.isLoading && <div className="muted">Loading…</div>}
              {funnelQ.error && (
                <div style={{ color: "var(--danger, #c0392b)", fontSize: 13 }}>
                  {errMsg(funnelQ.error)}
                </div>
              )}
              {f && <FunnelChart funnel={f} />}
            </section>

            {/* Recent campaigns */}
            <section className="card" style={{ padding: 18 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 12,
                }}
              >
                <h3 style={{ fontSize: 14, fontWeight: 600 }}>Campaigns</h3>
                <Link to="/campaigns">
                  <button className="btn btn--ghost btn--sm">View all</button>
                </Link>
              </div>
              {campaignsQ.isLoading && <div className="muted">Loading…</div>}
              {campaignsQ.data && campaignsQ.data.items.length === 0 && (
                <p className="muted" style={{ fontSize: 13 }}>
                  No campaigns yet.
                </p>
              )}
              {campaignsQ.data && campaignsQ.data.items.length > 0 && (
                <ul style={{ display: "grid", gap: 8 }}>
                  {campaignsQ.data.items.slice(0, 5).map((c) => (
                    <li
                      key={c.id}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "8px 0",
                        borderBottom: "1px solid var(--border)",
                        fontSize: 13,
                      }}
                    >
                      <span style={{ fontWeight: 500 }}>{c.name}</span>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {c.status} · {c.attached_senders?.length ?? 0} senders
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </>
  );
}

function Card({
  label,
  value,
  sub,
  loading,
  error,
}: {
  label: string;
  value: number | string;
  sub?: string;
  loading?: boolean;
  error?: string | null;
}) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700 }}>
        {loading ? "…" : error ? "—" : value}
      </div>
      {sub && (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function FunnelChart({ funnel }: { funnel: Funnel }) {
  const stages = [
    { key: "sent", label: "Sent", value: funnel.sent, color: "#3b82f6" },
    { key: "replied", label: "Replied", value: funnel.replied, color: "#6366f1" },
    { key: "engaged", label: "Engaged", value: funnel.engaged, color: "#8b5cf6" },
    { key: "lead", label: "Leads", value: funnel.lead, color: "#a855f7" },
    { key: "handoff", label: "Handoffs", value: funnel.handoff, color: "#ec4899" },
  ];
  const max = Math.max(1, ...stages.map((s) => s.value));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {stages.map((s, i) => {
        const pct = (s.value / max) * 100;
        const prev = i > 0 ? stages[i - 1].value : null;
        const dropRate = prev && prev > 0 ? Math.round(((prev - s.value) / prev) * 100) : null;
        return (
          <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 80, fontSize: 12, color: "var(--text-soft)" }}>
              {s.label}
            </div>
            <div
              style={{
                flex: 1,
                height: 26,
                background: "var(--bg-soft, #f7f8fa)",
                borderRadius: 4,
                position: "relative",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: s.color,
                  transition: "width 0.4s ease",
                }}
              />
              <span
                style={{
                  position: "absolute",
                  left: 8,
                  top: 0,
                  lineHeight: "26px",
                  fontSize: 12,
                  fontWeight: 600,
                  color: pct > 30 ? "#fff" : "var(--text)",
                }}
              >
                {s.value.toLocaleString()}
              </span>
            </div>
            <div
              style={{
                width: 60,
                fontSize: 11,
                color: "var(--text-soft)",
                textAlign: "right",
              }}
            >
              {dropRate != null && i > 0 ? `−${dropRate}%` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function EmptyDashboard() {
  return (
    <div className="card">
      <div className="card__body" style={{ textAlign: "center", padding: 48 }}>
        <div style={{ fontSize: 40, marginBottom: 8 }}>👋</div>
        <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 6 }}>
          Welcome to aimly
        </h2>
        <p className="muted" style={{ fontSize: 13, marginBottom: 20, maxWidth: 460, margin: "0 auto 20px" }}>
          You haven't sent anything yet. Connect an account, import contacts, and launch your first campaign.
        </p>
        <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
          <Link to="/accounts">
            <button className="btn btn--primary">1. Connect a TG account</button>
          </Link>
          <Link to="/contacts">
            <button className="btn btn--ghost">2. Import contacts</button>
          </Link>
          <Link to="/campaigns/new">
            <button className="btn btn--ghost">3. Create a campaign</button>
          </Link>
        </div>
      </div>
    </div>
  );
}
