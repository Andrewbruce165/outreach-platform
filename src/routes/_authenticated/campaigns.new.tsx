import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  Sparkles,
  Bot,
  Send,
  Users,
  Calendar,
  Link as LinkIcon,
  Rocket,
  Check,
  ChevronLeft,
  ChevronRight,
  Plus,
  Edit3,
  Trash2,
  X,
  Wrench,
  Flag,
  MousePointerClick,
  Smile,
  Info,
  Loader2,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";
import { toast } from "sonner";

type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];
type Sender = components["schemas"]["SenderResponse"];
type CampaignCreate = components["schemas"]["CampaignCreate"];
type Campaign = components["schemas"]["CampaignResponse"];
type ToolSpec = components["schemas"]["ToolSpec"];
type ToolParamSpec = components["schemas"]["ToolParamSpec"];
type PrimaryGoal = "book_meeting" | "qualify" | "click" | "engage";

export const Route = createFileRoute("/_authenticated/campaigns/new")({
  component: CampaignBuilder,
});

const STEPS = [
  { id: "brief", label: "Brief", Icon: Sparkles },
  { id: "agent", label: "Agent", Icon: Bot },
  { id: "accounts", label: "Senders", Icon: Send },
  { id: "audience", label: "Audience", Icon: Users },
  { id: "schedule", label: "Schedule", Icon: Calendar },
  { id: "integrations", label: "Integrations", Icon: LinkIcon },
  { id: "review", label: "Review", Icon: Rocket },
] as const;

const STEP_TITLES: Record<string, string> = {
  brief: "Write a brief",
  agent: "Pick an AI agent",
  accounts: "Choose senders",
  audience: "Pick your audience",
  schedule: "Set the schedule",
  integrations: "Integrations & signals",
  review: "Review & launch",
};

const STEP_SUBS: Record<string, string> = {
  brief: "Describe the goal in plain English. The AI will use this to suggest the agent, audience, and tone.",
  agent: "Each agent is a templated SDR — context, task, tone, signals.",
  accounts: "These Telegram accounts will send. They lock to this campaign while running.",
  audience: "Choose a contact folder. New contacts added later will be auto-enrolled.",
  schedule: "Working hours and days. aimly respects rate limits and the green corridor.",
  integrations: "Push signal events to your webhook and add custom tools.",
  review: "Final check — everything looks right? Launch and watch the dashboard.",
};

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

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function CampaignBuilder() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // --- form state ---
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [agentId, setAgentId] = useState("");
  const [audienceHints, setAudienceHints] = useState("");
  const [primaryGoal, setPrimaryGoal] = useState<PrimaryGoal | "">("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [messageTemplate, setMessageTemplate] = useState("Hi {{first_name}}! ");
  const [senderIds, setSenderIds] = useState<string[]>([]);
  const [folderId, setFolderId] = useState("");
  const [tz, setTz] = useState<string>(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [hourStart, setHourStart] = useState(9);
  const [hourEnd, setHourEnd] = useState(20);
  const [days, setDays] = useState<string[]>(["mon", "tue", "wed", "thu", "fri"]);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [tools, setTools] = useState<ToolSpec[]>([]);
  // lead_trigger_hint — plain-English condition that tells the AI when a conversation
  // should be classified as a qualified lead (fires the `lead` signal to the webhook).
  // Lives on the Agent step because it's a per-campaign override of the agent's lead detection.
  const [leadHint, setLeadHint] = useState("");
  // handoff_trigger_hint — plain-English condition for when the AI should stop and pass
  // the conversation to a human (fires the `handoff` signal). Per-campaign override.
  const [handoffHint, setHandoffHint] = useState("");
  // finish_trigger_hint — plain-English condition for when the conversation is done
  // (contact declined, unsubscribed, deal closed). Fires the `finished` signal.
  const [finishHint, setFinishHint] = useState("");

  // --- queries ---
  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<{ agents: Agent[]; total: number }>("/api/v1/agents"),
  });
  const foldersQ = useQuery({
    queryKey: ["folders"],
    queryFn: () => api<Folder[]>("/api/v1/folders"),
  });
  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<{ senders: Sender[] }>("/api/v1/senders"),
  });

  const agents = agentsQ.data?.agents ?? [];
  const folders = foldersQ.data ?? [];
  const senders = sendersQ.data?.senders ?? [];
  const selectedAgent = agents.find((a) => a.id === agentId);
  const selectedFolder = folders.find((f) => f.id === folderId);
  const selectedSenders = senders.filter((s) => senderIds.includes(s.id));

  // --- mutations ---
  const autoFillMut = useMutation({
    mutationFn: () =>
      api<{ name: string; audience_hints: string; primary_goal: string; success_criteria: string }>(
        "/api/v1/campaigns/auto-fill",
        { method: "POST", body: { brief } },
      ),
    onSuccess: (d) => {
      if (!name) setName(d.name);
      setAudienceHints(d.audience_hints);
      setSuccessCriteria(d.success_criteria);
      if (["book_meeting", "qualify", "click", "engage"].includes(d.primary_goal)) {
        setPrimaryGoal(d.primary_goal as PrimaryGoal);
      }
      toast.success("Brief expanded by AI");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  // Build the full campaign payload from current form state. Used both for
  // auto-saving drafts on step transition and for the final launch.
  const buildPayload = (): CampaignCreate => ({
    name: name || "Untitled campaign",
    description: brief || null,
    agent_id: agentId,
    folder_id: folderId,
    sender_ids: senderIds,
    message_template: messageTemplate,
    timezone: tz,
    work_hour_start: hourStart,
    work_hour_end: hourEnd,
    work_days_mask: maskFromDays(days),
    audience_hints: audienceHints || null,
    primary_goal: primaryGoal || null,
    success_criteria: successCriteria || null,
    webhook_url: webhookUrl || null,
    tools: tools.length ? tools : undefined,
    lead_trigger_hint: leadHint || null,
    handoff_trigger_hint: handoffHint || null,
    finish_trigger_hint: finishHint || null,
  });

  // Auto-save draft after each completed step. POST creates the draft the first
  // time we have the backend-required minimum (name + agent + folder + template);
  // subsequent saves PATCH the existing draft.
  const saveDraftMut = useMutation({
    mutationFn: async (): Promise<Campaign | null> => {
      const hasMinimum =
        name.trim().length > 0 && !!agentId && !!folderId && messageTemplate.trim().length > 0;
      if (!hasMinimum && !draftId) return null;
      const payload = buildPayload();
      if (draftId) {
        return api<Campaign>(`/api/v1/campaigns/${draftId}`, {
          method: "PATCH",
          body: payload as unknown as Record<string, unknown>,
        });
      }
      return api<Campaign>("/api/v1/campaigns", {
        method: "POST",
        body: payload as unknown as Record<string, unknown>,
      });
    },
    onSuccess: (c) => {
      if (!c) return;
      if (!draftId) {
        setDraftId(c.id);
        track("campaign_created", { campaign_id: c.id });
      }
      setSavedAt(Date.now());
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e) => toast.error(`Draft not saved: ${errMsg(e)}`),
  });

  // Continue to next step, saving a draft first. UI advances even if save fails
  // (error shown via toast) so the user can keep editing.
  const goNext = async () => {
    try {
      await saveDraftMut.mutateAsync();
    } catch {
      /* handled by onError */
    }
    setStep((s) => Math.min(STEPS.length - 1, s + 1));
  };

  const launchMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      let campaign: Campaign;
      if (draftId) {
        campaign = await api<Campaign>(`/api/v1/campaigns/${draftId}`, {
          method: "PATCH",
          body: payload as unknown as Record<string, unknown>,
        });
      } else {
        campaign = await api<Campaign>("/api/v1/campaigns", {
          method: "POST",
          body: payload as unknown as Record<string, unknown>,
        });
        setDraftId(campaign.id);
        track("campaign_created", { campaign_id: campaign.id });
      }
      await api(`/api/v1/campaigns/${campaign.id}/start`, { method: "POST" });
      return campaign;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
      toast.success("Campaign launched");
      navigate({ to: "/campaigns" });
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  // step validation — every field on every step is required before you can advance.
  const canNext = useMemo(() => {
    const id = STEPS[step].id;
    if (id === "brief") return name.trim().length > 0 && brief.trim().length > 0;
    if (id === "agent")
      return (
        !!agentId &&
        audienceHints.trim().length > 0 &&
        !!primaryGoal &&
        successCriteria.trim().length > 0 &&
        leadHint.trim().length > 0 &&
        handoffHint.trim().length > 0 &&
        finishHint.trim().length > 0
      );
    if (id === "accounts") return senderIds.length > 0;
    if (id === "audience") return !!folderId;
    if (id === "schedule")
      return (
        days.length > 0 &&
        hourEnd > hourStart &&
        tz.trim().length > 0 &&
        messageTemplate.trim().length > 0
      );
    if (id === "integrations") return webhookUrl.trim().length > 0;
    return true;
  }, [
    step,
    name,
    brief,
    agentId,
    audienceHints,
    primaryGoal,
    successCriteria,
    leadHint,
    handoffHint,
    finishHint,
    senderIds,
    folderId,
    days,
    hourStart,
    hourEnd,
    tz,
    messageTemplate,
    webhookUrl,
  ]);

  // Launch must require every step to be valid — `canNext` only knows about the
  // current step, so a user could otherwise jump to Review and launch early.
  const allValid =
    name.trim().length > 0 &&
    brief.trim().length > 0 &&
    !!agentId &&
    audienceHints.trim().length > 0 &&
    !!primaryGoal &&
    successCriteria.trim().length > 0 &&
    leadHint.trim().length > 0 &&
    handoffHint.trim().length > 0 &&
    finishHint.trim().length > 0 &&
    senderIds.length > 0 &&
    !!folderId &&
    days.length > 0 &&
    hourEnd > hourStart &&
    tz.trim().length > 0 &&
    messageTemplate.trim().length > 0 &&
    webhookUrl.trim().length > 0;

  const cur = STEPS[step];

  return (
    <>
      <Topbar
        title="New campaign"
        right={
          <>
            <DraftStatus
              saving={saveDraftMut.isPending}
              savedAt={savedAt}
              hasDraft={!!draftId}
            />
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => navigate({ to: "/campaigns" })}
            >
              {draftId ? "Close" : "Cancel"}
            </button>
            <button
              className="btn btn--primary btn--sm"
              disabled={!allValid || launchMut.isPending}
              onClick={() => launchMut.mutate()}
            >
              {launchMut.isPending ? (
                <><Loader2 size={13} className="ob__spin" /> Launching…</>
              ) : (
                <><Rocket size={13} /> Launch</>
              )}
            </button>
          </>
        }
      />
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "240px 1fr",
          minHeight: 0,
        }}
      >
        {/* Step list */}
        <aside
          style={{
            background: "var(--bg)",
            borderRight: "1px solid var(--border)",
            padding: "20px 14px",
          }}
        >
          <div
            style={{
              fontSize: 11,
              textTransform: "uppercase",
              color: "var(--text-faint)",
              fontWeight: 600,
              letterSpacing: "0.06em",
              marginBottom: 14,
              paddingLeft: 8,
            }}
          >
            Steps
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {STEPS.map((s, i) => {
              const done = i < step;
              const active = i === step;
              const Icon = s.Icon;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setStep(i)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 12px",
                    borderRadius: 9,
                    background: active ? "var(--tg-blue-soft)" : "transparent",
                    color: active
                      ? "var(--tg-blue)"
                      : done
                        ? "var(--text)"
                        : "var(--text-muted)",
                    fontWeight: active ? 500 : 400,
                    fontSize: 13.5,
                    textAlign: "left",
                  }}
                >
                  <div
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: 7,
                      background: done
                        ? "var(--success)"
                        : active
                          ? "var(--tg-blue)"
                          : "var(--bg-soft)",
                      color: done || active ? "white" : "var(--text-faint)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    {done ? <Check size={13} /> : <Icon size={12} />}
                  </div>
                  <span style={{ flex: 1 }}>{s.label}</span>
                  {active && <ChevronRight size={14} />}
                </button>
              );
            })}
          </div>
        </aside>

        {/* Center editor */}
        <div
          className="scroll"
          style={{ background: "var(--bg-soft)", padding: "28px 36px" }}
        >
          <div style={{ maxWidth: 720, margin: "0 auto" }}>
            <div
              style={{
                fontSize: 11,
                textTransform: "uppercase",
                color: "var(--tg-blue)",
                fontWeight: 600,
                letterSpacing: "0.08em",
                marginBottom: 6,
              }}
            >
              Step {step + 1} of {STEPS.length}
            </div>
            <div
              style={{
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                marginBottom: 4,
              }}
            >
              {STEP_TITLES[cur.id]}
            </div>
            <div
              style={{
                color: "var(--text-muted)",
                fontSize: 13.5,
                marginBottom: 24,
              }}
            >
              {STEP_SUBS[cur.id]}
            </div>

            <div
              className="card"
              style={{ padding: 24 }}
            >
              {cur.id === "brief" && (
                <BriefStep
                  name={name}
                  setName={setName}
                  brief={brief}
                  setBrief={setBrief}
                  onAutoFill={() => autoFillMut.mutate()}
                  autoFilling={autoFillMut.isPending}
                />
              )}
              {cur.id === "agent" && (
                <AgentStep
                  agents={agents}
                  agentId={agentId}
                  setAgentId={setAgentId}
                  audienceHints={audienceHints}
                  setAudienceHints={setAudienceHints}
                  primaryGoal={primaryGoal}
                  setPrimaryGoal={setPrimaryGoal}
                  successCriteria={successCriteria}
                  setSuccessCriteria={setSuccessCriteria}
                  leadHint={leadHint}
                  setLeadHint={setLeadHint}
                  handoffHint={handoffHint}
                  setHandoffHint={setHandoffHint}
                  finishHint={finishHint}
                  setFinishHint={setFinishHint}
                />
              )}
              {cur.id === "accounts" && (
                <AccountsStep
                  senders={senders}
                  senderIds={senderIds}
                  setSenderIds={setSenderIds}
                />
              )}
              {cur.id === "audience" && (
                <AudienceStep
                  folders={folders}
                  folderId={folderId}
                  setFolderId={setFolderId}
                />
              )}
              {cur.id === "schedule" && (
                <ScheduleStep
                  days={days}
                  setDays={setDays}
                  hourStart={hourStart}
                  setHourStart={setHourStart}
                  hourEnd={hourEnd}
                  setHourEnd={setHourEnd}
                  tz={tz}
                  setTz={setTz}
                  messageTemplate={messageTemplate}
                  setMessageTemplate={setMessageTemplate}
                />
              )}
              {cur.id === "integrations" && (
                <IntegrationsStep
                  webhookUrl={webhookUrl}
                  setWebhookUrl={setWebhookUrl}
                  tools={tools}
                  setTools={setTools}
                  messageTemplate={messageTemplate}
                  setMessageTemplate={setMessageTemplate}
                />
              )}
              {cur.id === "review" && (
                <ReviewStep
                  name={name}
                  agent={selectedAgent}
                  folder={selectedFolder}
                  senders={selectedSenders}
                  days={days}
                  hourStart={hourStart}
                  hourEnd={hourEnd}
                  tz={tz}
                  primaryGoal={primaryGoal}
                  webhookUrl={webhookUrl}
                  toolsCount={tools.length}
                  onJump={setStep}
                />
              )}
            </div>

            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: 18,
              }}
            >
              <button
                type="button"
                className="btn btn--ghost"
                disabled={step === 0}
                onClick={() => setStep((s) => Math.max(0, s - 1))}
                style={{ opacity: step === 0 ? 0.5 : 1 }}
              >
                <ChevronLeft size={14} /> Back
              </button>
              {step < STEPS.length - 1 ? (
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={!canNext || saveDraftMut.isPending}
                  onClick={() => void goNext()}
                >
                  {saveDraftMut.isPending ? (
                    <><Loader2 size={14} className="ob__spin" /> Saving…</>
                  ) : (
                    <>Continue <ChevronRight size={14} /></>
                  )}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={!allValid || launchMut.isPending}
                  onClick={() => launchMut.mutate()}
                >
                  {launchMut.isPending ? (
                    <><Loader2 size={14} className="ob__spin" /> Launching…</>
                  ) : (
                    <><Rocket size={14} /> Launch campaign</>
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------------- Step 1: Brief ---------------- */
function BriefStep({
  name,
  setName,
  brief,
  setBrief,
  onAutoFill,
  autoFilling,
}: {
  name: string;
  setName: (v: string) => void;
  brief: string;
  setBrief: (v: string) => void;
  onAutoFill: () => void;
  autoFilling: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="field">
        <label className="field__label">Campaign name</label>
        <input
          className="input"
          placeholder="e.g. EU SMB outreach — Q2"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="field">
        <label
          className="field__label"
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          Brief
          <span
            className="pill"
            style={{
              height: 18,
              padding: "0 7px",
              fontSize: 10,
              background: "color-mix(in oklab, var(--ai-purple) 14%, transparent)",
              color: "var(--ai-purple)",
            }}
          >
            <Sparkles size={9} /> AI-assisted
          </span>
        </label>
        <textarea
          className="textarea"
          rows={5}
          placeholder="Describe the audience, the offer, and the action you want."
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
        />
        <span className="field__hint">
          The AI will use this to pre-fill agent suggestions, tone, and audience hints.
        </span>
      </div>
      <div>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onAutoFill}
          disabled={!brief.trim() || autoFilling}
          style={{
            background: "color-mix(in oklab, var(--ai-purple) 12%, transparent)",
            color: "var(--ai-purple)",
          }}
        >
          {autoFilling ? (
            <><Loader2 size={12} className="ob__spin" /> Filling…</>
          ) : (
            <><Sparkles size={12} /> Auto-fill from brief</>
          )}
        </button>
      </div>
    </div>
  );
}

/* ---------------- Step 2: Agent ---------------- */
function AgentStep({
  agents,
  agentId,
  setAgentId,
  audienceHints,
  setAudienceHints,
  primaryGoal,
  setPrimaryGoal,
  successCriteria,
  setSuccessCriteria,
  leadHint,
  setLeadHint,
  handoffHint,
  setHandoffHint,
  finishHint,
  setFinishHint,
}: {
  agents: Agent[];
  agentId: string;
  setAgentId: (v: string) => void;
  audienceHints: string;
  setAudienceHints: (v: string) => void;
  primaryGoal: PrimaryGoal | "";
  setPrimaryGoal: (v: PrimaryGoal | "") => void;
  successCriteria: string;
  setSuccessCriteria: (v: string) => void;
  // Signal-trigger hints — see state declarations in CampaignBuilder for full context.
  // Mapped to payload as: lead_trigger_hint / handoff_trigger_hint / finish_trigger_hint.
  leadHint: string;
  setLeadHint: (v: string) => void;
  handoffHint: string;
  setHandoffHint: (v: string) => void;
  finishHint: string;
  setFinishHint: (v: string) => void;
}) {
  const GOALS: Array<{ id: PrimaryGoal; label: string; desc: string; Icon: typeof Calendar }> = [
    { id: "book_meeting", label: "Book a meeting", desc: "Calendar invite confirmed", Icon: Calendar },
    { id: "qualify", label: "Qualify the lead", desc: "Budget · timeline · authority", Icon: Flag },
    { id: "click", label: "Get a click", desc: "Visit link / sign up · UTM", Icon: MousePointerClick },
    { id: "engage", label: "Engage", desc: "Warm 5+ msg conversation", Icon: Smile },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div>
        <label className="field__label" style={{ marginBottom: 8, display: "block" }}>
          Choose agent
        </label>
        {agents.length === 0 ? (
          <div className="muted text-sm" style={{ padding: 16, textAlign: "center" }}>
            No agents yet. Create one in the Agents section first.
          </div>
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
                      <div className="muted text-xs">
                        {a.tone_of_voice || "Custom agent"}
                      </div>
                    </div>
                    {on && <Check size={16} style={{ color: "var(--tg-blue)" }} />}
                  </div>
                  {a.system_prompt && (
                    <div className="text-sm muted" style={{ lineHeight: 1.45, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                      {a.system_prompt}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div style={{ paddingTop: 20, borderTop: "1px solid var(--divider, var(--border))" }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
          Customize for this campaign
        </div>
        <div className="muted text-xs" style={{ marginBottom: 16 }}>
          Override the agent&apos;s defaults just for this campaign. Won&apos;t change the agent globally.
        </div>

        <div className="field" style={{ marginBottom: 16 }}>
          <label className="field__label">Audience hints</label>
          <textarea
            className="textarea"
            rows={3}
            placeholder="e.g. Cold US founders raising seed in AI"
            value={audienceHints}
            onChange={(e) => setAudienceHints(e.target.value)}
          />
        </div>

        <div className="field" style={{ marginBottom: 16 }}>
          <label className="field__label">Primary goal</label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {GOALS.map((g) => {
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
                    background: on
                      ? "var(--tg-blue-softer, var(--tg-blue-soft))"
                      : "var(--bg)",
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
          <label className="field__label">Success criteria</label>
          <textarea
            className="textarea"
            rows={2}
            placeholder="Demo booked / phone shared / link clicked"
            value={successCriteria}
            onChange={(e) => setSuccessCriteria(e.target.value)}
          />
          <span className="field__hint">
            Free-text rule for emitting the <b>lead</b> signal in this campaign.
          </span>
        </div>
      </div>

      {/*
        Signal trigger hints — sent to the backend as lead_trigger_hint /
        handoff_trigger_hint / finish_trigger_hint on CampaignCreate.
        These are plain-English overrides that tell the AI when to fire each
        webhook signal for this specific campaign. Living on the Agent step
        because they're agent behavior tweaks, not sender configuration.
      */}
      <div style={{ paddingTop: 20, borderTop: "1px solid var(--divider, var(--border))" }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
          Signal triggers
        </div>
        <div className="muted text-xs" style={{ marginBottom: 16 }}>
          Plain-English hints the AI uses to detect when to fire each signal in this conversation.
        </div>

        <div className="field" style={{ marginBottom: 14 }}>
          <label className="field__label">
            <Flag size={12} style={{ display: "inline", marginRight: 6, color: "var(--success)" }} />
            Lead trigger hint
          </label>
          {/* lead_trigger_hint: fires the `lead` webhook signal when matched. */}
          <textarea
            className="input"
            rows={2}
            value={leadHint}
            onChange={(e) => setLeadHint(e.target.value)}
            placeholder="e.g. The contact agrees to a demo or asks for pricing details."
          />
        </div>

        <div className="field" style={{ marginBottom: 14 }}>
          <label className="field__label">
            <Users size={12} style={{ display: "inline", marginRight: 6, color: "var(--warning, var(--tg-blue))" }} />
            Handoff trigger hint
          </label>
          {/* handoff_trigger_hint: fires the `handoff` signal — AI stops, human takes over. */}
          <textarea
            className="input"
            rows={2}
            value={handoffHint}
            onChange={(e) => setHandoffHint(e.target.value)}
            placeholder="e.g. The contact asks a technical or legal question the AI can’t answer."
          />
        </div>

        <div className="field">
          <label className="field__label">
            <Check size={12} style={{ display: "inline", marginRight: 6, color: "var(--text-muted)" }} />
            Finish trigger hint
          </label>
          {/* finish_trigger_hint: fires the `finished` signal — conversation is closed. */}
          <textarea
            className="input"
            rows={2}
            value={finishHint}
            onChange={(e) => setFinishHint(e.target.value)}
            placeholder="e.g. The contact declines, unsubscribes, or the deal is closed."
          />
        </div>
      </div>
    </div>
  );
}

/* ---------------- Step 3: Senders ---------------- */
function AccountsStep({
  senders,
  senderIds,
  setSenderIds,
}: {
  senders: Sender[];
  senderIds: string[];
  setSenderIds: (v: string[]) => void;
}) {
  const toggle = (id: string) =>
    setSenderIds(senderIds.includes(id) ? senderIds.filter((x) => x !== id) : [...senderIds, id]);

  const eligible = senders.filter((s) => s.status !== "error");
  const dailyTotal = senderIds.length * 150;

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 14,
          padding: "10px 12px",
          borderRadius: 9,
          background: "var(--bg-soft)",
          fontSize: 12.5,
        }}
      >
        <Info size={14} style={{ color: "var(--tg-blue)" }} />
        <span>
          {senderIds.length} accounts selected · up to <b className="num">{dailyTotal}</b>{" "}
          messages/day total
        </span>
      </div>
      {eligible.length === 0 ? (
        <div className="muted text-sm" style={{ padding: 24, textAlign: "center" }}>
          No accounts. Connect one in TG Accounts first.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {eligible.map((a) => {
            const on = senderIds.includes(a.id);
            return (
              <div
                key={a.id}
                onClick={() => toggle(a.id)}
                style={{
                  padding: "12px 14px",
                  borderRadius: 10,
                  cursor: "pointer",
                  border: `1px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
                  background: on ? "var(--tg-blue-softer, var(--tg-blue-soft))" : "var(--bg)",
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                }}
              >
                <div
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: 5,
                    border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border-strong, var(--border))"}`,
                    background: on ? "var(--tg-blue)" : "transparent",
                    color: "white",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {on && <Check size={12} />}
                </div>
                <div
                  className="avatar avatar--sm"
                  style={{ background: "var(--tg-blue)", color: "white" }}
                >
                  {(a.name || a.slug).slice(0, 1).toUpperCase()}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>
                    {a.name || a.slug}{" "}
                    <span className="muted">· {a.phone}</span>
                  </div>
                  <div className="text-xs muted">
                    role: {a.role || "sender"} · status: {a.status}
                  </div>
                </div>
                <span className={`pill ${a.status === "active" ? "pill--green" : ""}`}>
                  {a.status}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------- Step 4: Audience ---------------- */
function AudienceStep({
  folders,
  folderId,
  setFolderId,
}: {
  folders: Folder[];
  folderId: string;
  setFolderId: (v: string) => void;
}) {
  if (folders.length === 0) {
    return (
      <div className="muted text-sm" style={{ padding: 24, textAlign: "center" }}>
        No folders yet. Create one on the Contacts page first.
      </div>
    );
  }
  return (
    <div>
      <p className="muted text-sm" style={{ marginBottom: 14 }}>
        Pick one folder. While the campaign runs, any new contact added to this folder is
        auto-enrolled.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {folders.map((f) => {
          const sel = folderId === f.id;
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => setFolderId(f.id)}
              style={{
                padding: 14,
                borderRadius: 11,
                textAlign: "left",
                border: `1.5px solid ${sel ? "var(--tg-blue)" : "var(--border)"}`,
                background: sel ? "var(--tg-blue-softer, var(--tg-blue-soft))" : "var(--bg)",
                display: "flex",
                flexDirection: "column",
                gap: 10,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 8,
                    background: "var(--tg-blue-soft)",
                    color: "var(--tg-blue)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Users size={16} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>{f.name}</div>
                  <div className="muted text-xs">
                    {f.contact_count.toLocaleString()} contacts
                  </div>
                </div>
                {sel && <Check size={16} style={{ color: "var(--tg-blue)" }} />}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------- Step 5: Schedule ---------------- */
function ScheduleStep({
  days,
  setDays,
  hourStart,
  setHourStart,
  hourEnd,
  setHourEnd,
  tz,
  setTz,
  messageTemplate,
  setMessageTemplate,
}: {
  days: string[];
  setDays: (v: string[]) => void;
  hourStart: number;
  setHourStart: (v: number) => void;
  hourEnd: number;
  setHourEnd: (v: number) => void;
  tz: string;
  setTz: (v: string) => void;
  messageTemplate: string;
  setMessageTemplate: (v: string) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="field">
        <label className="field__label">Working days</label>
        <div style={{ display: "flex", gap: 8 }}>
          {DAYS.map((d) => {
            const on = days.includes(d.id);
            return (
              <button
                key={d.id}
                type="button"
                onClick={() =>
                  setDays(on ? days.filter((x) => x !== d.id) : [...days, d.id])
                }
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 10,
                  background: on ? "var(--tg-blue)" : "var(--bg-soft)",
                  color: on ? "white" : "var(--text-muted)",
                  fontWeight: 600,
                  fontSize: 13,
                }}
              >
                {d.label}
              </button>
            );
          })}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 2fr", gap: 12 }}>
        <div className="field">
          <label className="field__label">From (h)</label>
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
          <label className="field__label">To (h)</label>
          <input
            className="input"
            type="number"
            min={1}
            max={24}
            value={hourEnd}
            onChange={(e) => setHourEnd(Number(e.target.value))}
          />
        </div>
        <div className="field">
          <label className="field__label">Timezone</label>
          <input
            className="input"
            placeholder="Europe/London"
            value={tz}
            onChange={(e) => setTz(e.target.value)}
          />
        </div>
      </div>

      <div className="field">
        <label className="field__label">First message template</label>
        <textarea
          className="textarea"
          rows={4}
          value={messageTemplate}
          onChange={(e) => setMessageTemplate(e.target.value)}
        />
        <span className="field__hint">
          Use <code>&#123;&#123;first_name&#125;&#125;</code>, <code>&#123;&#123;full_name&#125;&#125;</code>,{" "}
          <code>&#123;&#123;username&#125;&#125;</code> as placeholders.
        </span>
      </div>

      <div
        style={{
          padding: 14,
          background: "var(--bg-soft)",
          borderRadius: 10,
          fontSize: 12.5,
          color: "var(--text-soft, var(--text-muted))",
        }}
      >
        <b>Green corridor:</b> aimly enforces 4 / 20 / 150 messages per account (hour / day /
        week) plus warm-up. You&apos;ll never exceed these.
      </div>
    </div>
  );
}

/* ---------------- Step 6: Integrations ---------------- */
function IntegrationsStep({
  webhookUrl,
  setWebhookUrl,
  tools,
  setTools,
}: {
  webhookUrl: string;
  setWebhookUrl: (v: string) => void;
  tools: ToolSpec[];
  setTools: (v: ToolSpec[]) => void;
  messageTemplate: string;
  setMessageTemplate: (v: string) => void;
}) {
  const [editing, setEditing] = useState<ToolSpec | null>(null);

  const startNew = () =>
    setEditing({ name: "", description: "", parameters: [], webhook_method: "POST" });
  const startEdit = (t: ToolSpec) =>
    setEditing({ ...t, parameters: (t.parameters ?? []).map((p) => ({ ...p })) });
  const save = (draft: ToolSpec) => {
    if (draft.id) {
      setTools(tools.map((t) => (t.id === draft.id ? draft : t)));
    } else {
      setTools([...tools, { ...draft, id: "t" + Date.now() }]);
    }
    setEditing(null);
  };
  const remove = (id: string | null | undefined) =>
    setTools(tools.filter((t) => t.id !== id));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div className="field">
        <label className="field__label">Where to push signal events</label>
        <input
          className="input"
          placeholder="https://your-app.com/webhook"
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
        />
        <span className="field__hint">
          All built-in signals (lead, handoff, finished) and custom tool calls fire here.
        </span>
      </div>

      <div>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
          <label className="field__label" style={{ margin: 0 }}>
            Custom tools
          </label>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={startNew}
          >
            <Plus size={12} /> Add tool
          </button>
        </div>
        {tools.length === 0 ? (
          <div
            style={{
              padding: "28px 18px",
              borderRadius: 11,
              border: "1.5px dashed var(--border-strong, var(--border))",
              background: "var(--bg-soft)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 10,
              textAlign: "center",
            }}
          >
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 9,
                background: "var(--bg)",
                color: "var(--text-faint)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Wrench size={16} />
            </div>
            <div style={{ fontSize: 13, fontWeight: 500 }}>No custom tools</div>
            <div className="muted text-xs" style={{ maxWidth: 320 }}>
              Give the agent extra capabilities — like booking demos, checking pricing, or
              pulling data from your API.
            </div>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={startNew}
              style={{ marginTop: 4 }}
            >
              <Plus size={12} /> Add tool
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {tools.map((t) => (
              <div
                key={t.id ?? t.name}
                style={{
                  padding: "12px 14px",
                  borderRadius: 10,
                  border: "1px solid var(--border)",
                  background: "var(--bg)",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 12,
                }}
              >
                <div
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: 8,
                    background: "var(--tg-blue-soft)",
                    color: "var(--tg-blue)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <Wrench size={14} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>
                      {t.name}
                    </span>
                    <span
                      className="pill"
                      style={{ height: 18, fontSize: 10, padding: "0 7px" }}
                    >
                      {(t.parameters ?? []).length} params
                    </span>
                  </div>
                  <div
                    className="muted text-xs"
                    style={{ marginTop: 3, lineHeight: 1.5 }}
                  >
                    {t.description || <i>No description</i>}
                  </div>
                </div>
                <button
                  type="button"
                  className="tb__icon-btn"
                  style={{ width: 28, height: 28 }}
                  onClick={() => startEdit(t)}
                  aria-label="Edit tool"
                >
                  <Edit3 size={13} />
                </button>
                <button
                  type="button"
                  className="tb__icon-btn"
                  style={{ width: 28, height: 28, color: "var(--danger)" }}
                  onClick={() => remove(t.id)}
                  aria-label="Remove tool"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {editing && (
        <ToolEditorModal
          draft={editing}
          setDraft={(d) =>
            setEditing((cur) => (cur ? { ...cur, ...(typeof d === "function" ? d(cur) : d) } : cur))
          }
          onSave={save}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

const SNAKE_CASE = /^[a-z][a-z0-9_]*$/;

function ToolEditorModal({
  draft,
  setDraft,
  onSave,
  onClose,
}: {
  draft: ToolSpec;
  setDraft: (
    patch: Partial<ToolSpec> | ((cur: ToolSpec) => Partial<ToolSpec>),
  ) => void;
  onSave: (d: ToolSpec) => void;
  onClose: () => void;
}) {
  const params = draft.parameters ?? [];
  const updateParam = (i: number, patch: Partial<ToolParamSpec>) =>
    setDraft((d) => ({
      parameters: (d.parameters ?? []).map((p, k) => (k === i ? { ...p, ...patch } : p)),
    }));
  const addParam = () =>
    setDraft((d) => ({
      parameters: [
        ...(d.parameters ?? []),
        { name: "", type: "string", description: "", required: false },
      ],
    }));
  const removeParam = (i: number) =>
    setDraft((d) => ({
      parameters: (d.parameters ?? []).filter((_, k) => k !== i),
    }));

  const nameInvalid = !!draft.name && !SNAKE_CASE.test(draft.name);
  const canSave = !!draft.name && SNAKE_CASE.test(draft.name);

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 600 }}
      >
        <header className="modal__head">
          <h3>{draft.id ? "Edit tool" : "Add tool"}</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <div
          className="modal__body scroll"
          style={{ display: "flex", flexDirection: "column", gap: 18 }}
        >
          <div className="field">
            <label className="field__label">Name</label>
            <input
              className="input"
              placeholder="book_demo"
              value={draft.name}
              onChange={(e) => setDraft({ name: e.target.value })}
              style={{ borderColor: nameInvalid ? "var(--danger)" : undefined }}
            />
            <span
              className="field__hint"
              style={{ color: nameInvalid ? "var(--danger)" : undefined }}
            >
              {nameInvalid
                ? "Must be lowercase snake_case (letters, numbers, underscores)."
                : "Lowercase snake_case. The agent picks the tool by this name."}
            </span>
          </div>

          <div className="field">
            <label className="field__label">Description</label>
            <textarea
              className="textarea"
              rows={3}
              placeholder="What this tool does — the LLM uses this to decide when to call it"
              value={draft.description}
              onChange={(e) => setDraft({ description: e.target.value })}
            />
          </div>

          <div>
            <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
              <label className="field__label" style={{ margin: 0 }}>
                Parameters ({params.length})
              </label>
            </div>

            {params.length === 0 ? (
              <div
                style={{
                  padding: 14,
                  borderRadius: 9,
                  border: "1px dashed var(--border-strong, var(--border))",
                  background: "var(--bg-soft)",
                  textAlign: "center",
                }}
              >
                <div className="muted text-xs">No parameters yet.</div>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {params.map((p, i) => (
                  <div
                    key={i}
                    style={{
                      padding: 12,
                      borderRadius: 10,
                      border: "1px solid var(--border)",
                      background: "var(--bg-soft)",
                    }}
                  >
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1.2fr 110px 1.6fr auto auto",
                        gap: 8,
                        alignItems: "center",
                      }}
                    >
                      <input
                        className="input"
                        style={{ height: 32, fontSize: 12 }}
                        placeholder="email"
                        value={p.name}
                        onChange={(e) => updateParam(i, { name: e.target.value })}
                      />
                      <select
                        className="select"
                        style={{ height: 32, fontSize: 12 }}
                        value={p.type}
                        onChange={(e) =>
                          updateParam(i, {
                            type: e.target.value as ToolParamSpec["type"],
                          })
                        }
                      >
                        <option value="string">string</option>
                        <option value="number">number</option>
                        <option value="integer">integer</option>
                        <option value="boolean">boolean</option>
                      </select>
                      <input
                        className="input"
                        style={{ height: 32, fontSize: 12 }}
                        placeholder="What this parameter is"
                        value={p.description ?? ""}
                        onChange={(e) =>
                          updateParam(i, { description: e.target.value })
                        }
                      />
                      <button
                        type="button"
                        onClick={() => updateParam(i, { required: !p.required })}
                        style={{
                          padding: "4px 10px",
                          borderRadius: 6,
                          background: p.required ? "var(--tg-blue-soft)" : "var(--bg)",
                          color: p.required ? "var(--tg-blue)" : "var(--text-muted)",
                          fontSize: 11,
                          fontWeight: 600,
                        }}
                      >
                        {p.required ? "Required" : "Optional"}
                      </button>
                      <button
                        type="button"
                        className="tb__icon-btn"
                        style={{ width: 28, height: 28 }}
                        onClick={() => removeParam(i)}
                        aria-label="Remove parameter"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={addParam}
              style={{ marginTop: 10 }}
            >
              <Plus size={12} /> Add parameter
            </button>
          </div>
        </div>
        <div
          style={{
            padding: "14px 22px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button type="button" className="btn btn--ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canSave}
            onClick={() => onSave(draft)}
          >
            Save tool
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Step 7: Review ---------------- */
function ReviewStep({
  name,
  agent,
  folder,
  senders,
  days,
  hourStart,
  hourEnd,
  tz,
  primaryGoal,
  webhookUrl,
  toolsCount,
  onJump,
}: {
  name: string;
  agent: Agent | undefined;
  folder: Folder | undefined;
  senders: Sender[];
  days: string[];
  hourStart: number;
  hourEnd: number;
  tz: string;
  primaryGoal: PrimaryGoal | "";
  webhookUrl: string;
  toolsCount: number;
  onJump: (step: number) => void;
}) {
  const Row = ({ label, value, stepIdx }: { label: string; value: React.ReactNode; stepIdx: number }) => (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "140px 1fr auto",
        gap: 12,
        alignItems: "center",
        padding: "10px 0",
        borderBottom: "1px solid var(--divider, var(--border))",
      }}
    >
      <div className="muted text-sm">{label}</div>
      <div style={{ fontSize: 13.5 }}>{value}</div>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={() => onJump(stepIdx)}
        aria-label={`Edit ${label}`}
      >
        <Edit3 size={12} />
      </button>
    </div>
  );

  const goalLabels: Record<PrimaryGoal, string> = {
    book_meeting: "Book a meeting",
    qualify: "Qualify",
    click: "Drive a click",
    engage: "Engage",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <Row label="Name" value={name || <span className="faint">—</span>} stepIdx={0} />
      <Row
        label="Agent"
        value={agent ? agent.name : <span className="faint">— not picked</span>}
        stepIdx={1}
      />
      <Row
        label="Senders"
        value={
          senders.length
            ? `${senders.length} accounts (${senders.map((s) => s.name || s.slug).join(", ")})`
            : <span className="faint">none</span>
        }
        stepIdx={2}
      />
      <Row
        label="Audience"
        value={
          folder
            ? `${folder.name} · ${folder.contact_count.toLocaleString()} contacts`
            : <span className="faint">— not picked</span>
        }
        stepIdx={3}
      />
      <Row
        label="Schedule"
        value={`${String(hourStart).padStart(2, "0")}:00 – ${String(hourEnd).padStart(2, "0")}:00 ${tz} · ${days.length} days/week`}
        stepIdx={4}
      />
      <Row
        label="Primary goal"
        value={primaryGoal ? goalLabels[primaryGoal] : <span className="faint">—</span>}
        stepIdx={1}
      />
      <Row
        label="Signal webhook"
        value={webhookUrl || <span className="faint">—</span>}
        stepIdx={5}
      />
      <Row
        label="Custom tools"
        value={toolsCount > 0 ? `${toolsCount} tool${toolsCount === 1 ? "" : "s"}` : <span className="faint">none</span>}
        stepIdx={5}
      />

      <div
        style={{
          marginTop: 16,
          padding: 14,
          borderRadius: 10,
          background:
            "linear-gradient(135deg, color-mix(in oklab, var(--ai-purple) 12%, transparent), color-mix(in oklab, var(--tg-blue) 10%, transparent))",
          display: "flex",
          gap: 12,
          alignItems: "flex-start",
        }}
      >
        <div
          style={{
            width: 24,
            height: 24,
            borderRadius: 7,
            background: "var(--ai-purple)",
            color: "white",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Sparkles size={13} />
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-soft, var(--text-muted))", lineHeight: 1.5 }}>
          <b>Heads up:</b> the campaign will start as a draft. You can launch it from the campaign
          detail page after a final review.
        </div>
      </div>
    </div>
  );
}
