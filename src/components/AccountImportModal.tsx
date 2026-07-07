import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  X,
  Upload,
  Phone as PhoneIcon,
  ShieldCheck,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  FileWarning,
  Link2Off,
  KeyRound,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";

type PreviewResponse = components["schemas"]["AccountImportPreviewResponse"];
type ConfirmResponse = components["schemas"]["ImportConfirmResponse"];
type StatusResponse = components["schemas"]["ImportStatusResponse"];
type StatusItem = components["schemas"]["ImportStatusItem"];

type Role = "sender" | "checker";
type Step = "upload" | "preview" | "progress";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return "Something went wrong. Try again.";
}

/**
 * Two-step bulk account import (Phase 21 / IMPT-09).
 *
 * upload → POST /accounts/import/preview (multipart ZIP) → recognized-set summary
 *   → pick role (sender|checker, one for the whole batch, D-16)
 *   → POST /import/{import_id}/confirm → 202 job_id
 *   → poll GET /import/{job_id}/status every 2s until status === "done"
 *
 * Never renders the twoFA value nor session bytes — the preview/status payloads are
 * secrets-free by construction (D-07); we only surface flags/basenames/status/reason.
 */
export function AccountImportModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Step 1: upload → preview ─────────────────────────────────────────────
  const previewMut = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      return api<PreviewResponse>("/api/v1/accounts/import/preview", {
        method: "POST",
        body: fd,
      });
    },
    onSuccess: (res) => {
      setPreview(res);
      setStep("preview");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  function handleFile(f: File | null) {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".zip")) {
      toast.error("Upload a .zip archive of account files.");
      return;
    }
    setFile(f);
  }

  // ── Step 2: confirm → job ──────────────────────────────────────────────────
  const confirmMut = useMutation({
    mutationFn: (args: { importId: string; role: Role }) =>
      api<ConfirmResponse>(`/api/v1/accounts/import/${args.importId}/confirm`, {
        method: "POST",
        body: { role: args.role },
      }),
    onSuccess: (res) => {
      setJobId(res.job_id);
      setStep("progress");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  // ── Progress: poll status until done ───────────────────────────────────────
  const statusQ = useQuery({
    queryKey: ["account-import-status", jobId],
    queryFn: () =>
      api<StatusResponse>(`/api/v1/accounts/import/${jobId}/status`),
    enabled: step === "progress" && !!jobId,
    // Poll every 2s while the batch is still draining; stop once the worker
    // flips the job to "done" (never dies even with broken entries, IMPT-07).
    refetchInterval: (q) => {
      const data = q.state.data as StatusResponse | undefined;
      return data && data.status === "done" ? false : 2000;
    },
  });

  const done = statusQ.data?.status === "done";
  // When the job is done, the accounts list may now show newly-imported rows.
  if (done) {
    void qc.invalidateQueries({ queryKey: ["senders"] });
  }

  const matchedCount = preview?.matched.length ?? 0;

  return (
    <div
      className="modal__scrim"
      role="dialog"
      aria-modal="true"
      aria-label="Import Telegram accounts"
      onClick={onClose}
    >
      <div
        className="modal modal--wide"
        onClick={(e) => e.stopPropagation()}
        style={{ maxHeight: "90vh", display: "flex", flexDirection: "column" }}
      >
        <header className="modal__head">
          <h3>Import accounts</h3>
          <button className="tb__icon-btn" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>

        <div
          className="modal__body scroll"
          style={{ display: "flex", flexDirection: "column", gap: 16 }}
        >
          {/* ── STEP 1: UPLOAD ───────────────────────────────────────────── */}
          {step === "upload" && (
            <>
              <p className="muted text-sm" style={{ margin: 0 }}>
                Upload one <code>.zip</code> containing your{" "}
                <code>&lt;phone&gt;.json</code> + <code>&lt;phone&gt;.session</code>{" "}
                account pairs. We&apos;ll show you what was recognized before anything
                is imported.
              </p>

              <label
                className="card"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 8,
                  padding: 28,
                  cursor: "pointer",
                  borderStyle: "dashed",
                }}
              >
                <Upload size={22} style={{ color: "var(--tg-blue)" }} />
                <span style={{ fontWeight: 500 }}>
                  {file ? file.name : "Choose a .zip file"}
                </span>
                <span className="muted text-xs">
                  {file
                    ? `${(file.size / 1024 / 1024).toFixed(1)} MB`
                    : "Only the recognized-set summary is returned — no secrets leave your box"}
                </span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip,application/zip"
                  style={{ display: "none" }}
                  onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
                />
              </label>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button className="btn btn--ghost" onClick={onClose}>
                  Cancel
                </button>
                <button
                  className="btn btn--primary"
                  disabled={!file || previewMut.isPending}
                  onClick={() => file && previewMut.mutate(file)}
                >
                  {previewMut.isPending ? (
                    <>
                      <Loader2 size={14} className="ob__spin" /> Reading…
                    </>
                  ) : (
                    "Preview"
                  )}
                </button>
              </div>
            </>
          )}

          {/* ── STEP 1b: PREVIEW + ROLE ──────────────────────────────────── */}
          {step === "preview" && preview && (
            <>
              {/* Counts */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: 12,
                }}
              >
                <CountCard
                  label="Matched pairs"
                  value={matchedCount}
                  color="var(--success)"
                />
                <CountCard
                  label="Unpaired"
                  value={preview.unpaired.length}
                  color="var(--warning)"
                />
                <CountCard
                  label="Malformed"
                  value={preview.malformed.length}
                  color="var(--danger)"
                />
              </div>

              {/* Matched */}
              {preview.matched.length > 0 && (
                <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <h4 className="muted text-xs" style={{ margin: 0, letterSpacing: 0.3 }}>
                    RECOGNIZED PAIRS
                  </h4>
                  <div className="card" style={{ padding: 0 }}>
                    {preview.matched.map((m) => (
                      <div
                        key={m.basename}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "10px 14px",
                          borderBottom: "1px solid var(--border)",
                        }}
                      >
                        <CheckCircle2 size={15} style={{ color: "var(--success)" }} />
                        <span className="num" style={{ fontWeight: 500 }}>
                          {m.phone}
                        </span>
                        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                          {m.has_2fa && (
                            <span className="pill pill--ghost" title="Has a 2FA password">
                              <KeyRound size={12} /> 2FA
                            </span>
                          )}
                          {m.has_proxy && (
                            <span className="pill pill--ghost" title="Ships with a proxy">
                              proxy
                            </span>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Unpaired */}
              {preview.unpaired.length > 0 && (
                <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <h4 className="muted text-xs" style={{ margin: 0, letterSpacing: 0.3 }}>
                    UNPAIRED (skipped — needs both .json and .session)
                  </h4>
                  <div className="card" style={{ padding: 0 }}>
                    {preview.unpaired.map((u) => (
                      <div
                        key={u.filename}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "8px 14px",
                          borderBottom: "1px solid var(--border)",
                        }}
                      >
                        <Link2Off size={14} style={{ color: "var(--warning)" }} />
                        <span className="text-sm">{u.filename}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Malformed */}
              {preview.malformed.length > 0 && (
                <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <h4 className="muted text-xs" style={{ margin: 0, letterSpacing: 0.3 }}>
                    MALFORMED (skipped)
                  </h4>
                  <div className="card" style={{ padding: 0 }}>
                    {preview.malformed.map((m) => (
                      <div
                        key={m.filename}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "8px 14px",
                          borderBottom: "1px solid var(--border)",
                        }}
                      >
                        <FileWarning size={14} style={{ color: "var(--danger)" }} />
                        <span className="text-sm">{m.filename}</span>
                        <span className="muted text-xs" style={{ marginLeft: "auto" }}>
                          {m.reason}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Role selection */}
              <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <h4 className="muted text-xs" style={{ margin: 0, letterSpacing: 0.3 }}>
                  ROLE FOR THIS BATCH
                </h4>
                <div className="ob__roles" role="radiogroup" aria-label="Account role">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={role === "sender"}
                    className={`ob__role ${role === "sender" ? "is-active" : ""}`}
                    onClick={() => setRole("sender")}
                  >
                    <span className="ob__roleTitle">
                      <PhoneIcon size={14} /> Sender
                    </span>
                    <span className="ob__roleHint">
                      Sends outreach messages (4/min · 20/hr · 150/day)
                    </span>
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={role === "checker"}
                    className={`ob__role ${role === "checker" ? "is-active" : ""}`}
                    onClick={() => setRole("checker")}
                  >
                    <span className="ob__roleTitle">
                      <ShieldCheck size={14} /> Checker
                    </span>
                    <span className="ob__roleHint">
                      Verifies whether phone numbers exist on Telegram
                    </span>
                  </button>
                </div>
              </section>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <button
                  className="btn btn--ghost"
                  onClick={() => {
                    setStep("upload");
                    setPreview(null);
                  }}
                >
                  Back
                </button>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {matchedCount === 0 && (
                    <span className="muted text-xs">Nothing to import</span>
                  )}
                  <button
                    className="btn btn--primary"
                    disabled={
                      !role || matchedCount === 0 || confirmMut.isPending
                    }
                    onClick={() =>
                      role &&
                      preview &&
                      confirmMut.mutate({ importId: preview.import_id, role })
                    }
                  >
                    {confirmMut.isPending ? (
                      <>
                        <Loader2 size={14} className="ob__spin" /> Starting…
                      </>
                    ) : (
                      `Import ${matchedCount} account${matchedCount === 1 ? "" : "s"}`
                    )}
                  </button>
                </div>
              </div>
            </>
          )}

          {/* ── STEP 2: PROGRESS ─────────────────────────────────────────── */}
          {step === "progress" && (
            <>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                }}
              >
                {done ? (
                  <CheckCircle2 size={18} style={{ color: "var(--success)" }} />
                ) : (
                  <Loader2 size={18} className="ob__spin" style={{ color: "var(--tg-blue)" }} />
                )}
                <span style={{ fontWeight: 500 }}>
                  {done
                    ? "Import complete"
                    : `Importing… ${statusQ.data?.processed ?? 0} / ${statusQ.data?.total ?? matchedCount}`}
                </span>
              </div>

              {statusQ.isError && (
                <div className="card" style={{ padding: 12, display: "flex", gap: 8 }}>
                  <AlertTriangle size={15} style={{ color: "var(--danger)" }} />
                  <span className="text-sm">{errMsg(statusQ.error)}</span>
                </div>
              )}

              <div className="card" style={{ padding: 0 }}>
                {(statusQ.data?.items ?? []).map((it) => (
                  <ResultRow key={it.basename} item={it} />
                ))}
                {(statusQ.data?.items?.length ?? 0) === 0 && (
                  <div className="muted text-sm" style={{ padding: 14 }}>
                    Preparing…
                  </div>
                )}
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button className="btn btn--primary" disabled={!done} onClick={onClose}>
                  {done ? "Done" : "Working…"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CountCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div
      className="card"
      style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 2 }}
    >
      <span className="num" style={{ fontSize: 22, fontWeight: 600, color, lineHeight: 1.1 }}>
        {value}
      </span>
      <span className="muted text-xs">{label}</span>
    </div>
  );
}

/**
 * Per-account result row. The item `status` is the worker's terminal state
 * (pending/processing/ok/failed); `result` is the result-code (imported /
 * already_connected / auth_failed / …). We surface a friendly chip + reason.
 */
function ResultRow({ item }: { item: StatusItem }) {
  const { label, pill, reason } = resultPresentation(item);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "9px 14px",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <span className="num" style={{ fontWeight: 500 }}>
        {item.basename}
      </span>
      <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
        {reason && <span className="muted text-xs">{reason}</span>}
        <span className={`pill ${pill}`}>
          <span className="pill__dot" /> {label}
        </span>
      </span>
    </div>
  );
}

const RESULT_LABEL: Record<string, string> = {
  imported: "Imported",
  already_connected: "Already connected",
  auth_failed: "Auth failed",
  not_authorized: "Not authorized",
  banned: "Banned",
  connect_failed: "Connect failed",
  convert_failed: "Session invalid",
  malformed_json: "Malformed",
  failed: "Failed",
};

function resultPresentation(item: StatusItem): {
  label: string;
  pill: string;
  reason: string | null;
} {
  if (item.status === "pending") {
    return { label: "Queued", pill: "pill--ghost", reason: null };
  }
  if (item.status === "processing") {
    return { label: "Importing…", pill: "pill--blue", reason: null };
  }
  if (item.status === "ok") {
    if (item.result === "already_connected") {
      return { label: "Already connected", pill: "pill--ghost", reason: null };
    }
    return { label: "Imported", pill: "pill--green", reason: null };
  }
  // failed
  const label = (item.result && RESULT_LABEL[item.result]) || "Failed";
  return { label, pill: "pill--red", reason: item.reason ?? null };
}
