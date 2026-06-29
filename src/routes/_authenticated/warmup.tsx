import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { toast } from "sonner";
import {
  Flame,
  Power,
  Pause,
  Play,
  Plus,
  Trash2,
  ShieldAlert,
  Sparkles,
  AlertCircle,
  MoreHorizontal,
  Info,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { Switch } from "@/components/ui/switch";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export const Route = createFileRoute("/_authenticated/warmup")({
  component: WarmupPage,
});

// ============= Types (local — bound to /api/v1/warmup) =============
type WarmupSettings = {
  enabled: boolean;
  topics: string[];
  system_prompt: string;
  language: string | null;
  tone: string | null;
};

type PoolSender = {
  id: string;
  slug: string;
  name: string | null;
  phone: string;
  in_pool: boolean;
  warmup_active: boolean;
  enrolled_at: string | null;
  enrolled_days: number;
  level: number;
  sent_today: number;
  restriction_status: string;
  restricted_until: string | null;
  warmup_reason: string | null;
};

type WarmupStats = {
  active_accounts: number;
  active_sessions: number;
  messages_today: number;
  sessions_completed_today: number;
};

// ============= Level → daily cap mapping (frontend only, per spec §4) =============
function capForLevel(level: number): number {
  if (level <= 1) return 10;
  if (level === 2) return 25;
  if (level === 3) return 50;
  if (level === 4) return 80;
  return 120;
}

function WarmupPage() {
  const qc = useQueryClient();

  const settingsQ = useQuery({
    queryKey: ["warmup-settings"],
    queryFn: () => api<WarmupSettings>("/api/v1/warmup/settings"),
  });
  const poolQ = useQuery({
    queryKey: ["warmup-pool"],
    queryFn: () => api<{ senders: PoolSender[] }>("/api/v1/warmup/pool"),
    refetchInterval: 30000,
  });
  const statsQ = useQuery({
    queryKey: ["warmup-stats"],
    queryFn: () => api<WarmupStats>("/api/v1/warmup/stats"),
    refetchInterval: 30000,
  });

  const settings = settingsQ.data;
  const enabled = settings?.enabled ?? false;
  const senders = poolQ.data?.senders ?? [];
  const inPool = senders.filter((s) => s.in_pool);
  const outOfPool = senders.filter((s) => !s.in_pool);
  const stats = statsQ.data;

  const [confirmDisable, setConfirmDisable] = useState(false);

  const saveSettingsMut = useMutation({
    mutationFn: (patch: Partial<WarmupSettings>) =>
      api<{ status: string; settings: WarmupSettings }>("/api/v1/warmup/settings", {
        method: "PUT",
        body: { ...settings, ...patch } as Record<string, unknown>,
      }),
    onSuccess: (res) => {
      qc.setQueryData(["warmup-settings"], res.settings);
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Couldn't save settings"),
  });

  const toggleMaster = (next: boolean) => {
    if (!next) {
      setConfirmDisable(true);
      return;
    }
    saveSettingsMut.mutate(
      { enabled: true },
      {
        onSuccess: () => toast.success("Warmup enabled"),
      },
    );
  };

  const confirmDisableMaster = () => {
    saveSettingsMut.mutate(
      { enabled: false },
      {
        onSuccess: () => {
          toast.success("Warmup disabled");
          setConfirmDisable(false);
        },
      },
    );
  };

  const loading = settingsQ.isLoading || poolQ.isLoading || statsQ.isLoading;
  const errorObj = settingsQ.error || poolQ.error || statsQ.error;
  const errorMsg = errorObj instanceof ApiError ? errorObj.message : null;

  return (
    <>
      <Topbar
        title="Account Warmup"
        crumbs={[
          {
            label:
              "Accounts chat with each other via AI — building activity without ban risk",
          },
        ]}
        right={
          <>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 12px",
                borderRadius: 8,
                background: "var(--bg-soft)",
                border: "1px solid var(--border)",
              }}
            >
              <Switch
                checked={enabled}
                onCheckedChange={toggleMaster}
                disabled={saveSettingsMut.isPending || settingsQ.isLoading}
                aria-label="Warmup master toggle"
              />
              <span
                className="text-sm fw5"
                style={{ color: enabled ? "var(--success)" : "var(--text-muted)" }}
              >
                {enabled ? "Warmup on" : "Warmup off"}
              </span>
            </div>
            {!enabled && (
              <button
                className="btn btn--primary btn--sm"
                onClick={() => toggleMaster(true)}
                disabled={saveSettingsMut.isPending}
              >
                <Power size={14} /> Enable warmup
              </button>
            )}
          </>
        }
      />

      <div
        className="scroll"
        style={{ flex: 1, padding: 24, background: "var(--bg-soft)" }}
      >
        {errorMsg && (
          <div
            className="card"
            style={{ marginBottom: 16, padding: 14, color: "var(--danger)" }}
          >
            <AlertCircle size={14} style={{ display: "inline", marginRight: 6 }} />
            Couldn't load warmup. Refresh the page or try again later.
            <div className="muted text-xs" style={{ marginTop: 4 }}>
              {errorMsg}
            </div>
          </div>
        )}

        {/* Mini metrics */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 12,
            marginBottom: 16,
          }}
        >
          <MiniMetric
            label="In warmup"
            value={stats?.active_accounts ?? 0}
            sub="Accounts in pool"
            color="var(--tg-blue)"
          />
          <MiniMetric
            label="Active sessions"
            value={stats?.active_sessions ?? 0}
            sub="Running right now"
            color="var(--success)"
          />
          <MiniMetric
            label="Messages today"
            value={stats?.messages_today ?? 0}
            sub="In the last 24h"
            color="var(--text)"
          />
          <MiniMetric
            label="Sessions completed today"
            value={stats?.sessions_completed_today ?? 0}
            sub="Closed dialogs"
            color="var(--text-muted)"
          />
        </div>

        {/* Pool card */}
        <div className="card" style={{ marginBottom: 16 }}>
          <div
            className="card__head"
            style={{
              padding: "14px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div>
              <h3 style={{ fontSize: 14, fontWeight: 600 }}>Warmup pool</h3>
              <div className="muted text-xs" style={{ marginTop: 2 }}>
                Warmup runs 09:00–20:00 MSK · An account can warm up and run in a
                campaign at the same time
              </div>
            </div>
          </div>

          {loading && inPool.length === 0 ? (
            <div className="card__body muted">Loading pool…</div>
          ) : inPool.length === 0 ? (
            <EmptyPool
              hasAccounts={senders.length > 0}
              outOfPool={outOfPool}
              enabled={enabled}
            />
          ) : (
            <>
              {!enabled && (
                <div
                  className="muted text-sm"
                  style={{
                    padding: "10px 16px",
                    background: "var(--warning-soft)",
                    color: "#a86200",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  Warmup is off. Turn it on for accounts to start building activity.
                </div>
              )}
              <TooltipProvider delayDuration={150}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th>Status</th>
                      <th>Level</th>
                      <th>Today</th>
                      <th style={{ width: 60 }} aria-label="actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {inPool.map((s) => (
                      <PoolRow key={s.id} sender={s} />
                    ))}
                  </tbody>
                </table>
              </TooltipProvider>
            </>
          )}

          {outOfPool.length > 0 && (
            <div
              style={{
                borderTop: "1px solid var(--border)",
                padding: 16,
              }}
            >
              <div className="text-sm fw5" style={{ marginBottom: 8 }}>
                Not in warmup ({outOfPool.length})
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {outOfPool.map((s) => (
                  <AddRow key={s.id} sender={s} />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Settings card */}
        {settings && <SettingsCard settings={settings} />}
      </div>

      <AlertDialog open={confirmDisable} onOpenChange={setConfirmDisable}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disable warmup?</AlertDialogTitle>
            <AlertDialogDescription>
              All accounts will stop building activity until you enable warmup again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDisableMaster}
              style={{ background: "var(--danger)", color: "white" }}
            >
              Disable warmup
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ============= Mini metric (cloned from accounts.tsx) =============
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
      <span
        className="num"
        style={{ fontSize: 24, fontWeight: 600, color, lineHeight: 1.1 }}
      >
        {value}
      </span>
      <span className="muted text-xs">{sub}</span>
    </div>
  );
}

function EmptyPool({
  hasAccounts,
  outOfPool,
  enabled,
}: {
  hasAccounts: boolean;
  outOfPool: PoolSender[];
  enabled: boolean;
}) {
  return (
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
        <Flame size={24} />
      </div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
        Warmup pool is empty
      </h3>
      <p
        className="muted"
        style={{ fontSize: 13, maxWidth: 420, margin: "0 auto 8px" }}
      >
        Add Telegram accounts to warmup — they'll safely chat with each other via AI.
        Warmup is isolated from outreach: it won't touch your campaigns or burn limits.
      </p>
      {!enabled && hasAccounts && outOfPool.length > 0 && (
        <p className="muted text-xs" style={{ marginTop: 8 }}>
          Tip: turn on the master toggle above and add accounts from the list below.
        </p>
      )}
      {!hasAccounts && (
        <a className="btn btn--primary" href="/accounts">
          <Plus size={14} /> Add account
        </a>
      )}
    </div>
  );
}

function AddRow({ sender }: { sender: PoolSender }) {
  const qc = useQueryClient();
  const addMut = useMutation({
    mutationFn: () =>
      api(`/api/v1/warmup/pool/${sender.id}`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Account added to warmup");
      qc.invalidateQueries({ queryKey: ["warmup-pool"] });
      qc.invalidateQueries({ queryKey: ["warmup-stats"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Couldn't add account"),
  });
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 10px",
        borderRadius: 8,
        background: "var(--bg-soft)",
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div
          className="avatar avatar--sm"
          style={{ background: "var(--tg-blue-soft)", color: "var(--tg-blue)" }}
        >
          {(sender.name || sender.phone).slice(0, 1).toUpperCase()}
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <span className="fw5 text-sm">{sender.name || sender.phone}</span>
          <span className="muted text-xs mono">{sender.phone}</span>
        </div>
      </div>
      <button
        className="btn btn--ghost btn--sm"
        onClick={() => addMut.mutate()}
        disabled={addMut.isPending}
      >
        <Plus size={14} /> Add to warmup
      </button>
    </div>
  );
}

function PoolRow({ sender }: { sender: PoolSender }) {
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const toggleMut = useMutation({
    mutationFn: () =>
      api<{ sender_id: string; warmup_active: boolean }>(
        `/api/v1/warmup/pool/${sender.id}/toggle`,
        { method: "PATCH" },
      ),
    onSuccess: (res) => {
      toast.success(res.warmup_active ? "Warmup resumed" : "Warmup paused");
      qc.invalidateQueries({ queryKey: ["warmup-pool"] });
      qc.invalidateQueries({ queryKey: ["warmup-stats"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Couldn't update"),
  });

  const removeMut = useMutation({
    mutationFn: () => api(`/api/v1/warmup/pool/${sender.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Account removed from warmup");
      qc.invalidateQueries({ queryKey: ["warmup-pool"] });
      qc.invalidateQueries({ queryKey: ["warmup-stats"] });
      setConfirmRemove(false);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Couldn't remove account"),
  });

  const cap = capForLevel(sender.level);
  const restricted = !!sender.warmup_reason;
  const restrictionTone =
    sender.restriction_status === "spam_limited"
      ? { pill: "pill--orange", dot: "var(--warning)" }
      : { pill: "pill--red", dot: "var(--danger)" };

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
                background: restricted
                  ? restrictionTone.dot
                  : sender.warmup_active
                    ? "var(--success)"
                    : "var(--text-muted)",
                border: "2px solid var(--bg)",
              }}
            />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="fw5">
              {sender.name || sender.phone}{" "}
              <span
                className="pill pill--purple"
                style={{ marginLeft: 6, fontSize: 10, padding: "1px 6px" }}
                title="Content is AI-generated"
              >
                <Sparkles size={10} /> AI
              </span>
            </span>
            <span className="muted text-xs mono">{sender.phone}</span>
          </div>
        </div>
      </td>
      <td>
        {restricted ? (
          <>
            <span className={`pill ${restrictionTone.pill}`}>
              <ShieldAlert size={11} /> Restricted
            </span>
            <div
              className="text-xs"
              style={{ marginTop: 4, color: "var(--danger)", maxWidth: 280 }}
            >
              {sender.warmup_reason}
            </div>
          </>
        ) : sender.warmup_active ? (
          <span className="pill pill--green">
            <span className="pill__dot" /> Active
          </span>
        ) : (
          <span className="pill pill--ghost">
            <span className="pill__dot" /> Paused
          </span>
        )}
      </td>
      <td>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 200 }}>
          <Progress value={(sender.level / 5) * 100} className="h-1.5" />
          <span className="muted text-xs">
            Level {sender.level} of 5 · {sender.sent_today}/{cap} messages today ·{" "}
            {sender.enrolled_days}d in warmup
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="muted text-xs"
                style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "help" }}
              >
                <Info size={10} /> auto intensity
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[280px] text-left leading-relaxed">
              Intensity ramps up automatically over days — manual tuning is disabled for
              safety.
            </TooltipContent>
          </Tooltip>
        </div>
      </td>
      <td className="num mono text-sm">
        {sender.sent_today}/{cap}
      </td>
      <td style={{ textAlign: "right", position: "relative" }}>
        <button
          className="tb__icon-btn"
          aria-label="Actions"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <MoreHorizontal size={16} />
        </button>
        {menuOpen && (
          <>
            <div className="ob__menuScrim" onClick={() => setMenuOpen(false)} />
            <div className="ob__menu" role="menu">
              <button
                onClick={() => {
                  setMenuOpen(false);
                  toggleMut.mutate();
                }}
              >
                {sender.warmup_active ? (
                  <>
                    <Pause size={14} /> Pause
                  </>
                ) : (
                  <>
                    <Play size={14} /> Resume
                  </>
                )}
              </button>
              <button
                style={{ color: "var(--danger)" }}
                onClick={() => {
                  setMenuOpen(false);
                  setConfirmRemove(true);
                }}
              >
                <Trash2 size={14} /> Remove from warmup
              </button>
            </div>
          </>
        )}

        <AlertDialog open={confirmRemove} onOpenChange={setConfirmRemove}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Remove account from warmup?</AlertDialogTitle>
              <AlertDialogDescription>
                The account will stop chatting with others. This won't delete its warmup
                history.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => removeMut.mutate()}
                style={{ background: "var(--danger)", color: "white" }}
              >
                Remove account
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </td>
    </tr>
  );
}

function SettingsCard({ settings }: { settings: WarmupSettings }) {
  const qc = useQueryClient();
  const [topicsText, setTopicsText] = useState(settings.topics.join("\n"));
  const [systemPrompt, setSystemPrompt] = useState(settings.system_prompt);

  useEffect(() => {
    setTopicsText(settings.topics.join("\n"));
    setSystemPrompt(settings.system_prompt);
  }, [settings.topics, settings.system_prompt]);

  const saveMut = useMutation({
    mutationFn: () => {
      const topics = topicsText
        .split("\n")
        .map((t) => t.trim())
        .filter(Boolean);
      return api<{ status: string; settings: WarmupSettings }>(
        "/api/v1/warmup/settings",
        {
          method: "PUT",
          body: {
            enabled: settings.enabled,
            topics,
            system_prompt: systemPrompt,
            language: settings.language,
            tone: settings.tone,
          },
        },
      );
    },
    onSuccess: (res) => {
      qc.setQueryData(["warmup-settings"], res.settings);
      toast.success("Warmup settings saved");
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Couldn't save settings"),
  });

  return (
    <div className="card">
      <div
        className="card__head"
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <h3 style={{ fontSize: 14, fontWeight: 600 }}>Warmup settings</h3>
        <div className="muted text-xs" style={{ marginTop: 2 }}>
          Defaults to 24 built-in Russian-language topics
        </div>
      </div>
      <div
        className="card__body"
        style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label className="text-sm fw5">Conversation topics (one per line)</label>
          <Textarea
            value={topicsText}
            onChange={(e) => setTopicsText(e.target.value)}
            rows={8}
            placeholder="weekend plans&#10;favorite TV show&#10;..."
          />
          <span className="muted text-xs">
            Leave empty to restore the 24 default Russian-language topics.
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label className="text-sm fw5">AI system prompt</label>
          <Textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={5}
          />
        </div>
        <div>
          <button
            className="btn btn--primary"
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending}
          >
            {saveMut.isPending ? "Saving…" : "Save settings"}
          </button>
        </div>
      </div>
    </div>
  );
}
