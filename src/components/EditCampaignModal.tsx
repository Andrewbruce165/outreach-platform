import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type CampaignUpdate = components["schemas"]["CampaignUpdate"];
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

/* ----------------
   Inline stage editor for EditCampaignModal (Phase 11 D-04/D-05).
   Same logic as StageEditor in campaigns.new.tsx; self-contained to avoid cross-file dep.
 ---------------- */
interface StageItem {
  title: string;
  instruction: string;
}

function InlineStageEditor({
  stages,
  onChange,
}: {
  stages: StageItem[];
  onChange: (v: StageItem[]) => void;
}) {
  const inputStyle: React.CSSProperties = {
    width: "100%",
    height: 34,
    padding: "0 10px",
    background: "var(--bg-soft)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    fontSize: 13,
    color: "var(--text)",
    outline: "none",
    boxSizing: "border-box",
  };
  const taStyle: React.CSSProperties = {
    ...inputStyle,
    height: "auto",
    padding: 10,
    resize: "vertical",
    fontFamily: "inherit",
    minHeight: 60,
  };

  const add = () => onChange([...stages, { title: "", instruction: "" }]);
  const remove = (i: number) => onChange(stages.filter((_, k) => k !== i));
  const up = (i: number) => {
    if (i === 0) return;
    const n = [...stages];
    [n[i - 1], n[i]] = [n[i], n[i - 1]];
    onChange(n);
  };
  const down = (i: number) => {
    if (i === stages.length - 1) return;
    const n = [...stages];
    [n[i], n[i + 1]] = [n[i + 1], n[i]];
    onChange(n);
  };
  const update = (i: number, patch: Partial<StageItem>) =>
    onChange(stages.map((s, k) => (k === i ? { ...s, ...patch } : s)));

  const btnBase: React.CSSProperties = {
    width: 26,
    height: 26,
    borderRadius: 6,
    border: "1px solid var(--border)",
    background: "var(--bg)",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    padding: 0,
  };

  return (
    <div>
      {stages.length === 0 ? (
        <div
          style={{
            padding: "18px 14px",
            borderRadius: 9,
            border: "1.5px dashed var(--border)",
            background: "var(--bg-soft)",
            textAlign: "center",
            marginBottom: 10,
            fontSize: 13,
            color: "var(--text-muted)",
          }}
        >
          Ход разговора пока пуст
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
          {stages.map((s, idx) => (
            <div
              key={idx}
              style={{
                padding: 12,
                borderRadius: 9,
                border: "1px solid var(--border)",
                background: "var(--bg-soft)",
                display: "flex",
                gap: 10,
              }}
            >
              <div
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  background: "var(--tg-blue)",
                  color: "white",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 11,
                  fontWeight: 600,
                  flexShrink: 0,
                  marginTop: 6,
                }}
              >
                {idx + 1}
              </div>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
                <input
                  style={inputStyle}
                  placeholder={`Название стадии ${idx + 1}`}
                  value={s.title}
                  onChange={(e) => update(idx, { title: e.target.value })}
                />
                <textarea
                  style={taStyle}
                  rows={2}
                  placeholder="Что должен делать ИИ на этой стадии?"
                  value={s.instruction}
                  onChange={(e) => update(idx, { instruction: e.target.value })}
                />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <button
                  type="button"
                  style={{ ...btnBase, color: idx === 0 ? "var(--text-faint)" : "var(--tg-blue)", cursor: idx === 0 ? "default" : "pointer" }}
                  aria-label="Переместить вверх"
                  disabled={idx === 0}
                  onClick={() => up(idx)}
                >
                  <ChevronUp size={13} />
                </button>
                <button
                  type="button"
                  style={{ ...btnBase, color: idx === stages.length - 1 ? "var(--text-faint)" : "var(--tg-blue)", cursor: idx === stages.length - 1 ? "default" : "pointer" }}
                  aria-label="Переместить вниз"
                  disabled={idx === stages.length - 1}
                  onClick={() => down(idx)}
                >
                  <ChevronDown size={13} />
                </button>
                <button
                  type="button"
                  style={{ ...btnBase, color: "var(--danger)", marginTop: 2 }}
                  aria-label="Удалить стадию"
                  onClick={() => remove(idx)}
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={add}
        disabled={stages.length >= 7}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12.5,
          color: "var(--tg-blue)",
          background: "none",
          border: "none",
          cursor: stages.length >= 7 ? "default" : "pointer",
          padding: "4px 0",
          opacity: stages.length >= 7 ? 0.5 : 1,
        }}
      >
        <Plus size={12} /> Добавить стадию
      </button>
    </div>
  );
}

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
  const [messageTemplate, setMessageTemplate] = useState(
    campaign.message_template ?? "",
  );
  const [tz, setTz] = useState(campaign.timezone ?? "UTC");
  const [hourStart, setHourStart] = useState(campaign.work_hour_start ?? 9);
  const [hourEnd, setHourEnd] = useState(campaign.work_hour_end ?? 20);
  const [days, setDays] = useState<string[]>(
    daysFromMask(campaign.work_days_mask ?? 0),
  );
  // 026: per-campaign re-contact policy.
  const [allowRecontact, setAllowRecontact] = useState(
    campaign.allow_recontact ?? false,
  );
  const [recontactMinAgeDays, setRecontactMinAgeDays] = useState(
    campaign.recontact_min_age_days ?? 30,
  );
  const [startDate, setStartDate] = useState(toDateInput(campaign.start_date));
  const [stopDate, setStopDate] = useState(toDateInput(campaign.stop_date));
  const [audienceHints, setAudienceHints] = useState(
    campaign.audience_hints ?? "",
  );
  const [primaryGoal, setPrimaryGoal] = useState<string>(
    campaign.primary_goal ?? "",
  );
  // Phase 11 D-04/D-05: dialogue_flow stage editor
  const [dialogueFlow, setDialogueFlow] = useState<Array<{ title: string; instruction: string }>>(
    (campaign.dialogue_flow ?? []).map((s) => ({
      title: (s as { title?: string }).title ?? "",
      instruction: (s as { instruction?: string }).instruction ?? "",
    })),
  );
  // Phase 11 D-12: arguments_facts
  const [argumentsFacts, setArgumentsFacts] = useState(campaign.arguments_facts ?? "");
  // Phase 11 D-14: campaign-level rules
  const [campaignRules, setCampaignRules] = useState(campaign.campaign_rules ?? "");
  // Phase 11 D-13: success_criteria removed; lead_trigger_hint is the merged field
  const [leadHint, setLeadHint] = useState(campaign.lead_trigger_hint ?? "");
  const [handoffHint, setHandoffHint] = useState(
    campaign.handoff_trigger_hint ?? "",
  );
  const [finishHint, setFinishHint] = useState(
    campaign.finish_trigger_hint ?? "",
  );
  const [webhookUrl, setWebhookUrl] = useState(campaign.webhook_url ?? "");
  const [leadWebhook, setLeadWebhook] = useState(
    campaign.lead_webhook_url ?? "",
  );
  const [handoffWebhook, setHandoffWebhook] = useState(
    campaign.handoff_webhook_url ?? "",
  );
  const [finishWebhook, setFinishWebhook] = useState(
    campaign.finish_webhook_url ?? "",
  );

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
      // Build a PATCH body containing ONLY fields the user actually changed.
      // Sending unchanged values for immutable-on-running fields like agent_id /
      // folder_id makes the backend reject the whole update with a 409
      // CAMPAIGN_RUNNING_IMMUTABLE_FIELDS even when those fields weren't touched.
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
        start_date: startDate ? new Date(startDate).toISOString() : null,
        stop_date: stopDate ? new Date(stopDate).toISOString() : null,
        audience_hints: audienceHints || null,
        primary_goal: (primaryGoal || null) as CampaignUpdate["primary_goal"],
        // Phase 11 D-04: drop stages with empty instruction before saving
        dialogue_flow: dialogueFlow.filter((s) => s.instruction.trim().length > 0).length > 0
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

      // Compare new value to original campaign value, normalising "" / undefined → null
      // and ISO date strings → YYYY-MM-DD slice (start_date / stop_date come back as
      // full ISO timestamps but are edited as date inputs).
      const norm = (v: unknown): unknown => {
        if (v === undefined || v === "") return null;
        return v;
      };
      const origDate = (v?: string | null) =>
        v ? new Date(v).toISOString() : null;

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

      return api(`/api/v1/campaigns/${campaign.id}`, {
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

  const labelStyle: React.CSSProperties = {
    fontSize: 12,
    color: "var(--text-muted)",
    marginBottom: 4,
    display: "block",
  };
  const inputStyle: React.CSSProperties = {
    width: "100%",
    height: 34,
    padding: "0 10px",
    background: "var(--bg-soft)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    fontSize: 13,
    color: "var(--text)",
    outline: "none",
  };
  const taStyle: React.CSSProperties = {
    ...inputStyle,
    height: "auto",
    padding: 10,
    resize: "vertical",
    fontFamily: "inherit",
  };
  const fieldStyle: React.CSSProperties = { marginBottom: 14 };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Edit campaign"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
        padding: 20,
      }}
    >
      <div
        className="card"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 720,
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          background: "var(--bg)",
          padding: 0,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "16px 20px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>Edit campaign</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              Update settings for {campaign.name}
            </div>
          </div>
          <button
            className="tb__icon-btn"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div style={{ overflow: "auto", padding: 20 }}>
          <div style={fieldStyle}>
            <label style={labelStyle}>Name</label>
            <input
              style={inputStyle}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div style={fieldStyle}>
            <label style={labelStyle}>Description / brief</label>
            <textarea
              style={taStyle}
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div style={fieldStyle}>
              <label style={labelStyle}>Agent</label>
              <select
                style={inputStyle}
                value={agentId ?? ""}
                onChange={(e) => setAgentId(e.target.value)}
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Folder</label>
              <select
                style={inputStyle}
                value={folderId ?? ""}
                onChange={(e) => setFolderId(e.target.value)}
              >
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={fieldStyle}>
            <label style={labelStyle}>Message template</label>
            <textarea
              style={taStyle}
              rows={3}
              value={messageTemplate}
              onChange={(e) => setMessageTemplate(e.target.value)}
              placeholder="Hi {{first_name}}!"
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr", gap: 12 }}>
            <div style={fieldStyle}>
              <label style={labelStyle}>Timezone</label>
              <select
                style={inputStyle}
                value={tz}
                onChange={(e) => setTz(e.target.value)}
              >
                {tzOptions.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Start hour</label>
              <input
                type="number"
                min={0}
                max={23}
                style={inputStyle}
                value={hourStart}
                onChange={(e) => setHourStart(Number(e.target.value))}
              />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>End hour</label>
              <input
                type="number"
                min={0}
                max={23}
                style={inputStyle}
                value={hourEnd}
                onChange={(e) => setHourEnd(Number(e.target.value))}
              />
            </div>
          </div>

          <div style={fieldStyle}>
            <label style={labelStyle}>Work days</label>
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

          <div style={fieldStyle}>
            <label
              style={{ ...labelStyle, display: "flex", alignItems: "center", justifyContent: "space-between" }}
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
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Message contacts whose dialog is finished or long inactive. Live conversations
              (lead / handoff / in progress) are never interrupted.
            </span>
          </div>

          {allowRecontact && (
            <div style={fieldStyle}>
              <label style={labelStyle}>Keep dialogs untouched if active within (days)</label>
              <input
                type="number"
                min={1}
                max={365}
                style={inputStyle}
                value={recontactMinAgeDays}
                onChange={(e) => setRecontactMinAgeDays(Number(e.target.value))}
              />
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div style={fieldStyle}>
              <label style={labelStyle}>Start date</label>
              <input
                type="date"
                style={inputStyle}
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Stop date</label>
              <input
                type="date"
                style={inputStyle}
                value={stopDate}
                onChange={(e) => setStopDate(e.target.value)}
              />
            </div>
          </div>

          <div style={fieldStyle}>
            <label style={labelStyle}>Primary goal</label>
            <select
              style={inputStyle}
              value={primaryGoal}
              onChange={(e) => setPrimaryGoal(e.target.value)}
            >
              <option value="">—</option>
              <option value="book_meeting">Book meeting</option>
              <option value="qualify">Qualify</option>
              <option value="click">Click</option>
              <option value="engage">Engage</option>
            </select>
          </div>

          {/* Phase 11 D-13: audience_hints relabeled "Кому пишем" */}
          <div style={fieldStyle}>
            <label style={labelStyle}>Кому пишем</label>
            <textarea
              style={taStyle}
              rows={2}
              value={audienceHints}
              onChange={(e) => setAudienceHints(e.target.value)}
            />
          </div>

          {/* Phase 11 D-04/D-05: Ход разговора stage editor */}
          <div style={fieldStyle}>
            <label style={labelStyle}>Ход разговора</label>
            <InlineStageEditor stages={dialogueFlow} onChange={setDialogueFlow} />
          </div>

          {/* Phase 11 D-12: Аргументы и факты */}
          <div style={fieldStyle}>
            <label style={labelStyle}>Аргументы и факты</label>
            <textarea
              style={taStyle}
              rows={3}
              value={argumentsFacts}
              onChange={(e) => setArgumentsFacts(e.target.value)}
              placeholder="Факты о продукте, ответы на типичные возражения."
            />
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              ИИ использует только эти факты и не выдумывает остальное.
            </span>
          </div>

          {/* Phase 11 D-14: Правила кампании */}
          <div style={fieldStyle}>
            <label style={labelStyle}>Правила кампании</label>
            <textarea
              style={taStyle}
              rows={2}
              value={campaignRules}
              onChange={(e) => setCampaignRules(e.target.value)}
              placeholder="Запреты и правила, специфичные для этой кампании."
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 14 }}>
            <div style={fieldStyle}>
              {/* Phase 11 D-13: «Сигнал "Лид"» — merged with old success_criteria */}
              <label style={labelStyle}>Сигнал «Лид»</label>
              <textarea
                style={taStyle}
                rows={4}
                value={leadHint}
                onChange={(e) => setLeadHint(e.target.value)}
                placeholder="e.g. The contact agrees to a demo or asks for pricing details."
              />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Handoff trigger hint</label>
              <textarea
                style={taStyle}
                rows={4}
                value={handoffHint}
                onChange={(e) => setHandoffHint(e.target.value)}
                placeholder="e.g. The contact asks a technical or legal question the AI can’t answer."
              />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Finish trigger hint</label>
              <textarea
                style={taStyle}
                rows={4}
                value={finishHint}
                onChange={(e) => setFinishHint(e.target.value)}
                placeholder="e.g. The contact declines, unsubscribes, or the deal is closed."
              />
            </div>
          </div>

          <div style={fieldStyle}>
            <label style={labelStyle}>Webhook URL</label>
            <input
              style={inputStyle}
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder="https://…"
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            <div style={fieldStyle}>
              <label style={labelStyle}>Lead webhook</label>
              <input
                style={inputStyle}
                value={leadWebhook}
                onChange={(e) => setLeadWebhook(e.target.value)}
              />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Handoff webhook</label>
              <input
                style={inputStyle}
                value={handoffWebhook}
                onChange={(e) => setHandoffWebhook(e.target.value)}
              />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Finish webhook</label>
              <input
                style={inputStyle}
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
                marginTop: 4,
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
            padding: "12px 20px",
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
