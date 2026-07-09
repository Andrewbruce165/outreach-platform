import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import {
  ArrowLeft,
  Clock,
  Edit3,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  Settings,
  Trash2,
  Upload,
  Users,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";
import { humanBytes } from "./knowledge-bases.index";

type KnowledgeBase = components["schemas"]["KnowledgeBaseResponse"];
type KnowledgeBaseUpdate = components["schemas"]["KnowledgeBaseUpdate"];
type KbDocument = components["schemas"]["KbDocumentResponse"];
type KbSearchResponse = components["schemas"]["KbSearchResponse"];
type AgentForKb = components["schemas"]["AgentForKbResponse"];

export const Route = createFileRoute("/_authenticated/knowledge-bases/$id")({
  component: KnowledgeBaseDetailPage,
});

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

const KB_STATUS_PILL: Record<
  string,
  { label: string; pill: string; dot: string }
> = {
  indexed:    { label: "Ready",    pill: "pill--green",  dot: "var(--success)" },
  processing: { label: "Indexing", pill: "pill--orange", dot: "var(--warning)" },
  failed:     { label: "Failed",   pill: "pill--red",    dot: "var(--danger)" },
  empty:      { label: "Empty",    pill: "pill--ghost",  dot: "var(--text-faint)" },
};

const DOC_STATUS_PILL: Record<
  string,
  { label: string; pill: string; dot: string }
> = {
  indexed:    { label: "Indexed",    pill: "pill--green",  dot: "var(--success)" },
  processing: { label: "Processing", pill: "pill--orange", dot: "var(--warning)" },
  pending:    { label: "Processing", pill: "pill--orange", dot: "var(--warning)" },
  failed:     { label: "Failed",     pill: "pill--red",    dot: "var(--danger)" },
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

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type TabId = "documents" | "search" | "agents" | "settings";

function KnowledgeBaseDetailPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabId>("documents");
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const kbQ = useQuery({
    queryKey: ["knowledge-base", id],
    queryFn: () => api<KnowledgeBase>(`/api/v1/knowledge-bases/${id}`),
  });
  const kb = kbQ.data;

  const reindexMut = useMutation({
    mutationFn: () =>
      Promise.all(
        (docsRef.current ?? [])
          .filter((d) => d.status === "failed")
          .map((d) =>
            api<KbDocument>(
              `/api/v1/knowledge-bases/${id}/documents/${d.id}/reindex`,
              { method: "POST" },
            ),
          ),
      ),
    onSuccess: () => {
      toast.success("Re-indexing started");
      void qc.invalidateQueries({ queryKey: ["kb-documents", id] });
      void qc.invalidateQueries({ queryKey: ["knowledge-base", id] });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  const deleteKbMut = useMutation({
    mutationFn: () =>
      api(`/api/v1/knowledge-bases/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Knowledge base deleted");
      void qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      void navigate({ to: "/knowledge-bases" });
    },
    onError: (e) => setActionError(errMsg(e)),
  });

  // Latest document snapshot used by the header Re-index action (failed docs).
  const docsRef = useRef<KbDocument[] | null>(null);

  const docCount = kb?.documents ?? 0;

  return (
    <>
      <Topbar
        title={kb?.name ?? "Knowledge base"}
        crumbs={[
          { label: "Knowledge bases", href: "/knowledge-bases" },
          { label: kb?.name ?? "…" },
        ]}
      />

      <div className="scroll" style={{ flex: 1, padding: 24 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 16,
          }}
        >
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => navigate({ to: "/knowledge-bases" })}
          >
            <ArrowLeft size={14} /> Back
          </button>
        </div>

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

        {kbQ.isLoading && (
          <div className="muted" style={{ padding: 24 }}>
            Loading knowledge base…
          </div>
        )}
        {kbQ.error && (
          <div className="card" style={{ padding: 16, color: "var(--danger)" }}>
            {errMsg(kbQ.error)}{" "}
            <Link to="/knowledge-bases" style={{ marginLeft: 8 }}>
              Back to list
            </Link>
          </div>
        )}

        {kb && (
          <>
            {/* D-09 HEADER */}
            <section className="card" style={{ padding: 20, marginBottom: 16 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 12,
                  marginBottom: 18,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    gap: 12,
                    flex: 1,
                    fontSize: 13,
                  }}
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span className="muted">Type:</span>
                    <span className="pill pill--blue">
                      <span className="pill__dot" style={{ background: "var(--tg-blue)" }} />
                      Files
                    </span>
                  </span>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span className="muted">Status:</span>
                    <KbStatusPill status={kb.status} />
                  </span>
                  <span
                    className="muted"
                    style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
                  >
                    <Clock size={13} /> Updated {fmtDateTime(kb.updated_at)}
                  </span>
                </div>

                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <button
                    className="tb__icon-btn"
                    aria-label="Re-index knowledge base"
                    title="Re-index failed documents"
                    disabled={reindexMut.isPending}
                    onClick={() => {
                      setActionError(null);
                      reindexMut.mutate();
                    }}
                  >
                    <RefreshCw size={16} />
                  </button>
                  <button
                    className="tb__icon-btn"
                    aria-label="Edit knowledge base"
                    title="Edit"
                    onClick={() => setTab("settings")}
                  >
                    <Edit3 size={16} />
                  </button>
                  <button
                    className="tb__icon-btn"
                    aria-label="Knowledge base settings"
                    title="Settings"
                    onClick={() => setTab("settings")}
                  >
                    <Settings size={16} />
                  </button>
                  <button
                    className="tb__icon-btn"
                    aria-label="Delete knowledge base"
                    title="Delete"
                    style={{ color: "var(--danger)" }}
                    onClick={() => setConfirmingDelete(true)}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* D-09 5-METRIC STAT ROW */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(5, 1fr)",
                  gap: 12,
                }}
              >
                <StatMetric label="Documents" value={kb.documents} dot="var(--text-faint)" />
                <StatMetric label="Indexed" value={kb.indexed} dot="var(--success)" />
                <StatMetric label="Processing" value={kb.processing} dot="var(--warning)" />
                <StatMetric label="Failed" value={kb.failed} dot="var(--danger)" />
                <StatMetric
                  label="Storage"
                  value={humanBytes(kb.storage_bytes)}
                  dot="var(--tg-blue)"
                />
              </div>
            </section>

            {/* TAB BAR (D-11) */}
            <div className="tabs" style={{ padding: 0, marginBottom: 16 }}>
              <button
                className={`tab ${tab === "documents" ? "is-active" : ""}`}
                onClick={() => setTab("documents")}
              >
                <FileText size={15} /> Documents <span className="count">{docCount}</span>
              </button>
              <button
                className={`tab ${tab === "search" ? "is-active" : ""}`}
                onClick={() => setTab("search")}
              >
                <Search size={15} /> Search
              </button>
              <button
                className={`tab ${tab === "agents" ? "is-active" : ""}`}
                onClick={() => setTab("agents")}
              >
                <Users size={15} /> Agents
              </button>
              <button
                className={`tab ${tab === "settings" ? "is-active" : ""}`}
                onClick={() => setTab("settings")}
              >
                <Settings size={15} /> Settings
              </button>
            </div>

            {tab === "documents" && (
              <DocumentsTab
                kbId={id}
                onError={setActionError}
                onDocsSnapshot={(docs) => {
                  docsRef.current = docs;
                }}
              />
            )}
            {tab === "search" && (
              <SearchTab kbId={id} indexedCount={kb.indexed} />
            )}
            {tab === "agents" && <AgentsTab kbId={id} />}
            {tab === "settings" && (
              <SettingsTab
                kb={kb}
                onError={setActionError}
                onDelete={() => setConfirmingDelete(true)}
              />
            )}
          </>
        )}
      </div>

      {confirmingDelete && kb && (
        <ConfirmDialog
          title={`Удалить базу знаний «${kb.name}»?`}
          body="Все документы и индекс будут удалены безвозвратно."
          confirmLabel="Удалить"
          cancelLabel="Отмена"
          busy={deleteKbMut.isPending}
          onCancel={() => setConfirmingDelete(false)}
          onConfirm={() => {
            setActionError(null);
            deleteKbMut.mutate();
          }}
        />
      )}
    </>
  );
}

function StatMetric({
  label,
  value,
  dot,
}: {
  label: string;
  value: number | string;
  dot: string;
}) {
  return (
    <div style={{ background: "var(--bg-soft)", borderRadius: 10, padding: 14 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 6,
        }}
      >
        <span className="pill__dot" style={{ background: dot }} />
        <span
          className="muted"
          style={{
            fontSize: 11.5,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          {label}
        </span>
      </div>
      <div className="num metric__value">
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </div>
  );
}

/* ----------------------------- Documents tab ----------------------------- */
function DocumentsTab({
  kbId,
  onError,
  onDocsSnapshot,
}: {
  kbId: string;
  onError: (m: string) => void;
  onDocsSnapshot: (docs: KbDocument[]) => void;
}) {
  const qc = useQueryClient();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [deleting, setDeleting] = useState<KbDocument | null>(null);

  const docsQ = useQuery({
    queryKey: ["kb-documents", kbId],
    queryFn: () =>
      api<KbDocument[]>(`/api/v1/knowledge-bases/${kbId}/documents`),
    // D-02 async reflection: poll while any doc is still processing/pending.
    refetchInterval: (q) => {
      const docs = q.state.data as KbDocument[] | undefined;
      const anyProcessing = docs?.some(
        (d) => d.status === "processing" || d.status === "pending",
      );
      return anyProcessing ? 2500 : false;
    },
  });

  const docs = docsQ.data ?? [];
  if (docsQ.data) onDocsSnapshot(docsQ.data);

  const reindexMut = useMutation({
    mutationFn: (docId: string) =>
      api<KbDocument>(
        `/api/v1/knowledge-bases/${kbId}/documents/${docId}/reindex`,
        { method: "POST" },
      ),
    onSuccess: () => {
      toast.success("Re-indexing started");
      void qc.invalidateQueries({ queryKey: ["kb-documents", kbId] });
      void qc.invalidateQueries({ queryKey: ["knowledge-base", kbId] });
    },
    onError: (e) => onError(errMsg(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (docId: string) =>
      api(`/api/v1/knowledge-bases/${kbId}/documents/${docId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      toast.success("Document deleted");
      void qc.invalidateQueries({ queryKey: ["kb-documents", kbId] });
      void qc.invalidateQueries({ queryKey: ["knowledge-base", kbId] });
      setDeleting(null);
    },
    onError: (e) => onError(errMsg(e)),
  });

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          marginBottom: 14,
          gap: 8,
        }}
      >
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--primary btn--sm"
          onClick={() => setUploadOpen(true)}
        >
          <Upload size={14} /> Upload files
        </button>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => setPasteOpen(true)}
        >
          Paste text
        </button>
      </div>

      {docsQ.isLoading && (
        <div className="muted" style={{ padding: 24 }}>
          Loading documents…
        </div>
      )}
      {docsQ.error && (
        <div className="card" style={{ padding: 16, color: "var(--danger)" }}>
          {errMsg(docsQ.error)}
        </div>
      )}

      {docsQ.data && docs.length === 0 && (
        <div
          style={{
            textAlign: "center",
            padding: "64px 24px",
            maxWidth: 480,
            margin: "0 auto",
          }}
        >
          <div style={{ fontSize: 40, marginBottom: 12 }}>📄</div>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
            No documents yet
          </h3>
          <p
            className="muted"
            style={{ fontSize: 13, marginBottom: 16, lineHeight: 1.5 }}
          >
            Upload files (PDF, DOCX, TXT, MD, CSV) or paste text. Documents are
            indexed in the background — status updates here automatically.
          </p>
          <button
            className="btn btn--primary"
            onClick={() => setUploadOpen(true)}
          >
            <Upload size={14} /> Upload files
          </button>
        </div>
      )}

      {docs.length > 0 && (
        <div className="card">
          <table className="tbl">
            <thead>
              <tr>
                <th>Document</th>
                <th>Source</th>
                <th>Status</th>
                <th style={{ textAlign: "right" }}>Size</th>
                <th>Uploaded</th>
                <th style={{ width: 80 }} />
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td style={{ fontWeight: 500 }}>{d.name}</td>
                  <td className="muted text-xs">
                    {d.source_kind === "text" ? (
                      "Pasted text"
                    ) : (
                      <span className="pill pill--ghost" style={{ height: 18 }}>
                        {d.source_kind.toUpperCase()}
                      </span>
                    )}
                  </td>
                  <td>
                    <DocStatusPill doc={d} />
                  </td>
                  <td className="num" style={{ textAlign: "right", color: "var(--text-soft)" }}>
                    {humanBytes(d.size_bytes)}
                  </td>
                  <td className="muted text-xs">{fmtDateTime(d.created_at)}</td>
                  <td>
                    <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                      {d.status === "failed" && (
                        <button
                          className="btn btn--ghost btn--sm"
                          title="Индексация не удалась. Нажмите «Переиндексировать»."
                          disabled={reindexMut.isPending}
                          onClick={() => reindexMut.mutate(d.id)}
                        >
                          <RefreshCw size={13} /> Re-index
                        </button>
                      )}
                      <button
                        className="tb__icon-btn"
                        aria-label={`Delete ${d.name}`}
                        title="Delete document"
                        style={{ color: "var(--danger)", width: 28, height: 28 }}
                        onClick={() => setDeleting(d)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {uploadOpen && (
        <UploadModal
          kbId={kbId}
          onClose={() => setUploadOpen(false)}
          onUploaded={() => {
            void qc.invalidateQueries({ queryKey: ["kb-documents", kbId] });
            void qc.invalidateQueries({ queryKey: ["knowledge-base", kbId] });
          }}
        />
      )}
      {pasteOpen && (
        <PasteModal
          kbId={kbId}
          onClose={() => setPasteOpen(false)}
          onPasted={() => {
            void qc.invalidateQueries({ queryKey: ["kb-documents", kbId] });
            void qc.invalidateQueries({ queryKey: ["knowledge-base", kbId] });
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={`Удалить документ «${deleting.name}» из базы?`}
          body="Его чанки будут удалены из индекса."
          confirmLabel="Удалить"
          cancelLabel="Отмена"
          busy={deleteMut.isPending}
          onCancel={() => setDeleting(null)}
          onConfirm={() => deleteMut.mutate(deleting.id)}
        />
      )}
    </>
  );
}

function DocStatusPill({ doc }: { doc: KbDocument }) {
  const s = DOC_STATUS_PILL[doc.status] ?? DOC_STATUS_PILL.pending;
  const processing = doc.status === "processing" || doc.status === "pending";
  return (
    <span
      className={`pill ${s.pill}`}
      title={doc.status === "failed" && doc.error ? doc.error : undefined}
    >
      {processing ? (
        <Loader2 size={11} className="ob__spin" />
      ) : (
        <span className="pill__dot" style={{ background: s.dot }} />
      )}
      {processing ? "Indexing…" : s.label}
    </span>
  );
}

function UploadModal({
  kbId,
  onClose,
  onUploaded,
}: {
  kbId: string;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFile(file: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      await api<KbDocument>(`/api/v1/knowledge-bases/${kbId}/documents`, {
        method: "POST",
        body: form,
      });
      toast.success(`«${file.name}» загружается`);
      onUploaded();
      onClose();
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `Не удалось обработать файл «${file.name}». Проверьте формат (PDF, DOCX, TXT, MD, CSV) и попробуйте снова.`
          : errMsg(e);
      toast.error(msg);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal__head">
          <h3>Upload files</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <div className="modal__body">
          <div className="ct__upload">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt,.md,.csv"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleFile(f);
              }}
            />
            <div
              className="ct__dropzone"
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files[0];
                if (f) void handleFile(f);
              }}
            >
              {uploading ? (
                <>
                  <Loader2 size={20} className="ob__spin" />
                  <span>Uploading…</span>
                </>
              ) : (
                <>
                  <Upload size={24} />
                  <span className="fw5">Drop files here, or click to choose</span>
                  <span className="muted text-sm">PDF, DOCX, TXT, MD, CSV</span>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PasteModal({
  kbId,
  onClose,
  onPasted,
}: {
  kbId: string;
  onClose: () => void;
  onPasted: () => void;
}) {
  const [name, setName] = useState("");
  const [content, setContent] = useState("");

  const pasteMut = useMutation({
    mutationFn: () =>
      api<KbDocument>(`/api/v1/knowledge-bases/${kbId}/documents/paste`, {
        method: "POST",
        body: { name: name.trim(), content },
      }),
    onSuccess: () => {
      toast.success("Текст добавлен в базу");
      onPasted();
      onClose();
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !content.trim()) return;
    pasteMut.mutate();
  };

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal__head">
          <h3>Paste text</h3>
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
                placeholder="e.g. Pricing notes"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="field">
              <label className="field__label">Text *</label>
              <textarea
                className="textarea"
                rows={10}
                placeholder="Paste reference text here…"
                value={content}
                onChange={(e) => setContent(e.target.value)}
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
              Отмена
            </button>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={pasteMut.isPending || !name.trim() || !content.trim()}
            >
              {pasteMut.isPending ? "Добавление…" : "Добавить текст"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}

/* ------------------------------- Search tab ------------------------------ */
function SearchTab({
  kbId,
  indexedCount,
}: {
  kbId: string;
  indexedCount: number;
}) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const searchMut = useMutation({
    mutationFn: (q: string) =>
      api<KbSearchResponse>(`/api/v1/knowledge-bases/${kbId}/search`, {
        method: "POST",
        body: { query: q },
      }),
  });

  const noIndexed = indexedCount === 0;

  const run = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || noIndexed) return;
    setSubmitted(true);
    searchMut.mutate(q);
  };

  const results = searchMut.data?.results ?? [];

  return (
    <div className="card" style={{ padding: 20 }}>
      <form onSubmit={run} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="Search this knowledge base…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={noIndexed}
        />
        <button
          type="submit"
          className="btn btn--primary"
          disabled={searchMut.isPending || noIndexed || !query.trim()}
        >
          <Search size={14} /> Search knowledge base
        </button>
      </form>

      {noIndexed ? (
        <div className="muted" style={{ fontSize: 13 }}>
          В этой базе пока нет проиндексированных документов. Загрузите документы
          на вкладке Documents.
        </div>
      ) : searchMut.error ? (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          {errMsg(searchMut.error)}
        </div>
      ) : searchMut.isPending ? (
        <div className="muted" style={{ fontSize: 13 }}>
          <Loader2 size={14} className="ob__spin" /> Searching…
        </div>
      ) : !submitted ? (
        <div className="muted" style={{ fontSize: 13 }}>
          Введите запрос, чтобы проверить, что находит агент в этой базе.
        </div>
      ) : results.length === 0 ? (
        <div className="muted" style={{ fontSize: 13 }}>
          Ничего не найдено по этому запросу.
        </div>
      ) : (
        <ul
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            margin: 0,
            padding: 0,
            listStyle: "none",
          }}
        >
          {results.map((hit, i) => (
            <li
              key={`${hit.document_id}-${i}`}
              style={{
                background: "var(--bg-soft)",
                borderRadius: 10,
                padding: 14,
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  color: "var(--text)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  marginBottom: 8,
                }}
              >
                {hit.content}
              </div>
              <div
                className="muted text-xs"
                style={{ display: "flex", alignItems: "center", gap: 8 }}
              >
                <FileText size={12} />
                {hit.document_name || "Document"}
                <span style={{ color: "var(--text-faint)" }}>
                  · score {(1 - hit.distance).toFixed(3)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------- Agents tab ------------------------------ */
function AgentsTab({ kbId }: { kbId: string }) {
  const agentsQ = useQuery({
    queryKey: ["kb-agents", kbId],
    queryFn: () =>
      api<AgentForKb[]>(`/api/v1/knowledge-bases/${kbId}/agents`),
  });
  const agents = agentsQ.data ?? [];

  return (
    <div className="card" style={{ padding: 20 }}>
      {agentsQ.isLoading && (
        <div className="muted" style={{ fontSize: 13 }}>
          Loading agents…
        </div>
      )}
      {agentsQ.error && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          {errMsg(agentsQ.error)}
        </div>
      )}
      {agentsQ.data && agents.length === 0 && (
        <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
          Эта база пока не подключена ни к одному агенту. Подключите её в
          настройках агента.
        </div>
      )}
      {agents.length > 0 && (
        <ul
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            margin: 0,
            padding: 0,
            listStyle: "none",
          }}
        >
          {agents.map((a) => (
            <li
              key={a.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 12px",
                background: "var(--bg-soft)",
                borderRadius: 8,
              }}
            >
              <div
                className="avatar avatar--sm"
                style={{ background: "var(--tg-blue)", color: "white", flexShrink: 0 }}
              >
                {(a.agent_name || "A").slice(0, 1).toUpperCase()}
              </div>
              <Link
                to="/agents"
                style={{ fontWeight: 500, color: "var(--tg-blue)" }}
              >
                {a.agent_name}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------ Settings tab ----------------------------- */
function SettingsTab({
  kb,
  onError,
  onDelete,
}: {
  kb: KnowledgeBase;
  onError: (m: string) => void;
  onDelete: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(kb.name);
  const [description, setDescription] = useState(kb.description ?? "");

  const saveMut = useMutation({
    mutationFn: (body: KnowledgeBaseUpdate) =>
      api<KnowledgeBase>(`/api/v1/knowledge-bases/${kb.id}`, {
        method: "PATCH",
        body: body as unknown as Record<string, unknown>,
      }),
    onSuccess: () => {
      toast.success("Settings saved");
      void qc.invalidateQueries({ queryKey: ["knowledge-base", kb.id] });
      void qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
    onError: (e) => onError(errMsg(e)),
  });

  const dirty = name.trim() !== kb.name || (description.trim() || "") !== (kb.description ?? "");

  return (
    <div className="card" style={{ padding: 20, maxWidth: 560 }}>
      <div style={{ display: "grid", gap: 14 }}>
        <div className="field">
          <label className="field__label">Name</label>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field__label">Type</label>
          <input className="input" value="Files" readOnly disabled />
        </div>
        <div className="field">
          <label className="field__label">Description</label>
          <textarea
            className="textarea"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <button
            className="btn btn--ghost btn--sm"
            style={{ color: "var(--danger)" }}
            onClick={onDelete}
          >
            <Trash2 size={14} /> Delete knowledge base
          </button>
          <button
            className="btn btn--primary"
            disabled={saveMut.isPending || !name.trim() || !dirty}
            onClick={() =>
              saveMut.mutate({
                name: name.trim(),
                description: description.trim() || null,
              })
            }
          >
            {saveMut.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------- Confirm dialog ---------------------------- */
function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
        <header className="modal__head">
          <h3>{title}</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onCancel}>
            <X size={16} />
          </button>
        </header>
        <div className="modal__body">
          <p style={{ fontSize: 13, color: "var(--text-soft)", margin: 0 }}>{body}</p>
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
          <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className="btn btn--primary"
            style={{ background: "var(--danger)", borderColor: "var(--danger)" }}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "…" : confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}
