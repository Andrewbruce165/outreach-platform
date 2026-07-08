import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect, useRef } from "react";
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
  Upload,
  Loader2,
  Pause,
  Play,
  Star,
  Phone as PhoneIcon,
  Search,
  ShieldAlert,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { OnboardingFlow } from "@/components/OnboardingFlow";
import { AccountImportModal } from "@/components/AccountImportModal";
import { api, ApiError, apiBaseUrl } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { components } from "@/types/api";

type Sender = components["schemas"]["SenderResponse"];
type RestrictionEvent = components["schemas"]["RestrictionEventResponse"];
type ProfileUpdateResponse = components["schemas"]["ProfileUpdateResponse"];
type UsernameCheckResponse = components["schemas"]["UsernameCheckResponse"];

/** Recovery-email step-1 response (POST /2fa/recovery-email). Not a named schema. */
type RecoveryEmailStartResponse = { code?: string | null; code_length?: number | null };

export const Route = createFileRoute("/_authenticated/accounts")({
  component: AccountsPage,
});

function AccountsPage() {
  const [modal, setModal] = useState<null | {
    mode: "new" | "reauth";
    phone?: string;
    slug?: string;
  }>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [search, setSearch] = useState("");
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
    refetchInterval: 15000,
  });

  const allSenders = data?.senders ?? [];
  const q = search.trim().toLowerCase();
  const senders = q
    ? allSenders.filter((s) => {
        const hay = [s.name, s.tg_username, s.phone]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return hay.includes(q);
      })
    : allSenders;
  const counts = {
    total: allSenders.length,
    active: allSenders.filter((s) => s.status === "active").length,
    warmup: allSenders.filter((s) => s.status === "warmup").length,
    paused: allSenders.filter((s) => s.status === "paused").length,
    error: allSenders.filter((s) => s.status === "error").length,
    restricted: allSenders.filter((s) => s.status === "limited" || s.status === "frozen").length,
  };

  return (
    <>
      <Topbar
        title="Telegram accounts"
        right={
          <>
            <div style={{ position: "relative" }}>
              <Search
                size={13}
                style={{
                  position: "absolute",
                  left: 8,
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "var(--text-muted)",
                  pointerEvents: "none",
                }}
              />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name…"
                aria-label="Search accounts"
                className="input input--sm"
                style={{ width: 200, paddingLeft: 26, height: 28 }}
              />
            </div>
            <button className="btn btn--ghost btn--sm" type="button">
              <Filter size={14} /> Filters
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setImportOpen(true)}
            >
              <Upload size={14} /> Import accounts
            </button>
            <button className="btn btn--primary btn--sm" onClick={() => setModal({ mode: "new" })}>
              <Plus size={14} /> Connect account
            </button>
          </>
        }
      />
      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}>
        {allSenders.length > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(6, 1fr)",
              gap: 12,
              marginBottom: 16,
            }}
          >
            <MiniMetric
              label="Connected"
              value={counts.total}
              sub="All accounts"
              color="var(--tg-blue)"
            />
            <MiniMetric
              label="Active"
              value={counts.active}
              sub="Sending now"
              color="var(--success)"
            />
            <MiniMetric
              label="Warm-up"
              value={counts.warmup}
              sub="≤ 30 days"
              color="var(--warning)"
            />
            <MiniMetric label="Paused" value={counts.paused} sub="Idle" color="var(--text-muted)" />
            <MiniMetric
              label="Restricted"
              value={counts.restricted}
              sub="Spam-limit / frozen"
              color="var(--orange, var(--warning))"
            />
            <MiniMetric
              label="Errors"
              value={counts.error}
              sub="Need attention"
              color="var(--danger)"
            />
          </div>
        )}

        {allSenders.length > 0 && senders.length === 0 && !isLoading && !error ? (
          <div className="card">
            <div className="card__body muted" style={{ textAlign: "center", padding: "32px 24px" }}>
              No accounts match “{search}”.
            </div>
          </div>
        ) : (
          <FleetTable
            senders={senders}
            isLoading={isLoading}
            errorMsg={error instanceof ApiError ? error.message : null}
            onEmpty={() => setModal({ mode: "new" })}
            onReauth={(s) => setModal({ mode: "reauth", phone: s.phone, slug: s.slug })}
          />
        )}
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

      {importOpen && <AccountImportModal onClose={() => setImportOpen(false)} />}
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
    <div
      className="card"
      style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 4 }}
    >
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

// ─── Account status presentation (shared by SenderCard) ─────────────────────
// Sender-role statuses.
const SENDER_STATUS_STYLE: Record<string, { pill: string; dot: string }> = {
  active: { pill: "pill--green", dot: "var(--success)" },
  warmup: { pill: "pill--blue", dot: "var(--tg-blue)" },
  paused: { pill: "pill--ghost", dot: "var(--text-muted)" },
  error: { pill: "pill--red", dot: "var(--danger)" },
  limited: { pill: "pill--orange", dot: "var(--warning)" },
  frozen: { pill: "pill--red", dot: "var(--danger)" },
};
const SENDER_STATUS_LABEL: Record<string, string> = {
  active: "Active",
  warmup: "Warm-up",
  paused: "Paused",
  error: "Error",
  limited: "Spam-limited",
  frozen: "Frozen",
};
// Checker-specific statuses (role='checker'). Amber = auto-recovering, no action.
// Red = needs the user (re-auth / banned). Distinct from the sender-oriented
// status above so a self-healing throttle never reads as a hard error.
const CHECKER_STATUS_STYLE: Record<string, { pill: string; dot: string }> = {
  active: { pill: "pill--green", dot: "var(--success)" },
  cooling_down: { pill: "pill--orange", dot: "var(--warning)" },
  frozen: { pill: "pill--orange", dot: "var(--warning)" },
  paused: { pill: "pill--ghost", dot: "var(--text-muted)" },
  reauth_needed: { pill: "pill--red", dot: "var(--danger)" },
  banned: { pill: "pill--red", dot: "var(--danger)" },
};
const CHECKER_STATUS_LABEL: Record<string, string> = {
  active: "Active",
  cooling_down: "Cooling down",
  frozen: "Frozen",
  paused: "Paused",
  reauth_needed: "Re-auth needed",
  banned: "Banned",
};

/**
 * Grouping priority within a role group. Tier 0 (accounts that need the user —
 * re-auth / banned) always floats to the top, unconditionally, regardless of any
 * other status. Then active (tier 1), then everything else (tier 2). Callers
 * sort stably within a tier by preserving the original API index.
 */
function priorityTier(s: Sender): number {
  if (s.role === "checker") {
    if (s.checker_status === "reauth_needed" || s.checker_status === "banned") return 0;
    if (s.checker_status === "active") return 1;
    return 2;
  }
  if (s.auth_status !== "ok") return 0;
  if (s.status === "active") return 1;
  return 2;
}

type SenderGroup = { role: "sender" | "checker"; label: string; items: Sender[] };

/** Split senders into Sender/Checker groups, each priority-sorted (stable). */
function groupSenders(senders: Sender[]): SenderGroup[] {
  const indexed = senders.map((s, i) => ({ s, i }));
  const byTier = (a: { s: Sender; i: number }, b: { s: Sender; i: number }) =>
    priorityTier(a.s) - priorityTier(b.s) || a.i - b.i;
  const senderItems = indexed
    .filter((x) => x.s.role !== "checker")
    .sort(byTier)
    .map((x) => x.s);
  const checkerItems = indexed
    .filter((x) => x.s.role === "checker")
    .sort(byTier)
    .map((x) => x.s);
  const groups: SenderGroup[] = [];
  if (senderItems.length) groups.push({ role: "sender", label: "Sender", items: senderItems });
  if (checkerItems.length) groups.push({ role: "checker", label: "Checker", items: checkerItems });
  return groups;
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

  const groups = groupSenders(senders);
  return (
    <TooltipProvider delayDuration={150}>
      {groups.map((g) => (
        <section key={g.role} className="acct-group">
          <div className="acct-group__head">
            {g.role === "checker" ? <ShieldCheck size={15} /> : <PhoneIcon size={15} />}
            {g.label}
            <span className="acct-group__count">{g.items.length}</span>
          </div>
          <div className="tile-grid">
            {g.items.map((s) => (
              <SenderCard key={s.id} sender={s} onReauth={() => onReauth(s)} />
            ))}
          </div>
        </section>
      ))}
    </TooltipProvider>
  );
}

type SpambotResult = {
  status?: string;
  raw_text?: string;
  auth_status_updated?: string | null;
};

function SenderCard({ sender, onReauth }: { sender: Sender; onReauth: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [spambotResult, setSpambotResult] = useState<SpambotResult | null>(null);

  const spambotMut = useMutation({
    mutationFn: () =>
      api<SpambotResult>(`/api/v1/senders/${sender.slug}/spambot-check`),
    onSuccess: (res) => {
      setSpambotResult(res ?? {});
      void qc.invalidateQueries({ queryKey: ["senders"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Spam Bot check failed"),
  });

  const deleteMut = useMutation({
    mutationFn: () => api(`/api/v1/senders/${sender.slug}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Account deleted");
      qc.invalidateQueries({ queryKey: ["senders"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });

  // D-12 manual resync: re-fetch the live Telegram profile (username/bio/photo)
  // via POST /resync. Replaces the old "Обновить статус" (status derives fresh
  // on every load, so that affordance was redundant).
  const resyncMut = useMutation({
    mutationFn: () => api<Sender>(`/api/v1/senders/${sender.slug}/resync`, { method: "POST" }),
    onSuccess: (fresh) => {
      qc.setQueryData<{ senders: Sender[] }>(["senders"], (prev) =>
        prev ? { senders: prev.senders.map((x) => (x.slug === sender.slug ? fresh : x)) } : prev,
      );
      void qc.invalidateQueries({ queryKey: ["senders"] });
      void qc.invalidateQueries({ queryKey: ["sender-photo", sender.slug] });
      toast.success("Профиль обновлён");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось обновить профиль"),
  });

  // UI-SPEC §5.10 row actions Pause / Resume — flip lifecycle_status via the
  // dedicated endpoints (idempotent on the backend). Both return
  // SenderCreateResponse {sender, warnings} — unwrap .sender for the cache.
  const applyFreshSender = (fresh: Sender) => {
    qc.setQueryData<{ senders: Sender[] }>(["senders"], (prev) =>
      prev ? { senders: prev.senders.map((x) => (x.slug === sender.slug ? fresh : x)) } : prev,
    );
    void qc.invalidateQueries({ queryKey: ["senders"] });
  };
  const pauseMut = useMutation({
    mutationFn: () =>
      api<{ sender: Sender }>(`/api/v1/senders/${sender.slug}/pause`, { method: "POST" }),
    onSuccess: (res) => {
      applyFreshSender(res.sender);
      toast.success("Аккаунт поставлен на паузу");
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "SENDER_USED_BY_RUNNING_CAMPAIGN") {
        const campaigns = (e.detail as { campaigns?: { name: string }[] } | undefined)?.campaigns;
        const names = campaigns?.map((c) => c.name).join(", ");
        toast.error(
          `Аккаунт занят в запущенной кампании${names ? ` (${names})` : ""} — сначала остановите её`,
        );
        return;
      }
      toast.error(e instanceof ApiError ? e.message : "Не удалось поставить на паузу");
    },
  });
  const resumeMut = useMutation({
    mutationFn: () =>
      api<{ sender: Sender }>(`/api/v1/senders/${sender.slug}/resume`, { method: "POST" }),
    onSuccess: (res) => {
      applyFreshSender(res.sender);
      toast.success("Аккаунт снят с паузы");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось снять с паузы"),
  });

  const sty = SENDER_STATUS_STYLE[sender.status] ?? SENDER_STATUS_STYLE.paused;
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

  // Checker status presentation (only when the backend supplied checker_status).
  const checkerStatus = isChecker ? (sender.checker_status ?? null) : null;
  const cSty = checkerStatus
    ? (CHECKER_STATUS_STYLE[checkerStatus] ?? SENDER_STATUS_STYLE.paused)
    : null;
  const checkerRetry =
    checkerStatus === "cooling_down" || checkerStatus === "frozen"
      ? relativeRetry(sender.restricted_until)
      : null;
  const checkerTrip = sender.checker_trip_count ?? 0;
  const checkerSubtitle =
    checkerStatus === "cooling_down" || checkerStatus === "frozen"
      ? `Auto · no action needed${checkerTrip > 1 ? ` · attempt ${checkerTrip}, longer rest` : ""}`
      : checkerStatus === "reauth_needed"
        ? "Action required · session expired"
        : checkerStatus === "banned"
          ? "Action required · banned"
          : null;
  const dotColor = cSty ? cSty.dot : sty.dot;
  const showReauth = checkerStatus
    ? checkerStatus === "reauth_needed"
    : sender.auth_status !== "ok";

  return (
    <div className="acct-card">
      {/* identity + actions */}
      <div className="acct-card__top">
        <AccountAvatar sender={sender} dotColor={dotColor} size="lg" />
        <div className="acct-card__ident">
          <span className="acct-card__name">{sender.name || sender.phone}</span>
          {sender.tg_username && <span className="muted text-xs">@{sender.tg_username}</span>}
          <span className="muted text-xs mono">{sender.phone}</span>
          {sender.location && <span className="muted text-xs">Location: {sender.location}</span>}
        </div>
        <div className="acct-card__actions">
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
                  <Pencil size={13} /> Изменить профиль
                </button>
                <button
                  disabled={resyncMut.isPending}
                  onClick={() => {
                    setOpen(false);
                    resyncMut.mutate();
                  }}
                >
                  {resyncMut.isPending ? (
                    <Loader2 size={13} className="ob__spin" />
                  ) : (
                    <RefreshCcw size={13} />
                  )}{" "}
                  Обновить профиль
                </button>
                {sender.lifecycle_status === "paused" ? (
                  <button
                    disabled={resumeMut.isPending}
                    onClick={() => {
                      setOpen(false);
                      resumeMut.mutate();
                    }}
                  >
                    {resumeMut.isPending ? (
                      <Loader2 size={13} className="ob__spin" />
                    ) : (
                      <Play size={13} />
                    )}{" "}
                    Снять с паузы
                  </button>
                ) : sender.lifecycle_status === "active" ? (
                  <button
                    disabled={pauseMut.isPending}
                    onClick={() => {
                      setOpen(false);
                      pauseMut.mutate();
                    }}
                  >
                    {pauseMut.isPending ? (
                      <Loader2 size={13} className="ob__spin" />
                    ) : (
                      <Pause size={13} />
                    )}{" "}
                    Поставить на паузу
                  </button>
                ) : null}
                <button
                  disabled={spambotMut.isPending}
                  onClick={() => {
                    setOpen(false);
                    spambotMut.mutate();
                  }}
                >
                  {spambotMut.isPending ? (
                    <Loader2 size={13} className="ob__spin" />
                  ) : (
                    <ShieldAlert size={13} />
                  )}{" "}
                  Check Spam Bot
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
        </div>
      </div>

      {/* bio (clamped to 2 lines) */}
      {sender.tg_bio && <div className="acct-card__bio text-clamp-2">{sender.tg_bio}</div>}

      {/* status + role + re-auth */}
      <div className="acct-card__pillCol">
        <div className="acct-card__pills">
          {checkerStatus && cSty ? (
            <span className={`pill ${cSty.pill}`}>
              <span className="pill__dot" /> {CHECKER_STATUS_LABEL[checkerStatus] ?? checkerStatus}
              {checkerRetry ? ` · retry in ${checkerRetry}` : ""}
            </span>
          ) : (
            <span className={`pill ${sty.pill}`}>
              <span className="pill__dot" /> {SENDER_STATUS_LABEL[sender.status] ?? sender.status}
            </span>
          )}
          <span
            className={`pill ${isChecker ? "pill--purple" : "pill--ghost"}`}
            title={isChecker ? "Verifies phone numbers on Telegram" : "Sends outreach messages"}
          >
            {isChecker ? <ShieldCheck size={11} /> : <PhoneIcon size={11} />}
            {isChecker ? "Checker" : "Sender"}
          </span>
          {sender.tg_premium && (
            <span className="pill pill--orange" title="У аккаунта есть Telegram Premium">
              <Star size={11} /> Premium
            </span>
          )}
          {showReauth && (
            <button className="ob__link" onClick={onReauth}>
              re-auth
            </button>
          )}
        </div>
        {checkerStatus && checkerSubtitle && <div className="muted text-xs">{checkerSubtitle}</div>}
        {!checkerStatus && isRestricted && (
          <div className="muted text-xs">
            {restrictedUntil
              ? `Not sending · rechecks ${restrictedUntil}`
              : "Not sending until cleared"}
          </div>
        )}
      </div>

      {/* today · ceiling */}
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="acct-card__meter" style={{ cursor: "help" }}>
            <div className="acct-card__meterRow">
              <span className="muted text-xs">Сегодня · потолок</span>
              <span className="num text-xs muted">
                {sender.sent_today ?? 0} / {dailyLimit}
              </span>
            </div>
            <CorridorBar value={sender.sent_today ?? 0} limit={dailyLimit} />
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[260px] text-left leading-relaxed">
          {isChecker
            ? `Overall message limit — ${dailyLimit}/day (incl. follow-ups).`
            : `Overall message limit — ${dailyLimit}/day (incl. follow-ups). New-contact outreach is capped separately at 50 new dialogs/day per account.`}
        </TooltipContent>
      </Tooltip>

      {/* limits · proxy · activity */}
      <dl className="acct-card__kv">
        <dt>Лимиты</dt>
        <dd className="num mono">
          {sender.rate_limits.per_minute} · {sender.rate_limits.per_hour} ·{" "}
          {sender.rate_limits.per_day}
        </dd>
        <dt>Прокси</dt>
        <dd>
          <span className="pill pill--ghost">
            {sender.proxy ? proxyLabel(sender.proxy) : "Direct"}
          </span>
        </dd>
        <dt>Активность</dt>
        <dd className="muted">{lastUsed}</dd>
      </dl>

      {editing && <ProfileModal sender={sender} onClose={() => setEditing(false)} />}
      {historyOpen && (
        <RestrictionHistoryModal sender={sender} onClose={() => setHistoryOpen(false)} />
      )}
      {spambotResult && (
        <SpambotResultModal
          sender={sender}
          result={spambotResult}
          onClose={() => setSpambotResult(null)}
        />
      )}
    </div>
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
  privacy_restricted: {
    label: "Приватность получателя",
    pill: "pill--ghost",
    dot: "var(--text-muted)",
  },
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

function RestrictionHistoryModal({ sender, onClose }: { sender: Sender; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["restriction-events", sender.slug],
    queryFn: () => api<RestrictionEvent[]>(`/api/v1/senders/${sender.slug}/restriction-events`),
  });

  const events = data ?? [];

  return (
    <Modal title={`История ограничений · ${sender.name || sender.phone}`} onClose={onClose}>
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
                  <span className="muted text-xs">{SOURCE_LABEL[ev.source] ?? ev.source}</span>
                  <span className="muted text-xs" style={{ marginLeft: "auto" }}>
                    {eventTime(ev.created_at)}
                  </span>
                </div>
                {until && <div className="muted text-xs">Ограничение до проверки в {until}</div>}
                {summary && <div className="muted text-xs mono">{summary}</div>}
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

function SpambotResultModal({
  sender,
  result,
  onClose,
}: {
  sender: Sender;
  result: SpambotResult;
  onClose: () => void;
}) {
  const status = (result.status ?? "unknown").toLowerCase();
  const STATUS_STY: Record<string, { pill: string; dot: string; label: string }> = {
    free: { pill: "pill--green", dot: "var(--success)", label: "Free" },
    limited: { pill: "pill--orange", dot: "var(--warning)", label: "Limited" },
    suspended: { pill: "pill--red", dot: "var(--danger)", label: "Suspended" },
    unknown: { pill: "pill--ghost", dot: "var(--text-muted)", label: "Unknown" },
  };
  const sty = STATUS_STY[status] ?? STATUS_STY.unknown;
  return (
    <Modal title={`@SpamBot check · ${sender.name || sender.phone}`} onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className={`pill ${sty.pill}`}>
            <span className="pill__dot" style={{ background: sty.dot }} /> {sty.label}
          </span>
          {result.auth_status_updated && (
            <span className="muted text-xs">
              auth status: {result.auth_status_updated}
            </span>
          )}
        </div>
        {result.raw_text ? (
          <div
            style={{
              padding: 12,
              background: "var(--bg-soft)",
              borderRadius: 8,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: 13,
              lineHeight: 1.5,
              maxHeight: 360,
              overflowY: "auto",
            }}
          >
            {result.raw_text}
          </div>
        ) : (
          <div className="muted text-sm">No response text from @SpamBot.</div>
        )}
      </div>
    </Modal>
  );
}

// ─── Phase 20 profile helpers ───────────────────────────────────────────────
const HOUR_MS = 3_600_000;
const USERNAME_RE = /^[a-z0-9_]{5,32}$/;
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

/**
 * Fetch a sender's cached avatar bytes from the auth-gated endpoint
 * (GET /senders/{slug}/photo, D-11/C1) and hand back an object URL — never a
 * public blob URL, never base64 in the list. Returns null on 404/no-photo.
 */
async function fetchSenderPhoto(slug: string): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  const res = await fetch(`${apiBaseUrl}/api/v1/senders/${slug}/photo`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/** Remaining ms of a per-field 1h cooldown (D-08); 0 when clear. */
function cooldownRemainingMs(
  sender: Sender,
  field: "username" | "photo",
  nowMs: number = Date.now(),
): number {
  const map = sender.profile_field_changed_at as Record<string, unknown> | undefined;
  const iso = map?.[field];
  if (typeof iso !== "string") return 0;
  const changed = new Date(iso).getTime();
  if (Number.isNaN(changed)) return 0;
  const remaining = changed + HOUR_MS - nowMs;
  return remaining > 0 ? remaining : 0;
}

function fmtCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** D-09: account younger than 7 days. */
function isFreshAccount(sender: Sender): boolean {
  if (!sender.created_at) return false;
  const created = new Date(sender.created_at).getTime();
  if (Number.isNaN(created)) return false;
  return Date.now() - created < 7 * 24 * HOUR_MS;
}

/** Re-render every second while `active`, so live countdowns tick down. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** Map a backend profile / 2FA error code to the approved RU copy (UI-SPEC §Copywriting). */
function profileErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "Что-то пошло не так. Попробуйте снова.";
  const retry = (() => {
    const d = e.detail as Record<string, unknown> | undefined;
    const s = d ? (d.retry_after ?? d.seconds) : undefined;
    return typeof s === "number" ? `${s} c` : "немного";
  })();
  switch (e.code) {
    case "USERNAME_TAKEN":
      return "Этот username уже занят";
    case "USERNAME_INVALID":
      return "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)";
    case "BIO_TOO_LONG":
      return "Описание слишком длинное (максимум 70 символов)";
    case "PASSWORD_INVALID":
      return "Неверный текущий пароль 2FA";
    case "EMAIL_INVALID":
      return "Некорректный email";
    case "EMAIL_CODE_INVALID":
      return "Неверный или просроченный код";
    case "TOO_FRESH":
      return `Telegram временно блокирует это действие на новом аккаунте. Попробуйте через ${retry}.`;
    case "TOO_FREQUENT":
      return "Слишком часто. Это действие можно повторять не чаще раза в час.";
    case "FLOOD_WAIT":
      return `Слишком часто. Повторите через ${retry}.`;
    case "FILE_TOO_LARGE":
      return "Файл слишком большой (максимум 5 МБ)";
    case "UNSUPPORTED_FILE_TYPE":
      return "Неподдерживаемый формат. Загрузите JPG или PNG";
    case "PHOTO_TOO_SMALL":
      return "Фото слишком маленькое";
    case "PHOTO_FORMAT_INVALID":
      return "Неподдерживаемый формат. Загрузите JPG или PNG";
    default:
      return e.message;
  }
}

/**
 * D-10/D-11: account-row avatar. Cached photo (fetched auth-gated → object URL)
 * when `has_photo`, initials fallback otherwise. Keeps the status-dot overlay.
 */
function AccountAvatar({
  sender,
  dotColor,
  size = "sm",
}: {
  sender: Sender;
  dotColor: string;
  size?: "sm" | "lg";
}) {
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const photoChangedAt = (sender.profile_field_changed_at as Record<string, unknown> | undefined)
    ?.photo;

  useEffect(() => {
    if (!sender.has_photo) {
      setPhotoUrl(null);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    void fetchSenderPhoto(sender.slug).then((url) => {
      if (cancelled) {
        if (url) URL.revokeObjectURL(url);
        return;
      }
      created = url;
      setPhotoUrl(url);
    });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [sender.slug, sender.has_photo, photoChangedAt]);

  const initial = (sender.name || sender.phone).slice(0, 1).toUpperCase();
  const dotSize = size === "lg" ? 13 : 11;
  return (
    <div style={{ position: "relative", flexShrink: 0 }}>
      <div
        className={`avatar avatar--${size}`}
        style={{
          background: "var(--tg-blue-soft)",
          color: "var(--tg-blue)",
          overflow: "hidden",
        }}
      >
        {sender.has_photo && photoUrl ? (
          <img
            src={photoUrl}
            alt=""
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          initial
        )}
      </div>
      <div
        aria-hidden
        style={{
          position: "absolute",
          bottom: -1,
          right: -1,
          width: dotSize,
          height: dotSize,
          borderRadius: 50,
          background: dotColor,
          border: "2px solid var(--bg)",
        }}
      />
    </div>
  );
}

/** Best-effort split of a combined Telegram display name into [first, last]. */
function splitName(full: string | null | undefined): [string, string] {
  const s = (full ?? "").trim();
  if (!s) return ["", ""];
  const idx = s.indexOf(" ");
  if (idx === -1) return [s, ""];
  return [s.slice(0, idx), s.slice(idx + 1).trim()];
}

/**
 * Phase 20 full account-profile editor (Surface 3). Two independently-submitted
 * sections — A: Профиль (identity via PATCH /profile) and B: Безопасность (2FA)
 * (password via POST /2fa, recovery email via the two-step /2fa/recovery-email
 * flow). Each section has its own scoped primary button (Reconciliation §5) —
 * there is no single generic Save. Frequency guardrails (D-06..09) are computed
 * client-side and mirror the backend 409/422.
 */
function ProfileModal({ sender, onClose }: { sender: Sender; onClose: () => void }) {
  const qc = useQueryClient();
  const authExpired = sender.auth_status !== "ok";

  // ── Section A — identity ────────────────────────────────────────────────
  // Telegram stores one combined display name; best-effort split on the first
  // space so the Фамилия field prefills instead of always starting blank.
  const [initialFirst, initialLast] = splitName(sender.name);
  const initialRole = sender.role === "checker" ? "checker" : "sender";
  const [firstName, setFirstName] = useState(initialFirst);
  const [lastName, setLastName] = useState(initialLast);
  const [username, setUsername] = useState(sender.tg_username ?? "");
  const [about, setAbout] = useState(sender.tg_bio ?? "");
  const [role, setRole] = useState<"sender" | "checker">(initialRole);

  const usernameChanged = username.trim() !== (sender.tg_username ?? "");
  const nameChanged =
    firstName.trim() !== initialFirst.trim() || lastName.trim() !== initialLast.trim();
  const bioChanged = about !== (sender.tg_bio ?? "");
  const roleChanged = role !== initialRole;

  // ── live per-field cooldowns (D-08) ─────────────────────────────────────
  const usernameCd0 = cooldownRemainingMs(sender, "username");
  const photoCd0 = cooldownRemainingMs(sender, "photo");
  const now = useNow(usernameCd0 > 0 || photoCd0 > 0);
  const usernameCd = cooldownRemainingMs(sender, "username", now);
  const photoCd = cooldownRemainingMs(sender, "photo", now);
  const usernameBlocked = usernameChanged && usernameCd > 0;
  const photoBlocked = photoCd > 0;
  const isWarmupOrFresh = sender.lifecycle_status === "warmup" || isFreshAccount(sender);

  // ── username availability (C5) ──────────────────────────────────────────
  const [uState, setUState] = useState<"idle" | "checking" | "free" | "taken" | "invalid">("idle");
  useEffect(() => {
    const u = username.trim();
    if (!usernameChanged || u === "") {
      setUState("idle");
      return;
    }
    if (!USERNAME_RE.test(u)) {
      setUState("invalid");
      return;
    }
    setUState("checking");
    const ctrl = new AbortController();
    const id = window.setTimeout(() => {
      api<UsernameCheckResponse>(`/api/v1/senders/${sender.slug}/username-check`, {
        query: { username: u },
        signal: ctrl.signal,
      })
        .then((r) => setUState(r.available ? "free" : "taken"))
        .catch(() => setUState("idle"));
    }, 450);
    return () => {
      ctrl.abort();
      window.clearTimeout(id);
    };
  }, [username, usernameChanged, sender.slug]);

  // ── photo preview (auth-gated bytes) ────────────────────────────────────
  const photoChangedAt = (sender.profile_field_changed_at as Record<string, unknown> | undefined)
    ?.photo;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!sender.has_photo) {
      setPreviewUrl(null);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    void fetchSenderPhoto(sender.slug).then((url) => {
      if (cancelled) {
        if (url) URL.revokeObjectURL(url);
        return;
      }
      created = url;
      setPreviewUrl(url);
    });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [sender.slug, sender.has_photo, photoChangedAt]);

  // ── Section A save (identity + optional app-level role) ─────────────────
  const saveProfileMut = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = {
        first_name: firstName.trim() || null,
        last_name: lastName.trim() || null,
        about: about,
      };
      if (usernameChanged) body.username = username.trim() || null;
      const res = await api<ProfileUpdateResponse>(`/api/v1/senders/${sender.slug}/profile`, {
        method: "PATCH",
        body,
      });
      // Role (sender↔checker) is an app-level setting, not a Telegram profile
      // field — persisted via the SenderUpdate endpoint. Kept here so the
      // profile editor does not regress the operationally-critical role flip.
      if (roleChanged) {
        await api(`/api/v1/senders/${sender.slug}`, {
          method: "PATCH",
          body: { role },
        });
      }
      return res;
    },
    onSuccess: (res) => {
      toast.success("Профиль обновлён");
      (res.warnings ?? []).forEach((w) => toast.warning(w.message));
      void qc.invalidateQueries({ queryKey: ["senders"] });
      void qc.invalidateQueries({ queryKey: ["sender-photo", sender.slug] });
    },
    onError: (e) => toast.error(profileErrorMessage(e)),
  });

  function handleSaveProfile() {
    if (usernameBlocked) return; // hard block (button already disabled)
    if (usernameChanged && (uState === "taken" || uState === "invalid")) {
      toast.error(
        uState === "taken"
          ? "Этот username уже занят"
          : "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)",
      );
      return;
    }
    const advisories: string[] = [];
    if (nameChanged || bioChanged)
      advisories.push(
        "Слишком частая смена имени или описания может насторожить Telegram. Продолжить?",
      );
    if (isWarmupOrFresh)
      advisories.push(
        "Аккаунт ещё прогревается (моложе 7 дней). Резкие изменения профиля повышают риск ограничений. Продолжить?",
      );
    if (advisories.length && !window.confirm(advisories.join("\n\n"))) return;
    saveProfileMut.mutate();
  }

  // ── photo upload / delete (D-08/D-11, C6) ───────────────────────────────
  const fileRef = useRef<HTMLInputElement>(null);
  const photoMut = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<ProfileUpdateResponse>(`/api/v1/senders/${sender.slug}/photo`, {
        method: "POST",
        body: fd,
      });
    },
    onSuccess: () => {
      toast.success("Профиль обновлён");
      void qc.invalidateQueries({ queryKey: ["senders"] });
      void qc.invalidateQueries({ queryKey: ["sender-photo", sender.slug] });
    },
    onError: (e) => toast.error(profileErrorMessage(e)),
  });
  const deletePhotoMut = useMutation({
    mutationFn: () => api(`/api/v1/senders/${sender.slug}/photo`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Профиль обновлён");
      void qc.invalidateQueries({ queryKey: ["senders"] });
      void qc.invalidateQueries({ queryKey: ["sender-photo", sender.slug] });
    },
    onError: (e) => toast.error(profileErrorMessage(e)),
  });

  function handlePhotoFile(file: File) {
    if (photoBlocked || photoMut.isPending) return;
    if (file.size > MAX_PHOTO_BYTES) {
      toast.error("Файл слишком большой (максимум 5 МБ)");
      return;
    }
    if (!["image/jpeg", "image/png"].includes(file.type)) {
      toast.error("Только JPG или PNG");
      return;
    }
    photoMut.mutate(file);
  }

  // ── Section B — 2FA password ────────────────────────────────────────────
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [hint, setHint] = useState("");
  const passwordMut = useMutation({
    mutationFn: () =>
      api(`/api/v1/senders/${sender.slug}/2fa`, {
        method: "POST",
        body: {
          current_password: currentPw || null,
          new_password: newPw,
          hint: hint.trim() || null,
        },
      }),
    onSuccess: () => {
      toast.success("Пароль 2FA обновлён");
      setNewPw("");
      setHint("");
    },
    onError: (e) => toast.error(profileErrorMessage(e)),
  });

  // ── Section B — recovery email (two-step, C4) ───────────────────────────
  const [email, setEmail] = useState("");
  const [emailStep, setEmailStep] = useState<"input" | "sent">("input");
  const [code, setCode] = useState("");
  const [codeLength, setCodeLength] = useState<number | null>(null);
  const emailStartMut = useMutation({
    mutationFn: () =>
      api<RecoveryEmailStartResponse>(`/api/v1/senders/${sender.slug}/2fa/recovery-email`, {
        method: "POST",
        body: { current_password: currentPw || null, email: email.trim() },
      }),
    onSuccess: (res) => {
      setCodeLength(typeof res.code_length === "number" ? res.code_length : null);
      setEmailStep("sent");
    },
    onError: (e) => toast.error(profileErrorMessage(e)),
  });
  const emailConfirmMut = useMutation({
    mutationFn: () =>
      api(`/api/v1/senders/${sender.slug}/2fa/recovery-email/confirm`, {
        method: "POST",
        body: { code: code.trim() },
      }),
    onSuccess: () => {
      toast.success("Email восстановления обновлён");
      setEmailStep("input");
      setEmail("");
      setCode("");
      setCodeLength(null);
    },
    onError: (e) => toast.error(profileErrorMessage(e)),
  });

  const usernameHint = (() => {
    if (usernameBlocked)
      return {
        text: `Username можно менять не чаще раза в час. Попробуйте снова через ${fmtCountdown(usernameCd)}.`,
        color: "var(--danger)",
      };
    if (!usernameChanged) return { text: "5–32 символа: a-z, 0-9, _", color: "var(--text-muted)" };
    switch (uState) {
      case "checking":
        return { text: "Проверяем…", color: "var(--text-muted)" };
      case "free":
        return { text: "Свободно", color: "var(--success)" };
      case "taken":
        return { text: "Занято", color: "var(--danger)" };
      case "invalid":
        return {
          text: "Недопустимый формат (a-z, 0-9, _, 5–32 символа)",
          color: "var(--danger)",
        };
      default:
        return { text: "5–32 символа: a-z, 0-9, _", color: "var(--text-muted)" };
    }
  })();

  if (authExpired) {
    return (
      <Modal title={`Профиль · ${sender.name || sender.phone}`} onClose={onClose} wide>
        <div style={{ color: "var(--danger)", fontSize: 13, display: "flex", gap: 8 }}>
          <AlertCircle size={16} />
          <span>
            Сессия аккаунта истекла. Переподключите аккаунт (кнопка «re-auth» в строке), чтобы
            менять профиль.
          </span>
        </div>
      </Modal>
    );
  }

  const initial = (sender.name || sender.phone).slice(0, 1).toUpperCase();

  return (
    <Modal title={`Профиль · ${sender.name || sender.phone}`} onClose={onClose} wide>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {sender.location && (
          <span className="muted text-xs">Location: {sender.location}</span>
        )}
        {/* ── Section A — Профиль ─────────────────────────────────────── */}
        <div className="profile-section">
          <div className="profile-section__head">
            <Pencil size={13} /> Профиль
          </div>

          <div style={{ display: "flex", gap: 12 }}>
            <div className="field" style={{ flex: 1 }}>
              <label className="field__label">Имя</label>
              <input
                className="input"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                placeholder={sender.phone}
              />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label className="field__label">Фамилия</label>
              <input
                className="input"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
              />
            </div>
          </div>

          <div className="field">
            <label className="field__label">Username</label>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span className="muted" style={{ fontSize: 13 }}>
                @
              </span>
              <input
                className="input"
                style={{ flex: 1 }}
                value={username}
                onChange={(e) => setUsername(e.target.value.replace(/^@/, ""))}
                placeholder="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
            </div>
            <span className="field__hint" style={{ color: usernameHint.color }}>
              {usernameHint.text}
            </span>
          </div>

          <div className="field">
            <label className="field__label">Описание</label>
            <textarea
              className="textarea"
              value={about}
              maxLength={70}
              rows={2}
              onChange={(e) => setAbout(e.target.value)}
            />
            <span className="field__hint">{about.length}/70</span>
          </div>

          <div className="field">
            <label className="field__label">Фото профиля</label>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
              <div
                className="avatar avatar--xl"
                style={{
                  background: "var(--tg-blue-soft)",
                  color: "var(--tg-blue)",
                  overflow: "hidden",
                  flex: "0 0 auto",
                }}
              >
                {sender.has_photo && previewUrl ? (
                  <img
                    src={previewUrl}
                    alt=""
                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                  />
                ) : (
                  initial
                )}
              </div>
              <div style={{ flex: 1 }}>
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/jpeg,image/png"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handlePhotoFile(f);
                    e.target.value = "";
                  }}
                />
                <div
                  className="ct__dropzone"
                  onClick={() => {
                    if (!photoBlocked && !photoMut.isPending) fileRef.current?.click();
                  }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    const f = e.dataTransfer.files[0];
                    if (f) handlePhotoFile(f);
                  }}
                  style={photoBlocked ? { opacity: 0.55, pointerEvents: "none" } : undefined}
                >
                  {photoMut.isPending ? (
                    <>
                      <Loader2 size={20} className="ob__spin" />
                      <span>Загрузка…</span>
                    </>
                  ) : (
                    <>
                      <Upload size={20} />
                      <span className="fw5">Загрузить фото</span>
                      <span className="muted text-xs">
                        Перетащите фото сюда или нажмите, чтобы выбрать (JPG/PNG, до 5 МБ)
                      </span>
                    </>
                  )}
                </div>
                {sender.has_photo && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    style={{ marginTop: 8, color: "var(--danger)" }}
                    disabled={deletePhotoMut.isPending || photoBlocked}
                    onClick={() => {
                      if (window.confirm("Удалить фото профиля?")) deletePhotoMut.mutate();
                    }}
                  >
                    <Trash2 size={13} /> Удалить фото
                  </button>
                )}
                {photoBlocked && (
                  <div className="field__hint" style={{ color: "var(--danger)" }}>
                    Фото можно менять не чаще раза в час. Попробуйте снова через{" "}
                    {fmtCountdown(photoCd)}.
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Роль — app-level operational setting, kept out of the identity fields */}
          <div className="profile-section__block">
            <span className="profile-section__blockLabel">Роль</span>
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

          <div className="profile-footer">
            <button className="btn btn--ghost" type="button" onClick={onClose}>
              Отмена
            </button>
            <button
              className="btn btn--primary"
              type="button"
              disabled={saveProfileMut.isPending || usernameBlocked}
              onClick={handleSaveProfile}
            >
              {saveProfileMut.isPending ? "Сохранение…" : "Сохранить профиль"}
            </button>
          </div>
        </div>

        {/* ── Section B — Безопасность (2FA) ──────────────────────────── */}
        <div className="profile-section">
          <div className="profile-section__head">
            <ShieldCheck size={13} /> Безопасность (2FA)
          </div>

          <div className="field">
            <label className="field__label">Текущий пароль 2FA</label>
            <input
              className="input"
              type="password"
              value={currentPw}
              autoComplete="off"
              onChange={(e) => setCurrentPw(e.target.value)}
            />
            <span className="field__hint">Заполните, если на аккаунте уже включён 2FA</span>
          </div>

          <div className="field">
            <label className="field__label">Новый пароль 2FA</label>
            <input
              className="input"
              type="password"
              value={newPw}
              autoComplete="new-password"
              onChange={(e) => setNewPw(e.target.value)}
            />
          </div>

          <div className="field">
            <label className="field__label">Подсказка (необязательно)</label>
            <input className="input" value={hint} onChange={(e) => setHint(e.target.value)} />
          </div>

          <div className="profile-footer">
            <button
              className="btn btn--primary"
              type="button"
              disabled={passwordMut.isPending || !newPw}
              onClick={() => passwordMut.mutate()}
            >
              {passwordMut.isPending ? "Сохранение…" : "Обновить пароль 2FA"}
            </button>
          </div>

          <div className="profile-section__block">
            <span className="profile-section__blockLabel">Email для восстановления</span>
            {emailStep === "input" ? (
              <>
                <input
                  className="input"
                  type="email"
                  value={email}
                  placeholder="you@example.com"
                  onChange={(e) => setEmail(e.target.value)}
                />
                <div className="profile-footer">
                  <button
                    className="btn btn--primary"
                    type="button"
                    disabled={emailStartMut.isPending || !email.trim()}
                    onClick={() => emailStartMut.mutate()}
                  >
                    {emailStartMut.isPending ? "Отправка…" : "Отправить код подтверждения"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="muted text-sm">Мы отправили код на {email}. Введите его ниже.</div>
                <input
                  className="input"
                  value={code}
                  inputMode="numeric"
                  placeholder={codeLength ? `${codeLength}-значный код` : "Код из письма"}
                  onChange={(e) => setCode(e.target.value)}
                />
                <div
                  style={{
                    display: "flex",
                    gap: 12,
                    alignItems: "center",
                    justifyContent: "flex-end",
                  }}
                >
                  <button
                    className="ob__link"
                    type="button"
                    disabled={emailStartMut.isPending}
                    onClick={() => emailStartMut.mutate()}
                  >
                    Отправить снова
                  </button>
                  <button
                    className="btn btn--primary"
                    type="button"
                    disabled={emailConfirmMut.isPending || !code.trim()}
                    onClick={() => emailConfirmMut.mutate()}
                  >
                    {emailConfirmMut.isPending ? "Подтверждение…" : "Подтвердить email"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function CorridorBar({ value, limit }: { value: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (value / limit) * 100) : 0;
  const color = pct > 90 ? "var(--danger)" : pct > 70 ? "var(--warning)" : "var(--tg-blue)";
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
  wide = false,
}: {
  children: React.ReactNode;
  onClose: () => void;
  title: string;
  wide?: boolean;
}) {
  return (
    <div
      className="modal__scrim"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div className={`modal${wide ? " modal--wide" : ""}`} onClick={(e) => e.stopPropagation()}>
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

// Future-facing relative time for a checker's auto-retry (restricted_until).
// "Cooling down · retry in ~3 min" reads far clearer than an absolute timestamp.
function relativeRetry(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return "any moment";
  const min = Math.round(ms / 60000);
  if (min < 1) return "<1 min";
  if (min < 60) return `~${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `~${h}h ${m}m` : `~${h}h`;
}
