import { Link, useLocation } from "@tanstack/react-router";
import {
  LayoutDashboard,
  Megaphone,
  Inbox,
  Bot,
  Library,
  Users,
  Smartphone,
  Flame,
  Settings,
  HelpCircle,
  ChevronDown,
  Search,
} from "lucide-react";
import { PulseLogo } from "./PulseLogo";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  badge?: number;
  badgeKind?: "primary" | "muted";
  dot?: "warning" | "success";
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone },
  { to: "/inbox", label: "Inbox", icon: Inbox, badgeKind: "primary" },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/knowledge-bases", label: "Knowledge bases", icon: Library },
  { to: "/contacts", label: "Contacts", icon: Users },
  { to: "/accounts", label: "TG accounts", icon: Smartphone },
  { to: "/warmup", label: "Warmup", icon: Flame },
];

const SECONDARY: NavItem[] = [
  { to: "/settings", label: "Settings", icon: Settings },
];

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((n) => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

interface Props {
  workspaceName?: string;
  workspaceUser?: string;
  plan?: string;
}

export function AppSidebar({
  workspaceName = "Workspace",
  workspaceUser = "Owner",
  plan = "Free",
}: Props) {
  const location = useLocation();
  const isActive = (to: string) =>
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);

  const renderItem = (item: NavItem) => {
    const Icon = item.icon;
    const active = isActive(item.to);
    return (
      <Link
        key={item.to}
        to={item.to}
        className={`sb__item ${active ? "is-active" : ""}`}
      >
        <Icon size={18} />
        <span>{item.label}</span>
        {item.badge !== undefined && (
          <span className={`badge ${item.badgeKind === "primary" ? "" : "is-muted"}`}>
            {item.badge}
          </span>
        )}
        {item.dot && (
          <span
            className="dot"
            style={{
              background:
                item.dot === "warning" ? "var(--warning)" : "var(--success)",
            }}
          />
        )}
      </Link>
    );
  };

  return (
    <aside className="app__sidebar">
      <div className="sb__brand">
        <div className="sb__logo">
          <PulseLogo />
        </div>
        <div>
          <div className="sb__brand-name">aimly</div>
          <div className="sb__brand-plan">{plan}</div>
        </div>
        <button
          style={{ marginLeft: "auto", padding: 6, color: "var(--text-muted)" }}
          aria-label="Switch workspace (v2)"
          disabled
        >
          <ChevronDown size={16} />
        </button>
      </div>

      <div className="sb__search" title="Command palette (v2)">
        <Search size={14} />
        <span>Search</span>
        <kbd>⌘ K</kbd>
      </div>

      <div className="sb__nav scroll" style={{ flex: 1 }}>
        {NAV_ITEMS.map(renderItem)}
        <div className="sb__section">Workspace</div>
        {SECONDARY.map(renderItem)}
        <a
          className="sb__item"
          href="https://docs.aimly.com"
          target="_blank"
          rel="noreferrer"
        >
          <HelpCircle size={18} />
          <span>Help &amp; docs</span>
        </a>
      </div>

      <div className="sb__workspace">
        <div className="sb__workspace-av">{initials(workspaceName)}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="sb__workspace-name">{workspaceName}</div>
          <div className="sb__workspace-role">{workspaceUser}</div>
        </div>
        <button style={{ padding: 4, color: "var(--text-muted)" }} aria-label="Account menu">
          <ChevronDown size={14} />
        </button>
      </div>
    </aside>
  );
}
