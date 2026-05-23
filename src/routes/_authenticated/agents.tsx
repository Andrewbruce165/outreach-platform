import { createFileRoute } from "@tanstack/react-router";
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
type AgentCreate = components["schemas"]["AgentCreate"];
type AgentUpdate = components["schemas"]["AgentUpdate"];
type AgentListResponse = components["schemas"]["AgentListResponse"];

export const Route = createFileRoute("/_authenticated/agents")({
  component: AgentsPage,
});

const VOICE_OPTIONS = ["Professional", "Friendly", "Playful"] as const;

const agentSchema = z.object({
  name: z.string().min(1, "Required").max(100),
  who_is_agent: z.string().max(500).optional().or(z.literal("")),
  company_info: z.string().max(2000).optional().or(z.literal("")),
  product_info: z.string().max(2000).optional().or(z.literal("")),
  voice_baseline: z.enum(VOICE_OPTIONS).optional().or(z.literal("")),
  rules: z.string().max(2000).optional().or(z.literal("")),
  max_message_length: z
    .union([z.coerce.number().int().min(1).max(4096), z.literal("")])
    .optional(),
  mirror_language: z.boolean().optional(),
  allow_emoji: z.boolean().optional(),
});

type FormValues = z.infer<typeof agentSchema>;

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function AgentsPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Agent | "new" | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<AgentListResponse>("/api/v1/agents"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api(`/api/v1/agents/${id}`, { method: "DELETE" }),
    onSuccess: (_d, id) => {
      track("agent_deleted", { agent_id: id });
      void qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  const duplicateMut = useMutation({
    mutationFn: (id: string) =>
      api<Agent>(`/api/v1/agents/${id}/duplicate`, { method: "POST" }),
    onSuccess: (d) => {
      track("agent_duplicated", { agent_id: d.id });
      void qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  return (
    <>
      <Topbar
        title="Agents"
        right={
          <button className="btn btn--primary btn--sm" onClick={() => setEditing("new")}>
            + New agent
          </button>
        }
      />
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        {isLoading && <div className="muted">Loading…</div>}
        {error && (
          <div className="card" style={{ padding: 16, color: "var(--danger, #c0392b)" }}>
            {errMsg(error)}
          </div>
        )}
        {data && data.agents.length === 0 && !isLoading && (
          <EmptyState onCreate={() => setEditing("new")} />
        )}
        {data && data.agents.length > 0 && (
          <div className="card">
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                  <th style={th}>Name</th>
                  <th style={th}>Voice</th>
                  <th style={th}>Campaigns</th>
                  <th style={th}>Updated</th>
                  <th style={{ ...th, width: 1 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.agents.map((a) => (
                  <tr key={a.id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={td}>
                      <button
                        type="button"
                        onClick={() => setEditing(a)}
                        style={{
                          background: "none",
                          border: 0,
                          padding: 0,
                          fontWeight: 600,
                          color: "var(--tg-blue)",
                          cursor: "pointer",
                        }}
                      >
                        {a.name}
                      </button>
                    </td>
                    <td style={{ ...td, color: "var(--text-soft)" }}>
                      {a.voice_baseline || "—"}
                    </td>
                    <td style={td}>{a.campaign_count ?? 0}</td>
                    <td style={{ ...td, color: "var(--text-soft)" }}>
                      {a.updated_at ? new Date(a.updated_at).toLocaleDateString() : "—"}
                    </td>
                    <td style={td}>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          className="btn btn--ghost btn--sm"
                          onClick={() => duplicateMut.mutate(a.id)}
                          disabled={duplicateMut.isPending}
                        >
                          Duplicate
                        </button>
                        <button
                          className="btn btn--ghost btn--sm"
                          onClick={() => {
                            if (confirm(`Delete agent "${a.name}"?`)) deleteMut.mutate(a.id);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editing && (
        <AgentEditor
          agent={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            void qc.invalidateQueries({ queryKey: ["agents"] });
            setEditing(null);
          }}
        />
      )}
    </>
  );
}

const th: React.CSSProperties = {
  padding: "10px 14px",
  fontSize: 12,
  fontWeight: 500,
  color: "var(--text-soft)",
};
const td: React.CSSProperties = { padding: "12px 14px", fontSize: 13 };

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div
      style={{
        textAlign: "center",
        padding: "64px 24px",
        maxWidth: 440,
        margin: "0 auto",
      }}
    >
      <div style={{ fontSize: 40, marginBottom: 12 }}>🤖</div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>No agents yet</h3>
      <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>
        Agents define your AI's voice, knowledge, and rules. Create one to use in a campaign.
      </p>
      <button className="btn btn--primary" onClick={onCreate}>
        Create your first agent
      </button>
    </div>
  );
}

function AgentEditor({
  agent,
  onClose,
  onSaved,
}: {
  agent: Agent | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = !agent;
  const [submitError, setSubmitError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(agentSchema),
    defaultValues: {
      name: agent?.name ?? "",
      who_is_agent: agent?.who_is_agent ?? "",
      company_info: agent?.company_info ?? "",
      product_info: agent?.product_info ?? "",
      voice_baseline:
        (agent?.voice_baseline as FormValues["voice_baseline"]) ?? "",
      rules: agent?.rules ?? "",
      max_message_length: (agent?.max_message_length ?? "") as FormValues["max_message_length"],
      mirror_language: agent?.mirror_language ?? true,
      allow_emoji: agent?.allow_emoji ?? true,
    },
  });

  const onSubmit = handleSubmit(async (values) => {
    setSubmitError(null);
    const payload: AgentCreate | AgentUpdate = {
      name: values.name,
      who_is_agent: values.who_is_agent || null,
      company_info: values.company_info || null,
      product_info: values.product_info || null,
      voice_baseline: values.voice_baseline
        ? (values.voice_baseline as "Professional" | "Friendly" | "Playful")
        : null,
      rules: values.rules || null,
      max_message_length:
        values.max_message_length === "" || values.max_message_length == null
          ? null
          : Number(values.max_message_length),
      mirror_language: values.mirror_language ?? null,
      allow_emoji: values.allow_emoji ?? null,
    };
    try {
      if (isNew) {
        const created = await api<Agent>("/api/v1/agents", {
          method: "POST",
          body: payload as unknown as Record<string, unknown>,
        });
        track("agent_created", { agent_id: created.id, name: values.name });
      } else {
        await api<Agent>(`/api/v1/agents/${agent.id}`, {
          method: "PATCH",
          body: payload as unknown as Record<string, unknown>,
        });
        track("agent_updated", { agent_id: agent.id });
      }
      onSaved();
    } catch (e) {
      setSubmitError(errMsg(e));
    }
  });

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 720, width: "92vw", maxHeight: "90vh", display: "flex", flexDirection: "column" }}
      >
        <header className="modal__head">
          <h3>{isNew ? "Create agent" : `Edit ${agent.name}`}</h3>
          <button className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <form onSubmit={onSubmit} style={{ display: "contents" }}>
          <div className="modal__body" style={{ display: "grid", gap: 14 }}>
            <Field label="Name *" error={errors.name?.message}>
              <input
                className="input"
                placeholder="e.g. Sales rep — EU SMB"
                {...register("name")}
              />
            </Field>

            <Field label="Who is the agent?">
              <textarea
                className="textarea"
                rows={2}
                placeholder="Short persona: role, company, what they do."
                {...register("who_is_agent")}
              />
            </Field>

            <Field label="Company info">
              <textarea
                className="textarea"
                rows={3}
                placeholder="What does your company do, who do you serve?"
                {...register("company_info")}
              />
            </Field>

            <Field label="Product info">
              <textarea
                className="textarea"
                rows={3}
                placeholder="What you're pitching, key value props."
                {...register("product_info")}
              />
            </Field>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Voice baseline">
                <select className="select" {...register("voice_baseline")}>
                  <option value="">— Select —</option>
                  {VOICE_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Max message length">
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={4096}
                  placeholder="e.g. 280"
                  {...register("max_message_length")}
                />
              </Field>
            </div>

            <Field label="Rules / guardrails">
              <textarea
                className="textarea"
                rows={3}
                placeholder="Things the agent must never say or do."
                {...register("rules")}
              />
            </Field>

            <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
              <label style={cbStyle}>
                <input type="checkbox" {...register("mirror_language")} />
                Mirror contact's language
              </label>
              <label style={cbStyle}>
                <input type="checkbox" {...register("allow_emoji")} />
                Allow emoji
              </label>
            </div>

            {submitError && (
              <div
                style={{
                  padding: 10,
                  borderRadius: 8,
                  background: "color-mix(in oklab, var(--danger, #c0392b) 10%, transparent)",
                  color: "var(--danger, #c0392b)",
                  fontSize: 13,
                }}
              >
                {submitError}
              </div>
            )}
          </div>
          <footer
            style={{
              padding: 14,
              borderTop: "1px solid var(--border)",
              display: "flex",
              justifyContent: "flex-end",
              gap: 8,
            }}
          >
            <button type="button" className="btn btn--ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn--primary" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : isNew ? "Create agent" : "Save changes"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}

const cbStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 13,
  color: "var(--text)",
  cursor: "pointer",
};

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label className="field__label">{label}</label>
      {children}
      {error && (
        <span style={{ fontSize: 11.5, color: "var(--danger, #c0392b)" }}>{error}</span>
      )}
    </div>
  );
}
