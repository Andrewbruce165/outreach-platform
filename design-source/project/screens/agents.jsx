// agents.jsx — agent cards + editor (4 tabs: Context / Voice / Knowledge / Safety)

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
                <VoiceBadge value={a.voiceBaseline}/>
                <button className="tb__icon-btn" style={{ width: 28, height: 28 }} onClick={e => e.stopPropagation()}>
                  <Icon name="more" size={14}/>
                </button>
              </div>
              <div className="text-sm" style={{ color: "var(--text-soft)", marginBottom: 14, lineHeight: 1.45, minHeight: 38,
                display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                {a.who}
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

const VOICE_TINT = {
  Professional: { bg: "#e8f3fe", fg: "#1c66b0", icon: "shield" },
  Friendly:     { bg: "#e8faec", fg: "#1e8a3a", icon: "smile" },
  Playful:      { bg: "#f1eefb", fg: "var(--ai-purple)", icon: "sparkles" },
};
function VoiceBadge({ value }) {
  const t = VOICE_TINT[value] || VOICE_TINT.Friendly;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      height: 22, padding: "0 9px", borderRadius: 999,
      background: t.bg, color: t.fg,
      fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em",
      whiteSpace: "nowrap",
    }}>
      <Icon name={t.icon} size={10}/> {value}
    </span>
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
// Agent editor
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
          <button className="btn btn--ghost btn--sm"><Icon name="copy" size={14}/> Duplicate</button>
          <button className="btn btn--primary btn--sm">Save changes</button>
        </div>
      </div>

      <div className="tabs">
        {[
          ["context", "Context"],
          ["voice", "Voice"],
          ["faq", "FAQ / Knowledge"],
          ["safety", "Safety"],
        ].map(([id, label]) => (
          <button key={id} className={`tab ${tab === id ? "is-active" : ""}`} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      <div className="scroll" style={{ flex: 1, background: "var(--bg-soft)" }}>
        {tab === "context" && <TabContext agent={agent}/>}
        {tab === "voice"   && <TabVoice agent={agent}/>}
        {tab === "faq"     && <TabFaq agent={agent}/>}
        {tab === "safety"  && <TabSafety agent={agent}/>}
      </div>
    </>
  );
}

// ============================================================
// Reusable bits
// ============================================================
function Panel({ label, hint, children }) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4, gap: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>
        {hint && <div className="muted text-xs" style={{ textAlign: "right" }}>{hint}</div>}
      </div>
      <div style={{ marginTop: 8 }}>{children}</div>
    </div>
  );
}

function EditorLayout({ left, right }) {
  return (
    <div style={{ padding: 24 }}>
      <div style={{ maxWidth: 960, margin: "0 auto", display: "grid", gridTemplateColumns: right ? "1fr 340px" : "1fr", gap: 18, alignItems: "flex-start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>{left}</div>
        {right}
      </div>
    </div>
  );
}

function LivePreviewCard({ agent, sample }) {
  return (
    <div className="card" style={{ position: "sticky", top: 0, padding: 16 }}>
      <div className="muted text-xs" style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 12 }}>
        Live preview
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <div className="avatar" style={{ background: agent.accent }}>{agent.avatar}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{agent.name}</div>
          <div className="muted text-xs">{agent.role}</div>
        </div>
        <VoiceBadge value={agent.voiceBaseline}/>
      </div>
      <div style={{ padding: 12, background: "var(--bg-soft)", borderRadius: 10, fontSize: 12.5, lineHeight: 1.5 }}>
        <div className="muted text-xs" style={{ marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em" }}>Opener · sample</div>
        {sample || `"Hi Sophie — Anna here from Acme. Saw UpperCode shipped that programmatic SEO feature last week, that's a sharp move. Quick question: how are you handling outbound on your side right now?"`}
      </div>
      <div style={{ padding: 12, background: "var(--tg-blue-softer)", borderRadius: 10, fontSize: 12, lineHeight: 1.5, marginTop: 10, borderLeft: "3px solid var(--tg-blue)" }}>
        <span className="muted text-xs">First reply would consume <b>~1.6K tokens</b>, cost <b>~$0.012</b></span>
      </div>
      <button className="btn btn--ghost" style={{ width: "100%", marginTop: 12, justifyContent: "center" }}>
        <Icon name="refresh" size={13}/> Regenerate sample
      </button>
    </div>
  );
}

// ============================================================
// Tab: Context (2 textareas)
// ============================================================
function TabContext({ agent }) {
  return (
    <EditorLayout
      right={<LivePreviewCard agent={agent}/>}
      left={<>
        <Panel label="Who is this agent?" hint="Backstory + role. The LLM uses this to ground its voice.">
          <textarea className="textarea" rows={4}
            placeholder="e.g. Friendly SDR for AI SaaS startups"
            defaultValue={agent.who}/>
        </Panel>
        <Panel label="What does it know about the company?" hint="Company name, what you do, pricing, key links.">
          <textarea className="textarea" rows={8}
            placeholder="Company name, what you do, pricing, key links"
            defaultValue={`Acme — aimly. AI SDR layer for Telegram, founded 2024.
Reply rate 3× cold email. Per-account safety: 4/20/150 corridor + 30-day warm-up.
Residential proxies bound 1-to-1 with each session.
Pricing: $890/mo Starter (3 senders), $2,490 Business (15 senders), Enterprise custom.
Docs: docs.aimly.io · Trust center: aimly.io/trust`}/>
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
      </>}
    />
  );
}

// ============================================================
// Tab: Voice
// ============================================================
const VOICE_OPTIONS = [
  { id: "Professional", title: "Professional", desc: "Polished, structured, value-first" },
  { id: "Friendly",     title: "Friendly",     desc: "Warm and approachable, conversational" },
  { id: "Playful",      title: "Playful",      desc: "Witty, light-touch, on-brand humor" },
];

function TabVoice({ agent }) {
  const [voice, setVoice] = React.useState(agent.voiceBaseline);
  const [tone, setTone] = React.useState({ formal: 0, warm: 15, brief: -5 });
  const [maxLen, setMaxLen] = React.useState(280);
  const [mirror, setMirror] = React.useState(true);
  const [emoji, setEmoji] = React.useState(false);
  const [banlist, setBanlist] = React.useState(["revolutionary", "synergy", "circle back"]);

  return (
    <EditorLayout
      right={<LivePreviewCard agent={{ ...agent, voiceBaseline: voice }}/>}
      left={<>
        <Panel label="Voice baseline" hint="Pick a starting voice. The sliders below fine-tune it.">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
            {VOICE_OPTIONS.map(v => {
              const on = voice === v.id;
              const t = VOICE_TINT[v.id];
              return (
                <button key={v.id} onClick={() => setVoice(v.id)} style={{
                  padding: "14px 14px", borderRadius: 12, textAlign: "left",
                  border: `1.5px solid ${on ? t.fg : "var(--border)"}`,
                  background: on ? t.bg : "white",
                  display: "flex", flexDirection: "column", gap: 8,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ width: 26, height: 26, borderRadius: 7, background: on ? t.fg : "var(--bg-soft)", color: on ? "white" : "var(--text-muted)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <Icon name={t.icon} size={13}/>
                    </div>
                    <div style={{ fontSize: 13.5, fontWeight: 600 }}>{v.title}</div>
                    {on && <div style={{ marginLeft: "auto", color: t.fg }}><Icon name="check" size={14}/></div>}
                  </div>
                  <div className="muted text-xs" style={{ lineHeight: 1.4 }}>{v.desc}</div>
                </button>
              );
            })}
          </div>
        </Panel>

        <Panel label="Tone" hint="Fine-tune within −50 / +50. 0 = neutral.">
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <ToneSlider leftLabel="Formal" rightLabel="Casual" value={tone.formal} onChange={v => setTone(t => ({ ...t, formal: v }))}/>
            <ToneSlider leftLabel="Reserved" rightLabel="Warm" value={tone.warm} onChange={v => setTone(t => ({ ...t, warm: v }))}/>
            <ToneSlider leftLabel="Brief" rightLabel="Detailed" value={tone.brief} onChange={v => setTone(t => ({ ...t, brief: v }))}/>
          </div>
        </Panel>

        <Panel label="Reply constraints">
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>Max message length</div>
                <div className="muted text-xs">Cap on a single reply. Telegram displays well up to ~3,000.</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input className="input" type="number" value={maxLen} onChange={e => setMaxLen(+e.target.value)} style={{ width: 80, textAlign: "right" }}/>
                <span className="muted text-xs">chars</span>
              </div>
            </div>
            <SwitchRow on={mirror} setOn={setMirror}
              title="Mirror user's language"
              desc="Reply in the language the contact wrote in (auto-detect)."/>
            <SwitchRow on={emoji} setOn={setEmoji}
              title="Allow emoji"
              desc="When on, the agent may add 1 emoji per message if fitting."/>
          </div>
        </Panel>

        <Panel label="Banlist" hint="Hard-block phrases. Higher priority than tone or voice.">
          <TagInput
            tags={banlist}
            onAdd={v => setBanlist(b => [...b, v])}
            onRemove={i => setBanlist(b => b.filter((_, k) => k !== i))}
            placeholder="Type a phrase + Enter to ban"
          />
        </Panel>
      </>}
    />
  );
}

function ToneSlider({ leftLabel, rightLabel, value, onChange }) {
  // value -50..+50 → 0..100 for display
  const pos = ((value + 50) / 100) * 100;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, color: "var(--text-muted)", marginBottom: 8, fontWeight: 500 }}>
        <span>{leftLabel}</span>
        <span style={{
          fontVariantNumeric: "tabular-nums",
          background: value === 0 ? "var(--bg-soft)" : value > 0 ? "var(--tg-blue-soft)" : "var(--ai-purple-soft)",
          color: value === 0 ? "var(--text-muted)" : value > 0 ? "var(--tg-blue)" : "var(--ai-purple)",
          padding: "1px 8px", borderRadius: 999, fontSize: 11, fontWeight: 600,
        }}>{value > 0 ? "+" : ""}{value}</span>
        <span>{rightLabel}</span>
      </div>
      <div style={{ position: "relative", height: 24 }}>
        <div style={{ position: "absolute", top: 11, left: 0, right: 0, height: 4, background: "var(--bg-soft)", borderRadius: 999 }}/>
        <div style={{ position: "absolute", top: 11, left: "50%", width: 1, height: 8, background: "var(--border-strong)", transform: "translate(-50%, -2px)" }}/>
        {value !== 0 && (
          <div style={{
            position: "absolute", top: 11, height: 4, borderRadius: 999,
            left: value < 0 ? `${pos}%` : "50%",
            width: value < 0 ? `${50 - pos}%` : `${pos - 50}%`,
            background: value > 0 ? "var(--tg-blue)" : "var(--ai-purple)",
          }}/>
        )}
        <input type="range" min="-50" max="50" value={value} onChange={e => onChange(parseInt(e.target.value))}
          style={{ position: "absolute", inset: 0, width: "100%", opacity: 0, cursor: "pointer" }}/>
        <div style={{
          position: "absolute", top: 2, left: `calc(${pos}% - 10px)`,
          width: 20, height: 20, background: "white", borderRadius: 50,
          boxShadow: `0 1px 3px rgba(0,0,0,0.18), 0 0 0 1.5px ${value > 0 ? "var(--tg-blue)" : value < 0 ? "var(--ai-purple)" : "var(--border-strong)"}`,
          pointerEvents: "none",
        }}/>
      </div>
    </div>
  );
}

function SwitchRow({ on, setOn, title, desc }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
        <div className="muted text-xs">{desc}</div>
      </div>
      <button onClick={() => setOn(o => !o)} className={`toggle ${on ? "is-on" : ""}`} style={{ flexShrink: 0 }}/>
    </div>
  );
}

function TagInput({ tags, onAdd, onRemove, placeholder }) {
  const [draft, setDraft] = React.useState("");
  const commit = () => {
    const v = draft.trim();
    if (!v) return;
    onAdd(v);
    setDraft("");
  };
  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center",
      padding: 8, border: "1px solid var(--border)", borderRadius: 9,
      background: "white", minHeight: 42,
    }}>
      {tags.map((t, i) => (
        <Tag key={i} onRemove={() => onRemove(i)}>{t}</Tag>
      ))}
      <input
        value={draft} onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); } }}
        onBlur={commit}
        placeholder={placeholder}
        style={{ border: "none", outline: "none", flex: 1, minWidth: 160, fontSize: 12.5, padding: "2px 4px", background: "transparent" }}
      />
    </div>
  );
}

// ============================================================
// Tab: FAQ / Knowledge
// ============================================================
function TabFaq({ agent }) {
  const [qaPairs, setQaPairs] = React.useState([
    { q: "How do you handle Telegram rate limits?", a: "Per-account 4 / 20 / 150 corridor across hour / day / week, plus a 30-day warm-up curve. Residential proxies are bound 1-to-1 with each account." },
    { q: "Do you store our cloud password?", a: "No. We use MTProto and store only the session token, encrypted at rest. Cloud passwords never touch our DB." },
    { q: "What's the typical reply rate?", a: "Across our customer base, reply rates land between 18–32% — roughly 3× cold email. Heavily depends on ICP fit and agent quality." },
  ]);

  const addPair = () => setQaPairs(p => [...p, { q: "", a: "" }]);
  const removePair = i => setQaPairs(p => p.filter((_, k) => k !== i));
  const updatePair = (i, field, value) => setQaPairs(p => p.map((row, k) => k === i ? { ...row, [field]: value } : row));

  return (
    <EditorLayout
      right={<LivePreviewCard agent={agent}/>}
      left={<>
        <Panel label="Knowledge base" hint="What should the agent know about your company and leads?">
          <textarea className="textarea" rows={10}
            placeholder="Paste product context, ICP description, common objections, proof points, anything the agent needs to recall…"
            defaultValue={`ICP — early-stage AI SaaS founders (seed → Series B), US/EU, 5-50 ppl.
They've burned out on email outbound. Open to Telegram if it's not spammy.

Pricing tiers:
  Starter $890 — 3 senders, 1 agent
  Business $2,490 — 15 senders, 5 agents, custom signals
  Enterprise — 30+ senders, SSO, dedicated infra

Top 3 objections we hear:
  1) "Will it get our accounts banned?" → cite 4/20/150 corridor + warm-up
  2) "How is this different from Apollo?" → reply rate + native channel
  3) "Can we test on a small batch?" → yes, 100-contact pilot free`}/>
        </Panel>

        <Panel label={`Q&A pairs (${qaPairs.length})`} hint="Higher priority than retrieved chunks. Canonical phrasing.">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {qaPairs.map((qa, i) => (
              <div key={i} style={{ padding: 12, borderRadius: 10, border: "1px solid var(--border)", background: "white" }}>
                <div style={{ display: "flex", gap: 10, alignItems: "flex-start", marginBottom: 8 }}>
                  <div style={{ width: 22, height: 22, borderRadius: 7, background: "var(--ai-purple-soft)", color: "var(--ai-purple)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, fontSize: 11, fontWeight: 700 }}>Q</div>
                  <textarea rows={1} value={qa.q} onChange={e => updatePair(i, "q", e.target.value)}
                    placeholder="Question…"
                    style={{ flex: 1, border: "none", outline: "none", resize: "none", fontSize: 13, fontWeight: 500, lineHeight: 1.4, background: "transparent", fontFamily: "inherit" }}/>
                  <button onClick={() => removePair(i)} className="tb__icon-btn" style={{ width: 26, height: 26 }}>
                    <Icon name="x" size={13}/>
                  </button>
                </div>
                <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <div style={{ width: 22, height: 22, borderRadius: 7, background: "var(--tg-blue-soft)", color: "var(--tg-blue)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, fontSize: 11, fontWeight: 700 }}>A</div>
                  <textarea rows={2} value={qa.a} onChange={e => updatePair(i, "a", e.target.value)}
                    placeholder="Answer…"
                    style={{ flex: 1, border: "none", outline: "none", resize: "vertical", fontSize: 12.5, lineHeight: 1.5, color: "var(--text-soft)", background: "transparent", fontFamily: "inherit", minHeight: 32 }}/>
                </div>
              </div>
            ))}
            <button onClick={addPair} className="btn btn--soft btn--sm" style={{ alignSelf: "flex-start" }}>
              <Icon name="plus" size={12}/> Add Q&A
            </button>
          </div>
        </Panel>
      </>}
    />
  );
}

// ============================================================
// Tab: Safety
// ============================================================
function TabSafety({ agent }) {
  const [triggers, setTriggers] = React.useState([
    "unsubscribe", "stop messaging me", "не пишите больше", "report you", "spam", "lawyer", "GDPR"
  ]);

  return (
    <EditorLayout
      left={<>
        <Panel label="Auto-pause when user message matches"
          hint="AI will stop replying when these patterns appear (regex or plain phrases).">
          <TagInput
            tags={triggers}
            onAdd={v => setTriggers(t => [...t, v])}
            onRemove={i => setTriggers(t => t.filter((_, k) => k !== i))}
            placeholder="Add a phrase or /regex/ + Enter"
          />
          <div style={{ marginTop: 14, padding: 12, background: "var(--bg-soft)", borderRadius: 9, display: "flex", gap: 10, fontSize: 12.5, color: "var(--text-soft)", lineHeight: 1.5 }}>
            <Icon name="info" size={14} color="var(--tg-blue)" style={{ flexShrink: 0, marginTop: 2 }}/>
            <div>
              When a match fires, the conversation switches to <b>manager mode</b> — AI stops, the dialog is flagged in the inbox, and the campaign sender stays locked to this contact only for the human.
            </div>
          </div>
        </Panel>

        <Panel label="Pause behavior">
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <RadioRow
              checked
              title="Pause this conversation only"
              desc="The agent keeps running with other contacts. The flagged thread waits for a human."/>
            <RadioRow
              title="Pause this contact across all campaigns"
              desc="Useful for unsubscribes — never message this person again, from any agent."/>
            <RadioRow
              title="Pause the entire campaign"
              desc="Hard stop — for emergency containment only."/>
          </div>
        </Panel>

        <Panel label="Recent auto-pauses" hint="Last 7 days · click to review">
          <div style={{ display: "flex", flexDirection: "column" }}>
            {[
              { who: "Sam Whitaker", trigger: '"unsubscribe"', at: "2h ago" },
              { who: "Ava Morales", trigger: '"stop messaging me"', at: "yesterday" },
              { who: "Bryan Cole", trigger: '"GDPR"', at: "3 days ago" },
            ].map((p, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: i < 2 ? "1px solid var(--divider)" : "none" }}>
                <Avatar name={p.who} size="sm"/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{p.who}</div>
                  <div className="muted text-xs">Matched <span className="mono" style={{ color: "var(--danger)" }}>{p.trigger}</span></div>
                </div>
                <span className="muted text-xs">{p.at}</span>
                <button className="btn btn--ghost btn--sm">Review</button>
              </div>
            ))}
          </div>
        </Panel>
      </>}
    />
  );
}

function RadioRow({ checked, title, desc }) {
  return (
    <label style={{
      display: "flex", alignItems: "flex-start", gap: 12, padding: 12, borderRadius: 10,
      border: `1.5px solid ${checked ? "var(--tg-blue)" : "var(--border)"}`,
      background: checked ? "var(--tg-blue-softer)" : "white",
      cursor: "pointer",
    }}>
      <div style={{
        width: 18, height: 18, borderRadius: "50%", marginTop: 2,
        border: `2px solid ${checked ? "var(--tg-blue)" : "var(--border-strong)"}`,
        background: "white", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {checked && <div style={{ width: 8, height: 8, borderRadius: 50, background: "var(--tg-blue)" }}/>}
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
        <div className="muted text-xs" style={{ marginTop: 2, lineHeight: 1.5 }}>{desc}</div>
      </div>
    </label>
  );
}

Object.assign(window, { AgentsScreen });
