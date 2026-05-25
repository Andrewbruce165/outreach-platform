import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  Plus,
  MoreHorizontal,
  RefreshCcw,
  Trash2,
  Pencil,
  Activity,
  X,
  AlertCircle,
  Filter,
  ShieldCheck,
  Phone as PhoneIcon,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { OnboardingFlow } from "@/components/OnboardingFlow";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";

type Sender = components["schemas"]["SenderResponse"];

export const Route = createFileRoute("/_authenticated/accounts")({
  component: AccountsPage,
});

function AccountsPage() {
  const [modal, setModal] = useState<null | { mode: "new" | "reauth"; phone?: string; slug?: string }>(
    null,
  );
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
    refetchInterval: 15000,
  });

  const senders = data?.senders ?? [];
  const counts = {
    total: senders.length,
    active: senders.filter((s) => s.status === "active").length,
    warmup: senders.filter((s) => s.status === "warmup").length,
    paused: senders.filter((s) => s.status === "paused").length,
    error: senders.filter((s) => s.status === "error").length,
  };

  return (
    <>
      <Topbar
        title="Telegram accounts"
        right={
          <>
            <button className="btn btn--ghost btn--sm" type="button">
              <Filter size={14} /> Filters
            </button>
            <button className="btn btn--primary btn--sm" onClick={() => setModal({ mode: "new" })}>
              <Plus size={14} /> Connect account
            </button>
          </>
        }
      />
      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        {senders.length > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(5, 1fr)",
              gap: 12,
              marginBottom: 16,
            }}
          >
            <MiniMetric label="Connected" value={counts.total} sub="All accounts" color="var(--tg-blue)" />
            <MiniMetric label="Active" value={counts.active} sub="Sending now" color="var(--success)" />
            <MiniMetric label="Warm-up" value={counts.warmup} sub="≤ 30 days" color="var(--warning)" />
            <MiniMetric label="Paused" value={counts.paused} sub="Idle" color="var(--text-muted)" />
            <MiniMetric label="Errors" value={counts.error} sub="Need attention" color="var(--danger)" />
          </div>
        )}

        <FleetTable
          senders={senders}
          isLoading={isLoading}
          errorMsg={error instanceof ApiError ? error.message : null}
          onEmpty={() => setModal({ mode: "new" })}
          onReauth={(s) => setModal({ mode: "reauth", phone: s.phone, slug: s.slug })}
        />
      </div>

      {modal && (
        <Modal
          onClose={() => setModal(null)}
          title={modal.mode === "reauth" ? "Re-authenticate account" : "Connect a Telegram account"}
        >
          <OnboardingFlow
            compact
            initialPhone={modal.phone}
            onComplete={() => {
              void qc.invalidateQueries({ queryKey: ["senders"] });
              setTimeout(() => setModal(null), 1400);
            }}
          />
        </Modal>
      )}
    </>
  );
}

function MiniMetric({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: number;
  sub: string;
  color: string;
}) {
  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="muted text-xs" style={{ fontWeight: 500, letterSpacing: 0.2 }}>
        {label}
      </span>
      <span className="num" style={{ fontSize: 24, fontWeight: 600, color, lineHeight: 1.1 }}>
        {value}
      </span>
      <span className="muted text-xs">{sub}</span>
    </div>
  );
}

function FleetTable({
  senders,
  isLoading,
  errorMsg,
  onEmpty,
  onReauth,
}: {
  senders: Sender[];
  isLoading: boolean;
  errorMsg: string | null;
  onEmpty: () => void;
  onReauth: (s: Sender) => void;
}) {
  if (isLoading) {
    return (
      <div className="card">
        <div className="card__body muted">Loading accounts…</div>
      </div>
    );
  }
  if (errorMsg) {
    return (
      <div className="card">
        <div className="card__body" style={{ color: "var(--danger)" }}>
          <AlertCircle size={14} /> {errorMsg}
        </div>
      </div>
    );
  }
  if (senders.length === 0) {
    return (
      <div className="card">
        <div className="card__body" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div
            style={{
              margin: "0 auto 16px",
              width: 56,
              height: 56,
              borderRadius: 28,
              background: "var(--tg-blue-soft)",
              display: "grid",
              placeItems: "center",
              color: "var(--tg-blue)",
            }}
          >
            <Activity size={24} />
          </div>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>No accounts yet</h3>
          <p
            className="muted"
            style={{ fontSize: 13, marginBottom: 16, maxWidth: 360, margin: "0 auto 16px" }}
          >
            Connect a Telegram phone number so aimly can send messages. We warm new accounts up
            automatically.
          </p>
          <button className="btn btn--primary" onClick={onEmpty}>
            <Plus size={14} /> Connect first account
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <table className="tbl">
        <thead>
          <tr>
            <th>Account</th>
            <th>Status</th>
            <th>Role</th>
            <th>Today · ceiling</th>
            <th>Limits (min · hr · day)</th>
            <th>Proxy</th>
            <th>Last used</th>
            <th style={{ width: 40 }} aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {senders.map((s) => (
            <SenderRow key={s.id} sender={s} onReauth={() => onReauth(s)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SenderRow({ sender, onReauth }: { sender: Sender; onReauth: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const action = useMutation({
    mutationFn: async (kind: "pause" | "resume" | "delete") => {
      if (kind === "delete") {
        await api(`/api/v1/senders/${sender.slug}`, { method: "DELETE" });
      } else {
        await api(`/api/v1/senders/${sender.slug}/${kind}`, { method: "POST" });
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["senders"] }),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Action failed"),
  });

  const statusStyle: Record<string, { pill: string; dot: string }> = {
    active: { pill: "pill--green", dot: "var(--success)" },
    warmup: { pill: "pill--blue", dot: "var(--tg-blue)" },
    paused: { pill: "pill--ghost", dot: "var(--text-muted)" },
    error: { pill: "pill--red", dot: "var(--danger)" },
  };
  const sty = statusStyle[sender.status] ?? statusStyle.paused;

  const lastUsed = sender.last_used_at ? relativeTime(sender.last_used_at) : "—";
  const isChecker = sender.role === "checker";
  const dailyLimit = sender.rate_limits.per_day;

  return (
    <tr>
      <td>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ position: "relative" }}>
            <div
              className="avatar avatar--sm"
              style={{ background: "var(--tg-blue-soft)", color: "var(--tg-blue)" }}
            >
              {(sender.name || sender.phone).slice(0, 1).toUpperCase()}
            </div>
            <div
              aria-hidden
              style={{
                position: "absolute",
                bottom: -1,
                right: -1,
                width: 11,
                height: 11,
                borderRadius: 50,
                background: sty.dot,
                border: "2px solid var(--bg)",
              }}
            />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="fw5">{sender.name || sender.phone}</span>
            <span className="muted text-xs mono">{sender.phone}</span>
          </div>
        </div>
      </td>
      <td>
        <span className={`pill ${sty.pill}`}>
          <span className="pill__dot" /> {sender.status}
        </span>
        {sender.auth_status !== "ok" && (
          <button className="ob__link" style={{ marginLeft: 8 }} onClick={onReauth}>
            re-auth
          </button>
        )}
      </td>
      <td>
        <span
          className={`pill ${isChecker ? "pill--purple" : "pill--ghost"}`}
          title={isChecker ? "Verifies phone numbers on Telegram" : "Sends outreach messages"}
        >
          {isChecker ? <ShieldCheck size={11} /> : <PhoneIcon size={11} />}
          {isChecker ? "Checker" : "Sender"}
        </span>
      </td>
      <td>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 120 }}>
          <span className="num text-xs muted">— / {dailyLimit}</span>
          <CorridorBar value={0} limit={dailyLimit} />
        </div>
      </td>
      <td className="num mono text-sm">
        {sender.rate_limits.per_minute} · {sender.rate_limits.per_hour} · {sender.rate_limits.per_day}
      </td>
      <td>
        <span className="pill pill--ghost">
          {sender.proxy ? proxyLabel(sender.proxy) : "Direct"}
        </span>
      </td>
      <td className="muted text-sm">{lastUsed}</td>
      <td style={{ textAlign: "right", position: "relative" }}>
        <button className="tb__icon-btn" aria-label="Actions" onClick={() => setOpen((v) => !v)}>
          <MoreHorizontal size={16} />
        </button>
        {open && (
          <>
            <div className="ob__menuScrim" onClick={() => setOpen(false)} />
            <div className="ob__menu" role="menu">
              {sender.status === "paused" ? (
                <button
                  onClick={() => {
                    setOpen(false);
                    action.mutate("resume");
                  }}
                >
                  <Play size={13} /> Resume
                </button>
              ) : (
                <button
                  onClick={() => {
                    setOpen(false);
                    action.mutate("pause");
                  }}
                >
                  <Pause size={13} /> Pause
                </button>
              )}
              <button
                onClick={() => {
                  setOpen(false);
                  onReauth();
                }}
              >
                <RefreshCcw size={13} /> Re-authenticate
              </button>
              <button
                className="is-danger"
                onClick={() => {
                  setOpen(false);
                  if (
                    confirm(
                      `Delete ${sender.name || sender.phone}? This stops any campaign using it.`,
                    )
                  ) {
                    action.mutate("delete");
                  }
                }}
              >
                <Trash2 size={13} /> Delete
              </button>
            </div>
          </>
        )}
      </td>
    </tr>
  );
}

function CorridorBar({ value, limit }: { value: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (value / limit) * 100) : 0;
  const color =
    pct > 90 ? "var(--danger)" : pct > 70 ? "var(--warning)" : "var(--tg-blue)";
  return (
    <div
      style={{
        height: 4,
        background: "var(--bg-soft)",
        borderRadius: 999,
        overflow: "hidden",
      }}
    >
      <div style={{ width: `${pct}%`, height: "100%", background: color }} />
    </div>
  );
}

function proxyLabel(proxy: NonNullable<Sender["proxy"]>): string {
  const p = proxy as { host?: string; type?: string };
  if (p.host) return p.host;
  if (p.type) return p.type;
  return "Proxy";
}

function Modal({
  children,
  onClose,
  title,
}: {
  children: React.ReactNode;
  onClose: () => void;
  title: string;
}) {
  return (
    <div
      className="modal__scrim"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal__head">
          <h3>{title}</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <div className="modal__body">{children}</div>
      </div>
    </div>
  );
}

function relativeTime(iso: string): string {
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
