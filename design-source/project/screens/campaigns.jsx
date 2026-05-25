// campaigns.jsx — list of campaigns + entry point to create

function Campaigns({ onOpenCampaign, onNewCampaign }) {
  const [tab, setTab] = React.useState("all");
  const [search, setSearch] = React.useState("");

  const counts = {
    all: CAMPAIGNS.length,
    running: CAMPAIGNS.filter(c => c.status === "running").length,
    paused: CAMPAIGNS.filter(c => c.status === "paused").length,
    draft: CAMPAIGNS.filter(c => c.status === "draft").length,
    scheduled: CAMPAIGNS.filter(c => c.status === "scheduled").length,
    finished: CAMPAIGNS.filter(c => c.status === "finished").length,
  };

  const filtered = CAMPAIGNS
    .filter(c => tab === "all" || c.status === tab)
    .filter(c => !search || c.name.toLowerCase().includes(search.toLowerCase()));

  return (
    <>
      <div className="tb">
        <div className="tb__title">Campaigns</div>
        <div className="tb__right">
          <div style={{
            display: "flex", alignItems: "center", gap: 6, padding: "0 12px", height: 36,
            background: "var(--bg-soft)", borderRadius: 9, color: "var(--text-muted)",
          }}>
            <Icon name="search" size={14}/>
            <input
              placeholder="Search campaigns…"
              value={search} onChange={e => setSearch(e.target.value)}
              style={{ background: "none", border: "none", outline: "none", width: 220, fontSize: 13 }}/>
          </div>
          <button className="btn btn--ghost btn--sm">
            <Icon name="filter" size={14}/> Filters
          </button>
          <button className="btn btn--primary" onClick={onNewCampaign}>
            <Icon name="plus" size={14}/> New campaign
          </button>
        </div>
      </div>

      <div className="tabs">
        {[
          ["all", "All"],
          ["running", "Running"],
          ["paused", "Paused"],
          ["scheduled", "Scheduled"],
          ["draft", "Drafts"],
          ["finished", "Finished"],
        ].map(([id, label]) => (
          <button key={id} className={`tab ${tab === id ? "is-active" : ""}`} onClick={() => setTab(id)}>
            {label} <span className="count">{counts[id]}</span>
          </button>
        ))}
      </div>

      <div className="scroll" style={{ flex: 1, padding: 24 }}>
        <div className="card" style={{ overflow: "hidden" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 32 }}><input type="checkbox" disabled/></th>
                <th>Campaign</th>
                <th>Status</th>
                <th>Agent · Folder</th>
                <th>Senders</th>
                <th>Progress</th>
                <th style={{ textAlign: "right" }}>Funnel (sent → leads)</th>
                <th style={{ width: 40 }}/>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => (
                <tr key={c.id} onClick={() => onOpenCampaign(c.id)} style={{ cursor: "pointer" }}>
                  <td onClick={e => e.stopPropagation()}>
                    <input type="checkbox"/>
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <div style={{
                        width: 36, height: 36, borderRadius: 9,
                        background: c.status === "running" ? "var(--success-soft)"
                                  : c.status === "paused" ? "var(--warning-soft)"
                                  : c.status === "scheduled" ? "var(--tg-blue-soft)"
                                  : c.status === "draft" ? "var(--bg-soft)"
                                  : "var(--bg-soft)",
                        color: c.status === "running" ? "#1e8a3a"
                             : c.status === "paused" ? "#a86200"
                             : c.status === "scheduled" ? "var(--tg-blue)"
                             : "var(--text-muted)",
                        display: "flex", alignItems: "center", justifyContent: "center",
                      }}>
                        <Icon name={c.status === "scheduled" ? "calendar" : c.status === "paused" ? "pause" : c.status === "draft" ? "edit" : c.status === "finished" ? "flag" : "play"} size={15}/>
                      </div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontWeight: 500, fontSize: 13.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 260 }}>{c.name}</div>
                        <div className="muted text-xs" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.startedAt} · {c.hours}</div>
                      </div>
                    </div>
                  </td>
                  <td><StatusPill status={c.status}/></td>
                  <td>
                    <div style={{ fontSize: 12.5 }}>{c.agent}</div>
                    <div className="muted text-xs">{c.folder} · {c.contacts.toLocaleString()} contacts</div>
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: -4 }}>
                      {Array.from({ length: Math.min(c.senders, 3) }).map((_, i) => (
                        <div key={i} style={{
                          width: 22, height: 22, borderRadius: 50, marginLeft: i ? -6 : 0,
                          ...avatarStyle("sender" + (c.id + i)),
                          border: "2px solid white",
                          fontSize: 9, color: "white",
                          display: "flex", alignItems: "center", justifyContent: "center",
                        }}>{String.fromCharCode(65 + i)}</div>
                      ))}
                      {c.senders > 3 && (
                        <span style={{ marginLeft: 4, fontSize: 11.5, color: "var(--text-muted)" }}>+{c.senders - 3}</span>
                      )}
                      {c.senders === 0 && <span className="muted text-xs">—</span>}
                    </div>
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 100 }}>
                      <div style={{ flex: 1, height: 5, background: "var(--bg-soft)", borderRadius: 999, overflow: "hidden" }}>
                        <div style={{ width: `${c.progress * 100}%`, height: "100%", background: "var(--tg-blue)", borderRadius: 999 }}/>
                      </div>
                      <span className="num text-xs muted">{Math.round(c.progress * 100)}%</span>
                    </div>
                  </td>
                  <td>
                    <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12 }}>
                      <FunnelMini sent={c.sent} replied={c.replied} leads={c.leads}/>
                    </div>
                  </td>
                  <td onClick={e => e.stopPropagation()}>
                    <button className="tb__icon-btn" style={{ width: 28, height: 28 }}>
                      <Icon name="more" size={16}/>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function FunnelMini({ sent, replied, leads }) {
  const max = Math.max(sent, 1);
  const bar = (v, color) => (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 50, height: 4, background: "var(--bg-soft)", borderRadius: 999, overflow: "hidden" }}>
        <div style={{ width: `${(v / max) * 100}%`, height: "100%", background: color, borderRadius: 999 }}/>
      </div>
      <span className="num text-xs" style={{ minWidth: 36, textAlign: "right", color: "var(--text-soft)" }}>{v.toLocaleString()}</span>
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {bar(sent, "var(--tg-blue)")}
      {bar(replied, "var(--ai-purple)")}
      {bar(leads, "var(--success)")}
    </div>
  );
}

Object.assign(window, { Campaigns });
