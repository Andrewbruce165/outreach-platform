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
      toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить настройки"),
  });

  const toggleMaster = (next: boolean) => {
    if (!next) {
      setConfirmDisable(true);
      return;
    }
    saveSettingsMut.mutate(
      { enabled: true },
      {
        onSuccess: () => toast.success("Прогрев включён"),
      },
    );
  };

  const confirmDisableMaster = () => {
    saveSettingsMut.mutate(
      { enabled: false },
      {
        onSuccess: () => {
          toast.success("Прогрев выключен");
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
        title="Прогрев аккаунтов"
        crumbs={[
          {
            label:
              "Аккаунты переписываются между собой через AI — набирают активность без риска бана",
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
                aria-label="Master toggle прогрева"
              />
              <span
                className="text-sm fw5"
                style={{ color: enabled ? "var(--success)" : "var(--text-muted)" }}
              >
                {enabled ? "Прогрев включён" : "Прогрев выключен"}
              </span>
            </div>
            {!enabled && (
              <button
                className="btn btn--primary btn--sm"
                onClick={() => toggleMaster(true)}
                disabled={saveSettingsMut.isPending}
              >
                <Power size={14} /> Включить прогрев
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
            Не удалось загрузить прогрев. Обновите страницу или попробуйте позже.
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
            label="В прогреве"
            value={stats?.active_accounts ?? 0}
            sub="Аккаунтов в пуле"
            color="var(--tg-blue)"
          />
          <MiniMetric
            label="Активные сессии"
            value={stats?.active_sessions ?? 0}
            sub="Идут прямо сейчас"
            color="var(--success)"
          />
          <MiniMetric
            label="Сообщений сегодня"
            value={stats?.messages_today ?? 0}
            sub="За последние 24 ч"
            color="var(--text)"
          />
          <MiniMetric
            label="Сессий завершено сегодня"
            value={stats?.sessions_completed_today ?? 0}
            sub="Закрытых диалогов"
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
              <h3 style={{ fontSize: 14, fontWeight: 600 }}>Пул прогрева</h3>
              <div className="muted text-xs" style={{ marginTop: 2 }}>
                Прогрев работает 09:00–20:00 МСК · Аккаунт может одновременно греться и
                работать в кампании
              </div>
            </div>
          </div>

          {loading && inPool.length === 0 ? (
            <div className="card__body muted">Загружаем пул…</div>
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
                  Прогрев выключен. Включите его, чтобы аккаунты начали набирать
                  активность.
                </div>
              )}
              <TooltipProvider delayDuration={150}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Аккаунт</th>
                      <th>Статус</th>
                      <th>Уровень</th>
                      <th>Сегодня</th>
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
                Не в прогреве ({outOfPool.length})
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
            <AlertDialogTitle>Выключить прогрев?</AlertDialogTitle>
            <AlertDialogDescription>
              Все аккаунты перестанут набирать активность, пока вы снова не включите
              прогрев.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDisableMaster}
              style={{ background: "var(--danger)", color: "white" }}
            >
              Выключить прогрев
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
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>Пул прогрева пуст</h3>
      <p
        className="muted"
        style={{ fontSize: 13, maxWidth: 420, margin: "0 auto 8px" }}
      >
        Добавьте Telegram-аккаунты в прогрев — они начнут безопасно переписываться между
        собой через AI. Прогрев изолирован от рассылок: он не трогает ваши кампании и не
        жжёт лимиты.
      </p>
      {!enabled && hasAccounts && outOfPool.length > 0 && (
        <p className="muted text-xs" style={{ marginTop: 8 }}>
          Подсказка: включите мастер-переключатель сверху и добавьте аккаунты из списка
          ниже.
        </p>
      )}
      {!hasAccounts && (
        <a className="btn btn--primary" href="/accounts">
          <Plus size={14} /> Добавить аккаунт
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
      toast.success("Аккаунт добавлен в прогрев");
      qc.invalidateQueries({ queryKey: ["warmup-pool"] });
      qc.invalidateQueries({ queryKey: ["warmup-stats"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Не удалось добавить"),
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
        <Plus size={14} /> Добавить в прогрев
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
      toast.success(res.warmup_active ? "Прогрев возобновлён" : "Прогрев на паузе");
      qc.invalidateQueries({ queryKey: ["warmup-pool"] });
      qc.invalidateQueries({ queryKey: ["warmup-stats"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Не удалось изменить"),
  });

  const removeMut = useMutation({
    mutationFn: () => api(`/api/v1/warmup/pool/${sender.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Аккаунт убран из прогрева");
      qc.invalidateQueries({ queryKey: ["warmup-pool"] });
      qc.invalidateQueries({ queryKey: ["warmup-stats"] });
      setConfirmRemove(false);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось убрать"),
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
                title="Контент генерируется AI"
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
              <ShieldAlert size={11} /> Ограничен
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
            <span className="pill__dot" /> Активен
          </span>
        ) : (
          <span className="pill pill--ghost">
            <span className="pill__dot" /> На паузе
          </span>
        )}
      </td>
      <td>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 200 }}>
          <Progress value={(sender.level / 5) * 100} className="h-1.5" />
          <span className="muted text-xs">
            Уровень {sender.level} из 5 · {sender.sent_today}/{cap} сообщений сегодня · в
            прогреве {sender.enrolled_days} дн.
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="muted text-xs"
                style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "help" }}
              >
                <Info size={10} /> авто-интенсивность
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[280px] text-left leading-relaxed">
              Интенсивность растёт автоматически по дням — ручная настройка отключена для
              безопасности.
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
          aria-label="Действия"
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
                    <Pause size={14} /> Поставить на паузу
                  </>
                ) : (
                  <>
                    <Play size={14} /> Возобновить
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
                <Trash2 size={14} /> Убрать из прогрева
              </button>
            </div>
          </>
        )}

        <AlertDialog open={confirmRemove} onOpenChange={setConfirmRemove}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Убрать аккаунт из прогрева?</AlertDialogTitle>
              <AlertDialogDescription>
                Аккаунт перестанет переписываться с другими. Его историю прогрева это не
                удалит.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => removeMut.mutate()}
                style={{ background: "var(--danger)", color: "white" }}
              >
                Убрать аккаунт
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
      toast.success("Настройки прогрева сохранены");
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить настройки"),
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
        <h3 style={{ fontSize: 14, fontWeight: 600 }}>Настройки прогрева</h3>
        <div className="muted text-xs" style={{ marginTop: 2 }}>
          По умолчанию используются 24 русскоязычные темы
        </div>
      </div>
      <div
        className="card__body"
        style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label className="text-sm fw5">Темы для переписок (по одной на строке)</label>
          <Textarea
            value={topicsText}
            onChange={(e) => setTopicsText(e.target.value)}
            rows={8}
            placeholder="планы на выходные&#10;любимый сериал&#10;..."
          />
          <span className="muted text-xs">
            Оставьте поле пустым — вернутся 24 русскоязычные темы по умолчанию.
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label className="text-sm fw5">System prompt для AI</label>
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
            {saveMut.isPending ? "Сохраняем…" : "Сохранить настройки"}
          </button>
        </div>
      </div>
    </div>
  );
}
