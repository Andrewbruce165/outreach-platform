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
  History,
  Phone as PhoneIcon,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { OnboardingFlow } from "@/components/OnboardingFlow";
import { api, ApiError } from "@/lib/api";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { components } from "@/types/api";

type Sender = components["schemas"]["SenderResponse"];
type RestrictionEvent = components["schemas"]["RestrictionEventResponse"];

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
    restricted: senders.filter((s) => s.status === "limited" || s.status === "frozen").length,
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
              gridTemplateColumns: "repeat(6, 1fr)",
              gap: 12,
              marginBottom: 16,
            }}
          >
            <MiniMetric label="Connected" value={counts.total} sub="All accounts" color="var(--tg-blue)" />
            <MiniMetric label="Active" value={counts.active} sub="Sending now" color="var(--success)" />
            <MiniMetric label="Warm-up" value={counts.warmup} sub="≤ 30 days" color="var(--warning)" />
            <MiniMetric label="Paused" value={counts.paused} sub="Idle" color="var(--text-muted)" />
            <MiniMetric label="Restricted" value={counts.restricted} sub="Spam-limit / frozen" color="var(--orange, var(--warning))" />
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
    <TooltipProvider delayDuration={150}>
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
    </TooltipProvider>
  );
}

function SenderRow({ sender, onReauth }: { sender: Sender; onReauth: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const deleteMut = useMutation({
    mutationFn: () => api(`/api/v1/senders/${sender.slug}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Account deleted");
      qc.invalidateQueries({ queryKey: ["senders"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });

  const refreshMut = useMutation({
    mutationFn: () => api<Sender>(`/api/v1/senders/${sender.slug}`),
    onSuccess: (fresh) => {
      qc.setQueryData<{ senders: Sender[] }>(["senders"], (prev) =>
        prev
          ? { senders: prev.senders.map((x) => (x.slug === sender.slug ? fresh : x)) }
          : prev,
      );
      toast.success(`Status: ${fresh.status}`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Refresh failed"),
  });

  const statusStyle: Record<string, { pill: string; dot: string }> = {
    active: { pill: "pill--green", dot: "var(--success)" },
    warmup: { pill: "pill--blue", dot: "var(--tg-blue)" },
    paused: { pill: "pill--ghost", dot: "var(--text-muted)" },
    error: { pill: "pill--red", dot: "var(--danger)" },
    limited: { pill: "pill--orange", dot: "var(--warning)" },
    frozen: { pill: "pill--red", dot: "var(--danger)" },
  };
  const statusLabel: Record<string, string> = {
    active: "Active",
    warmup: "Warm-up",
    paused: "Paused",
    error: "Error",
    limited: "Spam-limited",
    frozen: "Frozen",
  };
  const sty = statusStyle[sender.status] ?? statusStyle.paused;
  const isRestricted = sender.status === "limited" || sender.status === "frozen";
  const restrictedUntil =
    isRestricted && sender.restricted_until
      ? new Date(sender.restricted_until).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : null;

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
          <span className="pill__dot" /> {statusLabel[sender.status] ?? sender.status}
        </span>
        {sender.auth_status !== "ok" && (
          <button className="ob__link" style={{ marginLeft: 8 }} onClick={onReauth}>
            re-auth
          </button>
        )}
        {isRestricted && (
          <div className="muted text-xs" style={{ marginTop: 4 }}>
            {restrictedUntil ? `Not sending · rechecks ${restrictedUntil}` : "Not sending until cleared"}
          </div>
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
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                minWidth: 120,
                cursor: "help",
              }}
            >
              <span className="num text-xs muted">{sender.sent_today ?? 0} / {dailyLimit}</span>
              <CorridorBar value={sender.sent_today ?? 0} limit={dailyLimit} />
            </div>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-[260px] text-left leading-relaxed">
            {isChecker
              ? `Overall message limit — ${dailyLimit}/day (incl. follow-ups).`
              : `Overall message limit — ${dailyLimit}/day (incl. follow-ups). New-contact outreach is capped separately at 50 new dialogs/day per account.`}
          </TooltipContent>
        </Tooltip>
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
              <button
                onClick={() => {
                  setOpen(false);
                  setEditing(true);
                }}
              >
                <Pencil size={13} /> Изменить
              </button>
              <button
                disabled={refreshMut.isPending}
                onClick={() => {
                  setOpen(false);
                  refreshMut.mutate();
                }}
              >
                <RefreshCcw size={13} /> Обновить статус
              </button>
              <button
                onClick={() => {
                  setOpen(false);
                  setHistoryOpen(true);
                }}
              >
                <History size={13} /> История ограничений
              </button>
              <button
                className="is-danger"
                disabled={deleteMut.isPending}
                onClick={() => {
                  setOpen(false);
                  if (
                    confirm(
                      `Удалить ${sender.name || sender.phone}? Это остановит все кампании, где он используется.`,
                    )
                  ) {
                    deleteMut.mutate();
                  }
                }}
              >
                <Trash2 size={13} /> Удалить
              </button>
            </div>
          </>
        )}
        {editing && (
          <EditSenderModal sender={sender} onClose={() => setEditing(false)} />
        )}
        {historyOpen && (
          <RestrictionHistoryModal
            sender={sender}
            onClose={() => setHistoryOpen(false)}
          />
        )}
      </td>
    </tr>
  );
}

/**
 * POOLV-04: mini restriction-event list for one account. Reads the workspace-scoped,
 * newest-first history from GET /senders/{slug}/restriction-events (HLTH-03). A
 * freeze→extension→clear sequence reads as a clean chronology (D-01: no per-tick noise).
 */
const EVENT_META: Record<string, { label: string; pill: string; dot: string }> = {
  spam_limited: { label: "Спам-лимит", pill: "pill--orange", dot: "var(--warning)" },
  frozen: { label: "Заморожен", pill: "pill--red", dot: "var(--danger)" },
  flood_wait: { label: "Flood-wait", pill: "pill--ghost", dot: "var(--text-muted)" },
  extension: { label: "Продление", pill: "pill--orange", dot: "var(--warning)" },
  cleared: { label: "Снято", pill: "pill--green", dot: "var(--success)" },
  banned: { label: "Бан", pill: "pill--red", dot: "var(--danger)" },
  privacy_restricted: { label: "Приватность получателя", pill: "pill--ghost", dot: "var(--text-muted)" },
};

const SOURCE_LABEL: Record<string, string> = {
  queue_error: "очередь",
  spambot_reconcile: "@SpamBot",
  antispam_signal: "антиспам-сигнал",
};

function eventTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function untilTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** One-line activity-slice summary, e.g. "12 отпр./ч · 138 уник. контактов/24ч". */
function sliceSummary(slice: RestrictionEvent["activity_slice"]): string | null {
  if (!slice || typeof slice !== "object") return null;
  const s = slice as Record<string, unknown>;
  const parts: string[] = [];
  if (typeof s.sends_1h === "number") parts.push(`${s.sends_1h} отпр./ч`);
  if (typeof s.sends_24h === "number") parts.push(`${s.sends_24h} отпр./24ч`);
  if (typeof s.unique_contacts_24h === "number")
    parts.push(`${s.unique_contacts_24h} уник. контактов/24ч`);
  return parts.length ? parts.join(" · ") : null;
}

function RestrictionHistoryModal({
  sender,
  onClose,
}: {
  sender: Sender;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["restriction-events", sender.slug],
    queryFn: () =>
      api<RestrictionEvent[]>(
        `/api/v1/senders/${sender.slug}/restriction-events`,
      ),
  });

  const events = data ?? [];

  return (
    <Modal
      title={`История ограничений · ${sender.name || sender.phone}`}
      onClose={onClose}
    >
      {isLoading && <div className="muted text-sm">Загрузка истории…</div>}
      {error && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          <AlertCircle size={14} />{" "}
          {error instanceof ApiError ? error.message : "Не удалось загрузить историю"}
        </div>
      )}
      {!isLoading && !error && events.length === 0 && (
        <div className="muted text-sm" style={{ padding: "8px 0" }}>
          Ограничений по этому аккаунту ещё не было.
        </div>
      )}
      {!isLoading && !error && events.length > 0 && (
        <ul
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            margin: 0,
            padding: 0,
            listStyle: "none",
            maxHeight: 420,
            overflowY: "auto",
          }}
        >
          {events.map((ev) => {
            const meta = EVENT_META[ev.event_type] ?? {
              label: ev.event_type,
              pill: "pill--ghost",
              dot: "var(--text-muted)",
            };
            const until = untilTime(ev.restricted_until);
            const summary = sliceSummary(ev.activity_slice);
            return (
              <li
                key={ev.id}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  padding: "10px 12px",
                  background: "var(--bg-soft)",
                  borderRadius: 8,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    flexWrap: "wrap",
                  }}
                >
                  <span className={`pill ${meta.pill}`} style={{ fontSize: 11 }}>
                    <span className="pill__dot" style={{ background: meta.dot }} />
                    {meta.label}
                  </span>
                  <span className="muted text-xs">
                    {SOURCE_LABEL[ev.source] ?? ev.source}
                  </span>
                  <span className="muted text-xs" style={{ marginLeft: "auto" }}>
                    {eventTime(ev.created_at)}
                  </span>
                </div>
                {until && (
                  <div className="muted text-xs">
                    Ограничение до проверки в {until}
                  </div>
                )}
                {summary && (
                  <div className="muted text-xs mono">{summary}</div>
                )}
                {ev.raw_text && (
                  <div
                    className="muted text-xs"
                    style={{
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      opacity: 0.85,
                    }}
                  >
                    {ev.raw_text}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Modal>
  );
}

function EditSenderModal({ sender, onClose }: { sender: Sender; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(sender.name ?? "");
  const [role, setRole] = useState<"sender" | "checker">(
    sender.role === "checker" ? "checker" : "sender",
  );

  const mut = useMutation({
    mutationFn: () =>
      api(`/api/v1/senders/${sender.slug}`, {
        method: "PATCH",
        body: { name: name.trim() || null, role },
      }),
    onSuccess: () => {
      toast.success("Сохранено");
      qc.invalidateQueries({ queryKey: ["senders"] });
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });

  return (
    <Modal title="Изменить аккаунт" onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <label className="muted text-xs" style={{ display: "block", marginBottom: 6 }}>
            Имя
          </label>
          <input
            className="ob__input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={sender.phone}
            style={{ width: "100%" }}
          />
        </div>
        <div>
          <label className="muted text-xs" style={{ display: "block", marginBottom: 6 }}>
            Роль
          </label>
          <div className="ob__roles">
            <button
              type="button"
              className={`ob__role ${role === "sender" ? "is-active" : ""}`}
              onClick={() => setRole("sender")}
            >
              <span className="ob__roleTitle">
                <PhoneIcon size={14} /> Sender
              </span>
              <span className="ob__roleHint">Отправляет outreach (4/мин · 20/ч · 150/день)</span>
            </button>
            <button
              type="button"
              className={`ob__role ${role === "checker" ? "is-active" : ""}`}
              onClick={() => setRole("checker")}
            >
              <span className="ob__roleTitle">
                <ShieldCheck size={14} /> Checker
              </span>
              <span className="ob__roleHint">Проверяет номера в Telegram</span>
            </button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn btn--ghost" type="button" onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn btn--primary"
            type="button"
            disabled={mut.isPending}
            onClick={() => mut.mutate()}
          >
            {mut.isPending ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
      </div>
    </Modal>
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
