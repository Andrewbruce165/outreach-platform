import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, Plus, Trash2, LogOut, Loader2 } from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { errorMessageFromEnvelope } from "@/lib/error-codes";
import { supabase } from "@/lib/supabase";
import { track } from "@/lib/telemetry";

export const Route = createFileRoute("/_authenticated/settings")({
  component: SettingsPage,
});

type Tab = "workspace" | "ai-llm" | "api-keys" | "members" | "profile" | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "workspace", label: "Workspace" },
  { id: "ai-llm", label: "AI / LLM" },
  { id: "api-keys", label: "API keys" },
  { id: "members", label: "Members" },
  { id: "profile", label: "Profile" },
  { id: "appearance", label: "Appearance" },
];

interface Workspace {
  id: string;
  name: string;
  timezone?: string;
  plan?: string;
}

interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at?: string | null;
}

function SettingsPage() {
  const [tab, setTab] = useState<Tab>("workspace");
  return (
    <>
      <Topbar title="Settings" />
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? "is-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        {tab === "workspace" && <WorkspaceTab />}
        {tab === "ai-llm" && <AiLlmTab />}
        {tab === "api-keys" && <ApiKeysTab />}
        {tab === "members" && <MembersTab />}
        {tab === "profile" && <ProfileTab />}
        {tab === "appearance" && <AppearanceTab />}
      </div>
    </>
  );
}

function Card({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ maxWidth: 720 }}>
      <div className="card__header">
        <div>
          <div className="card__title">{title}</div>
          {sub && <div className="card__sub">{sub}</div>}
        </div>
      </div>
      <div className="card__body">{children}</div>
    </div>
  );
}

function WorkspaceTab() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["workspace"],
    queryFn: () => api<Workspace>("/api/v1/workspace"),
    retry: false,
  });
  const [name, setName] = useState("");
  useEffect(() => {
    if (data?.name) setName(data.name);
  }, [data?.name]);

  const save = useMutation({
    mutationFn: (payload: Partial<Workspace>) =>
      api<Workspace>("/api/v1/workspace", { method: "PATCH", body: payload }),
    onSuccess: () => {
      track("settings_changed", { tab: "workspace" });
      toast.success("Workspace updated");
      qc.invalidateQueries({ queryKey: ["workspace"] });
    },
    onError: (e: unknown) => {
      toast.error(e instanceof ApiError ? e.message : "Could not save");
    },
  });

  if (isLoading) return <Skeleton />;
  if (error) return <ErrorState error={error} />;

  return (
    <Card title="Workspace" sub="Visible to your team and on outbound messages.">
      <div className="field" style={{ marginBottom: 16 }}>
        <label className="field__label" htmlFor="ws-name">Workspace name</label>
        <input
          id="ws-name"
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="field" style={{ marginBottom: 16 }}>
        <label className="field__label">Timezone</label>
        <input className="input" value={data?.timezone ?? "UTC"} disabled />
        <div className="field__hint">Detected from your browser. Editing lands in v2.</div>
      </div>
      <div className="field" style={{ marginBottom: 20 }}>
        <label className="field__label">Plan</label>
        <input className="input" value={data?.plan ?? "Free"} disabled />
      </div>
      <button
        className="btn btn--primary"
        disabled={save.isPending || !name || name === data?.name}
        onClick={() => save.mutate({ name })}
      >
        {save.isPending ? "Saving…" : "Save changes"}
      </button>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// AI / LLM settings — provider switch + BYO key + live model list + capability-
// gated knobs with the D-10 green corridor. Wired to /api/v1/workspace/llm-settings.
// ---------------------------------------------------------------------------

type LlmProvider = "openai" | "anthropic";

interface LlmSettings {
  provider: LlmProvider;
  model: string | null;
  api_key_prefix: string | null;
  api_key_status: "unset" | "valid" | "invalid";
  temperature: number | null;
  reasoning_effort: string | null;
  max_tokens: number | null;
}

interface LlmModelList {
  models: string[];
  note?: string | null;
}

interface LlmTestConnection {
  status: "valid" | "invalid";
  detail?: string | null;
}

const REASONING_FLOOR = 4000; // D-10 — reasoning models must not go below this.
const MAX_TOKENS_CEILING = 32000; // D-10 green-corridor ceiling.
const EFFORT_LEVELS = ["minimal", "low", "medium", "high"] as const;

// Mirror of app/services/llm/capabilities.is_reasoning_model (OpenAI reasoning
// family: gpt-5*/o1/o3/o4*). Claude reasoning is provider-driven, gated separately.
function isOpenAiReasoning(model: string | null): boolean {
  const m = (model ?? "").toLowerCase();
  return (
    m.startsWith("gpt-5") ||
    m.startsWith("o1") ||
    m.startsWith("o3") ||
    m.startsWith("o4")
  );
}

// D-09: temperature shows for non-reasoning OpenAI models + ALL Claude models.
function supportsTemperature(provider: LlmProvider, model: string | null): boolean {
  if (provider === "anthropic") return true;
  return !isOpenAiReasoning(model);
}

// D-09: reasoning-effort shows for OpenAI reasoning models + ALL Claude models.
function supportsReasoningEffort(provider: LlmProvider, model: string | null): boolean {
  if (provider === "anthropic") return true;
  return isOpenAiReasoning(model);
}

// D-10 green corridor: the reasoning-family floor applies to OpenAI reasoning
// models and Claude (extended thinking eats the budget the same way).
function hasReasoningFloor(provider: LlmProvider, model: string | null): boolean {
  return provider === "anthropic" || isOpenAiReasoning(model);
}

function AiLlmTab() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: () => api<LlmSettings>("/api/v1/workspace/llm-settings"),
    retry: false,
  });

  // Local editable state, seeded from the server settings.
  const [provider, setProvider] = useState<LlmProvider>("openai");
  const [model, setModel] = useState<string>("");
  const [apiKey, setApiKey] = useState<string>(""); // never pre-filled — write-only.
  const [temperature, setTemperature] = useState<number>(1);
  const [reasoningEffort, setReasoningEffort] = useState<string>("medium");
  const [maxTokens, setMaxTokens] = useState<number>(REASONING_FLOOR);
  // Connection must be verified before saving. Cleared on any form change.
  const [testedOk, setTestedOk] = useState<boolean>(false);

  // Seed form from server settings for the currently-active provider only.
  function seedFromServer(next: LlmSettings) {
    setProvider(next.provider ?? "openai");
    setModel(next.model ?? "");
    setTemperature(next.temperature ?? 1);
    setReasoningEffort(next.reasoning_effort ?? "medium");
    setMaxTokens(next.max_tokens ?? REASONING_FLOOR);
    setApiKey("");
    // A stored valid key means we already have a proven connection on the server.
    setTestedOk(next.api_key_status === "valid");
  }

  useEffect(() => {
    if (!data) return;
    seedFromServer(data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  function onProviderChange(next: LlmProvider) {
    setProvider(next);
    setTestedOk(false);
    if (data && data.provider === next) {
      // Coming back to the stored provider — restore its saved values.
      setModel(data.model ?? "");
      setTemperature(data.temperature ?? 1);
      setReasoningEffort(data.reasoning_effort ?? "medium");
      setMaxTokens(data.max_tokens ?? REASONING_FLOOR);
      setApiKey("");
      setTestedOk(data.api_key_status === "valid");
    } else {
      // No saved data for this provider — show an empty form.
      setModel("");
      setTemperature(1);
      setReasoningEffort("medium");
      setMaxTokens(REASONING_FLOOR);
      setApiKey("");
    }
  }

  // Is the currently-selected provider the one stored on the server?
  const isStoredProvider = data?.provider === provider;
  const keyStored = isStoredProvider && data?.api_key_prefix != null;
  const hasKey = keyStored || apiKey.trim().length > 0;

  // Live model list — fetched for the *currently selected* provider. OpenAI can
  // list against the platform key pre-BYOK; Anthropic needs a key (D-03/D-08).
  const modelsEnabled = provider === "openai" || hasKey;
  const { data: modelList, isFetching: modelsLoading } = useQuery({
    queryKey: ["llm-models", provider],
    queryFn: () =>
      api<LlmModelList>("/api/v1/workspace/llm-settings/models", {
        query: { provider },
      }),
    enabled: modelsEnabled,
    retry: false,
  });

  const testConn = useMutation({
    mutationFn: () =>
      api<LlmTestConnection>("/api/v1/workspace/llm-settings/test-connection", {
        method: "POST",
        body: { provider, ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}) },
      }),
    onSuccess: (r) => {
      if (r.status === "valid") {
        setTestedOk(true);
        toast.success("Connection verified");
      } else {
        setTestedOk(false);
        toast.error(r.detail ? `Connection failed: ${r.detail}` : "Connection failed");
      }
      qc.invalidateQueries({ queryKey: ["llm-settings"] });
    },
    onError: (e: unknown) => {
      setTestedOk(false);
      toast.error(e instanceof ApiError ? e.message : "Test failed");
    },
  });

  const save = useMutation({
    mutationFn: (payload: Partial<LlmSettings> & { api_key?: string }) =>
      api<LlmSettings>("/api/v1/workspace/llm-settings", {
        method: "PATCH",
        body: payload,
      }),
    onSuccess: () => {
      track("settings_changed", { tab: "ai-llm" });
      toast.success("LLM settings saved");
      setApiKey("");
      qc.invalidateQueries({ queryKey: ["llm-settings"] });
      qc.invalidateQueries({ queryKey: ["llm-models"] });
    },
    onError: (e: unknown) =>
      toast.error(e instanceof ApiError ? e.message : "Could not save"),
  });

  function saveConfig() {
    // D-10: warn (do not block) if a reasoning model is below the floor. The
    // backend hard-clamps regardless, but the user should see it.
    const belowFloor = hasReasoningFloor(provider, model || null) && maxTokens < REASONING_FLOOR;
    if (belowFloor) {
      toast.warning(
        `Token budget is below the recommended minimum (${REASONING_FLOOR}) for reasoning models — the server will raise it to ${REASONING_FLOOR}.`,
      );
    }
    const payload: Partial<LlmSettings> & { api_key?: string } = {
      provider,
      model: model || null,
      max_tokens: maxTokens,
    };
    if (supportsTemperature(provider, model || null)) payload.temperature = temperature;
    if (supportsReasoningEffort(provider, model || null)) payload.reasoning_effort = reasoningEffort;
    if (apiKey.trim()) payload.api_key = apiKey.trim();
    save.mutate(payload);
  }

  if (isLoading) return <Skeleton />;
  if (error) return <ErrorState error={error} />;

  const tempMax = provider === "anthropic" ? 1 : 2; // 0-1 Claude, 0-2 OpenAI (D-10).
  const showTemperature = supportsTemperature(provider, model || null);
  const showReasoningEffort = supportsReasoningEffort(provider, model || null);
  const reasoningFloor = hasReasoningFloor(provider, model || null);
  const belowFloorWarning = reasoningFloor && maxTokens < REASONING_FLOOR;
  const canSave = testedOk && hasKey && !save.isPending;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        title="Provider and key"
        sub="The AI auto-responder and warmup use the selected provider and model."
      >
        <div className="field" style={{ marginBottom: 16 }}>
          <label className="field__label" htmlFor="llm-provider">Provider</label>
          <select
            id="llm-provider"
            className="input"
            value={provider}
            onChange={(e) => onProviderChange(e.target.value as LlmProvider)}
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic (Claude)</option>
          </select>
        </div>

        <div className="field" style={{ marginBottom: 8 }}>
          <label className="field__label" htmlFor="llm-key">API key</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              id="llm-key"
              className="input"
              type="password"
              autoComplete="off"
              placeholder={
                keyStored
                  ? `Saved: ${data?.api_key_prefix}…`
                  : "Paste your provider key"
              }
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                setTestedOk(false);
              }}
            />
          </div>
          <div className="field__hint" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <KeyStatusBadge
              status={isStoredProvider ? data?.api_key_status ?? "unset" : "unset"}
            />
            {keyStored && (
              <span className="mono muted">{data?.api_key_prefix}…</span>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
          <button
            className="btn btn--ghost"
            disabled={testConn.isPending || !hasKey}
            onClick={() => testConn.mutate()}
          >
            {testConn.isPending ? (
              <>
                <Loader2 size={13} className="animate-spin" /> Testing…
              </>
            ) : (
              "Test connection"
            )}
          </button>
          {!hasKey && (
            <span className="field__hint">Enter a key to test.</span>
          )}
          {hasKey && testedOk && (
            <span className="field__hint" style={{ color: "var(--success, #1a7f37)" }}>
              Connection verified.
            </span>
          )}
        </div>
      </Card>

      <Card title="Model" sub="Live list filtered to chat models with tool support.">
        {!hasKey ? (
          // D-03 gate — mirror the backend KEY_REQUIRED so the user never hits a raw 400.
          <div className="field__hint" style={{ color: "var(--warning, #a86200)" }}>
            {errorMessageFromEnvelope("KEY_REQUIRED", {})}
          </div>
        ) : (
          <>
            <div className="field" style={{ marginBottom: 12 }}>
              <label className="field__label" htmlFor="llm-model">Model</label>
              <select
                id="llm-model"
                className="input"
                value={model}
                onChange={(e) => {
                  setModel(e.target.value);
                  setTestedOk(false);
                }}
                disabled={!hasKey}
              >
                <option value="">
                  {modelsLoading ? "Loading…" : "Platform default"}
                </option>
                {(modelList?.models ?? []).map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
                {/* keep the stored model selectable even if the live list omits it */}
                {model && !(modelList?.models ?? []).includes(model) && (
                  <option value={model}>{model}</option>
                )}
              </select>
              {modelList?.note && (
                <div className="field__hint">
                  {modelList.note} — you can enter a model identifier manually below.
                </div>
              )}
            </div>
            {modelList?.note && (
              <div className="field" style={{ marginBottom: 12 }}>
                <label className="field__label" htmlFor="llm-model-manual">Model (manual)</label>
                <input
                  id="llm-model-manual"
                  className="input"
                  placeholder="e.g. claude-3-5-sonnet-latest"
                  value={model}
                  onChange={(e) => {
                    setModel(e.target.value);
                    setTestedOk(false);
                  }}
                />
              </div>
            )}
          </>
        )}
      </Card>

      {hasKey && (
        <Card title="Model settings" sub="Only options supported by the selected model are shown.">
          {showTemperature && (
            <div className="field" style={{ marginBottom: 16 }}>
              <label className="field__label" htmlFor="llm-temp">
                Temperature: {temperature.toFixed(2)}
              </label>
              <input
                id="llm-temp"
                type="range"
                min={0}
                max={tempMax}
                step={0.05}
                value={temperature}
                onChange={(e) => setTemperature(Number(e.target.value))}
              />
              <div className="field__hint">
                Recommended range 0–{tempMax} for {provider === "anthropic" ? "Claude" : "OpenAI"}.
              </div>
            </div>
          )}

          {showReasoningEffort && (
            <div className="field" style={{ marginBottom: 16 }}>
              <label className="field__label" htmlFor="llm-effort">Reasoning effort</label>
              <select
                id="llm-effort"
                className="input"
                value={reasoningEffort}
                onChange={(e) => setReasoningEffort(e.target.value)}
              >
                {EFFORT_LEVELS.map((lvl) => (
                  <option key={lvl} value={lvl}>{lvl}</option>
                ))}
              </select>
            </div>
          )}

          <div className="field" style={{ marginBottom: 16 }}>
            <label className="field__label" htmlFor="llm-maxtok">Response budget (max tokens)</label>
            <input
              id="llm-maxtok"
              className="input"
              type="number"
              min={reasoningFloor ? REASONING_FLOOR : 1}
              max={MAX_TOKENS_CEILING}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
            />
            <div className="field__hint">
              {reasoningFloor
                ? `Recommended range ${REASONING_FLOOR}–${MAX_TOKENS_CEILING} for reasoning models.`
                : `Recommended range up to ${MAX_TOKENS_CEILING}.`}
            </div>
            {belowFloorWarning && (
              <div className="field__hint" style={{ color: "var(--warning, #a86200)" }}>
                Below the recommended minimum of {REASONING_FLOOR} — a reasoning model may return
                an empty response. The server will raise the value to {REASONING_FLOOR}.
              </div>
            )}
          </div>

          <button
            className="btn btn--primary"
            disabled={!canSave}
            onClick={saveConfig}
            title={!testedOk ? "Test the connection first" : undefined}
          >
            {save.isPending ? "Saving…" : "Save settings"}
          </button>
          {!testedOk && (
            <div className="field__hint" style={{ marginTop: 8 }}>
              Test the connection successfully before saving.
            </div>
          )}
        </Card>
      )}
    </div>
  );
}


function KeyStatusBadge({ status }: { status: "unset" | "valid" | "invalid" }) {
  const map = {
    unset: { label: "не задан", color: "var(--muted, #888)" },
    valid: { label: "валиден", color: "var(--success, #1a7f37)" },
    invalid: { label: "невалиден", color: "var(--danger, #cf222e)" },
  } as const;
  const s = map[status];
  return (
    <span
      style={{
        fontSize: 12,
        fontWeight: 600,
        color: s.color,
        border: `1px solid ${s.color}`,
        borderRadius: 6,
        padding: "1px 8px",
      }}
    >
      {s.label}
    </span>
  );
}

function ApiKeysTab() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["workspace", "api-keys"],
    queryFn: () => api<ApiKey[]>("/api/v1/workspace/api-keys"),
    retry: false,
  });
  const [newKeyName, setNewKeyName] = useState("");
  const [revealed, setRevealed] = useState<{ key: string; name: string } | null>(null);

  const create = useMutation({
    mutationFn: (name: string) =>
      api<{ id: string; name: string; key: string }>("/api/v1/workspace/api-keys", {
        method: "POST",
        body: { name },
      }),
    onSuccess: (k) => {
      track("workspace_api_key_created", { key_id: k.id, name: k.name });
      setRevealed({ key: k.key, name: k.name });
      setNewKeyName("");
      qc.invalidateQueries({ queryKey: ["workspace", "api-keys"] });
    },
    onError: (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Could not create key"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/v1/workspace/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Key revoked");
      qc.invalidateQueries({ queryKey: ["workspace", "api-keys"] });
    },
  });

  if (isLoading) return <Skeleton />;
  if (error) return <ErrorState error={error} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title="Create API key" sub="Used for outbound integrations and the public REST API.">
        <div style={{ display: "flex", gap: 8 }}>
          <input
            className="input"
            placeholder="Key name (e.g. Zapier production)"
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
          />
          <button
            className="btn btn--primary"
            disabled={!newKeyName || create.isPending}
            onClick={() => create.mutate(newKeyName)}
          >
            <Plus size={14} /> Create
          </button>
        </div>
        {revealed && (
          <div
            style={{
              marginTop: 16,
              padding: 12,
              borderRadius: 8,
              background: "var(--warning-soft)",
              color: "#a86200",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
              Copy this now — it won't be shown again.
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <code className="mono" style={{ flex: 1, fontSize: 12, wordBreak: "break-all" }}>{revealed.key}</code>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  void navigator.clipboard.writeText(revealed.key);
                  toast.success("Copied");
                }}
              >
                <Copy size={12} /> Copy
              </button>
            </div>
          </div>
        )}
      </Card>

      <Card title="Active keys" sub={`${data?.length ?? 0} key${data?.length === 1 ? "" : "s"}`}>
        {data && data.length > 0 ? (
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Last used</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td className="mono">{k.prefix}…</td>
                  <td className="muted">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "Never"}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => remove.mutate(k.id)}
                      disabled={remove.isPending}
                      aria-label={`Revoke ${k.name}`}
                    >
                      <Trash2 size={12} /> Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState
            title="No API keys yet"
            body="Create one above to start integrating aimly with your stack."
          />
        )}
      </Card>
    </div>
  );
}

function MembersTab() {
  return (
    <Card title="Members" sub="Multi-user invitations land in v2.">
      <EmptyState
        title="It's just you for now"
        body="Inviting teammates is on the roadmap. We'll email you when it ships."
      />
    </Card>
  );
}

function ProfileTab() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  useEffect(() => {
    void supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? ""));
  }, []);
  async function signOut() {
    await supabase.auth.signOut();
    toast.success("Signed out");
    navigate({ to: "/login" });
  }
  return (
    <Card title="Profile">
      <div className="field" style={{ marginBottom: 16 }}>
        <label className="field__label">Email</label>
        <input className="input" value={email} disabled />
      </div>
      <button className="btn btn--ghost" onClick={signOut}>
        <LogOut size={14} /> Sign out
      </button>
    </Card>
  );
}

function AppearanceTab() {
  return (
    <Card title="Appearance" sub="Theme">
      <div style={{ display: "flex", gap: 12 }}>
        <label className="card" style={{ padding: 12, display: "flex", gap: 10, alignItems: "center", cursor: "pointer" }}>
          <input type="radio" name="theme" defaultChecked />
          <div>
            <div style={{ fontWeight: 500 }}>Light</div>
            <div className="text-xs muted">Default</div>
          </div>
        </label>
        <label className="card" style={{ padding: 12, display: "flex", gap: 10, alignItems: "center", opacity: 0.5 }}>
          <input type="radio" name="theme" disabled />
          <div>
            <div style={{ fontWeight: 500 }}>Dark</div>
            <div className="text-xs muted">v2</div>
          </div>
        </label>
      </div>
    </Card>
  );
}

function Skeleton() {
  return (
    <div className="card" style={{ maxWidth: 720, padding: 24 }}>
      <Loader2 size={20} className="animate-spin" style={{ color: "var(--tg-blue)" }} />
    </div>
  );
}

function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? error.message : "Could not load.";
  return (
    <div className="card" style={{ maxWidth: 720, padding: 24 }}>
      <div style={{ color: "var(--danger)" }}>{msg}</div>
    </div>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ textAlign: "center", padding: "32px 16px" }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{title}</div>
      <div className="muted" style={{ fontSize: 13 }}>{body}</div>
    </div>
  );
}
