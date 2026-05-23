// campaign-builder.jsx — multi-step AI-feel campaign creation

const BUILDER_STEPS = [
  { id: "brief", label: "Brief", icon: "sparkles" },
  { id: "agent", label: "Agent", icon: "agents" },
  { id: "accounts", label: "Senders", icon: "send" },
  { id: "audience", label: "Audience", icon: "contacts" },
  { id: "schedule", label: "Schedule", icon: "calendar" },
  { id: "review", label: "Review", icon: "rocket" },
];

function CampaignBuilder({ onExit, onLaunched }) {
  const [step, setStep] = React.useState(0);
  const [brief, setBrief] = React.useState("Personalized outreach to early-stage AI SaaS founders in the US. We're pitching aimly — an AI SDR layer for Telegram. Book a 20-min demo with anyone interested.");
  const [name, setName] = React.useState("AI SaaS founders · US — May");
  const [agentId, setAgentId] = React.useState("a1");
  const [accountIds, setAccountIds] = React.useState(["ac1", "ac2"]);
  const [folderId, setFolderId] = React.useState("f1");
  const [hours, setHours] = React.useState({ from: "09:00", to: "19:00", tz: "GMT-5" });
  const [days, setDays] = React.useState(["mon", "tue", "wed", "thu", "fri"]);
  const [launching, setLaunching] = React.useState(false);

  const cur = BUILDER_STEPS[step];

  return (
    <>
      <div className="tb">
        <div className="row">
          <button className="tb__icon-btn" onClick={onExit}><Icon name="chevron_left" size={18}/></button>
          <div className="tb__crumb">Campaigns <span>/</span></div>
          <div className="tb__title">New campaign</div>
        </div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm" onClick={onExit}>Save as draft</button>
          <button className="btn btn--dark" onClick={() => setLaunching(true)}>
            <Icon name="rocket" size={14}/> Launch
          </button>
        </div>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "260px 1fr 360px", minHeight: 0 }}>
        {/* Steps */}
        <div style={{ background: "var(--bg)", borderRight: "1px solid var(--border)", padding: "20px 14px" }}>
          <div style={{ fontSize: 11, textTransform: "uppercase", color: "var(--text-faint)", fontWeight: 600, letterSpacing: "0.06em", marginBottom: 14, paddingLeft: 8 }}>Steps</div>
          <StepList step={step} setStep={setStep}/>

          <div style={{ marginTop: 24, padding: 14, borderRadius: 12, background: "linear-gradient(135deg, #f1eefb 0%, #e8f3fe 100%)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <div style={{ width: 24, height: 24, borderRadius: 7, background: "var(--ai-purple)", color: "white", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <Icon name="sparkles" size={13}/>
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: "nowrap" }}>AI co-pilot</div>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-soft)", lineHeight: 1.5 }}>
              Drop a brief and I'll pre-fill the campaign for you — agent, senders, audience, schedule.
            </div>
            <button className="btn btn--sm" style={{ marginTop: 10, background: "white", color: "var(--ai-purple)", width: "100%", justifyContent: "center" }}>
              Auto-fill from brief
            </button>
          </div>
        </div>

        {/* Center editor */}
        <div className="scroll" style={{ background: "var(--bg-soft)", padding: "28px 36px" }}>
          <div style={{ maxWidth: 640, margin: "0 auto" }}>
            <div style={{ fontSize: 11, textTransform: "uppercase", color: "var(--tg-blue)", fontWeight: 600, letterSpacing: "0.08em", marginBottom: 6 }}>
              Step {step + 1} of {BUILDER_STEPS.length}
            </div>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.01em", marginBottom: 4 }}>
              {STEP_TITLES[cur.id]}
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: 13.5, marginBottom: 24 }}>
              {STEP_SUBS[cur.id]}
            </div>

            <div style={{ background: "white", border: "1px solid var(--border)", borderRadius: 14, padding: 24 }}>
              {cur.id === "brief" && <BriefStep {...{ name, setName, brief, setBrief }}/>}
              {cur.id === "agent" && <AgentStep {...{ agentId, setAgentId }}/>}
              {cur.id === "accounts" && <AccountsStep {...{ accountIds, setAccountIds }}/>}
              {cur.id === "audience" && <AudienceStep {...{ folderId, setFolderId }}/>}
              {cur.id === "schedule" && <ScheduleStep {...{ hours, setHours, days, setDays }}/>}
              {cur.id === "review" && <ReviewStep {...{ name, agentId, accountIds, folderId, hours, days }}/>}
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 18 }}>
              <button className="btn btn--ghost" disabled={step === 0} onClick={() => setStep(s => Math.max(0, s - 1))} style={{ opacity: step === 0 ? 0.5 : 1 }}>
                <Icon name="chevron_left" size={14}/> Back
              </button>
              {step < BUILDER_STEPS.length - 1 ? (
                <button className="btn btn--primary" onClick={() => setStep(s => s + 1)}>
                  Continue <Icon name="arrow_right" size={14}/>
                </button>
              ) : (
                <button className="btn btn--dark" onClick={() => setLaunching(true)}>
                  <Icon name="rocket" size={14}/> Launch campaign
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Preview */}
        <CampaignPreview {...{ name, agentId, accountIds, folderId, hours, days }}/>
      </div>

      {launching && <LaunchOverlay onDone={() => { setLaunching(false); onLaunched(); }}/>}
    </>
  );
}

const STEP_TITLES = {
  brief: "Write a brief",
  agent: "Pick an AI agent",
  accounts: "Choose senders",
  audience: "Pick your audience",
  schedule: "Set the schedule",
  review: "Review & launch",
};
const STEP_SUBS = {
  brief: "Describe the goal in plain English. The AI will use this to suggest the agent, audience, and tone.",
  agent: "Each agent is a templated SDR — context, task, tone, signals. One agent can run many campaigns.",
  accounts: "These Telegram accounts will send. They lock to this campaign while running.",
  audience: "Choose a contact folder. New contacts added to it later will be auto-enrolled.",
  schedule: "Working hours and days. aimly respects rate limits and the green corridor.",
  review: "Final check — everything looks right? Launch and watch the dashboard.",
};

function StepList({ step, setStep }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {BUILDER_STEPS.map((s, i) => {
        const done = i < step, active = i === step;
        return (
          <button key={s.id} onClick={() => setStep(i)} style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 12px", borderRadius: 9,
            background: active ? "var(--tg-blue-soft)" : "transparent",
            color: active ? "var(--tg-blue)" : done ? "var(--text)" : "var(--text-muted)",
            fontWeight: active ? 500 : 400, fontSize: 13.5,
            textAlign: "left", width: "100%",
          }}>
            <div style={{
              width: 24, height: 24, borderRadius: 7,
              background: done ? "var(--success)" : active ? "var(--tg-blue)" : "var(--bg-soft)",
              color: done || active ? "white" : "var(--text-faint)",
              display: "flex", alignItems: "center", justifyContent: "center",
              flexShrink: 0,
            }}>
              {done ? <Icon name="check" size={13}/> : <Icon name={s.icon} size={12}/>}
            </div>
            <span style={{ flex: 1 }}>{s.label}</span>
            {active && <Icon name="chevron_right" size={14}/>}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// Steps
// ============================================================
function BriefStep({ name, setName, brief, setBrief }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="field">
        <div className="field__label">Campaign name</div>
        <input className="input" value={name} onChange={e => setName(e.target.value)}/>
      </div>
      <div className="field">
        <div className="field__label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          Brief
          <span className="pill pill--purple" style={{ height: 18, padding: "0 7px", fontSize: 10 }}>
            <Icon name="sparkles" size={9}/> AI-assisted
          </span>
        </div>
        <textarea className="textarea" rows={5} value={brief} onChange={e => setBrief(e.target.value)}/>
        <div className="field__hint">The AI will use this to pre-fill agent suggestions, tone, and audience hints.</div>
      </div>
      <div className="field">
        <div className="field__label">Goal</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {["Book a meeting", "Qualify lead", "Drive a click", "Re-engage", "Custom"].map(g => (
            <button key={g} className="pill" style={{ height: 30, padding: "0 12px", fontSize: 12, cursor: "pointer",
              background: g === "Book a meeting" ? "var(--tg-blue-soft)" : undefined,
              color: g === "Book a meeting" ? "var(--tg-blue)" : undefined,
            }}>{g}</button>
          ))}
        </div>
      </div>
    </div>
  );
}

function AgentStep({ agentId, setAgentId }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
      {AGENTS.slice(0, 4).map(a => (
        <button key={a.id} onClick={() => setAgentId(a.id)} style={{
          padding: 14, borderRadius: 11, textAlign: "left",
          border: `1.5px solid ${agentId === a.id ? a.accent : "var(--border)"}`,
          background: agentId === a.id ? `${a.accent}0a` : "white",
          display: "flex", flexDirection: "column", gap: 10,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div className="avatar" style={{ background: a.accent }}>{a.avatar}</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>{a.name}</div>
              <div className="muted text-xs">{a.role}</div>
            </div>
            {agentId === a.id && <div style={{ color: a.accent }}><Icon name="check" size={16}/></div>}
          </div>
          <div className="text-sm muted" style={{ lineHeight: 1.45 }}>{a.desc}</div>
          <div style={{ display: "flex", gap: 6 }}>
            <span className="pill"><Icon name="message" size={10}/> {a.conversations.toLocaleString()}</span>
            <span className="pill pill--green"><Icon name="flag" size={10}/> {a.leads} leads</span>
          </div>
        </button>
      ))}
      <button style={{
        gridColumn: "span 2", padding: 16, borderRadius: 11,
        border: "1.5px dashed var(--border-strong)", background: "var(--bg-softer)",
        color: "var(--text-muted)", display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
      }}>
        <Icon name="plus" size={14}/> Create new agent for this campaign
      </button>
    </div>
  );
}

function AccountsStep({ accountIds, setAccountIds }) {
  const toggle = id => setAccountIds(ids => ids.includes(id) ? ids.filter(i => i !== id) : [...ids, id]);
  const eligible = ACCOUNTS.filter(a => a.status === "active" || a.status === "warmup");
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, padding: "10px 12px", borderRadius: 9, background: "var(--bg-soft)", fontSize: 12.5 }}>
        <Icon name="info" size={14} color="var(--tg-blue)"/>
        <span>4 accounts selected can send up to <b className="num">80 messages/day</b> total · <b className="num">560/week</b></span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {eligible.map(a => {
          const on = accountIds.includes(a.id);
          return (
            <div key={a.id} onClick={() => toggle(a.id)} style={{
              padding: "12px 14px", borderRadius: 10, cursor: "pointer",
              border: `1px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
              background: on ? "var(--tg-blue-softer)" : "white",
              display: "flex", alignItems: "center", gap: 12,
            }}>
              <div style={{
                width: 18, height: 18, borderRadius: 5,
                border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border-strong)"}`,
                background: on ? "var(--tg-blue)" : "white",
                color: "white", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
              }}>
                {on && <Icon name="check" size={12}/>}
              </div>
              <Avatar name={a.name}/>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500, fontSize: 13 }}>{a.name} · <span className="muted">{a.username}</span></div>
                <div className="text-xs muted">{a.proxy} · age {a.ageDays}d · warm-up day {a.warmupDay}</div>
              </div>
              <div style={{ width: 90 }}>
                <div className="text-xs muted" style={{ marginBottom: 4 }}>{a.sentToday}/{a.limitDaily} today</div>
                <CorridorBar value={a.sentToday} limit={a.limitDaily}/>
              </div>
              <StatusPill status={a.status}/>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AudienceStep({ folderId, setFolderId }) {
  return (
    <div>
      <div className="field" style={{ marginBottom: 14 }}>
        <div className="field__label">Contact folder</div>
        <div className="field__hint">Pick one folder. While the campaign runs, any new contact added to this folder is auto-enrolled.</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {FOLDERS.slice(0, 6).map(f => {
          const sel = folderId === f.id;
          return (
            <button key={f.id} onClick={() => setFolderId(f.id)} style={{
              padding: 14, borderRadius: 11, textAlign: "left",
              border: `1.5px solid ${sel ? "var(--tg-blue)" : "var(--border)"}`,
              background: sel ? "var(--tg-blue-softer)" : "white",
              display: "flex", flexDirection: "column", gap: 10,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 32, height: 32, borderRadius: 8, background: `${f.color}1A`, color: f.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Icon name="folder" size={16}/>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>{f.name}</div>
                  <div className="muted text-xs">{f.source}</div>
                </div>
                {sel && <Icon name="check" size={16} color="var(--tg-blue)"/>}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div className="num" style={{ fontSize: 18, fontWeight: 600 }}>{f.contacts.toLocaleString()}</div>
                <div className="text-xs muted">{Math.round(f.inTg * 100)}% in Telegram</div>
              </div>
              <div style={{ height: 4, background: "var(--bg-soft)", borderRadius: 999, overflow: "hidden" }}>
                <div style={{ width: `${f.inTg * 100}%`, height: "100%", background: "var(--success)" }}/>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ScheduleStep({ hours, setHours, days, setDays }) {
  const D = [["mon","M"],["tue","T"],["wed","W"],["thu","T"],["fri","F"],["sat","S"],["sun","S"]];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="field">
        <div className="field__label">Working days</div>
        <div style={{ display: "flex", gap: 8 }}>
          {D.map(([id, l]) => {
            const on = days.includes(id);
            return (
              <button key={id} onClick={() => setDays(ds => on ? ds.filter(d => d !== id) : [...ds, id])}
                style={{
                  width: 40, height: 40, borderRadius: 10,
                  background: on ? "var(--tg-blue)" : "var(--bg-soft)",
                  color: on ? "white" : "var(--text-muted)",
                  fontWeight: 600, fontSize: 13,
                }}>{l}</button>
            );
          })}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
        <div className="field">
          <div className="field__label">From</div>
          <input className="input" value={hours.from} onChange={e => setHours({ ...hours, from: e.target.value })}/>
        </div>
        <div className="field">
          <div className="field__label">To</div>
          <input className="input" value={hours.to} onChange={e => setHours({ ...hours, to: e.target.value })}/>
        </div>
        <div className="field">
          <div className="field__label">Timezone</div>
          <input className="input" value={hours.tz} onChange={e => setHours({ ...hours, tz: e.target.value })}/>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div className="field">
          <div className="field__label">Start date</div>
          <input className="input" placeholder="May 12, 2026" defaultValue="May 12, 2026"/>
        </div>
        <div className="field">
          <div className="field__label">End date (optional)</div>
          <input className="input" placeholder="Continuous" defaultValue=""/>
        </div>
      </div>
      <div style={{ padding: 14, background: "var(--bg-soft)", borderRadius: 10, fontSize: 12.5, color: "var(--text-soft)" }}>
        <b>Green corridor:</b> aimly enforces 4 / 20 / 150 messages per account (hour / day / week) plus warm-up. You'll never exceed these.
      </div>
    </div>
  );
}

function ReviewStep({ name, agentId, accountIds, folderId, hours, days }) {
  const agent = AGENTS.find(a => a.id === agentId);
  const folder = FOLDERS.find(f => f.id === folderId);
  const accs = ACCOUNTS.filter(a => accountIds.includes(a.id));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <ReviewRow label="Name" value={name}/>
      <ReviewRow label="Agent" value={`${agent?.name} — ${agent?.role}`}/>
      <ReviewRow label="Senders" value={`${accs.length} accounts (${accs.map(a => a.username).join(", ")})`}/>
      <ReviewRow label="Audience" value={`${folder?.name} · ${folder?.contacts.toLocaleString()} contacts`}/>
      <ReviewRow label="Schedule" value={`${hours.from} – ${hours.to} ${hours.tz} · ${days.length} days/week`}/>
      <div style={{ padding: 14, borderRadius: 10, background: "linear-gradient(135deg, #f1eefb 0%, #e8f3fe 100%)", display: "flex", gap: 12, alignItems: "flex-start" }}>
        <div style={{ width: 24, height: 24, borderRadius: 7, background: "var(--ai-purple)", color: "white", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <Icon name="sparkles" size={13}/>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-soft)", lineHeight: 1.5 }}>
          <b>Forecast:</b> at this pace, you'll reach all <b>{folder?.contacts.toLocaleString()}</b> contacts in <b>~17 working days</b>.
          Expected <b>~{Math.round(folder?.contacts * 0.23)}</b> replies and <b>~{Math.round(folder?.contacts * 0.04)}</b> leads based on Maya's recent runs.
        </div>
      </div>
    </div>
  );
}

function ReviewRow({ label, value }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr auto", gap: 12, alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--divider)" }}>
      <div className="muted text-sm">{label}</div>
      <div style={{ fontSize: 13.5 }}>{value}</div>
      <button className="btn btn--sm btn--ghost"><Icon name="edit" size={12}/></button>
    </div>
  );
}

// ============================================================
// Right-rail preview
// ============================================================
function CampaignPreview({ name, agentId, accountIds, folderId, hours, days }) {
  const agent = AGENTS.find(a => a.id === agentId);
  const folder = FOLDERS.find(f => f.id === folderId);
  const accs = ACCOUNTS.filter(a => accountIds.includes(a.id));
  return (
    <div style={{ background: "var(--bg)", borderLeft: "1px solid var(--border)", padding: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div className="muted text-xs" style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
          Campaign preview
        </div>
        <button className="tb__icon-btn" style={{ width: 26, height: 26 }}><Icon name="eye" size={14}/></button>
      </div>
      <div style={{ padding: 14, borderRadius: 12, background: "var(--bg-soft)", marginBottom: 14 }}>
        <div className="muted text-xs" style={{ marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em" }}>Campaign</div>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{name}</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <Tag>Outreach</Tag>
          <Tag color="var(--ai-purple)">{agent?.role}</Tag>
        </div>
      </div>

      <PreviewSection label="Agent">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div className="avatar" style={{ background: agent?.accent }}>{agent?.avatar}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>{agent?.name}</div>
            <div className="muted text-xs">{agent?.tone}</div>
          </div>
        </div>
      </PreviewSection>

      <PreviewSection label="Senders">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {accs.slice(0, 4).map(a => (
            <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Avatar name={a.name} size="sm"/>
              <span style={{ fontSize: 12.5 }}>{a.username}</span>
              <span className="spacer"/>
              <span className="text-xs muted">{a.limitDaily}/day</span>
            </div>
          ))}
          {!accs.length && <div className="muted text-xs">No senders picked yet</div>}
        </div>
      </PreviewSection>

      <PreviewSection label="Audience">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 28, height: 28, borderRadius: 7, background: `${folder?.color}1A`, color: folder?.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Icon name="folder" size={14}/>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>{folder?.name}</div>
            <div className="muted text-xs">{folder?.contacts.toLocaleString()} contacts · {Math.round(folder?.inTg * 100)}% in TG</div>
          </div>
        </div>
      </PreviewSection>

      <PreviewSection label="Schedule">
        <div style={{ fontSize: 13 }}>{hours.from} – {hours.to} {hours.tz}</div>
        <div className="muted text-xs">{days.length} working days/week</div>
      </PreviewSection>

      <div style={{ marginTop: 18, padding: 12, borderRadius: 10, background: "var(--bg-soft)", display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="info" size={14} color="var(--text-muted)"/>
        <span className="text-xs muted">Estimated start: <b style={{ color: "var(--text)" }}>May 12, 9:00 GMT-5</b></span>
      </div>
    </div>
  );
}

function PreviewSection({ label, children }) {
  return (
    <div style={{ marginBottom: 14, paddingBottom: 14, borderBottom: "1px solid var(--divider)" }}>
      <div className="muted text-xs" style={{ textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }}>{label}</div>
      {children}
    </div>
  );
}

// ============================================================
// Launch overlay — AI-feel progress
// ============================================================
function LaunchOverlay({ onDone }) {
  const STAGES = [
    "Validating audience…",
    "Locking 4 sender accounts…",
    "Compiling agent prompt with variables…",
    "Calibrating green corridor (4 / 20 / 150)…",
    "Scheduling first wave of 248 messages…",
    "Launched.",
  ];
  const [i, setI] = React.useState(0);
  React.useEffect(() => {
    if (i >= STAGES.length - 1) {
      const t = setTimeout(onDone, 1100);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setI(i + 1), 580);
    return () => clearTimeout(t);
  }, [i]);

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(15,20,25,0.55)",
      backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 9999,
    }}>
      <div style={{
        width: 480, background: "white", borderRadius: 18, padding: 28,
        boxShadow: "0 30px 80px rgba(0,0,0,0.3)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: "linear-gradient(135deg, var(--ai-purple), var(--tg-blue))",
            color: "white", display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Icon name="sparkles" size={20}/>
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>Launching campaign</div>
            <div className="muted text-sm">Setting things up — a few seconds.</div>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {STAGES.map((s, idx) => {
            const done = idx < i;
            const active = idx === i;
            return (
              <div key={s} style={{ display: "flex", alignItems: "center", gap: 10, opacity: idx > i ? 0.35 : 1 }}>
                <div style={{
                  width: 20, height: 20, borderRadius: 50,
                  background: done ? "var(--success)" : active ? "var(--tg-blue-soft)" : "var(--bg-soft)",
                  color: done ? "white" : "var(--tg-blue)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  flexShrink: 0,
                }}>
                  {done ? <Icon name="check" size={11}/> : active ? <Spinner size={12}/> : null}
                </div>
                <span style={{ fontSize: 13.5, color: done ? "var(--text-muted)" : "var(--text)", fontWeight: active ? 500 : 400 }}>
                  {s}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Spinner({ size = 14, color = "var(--tg-blue)" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ animation: "spin 0.8s linear infinite" }}>
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="2.5" fill="none" strokeDasharray="40 60" strokeLinecap="round"/>
    </svg>
  );
}
const _spinKf = document.createElement("style");
_spinKf.textContent = "@keyframes spin { to { transform: rotate(360deg); } }";
document.head.appendChild(_spinKf);

Object.assign(window, { CampaignBuilder });
