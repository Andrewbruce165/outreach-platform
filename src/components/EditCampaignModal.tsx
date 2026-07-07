import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar, Check, Flag, MousePointerClick, Smile, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { StageEditor } from "@/components/StageEditor";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type CampaignUpdate = components["schemas"]["CampaignUpdate"];
// Phase 12 NDLG-06: PATCH now returns {campaign, warnings[]}
type CampaignWriteResponse = components["schemas"]["CampaignWriteResponse"];
type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];

const DAYS: Array<{ id: string; label: string; bit: number }> = [
  { id: "mon", label: "M", bit: 1 },
  { id: "tue", label: "T", bit: 2 },
  { id: "wed", label: "W", bit: 4 },
  { id: "thu", label: "T", bit: 8 },
  { id: "fri", label: "F", bit: 16 },
  { id: "sat", label: "S", bit: 32 },
  { id: "sun", label: "S", bit: 64 },
];

const GOAL_OPTIONS = [
  { id: "book_meeting", label: "Book a meeting", desc: "Calendar invite confirmed", Icon: Calendar },
  { id: "qualify", label: "Qualify the lead", desc: "Budget · timeline · authority", Icon: Flag },
  { id: "click", label: "Get a click", desc: "Visit link / sign up", Icon: MousePointerClick },
  { id: "engage", label: "Engage", desc: "Warm 5+ msg conversation", Icon: Smile },
] as const;

function maskFromDays(ids: string[]): number {
  return ids.reduce((m, id) => m | (DAYS.find((d) => d.id === id)?.bit ?? 0), 0);
}
function daysFromMask(mask: number): string[] {
  return DAYS.filter((d) => (mask & d.bit) !== 0).map((d) => d.id);
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function toDateInput(v?: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

const sectionTitle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--text-muted)",
  paddingTop: 20,
  borderTop: "1px solid var(--border)",
  marginTop: 6,
};

export function EditCampaignModal({
  campaign,
  onClose,
}: {
  campaign: Campaign;
  onClose: () => void;
}) {
  const qc = useQueryClient();

  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<{ agents: Agent[]; total: number }>("/api/v1/agents"),
    staleTime: 60_000,
  });
  const foldersQ = useQuery({
    queryKey: ["folders"],
    queryFn: () => api<Folder[]>("/api/v1/folders"),
    staleTime: 60_000,
  });

  const [name, setName] = useState(campaign.name);
  const [description, setDescription] = useState(campaign.description ?? "");
  const [agentId, setAgentId] = useState(campaign.agent_id);
  const [folderId, setFolderId] = useState(campaign.folder_id);
  const [messageTemplate, setMessageTemplate] = useState(campaign.message_template ?? "");
  const [tz, setTz] = useState(campaign.timezone ?? "UTC");
  const [hourStart, setHourStart] = useState(campaign.work_hour_start ?? 9);
  const [hourEnd, setHourEnd] = useState(campaign.work_hour_end ?? 20);
  const [days, setDays] = useState<string[]>(daysFromMask(campaign.work_days_mask ?? 0));
  const [allowRecontact, setAllowRecontact] = useState(campaign.allow_recontact ?? false);
  const [recontactMinAgeDays, setRecontactMinAgeDays] = useState(campaign.recontact_min_age_days ?? 30);
  // Phase 12 NDLG-06: per-account daily new-dialog cap
  const [maxNewDialogsPerDay, setMaxNewDialogsPerDay] = useState(campaign.max_new_dialogs_per_day ?? 10);
  // Phase 19 NORP-13: no-reply follow-up + auto-finish (D-08/D-12).
  const [followUpEnabled, setFollowUpEnabled] = useState(campaign.follow_up_enabled ?? false);
  const [followUpIntervalHours, setFollowUpIntervalHours] = useState(campaign.follow_up_interval_hours ?? 24);
  const [followUpMaxPings, setFollowUpMaxPings] = useState(campaign.follow_up_max_pings ?? 2);
  const [autoFinishHours, setAutoFinishHours] = useState(campaign.auto_finish_hours ?? 72);
  const [startDate, setStartDate] = useState(toDateInput(campaign.start_date));
  const [stopDate, setStopDate] = useState(toDateInput(campaign.stop_date));
  const [audienceHints, setAudienceHints] = useState(campaign.audience_hints ?? "");
  const [primaryGoal, setPrimaryGoal] = useState<string>(campaign.primary_goal ?? "");
  const [dialogueFlow, setDialogueFlow] = useState<Array<{ title: string; instruction: string }>>(
    (campaign.dialogue_flow ?? []).map((s) => ({
      title: (s as { title?: string }).title ?? "",
      instruction: (s as { instruction?: string }).instruction ?? "",
    })),
  );
  const [argumentsFacts, setArgumentsFacts] = useState(campaign.arguments_facts ?? "");
  const [campaignRules, setCampaignRules] = useState(campaign.campaign_rules ?? "");
  const [leadHint, setLeadHint] = useState(campaign.lead_trigger_hint ?? "");
  const [handoffHint, setHandoffHint] = useState(campaign.handoff_trigger_hint ?? "");
  const [finishHint, setFinishHint] = useState(campaign.finish_trigger_hint ?? "");
  const [webhookUrl, setWebhookUrl] = useState(campaign.webhook_url ?? "");
  const [leadWebhook, setLeadWebhook] = useState(campaign.lead_webhook_url ?? "");
  const [handoffWebhook, setHandoffWebhook] = useState(campaign.handoff_webhook_url ?? "");
  const [finishWebhook, setFinishWebhook] = useState(campaign.finish_webhook_url ?? "");

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const saveMut = useMutation({
    mutationFn: () => {
      const next = {
        name,
        description: description || null,
        agent_id: agentId,
        folder_id: folderId,
        message_template: messageTemplate,
        timezone: tz,
        work_hour_start: hourStart,
        work_hour_end: hourEnd,
        work_days_mask: maskFromDays(days),
        allow_recontact: allowRecontact,
        recontact_min_age_days: recontactMinAgeDays,
        max_new_dialogs_per_day: maxNewDialogsPerDay,
        follow_up_enabled: followUpEnabled,
        follow_up_interval_hours: followUpIntervalHours,
        follow_up_max_pings: followUpMaxPings,
        auto_finish_hours: autoFinishHours,
        start_date: startDate ? new Date(startDate).toISOString() : null,
        stop_date: stopDate ? new Date(stopDate).toISOString() : null,
        audience_hints: audienceHints || null,
        primary_goal: (primaryGoal || null) as CampaignUpdate["primary_goal"],
        dialogue_flow:
          dialogueFlow.filter((s) => s.instruction.trim().length > 0).length > 0
            ? dialogueFlow.filter((s) => s.instruction.trim().length > 0)
            : null,
        arguments_facts: argumentsFacts || null,
        campaign_rules: campaignRules || null,
        lead_trigger_hint: leadHint || null,
        handoff_trigger_hint: handoffHint || null,
        finish_trigger_hint: finishHint || null,
        webhook_url: webhookUrl || null,
        lead_webhook_url: leadWebhook || null,
        handoff_webhook_url: handoffWebhook || null,
        finish_webhook_url: finishWebhook || null,
      };

      const norm = (v: unknown): unknown => {
        if (v === undefined || v === "") return null;
        return v;
      };
      const origDate = (v?: string | null) => (v ? new Date(v).toISOString() : null);

      const original: Record<string, unknown> = {
        name: campaign.name,
        description: campaign.description ?? null,
        agent_id: campaign.agent_id,
        folder_id: campaign.folder_id,
        message_template: campaign.message_template ?? null,
        timezone: campaign.timezone ?? null,
        work_hour_start: campaign.work_hour_start ?? null,
        work_hour_end: campaign.work_hour_end ?? null,
        work_days_mask: campaign.work_days_mask ?? null,
        allow_recontact: campaign.allow_recontact ?? false,
        recontact_min_age_days: campaign.recontact_min_age_days ?? 30,
        max_new_dialogs_per_day: campaign.max_new_dialogs_per_day ?? 10,
        follow_up_enabled: campaign.follow_up_enabled ?? false,
        follow_up_interval_hours: campaign.follow_up_interval_hours ?? 24,
        follow_up_max_pings: campaign.follow_up_max_pings ?? 2,
        auto_finish_hours: campaign.auto_finish_hours ?? 72,
        start_date: origDate(campaign.start_date),
        stop_date: origDate(campaign.stop_date),
        audience_hints: campaign.audience_hints ?? null,
        primary_goal: campaign.primary_goal ?? null,
        dialogue_flow: (campaign.dialogue_flow ?? null) as unknown as null,
        arguments_facts: campaign.arguments_facts ?? null,
        campaign_rules: campaign.campaign_rules ?? null,
        lead_trigger_hint: campaign.lead_trigger_hint ?? null,
        handoff_trigger_hint: campaign.handoff_trigger_hint ?? null,
        finish_trigger_hint: campaign.finish_trigger_hint ?? null,
        webhook_url: campaign.webhook_url ?? null,
        lead_webhook_url: campaign.lead_webhook_url ?? null,
        handoff_webhook_url: campaign.handoff_webhook_url ?? null,
        finish_webhook_url: campaign.finish_webhook_url ?? null,
      };

      const body: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(next)) {
        if (norm(v) !== norm(original[k])) body[k] = v;
      }

      // Phase 12 NDLG-06: response is now {campaign, warnings[]}; we only need it to settle.
      return api<CampaignWriteResponse>(`/api/v1/campaigns/${campaign.id}`, {
        method: "PATCH",
        body,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
      void qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
      onClose();
    },
    onError: (e) => setError(errMsg(e)),
  });

  const agents = agentsQ.data?.agents ?? [];
  const folders = foldersQ.data ?? [];

  const tzOptions = useMemo(
    () =>
      Array.from(
        new Set([
          tz,
          "UTC",
          "America/New_York",
          "America/Chicago",
          "America/Los_Angeles",
          "Europe/London",
          "Europe/Berlin",
          "Europe/Moscow",
          "Asia/Dubai",
          "Asia/Singapore",
          "Asia/Tokyo",
        ]),
      ),
    [tz],
  );

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" aria-label="Edit campaign" onClick={onClose}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()} style={{ maxHeight: "90vh", display: "flex", flexDirection: "column" }}>
        <header className="modal__head">
          <div>
            <h3>Edit campaign</h3>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {campaign.name}
            </div>
          </div>
          <button className="tb__icon-btn" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </header>

        <div className="modal__body scroll" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Section 1 — basics */}
          <div className="field">
            <label className="field__label">Name</label>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>

          <div className="field">
            <label className="field__label">Description / brief</label>
            <textarea
              className="textarea"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {/* Section 2 — Agent & goal */}
          <div style={sectionTitle}>Agent & goal</div>

          <div className="field">
            <label className="field__label">Agent</label>
            {agents.length === 0 ? (
              <div className="muted text-sm" style={{ padding: 12 }}>No agents yet.</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {agents.map((a) => {
                  const on = agentId === a.id;
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => setAgentId(a.id)}
                      style={{
                        padding: 14,
                        borderRadius: 11,
                        textAlign: "left",
                        border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
                        background: on ? "var(--tg-blue-softer, var(--tg-blue-soft))" : "var(--bg)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div
                          className="avatar avatar--sm"
                          style={{ background: "var(--tg-blue)", color: "white" }}
                        >
                          {(a.name || "?").slice(0, 1).toUpperCase()}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 600, fontSize: 13.5 }}>{a.name}</div>
                          <div className="muted text-xs">{a.tone_preset || "Custom agent"}</div>
                        </div>
                        {on && <Check size={16} style={{ color: "var(--tg-blue)" }} />}
                      </div>
                      {a.system_prompt && (
                        <div
                          className="text-sm muted"
                          style={{
                            lineHeight: 1.45,
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}
                        >
                          {a.system_prompt}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="field">
            <label className="field__label">Folder</label>
            <select className="select" value={folderId ?? ""} onChange={(e) => setFolderId(e.target.value)}>
              <option value="">— Select folder —</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name} · {f.contact_count.toLocaleString()} contacts
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="field__label">Primary goal</label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {GOAL_OPTIONS.map((g) => {
                const on = primaryGoal === g.id;
                const Icon = g.Icon;
                return (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => setPrimaryGoal(on ? "" : g.id)}
                    style={{
                      padding: "10px 12px",
                      borderRadius: 10,
                      textAlign: "left",
                      display: "flex",
                      gap: 10,
                      alignItems: "center",
                      border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
                      background: on ? "var(--tg-blue-softer, var(--tg-blue-soft))" : "var(--bg)",
                      cursor: "pointer",
                    }}
                  >
                    <div
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: 8,
                        background: on ? "var(--tg-blue)" : "var(--bg-soft)",
                        color: on ? "white" : "var(--text-muted)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <Icon size={13} />
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>{g.label}</div>
                      <div className="muted text-xs">{g.desc}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="field">
            <label className="field__label">Who are we writing to</label>
            <textarea
              className="textarea"
              rows={2}
              value={audienceHints}
              onChange={(e) => setAudienceHints(e.target.value)}
            />
          </div>

          {/* Section 3 — AI behaviour */}
          <div style={sectionTitle}>AI behaviour</div>

          <div className="field">
            <label className="field__label">Conversation flow</label>
            <StageEditor stages={dialogueFlow} onChange={setDialogueFlow} />
          </div>

          <div className="field">
            <label className="field__label">Arguments &amp; facts</label>
            <textarea
              className="textarea"
              rows={3}
              value={argumentsFacts}
              onChange={(e) => setArgumentsFacts(e.target.value)}
              placeholder="Факты о продукте, ответы на типичные возражения."
            />
            <span className="field__hint">
              ИИ использует только эти факты и не выдумывает остальное.
            </span>
          </div>

          <div className="field">
            <label className="field__label">Campaign rules</label>
            <textarea
              className="textarea"
              rows={2}
              value={campaignRules}
              onChange={(e) => setCampaignRules(e.target.value)}
              placeholder="Запреты и правила, специфичные для этой кампании."
            />
          </div>

          {/* Section 4 — Schedule */}
          <div style={sectionTitle}>Schedule</div>

          <div className="field">
            <label className="field__label">Message template</label>
            <textarea
              className="textarea"
              rows={3}
              value={messageTemplate}
              onChange={(e) => setMessageTemplate(e.target.value)}
              placeholder="Hi {{first_name}}!"
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
            <div className="field">
              <label className="field__label">Timezone</label>
              <select className="select" value={tz} onChange={(e) => setTz(e.target.value)}>
                {tzOptions.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field__label">Start hour</label>
              <input
                className="input"
                type="number"
                min={0}
                max={23}
                value={hourStart}
                onChange={(e) => setHourStart(Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label className="field__label">End hour</label>
              <input
                className="input"
                type="number"
                min={0}
                max={23}
                value={hourEnd}
                onChange={(e) => setHourEnd(Number(e.target.value))}
              />
            </div>
          </div>

          <div className="field">
            <label className="field__label">Work days</label>
            <div style={{ display: "flex", gap: 6 }}>
              {DAYS.map((d) => {
                const on = days.includes(d.id);
                return (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() =>
                      setDays((prev) =>
                        prev.includes(d.id)
                          ? prev.filter((x) => x !== d.id)
                          : [...prev, d.id],
                      )
                    }
                    style={{
                      width: 34,
                      height: 34,
                      borderRadius: 8,
                      border: "1px solid var(--border)",
                      background: on ? "var(--tg-blue)" : "var(--bg-soft)",
                      color: on ? "white" : "var(--text)",
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    {d.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="field">
            <label
              className="field__label"
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
            >
              <span>Re-contact closed conversations</span>
              <button
                type="button"
                role="switch"
                aria-checked={allowRecontact}
                onClick={() => setAllowRecontact(!allowRecontact)}
                style={{
                  width: 44,
                  height: 24,
                  borderRadius: 999,
                  background: allowRecontact ? "var(--tg-blue)" : "var(--bg-soft)",
                  position: "relative",
                  border: "1px solid var(--border)",
                  cursor: "pointer",
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    top: 2,
                    left: allowRecontact ? 22 : 2,
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    background: "white",
                    transition: "left 120ms",
                    boxShadow: "0 1px 2px rgba(0,0,0,.2)",
                  }}
                />
              </button>
            </label>
            <span className="field__hint">
              Message contacts whose dialog is finished or long inactive. Live conversations
              (lead / handoff / in progress) are never interrupted.
            </span>
          </div>

          {allowRecontact && (
            <div className="field">
              <label className="field__label">Keep dialogs untouched if active within (days)</label>
              <input
                className="input"
                type="number"
                min={1}
                max={365}
                value={recontactMinAgeDays}
                onChange={(e) => setRecontactMinAgeDays(Number(e.target.value))}
              />
            </div>
          )}

          {/* Phase 12 NDLG-06: per-account daily new-dialog cap */}
          <div className="field">
            <label className="field__label">Новых диалогов в сутки на аккаунт</label>
            <input
              className="input"
              type="number"
              min={1}
              max={100}
              value={maxNewDialogsPerDay}
              onChange={(e) => setMaxNewDialogsPerDay(Number(e.target.value))}
            />
            <span className="field__hint">
              Лимит новых диалогов в сутки для каждого подключённого аккаунта (не на всю кампанию).
            </span>
            {maxNewDialogsPerDay > 50 && (
              <span
                className="field__hint"
                role="alert"
                style={{ color: "var(--warning, var(--danger))", marginTop: 4 }}
              >
                рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана
              </span>
            )}
          </div>

          {/* Phase 19 NORP-13: no-reply follow-up + auto-finish (D-08/D-12). */}
          <div className="field">
            <label
              className="field__label"
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
            >
              <span>Follow Up при отсутствии ответа</span>
              <button
                type="button"
                role="switch"
                aria-checked={followUpEnabled}
                onClick={() => setFollowUpEnabled(!followUpEnabled)}
                style={{
                  width: 44,
                  height: 24,
                  borderRadius: 999,
                  background: followUpEnabled ? "var(--tg-blue)" : "var(--bg-soft)",
                  position: "relative",
                  border: "1px solid var(--border)",
                  cursor: "pointer",
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    top: 2,
                    left: followUpEnabled ? 22 : 2,
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    background: "white",
                    transition: "left 120ms",
                    boxShadow: "0 1px 2px rgba(0,0,0,.2)",
                  }}
                />
              </button>
            </label>
            <span className="field__hint">
              Кому написали и ждём ответа — получают статус «no reply». Если включено, aimly
              пингует их через интервал (с того же аккаунта), пока не ответят или не исчерпаются пинги.
            </span>
          </div>

          {followUpEnabled && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div className="field">
                <label className="field__label">Интервал пинга (часы)</label>
                <input
                  className="input"
                  type="number"
                  min={4}
                  max={168}
                  value={followUpIntervalHours}
                  onChange={(e) => setFollowUpIntervalHours(Number(e.target.value))}
                />
                <span className="field__hint">4–168, по умолчанию 24.</span>
              </div>
              <div className="field">
                <label className="field__label">Максимум пингов</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={5}
                  value={followUpMaxPings}
                  onChange={(e) => setFollowUpMaxPings(Number(e.target.value))}
                />
                <span className="field__hint">1–5, по умолчанию 2.</span>
              </div>
            </div>
          )}

          {/* Auto-finish: closes silent dialogs — sits with finish criteria (CONTEXT.md). */}
          <div className="field">
            <label className="field__label">Авто-финиш без ответа (часы)</label>
            <input
              className="input"
              type="number"
              min={24}
              max={720}
              value={autoFinishHours}
              onChange={(e) => setAutoFinishHours(Number(e.target.value))}
            />
            <span className="field__hint">
              Молчит столько часов — диалог закрывается («finished», в finish-webhook уходит
              reason=&quot;no_reply&quot;). 24–720, по умолчанию 72.
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="field">
              <label className="field__label">Start date</label>
              <input
                className="input"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div className="field">
              <label className="field__label">Stop date</label>
              <input
                className="input"
                type="date"
                value={stopDate}
                onChange={(e) => setStopDate(e.target.value)}
              />
            </div>
          </div>

          {/* Section 5 — Signals & webhooks */}
          <div style={sectionTitle}>Signals &amp; webhooks</div>

          <div className="field">
            <label className="field__label">Lead signal</label>
            <textarea
              className="textarea"
              rows={4}
              value={leadHint}
              onChange={(e) => setLeadHint(e.target.value)}
              placeholder="e.g. The contact agrees to a demo or asks for pricing details."
            />
          </div>
          <div className="field">
            <label className="field__label">Handoff signal</label>
            <textarea
              className="textarea"
              rows={4}
              value={handoffHint}
              onChange={(e) => setHandoffHint(e.target.value)}
              placeholder="e.g. The contact asks a technical or legal question the AI can’t answer."
            />
          </div>
          <div className="field">
            <label className="field__label">Finish signal</label>
            <textarea
              className="textarea"
              rows={4}
              value={finishHint}
              onChange={(e) => setFinishHint(e.target.value)}
              placeholder="e.g. The contact declines, unsubscribes, or the deal is closed."
            />
          </div>

          <div className="field">
            <label className="field__label">Webhook URL</label>
            <input
              className="input"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder="https://…"
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            <div className="field">
              <label className="field__label">Lead webhook</label>
              <input
                className="input"
                value={leadWebhook}
                onChange={(e) => setLeadWebhook(e.target.value)}
              />
            </div>
            <div className="field">
              <label className="field__label">Handoff webhook</label>
              <input
                className="input"
                value={handoffWebhook}
                onChange={(e) => setHandoffWebhook(e.target.value)}
              />
            </div>
            <div className="field">
              <label className="field__label">Finish webhook</label>
              <input
                className="input"
                value={finishWebhook}
                onChange={(e) => setFinishWebhook(e.target.value)}
              />
            </div>
          </div>

          {error && (
            <div
              style={{
                padding: 10,
                background: "var(--danger-soft, #fde2e1)",
                color: "var(--danger)",
                borderRadius: 8,
                fontSize: 13,
              }}
              role="alert"
            >
              {error}
            </div>
          )}
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            padding: "12px 18px",
            borderTop: "1px solid var(--border)",
          }}
        >
          <button className="btn btn--ghost btn--sm" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn--primary btn--sm"
            onClick={() => {
              setError(null);
              saveMut.mutate();
            }}
            disabled={saveMut.isPending || !name.trim()}
          >
            {saveMut.isPending ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
