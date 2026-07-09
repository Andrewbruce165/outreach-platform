import { createFileRoute } from "@tanstack/react-router";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Agent = components["schemas"]["AgentResponse"];
type AgentCreate = components["schemas"]["AgentCreate"];
type AgentUpdate = components["schemas"]["AgentUpdate"];
type AgentListResponse = components["schemas"]["AgentListResponse"];
type KnowledgeBase = components["schemas"]["KnowledgeBaseResponse"];
type AgentForKb = components["schemas"]["AgentForKbResponse"];

export const Route = createFileRoute("/_authenticated/agents")({
  component: AgentsPage,
});

// Phase 11 D-01: single tone source (replaces voice_baseline + tone sliders + tone_of_voice)
const TONE_OPTIONS = ["Friendly", "Professional", "Direct", "Casual"] as const;
type TonePreset = (typeof TONE_OPTIONS)[number];

// Phase 11 D-11: response speed presets
const SPEED_OPTIONS = [
  { value: "instant", label: "Мгновенно" },
  { value: "human", label: "Как человек" },
  { value: "slow", label: "Медленно" },
  { value: "manual", label: "Вручную" },
] as const;
type ResponseSpeed = (typeof SPEED_OPTIONS)[number]["value"];

const agentSchema = z.object({
  name: z.string().min(1, "Required").max(100),
  who_is_agent: z.string().optional().or(z.literal("")),
  company_info: z.string().optional().or(z.literal("")),
  product_info: z.string().optional().or(z.literal("")),
  tone_preset: z.enum(TONE_OPTIONS).optional().or(z.literal("")),
  rules: z.string().optional().or(z.literal("")),
  response_speed: z
    .enum(["instant", "human", "slow", "manual"])
    .optional()
    .or(z.literal("")),
  response_delay_seconds: z
    .union([z.coerce.number().int().min(0), z.literal("")])
    .optional(),
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
  return "Что-то пошло не так. Попробуйте ещё раз.";
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
                  <th style={th}>Тон</th>
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
                      {a.tone_preset || "—"}
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
                            if (confirm(`Удалить агента "${a.name}"? Это действие необратимо.`)) deleteMut.mutate(a.id);
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
  // Phase 16: KBs picked while CREATING an agent. The M:N attach needs the agent
  // id (only exists after save), so for a new agent we collect selections locally
  // and attach them right after the POST succeeds (deferred attach → one-step UX).
  const [pendingKbIds, setPendingKbIds] = useState<string[]>([]);
  const qc = useQueryClient();

  const {
    register,
    watch,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(agentSchema),
    defaultValues: {
      name: agent?.name ?? "",
      who_is_agent: agent?.who_is_agent ?? "",
      company_info: agent?.company_info ?? "",
      product_info: agent?.product_info ?? "",
      tone_preset: (agent?.tone_preset as FormValues["tone_preset"]) ?? "",
      rules: agent?.rules ?? "",
      response_speed: (agent?.response_speed as FormValues["response_speed"]) ?? "human",
      response_delay_seconds: (agent?.response_delay_seconds ?? "") as FormValues["response_delay_seconds"],
      max_message_length: (agent?.max_message_length ?? "") as FormValues["max_message_length"],
      mirror_language: agent?.mirror_language ?? true,
      allow_emoji: agent?.allow_emoji ?? true,
    },
  });

  const responseSpeed = watch("response_speed");

  const onSubmit = handleSubmit(async (values) => {
    setSubmitError(null);
    const payload: AgentCreate | AgentUpdate = {
      name: values.name,
      who_is_agent: values.who_is_agent || null,
      company_info: values.company_info || null,
      product_info: values.product_info || null,
      tone_preset: values.tone_preset
        ? (values.tone_preset as TonePreset)
        : null,
      rules: values.rules || null,
      response_speed: (values.response_speed as ResponseSpeed) || "human",
      response_delay_seconds:
        values.response_speed === "manual" &&
        values.response_delay_seconds !== "" &&
        values.response_delay_seconds != null
          ? Number(values.response_delay_seconds)
          : null,
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
        // Deferred KB attach: now that the agent has an id, attach the KBs the
        // user picked in the create form. Partial failures don't lose the agent.
        if (pendingKbIds.length) {
          const results = await Promise.allSettled(
            pendingKbIds.map((kbId) =>
              api(`/api/v1/knowledge-bases/${kbId}/agents`, {
                method: "POST",
                body: { agent_id: created.id },
              }),
            ),
          );
          pendingKbIds.forEach((kbId) =>
            void qc.invalidateQueries({ queryKey: ["kb-agents", kbId] }),
          );
          void qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
          const failed = results.filter((r) => r.status === "rejected").length;
          if (failed > 0) {
            toast.error(
              `Агент создан, но ${failed} баз(у/ы) не удалось подключить — добавьте их в редактировании`,
            );
          }
        }
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
          <button className="tb__icon-btn" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </header>
        <form onSubmit={onSubmit} style={{ display: "contents" }}>
          <div className="modal__body" style={{ display: "grid", gap: 14 }}>

            <Field label="Название *" error={errors.name?.message}>
              <input
                className="input"
                placeholder="e.g. Sales rep — EU SMB"
                {...register("name")}
              />
            </Field>

            {/* Идентичность — КТО: имя, роль, характер, манера речи. Без задачи/цели. */}
            <Field label="Идентичность">
              <textarea
                className="textarea"
                rows={2}
                placeholder="Имя, роль, характер, манера речи."
                {...register("who_is_agent")}
              />
            </Field>

            <Field label="Информация о компании">
              <textarea
                className="textarea"
                rows={3}
                placeholder="Чем занимается компания, кому помогает?"
                {...register("company_info")}
              />
            </Field>

            <Field label="Что продаёт">
              <textarea
                className="textarea"
                rows={3}
                placeholder="Продукт / услуга, ключевые ценности."
                {...register("product_info")}
              />
            </Field>

            {/* Phase 11 D-01: tone_preset replaces voice_baseline + tone sliders + tone_of_voice */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Тон">
                <select className="select" {...register("tone_preset")}>
                  <option value="">— Выберите —</option>
                  {TONE_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
                <span className="field__hint">
                  Единый тон агента. Не дублируйте тон в жёстких правилах.
                </span>
              </Field>
              <Field label="Макс. длина сообщения">
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

            {/* Phase 11 D-11: response_speed control */}
            <Field label="Скорость ответа">
              <select className="select" {...register("response_speed")}>
                {SPEED_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>

            {responseSpeed === "manual" && (
              <Field label="Задержка, сек">
                <input
                  className="input"
                  type="number"
                  min={0}
                  placeholder="e.g. 120"
                  {...register("response_delay_seconds")}
                />
                <span className="field__hint">
                  Фиксированная задержка перед ответом ИИ, в секундах.
                </span>
              </Field>
            )}

            {/* Жёсткие правила — только запреты и стоп-темы, без тона (D-03) */}
            <Field label="Жёсткие правила">
              <textarea
                className="textarea"
                rows={3}
                placeholder="Только запреты и стоп-темы."
                {...register("rules")}
              />
            </Field>

            {/*
              Phase 16 D-07/D-08: M:N knowledge-base attach/detach. This is a
              SEPARATE control from the static `knowledge_base` text field (D-08) —
              the multi-select is additional. Attach/detach hit
              POST/DELETE /knowledge-bases/{kb_id}/agents and need an agent id, so
              for a brand-new agent we prompt the user to save first.
            */}
            <Field label="Базы знаний">
              <KbMultiSelect
                agentId={agent?.id ?? null}
                pendingKbIds={pendingKbIds}
                onPendingChange={setPendingKbIds}
              />
              <span className="field__hint">
                Агент обращается к этим базам по необходимости во время ответа.
              </span>
            </Field>

            <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
              <label style={cbStyle}>
                <input type="checkbox" {...register("mirror_language")} />
                Зеркалить язык контакта
              </label>
              <label style={cbStyle}>
                <input type="checkbox" {...register("allow_emoji")} />
                Разрешить эмодзи
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
              Отмена
            </button>
            <button type="submit" className="btn btn--primary" disabled={isSubmitting}>
              {isSubmitting ? "Сохранение…" : isNew ? "Создать агента" : "Сохранить агента"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}

/**
 * Phase 16 D-07: KB multi-select on the agent editor. M:N attach/detach against
 * POST/DELETE /knowledge-bases/{kb_id}/agents.
 *
 * The backend exposes the M:N only from the KB side (reverse list
 * GET /knowledge-bases/{kb_id}/agents), so this agent's attached set is derived
 * by checking each workspace KB's agent list for this agent's id. With a small
 * number of workspace KBs this is a handful of cached queries.
 *
 * D-08: this is ADDITIONAL to the static `knowledge_base` text field — they are
 * independent controls.
 */
function KbMultiSelect({
  agentId,
  pendingKbIds,
  onPendingChange,
}: {
  agentId: string | null;
  // Deferred mode (new agent): selection is held locally and attached by the
  // parent form right after the agent is created. When agentId is set, these are
  // ignored and live attach/detach mutations are used instead.
  pendingKbIds?: string[];
  onPendingChange?: (ids: string[]) => void;
}) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);

  const kbsQ = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api<KnowledgeBase[]>("/api/v1/knowledge-bases"),
    staleTime: 60_000,
  });
  const kbs = kbsQ.data ?? [];

  // Per-KB reverse lists — one cached query each. Only needed when editing an
  // existing agent (a new agent isn't attached to anything yet).
  const agentListQs = useQueries({
    queries: kbs.map((kb) => ({
      queryKey: ["kb-agents", kb.id],
      queryFn: () =>
        api<AgentForKb[]>(`/api/v1/knowledge-bases/${kb.id}/agents`),
      enabled: !!agentId,
      staleTime: 60_000,
    })),
  });

  const attachedKbIds = new Set<string>();
  if (agentId) {
    kbs.forEach((kb, i) => {
      const agents = agentListQs[i]?.data ?? [];
      if (agents.some((a) => a.agent_id === agentId || a.id === agentId)) {
        attachedKbIds.add(kb.id);
      }
    });
  }

  const invalidate = (kbId: string) => {
    void qc.invalidateQueries({ queryKey: ["kb-agents", kbId] });
    void qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
  };

  const attachMut = useMutation({
    mutationFn: (kbId: string) =>
      api(`/api/v1/knowledge-bases/${kbId}/agents`, {
        method: "POST",
        body: { agent_id: agentId },
      }),
    onSuccess: (_d, kbId) => {
      toast.success("База знаний подключена");
      invalidate(kbId);
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const detachMut = useMutation({
    mutationFn: (kbId: string) =>
      api(`/api/v1/knowledge-bases/${kbId}/agents/${agentId}`, {
        method: "DELETE",
      }),
    onSuccess: (_d, kbId) => {
      toast.success("База знаний отключена");
      invalidate(kbId);
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const busy = attachMut.isPending || detachMut.isPending;

  // New agent + a deferred handler wired → select KBs locally (the parent attaches
  // them once the agent is created). busy stays false here (mutations never fire).
  const deferred = !agentId && !!onPendingChange;

  // New agent with no deferred handler → fall back to the save-first hint.
  if (!agentId && !deferred) {
    return (
      <div className="muted text-xs" style={{ lineHeight: 1.5 }}>
        Сохраните агента, чтобы подключить базы знаний.
      </div>
    );
  }

  if (kbsQ.isLoading) {
    return <div className="muted text-xs">Загрузка баз знаний…</div>;
  }

  const isAttached = (kbId: string) =>
    deferred ? (pendingKbIds ?? []).includes(kbId) : attachedKbIds.has(kbId);
  const add = (kbId: string) => {
    if (deferred) onPendingChange!([...(pendingKbIds ?? []), kbId]);
    else attachMut.mutate(kbId);
  };
  const remove = (kbId: string) => {
    if (deferred) onPendingChange!((pendingKbIds ?? []).filter((id) => id !== kbId));
    else detachMut.mutate(kbId);
  };

  const selected = kbs.filter((kb) => isAttached(kb.id));
  const available = kbs.filter((kb) => !isAttached(kb.id));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {selected.length === 0 ? (
        <div className="muted text-xs">Базы знаний не подключены.</div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {selected.map((kb) => (
            <span
              key={kb.id}
              className="pill pill--blue"
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <span className="pill__dot" style={{ background: "var(--tg-blue)" }} />
              {kb.name}
              <button
                type="button"
                aria-label={`Отключить ${kb.name}`}
                disabled={busy}
                onClick={() => remove(kb.id)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  background: "none",
                  border: 0,
                  padding: 0,
                  cursor: busy ? "default" : "pointer",
                  color: "inherit",
                  opacity: busy ? 0.6 : 1,
                }}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {available.length > 0 && (
        <div style={{ position: "relative" }}>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={busy}
            onClick={() => setPicking((v) => !v)}
          >
            <Plus size={13} /> Подключить базу
          </button>
          {picking && (
            <div
              className="card"
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                left: 0,
                minWidth: 220,
                padding: 4,
                zIndex: 40,
                boxShadow: "var(--shadow-lg)",
              }}
            >
              {available.map((kb) => (
                <button
                  key={kb.id}
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setPicking(false);
                    add(kb.id);
                  }}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "8px 12px",
                    fontSize: 13,
                    color: "var(--text)",
                    background: "none",
                    border: 0,
                    cursor: "pointer",
                  }}
                >
                  {kb.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
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
