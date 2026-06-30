import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  Filter,
  Brain,
  Download,
  Send,
  Paperclip,
  Smile,
  Sparkles,
  MessageCircle,
  Check,
  X,
  Flag,
  Trash2,
  CheckSquare,
  User as UserIcon,
  ChevronDown,
  Phone,
  Bot,
} from "lucide-react";
import { toast } from "sonner";
import { Topbar } from "@/components/Topbar";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Conversation = components["schemas"]["ConversationResponse"];
type ConversationList = components["schemas"]["ConversationListResponse"];
type Message = components["schemas"]["MessageResponse"];
type MessageList = components["schemas"]["MessageListResponse"];
type LLMCall = components["schemas"]["LLMCallResponse"];
type LLMCallList = components["schemas"]["LLMCallListResponse"];
type CampaignList = components["schemas"]["CampaignListResponse"];
type Campaign = components["schemas"]["CampaignResponse"];
type SenderList = components["schemas"]["SenderListResponse"];
type Sender = components["schemas"]["SenderResponse"];
type AgentList = components["schemas"]["AgentListResponse"];
type Agent = components["schemas"]["AgentResponse"];

export const Route = createFileRoute("/_authenticated/inbox")({
  component: InboxPage,
});

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

const STATUS_FILTERS = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "lead", label: "Leads" },
  { id: "handoff", label: "Handoff" },
  { id: "no-reply", label: "No reply" },
  { id: "finished", label: "Finished" },
] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number]["id"];

function matchesStatus(c: Conversation, f: StatusFilter): boolean {
  if (f === "all") return true;
  const s = (c.status || "").toLowerCase();
  if (f === "active") {
    return !["finished", "handoff", "stopped", "closed"].includes(s);
  }
  if (f === "no-reply") {
    return s === "no-reply" || s === "no_reply" || s === "awaiting" || s === "pending";
  }
  return s === f;
}

function InboxPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [campaignFilter, setCampaignFilter] = useState<string>("all");
  const [showTrace, setShowTrace] = useState(true);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [pendingDelete, setPendingDelete] = useState<
    { kind: "single"; id: string; name: string } | { kind: "bulk" } | null
  >(null);
  const queryClient = useQueryClient();
  // Conversations viewed in this session — used to suppress the unread badge
  // since the backend has no mark-as-read endpoint in v1.
  const viewedRef = useRef<Set<string>>(new Set());

  const listQ = useQuery({
    queryKey: ["conversations", { search }],
    queryFn: () =>
      api<ConversationList>("/api/v1/conversations", {
        query: { limit: 100, ...(search ? { search } : {}) },
      }),
    refetchInterval: 10_000,
  });

  const campaignsQ = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<CampaignList>("/api/v1/campaigns"),
  });

  const allConversations = useMemo(
    () =>
      (listQ.data?.conversations ?? []).map((c) =>
        viewedRef.current.has(c.id) ? { ...c, unread_count: 0 } : c,
      ),
    [listQ.data],
  );
  const campaigns = campaignsQ.data?.items ?? [];

  const conversations = useMemo(
    () =>
      allConversations
        .filter((c) => campaignFilter === "all" || c.campaign_id === campaignFilter)
        .filter((c) => matchesStatus(c, statusFilter)),
    [allConversations, campaignFilter, statusFilter],
  );

  useEffect(() => {
    if (!selectedId && conversations.length > 0) {
      setSelectedId(conversations[0].id);
    }
  }, [conversations, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    viewedRef.current.add(selectedId);
    queryClient.setQueriesData<ConversationList>({ queryKey: ["conversations"] }, (old) => {
      if (!old) return old;
      return {
        ...old,
        conversations: old.conversations.map((c) =>
          c.id === selectedId ? { ...c, unread_count: 0 } : c,
        ),
      };
    });
  }, [selectedId, queryClient]);

  // ── Deletion (single + bulk) ──────────────────────────────────────────────
  const deleteOneMut = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/conversations/${id}`, { method: "DELETE" }),
    onSuccess: (_data, id) => {
      toast.success("Conversation deleted");
      if (selectedId === id) setSelectedId(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });

  const deleteBulkMut = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ deleted: number }>("/api/v1/conversations/delete", {
        method: "POST",
        body: { conversation_ids: ids },
      }),
    onSuccess: (res, ids) => {
      toast.success(`Deleted ${res.deleted} chat${res.deleted === 1 ? "" : "s"}`);
      if (selectedId && ids.includes(selectedId)) setSelectedId(null);
      setSelectedIds(new Set());
      setSelectionMode(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });

  const toggleSelect = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allVisibleSelected =
    conversations.length > 0 && conversations.every((c) => selectedIds.has(c.id));

  const toggleSelectAll = () =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) conversations.forEach((c) => next.delete(c.id));
      else conversations.forEach((c) => next.add(c.id));
      return next;
    });

  const exitSelectionMode = () => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    if (pendingDelete.kind === "single") deleteOneMut.mutate(pendingDelete.id);
    else deleteBulkMut.mutate([...selectedIds]);
    setPendingDelete(null);
  };

  return (
    <>
      <Topbar
        title="Inbox"
        right={
          <>
            <button className="btn btn--ghost btn--sm" type="button">
              <Filter size={14} /> Saved views
            </button>
            <button
              className="btn btn--ghost btn--sm"
              type="button"
              onClick={() => setShowTrace((v) => !v)}
            >
              <Brain size={14} /> {showTrace ? "Hide" : "Show"} LLM trace
            </button>
            <button className="btn btn--ghost btn--sm" type="button">
              <Download size={14} /> Export
            </button>
          </>
        }
      />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: showTrace ? "340px 1fr 360px" : "340px 1fr",
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
          background: "var(--bg-soft)",
        }}
      >
        <ConvList
          loading={listQ.isLoading}
          error={listQ.error ? errMsg(listQ.error) : null}
          items={conversations}
          totalCount={allConversations.length}
          campaigns={campaigns}
          activeId={selectedId}
          onSelect={setSelectedId}
          search={search}
          onSearch={setSearch}
          statusFilter={statusFilter}
          onStatusFilter={setStatusFilter}
          campaignFilter={campaignFilter}
          onCampaignFilter={setCampaignFilter}
          selectionMode={selectionMode}
          selectedIds={selectedIds}
          allVisibleSelected={allVisibleSelected}
          bulkPending={deleteBulkMut.isPending}
          onToggleSelectionMode={() =>
            selectionMode ? exitSelectionMode() : setSelectionMode(true)
          }
          onToggleSelect={toggleSelect}
          onToggleSelectAll={toggleSelectAll}
          onRequestDeleteOne={(id, name) =>
            setPendingDelete({ kind: "single", id, name })
          }
          onRequestDeleteBulk={() => setPendingDelete({ kind: "bulk" })}
        />
        {selectedId ? (
          <Thread
            conversationId={selectedId}
            campaigns={campaigns}
            llmShown={showTrace}
            onToggleLlm={() => setShowTrace((v) => !v)}
          />
        ) : (
          <EmptyMid />
        )}
        {showTrace && selectedId && <TracePane conversationId={selectedId} />}
      </div>

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDelete?.kind === "bulk"
                ? `Delete ${selectedIds.size} chat${selectedIds.size === 1 ? "" : "s"}?`
                : "Delete this chat?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.kind === "single" && pendingDelete.name
                ? `“${pendingDelete.name}” and its full message history will be permanently removed from your inbox. This cannot be undone.`
                : "The selected conversations and their full message history will be permanently removed from your inbox. This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              style={{ background: "var(--danger)", color: "#fff" }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/* ---------------- LEFT: conversation list ---------------- */

function Avatar({ name, size = 36 }: { name: string; size?: number }) {
  const initials = (name || "?")
    .split(/\s+/)
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
  // Stable pseudo-random hue from name
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: `hsl(${hue}, 65%, 88%)`,
        color: `hsl(${hue}, 50%, 32%)`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size <= 28 ? 11 : 13,
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      {initials || "?"}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    lead: { bg: "var(--success-soft)", fg: "#1e8a3a", label: "Lead" },
    handoff: {
      bg: "color-mix(in oklab, var(--ai-purple, #8774e1) 16%, transparent)",
      fg: "var(--ai-purple, #8774e1)",
      label: "Handoff",
    },
    finished: { bg: "var(--bg-soft)", fg: "var(--text-muted)", label: "Finished" },
    "no-reply": { bg: "var(--bg-soft)", fg: "var(--text-muted)", label: "No reply" },
    no_reply: { bg: "var(--bg-soft)", fg: "var(--text-muted)", label: "No reply" },
    active: { bg: "var(--tg-blue-soft, #e8f1fc)", fg: "var(--tg-blue, #3390ec)", label: "Active" },
  };
  const entry = map[s] || {
    bg: "var(--bg-soft)",
    fg: "var(--text-muted)",
    label: status || "—",
  };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "1px 8px",
        fontSize: 10.5,
        fontWeight: 600,
        borderRadius: 999,
        background: entry.bg,
        color: entry.fg,
        textTransform: "capitalize",
      }}
    >
      {entry.label}
    </span>
  );
}

function ConvList({
  loading,
  error,
  items,
  totalCount,
  campaigns,
  activeId,
  onSelect,
  search,
  onSearch,
  statusFilter,
  onStatusFilter,
  campaignFilter,
  onCampaignFilter,
  selectionMode,
  selectedIds,
  allVisibleSelected,
  bulkPending,
  onToggleSelectionMode,
  onToggleSelect,
  onToggleSelectAll,
  onRequestDeleteOne,
  onRequestDeleteBulk,
}: {
  loading: boolean;
  error: string | null;
  items: Conversation[];
  totalCount: number;
  campaigns: Campaign[];
  activeId: string | null;
  onSelect: (id: string) => void;
  search: string;
  onSearch: (s: string) => void;
  statusFilter: StatusFilter;
  onStatusFilter: (s: StatusFilter) => void;
  campaignFilter: string;
  onCampaignFilter: (c: string) => void;
  selectionMode: boolean;
  selectedIds: Set<string>;
  allVisibleSelected: boolean;
  bulkPending: boolean;
  onToggleSelectionMode: () => void;
  onToggleSelect: (id: string) => void;
  onToggleSelectAll: () => void;
  onRequestDeleteOne: (id: string, name: string) => void;
  onRequestDeleteBulk: () => void;
}) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  return (
    <aside
      style={{
        borderRight: "1px solid var(--border)",
        background: "var(--bg)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      {/* Search */}
      <div style={{ padding: "14px 14px 8px" }}>
        <div style={{ position: "relative" }}>
          <Search
            size={14}
            style={{
              position: "absolute",
              left: 11,
              top: 11,
              color: "var(--text-faint)",
              pointerEvents: "none",
            }}
          />
          <input
            className="input"
            style={{ paddingLeft: 32, height: 36, fontSize: 13 }}
            placeholder="Search conversations"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Campaign filter */}
      <div style={{ padding: "4px 14px 10px" }}>
        <div style={{ position: "relative" }}>
          <select
            value={campaignFilter}
            onChange={(e) => onCampaignFilter(e.target.value)}
            style={{
              width: "100%",
              height: 32,
              padding: "0 28px 0 10px",
              fontSize: 12,
              borderRadius: 7,
              border: "1px solid var(--border)",
              background: "var(--bg)",
              color: "var(--text)",
              appearance: "none",
              cursor: "pointer",
            }}
          >
            <option value="all">All campaigns</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <ChevronDown
            size={14}
            style={{
              position: "absolute",
              right: 8,
              top: 9,
              color: "var(--text-faint)",
              pointerEvents: "none",
            }}
          />
        </div>
      </div>

      {/* Status filter chips */}
      <div
        style={{
          padding: "0 8px 8px",
          display: "flex",
          gap: 4,
          overflowX: "auto",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {STATUS_FILTERS.map((f) => {
          const active = statusFilter === f.id;
          return (
            <button
              key={f.id}
              onClick={() => onStatusFilter(f.id)}
              type="button"
              style={{
                padding: "5px 10px",
                borderRadius: 7,
                fontSize: 12,
                fontWeight: 500,
                whiteSpace: "nowrap",
                border: "none",
                cursor: "pointer",
                background: active ? "var(--tg-blue-soft, #e8f1fc)" : "transparent",
                color: active ? "var(--tg-blue, #3390ec)" : "var(--text-muted)",
              }}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {/* Selection toolbar / bulk action bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          minHeight: 44,
          borderBottom: "1px solid var(--border)",
          background: selectionMode ? "var(--tg-blue-softer, #f3f8fe)" : "transparent",
        }}
      >
        {selectionMode ? (
          <>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12.5,
                fontWeight: 600,
                cursor: "pointer",
                userSelect: "none",
              }}
            >
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={onToggleSelectAll}
                disabled={items.length === 0}
              />
              {selectedIds.size > 0 ? `${selectedIds.size} selected` : "Select all"}
            </label>
            <span style={{ flex: 1 }} />
            <button
              type="button"
              className="btn btn--sm"
              disabled={selectedIds.size === 0 || bulkPending}
              onClick={onRequestDeleteBulk}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                background: "var(--danger)",
                color: "#fff",
                opacity: selectedIds.size === 0 || bulkPending ? 0.5 : 1,
                cursor: selectedIds.size === 0 || bulkPending ? "not-allowed" : "pointer",
              }}
            >
              <Trash2 size={13} />
              {bulkPending ? "Deleting…" : `Delete (${selectedIds.size})`}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onToggleSelectionMode}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <span style={{ flex: 1 }} />
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onToggleSelectionMode}
              disabled={items.length === 0}
              style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
            >
              <CheckSquare size={14} /> Select
            </button>
          </>
        )}
      </div>

      <div className="scroll" style={{ flex: 1 }}>
        {loading && <div className="muted" style={{ padding: 16 }}>Loading…</div>}
        {error && (
          <div style={{ padding: 16, color: "var(--danger)", fontSize: 13 }}>{error}</div>
        )}
        {!loading && !error && totalCount === 0 && (
          <div style={{ padding: 24, textAlign: "center" }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>💬</div>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
              No conversations
            </div>
            <p className="muted" style={{ fontSize: 12 }}>
              Launch a campaign to start outreach.
            </p>
          </div>
        )}
        {!loading && !error && totalCount > 0 && items.length === 0 && (
          <div className="muted" style={{ padding: 24, textAlign: "center", fontSize: 13 }}>
            No conversations match these filters.
          </div>
        )}
        {items.map((c) => {
          const active = c.id === activeId;
          const name = c.contact_name || c.contact_phone;
          const selected = selectedIds.has(c.id);
          const showDelete = !selectionMode && hoveredId === c.id;
          return (
            <div
              key={c.id}
              onClick={() => (selectionMode ? onToggleSelect(c.id) : onSelect(c.id))}
              onMouseEnter={() => setHoveredId(c.id)}
              onMouseLeave={() => setHoveredId((h) => (h === c.id ? null : h))}
              style={{
                position: "relative",
                padding: "12px 14px",
                display: "flex",
                gap: 10,
                cursor: "pointer",
                background: selected
                  ? "var(--tg-blue-soft, #e8f1fc)"
                  : active
                    ? "var(--tg-blue-softer, #f3f8fe)"
                    : "transparent",
                borderLeft: `3px solid ${active ? "var(--tg-blue, #3390ec)" : "transparent"}`,
                borderBottom: "1px solid var(--border)",
              }}
            >
              {selectionMode && (
                <input
                  type="checkbox"
                  checked={selected}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    e.stopPropagation();
                    onToggleSelect(c.id);
                  }}
                  style={{ flexShrink: 0, alignSelf: "center" }}
                />
              )}
              {showDelete && (
                <button
                  type="button"
                  aria-label="Delete conversation"
                  title="Delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRequestDeleteOne(c.id, name);
                  }}
                  style={{
                    position: "absolute",
                    top: 8,
                    right: 8,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 26,
                    height: 26,
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "var(--bg)",
                    color: "var(--danger)",
                    cursor: "pointer",
                    boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
                  }}
                >
                  <Trash2 size={14} />
                </button>
              )}
              <Avatar name={name} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 2,
                  }}
                >
                  <span
                    style={{
                      fontSize: 13.5,
                      fontWeight: c.unread_count > 0 ? 600 : 500,
                      flex: 1,
                      minWidth: 0,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {name}
                  </span>
                  <span
                    className="muted"
                    style={{ fontSize: 11, flexShrink: 0 }}
                  >
                    {c.last_message_at
                      ? new Date(c.last_message_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : ""}
                  </span>
                </div>
                {c.sender_slug && (
                  <div
                    style={{
                      fontSize: 11.5,
                      color: "var(--text-muted)",
                      marginBottom: 4,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    via @{c.sender_slug}
                  </div>
                )}
                <div
                  style={{
                    fontSize: 12,
                    color: c.unread_count > 0 ? "var(--text)" : "var(--text-muted)",
                    lineHeight: 1.4,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {c.last_message ?? "—"}
                </div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginTop: 6,
                  }}
                >
                  <StatusPill status={c.status} />
                  {!c.ai_enabled && (
                    <span
                      title="Human takeover"
                      style={{
                        fontSize: 10,
                        padding: "1px 6px",
                        borderRadius: 4,
                        background: "var(--warning-soft, #fff4d6)",
                        color: "#8a6a00",
                        fontWeight: 600,
                      }}
                    >
                      Human
                    </span>
                  )}
                  <span style={{ flex: 1 }} />
                  {c.unread_count > 0 && (
                    <span
                      style={{
                        background: "var(--tg-blue, #3390ec)",
                        color: "white",
                        fontSize: 10,
                        padding: "1px 6px",
                        borderRadius: 999,
                        fontWeight: 600,
                      }}
                    >
                      {c.unread_count}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function EmptyMid() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-soft)",
        fontSize: 14,
      }}
    >
      Select a conversation to view messages.
    </div>
  );
}

/* ---------------- MIDDLE: thread ---------------- */

function KV({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
      <span style={{ color: "var(--text-faint)" }}>{icon}</span>
      <div style={{ minWidth: 0 }}>
        <div
          className="muted"
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: 12,
            fontWeight: 500,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 140,
          }}
        >
          {value}
        </div>
      </div>
    </div>
  );
}

function Thread({
  conversationId,
  campaigns,
  llmShown,
  onToggleLlm,
}: {
  conversationId: string;
  campaigns: Campaign[];
  llmShown: boolean;
  onToggleLlm: () => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const convQ = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api<Conversation>(`/api/v1/conversations/${conversationId}`),
    refetchInterval: 15_000,
  });

  const messagesQ = useQuery({
    queryKey: ["messages", conversationId],
    queryFn: () =>
      api<MessageList>(`/api/v1/conversations/${conversationId}/messages`, {
        query: { limit: 200 },
      }),
    refetchInterval: 10_000,
  });

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messagesQ.data]);

  const disableAiMut = useMutation({
    mutationFn: () =>
      api<Conversation>(`/api/v1/conversations/${conversationId}/disable-ai`, {
        method: "POST",
      }),
    onSuccess: () => {
      track("conversation_taken_over_by_human", { conversation_id: conversationId });
      void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const enableAiMut = useMutation({
    mutationFn: () =>
      api<Conversation>(`/api/v1/conversations/${conversationId}/enable-ai`, {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const sendMut = useMutation({
    mutationFn: (text: string) =>
      api(`/api/v1/conversations/${conversationId}/send`, {
        method: "POST",
        body: { message_text: text },
      }),
    onSuccess: () => {
      setDraft("");
      setSendError(null);
      void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (e) => setSendError(errMsg(e)),
  });

  const conv = convQ.data;
  const messages = messagesQ.data?.messages ?? [];
  const campaign = campaigns.find((c) => c.id === conv?.campaign_id);
  const name = conv?.contact_name || conv?.contact_phone || "—";

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        minWidth: 0,
        background: "var(--bg-soft)",
      }}
    >
      {/* Header */}
      <header
        style={{
          padding: "12px 20px",
          background: "var(--bg)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <Avatar name={name} size={42} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14.5, fontWeight: 600 }}>{name}</span>
            {conv?.contact_phone && conv.contact_phone !== name && (
              <span className="muted" style={{ fontSize: 12 }}>
                · {conv.contact_phone}
              </span>
            )}
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            last active{" "}
            {conv?.last_message_at
              ? new Date(conv.last_message_at).toLocaleString()
              : "—"}
          </div>
        </div>

        {conv && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              marginRight: 10,
              paddingRight: 14,
              borderRight: "1px solid var(--border)",
            }}
          >
            {conv.sender_slug && (
              <KV
                label="Sender"
                value={`@${conv.sender_slug}`}
                icon={<Send size={13} />}
              />
            )}
            {campaign && (
              <KV
                label="Campaign"
                value={campaign.name}
                icon={<Flag size={13} />}
              />
            )}
          </div>
        )}

        {conv &&
          (conv.ai_enabled ? (
            <button
              type="button"
              className="btn btn--sm"
              style={{ background: "var(--warning-soft, #fff4d6)", color: "#a86200" }}
              onClick={() => disableAiMut.mutate()}
              disabled={disableAiMut.isPending}
            >
              <UserIcon size={14} /> Take over
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={() => enableAiMut.mutate()}
              disabled={enableAiMut.isPending}
            >
              Hand back to AI
            </button>
          ))}
        {!llmShown && (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onToggleLlm}
            aria-label="Show LLM trace"
          >
            <Brain size={14} />
          </button>
        )}
      </header>

      {/* Lead banner */}
      {conv?.status === "lead" && (
        <div
          style={{
            padding: "10px 20px",
            background:
              "linear-gradient(90deg, var(--success-soft, #e6f7ec), var(--tg-blue-softer, #f3f8fe))",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: 7,
              background: "var(--success, #4dcd5e)",
              color: "white",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Flag size={13} />
          </div>
          <div style={{ fontSize: 12.5 }}>
            <b>Lead detected</b> in this conversation
          </div>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn--sm"
            style={{ background: "var(--success, #4dcd5e)", color: "white" }}
          >
            <Check size={12} /> Confirm
          </button>
          <button type="button" className="btn btn--sm btn--ghost">
            <X size={12} /> Dismiss
          </button>
        </div>
      )}

      {/* Messages */}
      <div
        ref={scrollRef}
        className="scroll"
        style={{ flex: 1, padding: "20px 24px" }}
      >
        {messagesQ.isLoading && <div className="muted">Loading messages…</div>}
        {messagesQ.error && (
          <div style={{ color: "var(--danger)" }}>{errMsg(messagesQ.error)}</div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} m={m} />
        ))}
      </div>

      {/* Composer */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const text = draft.trim();
          if (!text) return;
          sendMut.mutate(text);
        }}
        style={{
          padding: 16,
          borderTop: "1px solid var(--border)",
          background: "var(--bg)",
        }}
      >
        {sendError && (
          <div
            style={{
              color: "var(--danger)",
              fontSize: 12,
              marginBottom: 6,
            }}
          >
            {sendError}
          </div>
        )}
        <div
          style={{
            padding: 12,
            border: "1px solid var(--border)",
            borderRadius: 12,
            background: "var(--bg)",
          }}
        >
          <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <SuggestionChip
              icon={<Sparkles size={12} />}
              color="var(--tg-blue, #3390ec)"
              onClick={() => setDraft("Would you like to book a quick call?")}
            >
              Suggest meeting
            </SuggestionChip>
            <SuggestionChip
              icon={<MessageCircle size={12} />}
              color="var(--ai-purple, #8774e1)"
              onClick={() => setDraft("What's your decision timeline?")}
            >
              Ask about timeline
            </SuggestionChip>
            <SuggestionChip
              icon={<Check size={12} />}
              color="var(--success, #4dcd5e)"
              onClick={() => setDraft("Confirming our pricing — does this work for you?")}
            >
              Confirm pricing
            </SuggestionChip>
          </div>
          <textarea
            className="textarea"
            placeholder={
              conv?.ai_enabled
                ? "Disable AI to send manually…"
                : "Type a message, or click a suggestion above"
            }
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={!conv || conv.ai_enabled || sendMut.isPending}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                const text = draft.trim();
                if (text) sendMut.mutate(text);
              }
            }}
            style={{
              border: "none",
              padding: 0,
              minHeight: 50,
              resize: "none",
              width: "100%",
              background: "transparent",
              fontSize: 13.5,
              outline: "none",
            }}
          />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              paddingTop: 8,
              borderTop: "1px solid var(--border)",
            }}
          >
            <button
              type="button"
              className="tb__icon-btn"
              style={{ width: 32, height: 32 }}
              aria-label="Attach file"
            >
              <Paperclip size={16} />
            </button>
            <button
              type="button"
              className="tb__icon-btn"
              style={{ width: 32, height: 32 }}
              aria-label="Add emoji"
            >
              <Smile size={16} />
            </button>
            <span style={{ flex: 1 }} />
            <span className="muted" style={{ fontSize: 11 }}>
              {conv?.sender_slug && <>Sending via <b>@{conv.sender_slug}</b></>} · ⌘+Enter
            </span>
            <button
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={!conv || conv.ai_enabled || !draft.trim() || sendMut.isPending}
            >
              <Send size={12} /> {sendMut.isPending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}

function SuggestionChip({
  children,
  icon,
  color,
  onClick,
}: {
  children: React.ReactNode;
  icon: React.ReactNode;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        height: 28,
        padding: "0 10px",
        borderRadius: 8,
        background: `color-mix(in oklab, ${color} 12%, transparent)`,
        color,
        fontSize: 12,
        fontWeight: 500,
        border: "none",
        cursor: "pointer",
      }}
    >
      {icon}
      {children}
    </button>
  );
}

function MessageBubble({ m }: { m: Message }) {
  const isOutbound = m.direction === "outbound";
  const isAI = m.sent_by === "ai" || m.sent_by === "bot";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: isOutbound ? "flex-end" : "flex-start",
        marginBottom: 14,
      }}
    >
      <div style={{ maxWidth: "70%" }}>
        {!isOutbound && (
          <div
            className="muted"
            style={{ fontSize: 11, marginBottom: 4, paddingLeft: 14 }}
          >
            {new Date(m.created_at).toLocaleString()}
          </div>
        )}
        <div
          style={{
            padding: "10px 14px",
            borderRadius: isOutbound ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
            background: isOutbound
              ? isAI
                ? "color-mix(in oklab, var(--ai-purple, #8774e1) 14%, white)"
                : "var(--tg-blue, #3390ec)"
              : "white",
            color: isOutbound && !isAI ? "white" : "var(--text)",
            fontSize: 13.5,
            lineHeight: 1.5,
            boxShadow: isOutbound
              ? "none"
              : "0 1px 1px rgba(15,20,25,0.04), 0 0 0 1px rgba(15,20,25,0.04)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {m.message_text}
        </div>
        {isOutbound && (
          <div
            className="muted"
            style={{
              fontSize: 11,
              marginTop: 4,
              paddingRight: 14,
              textAlign: "right",
              display: "flex",
              alignItems: "center",
              gap: 4,
              justifyContent: "flex-end",
            }}
          >
            {new Date(m.created_at).toLocaleString()}
            {isAI && <span>· 🤖</span>}
            <Check size={11} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- RIGHT: thought trace ---------------- */

function TracePane({ conversationId }: { conversationId: string }) {
  const tracesQ = useQuery({
    queryKey: ["llm-calls", conversationId],
    queryFn: () =>
      api<LLMCallList>(`/api/v1/conversations/${conversationId}/llm-calls`, {
        query: { limit: 50 },
      }),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    track("llm_trace_opened", { conversation_id: conversationId });
  }, [conversationId]);

  return (
    <aside
      style={{
        borderLeft: "1px solid var(--border)",
        background: "var(--bg)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      <header
        style={{
          padding: "14px 18px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            background:
              "linear-gradient(135deg, var(--ai-purple, #8774e1), var(--tg-blue, #3390ec))",
            color: "white",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Brain size={14} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>Thought trace</div>
          <div className="muted" style={{ fontSize: 11 }}>
            Why the agent said what it said
          </div>
        </div>
      </header>
      <div className="scroll" style={{ flex: 1, padding: "14px 16px" }}>
        {tracesQ.isLoading && <div className="muted">Loading…</div>}
        {tracesQ.error && (
          <div style={{ color: "var(--danger)", fontSize: 12 }}>
            {errMsg(tracesQ.error)}
          </div>
        )}
        {tracesQ.data && tracesQ.data.llm_calls.length === 0 && (
          <div className="muted" style={{ fontSize: 12 }}>
            No AI calls yet.
          </div>
        )}
        {tracesQ.data?.llm_calls.map((call, i) => (
          <TraceCard key={call.id} call={call} latest={i === 0} />
        ))}
      </div>
    </aside>
  );
}

function TraceCard({ call, latest }: { call: LLMCall; latest: boolean }) {
  const [open, setOpen] = useState(latest);
  return (
    <div
      style={{
        marginBottom: 12,
        borderRadius: 12,
        border: "1px solid var(--border)",
        overflow: "hidden",
        background: latest
          ? "linear-gradient(180deg, color-mix(in oklab, var(--ai-purple, #8774e1) 14%, white) 0%, white 30%)"
          : "white",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "11px 12px",
          textAlign: "left",
          border: "none",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        <div
          style={{
            width: 22,
            height: 22,
            borderRadius: 6,
            background: "color-mix(in oklab, var(--ai-purple, #8774e1) 16%, transparent)",
            color: "var(--ai-purple, #8774e1)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Sparkles size={11} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 500,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {call.model}
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            {new Date(call.created_at).toLocaleTimeString()} ·{" "}
            {call.latency_ms != null ? `${call.latency_ms}ms` : "—"} ·{" "}
            {call.total_tokens ?? 0} tok
          </div>
        </div>
        <ChevronDown
          size={14}
          style={{
            color: "var(--text-muted)",
            transform: open ? "rotate(0deg)" : "rotate(-90deg)",
            transition: "transform 0.15s",
          }}
        />
      </button>
      {open && (
        <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
          {call.response_text && (
            <div>
              <div
                className="muted"
                style={{
                  fontSize: 10,
                  marginBottom: 6,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  fontWeight: 600,
                }}
              >
                Agent response
              </div>
              <div
                style={{
                  padding: "10px 12px",
                  borderRadius: 8,
                  fontSize: 12,
                  lineHeight: 1.5,
                  background: "var(--tg-blue-softer, #f3f8fe)",
                  borderLeft: "3px solid var(--tg-blue, #3390ec)",
                  whiteSpace: "pre-wrap",
                }}
              >
                {call.response_text}
              </div>
            </div>
          )}
          {call.error && (
            <div
              style={{
                fontSize: 11,
                color: "var(--danger)",
                padding: "6px 10px",
                background: "var(--danger-soft)",
                borderRadius: 6,
              }}
            >
              {call.error}
            </div>
          )}
          {call.tool_calls != null && (
            <details>
              <summary
                style={{
                  fontSize: 10,
                  color: "var(--text-soft)",
                  cursor: "pointer",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  fontWeight: 600,
                }}
              >
                Tool calls
              </summary>
              <pre
                style={{
                  fontSize: 10,
                  background: "var(--bg-soft)",
                  padding: 8,
                  borderRadius: 4,
                  marginTop: 4,
                  overflow: "auto",
                  maxHeight: 200,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {JSON.stringify(call.tool_calls, null, 2)}
              </pre>
            </details>
          )}
          <details>
            <summary
              style={{
                fontSize: 10,
                color: "var(--text-soft)",
                cursor: "pointer",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                fontWeight: 600,
              }}
            >
              Prompt
            </summary>
            <pre
              style={{
                fontSize: 10,
                maxHeight: 200,
                overflow: "auto",
                background: "var(--bg-soft)",
                padding: 8,
                borderRadius: 4,
                marginTop: 4,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {JSON.stringify(call.prompt, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}
