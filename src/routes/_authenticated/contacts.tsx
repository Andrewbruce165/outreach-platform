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
  UserPlus,
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
          <>
            <button className="btn btn--ghost btn--sm" onClick={() => setImportOpen(true)}>
              <Upload size={14} /> Import CSV
            </button>
            <button
              className="btn btn--primary btn--sm"
              onClick={() => {
                // Focus sidebar create form via event flag in URL hash
                window.dispatchEvent(new CustomEvent("aimly:new-folder"));
              }}
            >
              <Plus size={14} /> New folder
            </button>
          </>
        }
      />
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "280px 1fr",
          minHeight: 0,
        }}
      >
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

/* ---------------- Helpers ---------------- */
const FOLDER_PALETTE = [
  "#3b82f6",
  "#8774e1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
];

function folderColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return FOLDER_PALETTE[h % FOLDER_PALETTE.length];
}

function initials(name: string | null | undefined, fallback = "?"): string {
  const n = (name ?? "").trim();
  if (!n) return fallback;
  const parts = n.split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || fallback;
}

function ContactAvatar({ name, phone }: { name: string | null; phone: string | null }) {
  const seed = (name || phone || "?") as string;
  const color = folderColor(seed);
  return (
    <div
      className="avatar avatar--sm"
      style={{ background: `${color}1A`, color }}
    >
      {initials(name, (phone ?? "?").slice(-2))}
    </div>
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

  // Allow the topbar "New folder" button to open the create form
  if (typeof window !== "undefined") {
    // Attach once
    (window as unknown as { __aimlyNewFolderBound?: boolean }).__aimlyNewFolderBound ||
      window.addEventListener("aimly:new-folder", () => setCreating(true));
    (window as unknown as { __aimlyNewFolderBound?: boolean }).__aimlyNewFolderBound = true;
  }

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
    <aside
      style={{
        background: "var(--bg)",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <div
        style={{
          padding: "12px 14px 8px",
          color: "var(--text-faint)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        Folders ({folders.length})
      </div>
      <div className="scroll" style={{ flex: 1, padding: "0 8px" }}>
        {creating && (
          <form
            style={{ padding: 8, display: "flex", flexDirection: "column", gap: 8 }}
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
            <div style={{ display: "flex", gap: 6 }}>
              <button
                type="submit"
                className="btn btn--primary btn--sm"
                disabled={createMut.isPending}
                style={{ flex: 1 }}
              >
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

        {isLoading && (
          <div className="muted text-sm" style={{ padding: 12 }}>
            Loading…
          </div>
        )}

        {!isLoading && folders.length === 0 && !creating && (
          <div style={{ textAlign: "center", padding: "24px 12px" }}>
            <FolderIcon size={20} style={{ color: "var(--text-faint)" }} />
            <p className="muted text-sm" style={{ margin: "8px 0 12px" }}>
              No folders yet
            </p>
          </div>
        )}

        {folders.map((f) => {
          const sel = f.id === activeId;
          const color = folderColor(f.id);
          return (
            <button
              key={f.id}
              onClick={() => onSelect(f.id)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 12px",
                borderRadius: 9,
                marginBottom: 1,
                background: sel ? "var(--tg-blue-soft)" : "transparent",
                color: sel ? "var(--tg-blue)" : "var(--text-soft)",
                textAlign: "left",
              }}
            >
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 7,
                  background: `${color}1A`,
                  color,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <FolderIcon size={14} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {f.name}
                </div>
                <div className="muted text-xs">
                  {f.contact_count.toLocaleString()} contacts
                </div>
              </div>
            </button>
          );
        })}

        {!creating && (
          <button
            onClick={() => setCreating(true)}
            style={{
              width: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              padding: "10px 12px",
              borderRadius: 9,
              marginTop: 6,
              border: "1px dashed var(--border-strong)",
              color: "var(--text-muted)",
              fontSize: 12.5,
              background: "transparent",
            }}
          >
            <Plus size={13} /> New folder
          </button>
        )}
      </div>
    </aside>
  );
}

/* ---------------- Folder detail (right pane) ---------------- */
function FolderDetail({ folder, onImport }: { folder: Folder | null; onImport: () => void }) {
  const qc = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);

  const contactsQ = useQuery({
    queryKey: ["contacts", folder?.id],
    queryFn: () =>
      api<Contact[]>("/api/v1/contacts", { query: { folder_id: folder!.id, limit: 200 } }),
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

  const stats = useMemo(() => {
    const inTg = contacts.filter((c) => c.tg_status === "ok").length;
    const checking = contacts.filter(
      (c) => c.tg_status === "checking" || c.tg_status === "unknown",
    ).length;
    const notFound = contacts.filter(
      (c) => c.tg_status === "not_found" || c.tg_status === "privacy",
    ).length;
    return { inTg, checking, notFound };
  }, [contacts]);

  if (!folder) {
    return (
      <section className="scroll" style={{ background: "var(--bg-soft)", padding: 24 }}>
        <EmptyState
          onImport={onImport}
          title="No folder selected"
          body="Pick a folder on the left or import a CSV to get started."
        />
      </section>
    );
  }

  const color = folderColor(folder.id);
  const total = folder.contact_count;

  return (
    <section className="scroll" style={{ background: "var(--bg-soft)", padding: 24 }}>
      {/* Folder header */}
      <div style={{ display: "flex", alignItems: "center", marginBottom: 16, gap: 14 }}>
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 11,
            background: `${color}1A`,
            color,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <FolderIcon size={22} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          {renaming ? (
            <form
              style={{ display: "flex", gap: 8 }}
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
              <button type="submit" className="btn btn--primary btn--sm">
                Save
              </button>
            </form>
          ) : (
            <>
              <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>
                {folder.name}
              </div>
              <div className="muted text-sm">
                {total.toLocaleString()} contacts · updated {relativeDate(folder.updated_at)}
              </div>
            </>
          )}
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
          onClick={() => toast.info("Move to… coming soon")}
        >
          <Shuffle size={13} /> Move to…
        </button>
        <button
          className="btn btn--ghost btn--sm"
          style={{ color: "var(--danger)" }}
          onClick={() => {
            if (confirm(`Delete folder "${folder.name}"? Contacts will be removed.`)) {
              deleteMut.mutate();
            }
          }}
          aria-label="Delete folder"
        >
          <Trash2 size={13} />
        </button>
      </div>

      {/* Stats */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <MiniMetric label="Total" value={total} sub="All sources" color="var(--tg-blue)" />
        <MiniMetric
          label="In Telegram"
          value={stats.inTg}
          sub={total > 0 ? `${Math.round((stats.inTg / total) * 100)}% match` : "—"}
          color="var(--success)"
        />
        <MiniMetric
          label="Checking"
          value={stats.checking}
          sub="Awaiting resolve"
          color="var(--ai-purple)"
        />
        <MiniMetric
          label="Not found"
          value={stats.notFound}
          sub="Privacy or missing"
          color="var(--warning)"
        />
      </div>

      {/* Contacts table */}
      <div className="card" style={{ overflow: "hidden" }}>
        <div
          className="card__header"
          style={{ gap: 10, display: "flex", alignItems: "center", padding: "12px 14px" }}
        >
          <div style={{ position: "relative" }}>
            <Search
              size={14}
              style={{ position: "absolute", left: 10, top: 9, color: "var(--text-faint)" }}
            />
            <input
              className="input"
              style={{ paddingLeft: 30, height: 32, fontSize: 12.5, width: 240 }}
              placeholder="Search contacts…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button className="btn btn--ghost btn--sm" type="button">
            <Filter size={12} /> Filters
          </button>
          <span style={{ flex: 1 }} />
          <span className="muted text-xs">
            Showing {filtered.length.toLocaleString()} of {total.toLocaleString()}
          </span>
        </div>

        {contactsQ.isLoading && (
          <div className="muted" style={{ padding: 24 }}>
            Loading contacts…
          </div>
        )}

        {!contactsQ.isLoading && contacts.length === 0 && (
          <EmptyState
            onImport={onImport}
            title="No contacts in this folder"
            body="Import a CSV to add people."
          />
        )}

        {!contactsQ.isLoading && contacts.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input type="checkbox" disabled />
                </th>
                <th>Contact</th>
                <th>Company · Role</th>
                <th>Username</th>
                <th>Phone</th>
                <th>Source</th>
                <th>In TG</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => {
                const company =
                  typeof c.custom?.company === "string" ? (c.custom.company as string) : "";
                const role =
                  typeof c.custom?.role === "string" ? (c.custom.role as string) : "";
                return (
                  <tr key={c.id}>
                    <td>
                      <input type="checkbox" />
                    </td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <ContactAvatar name={c.full_name} phone={c.phone} />
                        <span style={{ fontWeight: 500, fontSize: 13 }}>
                          {c.full_name || <span className="faint">—</span>}
                        </span>
                      </div>
                    </td>
                    <td>
                      {company || role ? (
                        <>
                          <div style={{ fontSize: 12.5 }}>{company || "—"}</div>
                          <div className="muted text-xs">{role || ""}</div>
                        </>
                      ) : (
                        <span className="faint">—</span>
                      )}
                    </td>
                    <td>
                      {c.username ? (
                        <span className="mono text-sm" style={{ color: "var(--tg-blue)" }}>
                          {c.username.startsWith("@") ? c.username : `@${c.username}`}
                        </span>
                      ) : (
                        <span className="muted text-xs">— phone only</span>
                      )}
                    </td>
                    <td className="muted text-xs mono">{c.phone || "—"}</td>
                    <td>
                      {c.source ? (
                        <span className="pill">{c.source}</span>
                      ) : (
                        <span className="faint">—</span>
                      )}
                    </td>
                    <td>
                      <TgInline status={c.tg_status} />
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted" style={{ textAlign: "center", padding: 24 }}>
                    No matches for &quot;{search}&quot;
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function MiniMetric({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: number;
  sub: string;
  color: string;
}) {
  return (
    <div
      className="card"
      style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 4 }}
    >
      <span className="muted text-xs" style={{ fontWeight: 500, letterSpacing: 0.2 }}>
        {label}
      </span>
      <span
        className="num"
        style={{ fontSize: 24, fontWeight: 600, color, lineHeight: 1.1 }}
      >
        {value.toLocaleString()}
      </span>
      <span className="muted text-xs">{sub}</span>
    </div>
  );
}

function TgInline({ status }: { status: string }) {
  if (status === "ok") {
    return (
      <span
        style={{
          color: "var(--success)",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 12,
        }}
      >
        <Check size={12} /> Yes
      </span>
    );
  }
  if (status === "checking" || status === "unknown") {
    return (
      <span
        style={{
          color: "var(--text-faint)",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 12,
        }}
      >
        <Clock size={12} /> Checking…
      </span>
    );
  }
  if (status === "not_found") {
    return (
      <span className="pill pill--red">
        <span className="pill__dot" /> Not found
      </span>
    );
  }
  if (status === "privacy") {
    return (
      <span className="pill pill--orange">
        <span className="pill__dot" /> Privacy
      </span>
    );
  }
  return (
    <span className="pill pill--ghost">
      <span className="pill__dot" /> {status}
    </span>
  );
}

function relativeDate(iso: string): string {
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function EmptyState({
  title,
  body,
  onImport,
}: {
  title: string;
  body: string;
  onImport: () => void;
}) {
  return (
    <div style={{ padding: 48, textAlign: "center" }}>
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 28,
          margin: "0 auto 16px",
          background: "var(--tg-blue-soft)",
          color: "var(--tg-blue)",
          display: "grid",
          placeItems: "center",
        }}
      >
        <Users size={24} />
      </div>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>{title}</h3>
      <p className="muted text-sm" style={{ maxWidth: 320, margin: "0 auto 16px" }}>
        {body}
      </p>
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
