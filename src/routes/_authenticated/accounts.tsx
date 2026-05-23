import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  Plus,
  MoreHorizontal,
  Pause,
  Play,
  RefreshCcw,
  Trash2,
  Activity,
  X,
  AlertCircle,
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

  return (
    <>
      <Topbar
        title="Telegram accounts"
        right={
          <button className="btn btn--primary btn--sm" onClick={() => setModal({ mode: "new" })}>
            <Plus size={14} /> Add account
          </button>
        }
      />
      <div className="scroll" style={{ flex: 1, padding: 24 }}>
        <FleetTable
          senders={data?.senders ?? []}
          isLoading={isLoading}
          errorMsg={error instanceof ApiError ? error.message : null}
          onEmpty={() => setModal({ mode: "new" })}
          onReauth={(s) => setModal({ mode: "reauth", phone: s.phone, slug: s.slug })}
        />
      </div>

      {modal && (
        <Modal onClose={() => setModal(null)} title={modal.mode === "reauth" ? "Re-authenticate account" : "Connect a Telegram account"}>
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
      <div className="card"><div className="card__body muted">Loading accounts…</div></div>
    );
  }
  if (errorMsg) {
    return (
      <div className="card"><div className="card__body" style={{ color: "var(--danger)" }}>
        <AlertCircle size={14} /> {errorMsg}
      </div></div>
    );
  }
  if (senders.length === 0) {
    return (
      <div className="card">
        <div className="card__body" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div style={{
            margin: "0 auto 16px",
            width: 56, height: 56, borderRadius: 28,
            background: "var(--tg-blue-soft)", display: "grid", placeItems: "center",
            color: "var(--tg-blue)",
          }}>
            <Activity size={24} />
          </div>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>No accounts yet</h3>
          <p className="muted" style={{ fontSize: 13, marginBottom: 16, maxWidth: 360, margin: "0 auto 16px" }}>
            Connect a Telegram phone number so aimly can send messages. We warm new accounts up automatically.
          </p>
          <button className="btn btn--primary" onClick={onEmpty}>
            <Plus size={14} /> Connect first account
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <table className="tbl">
        <thead>
          <tr>
            <th>Account</th>
            <th>Status</th>
            <th>Today</th>
            <th>Limits (min · hr · day)</th>
            <th>Last used</th>
            <th aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {senders.map((s) => <SenderRow key={s.id} sender={s} onReauth={() => onReauth(s)} />)}
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

  const statusPill =
    sender.status === "active"
      ? "pill--green"
      : sender.status === "warmup"
        ? "pill--blue"
        : sender.status === "paused"
          ? "pill--ghost"
          : "pill--red";

  const lastUsed = sender.last_used_at ? relativeTime(sender.last_used_at) : "—";

  return (
    <tr>
      <td>
        <div className="row">
          <div className="avatar avatar--sm" style={{ background: "var(--tg-blue-soft)", color: "var(--tg-blue)" }}>
            {(sender.name || sender.phone).slice(0, 1).toUpperCase()}
          </div>
          <div className="col" style={{ gap: 2 }}>
            <span className="fw5">{sender.name || sender.phone}</span>
            <span className="muted text-xs mono">{sender.phone}</span>
          </div>
        </div>
      </td>
      <td>
        <span className={`pill ${statusPill}`}>
          <span className="pill__dot" /> {sender.status}
        </span>
        {sender.auth_status !== "ok" && (
          <button className="ob__link" style={{ marginLeft: 8 }} onClick={onReauth}>
            re-auth
          </button>
        )}
      </td>
      <td className="num muted">—</td>
      <td className="num mono text-sm">
        {sender.rate_limits.per_minute} · {sender.rate_limits.per_hour} · {sender.rate_limits.per_day}
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
                <button onClick={() => { setOpen(false); action.mutate("resume"); }}>
                  <Play size={13} /> Resume
                </button>
              ) : (
                <button onClick={() => { setOpen(false); action.mutate("pause"); }}>
                  <Pause size={13} /> Pause
                </button>
              )}
              <button onClick={() => { setOpen(false); onReauth(); }}>
                <RefreshCcw size={13} /> Re-authenticate
              </button>
              <button
                className="is-danger"
                onClick={() => {
                  setOpen(false);
                  if (confirm(`Delete ${sender.name || sender.phone}? This stops any campaign using it.`)) {
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

function Modal({ children, onClose, title }: { children: React.ReactNode; onClose: () => void; title: string }) {
  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
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
