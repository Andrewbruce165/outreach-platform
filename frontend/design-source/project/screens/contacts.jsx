// contacts.jsx — folders + contacts table + CSV import overlay

function ContactsScreen() {
  const [folderId, setFolderId] = React.useState("f1");
  const [importing, setImporting] = React.useState(false);
  const folder = FOLDERS.find(f => f.id === folderId);

  return (
    <>
      <div className="tb">
        <div className="tb__title">Contacts</div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm" onClick={() => setImporting(true)}>
            <Icon name="upload" size={14}/> Import CSV
          </button>
          <button className="btn btn--primary">
            <Icon name="plus" size={14}/> New folder
          </button>
        </div>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "280px 1fr", minHeight: 0 }}>
        {/* Folder list */}
        <div style={{ background: "var(--bg)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "12px 14px 8px", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.06em", fontSize: 11, fontWeight: 600 }}>
            Folders ({FOLDERS.length})
          </div>
          <div className="scroll" style={{ flex: 1, padding: "0 8px" }}>
            {FOLDERS.map(f => {
              const sel = f.id === folderId;
              return (
                <button key={f.id} onClick={() => setFolderId(f.id)} style={{
                  width: "100%", display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 12px", borderRadius: 9, marginBottom: 1,
                  background: sel ? "var(--tg-blue-soft)" : "transparent",
                  color: sel ? "var(--tg-blue)" : "var(--text-soft)",
                  textAlign: "left",
                }}>
                  <div style={{ width: 28, height: 28, borderRadius: 7, background: `${f.color}1A`, color: f.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <Icon name="folder" size={14}/>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{f.name}</div>
                    <div className="muted text-xs">{f.contacts.toLocaleString()} contacts</div>
                  </div>
                </button>
              );
            })}
            <button style={{
              width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
              padding: "10px 12px", borderRadius: 9, marginTop: 6,
              border: "1px dashed var(--border-strong)", color: "var(--text-muted)",
              fontSize: 12.5,
            }}>
              <Icon name="plus" size={13}/> New folder
            </button>
          </div>
        </div>

        {/* Folder contents */}
        <div className="scroll" style={{ background: "var(--bg-soft)", padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 16, gap: 14 }}>
            <div style={{ width: 44, height: 44, borderRadius: 11, background: `${folder.color}1A`, color: folder.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Icon name="folder" size={22}/>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>{folder.name}</div>
              <div className="muted text-sm">{folder.contacts.toLocaleString()} contacts · {folder.source}</div>
            </div>
            <button className="btn btn--ghost btn--sm"><Icon name="shuffle" size={13}/> Move to…</button>
            <button className="btn btn--ghost btn--sm"><Icon name="trash" size={13}/></button>
          </div>

          {/* Stats */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
            <MiniMetric label="Total" value={folder.contacts} sub="All sources" color="var(--tg-blue)"/>
            <MiniMetric label="In Telegram" value={Math.round(folder.contacts * folder.inTg)} sub={`${Math.round(folder.inTg * 100)}% match`} color="var(--success)"/>
            <MiniMetric label="Currently messaged" value={Math.round(folder.contacts * 0.42)} sub="In active campaigns" color="var(--ai-purple)"/>
            <MiniMetric label="Replied" value={Math.round(folder.contacts * 0.11)} sub="Across all time" color="var(--warning)"/>
          </div>

          {/* Contacts table */}
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="card__header" style={{ gap: 10 }}>
              <div style={{ position: "relative" }}>
                <Icon name="search" size={14} color="var(--text-faint)" style={{ position: "absolute", left: 10, top: 9 }}/>
                <input className="input" style={{ paddingLeft: 30, height: 32, fontSize: 12.5, width: 240 }} placeholder="Search contacts…"/>
              </div>
              <button className="btn btn--ghost btn--sm"><Icon name="filter" size={12}/> Filters</button>
              <span className="spacer"/>
              <span className="muted text-xs">Showing 12 of {folder.contacts.toLocaleString()}</span>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 32 }}><input type="checkbox" disabled/></th>
                  <th>Contact</th>
                  <th>Company · Role</th>
                  <th>Username</th>
                  <th>Phone</th>
                  <th>Source</th>
                  <th>In TG</th>
                </tr>
              </thead>
              <tbody>
                {CONTACTS_SAMPLE.map((c, i) => (
                  <tr key={i}>
                    <td><input type="checkbox"/></td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <Avatar name={c.fullName}/>
                        <span style={{ fontWeight: 500, fontSize: 13 }}>{c.fullName}</span>
                      </div>
                    </td>
                    <td>
                      <div style={{ fontSize: 12.5 }}>{c.custom.company}</div>
                      <div className="muted text-xs">{c.custom.role}</div>
                    </td>
                    <td>
                      {c.username
                        ? <span className="mono text-sm" style={{ color: "var(--tg-blue)" }}>{c.username}</span>
                        : <span className="muted text-xs">— phone only</span>}
                    </td>
                    <td className="muted text-xs mono">{c.phone}</td>
                    <td><span className="pill">{c.source}</span></td>
                    <td>
                      {c.inTg
                        ? <span style={{ color: "var(--success)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                            <Icon name="check" size={12}/> Yes
                          </span>
                        : <span style={{ color: "var(--text-faint)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                            <Icon name="clock" size={12}/> Checking…
                          </span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {importing && <ImportOverlay onClose={() => setImporting(false)}/>}
    </>
  );
}

// ============================================================
// CSV import overlay — animated parse + field mapping
// ============================================================
function ImportOverlay({ onClose }) {
  const [stage, setStage] = React.useState(0); // 0 picker, 1 mapping, 2 importing, 3 done
  const [progress, setProgress] = React.useState(0);

  React.useEffect(() => {
    if (stage !== 2) return;
    let p = 0;
    const t = setInterval(() => {
      p += Math.random() * 14 + 4;
      if (p >= 100) { p = 100; setProgress(100); clearInterval(t); setTimeout(() => setStage(3), 400); return; }
      setProgress(p);
    }, 180);
    return () => clearInterval(t);
  }, [stage]);

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(15,20,25,0.55)", backdropFilter: "blur(4px)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9999 }}>
      <div style={{ width: 560, background: "white", borderRadius: 18, boxShadow: "0 30px 80px rgba(0,0,0,0.3)", overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--divider)", display: "flex", alignItems: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Import contacts from CSV</div>
          <span className="spacer"/>
          <button className="tb__icon-btn" style={{ width: 28, height: 28 }} onClick={onClose}><Icon name="x" size={14}/></button>
        </div>
        <div style={{ padding: 22 }}>
          {stage === 0 && (
            <div>
              <button onClick={() => setStage(1)} style={{
                width: "100%", border: "2px dashed var(--border-strong)", background: "var(--bg-softer)",
                padding: 38, borderRadius: 14, display: "flex", flexDirection: "column", alignItems: "center", gap: 10,
                color: "var(--text-muted)",
              }}>
                <div style={{ width: 48, height: 48, borderRadius: 12, background: "var(--tg-blue-soft)", color: "var(--tg-blue)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Icon name="upload" size={22}/>
                </div>
                <div style={{ fontSize: 14, color: "var(--text)", fontWeight: 500 }}>Drag & drop or click to upload</div>
                <div className="text-xs">CSV up to 200 MB · or paste a Google Sheets URL</div>
              </button>
              <div className="field" style={{ marginTop: 18 }}>
                <div className="field__label">Target folder</div>
                <select className="select">
                  <option>SaaS founders · US</option>
                  <option>Crypto whales · top 500</option>
                  <option>+ Create new folder</option>
                </select>
              </div>
            </div>
          )}
          {stage === 1 && (
            <div>
              <div className="muted text-sm" style={{ marginBottom: 14 }}>
                Detected <b>linkedin_q2.csv</b> · 5,824 rows · we'll match TG handles after import.
              </div>
              <div className="card" style={{ padding: 0 }}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>CSV column</th>
                      <th>→</th>
                      <th>aimly field</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ["full_name", "Full name"],
                      ["company_name", "custom.company"],
                      ["title", "custom.role"],
                      ["phone_e164", "Phone"],
                      ["tg_username", "Username"],
                      ["source", "Source"],
                    ].map(([from, to]) => (
                      <tr key={from}>
                        <td className="mono text-xs">{from}</td>
                        <td style={{ color: "var(--text-faint)" }}>→</td>
                        <td>
                          <span className="pill pill--blue">{to}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
                <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
                <button className="btn btn--primary" onClick={() => setStage(2)}>Import 5,824 contacts</button>
              </div>
            </div>
          )}
          {stage === 2 && (
            <div>
              <div style={{ textAlign: "center", padding: 20 }}>
                <div style={{
                  width: 56, height: 56, borderRadius: 14, margin: "0 auto",
                  background: "linear-gradient(135deg, var(--ai-purple), var(--tg-blue))",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "white", marginBottom: 14,
                }}>
                  <Icon name="sparkles" size={26}/>
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Matching contacts to Telegram…</div>
                <div className="muted text-sm" style={{ marginBottom: 18 }}>
                  Resolving usernames & checking handles ({Math.round(progress * 58.24)} / 5,824)
                </div>
                <div style={{ height: 6, background: "var(--bg-soft)", borderRadius: 999, overflow: "hidden", margin: "0 auto", maxWidth: 360 }}>
                  <div style={{ width: `${progress}%`, height: "100%", background: "linear-gradient(90deg, var(--ai-purple), var(--tg-blue))", transition: "width 0.18s" }}/>
                </div>
              </div>
            </div>
          )}
          {stage === 3 && (
            <div style={{ textAlign: "center", padding: 20 }}>
              <div style={{
                width: 56, height: 56, borderRadius: 14, margin: "0 auto",
                background: "var(--success-soft)", color: "var(--success)",
                display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 14,
              }}>
                <Icon name="check" size={26}/>
              </div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>Import complete</div>
              <div className="muted text-sm" style={{ marginTop: 6, marginBottom: 22 }}>
                <b>5,824</b> contacts added · <b>4,168</b> found in Telegram (71.6%)<br/>
                <b>1,212</b> auto-enrolled into <b>SaaS founders · US</b>
              </div>
              <button className="btn btn--primary" onClick={onClose}>Open folder</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ContactsScreen });
