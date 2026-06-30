import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Library, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";

type KnowledgeBase = components["schemas"]["KnowledgeBaseResponse"];
type KnowledgeBaseCreate = components["schemas"]["KnowledgeBaseCreate"];

export const Route = createFileRoute("/_authenticated/knowledge-bases/")({
  component: KnowledgeBasesPage,
});

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

/** Human-readable byte size (mirrors the STORAGE metric on the detail view). */
export function humanBytes(bytes: number | null | undefined): string {
  const n = bytes ?? 0;
  if (n === 0) return "0 Bytes";
  const units = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(n) / Math.log(1024));
  const v = n / Math.pow(1024, i);
  return `${v % 1 === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

/**
 * D-09 aggregate status → list-page Status pill. Derived router-side
 * (failed > processing > indexed > empty).
 */
const KB_STATUS_PILL: Record<
  string,
  { label: string; pill: string; dot: string }
> = {
  indexed:    { label: "Ready",    pill: "pill--green",  dot: "var(--success)" },
  processing: { label: "Indexing", pill: "pill--orange", dot: "var(--warning)" },
  failed:     { label: "Failed",   pill: "pill--red",    dot: "var(--danger)" },
  empty:      { label: "Empty",    pill: "pill--ghost",  dot: "var(--text-faint)" },
};

function KbStatusPill({ status }: { status: string }) {
  const s = KB_STATUS_PILL[status] ?? KB_STATUS_PILL.empty;
  return (
    <span className={`pill ${s.pill}`}>
      <span className="pill__dot" style={{ background: s.dot }} />
      {s.label}
    </span>
  );
}

function KnowledgeBasesPage() {
  const [creating, setCreating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api<KnowledgeBase[]>("/api/v1/knowledge-bases"),
  });

  const kbs = listQ.data ?? [];

  return (
    <>
      <Topbar
        title="Knowledge bases"
        right={
          <button
            className="btn btn--primary btn--sm"
            onClick={() => {
              setActionError(null);
              setCreating(true);
            }}
          >
            <Plus size={14} /> New knowledge base
          </button>
        }
      />

      <div className="scroll" style={{ flex: 1, padding: 24 }}>
        {actionError && (
          <div
            className="card"
            style={{
              padding: 12,
              marginBottom: 14,
              color: "var(--danger)",
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

        {listQ.isLoading && (
          <div className="muted" style={{ padding: 24 }}>
            Loading knowledge bases…
          </div>
        )}
        {listQ.error && (
          <div className="card" style={{ padding: 16, color: "var(--danger)" }}>
            {errMsg(listQ.error)}
          </div>
        )}

        {listQ.data && kbs.length === 0 && (
          <EmptyState onCreate={() => setCreating(true)} />
        )}

        {kbs.length > 0 && (
          <div className="card">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Documents</th>
                  <th style={{ textAlign: "right" }}>Storage</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {kbs.map((kb) => (
                  <tr key={kb.id}>
                    <td>
                      <Link
                        to="/knowledge-bases/$id"
                        params={{ id: kb.id }}
                        style={{
                          fontWeight: 600,
                          color: "var(--tg-blue)",
                        }}
                      >
                        {kb.name}
                      </Link>
                      {kb.description && (
                        <div
                          className="muted text-xs"
                          style={{
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            maxWidth: 320,
                          }}
                        >
                          {kb.description}
                        </div>
                      )}
                    </td>
                    <td>
                      <span className="pill pill--blue">
                        <span
                          className="pill__dot"
                          style={{ background: "var(--tg-blue)" }}
                        />
                        Files
                      </span>
                    </td>
                    <td>
                      <KbStatusPill status={kb.status} />
                    </td>
                    <td className="num" style={{ textAlign: "right" }}>
                      {kb.documents}
                    </td>
                    <td
                      className="num"
                      style={{ textAlign: "right", color: "var(--text-soft)" }}
                    >
                      {humanBytes(kb.storage_bytes)}
                    </td>
                    <td className="muted text-xs">
                      {new Date(kb.updated_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <CreateKbModal
          onClose={() => setCreating(false)}
          onError={(m) => setActionError(m)}
        />
      )}
    </>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div
      style={{
        textAlign: "center",
        padding: "64px 24px",
        maxWidth: 480,
        margin: "0 auto",
      }}
    >
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 14,
          background: "var(--tg-blue-soft)",
          color: "var(--tg-blue)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto 14px",
        }}
      >
        <Library size={26} />
      </div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
        No knowledge bases yet
      </h3>
      <p className="muted" style={{ fontSize: 13, marginBottom: 16, lineHeight: 1.5 }}>
        Knowledge bases give your agents searchable reference material. Create one
        and upload documents — agents you attach it to will pull from it on demand.
      </p>
      <button className="btn btn--primary" onClick={onCreate}>
        Create your first knowledge base
      </button>
    </div>
  );
}

function CreateKbModal({
  onClose,
  onError,
}: {
  onClose: () => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const createMut = useMutation({
    mutationFn: (body: KnowledgeBaseCreate) =>
      api<KnowledgeBase>("/api/v1/knowledge-bases", {
        method: "POST",
        body: body as unknown as Record<string, unknown>,
      }),
    onSuccess: (kb) => {
      toast.success("Knowledge base created");
      void qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      onClose();
      void navigate({ to: "/knowledge-bases/$id", params: { id: kb.id } });
    },
    onError: (e) => onError(errMsg(e)),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    createMut.mutate({
      name: trimmed,
      description: description.trim() || null,
    });
  };

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal__head">
          <h3>New knowledge base</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <form onSubmit={submit} style={{ display: "contents" }}>
          <div className="modal__body" style={{ display: "grid", gap: 14 }}>
            <div className="field">
              <label className="field__label">Name *</label>
              <input
                className="input"
                placeholder="e.g. Product FAQ"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="field">
              <label className="field__label">Description</label>
              <textarea
                className="textarea"
                rows={2}
                placeholder="What's in this knowledge base? (optional)"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
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
            <button
              type="submit"
              className="btn btn--primary"
              disabled={createMut.isPending || !name.trim()}
            >
              {createMut.isPending ? "Creating…" : "Create knowledge base"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
