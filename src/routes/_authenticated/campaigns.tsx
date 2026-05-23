import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type CampaignList = components["schemas"]["CampaignListResponse"];
type Agent = components["schemas"]["AgentResponse"];
type Folder = components["schemas"]["FolderResponse"];

export const Route = createFileRoute("/_authenticated/campaigns")({
  component: CampaignsPage,
});

const FILTERS = [
  { id: "all", label: "All" },
  { id: "draft", label: "Draft" },
  { id: "running", label: "Running" },
  { id: "paused", label: "Paused" },
  { id: "finished", label: "Finished" },
] as const;

type FilterId = (typeof FILTERS)[number]["id"];

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function CampaignsPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<FilterId>("all");
  const [actionError, setActionError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<CampaignList>("/api/v1/campaigns"),
  });

  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () =>
      api<{ agents: Agent[]; total: number }>("/api/v1/agents"),
    staleTime: 60_000,
  });

  const foldersQ = useQuery({
    queryKey: ["folders"],
    queryFn: () => api<Folder[]>("/api/v1/folders"),
    staleTime: 60_000,
  });

  const agentName = useMemo(() => {
    const m = new Map<string, string>();
    agentsQ.data?.agents.forEach((a) => m.set(a.id, a.name));
    return m;
  }, [agentsQ.data]);

  const folderName = useMemo(() => {
    const m = new Map<string, string>();
    foldersQ.data?.forEach((f) => m.set(f.id, f.name));
    return m;
  }, [foldersQ.data]);

  const items = useMemo(() => {
    const all = listQ.data?.items ?? [];
    if (filter === "all") return all;
    return all.filter((c) => c.status === filter);
  }, [listQ.data, filter]);

  const lifecycleMut = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "start" | "pause" | "resume" | "stop" | "finish" }) =>
      api<Campaign>(`/api/v1/campaigns/${id}/${action}`, { method: "POST" }),
    onSuccess: (_d, v) => {
      const eventMap = {
        start: "campaign_launched",
        pause: "campaign_paused",
        resume: "campaign_resumed",
      } as const;
      const e = eventMap[v.action as keyof typeof eventMap];
      if (e) track(e, { campaign_id: v.id });
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  const duplicateMut = useMutation({
    mutationFn: (id: string) =>
      api<Campaign>(`/api/v1/campaigns/${id}/duplicate`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaigns"] }),
    onError: (e) => setActionError(errMsg(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/campaigns/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaigns"] }),
    onError: (e) => setActionError(errMsg(e)),
  });

  return (
    <>
      <Topbar
        title="Campaigns"
        right={
          <Link to="/campaigns/new">

            <button className="btn btn--primary btn--sm">+ New campaign</button>
          </Link>
        }
      />
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        <div style={{ display: "flex", gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
          {FILTERS.map((f) => {
            const count =
              f.id === "all"
                ? listQ.data?.items.length ?? 0
                : listQ.data?.items.filter((c) => c.status === f.id).length ?? 0;
            const active = filter === f.id;
            return (
              <button
                key={f.id}
                className={`btn btn--sm ${active ? "btn--primary" : "btn--ghost"}`}
                onClick={() => setFilter(f.id)}
              >
                {f.label} <span style={{ opacity: 0.7, marginLeft: 4 }}>{count}</span>
              </button>
            );
          })}
        </div>

        {actionError && (
          <div
            className="card"
            style={{
              padding: 12,
              marginBottom: 14,
              color: "var(--danger, #c0392b)",
              fontSize: 13,
            }}
            role="alert"
          >
            {actionError}{" "}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setActionError(null)}
              style={{ marginLeft: 8 }}
            >
              Dismiss
            </button>
          </div>
        )}

        {listQ.isLoading && <div className="muted">Loading campaigns…</div>}
        {listQ.error && (
          <div className="card" style={{ padding: 16, color: "var(--danger, #c0392b)" }}>
            {errMsg(listQ.error)}
          </div>
        )}

        {listQ.data && items.length === 0 && (
          <EmptyState filter={filter} />
        )}

        {items.length > 0 && (
          <div className="card" style={{ overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                  <th style={th}>Campaign</th>
                  <th style={th}>Status</th>
                  <th style={th}>Agent</th>
                  <th style={th}>Folder</th>
                  <th style={th}>Senders</th>
                  <th style={th}>Updated</th>
                  <th style={{ ...th, width: 1 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={td}>
                      <Link
                        to="/campaigns"
                        search={{ id: c.id } as never}
                        style={{ fontWeight: 600, color: "var(--tg-blue)" }}
                      >
                        {c.name}
                      </Link>
                      {c.is_exhausted && (
                        <span
                          title="No more contacts to message"
                          style={{
                            marginLeft: 8,
                            fontSize: 11,
                            color: "var(--text-soft)",
                          }}
                        >
                          (exhausted)
                        </span>
                      )}
                    </td>
                    <td style={td}>
                      <StatusPill status={c.status} />
                    </td>
                    <td style={{ ...td, color: "var(--text-soft)" }}>
                      {agentName.get(c.agent_id) ?? "—"}
                    </td>
                    <td style={{ ...td, color: "var(--text-soft)" }}>
                      {folderName.get(c.folder_id) ?? "—"}
                    </td>
                    <td style={td}>{c.attached_senders?.length ?? 0}</td>
                    <td style={{ ...td, color: "var(--text-soft)" }}>
                      {new Date(c.updated_at).toLocaleDateString()}
                    </td>
                    <td style={td}>
                      <RowActions
                        campaign={c}
                        busy={lifecycleMut.isPending || duplicateMut.isPending || deleteMut.isPending}
                        onLifecycle={(action) => {
                          setActionError(null);
                          lifecycleMut.mutate({ id: c.id, action });
                        }}
                        onDuplicate={() => {
                          setActionError(null);
                          duplicateMut.mutate(c.id);
                        }}
                        onDelete={() => {
                          if (confirm(`Delete campaign "${c.name}"?`)) {
                            setActionError(null);
                            deleteMut.mutate(c.id);
                          }
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
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

const STATUS_STYLES: Record<string, { bg: string; fg: string; label: string }> = {
  draft: { bg: "#eef0f3", fg: "#5b6470", label: "Draft" },
  running: { bg: "#e6f4ea", fg: "#1e7a3a", label: "Running" },
  paused: { bg: "#fff4d6", fg: "#8a6a00", label: "Paused" },
  finished: { bg: "#eef0f3", fg: "#5b6470", label: "Finished" },
  stopped: { bg: "#fde7e7", fg: "#a02525", label: "Stopped" },
};

function StatusPill({ status }: { status: string }) {
  const s = STATUS_STYLES[status] ?? { bg: "#eef0f3", fg: "#5b6470", label: status };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        background: s.bg,
        color: s.fg,
        fontSize: 11,
        fontWeight: 500,
        textTransform: "capitalize",
      }}
    >
      {s.label}
    </span>
  );
}

function RowActions({
  campaign,
  busy,
  onLifecycle,
  onDuplicate,
  onDelete,
}: {
  campaign: Campaign;
  busy: boolean;
  onLifecycle: (action: "start" | "pause" | "resume" | "stop") => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const s = campaign.status;
  const can = {
    start: s === "draft",
    pause: s === "running",
    resume: s === "paused",
    stop: s === "running" || s === "paused",
    delete: s === "draft" || s === "finished" || s === "stopped",
  };
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
      {can.start && (
        <button
          className="btn btn--primary btn--sm"
          onClick={() => onLifecycle("start")}
          disabled={busy}
        >
          Start
        </button>
      )}
      {can.pause && (
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => onLifecycle("pause")}
          disabled={busy}
        >
          Pause
        </button>
      )}
      {can.resume && (
        <button
          className="btn btn--primary btn--sm"
          onClick={() => onLifecycle("resume")}
          disabled={busy}
        >
          Resume
        </button>
      )}
      {can.stop && (
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => onLifecycle("stop")}
          disabled={busy}
        >
          Stop
        </button>
      )}
      <button className="btn btn--ghost btn--sm" onClick={onDuplicate} disabled={busy}>
        Duplicate
      </button>
      {can.delete && (
        <button className="btn btn--ghost btn--sm" onClick={onDelete} disabled={busy}>
          Delete
        </button>
      )}
    </div>
  );
}

function EmptyState({ filter }: { filter: FilterId }) {
  const isFiltered = filter !== "all";
  return (
    <div
      style={{
        textAlign: "center",
        padding: "64px 24px",
        maxWidth: 460,
        margin: "0 auto",
      }}
    >
      <div style={{ fontSize: 40, marginBottom: 12 }}>📣</div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
        {isFiltered ? `No ${filter} campaigns` : "No campaigns yet"}
      </h3>
      <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>
        {isFiltered
          ? "Try a different filter, or create a new campaign."
          : "A campaign sends your agent's message from your accounts to a folder of contacts."}
      </p>
      <Link to="/campaigns/new">
        <button className="btn btn--primary">+ New campaign</button>
      </Link>
    </div>
  );
}
