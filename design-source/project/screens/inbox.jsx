// inbox.jsx — three-pane inbox with LLM thought trace
function InboxScreen({ embedded, campaignFilter, onOpenConvo }) {
  const [selectedId, setSelectedId] = React.useState("v1");
  const [filter, setFilter] = React.useState("all");
  const [agent, setAgent] = React.useState("all");
  const [showLlm, setShowLlm] = React.useState(true);

  const list = CONVOS
    .filter(v => !campaignFilter || v.campaign === campaignFilter)
    .filter(v => filter === "all" || v.status === filter)
    .filter(v => agent === "all" || v.agent === agent);

  const selected = CONVOS.find(v => v.id === selectedId) || list[0];

  const FILTERS = [
    { id: "all", label: "All" },
    { id: "active", label: "Active" },
    { id: "lead", label: "Leads" },
    { id: "handoff", label: "Handoff" },
    { id: "no-reply", label: "No reply" },
    { id: "finished", label: "Finished" },
  ];

  return (
    <>
      {!embedded && (
        <div className="tb">
          <div className="tb__title">Inbox</div>
          <div className="tb__right">
            <button className="btn btn--ghost btn--sm">
              <Icon name="filter" size={14}/> Saved views
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => setShowLlm(s => !s)}>
              <Icon name="brain" size={14}/> {showLlm ? "Hide" : "Show"} LLM trace
            </button>
            <button className="btn btn--ghost btn--sm">
              <Icon name="export" size={14}/> Export
            </button>
          </div>
        </div>
      )}

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: showLlm ? "320px 1fr 360px" : "320px 1fr", minHeight: 0 }}>
        {/* List pane */}
        <div style={{ borderRight: "1px solid var(--border)", background: "var(--bg)", display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "14px 14px 8px" }}>
            <div style={{ position: "relative" }}>
              <Icon name="search" size={14} color="var(--text-faint)" style={{ position: "absolute", left: 11, top: 12 }}/>
              <input className="input" style={{ paddingLeft: 32, height: 36, fontSize: 13 }} placeholder="Search conversations"/>
            </div>
          </div>
          <div style={{ padding: "4px 8px 6px", display: "flex", gap: 4, overflowX: "auto", borderBottom: "1px solid var(--divider)" }}>
            {FILTERS.map(f => (
              <button key={f.id}
                onClick={() => setFilter(f.id)}
                style={{
                  padding: "5px 10px", borderRadius: 7, fontSize: 12, fontWeight: 500, whiteSpace: "nowrap",
                  background: filter === f.id ? "var(--tg-blue-soft)" : "transparent",
                  color: filter === f.id ? "var(--tg-blue)" : "var(--text-muted)",
                }}>
                {f.label}
              </button>
            ))}
          </div>
          <div className="scroll" style={{ flex: 1 }}>
            {list.map(v => {
              const sel = v.id === selectedId;
              return (
                <div key={v.id}
                  onClick={() => setSelectedId(v.id)}
                  style={{
                    padding: "12px 14px", display: "flex", gap: 10, cursor: "pointer",
                    background: sel ? "var(--tg-blue-softer)" : "transparent",
                    borderLeft: `3px solid ${sel ? "var(--tg-blue)" : "transparent"}`,
                    borderBottom: "1px solid var(--divider)",
                  }}>
                  <Avatar name={v.contact}/>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                      <span style={{ fontSize: 13.5, fontWeight: v.unread > 0 ? 600 : 500, flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {v.contact}
                      </span>
                      <span className="muted text-xs" style={{ flexShrink: 0 }}>{v.lastAt}</span>
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {v.username} · {v.country}
                    </div>
                    <div style={{ fontSize: 12, color: v.unread > 0 ? "var(--text)" : "var(--text-muted)", lineHeight: 1.4,
                                  display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                      {v.snippet}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
                      <StatusPill status={v.status}/>
                      {v.starred && <Icon name="star" size={12} color="#f5a623"/>}
                      <span className="spacer"/>
                      {v.unread > 0 && <span style={{ background: "var(--tg-blue)", color: "white", fontSize: 10, padding: "1px 6px", borderRadius: 999, fontWeight: 600 }}>{v.unread}</span>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Conversation pane */}
        {selected && <ConversationPane v={selected} onToggleLlm={() => setShowLlm(s => !s)} llmShown={showLlm}/>}

        {/* LLM trace pane */}
        {showLlm && <LlmTracePane v={selected}/>}
      </div>
    </>
  );
}

function ConversationPane({ v, onToggleLlm, llmShown }) {
  const [draft, setDraft] = React.useState("");
  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg-soft)" }}>
      <div style={{
        padding: "12px 20px", background: "var(--bg)", borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", gap: 12,
      }}>
        <Avatar name={v.contact} size="lg"/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14.5, fontWeight: 600 }}>{v.contact}</span>
            <span className="muted text-sm">· {v.username}</span>
            <span className="muted text-xs">· {v.country}</span>
          </div>
          <div className="muted text-xs" style={{ marginTop: 2 }}>
            {v.company} · last active {v.lastAt}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginRight: 10, paddingRight: 14, borderRight: "1px solid var(--divider)" }}>
          <KV label="Agent" value={v.agent} icon="agents"/>
          <KV label="Sender" value={v.sender} icon="send"/>
          <KV label="Campaign" value={v.campaign} icon="campaigns"/>
        </div>
        <button className="btn btn--sm btn--ghost"><Icon name="star" size={14}/></button>
        <button className="btn btn--sm btn--ghost"><Icon name="pin" size={14}/></button>
        <button className="btn btn--sm" style={{ background: "var(--warning-soft)", color: "#a86200" }}>
          <Icon name="user" size={14}/> Take over
        </button>
        {!llmShown && (
          <button className="btn btn--sm btn--ghost" onClick={onToggleLlm}>
            <Icon name="brain" size={14}/>
          </button>
        )}
      </div>

      {/* Signal banner */}
      {v.status === "lead" && (
        <div style={{ padding: "10px 20px", background: "linear-gradient(90deg, var(--success-soft), var(--tg-blue-softer))",
                      borderBottom: "1px solid var(--divider)", display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 24, height: 24, borderRadius: 7, background: "var(--success)", color: "white", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Icon name="flag" size={13}/>
          </div>
          <div style={{ fontSize: 12.5 }}>
            <b>Lead detected</b> · Meeting booked for Tue Apr 30, 3:00 PM PT
          </div>
          <span className="spacer"/>
          <button className="btn btn--sm" style={{ background: "var(--success)", color: "white" }}>
            <Icon name="check" size={12}/> Confirm
          </button>
          <button className="btn btn--sm btn--ghost">
            <Icon name="x" size={12}/> Dismiss
          </button>
        </div>
      )}

      <div className="scroll" style={{ flex: 1, padding: "20px 24px" }}>
        {(v.messages || sampleMessages(v)).map((m, i) => (
          <Message key={i} m={m}/>
        ))}
      </div>

      <div style={{ padding: 16, borderTop: "1px solid var(--border)", background: "var(--bg)" }}>
        <div style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg)" }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <SuggestionChip color="var(--tg-blue)" icon="sparkles">Send meeting invite</SuggestionChip>
            <SuggestionChip color="var(--ai-purple)" icon="message">Ask about decision timeline</SuggestionChip>
            <SuggestionChip color="var(--success)" icon="check">Confirm pricing</SuggestionChip>
          </div>
          <textarea
            className="textarea"
            placeholder="Reply as Maya, or type / for templates…"
            value={draft} onChange={e => setDraft(e.target.value)}
            style={{ border: "none", padding: 0, minHeight: 50, resize: "none" }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 8, paddingTop: 8, borderTop: "1px solid var(--divider)" }}>
            <button className="tb__icon-btn" style={{ width: 32, height: 32 }}><Icon name="paperclip" size={16}/></button>
            <button className="tb__icon-btn" style={{ width: 32, height: 32 }}><Icon name="smile" size={16}/></button>
            <span className="spacer"/>
            <span className="muted text-xs">Sending as <b>Anna Petrova</b> via aimly</span>
            <button className="btn btn--primary btn--sm"><Icon name="send" size={12}/> Send</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function KV({ label, value, icon }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
      <Icon name={icon} size={13} color="var(--text-faint)"/>
      <div style={{ minWidth: 0 }}>
        <div className="muted text-xs" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
        <div style={{ fontSize: 12, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 130 }}>{value}</div>
      </div>
    </div>
  );
}

function Message({ m }) {
  const isAgent = m.from === "agent";
  return (
    <div style={{ display: "flex", justifyContent: isAgent ? "flex-end" : "flex-start", marginBottom: 14 }}>
      <div style={{ maxWidth: "70%" }}>
        {!isAgent && (
          <div className="muted text-xs" style={{ marginBottom: 4, paddingLeft: 14 }}>{m.time}</div>
        )}
        <div style={{
          padding: "10px 14px",
          borderRadius: isAgent ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
          background: isAgent ? "var(--tg-blue)" : "white",
          color: isAgent ? "white" : "var(--text)",
          fontSize: 13.5, lineHeight: 1.5,
          boxShadow: isAgent ? "none" : "0 1px 1px rgba(15,20,25,0.04), 0 0 0 1px rgba(15,20,25,0.04)",
        }}>{m.text}</div>
        {isAgent && (
          <div className="muted text-xs" style={{ marginTop: 4, paddingRight: 14, textAlign: "right" }}>
            {m.time} · sent via Maya
            <Icon name="check" size={11} style={{ marginLeft: 4 }}/>
          </div>
        )}
      </div>
    </div>
  );
}

function SuggestionChip({ children, icon, color }) {
  return (
    <button style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      height: 28, padding: "0 10px", borderRadius: 8,
      background: `${color}12`, color, fontSize: 12, fontWeight: 500,
    }}>
      <Icon name={icon} size={12}/> {children}
    </button>
  );
}

function sampleMessages(v) {
  return [
    { from: "agent", time: "Apr 28, 14:02", text: `Hi ${v.contact.split(" ")[0]} — Anna here from Acme. Quick question: how are you handling outbound on your side right now?` },
    { from: "contact", time: "Apr 28, 15:18", text: v.snippet || "Hey. Tell me more." },
  ];
}

// ============================================================
// LLM trace pane
// ============================================================
function LlmTracePane({ v }) {
  const [openId, setOpenId] = React.useState("t1");
  return (
    <div style={{ borderLeft: "1px solid var(--border)", background: "var(--bg)", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--divider)", display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 28, height: 28, borderRadius: 8, background: "linear-gradient(135deg, var(--ai-purple), var(--tg-blue))", color: "white", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="brain" size={14}/>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>Thought trace</div>
          <div className="muted text-xs">Why Maya said what she said</div>
        </div>
        <button className="tb__icon-btn" style={{ width: 28, height: 28 }}><Icon name="more" size={14}/></button>
      </div>

      <div className="scroll" style={{ flex: 1, padding: "14px 16px" }}>
        {LLM_TRACE.map((t, i) => (
          <TraceEntry key={t.id} t={t} open={openId === t.id} onToggle={() => setOpenId(openId === t.id ? null : t.id)} latest={i === 0}/>
        ))}

        <div style={{ display: "flex", justifyContent: "center", margin: "12px 0", color: "var(--text-faint)", fontSize: 11 }}>
          + 5 earlier calls
        </div>
      </div>
    </div>
  );
}

function TraceEntry({ t, open, onToggle, latest }) {
  return (
    <div style={{ marginBottom: 12, borderRadius: 12, border: "1px solid var(--border)", overflow: "hidden", background: latest ? "linear-gradient(180deg, var(--ai-purple-soft) 0%, white 30%)" : "white" }}>
      <button onClick={onToggle} style={{
        width: "100%", display: "flex", alignItems: "center", gap: 10, padding: "11px 12px",
        textAlign: "left",
      }}>
        <div style={{ width: 22, height: 22, borderRadius: 6, background: "var(--ai-purple-soft)", color: "var(--ai-purple)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <Icon name="sparkles" size={11}/>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {t.intent}
          </div>
          <div className="muted text-xs" style={{ marginTop: 2 }}>
            {t.at} · {t.latency} · {t.in_tokens + t.out_tokens} tok
          </div>
        </div>
        <Icon name={open ? "chevron_down" : "chevron_right"} size={14} color="var(--text-muted)"/>
      </button>
      {open && (
        <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
          <TraceBlock label="System / context" body={t.system_summary} icon="cpu" tone="default"/>
          {t.tools.length > 0 && (
            <div>
              <div className="muted text-xs" style={{ marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
                <Icon name="tool" size={11}/> Tool calls
              </div>
              {t.tools.map((tl, i) => (
                <div key={i} style={{ padding: "8px 10px", background: "var(--bg-soft)", borderRadius: 8, fontSize: 11.5, marginBottom: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="mono fw6">{tl.name}</span>
                    <span style={{ color: "var(--success)", fontSize: 10.5 }}>✓ {typeof tl.result === "string" ? tl.result.slice(0, 24) : "ok"}</span>
                  </div>
                  <div className="mono muted text-xs" style={{ wordBreak: "break-all" }}>
                    {JSON.stringify(tl.args)}
                  </div>
                </div>
              ))}
            </div>
          )}
          <TraceBlock label="Agent response" body={t.response} icon="message" tone="blue"/>
          {t.signals.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {t.signals.map(s => (
                <span key={s} className="pill pill--green" style={{ fontSize: 10.5 }}>
                  <Icon name="zap" size={9}/> {s}
                </span>
              ))}
            </div>
          )}
          <div className="muted text-xs" style={{ display: "flex", gap: 12, padding: "8px 0 0", borderTop: "1px solid var(--divider)" }}>
            <span>{t.model}</span>
            <span>·</span>
            <span>{t.in_tokens} → {t.out_tokens} tok</span>
            <span>·</span>
            <span>{t.cost}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function TraceBlock({ label, body, icon, tone }) {
  return (
    <div>
      <div className="muted text-xs" style={{ marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
        <Icon name={icon} size={11}/> {label}
      </div>
      <div style={{
        padding: "10px 12px", borderRadius: 8, fontSize: 12, lineHeight: 1.5,
        background: tone === "blue" ? "var(--tg-blue-softer)" : "var(--bg-soft)",
        color: tone === "blue" ? "var(--text)" : "var(--text-soft)",
        borderLeft: `3px solid ${tone === "blue" ? "var(--tg-blue)" : "var(--text-faint)"}`,
      }}>{body}</div>
    </div>
  );
}

Object.assign(window, { InboxScreen });
