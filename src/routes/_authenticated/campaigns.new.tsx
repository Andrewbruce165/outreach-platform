import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];
type Sender = components["schemas"]["SenderResponse"];
type CampaignCreate = components["schemas"]["CampaignCreate"];
type Campaign = components["schemas"]["CampaignResponse"];

export const Route = createFileRoute("/_authenticated/campaigns/new")({
  component: CampaignBuilder,
});

const schema = z.object({
  name: z.string().min(1, "Required").max(100),
  description: z.string().max(500).optional().or(z.literal("")),
  agent_id: z.string().min(1, "Pick an agent"),
  folder_id: z.string().min(1, "Pick a folder"),
  sender_ids: z.array(z.string()).min(1, "Attach at least one account"),
  message_template: z.string().min(1, "Required").max(4000),
  timezone: z.string().min(1),
  work_hour_start: z.coerce.number().int().min(0).max(23),
  work_hour_end: z.coerce.number().int().min(1).max(24),
  primary_goal: z.enum(["book_meeting", "qualify", "click", "engage"]).optional().or(z.literal("")),
  audience_hints: z.string().max(500).optional().or(z.literal("")),
});

type FormValues = z.infer<typeof schema>;

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function CampaignBuilder() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [submitError, setSubmitError] = useState<string | null>(null);

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

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: "",
      description: "",
      agent_id: "",
      folder_id: "",
      sender_ids: [],
      message_template: "Hi {{first_name}}! ",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      work_hour_start: 9,
      work_hour_end: 20,
      primary_goal: "",
      audience_hints: "",
    },
  });

  const sender_ids = watch("sender_ids");

  const toggleSender = (id: string) => {
    const cur = sender_ids ?? [];
    setValue(
      "sender_ids",
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
      { shouldValidate: true },
    );
  };

  const autoFillMut = useMutation({
    mutationFn: () =>
      api<{ name: string; audience_hints: string; primary_goal: string; success_criteria: string }>(
        "/api/v1/campaigns/auto-fill",
        { method: "POST", body: { brief: watch("description") || "" } },
      ),
    onSuccess: (d) => {
      if (!watch("name")) setValue("name", d.name);
      setValue("audience_hints", d.audience_hints);
      if (["book_meeting", "qualify", "click", "engage"].includes(d.primary_goal)) {
        setValue("primary_goal", d.primary_goal as FormValues["primary_goal"]);
      }
    },
  });

  const onSubmit = handleSubmit(async (v) => {
    setSubmitError(null);
    const payload: CampaignCreate = {
      name: v.name,
      description: v.description || null,
      agent_id: v.agent_id,
      folder_id: v.folder_id,
      sender_ids: v.sender_ids,
      message_template: v.message_template,
      timezone: v.timezone,
      work_hour_start: v.work_hour_start,
      work_hour_end: v.work_hour_end,
      work_days_mask: 31, // Mon-Fri default
      primary_goal: v.primary_goal ? (v.primary_goal as CampaignCreate["primary_goal"]) : null,
      audience_hints: v.audience_hints || null,
    };
    try {
      const created = await api<Campaign>("/api/v1/campaigns", {
        method: "POST",
        body: payload as unknown as Record<string, unknown>,
      });
      track("campaign_created", { campaign_id: created.id });
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
      navigate({ to: "/campaigns" });
    } catch (e) {
      setSubmitError(errMsg(e));
    }
  });

  return (
    <>
      <Topbar
        title="New campaign"
        right={
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => navigate({ to: "/campaigns" })}
          >
            Cancel
          </button>
        }
      />
      <form onSubmit={onSubmit} className="scroll" style={{ padding: 24, flex: 1 }}>
        <div style={{ maxWidth: 760, margin: "0 auto", display: "grid", gap: 16 }}>
          <Section title="1. Brief">
            <Field label="Name *" error={errors.name?.message}>
              <input className="input" placeholder="e.g. EU SMB outreach — Q2" {...register("name")} />
            </Field>
            <Field label="Description / brief">
              <textarea
                className="textarea"
                rows={2}
                placeholder="What this campaign is for, in plain language."
                {...register("description")}
              />
            </Field>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => autoFillMut.mutate()}
                disabled={autoFillMut.isPending}
                style={{ color: "var(--ai-purple, #8774e1)" }}
              >
                {autoFillMut.isPending ? "Filling…" : "✨ AI fill from brief"}
              </button>
            </div>
          </Section>

          <Section title="2. Targeting">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Field label="Agent *" error={errors.agent_id?.message}>
                <select className="select" {...register("agent_id")}>
                  <option value="">— Pick an agent —</option>
                  {agentsQ.data?.agents.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Contact folder *" error={errors.folder_id?.message}>
                <select className="select" {...register("folder_id")}>
                  <option value="">— Pick a folder —</option>
                  {foldersQ.data?.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name} ({f.contact_count})
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="Audience hints">
              <input
                className="input"
                placeholder="e.g. SaaS founders, 10-50 employees"
                {...register("audience_hints")}
              />
            </Field>
          </Section>

          <Section title="3. Senders">
            {errors.sender_ids?.message && (
              <div style={{ fontSize: 11.5, color: "var(--danger, #c0392b)", marginBottom: 6 }}>
                {errors.sender_ids.message}
              </div>
            )}
            <div
              style={{
                border: "1px solid var(--border)",
                borderRadius: 8,
                maxHeight: 200,
                overflow: "auto",
              }}
            >
              {sendersQ.data?.senders.length === 0 && (
                <div style={{ padding: 16, fontSize: 13 }} className="muted">
                  No accounts. Connect one in TG accounts first.
                </div>
              )}
              {sendersQ.data?.senders.map((s) => {
                const checked = (sender_ids ?? []).includes(s.id);
                const disabled = s.status === "error";
                return (
                  <label
                    key={s.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "8px 12px",
                      borderBottom: "1px solid var(--border)",
                      cursor: disabled ? "not-allowed" : "pointer",
                      opacity: disabled ? 0.5 : 1,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggleSender(s.id)}
                    />
                    <div style={{ flex: 1, fontSize: 13 }}>
                      <div style={{ fontWeight: 500 }}>
                        {s.name || `@${s.slug}`}
                      </div>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {s.phone} · {s.status}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          </Section>

          <Section title="4. First message">
            <Field
              label="Message template *"
              error={errors.message_template?.message}
              hint="Use {{first_name}}, {{full_name}}, {{username}} as placeholders."
            >
              <textarea
                className="textarea"
                rows={5}
                {...register("message_template")}
              />
            </Field>
            <Field label="Primary goal">
              <select className="select" {...register("primary_goal")}>
                <option value="">— Optional —</option>
                <option value="book_meeting">Book a meeting</option>
                <option value="qualify">Qualify</option>
                <option value="click">Drive a click</option>
                <option value="engage">Engage</option>
              </select>
            </Field>
          </Section>

          <Section title="5. Schedule">
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
              <Field label="Timezone *">
                <input className="input" placeholder="Europe/London" {...register("timezone")} />
              </Field>
              <Field label="Work start (h)">
                <input className="input" type="number" min={0} max={23} {...register("work_hour_start")} />
              </Field>
              <Field label="Work end (h)">
                <input className="input" type="number" min={1} max={24} {...register("work_hour_end")} />
              </Field>
            </div>
            <p className="muted" style={{ fontSize: 11 }}>
              Rate limits (4/min, 20/hr, 150/day per account) are enforced server-side.
            </p>
          </Section>

          {submitError && (
            <div
              style={{
                padding: 12,
                borderRadius: 8,
                background: "color-mix(in oklab, var(--danger, #c0392b) 10%, transparent)",
                color: "var(--danger, #c0392b)",
                fontSize: 13,
              }}
            >
              {submitError}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => navigate({ to: "/campaigns" })}
            >
              Cancel
            </button>
            <button type="submit" className="btn btn--primary" disabled={isSubmitting}>
              {isSubmitting ? "Creating…" : "Create as draft"}
            </button>
          </div>
        </div>
      </form>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card" style={{ padding: 18 }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>{title}</h3>
      <div style={{ display: "grid", gap: 12 }}>{children}</div>
    </section>
  );
}

function Field({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label className="field__label">{label}</label>
      {children}
      {hint && !error && <span className="field__hint">{hint}</span>}
      {error && <span style={{ fontSize: 11.5, color: "var(--danger, #c0392b)" }}>{error}</span>}
    </div>
  );
}
