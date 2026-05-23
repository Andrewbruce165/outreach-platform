import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, Plus, Trash2, LogOut, Loader2 } from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { track } from "@/lib/telemetry";

export const Route = createFileRoute("/_authenticated/settings")({
  component: SettingsPage,
});

type Tab = "workspace" | "api-keys" | "members" | "profile" | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "workspace", label: "Workspace" },
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
