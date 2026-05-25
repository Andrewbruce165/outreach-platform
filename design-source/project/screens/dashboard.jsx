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
// Conversion funnel + live stream — the AI-first fishka
// ============================================================
function LivePulseCard({ onOpenCampaign }) {
  const stages = [
    { id: "sent",    label: "Sent",    value: 2106, color: "#3390ec" },
    { id: "replied", label: "Replied", value: 517,  color: "#5eaef4" },
    { id: "engaged", label: "Engaged", value: 310,  color: "#8774e1" },
    { id: "lead",    label: "Lead",    value: 86,   color: "#4dcd5e" },
    { id: "handoff", label: "Handoff", value: 28,   color: "#16a34a" },
  ];

  return (
    <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div className="card__header">
        <div>
          <div className="card__title">Conversion funnel</div>
          <div className="card__sub">Sent → Handoff · last 7 days</div>
        </div>
        <div className="spacer"/>
        <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 11.5 }}>
          <span className="live-dot"/>
          <span>updating live</span>
        </div>
      </div>
      <div style={{ padding: "22px 26px 24px", flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <SankeyFunnel stages={stages}/>
      </div>
    </div>
  );
}

function SankeyFunnel({ stages }) {
  const W = 720;
  const H = 200;
  const colW = 46;
  const labelGap = 46;
  const gap = (W - colW * stages.length) / (stages.length - 1);
  const max = stages[0].value;
  const hFor = v => Math.max(2, (v / max) * (H - 16));
  const yTop = v => (H - hFor(v)) / 2;
  const yBot = v => yTop(v) + hFor(v);

  const ribbon = (a, b, ax, bx) => {
    const x1 = ax + colW, x2 = bx;
    const cx = x1 + (x2 - x1) * 0.5;
    return [
      `M ${x1} ${yTop(a.value)}`,
      `C ${cx} ${yTop(a.value)}, ${cx} ${yTop(b.value)}, ${x2} ${yTop(b.value)}`,
      `L ${x2} ${yBot(b.value)}`,
      `C ${cx} ${yBot(b.value)}, ${cx} ${yBot(a.value)}, ${x1} ${yBot(a.value)}`,
      "Z",
    ].join(" ");
  };

  const dropoff = (a, b, ax, bx) => {
    const x1 = ax + colW, x2 = bx;
    const cx = x1 + (x2 - x1) * 0.5;
    const topA = yBot(a.value);
    const topB = yBot(b.value);
    const tailY = H + 6;
    return [
      `M ${x1} ${topA}`,
      `C ${cx} ${topA}, ${cx} ${topB}, ${x2} ${topB}`,
      `L ${x2} ${tailY}`,
      `L ${x1} ${tailY}`,
      "Z",
    ].join(" ");
  };

  return (
    <svg viewBox={`0 0 ${W} ${H + labelGap}`} width="100%" style={{ display: "block", overflow: "visible" }}>
      <defs>
        {stages.slice(0, -1).map((s, i) => (
          <linearGradient key={s.id} id={`band-${s.id}`} x1="0" x2="1">
            <stop offset="0%" stopColor={s.color}/>
            <stop offset="100%" stopColor={stages[i + 1].color}/>
          </linearGradient>
        ))}
      </defs>

      {/* Drop-off bleed */}
      {stages.slice(0, -1).map((s, i) => {
        const next = stages[i + 1];
        const ax = i * (colW + gap);
        const bx = (i + 1) * (colW + gap);
        return (
          <path key={`drop-${s.id}`} d={dropoff(s, next, ax, bx)} fill={s.color} opacity="0.08"/>
        );
      })}

      {/* Flow ribbons */}
      {stages.slice(0, -1).map((s, i) => {
        const next = stages[i + 1];
        const ax = i * (colW + gap);
        const bx = (i + 1) * (colW + gap);
        return (
          <path key={`flow-${s.id}`} d={ribbon(s, next, ax, bx)} fill={`url(#band-${s.id})`} opacity="0.4"/>
        );
      })}

      {/* Stage bars */}
      {stages.map((s, i) => {
        const x = i * (colW + gap);
        return (
          <g key={s.id}>
            <rect x={x} y={yTop(s.value)} width={colW} height={hFor(s.value)} rx="4" fill={s.color}/>
            <text x={x + colW / 2} y={H + 20} textAnchor="middle" fontSize="10" fill="var(--text-faint)" fontWeight="600" style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {s.label}
            </text>
            <text x={x + colW / 2} y={H + 40} textAnchor="middle" fontSize="17" fontWeight="600" fill="var(--text)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {s.value.toLocaleString()}
            </text>
          </g>
        );
      })}

      {/* Conversion ratio chips between stages */}
      {stages.slice(0, -1).map((s, i) => {
        const next = stages[i + 1];
        const pct = (next.value / s.value) * 100;
        const label = pct < 10 ? pct.toFixed(1) + "%" : Math.round(pct) + "%";
        const cx = i * (colW + gap) + colW + gap / 2;
        return (
          <g key={`cr-${s.id}`}>
            <rect x={cx - 22} y={yTop(next.value) - 22} width="44" height="18" rx="9" fill="white" stroke="var(--border)"/>
            <text x={cx} y={yTop(next.value) - 9} textAnchor="middle" fontSize="10.5" fill="var(--text-soft)" fontWeight="600" style={{ fontVariantNumeric: "tabular-nums" }}>
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

const LIVE_EVENTS = [
  { who: "Sophie T.", what: "booked a meeting", via: "Maya", t: "12s", color: "var(--success)", icon: "flag" },
  { who: "Liam K.", what: "asked about pricing", via: "Maya", t: "48s", color: "var(--tg-blue)", icon: "message_circle" },
  { who: "Noah J.", what: "handed off to manager", via: "Cleo", t: "2m", color: "var(--ai-purple)", icon: "user" },
  { who: "Olivia R.", what: "replied to follow-up", via: "Theo", t: "3m", color: "var(--tg-blue)", icon: "message_circle" },
  { who: "Maya I.", what: "loop'd in CFO", via: "Cleo", t: "4m", color: "var(--success)", icon: "flag" },
  { who: "@anna_p", what: "sent 18th message today", via: null, t: "5m", color: "var(--text-muted)", icon: "send" },
  { who: "Ava M.", what: "asked for case studies", via: "Theo", t: "7m", color: "var(--tg-blue)", icon: "message_circle" },
  { who: "@hirot", what: "session revoked", via: null, t: "9m", color: "var(--danger)", icon: "alert_triangle" },
];

function LiveActivityRail() {
  return (
    <div style={{ width: 280, flexShrink: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="card__header" style={{ paddingLeft: 16, paddingRight: 14 }}>
        <div>
          <div className="card__title">Live stream</div>
          <div className="card__sub">Signals · last 10 min</div>
        </div>
      </div>
      <div className="scroll" style={{ flex: 1, padding: "4px 6px 8px" }}>
        {LIVE_EVENTS.map((e, i) => (
          <div key={i} style={{ display: "flex", gap: 10, padding: "9px 10px", alignItems: "flex-start", borderRadius: 8 }}>
            <div style={{ width: 22, height: 22, borderRadius: 7, background: `${e.color}15`, color: e.color, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 1 }}>
              <Icon name={e.icon} size={11}/>
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12.5, lineHeight: 1.4 }}>
                <b>{e.who}</b> <span className="muted">{e.what}</span>
              </div>
              <div className="muted text-xs" style={{ marginTop: 2 }}>
                {e.via && <>via {e.via} · </>}{e.t} ago
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
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