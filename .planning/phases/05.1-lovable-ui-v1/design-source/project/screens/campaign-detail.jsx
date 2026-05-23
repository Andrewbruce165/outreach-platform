// campaign-detail.jsx — single campaign view with analytics + conversations + LLM log

function CampaignDetail({ campaignId, onBack, onOpenConvo }) {
  const c = CAMPAIGNS.find(c => c.id === campaignId) || CAMPAIGNS[0];
  const [tab, setTab] = React.useState("overview");

  return (
    <>
      <div className="tb">
        <div className="row">
          <button className="tb__icon-btn" onClick={onBack}><Icon name="chevron_left" size={18}/></button>
          <div>
            <div className="tb__crumb">Campaigns <span>/</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div className="tb__title">{c.name}</div>
              <StatusPill status={c.status}/>
              {c.status === "running" && <span className="live-dot"/>}
            </div>
          </div>
        </div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm"><Icon name="copy" size={14}/> Duplicate</button>
          <button className="btn btn--ghost btn--sm"><Icon name="edit" size={14}/> Edit</button>
          {c.status === "running"
            ? <button className="btn btn--ghost btn--sm"><Icon name="pause" size={14}/> Pause</button>
            : <button className="btn btn--primary btn--sm"><Icon name="play" size={14}/> Resume</button>}
        </div>
      </div>

      <div className="tabs">
        {[
          ["overview", "Overview"],
          ["conversations", "Conversations", c.replied || 0],
          ["senders", "Senders", c.senders || 0],
          ["llm", "LLM trace"],
          ["settings", "Settings"],
        ].map(([id, label, count]) => (
          <button key={id} className={`tab ${tab === id ? "is-active" : ""}`} onClick={() => setTab(id)}>
            {label} {count !== undefined && <span className="count">{count}</span>}
          </button>
        ))}
      </div>

      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        {tab === "overview" && <CDOverview c={c} onOpenConvo={onOpenConvo}/>}
        {tab === "conversations" && <CDConversations c={c} onOpenConvo={onOpenConvo}/>}
        {tab === "senders" && <CDSenders c={c}/>}
        {tab === "llm" && <CDLlmTrace c={c}/>}
        {tab === "settings" && <CDSettings c={c}/>}
      </div>
    </>
  );
}

// ============================================================
// Overview tab
// ============================================================
function CDOverview({ c, onOpenConvo }) {
  return (
    <>
      {/* Metrics row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, marginBottom: 14 }}>
        <MiniMetric label="Sent" value={c.sent} sub="+128 today" color="var(--tg-blue)"/>
        <MiniMetric label="Delivered" value={Math.round(c.sent * 0.98)} sub="98.4%" color="var(--tg-blue)"/>
        <MiniMetric label="Replied" value={c.replied} sub={`${c.responseRate}%`} color="var(--ai-purple)"/>
        <MiniMetric label="Engaged" value={Math.round(c.replied * 0.6)} sub="2+ messages" color="var(--ai-purple)"/>
        <MiniMetric label="Leads" value={c.leads} sub={`${((c.leads / Math.max(c.sent, 1)) * 100).toFixed(1)}% CR`} color="var(--success)"/>
        <MiniMetric label="Handoffs" value={c.handoffs} sub="To manager" color="var(--warning)"/>
      </div>

      {/* Big chart + funnel */}
      <div style={{ display: "grid", gridTemplateColumns: "1.7fr 1fr", gap: 14, marginBottom: 14 }}>
        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Daily funnel</div>
              <div className="card__sub">Sent → Replied → Lead, last 14 days</div>
            </div>
            <div className="spacer"/>
            <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
              <Legend swatch="#3390ec" label="Sent"/>
              <Legend swatch="#8774e1" label="Replied"/>
              <Legend swatch="#4dcd5e" label="Leads"/>
            </div>
          </div>
          <div style={{ padding: "16px 18px 20px" }}>
            <StackedAreaChart
              width={600} height={200}
              series={[
                { name: "Sent", color: "#3390ec", data: c.sparkline.map(v => Math.max(v * 4, 0)) },
                { name: "Replied", color: "#8774e1", data: c.sparkline.map(v => Math.max(Math.round(v * 0.9), 0)) },
                { name: "Leads", color: "#4dcd5e", data: c.sparkline.map(v => Math.round(v * 0.15)) },
              ]}
              labels={["2w ago", "", "", "", "", "", "1w ago", "", "", "", "", "", "", "Today"]}
            />
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Funnel</div>
              <div className="card__sub">Conversion at each step</div>
            </div>
          </div>
          <div style={{ padding: "16px 18px" }}>
            <FunnelBars
              steps={[
                { label: "Contacts in folder", value: c.contacts, pct: 1 },
                { label: "Found in Telegram", value: Math.round(c.contacts * 0.78), pct: 0.78 },
                { label: "Messages sent", value: c.sent, pct: c.sent / c.contacts },
                { label: "Replied", value: c.replied, pct: c.replied / c.contacts },
                { label: "Qualified leads", value: c.leads, pct: c.leads / c.contacts },
                { label: "Handed off", value: c.handoffs, pct: c.handoffs / c.contacts },
              ]}
            />
          </div>
        </div>
      </div>

      {/* Live convos + signals */}
      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 14 }}>
        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Active conversations</div>
              <div className="card__sub">{CONVOS.slice(0, 5).length} live · {CONVOS.filter(v => v.status === "lead").length} leads pending</div>
            </div>
            <div className="spacer"/>
            <button className="btn btn--sm btn--ghost">See all <Icon name="arrow_right" size={12}/></button>
          </div>
          <div>
            {CONVOS.slice(0, 5).map(v => (
              <div key={v.id} onClick={() => onOpenConvo(v.id)} style={{
                padding: "12px 18px", display: "flex", alignItems: "center", gap: 12,
                borderTop: "1px solid var(--divider)", cursor: "pointer",
              }}>
                <Avatar name={v.contact}/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 500, whiteSpace: "nowrap" }}>{v.contact}</span>
                    <span className="muted text-xs" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{v.company}</span>
                    {v.unread > 0 && <span style={{ background: "var(--tg-blue)", color: "white", fontSize: 10, padding: "1px 5px", borderRadius: 999, fontWeight: 600, flexShrink: 0 }}>{v.unread}</span>}
                  </div>
                  <div className="muted text-xs" style={{ marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {v.snippet}
                  </div>
                </div>
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <StatusPill status={v.status}/>
                  <div className="muted text-xs" style={{ marginTop: 4 }}>{v.lastAt}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Sender pacing</div>
              <div className="card__sub">Today's volume vs ceiling</div>
            </div>
          </div>
          <div style={{ padding: "12px 18px 18px" }}>
            {ACCOUNTS.filter(a => a.campaign === c.name).map(a => (
              <div key={a.id} style={{ display: "grid", gridTemplateColumns: "1fr 80px 60px", gap: 10, alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--divider)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                  <Avatar name={a.name} size="sm"/>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 500 }}>{a.username}</div>
                    <div className="muted text-xs">{a.proxy}</div>
                  </div>
                </div>
                <CorridorBar value={a.sentToday} limit={a.limitDaily}/>
                <span className="num text-xs muted" style={{ textAlign: "right" }}>{a.sentToday}/{a.limitDaily}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function MiniMetric({ label, value, sub, color }) {
  return (
    <div className="metric" style={{ padding: "12px 14px", gap: 4 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em", color }}>{value.toLocaleString()}</div>
      <div className="muted text-xs">{sub}</div>
    </div>
  );
}

function FunnelBars({ steps }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {steps.map((s, i) => {
        const pct = Math.max(0.04, s.pct);
        return (
          <div key={s.label}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5, gap: 8 }}>
              <span style={{ fontSize: 12.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.label}</span>
              <span className="num text-xs muted" style={{ flexShrink: 0 }}>{s.value.toLocaleString()}</span>
            </div>
            <div style={{
              height: 24, width: `${pct * 100}%`,
              background: `linear-gradient(90deg, var(--tg-blue) 0%, var(--ai-purple) 100%)`,
              opacity: 1 - i * 0.13,
              borderRadius: 6,
              display: "flex", alignItems: "center", paddingLeft: 8,
              color: "white", fontSize: 11, fontWeight: 600,
              transition: "width 0.4s",
            }}>{Math.round(pct * 100)}%</div>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// Conversations tab — same as Inbox but filtered
// ============================================================
function CDConversations({ c, onOpenConvo }) {
  return <InboxScreen embedded campaignFilter={c.name} onOpenConvo={onOpenConvo}/>;
}

// ============================================================
// Senders tab
// ============================================================
function CDSenders({ c }) {
  const accs = ACCOUNTS.filter(a => a.campaign === c.name);
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <table className="tbl">
        <thead>
          <tr>
            <th>Sender</th>
            <th>Health</th>
            <th>Today</th>
            <th>This week</th>
            <th>Replies sent</th>
            <th>Leads</th>
            <th>Trend (14d)</th>
          </tr>
        </thead>
        <tbody>
          {accs.map(a => (
            <tr key={a.id}>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <Avatar name={a.name}/>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500 }}>{a.name} <span className="muted">{a.username}</span></div>
                    <div className="muted text-xs">{a.proxy} · age {a.ageDays}d</div>
                  </div>
                </div>
              </td>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Donut value={a.health / 100} size={32} stroke={4} color={a.health > 85 ? "var(--success)" : a.health > 60 ? "var(--warning)" : "var(--danger)"}/>
                  <span className="num fw5">{a.health}</span>
                </div>
              </td>
              <td>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 110 }}>
                  <span className="num text-xs">{a.sentToday} / {a.limitDaily}</span>
                  <CorridorBar value={a.sentToday} limit={a.limitDaily}/>
                </div>
              </td>
              <td>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 110 }}>
                  <span className="num text-xs">{a.sentWeek} / {a.limitWeek}</span>
                  <CorridorBar value={a.sentWeek} limit={a.limitWeek}/>
                </div>
              </td>
              <td className="num">{Math.round(a.sentWeek * 0.62)}</td>
              <td className="num"><b>{Math.round(a.sentWeek * 0.07)}</b></td>
              <td><Sparkline data={a.sparkline} width={100} height={28} color="var(--tg-blue)"/></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// LLM trace tab — searchable history of all LLM calls
// ============================================================
function CDLlmTrace({ c }) {
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 14 }}>
        <MiniMetric label="Total calls" value={3284} sub="+412 today" color="var(--ai-purple)"/>
        <MiniMetric label="Avg latency" value={"1.6s"} sub="p95 3.2s" color="var(--tg-blue)"/>
        <MiniMetric label="Tokens (in / out)" value={"4.2M"} sub="≈ 280K out" color="var(--ai-purple)"/>
        <MiniMetric label="Spend (7d)" value={"$48.20"} sub="≈ $0.014 / convo" color="var(--success)"/>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <div className="card__header">
          <div className="card__title">Recent LLM calls</div>
          <div className="card__sub">Last 100 calls · click to inspect prompt</div>
          <div className="spacer"/>
          <button className="btn btn--ghost btn--sm"><Icon name="filter" size={12}/> Filter</button>
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 130 }}>When</th>
              <th>Contact</th>
              <th>Intent</th>
              <th>Tools</th>
              <th>Tokens</th>
              <th style={{ width: 70 }}>Latency</th>
              <th style={{ width: 80 }}>Signals</th>
            </tr>
          </thead>
          <tbody>
            {LLM_TRACE.concat(LLM_TRACE).slice(0, 6).map((t, i) => (
              <tr key={i}>
                <td className="muted text-xs">{t.at}</td>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Avatar name={"Sophie Turner"} size="sm"/>
                    <span style={{ fontSize: 12.5 }}>Sophie Turner</span>
                  </div>
                </td>
                <td style={{ fontSize: 12.5 }}>{t.intent}</td>
                <td>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {t.tools.map((tl, j) => (
                      <span key={j} className="pill" style={{ height: 20, padding: "0 7px", fontSize: 10.5 }}>
                        <Icon name="tool" size={9}/> {tl.name}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="num text-xs muted">{t.in_tokens} / {t.out_tokens}</td>
                <td className="num text-xs">{t.latency}</td>
                <td>
                  {t.signals.length > 0 ? (
                    <span className="pill pill--green" style={{ height: 20, fontSize: 10 }}>
                      <Icon name="zap" size={9}/> {t.signals[0].split(":")[0]}
                    </span>
                  ) : <span className="muted text-xs">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ============================================================
// Settings tab — campaign-level configuration
// ============================================================
function CDSettings({ c }) {
  const [primaryGoal, setPrimaryGoal] = React.useState("meeting");
  const [audienceHints, setAudienceHints] = React.useState("Cold US AI SaaS founders, seed → Series A.");
  const [successCriteria, setSuccessCriteria] = React.useState("Demo booked with calendar confirmation, OR clear timeline + budget shared.");
  const [webhookUrl, setWebhookUrl] = React.useState("https://hooks.acme.co/aimly/signals");
  const [tools, setTools] = React.useState(["schedule.find_slots", "kb.search", "calendar.book"]);

  const GOALS = [
    { id: "meeting", label: "Book a meeting",   icon: "calendar" },
    { id: "qualify", label: "Qualify the lead", icon: "flag" },
    { id: "click",   label: "Get a click",       icon: "link" },
    { id: "engage",  label: "Engage",            icon: "smile" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, alignItems: "flex-start" }}>
      {/* LEFT: campaign-level overrides */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Customize for this campaign</div>
              <div className="card__sub">Overrides agent's defaults for this run only</div>
            </div>
          </div>
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="field">
              <div className="field__label">Audience hints</div>
              <textarea className="textarea" rows={3} value={audienceHints} onChange={e => setAudienceHints(e.target.value)}/>
            </div>
            <div className="field">
              <div className="field__label">Primary goal</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {GOALS.map(g => {
                  const on = primaryGoal === g.id;
                  return (
                    <button key={g.id} onClick={() => setPrimaryGoal(g.id)} style={{
                      padding: "10px 12px", borderRadius: 9, textAlign: "left", display: "flex", gap: 10, alignItems: "center",
                      border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
                      background: on ? "var(--tg-blue-softer)" : "white",
                    }}>
                      <div style={{ width: 24, height: 24, borderRadius: 7, background: on ? "var(--tg-blue)" : "var(--bg-soft)", color: on ? "white" : "var(--text-muted)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                        <Icon name={g.icon} size={12}/>
                      </div>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{g.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="field">
              <div className="field__label">Success criteria</div>
              <textarea className="textarea" rows={2} value={successCriteria} onChange={e => setSuccessCriteria(e.target.value)}/>
              <div className="field__hint">Free-text rule for emitting the <b>lead</b> signal.</div>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Schedule</div>
              <div className="card__sub">Working hours & rate corridor</div>
            </div>
          </div>
          <div style={{ padding: "6px 18px 16px" }}>
            <SettingRow label="Working hours" value={c.hours}/>
            <SettingRow label="Working days" value="Mon–Fri"/>
            <SettingRow label="Start / End" value={c.startedAt}/>
            <SettingRow label="Rate corridor" value="4 / 20 / 150 (hour / day / week)"/>
          </div>
        </div>
      </div>

      {/* RIGHT: integrations */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Built-in signals</div>
              <div className="card__sub">Always on · built into every agent</div>
            </div>
          </div>
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
            <CDBuiltinSignal icon="flag" color="var(--success)"
              title="Mark as lead"
              desc="Fires when the user expresses interest or matches your success criteria."
              count={c.leads}/>
            <CDBuiltinSignal icon="user" color="var(--ai-purple)"
              title="Transfer to manager"
              desc="Fires when the user asks for a human or pushes back on AI."
              count={c.handoffs}/>
            <CDBuiltinSignal icon="check" color="var(--text-muted)"
              title="Finish conversation"
              desc="Fires when the conversation reaches a natural end."
              count={Math.round((c.replied || 0) * 0.4)}/>
          </div>
        </div>

        <div className="card">
          <div className="card__header">
            <div>
              <div className="card__title">Webhook & tools</div>
              <div className="card__sub">Where signals push and which tools the agent may call</div>
            </div>
          </div>
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="field">
              <div className="field__label">Push signal events to</div>
              <input className="input" value={webhookUrl} onChange={e => setWebhookUrl(e.target.value)} placeholder="https://your-app.com/webhook"/>
              <div className="field__hint" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span>Last fired 12m ago · 217 events in 7d</span>
                <button className="btn btn--ghost btn--sm" style={{ height: 22, padding: "0 8px", fontSize: 11 }}>
                  <Icon name="flask" size={10}/> Send test
                </button>
              </div>
            </div>
            <div className="field">
              <div className="field__label">Custom tools</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {tools.map(t => (
                  <span key={t} className="pill pill--blue">
                    <Icon name="tool" size={10}/> <span className="mono">{t}</span>
                    <button onClick={() => setTools(ts => ts.filter(x => x !== t))} style={{ marginLeft: 4, color: "var(--tg-blue)", opacity: 0.6 }}>
                      <Icon name="x" size={9}/>
                    </button>
                  </span>
                ))}
                <button className="pill" style={{ background: "transparent", border: "1px dashed var(--border-strong)", color: "var(--text-muted)", cursor: "pointer" }}>
                  <Icon name="plus" size={10}/> Add tool
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="card" style={{ position: "relative", opacity: 0.78 }}>
          <div className="card__header">
            <div>
              <div className="card__title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                Custom signals
                <span className="pill pill--purple" style={{ height: 18, fontSize: 10 }}>
                  <Icon name="sparkles" size={9}/> In development
                </span>
              </div>
              <div className="card__sub">Define your own signals beyond the built-in three</div>
            </div>
          </div>
          <div style={{ padding: 18 }}>
            <div className="muted text-sm" style={{ lineHeight: 1.5, marginBottom: 10 }}>
              Custom triggers, dedicated webhooks per signal, and per-signal rate limits — coming soon.
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <span className="pill" style={{ background: "transparent", border: "1px dashed var(--border-strong)" }}>competitor_mentioned</span>
              <span className="pill" style={{ background: "transparent", border: "1px dashed var(--border-strong)" }}>enterprise_intent</span>
              <span className="pill" style={{ background: "transparent", border: "1px dashed var(--border-strong)" }}>budget_revealed</span>
            </div>
          </div>
          <div style={{ position: "absolute", inset: 0, cursor: "not-allowed" }}/>
        </div>
      </div>
    </div>
  );
}

function CDBuiltinSignal({ icon, color, title, desc, count }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
      <div style={{ width: 28, height: 28, borderRadius: 8, background: `${color}1A`, color, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 2 }}>
        <Icon name={icon} size={14}/>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="check" size={12} color="var(--success)"/>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{title}</span>
          <span className="spacer"/>
          <span className="text-xs muted">fired <b style={{ color: "var(--text)" }}>{count}×</b> / 7d</span>
        </div>
        <div className="muted text-xs" style={{ marginTop: 4, lineHeight: 1.5 }}>{desc}</div>
      </div>
    </div>
  );
}

function SettingRow({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "12px 0", borderBottom: "1px solid var(--divider)" }}>
      <span className="muted text-sm">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  );
}

Object.assign(window, { CampaignDetail });
