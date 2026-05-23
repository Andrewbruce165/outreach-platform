// dashboard.jsx — overview screen with live pulse viz

function Dashboard({ onOpenCampaign }) {
  return (
    <>
      <div className="tb">
        <div>
          <div className="tb__title">Welcome back, Andrew</div>
          <div className="tb__crumb">Last 7 days · Apr 23 – Apr 29</div>
        </div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm">
            <Icon name="calendar" size={14} /> Apr 23 — Apr 29
            <Icon name="chevron_down" size={12} />
          </button>
          <button className="btn btn--ghost btn--sm">
            <Icon name="filter" size={14} /> Filters
          </button>
          <button className="btn btn--ghost btn--sm">
            <Icon name="export" size={14} /> Export
          </button>
          <div style={{ width: 8 }} />
          <button className="tb__icon-btn">
            <Icon name="bell" size={18} />
            <span className="pulse" />
          </button>
          <div className="tb__user">
            <div className="tb__avatar">AA</div>
            <span style={{ fontSize: 13, fontWeight: 500 }}>Andrew</span>
          </div>
        </div>
      </div>

      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        {/* KPI cards */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 14 }}>
          <KpiCard
            label="Messages sent" value={2106} delta="+18.4%" up
            sub="↑ 327 vs last week"
            icon="send" color="var(--tg-blue)"
            spark={[180, 210, 240, 260, 295, 310, 340, 360, 380, 395, 410, 420]} />
          
          <KpiCard
            label="Response rate" value="24.6%" delta="+2.1pp" up
            sub="517 of 2,106 replied"
            icon="message_circle" color="var(--ai-purple)"
            spark={[18, 19, 21, 22, 23, 22, 24, 23, 24, 25, 24, 25]} />
          
          <KpiCard
            label="Leads" value={86} delta="+22%" up
            sub="From 9 active campaigns"
            icon="flag" color="var(--success)"
            spark={[3, 5, 7, 8, 12, 14, 18, 20, 24, 29, 34, 38]} />
          
          <KpiCard
            label="Handoffs to manager" value={28} delta="−6%" down
            sub="Avg time to handle 14m"
            icon="user" color="var(--warning)"
            spark={[2, 3, 4, 3, 5, 4, 5, 4, 4, 3, 2, 2]} />
          
        </div>

        {/* Live pulse + Account health */}
        <div style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr", gap: 14, marginBottom: 14 }}>
          <LivePulseCard onOpenCampaign={onOpenCampaign} />
          <AccountHealthCard />
        </div>

        {/* Bottom row */}
        <div style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr", gap: 14 }}>
          <CampaignPerformanceCard onOpenCampaign={onOpenCampaign} />
          <ActivityFeedCard />
        </div>
      </div>
    </>);

}

function KpiCard({ label, value, delta, up, sub, icon, color, spark }) {
  return (
    <div className="metric">
      <div className="metric__head">
        <div style={{
          width: 24, height: 24, borderRadius: 7, background: `${color}1A`,
          display: "flex", alignItems: "center", justifyContent: "center", color
        }}>
          <Icon name={icon} size={14} />
        </div>
        {label}
      </div>
      <div className="metric__row">
        <div className="metric__value num">{typeof value === "number" ? value.toLocaleString() : value}</div>
        <span className={`metric__delta ${up ? "up" : "down"}`}>
          {delta} {up ? "↑" : "↓"}
        </span>
      </div>
      <div className="metric__row" style={{ justifyContent: "space-between" }}>
        <div className="metric__sub">{sub}</div>
        <Sparkline data={spark} width={70} height={24} color={color} />
      </div>
    </div>);

}

// ============================================================
// Live pulse — the AI-first fishka
// ============================================================
function LivePulseCard({ onOpenCampaign }) {
  // Active senders -> active leads
  const senders = [
  { name: "Anna", at: [0.12, 0.82], color: "#3390ec", sent: 18 },
  { name: "Marco", at: [0.18, 0.18], color: "#3390ec", sent: 14 },
  { name: "Sasha", at: [0.08, 0.5], color: "#8774e1", sent: 19 },
  { name: "Elena", at: [0.22, 0.62], color: "#8774e1", sent: 16 },
  { name: "Priya", at: [0.15, 0.34], color: "#4dcd5e", sent: 12 }];

  const leads = [
  { name: "Sophie T.", at: [0.85, 0.16], status: "lead", color: "#3390ec" },
  { name: "Liam K.", at: [0.92, 0.30], status: "active", color: "#3390ec" },
  { name: "Olivia R.", at: [0.78, 0.46], status: "active", color: "#8774e1" },
  { name: "Noah J.", at: [0.88, 0.6], status: "handoff", color: "#4dcd5e" },
  { name: "Sara O.", at: [0.82, 0.76], status: "active", color: "#3390ec" },
  { name: "Maya I.", at: [0.95, 0.88], status: "lead", color: "#4dcd5e" }];

  // Wire pairs
  const wires = [
  [0, 0], [0, 4], [1, 1], [2, 2], [2, 3], [3, 3], [4, 5], [4, 3], [1, 0]];


  return (
    <div className="card" style={{ overflow: "hidden", position: "relative" }}>
      <div className="card__header">
        <div className="row">
          <span className="live-dot" />
          <div>
            <div className="card__title">Live conversations</div>
            <div className="card__sub">9 active threads · 5 senders engaged right now</div>
          </div>
        </div>
        <div className="spacer" />
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <Legend swatch="#3390ec" label="SaaS Q2" />
          <Legend swatch="#8774e1" label="Crypto whales" />
          <Legend swatch="#4dcd5e" label="YT sponsorship" />
        </div>
      </div>
      <div style={{ position: "relative", height: 280, background: "linear-gradient(180deg, #fafbfd 0%, #f4f6f9 100%)" }}>
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
          {/* Wires */}
          {wires.map(([si, li], i) => {
            const s = senders[si];
            const l = leads[li];
            return <LiveWire key={i} from={s.at} to={l.at} color={s.color} duration={1800 + i * 200} delay={i * 300} />;
          })}
        </svg>

        {/* Left column label */}
        <div style={{ position: "absolute", left: 18, top: 14, fontSize: 11, color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
          Senders
        </div>
        <div style={{ position: "absolute", right: 18, top: 14, fontSize: 11, color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
          Leads
        </div>

        {/* Sender nodes */}
        {senders.map((s, i) =>
        <Node key={i} pos={s.at}>
            <Avatar name={s.name} size="sm" />
            <NodeLabel>
              <div style={{ fontSize: 12, fontWeight: 500 }}>{s.name}</div>
              <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{s.sent}/20 today</div>
            </NodeLabel>
          </Node>
        )}
        {/* Lead nodes */}
        {leads.map((l, i) =>
        <Node key={i} pos={l.at} alignRight>
            <NodeLabel align="right">
              <div style={{ fontSize: 12, fontWeight: 500 }}>{l.name}</div>
              <div style={{ fontSize: 10 }}>
                <StatusPillMini status={l.status} />
              </div>
            </NodeLabel>
            <Avatar name={l.name} size="sm" />
          </Node>
        )}
      </div>
    </div>);

}

function Node({ pos, alignRight, children }) {
  return (
    <div style={{
      position: "absolute",
      left: `${pos[0] * 100}%`, top: `${pos[1] * 100}%`,
      transform: "translate(-50%, -50%)",
      display: "flex", alignItems: "center", gap: 8,
      flexDirection: alignRight ? "row-reverse" : "row"
    }}>
      {children}
    </div>);

}
function NodeLabel({ children, align }) {
  return (
    <div style={{ background: "white", borderRadius: 8, padding: "5px 9px",
      boxShadow: "0 4px 10px rgba(15,20,25,0.08), 0 0 0 1px rgba(15,20,25,0.05)",
      textAlign: align === "right" ? "right" : "left", minWidth: 80,
      whiteSpace: "nowrap" }}>
      {children}
    </div>);

}
function StatusPillMini({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.active;
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: s.dot, fontWeight: 600 }}>
    <span style={{ width: 5, height: 5, borderRadius: 50, background: s.dot }} /> {s.label}
  </span>;
}
function Legend({ swatch, label }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--text-muted)" }}>
      <span style={{ width: 8, height: 8, borderRadius: 2, background: swatch }} /> {label}
    </span>);

}

// ============================================================
// Account health card
// ============================================================
function AccountHealthCard() {
  const total = ACCOUNTS.length;
  const active = ACCOUNTS.filter((a) => a.status === "active").length;
  const warmup = ACCOUNTS.filter((a) => a.status === "warmup").length;
  const paused = ACCOUNTS.filter((a) => a.status === "paused").length;
  const err = ACCOUNTS.filter((a) => a.status === "error").length;
  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Account health</div>
          <div className="card__sub">{total} Telegram accounts connected</div>
        </div>
        <div className="spacer" />
        <button className="btn btn--sm btn--ghost"><Icon name="refresh" size={12} /></button>
      </div>
      <div style={{ padding: "18px 18px 14px", display: "flex", alignItems: "center", gap: 18 }}>
        <Donut value={(active + warmup * 0.5) / total} size={86} stroke={9} color="var(--success)" label={`${Math.round(active / total * 100)}%`} />
        <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <HealthRow label="Active" count={active} color="var(--success)" />
          <HealthRow label="Warm-up" count={warmup} color="var(--warning)" />
          <HealthRow label="Paused" count={paused} color="var(--text-faint)" />
          <HealthRow label="Error" count={err} color="var(--danger)" />
        </div>
      </div>
      <div style={{ padding: "0 18px 18px" }}>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10, fontWeight: 500 }}>Today's volume vs daily ceiling</div>
        {ACCOUNTS.slice(0, 4).map((a) =>
        <div key={a.id} style={{ display: "grid", gridTemplateColumns: "100px 1fr 60px", alignItems: "center", gap: 10, padding: "7px 0" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
              <Avatar name={a.name} size="sm" />
              <span style={{ fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.username}</span>
            </div>
            <CorridorBar value={a.sentToday} limit={a.limitDaily} />
            <span style={{ fontSize: 11.5, color: "var(--text-muted)", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
              {a.sentToday}/{a.limitDaily}
            </span>
          </div>
        )}
      </div>
      {err > 0 &&
      <div style={{ borderTop: "1px solid var(--divider)", padding: "10px 18px", display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--danger)" }}>
          <Icon name="alert_triangle" size={14} />
          <span><b>@hirot</b> session revoked — needs re-auth</span>
          <div className="spacer" />
          <button className="btn btn--sm" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>Re-auth</button>
        </div>
      }
    </div>);

}
function HealthRow({ label, count, color }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 8, height: 8, borderRadius: 50, background: color }} />
      <span style={{ fontSize: 12, color: "var(--text-soft)" }}>{label}</span>
      <span style={{ marginLeft: "auto", fontWeight: 600, fontSize: 13, fontVariantNumeric: "tabular-nums" }}>{count}</span>
    </div>);

}

// ============================================================
// Campaign performance
// ============================================================
function CampaignPerformanceCard({ onOpenCampaign }) {
  const rows = CAMPAIGNS.filter((c) => c.status === "running" || c.status === "finished").slice(0, 5);
  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Campaign performance</div>
          <div className="card__sub">Funnel · last 7 days</div>
        </div>
        <div className="spacer" />
        <button className="btn btn--sm btn--ghost">See all <Icon name="arrow_right" size={12} /></button>
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Sent</th>
            <th>Reply</th>
            <th>Leads</th>
            <th>Trend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) =>
          <tr key={r.id} onClick={() => onOpenCampaign(r.id)} style={{ cursor: "pointer" }}>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <StatusPill status={r.status} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 220 }}>{r.name}</div>
                    <div className="muted text-xs">{r.agent}</div>
                  </div>
                </div>
              </td>
              <td className="num">{r.sent.toLocaleString()}</td>
              <td>
                <span className="num">{r.replied}</span>
                <span className="muted text-xs"> · {r.responseRate}%</span>
              </td>
              <td className="num"><b>{r.leads}</b></td>
              <td><Sparkline data={r.sparkline} width={80} height={22} color="var(--tg-blue)" /></td>
            </tr>
          )}
        </tbody>
      </table>
    </div>);

}

// ============================================================
// Activity feed
// ============================================================
const ACTIVITY = [
{ who: "Maya", what: "booked a meeting with", whom: "Sophie Turner · UpperCode", at: "2m ago", icon: "flag", color: "var(--success)", tag: "lead" },
{ who: "Theo", what: "handed off to manager:", whom: "Noah Jansen · Bitline", at: "18m ago", icon: "user", color: "var(--ai-purple)", tag: "handoff" },
{ who: "System", what: "@hirot session revoked", whom: null, at: "1h ago", icon: "alert_triangle", color: "var(--danger)", tag: "error" },
{ who: "Cleo", what: "marked finished:", whom: "Maya Iwata · Drifthouse", at: "3h ago", icon: "check", color: "var(--text-muted)", tag: "finished" },
{ who: "System", what: "added 248 contacts to", whom: "SaaS founders · US", at: "5h ago", icon: "upload", color: "var(--tg-blue)", tag: "import" },
{ who: "Andrew", what: "launched campaign", whom: "Crypto YouTubers — sponsorship", at: "Yesterday", icon: "rocket", color: "var(--tg-blue)", tag: "launch" }];


function ActivityFeedCard() {
  return (
    <div className="card">
      <div className="card__header">
        <div>
          <div className="card__title">Activity</div>
          <div className="card__sub">Live signals & events</div>
        </div>
      </div>
      <div style={{ padding: "8px 4px" }}>
        {ACTIVITY.map((a, i) =>
        <div key={i} style={{ display: "flex", gap: 12, padding: "10px 18px", alignItems: "flex-start" }}>
            <div style={{
            width: 26, height: 26, borderRadius: 8, background: `${a.color}15`,
            color: a.color, display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0
          }}>
              <Icon name={a.icon} size={13} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13 }}>
                <b>{a.who}</b> <span className="muted">{a.what}</span> {a.whom && <span style={{ fontWeight: 500 }}>{a.whom}</span>}
              </div>
              <div className="muted text-xs" style={{ marginTop: 2 }}>{a.at}</div>
            </div>
          </div>
        )}
      </div>
    </div>);

}

Object.assign(window, { Dashboard });