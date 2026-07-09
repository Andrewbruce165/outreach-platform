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
  GripVertical,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
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

const ORDER_STORAGE_KEY = "aimly:sidebar:nav-order:v1";

function loadOrder(): string[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ORDER_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : null;
  } catch {
    return null;
  }
}

function applyOrder(items: NavItem[], order: string[] | null): NavItem[] {
  if (!order || order.length === 0) return items;
  const byTo = new Map(items.map((i) => [i.to, i]));
  const result: NavItem[] = [];
  for (const to of order) {
    const it = byTo.get(to);
    if (it) {
      result.push(it);
      byTo.delete(to);
    }
  }
  // append any new items not present in saved order
  for (const remaining of byTo.values()) result.push(remaining);
  return result;
}

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

  const [items, setItems] = useState<NavItem[]>(() => applyOrder(NAV_ITEMS, loadOrder()));
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);
  const draggingRef = useRef<string | null>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        ORDER_STORAGE_KEY,
        JSON.stringify(items.map((i) => i.to)),
      );
    } catch {
      /* ignore */
    }
  }, [items]);

  const onDragStart = (e: React.DragEvent, to: string) => {
    draggingRef.current = to;
    setDragKey(to);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", to);
  };
  const onDragOver = (e: React.DragEvent, to: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (overKey !== to) setOverKey(to);
  };
  const onDrop = (e: React.DragEvent, targetTo: string) => {
    e.preventDefault();
    const sourceTo = draggingRef.current ?? e.dataTransfer.getData("text/plain");
    draggingRef.current = null;
    setDragKey(null);
    setOverKey(null);
    if (!sourceTo || sourceTo === targetTo) return;
    setItems((prev) => {
      const next = [...prev];
      const fromIdx = next.findIndex((i) => i.to === sourceTo);
      const toIdx = next.findIndex((i) => i.to === targetTo);
      if (fromIdx === -1 || toIdx === -1) return prev;
      const [moved] = next.splice(fromIdx, 1);
      next.splice(toIdx, 0, moved);
      return next;
    });
  };
  const onDragEnd = () => {
    draggingRef.current = null;
    setDragKey(null);
    setOverKey(null);
  };

  const renderDraggable = (item: NavItem) => {
    const Icon = item.icon;
    const active = isActive(item.to);
    const isDragging = dragKey === item.to;
    const isOver = overKey === item.to && dragKey && dragKey !== item.to;
    return (
      <div
        key={item.to}
        draggable
        onDragStart={(e) => onDragStart(e, item.to)}
        onDragOver={(e) => onDragOver(e, item.to)}
        onDrop={(e) => onDrop(e, item.to)}
        onDragEnd={onDragEnd}
        style={{
          opacity: isDragging ? 0.4 : 1,
          borderTop: isOver ? "2px solid var(--tg-blue, #3390ec)" : "2px solid transparent",
          cursor: "grab",
        }}
      >
        <Link to={item.to} className={`sb__item ${active ? "is-active" : ""}`}>
          <GripVertical
            size={12}
            style={{ opacity: 0.35, marginRight: -4, cursor: "grab" }}
            aria-hidden
          />
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
      </div>
    );
  };

  const renderStatic = (item: NavItem) => {
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
        {items.map(renderDraggable)}
        <div className="sb__section">Workspace</div>
        {SECONDARY.map(renderStatic)}
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
