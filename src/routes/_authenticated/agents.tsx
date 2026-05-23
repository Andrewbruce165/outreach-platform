import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Topbar } from "@/components/Topbar";
import { api } from "@/lib/api";
import { errorCopy } from "@/lib/error-codes";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Agent = components["schemas"]["AgentResponse"];
type AgentCreate = components["schemas"]["AgentCreate"];
type AgentUpdate = components["schemas"]["AgentUpdate"];

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
  max_message_length: z.coerce.number().int().min(1).max(4096).optional().or(z.literal("" as unknown as number)),
  mirror_language: z.boolean().optional(),
  allow_emoji: z.boolean().optional(),
});

type FormValues = z.infer<typeof agentSchema>;

function AgentsPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Agent | "new" | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => api.get<{ agents: Agent[]; total: number }>("/api/v1/agents"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.del(`/api/v1/agents/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });

  const duplicateMut = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/agents/${id}/duplicate`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });

  return (
    <>
      <Topbar
        title="Agents"
        actions={
          <button className="btn btn--primary" onClick={() => setEditing("new")}>
            + New agent
          </button>
        }
      />
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        {isLoading && <div className="muted">Loading…</div>}
        {error && <div className="alert alert--error">{errorCopy(error)}</div>}
        {data && data.agents.length === 0 && (
          <EmptyState onCreate={() => setEditing("new")} />
        )}
        {data && data.agents.length > 0 && (
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Voice</th>
                  <th>Campaigns</th>
                  <th>Updated</th>
                  <th style={{ width: 1 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.agents.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <button
                        className="link"
                        onClick={() => setEditing(a)}
                        style={{ fontWeight: 600 }}
                      >
                        {a.name}
                      </button>
                    </td>
                    <td className="muted">{a.voice_baseline || "—"}</td>
                    <td>{(a as Agent & { campaign_count?: number }).campaign_count ?? 0}</td>
                    <td className="muted">
                      {a.updated_at ? new Date(a.updated_at).toLocaleDateString() : "—"}
                    </td>
                    <td>
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
            qc.invalidateQueries({ queryKey: ["agents"] });
            setEditing(null);
          }}
        />
      )}
    </>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="empty">
      <div className="empty__icon">🤖</div>
      <h3 className="empty__title">No agents yet</h3>
      <p className="empty__body">
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
      voice_baseline: (agent?.voice_baseline as FormValues["voice_baseline"]) ?? "",
      rules: agent?.rules ?? "",
      max_message_length: agent?.max_message_length ?? (undefined as unknown as number),
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
      voice_baseline: values.voice_baseline ? (values.voice_baseline as "Professional" | "Friendly" | "Playful") : null,
      rules: values.rules || null,
      max_message_length: values.max_message_length
        ? Number(values.max_message_length)
        : null,
      mirror_language: values.mirror_language ?? null,
      allow_emoji: values.allow_emoji ?? null,
    };
    try {
      if (isNew) {
        await api.post<Agent>("/api/v1/agents", payload);
        track("agent_created", { name: values.name });
      } else {
        await api.patch<Agent>(`/api/v1/agents/${agent.id}`, payload);
        track("agent_updated", { agent_id: agent.id });
      }
      onSaved();
    } catch (e) {
      setSubmitError(errorCopy(e));
    }
  });

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>{isNew ? "Create agent" : `Edit ${agent.name}`}</h2>
          <button className="modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <form onSubmit={onSubmit} className="modal__body">
          <div className="form-grid">
            <div className="field">
              <label>Name *</label>
              <input
                className="input"
                placeholder="e.g. Sales rep — EU SMB"
                {...register("name")}
              />
              {errors.name && <span className="field__error">{errors.name.message}</span>}
            </div>

            <div className="field">
              <label>Who is the agent?</label>
              <textarea
                className="input"
                rows={2}
                placeholder="Short persona: role, company, what they do."
                {...register("who_is_agent")}
              />
            </div>

            <div className="field">
              <label>Company info</label>
              <textarea
                className="input"
                rows={3}
                placeholder="What does your company do, who do you serve?"
                {...register("company_info")}
              />
            </div>

            <div className="field">
              <label>Product info</label>
              <textarea
                className="input"
                rows={3}
                placeholder="What you're pitching, key value props."
                {...register("product_info")}
              />
            </div>

            <div className="field">
              <label>Voice baseline</label>
              <select className="input" {...register("voice_baseline")}>
                <option value="">— Select —</option>
                {VOICE_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label>Rules / guardrails</label>
              <textarea
                className="input"
                rows={3}
                placeholder="Things the agent must never say or do."
                {...register("rules")}
              />
            </div>

            <div className="field-row">
              <div className="field">
                <label>Max message length</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={4096}
                  placeholder="e.g. 280"
                  {...register("max_message_length")}
                />
              </div>
              <label className="checkbox">
                <input type="checkbox" {...register("mirror_language")} />
                Mirror contact's language
              </label>
              <label className="checkbox">
                <input type="checkbox" {...register("allow_emoji")} />
                Allow emoji
              </label>
            </div>
          </div>

          {submitError && <div className="alert alert--error">{submitError}</div>}

          <div className="modal__footer">
            <button type="button" className="btn btn--ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn--primary" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : isNew ? "Create agent" : "Save changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
