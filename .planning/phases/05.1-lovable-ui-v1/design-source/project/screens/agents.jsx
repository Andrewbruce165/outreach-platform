// agents.jsx — agent cards + open-agent editor with chat-like config

function AgentsScreen() {
  const [openId, setOpenId] = React.useState(null);
  const open = AGENTS.find(a => a.id === openId);

  if (open) return <AgentEditor agent={open} onBack={() => setOpenId(null)}/>;

  return (
    <>
      <div className="tb">
        <div className="tb__title">Agents</div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm"><Icon name="filter" size={14}/> Filters</button>
          <button className="btn btn--primary"><Icon name="plus" size={14}/> New agent</button>
        </div>
      </div>

      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
          {AGENTS.map(a => (
            <div key={a.id} className="card" style={{ padding: 18, cursor: "pointer" }} onClick={() => setOpenId(a.id)}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                <div className="avatar avatar--lg" style={{ background: a.accent }}>{a.avatar}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 600 }}>{a.name}</div>
                  <div className="muted text-sm">{a.role}</div>
                </div>
                <button className="tb__icon-btn" style={{ width: 28, height: 28 }} onClick={e => e.stopPropagation()}>
                  <Icon name="more" size={14}/>
                </button>
              </div>
              <div className="text-sm muted" style={{ marginBottom: 14, lineHeight: 1.45, minHeight: 38 }}>
                {a.desc}
              </div>
              <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
                <span className="pill">{a.tone}</span>
                <span className="pill">{a.lang}</span>
              </div>
              <div style={{ display: "flex", gap: 18, paddingTop: 12, borderTop: "1px solid var(--divider)" }}>
                <Stat label="Campaigns" value={a.campaigns}/>
                <Stat label="Conversations" value={a.conversations}/>
                <Stat label="Leads" value={a.leads}/>
              </div>
              <div className="muted text-xs" style={{ marginTop: 10 }}>Updated {a.updated}</div>
            </div>
          ))}
          <button className="card" style={{
            padding: 18, display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
            border: "1.5px dashed var(--border-strong)", background: "var(--bg-softer)",
            color: "var(--text-muted)", minHeight: 240,
          }}>
            <Icon name="plus" size={18}/> Create new agent
          </button>
        </div>
      </div>
    </>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="num" style={{ fontSize: 16, fontWeight: 600 }}>{value.toLocaleString()}</div>
      <div className="muted text-xs">{label}</div>
    </div>
  );
}

// ============================================================
// Agent editor — chat-like configuration
// ============================================================
function AgentEditor({ agent, onBack }) {
  const [tab, setTab] = React.useState("context");
  return (
    <>
      <div className="tb">
        <div className="row">
          <button className="tb__icon-btn" onClick={onBack}><Icon name="chevron_left" size={18}/></button>
          <div className="tb__crumb">Agents <span>/</span></div>
          <div className="tb__title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div className="avatar" style={{ background: agent.accent, width: 28, height: 28, fontSize: 12 }}>{agent.avatar}</div>
            {agent.name}
            <span className="muted text-sm" style={{ fontWeight: 400 }}>— {agent.role}</span>
          </div>
        </div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm"><Icon name="flask" size={14}/> Test in sandbox</button>
          <button className="btn btn--ghost btn--sm"><Icon name="copy" size={14}/> Duplicate</button>
          <button className="btn btn--primary btn--sm">Save changes</button>
        </div>
      </div>

      <div className="tabs">
        {[
          ["context", "Context"],
          ["task", "Task & tone"],
          ["signals", "Signals"],
          ["tools", "Tools"],
          ["faq", "FAQ / knowledge"],
          ["sandbox", "Sandbox"],
        ].map(([id, label]) => (
          <button key={id} className={`tab ${tab === id ? "is-active" : ""}`} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        <div style={{ maxWidth: 920, margin: "0 auto", display: "grid", gridTemplateColumns: "1fr 320px", gap: 18 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Panel label="Who is this agent?" hint="Backstory the LLM uses to ground its voice. Stay specific.">
              <textarea className="textarea" rows={3} defaultValue={`You are Maya, a friendly, curious SDR at Acme. You've shipped AI tooling at three startups. You always end with a single, easy ask.`}/>
            </Panel>
            <Panel label="What does it know about Acme?" hint="Product context, pricing, key proof points.">
              <textarea className="textarea" rows={4} defaultValue={`aimly is an AI SDR layer for Telegram. Reply rate 3× email. Per-account safety: 4/20/150 corridor + warmup. Residential proxies. Starts at $890/mo.`}/>
            </Panel>
            <Panel label="Audience hints" hint="Who is on the other side, typically?">
              <textarea className="textarea" rows={2} defaultValue={`AI SaaS founders & heads of growth, mostly US/EU. They've usually tried HubSpot or Apollo and felt the deliverability cliff.`}/>
            </Panel>
            <Panel label="Variables (auto-substituted)" hint="From contact CSV / custom JSON. Russian aliases supported.">
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Tag>{`{{first_name}}`}</Tag>
                <Tag>{`{{company}}`}</Tag>
                <Tag>{`{{role}}`}</Tag>
                <Tag>{`{{source}}`}</Tag>
                <Tag>{`{{custom.industry}}`}</Tag>
                <Tag>{`{{имя}}`}</Tag>
                <button className="pill" style={{ height: 24, padding: "0 10px", border: "1px dashed var(--border-strong)", color: "var(--text-muted)", background: "transparent" }}>
                  <Icon name="plus" size={10}/> Add variable
                </button>
              </div>
            </Panel>
          </div>

          {/* Side preview */}
          <div className="card" style={{ position: "sticky", top: 0, alignSelf: "flex-start", padding: 16 }}>
            <div className="muted text-xs" style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 12 }}>
              Live preview
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
              <div className="avatar" style={{ background: agent.accent }}>{agent.avatar}</div>
              <div>
                <div style={{ fontWeight: 600 }}>{agent.name}</div>
                <div className="muted text-xs">{agent.role}</div>
              </div>
            </div>
            <div style={{ padding: 12, background: "var(--bg-soft)", borderRadius: 10, fontSize: 12.5, lineHeight: 1.5 }}>
              <div className="muted text-xs" style={{ marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em" }}>Opener · sample</div>
              "Hi Sophie — Anna here from Acme. Saw UpperCode shipped that programmatic SEO feature last week, that's a sharp move. Quick question: how are you handling outbound on your side right now?"
            </div>
            <div style={{ padding: 12, background: "var(--tg-blue-softer)", borderRadius: 10, fontSize: 12, lineHeight: 1.5, marginTop: 10, borderLeft: "3px solid var(--tg-blue)" }}>
              <span className="muted text-xs">First reply would consume <b>~1.6K tokens</b>, cost <b>~$0.012</b></span>
            </div>
            <button className="btn btn--soft" style={{ width: "100%", marginTop: 12, justifyContent: "center" }}>
              <Icon name="flask" size={14}/> Open sandbox
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

function Panel({ label, hint, children }) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>
        {hint && <div className="muted text-xs">{hint}</div>}
      </div>
      <div style={{ marginTop: 8 }}>{children}</div>
    </div>
  );
}

Object.assign(window, { AgentsScreen });
