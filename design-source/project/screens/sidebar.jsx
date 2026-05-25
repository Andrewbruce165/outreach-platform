// sidebar.jsx — left navigation
const NAV_ITEMS = [
{ id: "dashboard", label: "Dashboard", icon: "dashboard" },
{ id: "campaigns", label: "Campaigns", icon: "campaigns", badge: 5 },
{ id: "inbox", label: "Inbox", icon: "inbox", badge: 6, badgeKind: "primary" },
{ id: "agents", label: "Agents", icon: "agents" },
{ id: "contacts", label: "Contacts", icon: "contacts" },
{ id: "accounts", label: "TG accounts", icon: "accounts", dot: "warning" }];


const SECONDARY = [
{ id: "settings", label: "Settings", icon: "settings" },
{ id: "help", label: "Help & docs", icon: "help" }];


function PulseLogo({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M3 12h3l2-6 4 12 3-9 2 6h4" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>);

}

function Sidebar({ active, onNavigate }) {
  return (
    <aside className="app__sidebar">
      <div className="sb__brand">
        <div className="sb__logo">
          <PulseLogo />
        </div>
        <div>
          <div className="sb__brand-name">Aimly</div>
          <div className="sb__brand-plan">{WORKSPACE.plan}</div>
        </div>
        <button style={{ marginLeft: "auto", padding: 6, color: "var(--text-muted)" }}>
          <Icon name="chevron_down" size={16} />
        </button>
      </div>

      <div className="sb__search">
        <Icon name="search" size={14} />
        <span>Search</span>
        <kbd>⌘ K</kbd>
      </div>

      <div className="sb__nav scroll" style={{ flex: 1 }}>
        {NAV_ITEMS.map((item) =>
        <div
          key={item.id}
          className={`sb__item ${active === item.id ? "is-active" : ""}`}
          onClick={() => onNavigate(item.id)}>
          
            <Icon name={item.icon} size={18} />
            <span>{item.label}</span>
            {item.badge &&
          <span className={`badge ${item.badgeKind === "primary" ? "" : "is-muted"}`}>{item.badge}</span>
          }
            {item.dot &&
          <span className="dot" style={{ background: item.dot === "warning" ? "var(--warning)" : "var(--success)" }} />
          }
          </div>
        )}

        <div className="sb__section">Workspace</div>
        {SECONDARY.map((item) =>
        <div
          key={item.id}
          className={`sb__item ${active === item.id ? "is-active" : ""}`}
          onClick={() => onNavigate(item.id)}>
          
            <Icon name={item.icon} size={18} />
            <span>{item.label}</span>
          </div>
        )}
      </div>

      <div className="sb__workspace">
        <div className="sb__workspace-av">{initials(WORKSPACE.name)}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="sb__workspace-name">{WORKSPACE.name}</div>
          <div className="sb__workspace-role">{WORKSPACE.user}</div>
        </div>
        <button style={{ padding: 4, color: "var(--text-muted)" }}>
          <Icon name="chevron_down" size={14} />
        </button>
      </div>
    </aside>);

}

Object.assign(window, { Sidebar, PulseLogo });