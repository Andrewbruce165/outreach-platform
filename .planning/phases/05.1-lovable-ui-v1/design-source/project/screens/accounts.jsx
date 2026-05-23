// accounts.jsx — TG accounts list + onboarding flow

function AccountsScreen() {
  const [adding, setAdding] = React.useState(false);
  return (
    <>
      <div className="tb">
        <div className="tb__title">Telegram accounts</div>
        <div className="tb__right">
          <button className="btn btn--ghost btn--sm"><Icon name="filter" size={14}/> Filters</button>
          <button className="btn btn--primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={14}/> Connect account
          </button>
        </div>
      </div>

      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        {/* Top stats */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginBottom: 16 }}>
          <MiniMetric label="Connected" value={ACCOUNTS.length} sub="All accounts" color="var(--tg-blue)"/>
          <MiniMetric label="Active" value={ACCOUNTS.filter(a => a.status === "active").length} sub="Sending now" color="var(--success)"/>
          <MiniMetric label="Warm-up" value={ACCOUNTS.filter(a => a.status === "warmup").length} sub="≤ 30 days" color="var(--warning)"/>
          <MiniMetric label="Paused" value={ACCOUNTS.filter(a => a.status === "paused").length} sub="Idle" color="var(--text-muted)"/>
          <MiniMetric label="Errors" value={ACCOUNTS.filter(a => a.status === "error").length} sub="Need attention" color="var(--danger)"/>
        </div>

        <div className="card" style={{ overflow: "hidden" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Account</th>
                <th>Status</th>
                <th>Campaign</th>
                <th>Today · ceiling</th>
                <th>This week</th>
                <th>Proxy</th>
                <th>Warm-up</th>
                <th>Health</th>
                <th style={{ width: 40 }}/>
              </tr>
            </thead>
            <tbody>
              {ACCOUNTS.map(a => (
                <tr key={a.id}>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <div style={{ position: "relative" }}>
                        <Avatar name={a.name}/>
                        <div style={{
                          position: "absolute", bottom: -1, right: -1, width: 11, height: 11, borderRadius: 50,
                          background: STATUS_STYLES[a.status]?.dot || "var(--text-faint)",
                          border: "2px solid white",
                        }}/>
                      </div>
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 500 }}>{a.name}</div>
                        <div className="muted text-xs">{a.username} · {a.phone}</div>
                      </div>
                    </div>
                  </td>
                  <td><StatusPill status={a.status}/></td>
                  <td>
                    {a.campaign !== "—"
                      ? <span style={{ fontSize: 12.5 }}>{a.campaign}</span>
                      : <span className="muted text-xs">—</span>}
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
                  <td><span className="pill">{a.proxy}</span></td>
                  <td>
                    <div style={{ minWidth: 90 }}>
                      <div className="text-xs muted" style={{ marginBottom: 3 }}>Day {a.warmupDay} / 30</div>
                      <div style={{ height: 4, background: "var(--bg-soft)", borderRadius: 999, overflow: "hidden" }}>
                        <div style={{ width: `${Math.min(a.warmupDay / 30, 1) * 100}%`, height: "100%", background: a.warmupDay >= 30 ? "var(--success)" : "var(--warning)" }}/>
                      </div>
                    </div>
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Donut value={a.health / 100} size={28} stroke={3.5} color={a.health > 85 ? "var(--success)" : a.health > 60 ? "var(--warning)" : "var(--danger)"}/>
                      <span className="num text-sm fw5">{a.health}</span>
                    </div>
                  </td>
                  <td>
                    <button className="tb__icon-btn" style={{ width: 28, height: 28 }}><Icon name="more" size={14}/></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 14 }}>
          <div className="card">
            <div className="card__header">
              <div>
                <div className="card__title">Rate corridor — fleet</div>
                <div className="card__sub">Total hourly volume vs 4 / 20 / 150 ceilings (8 senders)</div>
              </div>
            </div>
            <div style={{ padding: "16px 18px" }}>
              <BarChart
                data={[12,16,21,28,33,40,46,52,58,61,68,72,78,82,80,76,68,52,38,22,14,10,8,6]}
                width={600}
                color="var(--tg-blue)"
                labels={Array.from({length: 24}, (_, i) => i % 4 === 0 ? `${i}:00` : "")}
                height={100}
              />
              <div style={{ display: "flex", gap: 16, marginTop: 12, fontSize: 11, color: "var(--text-muted)" }}>
                <Legend swatch="var(--tg-blue)" label="Sent this hour"/>
                <span>Peak <b className="num">82/h</b> at 11:00</span>
                <span>Ceiling <b className="num">160/h</b></span>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="card__header">
              <div>
                <div className="card__title">Account events</div>
                <div className="card__sub">Last 24 hours</div>
              </div>
            </div>
            <div style={{ padding: "4px 0" }}>
              {[
                { who: "@hirot", what: "Session revoked by Telegram", color: "var(--danger)", icon: "alert_triangle", at: "1h ago" },
                { who: "@lina.v", what: "Warm-up day 9 of 30 · ceiling now 4/day", color: "var(--warning)", icon: "flag", at: "3h ago" },
                { who: "@anna_p", what: "Proxy rotated — DE residential", color: "var(--tg-blue)", icon: "globe", at: "5h ago" },
                { who: "@marco_r", what: "First reply received — Apr 28", color: "var(--success)", icon: "message_circle", at: "1d ago" },
                { who: "@yusuf.k", what: "Paused by Andrew", color: "var(--text-muted)", icon: "pause", at: "1d ago" },
              ].map((e, i) => (
                <div key={i} style={{ display: "flex", gap: 10, padding: "10px 18px", alignItems: "center" }}>
                  <div style={{ width: 26, height: 26, borderRadius: 8, background: `${e.color}15`, color: e.color, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <Icon name={e.icon} size={13}/>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5 }}><b>{e.who}</b> <span className="muted">{e.what}</span></div>
                  </div>
                  <span className="muted text-xs">{e.at}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {adding && <OnboardingFlow onClose={() => setAdding(false)}/>}
    </>
  );
}

// ============================================================
// Onboarding flow
// ============================================================
function OnboardingFlow({ onClose }) {
  const [step, setStep] = React.useState(0);
  const [method, setMethod] = React.useState("phone");
  const STEPS = ["Method", "Phone & code", "2FA / Proxy", "Warm-up"];

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(15,20,25,0.55)", backdropFilter: "blur(4px)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9999 }}>
      <div style={{ width: 540, background: "white", borderRadius: 18, boxShadow: "0 30px 80px rgba(0,0,0,0.3)", overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--divider)", display: "flex", alignItems: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Connect Telegram account</div>
          <span className="spacer"/>
          <button className="tb__icon-btn" style={{ width: 28, height: 28 }} onClick={onClose}><Icon name="x" size={14}/></button>
        </div>

        {/* Stepper */}
        <div style={{ padding: "16px 20px", display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--divider)" }}>
          {STEPS.map((s, i) => (
            <React.Fragment key={s}>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{
                  width: 22, height: 22, borderRadius: 50,
                  background: i < step ? "var(--success)" : i === step ? "var(--tg-blue)" : "var(--bg-soft)",
                  color: i <= step ? "white" : "var(--text-faint)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 10, fontWeight: 600,
                }}>
                  {i < step ? <Icon name="check" size={11}/> : i + 1}
                </div>
                <span style={{ fontSize: 12, color: i <= step ? "var(--text)" : "var(--text-muted)", fontWeight: i === step ? 500 : 400 }}>{s}</span>
              </div>
              {i < STEPS.length - 1 && <div style={{ flex: 1, height: 1, background: "var(--divider)" }}/>}
            </React.Fragment>
          ))}
        </div>

        <div style={{ padding: 22 }}>
          {step === 0 && (
            <div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <OBChoice on={method === "phone"} onClick={() => setMethod("phone")} icon="phone" label="Phone + SMS code" desc="Standard flow with 2FA fallback"/>
                <OBChoice on={method === "qr"} onClick={() => setMethod("qr")} icon="qr" label="QR scan" desc="Scan from Telegram mobile · faster"/>
              </div>
              <div style={{ marginTop: 16, padding: 12, background: "var(--bg-soft)", borderRadius: 10, fontSize: 12, color: "var(--text-soft)", display: "flex", gap: 10 }}>
                <Icon name="info" size={14} color="var(--tg-blue)"/>
                We use MTProto — your session stays encrypted, bound to the proxy you assign. We never store your password.
              </div>
            </div>
          )}
          {step === 1 && method === "phone" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <div className="field">
                <div className="field__label">Phone number</div>
                <input className="input" defaultValue="+1 415 ··· 2810" placeholder="+1 415 555 2810"/>
              </div>
              <div className="field">
                <div className="field__label">SMS / Telegram code</div>
                <div style={{ display: "flex", gap: 6 }}>
                  {["1","2","8","3","4"].map((d, i) => (
                    <input key={i} className="input" defaultValue={d} maxLength={1} style={{
                      width: 38, textAlign: "center", fontSize: 16, fontWeight: 600,
                    }}/>
                  ))}
                </div>
                <div className="field__hint">Code sent to your Telegram app · resend in 38s</div>
              </div>
            </div>
          )}
          {step === 1 && method === "qr" && (
            <div style={{ display: "flex", gap: 18, alignItems: "center" }}>
              <div style={{ width: 160, height: 160, background: "var(--bg-soft)", borderRadius: 14, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <QRPlaceholder/>
              </div>
              <div>
                <ol style={{ paddingLeft: 18, fontSize: 13, color: "var(--text-soft)", lineHeight: 1.7 }}>
                  <li>Open <b>Telegram</b> on your phone</li>
                  <li>Settings → Devices → <b>Link Desktop Device</b></li>
                  <li>Scan this code</li>
                </ol>
                <div className="muted text-xs" style={{ marginTop: 10 }}>Code expires in 1:48</div>
              </div>
            </div>
          )}
          {step === 2 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div className="field">
                <div className="field__label">Two-factor password (cloud)</div>
                <input className="input" type="password" defaultValue="········"/>
                <div className="field__hint">Required only if you enabled cloud password on this account.</div>
              </div>
              <div className="field">
                <div className="field__label">Assign proxy</div>
                <select className="select" defaultValue="de1">
                  <option value="de1">🇩🇪 DE residential · de-fra-04 · 12ms</option>
                  <option value="us1">🇺🇸 US residential · us-nyc-01 · 28ms</option>
                  <option value="nl1">🇳🇱 NL residential · nl-ams-02 · 18ms</option>
                  <option value="">+ Add new proxy</option>
                </select>
                <div className="field__hint">Bound 1-to-1 with this account. Rotated only with manual confirm.</div>
              </div>
            </div>
          )}
          {step === 3 && (
            <div>
              <div style={{ padding: 14, borderRadius: 11, background: "linear-gradient(135deg, var(--success-soft), var(--tg-blue-softer))", marginBottom: 14, display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: "var(--success)", color: "white", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Icon name="check" size={18}/>
                </div>
                <div>
                  <div style={{ fontWeight: 600 }}>@sophie_t connected</div>
                  <div className="muted text-xs">Bound to DE residential · de-fra-04</div>
                </div>
              </div>
              <div className="field">
                <div className="field__label">Warm-up curve</div>
                <div style={{ display: "flex", gap: 6, fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>
                  <span>Day 1</span><span className="spacer"/><span>Day 15</span><span className="spacer"/><span>Day 30 — full speed</span>
                </div>
                <div style={{ display: "flex", gap: 2, height: 22 }}>
                  {Array.from({ length: 30 }).map((_, i) => {
                    const h = Math.min((i + 1) / 30, 1);
                    return <div key={i} style={{ flex: 1, background: `linear-gradient(180deg, var(--tg-blue) ${100 - h * 100}%, var(--tg-blue-soft) ${100 - h * 100}%)`, opacity: 0.5 + h * 0.5, borderRadius: 2 }}/>;
                  })}
                </div>
                <div className="field__hint" style={{ marginTop: 8 }}>aimly ramps from 4 → 20 messages/day across 30 days. Greenfield accounts converge fastest.</div>
              </div>
            </div>
          )}
        </div>

        <div style={{ padding: "14px 20px", borderTop: "1px solid var(--divider)", display: "flex", justifyContent: "space-between" }}>
          <button className="btn btn--ghost" onClick={() => step === 0 ? onClose() : setStep(step - 1)}>
            {step === 0 ? "Cancel" : "Back"}
          </button>
          {step < STEPS.length - 1
            ? <button className="btn btn--primary" onClick={() => setStep(step + 1)}>Continue</button>
            : <button className="btn btn--primary" onClick={onClose}>Finish — start warm-up</button>}
        </div>
      </div>
    </div>
  );
}

function OBChoice({ on, onClick, icon, label, desc }) {
  return (
    <button onClick={onClick} style={{
      padding: 16, borderRadius: 12, textAlign: "left", display: "flex", flexDirection: "column", gap: 8,
      border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
      background: on ? "var(--tg-blue-softer)" : "white",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 32, height: 32, borderRadius: 9, background: on ? "var(--tg-blue)" : "var(--bg-soft)", color: on ? "white" : "var(--text-muted)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name={icon} size={16}/>
        </div>
        <div style={{ fontWeight: 600, fontSize: 13.5 }}>{label}</div>
        {on && <div style={{ marginLeft: "auto", color: "var(--tg-blue)" }}><Icon name="check" size={16}/></div>}
      </div>
      <div className="muted text-sm">{desc}</div>
    </button>
  );
}

function QRPlaceholder() {
  // Simple grid-style QR look
  const cells = 21;
  const seed = "pulse-tg-2026";
  const grid = [];
  for (let i = 0; i < cells * cells; i++) {
    const c = seed.charCodeAt(i % seed.length) ^ i;
    grid.push((c * 31 + i * 7) % 3 === 0);
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${cells}, 1fr)`, width: 130, height: 130, gap: 1, background: "white", padding: 6, borderRadius: 8 }}>
      {grid.map((on, i) => {
        // Corner finders (top-left, top-right, bottom-left)
        const r = Math.floor(i / cells), col = i % cells;
        const inFinder = (rr, cc) => r >= rr && r < rr + 7 && col >= cc && col < cc + 7;
        const finder = inFinder(0, 0) || inFinder(0, cells - 7) || inFinder(cells - 7, 0);
        let dark = on;
        if (finder) {
          const lr = r >= cells - 7 ? r - (cells - 7) : r;
          const lc = col >= cells - 7 ? col - (cells - 7) : col;
          dark = lr === 0 || lr === 6 || lc === 0 || lc === 6 || (lr >= 2 && lr <= 4 && lc >= 2 && lc <= 4);
        }
        return <div key={i} style={{ background: dark ? "#0f1419" : "transparent" }}/>;
      })}
    </div>
  );
}

Object.assign(window, { AccountsScreen });
