import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Plus,
  Folder as FolderIcon,
  Upload,
  Search,
  Trash2,
  Edit3,
  RefreshCcw,
  X,
  AlertCircle,
  CheckCircle2,
  Users,
  Loader2,
  Filter,
  Check,
  Clock,
  Shuffle,
} from "lucide-react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";


type Folder = components["schemas"]["FolderResponse"];
type Contact = components["schemas"]["ContactResponse"];
type ImportPreview = components["schemas"]["ContactImportPreviewResponse"];
type ImportSummary = components["schemas"]["ContactImportSummary"];

const TARGET_FIELDS = [
  { key: "phone", label: "Phone" },
  { key: "username", label: "Telegram username" },
  { key: "full_name", label: "Full name" },
  { key: "source", label: "Source" },
] as const;

export const Route = createFileRoute("/_authenticated/contacts")({
  component: ContactsPage,
});

function ContactsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const qc = useQueryClient();

  const foldersQ = useQuery({
    queryKey: ["folders"],
    queryFn: () => api<Folder[]>("/api/v1/folders"),
  });

  // Auto-select first folder
  const activeId = selectedId ?? foldersQ.data?.[0]?.id ?? null;
  const activeFolder = foldersQ.data?.find((f) => f.id === activeId) ?? null;

  return (
    <>
      <Topbar
        title="Contacts"
        right={
          <button className="btn btn--primary btn--sm" onClick={() => setImportOpen(true)}>
            <Upload size={14} /> Import CSV
          </button>
        }
      />
      <div className="ct" style={{ flex: 1, minHeight: 0 }}>
        <FolderSidebar
          folders={foldersQ.data ?? []}
          isLoading={foldersQ.isLoading}
          activeId={activeId}
          onSelect={setSelectedId}
        />
        <FolderDetail folder={activeFolder} onImport={() => setImportOpen(true)} />
      </div>

      {importOpen && (
        <ImportModal
          folders={foldersQ.data ?? []}
          defaultFolderId={activeId}
          onClose={() => setImportOpen(false)}
          onDone={() => {
            void qc.invalidateQueries({ queryKey: ["folders"] });
            void qc.invalidateQueries({ queryKey: ["contacts"] });
          }}
        />
      )}
    </>
  );
}

/* ---------------- Folder sidebar ---------------- */
function FolderSidebar({
  folders,
  isLoading,
  activeId,
  onSelect,
}: {
  folders: Folder[];
  isLoading: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const createMut = useMutation({
    mutationFn: (n: string) =>
      api<Folder>("/api/v1/folders", { method: "POST", body: { name: n } }),
    onSuccess: (folder) => {
      void qc.invalidateQueries({ queryKey: ["folders"] });
      setCreating(false);
      setName("");
      onSelect(folder.id);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Couldn't create folder"),
  });

  return (
    <aside className="ct__side">
      <div className="ct__sideHead">
        <span className="ct__sideTitle">Folders</span>
        <button
          className="tb__icon-btn"
          aria-label="New folder"
          onClick={() => setCreating((v) => !v)}
        >
          <Plus size={14} />
        </button>
      </div>
      {creating && (
        <form
          className="ct__newFolder"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) createMut.mutate(name.trim());
          }}
        >
          <input
            className="input"
            autoFocus
            placeholder="Folder name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <div className="row">
            <button type="submit" className="btn btn--primary btn--sm" disabled={createMut.isPending}>
              Create
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                setCreating(false);
                setName("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {isLoading && <div className="muted text-sm" style={{ padding: 12 }}>Loading…</div>}
      {!isLoading && folders.length === 0 && !creating && (
        <div className="ct__sideEmpty">
          <FolderIcon size={20} />
          <p>No folders yet</p>
          <button className="btn btn--soft btn--sm" onClick={() => setCreating(true)}>
            <Plus size={12} /> Create folder
          </button>
        </div>
      )}
      <ul className="ct__list">
        {folders.map((f) => (
          <li key={f.id}>
            <button
              className={`ct__listItem ${f.id === activeId ? "is-active" : ""}`}
              onClick={() => onSelect(f.id)}
            >
              <FolderIcon size={14} />
              <span className="ct__listName">{f.name}</span>
              <span className="ct__listCount num">{f.contact_count}</span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

/* ---------------- Folder detail (right pane) ---------------- */
function FolderDetail({ folder, onImport }: { folder: Folder | null; onImport: () => void }) {
  const qc = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [search, setSearch] = useState("");

  const contactsQ = useQuery({
    queryKey: ["contacts", folder?.id],
    queryFn: () => api<Contact[]>("/api/v1/contacts", { query: { folder_id: folder!.id, limit: 200 } }),
    enabled: !!folder,
  });

  const renameMut = useMutation({
    mutationFn: (name: string) =>
      api<Folder>(`/api/v1/folders/${folder!.id}`, { method: "PATCH", body: { name } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["folders"] });
      setRenaming(false);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Rename failed"),
  });

  const deleteMut = useMutation({
    mutationFn: () => api(`/api/v1/folders/${folder!.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["folders"] });
      toast.success("Folder deleted");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });

  const recheckMut = useMutation({
    mutationFn: () =>
      api("/api/v1/contacts/recheck", { method: "POST", body: { folder_id: folder!.id } }),
    onSuccess: () => {
      toast.success("Recheck queued");
      void qc.invalidateQueries({ queryKey: ["contacts", folder?.id] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Recheck failed"),
  });

  const contacts = contactsQ.data ?? [];
  const filtered = useMemo(() => {
    if (!search.trim()) return contacts;
    const q = search.toLowerCase();
    return contacts.filter(
      (c) =>
        (c.full_name ?? "").toLowerCase().includes(q) ||
        (c.username ?? "").toLowerCase().includes(q) ||
        (c.phone ?? "").includes(q),
    );
  }, [contacts, search]);

  if (!folder) {
    return (
      <section className="ct__main">
        <EmptyState onImport={onImport} title="No folder selected" body="Pick a folder on the left or import a CSV to get started." />
      </section>
    );
  }




  return (
    <section className="ct__main">
      <header className="ct__mainHead">
        {renaming ? (
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              if (renameValue.trim()) renameMut.mutate(renameValue.trim());
            }}
          >
            <input
              className="input"
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onBlur={() => setRenaming(false)}
            />
            <button type="submit" className="btn btn--primary btn--sm">Save</button>
          </form>
        ) : (
          <div>
            <h2 className="ct__mainTitle">{folder.name}</h2>
            <p className="muted text-sm">{folder.contact_count} contacts</p>
          </div>
        )}
        <div className="row">
          <div className="ct__search">
            <Search size={14} />
            <input
              className="input"
              placeholder="Search contacts"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => recheckMut.mutate()}
            disabled={recheckMut.isPending}
          >
            <RefreshCcw size={13} /> Recheck
          </button>
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => {
              setRenameValue(folder.name);
              setRenaming(true);
            }}
          >
            <Edit3 size={13} /> Rename
          </button>
          <button
            className="btn btn--ghost btn--sm"
            style={{ color: "var(--danger)" }}
            onClick={() => {
              if (confirm(`Delete folder "${folder.name}"? Contacts will be removed.`)) {
                deleteMut.mutate();
              }
            }}
          >
            <Trash2 size={13} /> Delete
          </button>
        </div>
      </header>

      <div className="scroll" style={{ flex: 1 }}>
        {contactsQ.isLoading && (
          <div className="muted" style={{ padding: 24 }}>Loading contacts…</div>
        )}
        {!contactsQ.isLoading && contacts.length === 0 && (
          <EmptyState onImport={onImport} title="No contacts in this folder" body="Import a CSV to add people." />
        )}
        {!contactsQ.isLoading && contacts.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Username</th>
                <th>Telegram</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr key={c.id}>
                  <td className="fw5">{c.full_name || <span className="faint">—</span>}</td>
                  <td className="mono text-sm">{c.phone || <span className="faint">—</span>}</td>
                  <td className="mono text-sm">{c.username || <span className="faint">—</span>}</td>
                  <td><TgStatus status={c.tg_status} /></td>
                  <td className="muted text-sm">{c.source || "—"}</td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={5} className="muted" style={{ textAlign: "center", padding: 24 }}>
                  No matches for &quot;{search}&quot;
                </td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function TgStatus({ status }: { status: string }) {
  const map: Record<string, { cls: string; label: string }> = {
    ok: { cls: "pill--green", label: "On Telegram" },
    unknown: { cls: "pill--ghost", label: "Unchecked" },
    not_found: { cls: "pill--red", label: "Not found" },
    privacy: { cls: "pill--orange", label: "Privacy locked" },
    checking: { cls: "pill--blue", label: "Checking…" },
  };
  const s = map[status] ?? { cls: "pill--ghost", label: status };
  return <span className={`pill ${s.cls}`}><span className="pill__dot" />{s.label}</span>;
}

function EmptyState({ title, body, onImport }: { title: string; body: string; onImport: () => void }) {
  return (
    <div style={{ padding: 48, textAlign: "center" }}>
      <div style={{
        width: 56, height: 56, borderRadius: 28, margin: "0 auto 16px",
        background: "var(--tg-blue-soft)", color: "var(--tg-blue)",
        display: "grid", placeItems: "center",
      }}>
        <Users size={24} />
      </div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>{title}</h3>
      <p className="muted text-sm" style={{ maxWidth: 320, margin: "0 auto 16px" }}>{body}</p>
      <button className="btn btn--primary" onClick={onImport}>
        <Upload size={14} /> Import CSV
      </button>
    </div>
  );
}

/* ---------------- 4-stage import modal ---------------- */
type ImportStage = "upload" | "mapping" | "importing" | "done";

function ImportModal({
  folders,
  defaultFolderId,
  onClose,
  onDone,
}: {
  folders: Folder[];
  defaultFolderId: string | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [stage, setStage] = useState<ImportStage>("upload");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [folderMode, setFolderMode] = useState<"existing" | "new">(
    defaultFolderId ? "existing" : "new",
  );
  const [folderId, setFolderId] = useState<string | null>(defaultFolderId);
  const [folderName, setFolderName] = useState("");
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFile(file: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api<ImportPreview>("/api/v1/contacts/import/preview", {
        method: "POST",
        body: form,
      });
      setPreview(res);
      setMapping(res.suggested_mapping ?? {});
      setStage("mapping");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function runImport() {
    if (!preview) return;
    setStage("importing");
    try {
      const res = await api<ImportSummary>("/api/v1/contacts/import", {
        method: "POST",
        body: {
          import_id: preview.import_id,
          mapping,
          on_duplicate: "skip",
          folder_id: folderMode === "existing" ? folderId : null,
          folder_name: folderMode === "new" ? folderName.trim() : null,
        },
      });
      setSummary(res);
      track("contacts_imported", {
        folder_id: folderId ?? "new",
        created: res.imported,
        updated: 0,
        skipped: res.skipped_duplicates + res.skipped_invalid,
      });
      track("csv_import_completed", {
        folder_id: folderId ?? "new",
        created: res.imported,
        updated: 0,
        skipped: res.skipped_duplicates + res.skipped_invalid,
      });
      setStage("done");
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Import failed");
      setStage("mapping");
    }
  }

  const phoneOrUsernameMapped =
    Object.values(mapping).includes("phone") || Object.values(mapping).includes("username");

  return (
    <div className="modal__scrim" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal__head">
          <h3>Import contacts</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </header>
        <ImportStepper stage={stage} />
        <div className="modal__body">
          {stage === "upload" && (
            <div className="ct__upload">
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
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
                  <><Loader2 size={20} className="ob__spin" /><span>Parsing…</span></>
                ) : (
                  <>
                    <Upload size={24} />
                    <span className="fw5">Drop a CSV here, or click to choose</span>
                    <span className="muted text-sm">phone, username, full_name, source columns are auto-detected</span>
                  </>
                )}
              </div>
            </div>
          )}

          {stage === "mapping" && preview && (
            <div className="ct__mapping">
              <p className="muted text-sm">
                Map your CSV columns to aimly fields. We&apos;ll skip any unmapped columns.
              </p>
              <div className="ct__mapGrid">
                <div className="text-xs muted fw5">CSV column</div>
                <div className="text-xs muted fw5">aimly field</div>
                <div className="text-xs muted fw5">Sample</div>
                {preview.columns.map((col) => (
                  <RowMap
                    key={col}
                    col={col}
                    sample={String(preview.sample_rows[0]?.[col] ?? "")}
                    value={mapping[col] ?? ""}
                    onChange={(v) =>
                      setMapping((prev) => {
                        const next = { ...prev };
                        if (v) next[col] = v;
                        else delete next[col];
                        return next;
                      })
                    }
                  />
                ))}
              </div>

              <div className="divider-h" />

              <div className="col" style={{ gap: 12 }}>
                <div className="ct__folderChoice">
                  <label className={`ct__choice ${folderMode === "existing" ? "is-active" : ""}`}>
                    <input
                      type="radio"
                      checked={folderMode === "existing"}
                      onChange={() => setFolderMode("existing")}
                    />
                    Existing folder
                  </label>
                  <label className={`ct__choice ${folderMode === "new" ? "is-active" : ""}`}>
                    <input
                      type="radio"
                      checked={folderMode === "new"}
                      onChange={() => setFolderMode("new")}
                    />
                    New folder
                  </label>
                </div>
                {folderMode === "existing" ? (
                  <select
                    className="select"
                    value={folderId ?? ""}
                    onChange={(e) => setFolderId(e.target.value || null)}
                  >
                    <option value="" disabled>Choose a folder…</option>
                    {folders.map((f) => (
                      <option key={f.id} value={f.id}>{f.name} ({f.contact_count})</option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="input"
                    placeholder="e.g. Q2 leads"
                    value={folderName}
                    onChange={(e) => setFolderName(e.target.value)}
                  />
                )}
              </div>

              {!phoneOrUsernameMapped && (
                <div className="ct__warn"><AlertCircle size={14} /> Map at least <b>Phone</b> or <b>Telegram username</b> to import.</div>
              )}

              <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
                <button className="btn btn--ghost" onClick={() => setStage("upload")}>Back</button>
                <button
                  className="btn btn--primary"
                  disabled={
                    !phoneOrUsernameMapped ||
                    (folderMode === "existing" && !folderId) ||
                    (folderMode === "new" && !folderName.trim())
                  }
                  onClick={runImport}
                >
                  Import contacts
                </button>
              </div>
            </div>
          )}

          {stage === "importing" && (
            <div className="ct__importing">
              <Loader2 size={32} className="ob__spin" />
              <h3>Importing contacts…</h3>
              <p className="muted text-sm">This usually takes a few seconds.</p>
            </div>
          )}

          {stage === "done" && summary && (
            <div className="ob__success">
              <div className="ob__successIcon"><CheckCircle2 size={36} /></div>
              <h3 className="ob__successTitle">Import complete</h3>
              <div className="ct__summary">
                <div><b className="num">{summary.imported}</b><span className="muted text-sm">Imported</span></div>
                <div><b className="num">{summary.skipped_duplicates}</b><span className="muted text-sm">Duplicates</span></div>
                <div><b className="num">{summary.skipped_invalid}</b><span className="muted text-sm">Invalid</span></div>
                <div><b className="num">{summary.total}</b><span className="muted text-sm">Total rows</span></div>
              </div>
              <button className="btn btn--primary" onClick={onClose}>Done</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RowMap({
  col,
  sample,
  value,
  onChange,
}: {
  col: string;
  sample: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <>
      <div className="ct__mapCol mono text-sm">{col}</div>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">— skip —</option>
        {TARGET_FIELDS.map((f) => (
          <option key={f.key} value={f.key}>{f.label}</option>
        ))}
      </select>
      <div className="muted text-sm" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {sample || <span className="faint">empty</span>}
      </div>
    </>
  );
}

function ImportStepper({ stage }: { stage: ImportStage }) {
  const order: ImportStage[] = ["upload", "mapping", "importing", "done"];
  const labels: Record<ImportStage, string> = {
    upload: "Upload",
    mapping: "Map columns",
    importing: "Importing",
    done: "Done",
  };
  const idx = order.indexOf(stage);
  return (
    <ol className="ob__stepper" style={{ padding: "12px 18px", borderBottom: "1px solid var(--border)", margin: 0 }}>
      {order.map((s, i) => {
        const state = i < idx ? "done" : i === idx ? "active" : "todo";
        return (
          <li key={s} className={`ob__step is-${state}`}>
            <span className="ob__stepDot">{i + 1}</span>
            <span className="ob__stepLabel">{labels[s]}</span>
          </li>
        );
      })}
    </ol>
  );
}
