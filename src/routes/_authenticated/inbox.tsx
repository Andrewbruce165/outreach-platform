import { createFileRoute } from "@tanstack/react-router";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  Pencil,
  Play,
  Mic,
  Image as ImageIcon,
  FileText,
  Loader2,
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
import { api, ApiError, apiBaseUrl } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { errorMessageFromEnvelope } from "@/lib/error-codes";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Conversation = components["schemas"]["ConversationResponse"];
type ConversationList = components["schemas"]["ConversationListResponse"];
// Phase 23 extends MessageResponse with typed messages + edit marker + file meta.
// The generated schema hasn't caught up yet; extend locally without touching
// the codegen output.
type BaseMessage = components["schemas"]["MessageResponse"];
type Message = Omit<BaseMessage, "message_text"> & {
  message_text?: string | null;
  message_type?: "text" | "photo" | "video" | "voice" | "document" | string | null;
  edited_at?: string | null;
  file_name?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
};
type MessageList = Omit<components["schemas"]["MessageListResponse"], "messages"> & {
  messages: Message[];
};
// Phase 23 send-file response — not yet in the generated schema either.
interface SendFileFromUIResponse {
  success: boolean;
  message_id?: string | null;
  telegram_message_id?: number | null;
  message_type?: string | null;
  error?: string | null;
}
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
  { id: "telegram", label: "Telegram" },
] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number]["id"];

function matchesStatus(c: Conversation, f: StatusFilter): boolean {
  if (f === "all") return true;
  if (f === "telegram") return true; // fetched via server-side status param
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

  const isTelegramTab = statusFilter === "telegram";

  const listQ = useQuery({
    queryKey: ["conversations", { search, status: isTelegramTab ? "telegram_service" : null }],
    queryFn: () =>
      api<ConversationList>("/api/v1/conversations", {
        query: {
          limit: 100,
          ...(search ? { search } : {}),
          ...(isTelegramTab ? { status: "telegram_service" } : {}),
        },
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
        .filter((c) => isTelegramTab || campaignFilter === "all" || c.campaign_id === campaignFilter)
        .filter((c) => matchesStatus(c, statusFilter)),
    [allConversations, campaignFilter, statusFilter, isTelegramTab],
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
        {showTrace && selectedId && (
          <RightPane conversationId={selectedId} campaigns={campaigns} />
        )}
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
    telegram_service: { bg: "var(--tg-blue-soft, #e8f1fc)", fg: "var(--tg-blue, #3390ec)", label: "Telegram" },
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
            value={statusFilter === "telegram" ? "all" : campaignFilter}
            onChange={(e) => onCampaignFilter(e.target.value)}
            disabled={statusFilter === "telegram"}
            title={statusFilter === "telegram" ? "Not applicable to Telegram service messages" : undefined}
            style={{
              width: "100%",
              height: 32,
              padding: "0 28px 0 10px",
              fontSize: 12,
              borderRadius: 7,
              border: "1px solid var(--border)",
              background: "var(--bg)",
              color: statusFilter === "telegram" ? "var(--text-faint)" : "var(--text)",
              appearance: "none",
              cursor: statusFilter === "telegram" ? "not-allowed" : "pointer",
              opacity: statusFilter === "telegram" ? 0.6 : 1,
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
            maxWidth: 180,
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

  // ── Phase 23: message edit / delete-for-everyone / send-file ─────────────
  const [pendingMsgDelete, setPendingMsgDelete] = useState<Message | null>(null);
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // message_id -> object URL for already-viewed/sent photo/video bytes (23-UI-SPEC
  // Surface 4 / C2). Populated either by a manager tapping to view an inbound
  // bubble, or immediately on a successful send-file (from the local File the
  // manager just picked, zero network round-trip). Never re-fetched once cached;
  // revoked on unmount only, not on scroll.
  const mediaBlobUrlsRef = useRef<Map<string, string>>(new Map());
  const [, forceMediaCacheTick] = useState(0);
  const registerMediaBlobUrl = (messageId: string, url: string) => {
    const prev = mediaBlobUrlsRef.current.get(messageId);
    if (prev) URL.revokeObjectURL(prev);
    mediaBlobUrlsRef.current.set(messageId, url);
    forceMediaCacheTick((n) => n + 1);
  };
  useEffect(() => {
    const cache = mediaBlobUrlsRef.current;
    return () => {
      cache.forEach((url) => URL.revokeObjectURL(url));
      cache.clear();
    };
  }, []);

  const editMut = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      api(`/api/v1/conversations/${conversationId}/messages/${id}`, {
        method: "PATCH",
        body: { message: text },
      }),
    onMutate: async ({ id, text }) => {
      await qc.cancelQueries({ queryKey: ["messages", conversationId] });
      const prev = qc.getQueryData<MessageList>(["messages", conversationId]);
      const stamp = new Date().toISOString();
      qc.setQueryData<MessageList>(["messages", conversationId], (old) =>
        old
          ? {
              ...old,
              messages: old.messages.map((m) =>
                m.id === id ? { ...m, message_text: text, edited_at: stamp } : m,
              ),
            }
          : old,
      );
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["messages", conversationId], ctx.prev);
      toast.error(errMsg(e));
    },
    onSuccess: () => {
      setEditingId(null);
      void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const deleteMsgMut = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/conversations/${conversationId}/messages/${id}`, {
        method: "DELETE",
      }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["messages", conversationId] });
      const prev = qc.getQueryData<MessageList>(["messages", conversationId]);
      qc.setQueryData<MessageList>(["messages", conversationId], (old) =>
        old ? { ...old, messages: old.messages.filter((m) => m.id !== id) } : old,
      );
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["messages", conversationId], ctx.prev);
      toast.error(errMsg(e));
    },
    onSuccess: () => {
      toast.success("Message deleted");
      void qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const sendFileMut = useMutation({
    mutationFn: ({ file, caption }: { file: File; caption: string }) => {
      const fd = new FormData();
      fd.append("file", file);
      if (caption) fd.append("caption", caption);
      return api<SendFileFromUIResponse>(
        `/api/v1/conversations/${conversationId}/send-file`,
        { method: "POST", body: fd },
      );
    },
    onSuccess: (data, { file }) => {
      // The manager already has these bytes locally — show the real image/video
      // immediately, no round-trip to re-fetch what was just uploaded.
      if (data.message_id && (data.message_type === "photo" || data.message_type === "video")) {
        registerMediaBlobUrl(data.message_id, URL.createObjectURL(file));
      }
      setStagedFile(null);
      setDraft("");
      setSendError(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
    },
    onError: (e) => setSendError(errMsg(e)),
  });

  const onPickFile = (f: File | null) => {
    if (!f) return;
    if (f.size > 50 * 1024 * 1024) {
      setSendError("File is larger than 50 MB.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setSendError(null);
    setStagedFile(f);
  };


  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<SenderList>("/api/v1/senders"),
    staleTime: 60_000,
  });

  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<AgentList>("/api/v1/agents"),
    staleTime: 60_000,
  });

  const conv = convQ.data;
  const messages = messagesQ.data?.messages ?? [];
  const campaign = campaigns.find((c) => c.id === conv?.campaign_id);
  const senders: Sender[] = sendersQ.data?.senders ?? [];
  const agents: Agent[] = agentsQ.data?.agents ?? [];
  const sender = senders.find((s) => s.slug === conv?.sender_slug);
  const agent = campaign?.agent_id ? agents.find((a) => a.id === campaign.agent_id) : undefined;
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
      {/* Header — Sender · Agent · Campaign */}
      <header
        style={{
          padding: "12px 20px",
          background: "var(--bg)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 18,
          minHeight: 60,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18, flex: 1, minWidth: 0 }}>
          <KV
            label="Recipient"
            value={
              conv?.contact_name && conv?.contact_phone
                ? `${conv.contact_name} · ${conv.contact_phone}`
                : conv?.contact_name || conv?.contact_phone || name
            }
            icon={<UserIcon size={13} />}
          />
        </div>

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
            aria-label="Show details"
          >
            <Brain size={14} />
          </button>
        )}
      </header>
      {/* keep name available for downstream refs */}
      {false && <span>{name}</span>}

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
          <MessageBubble
            key={m.id}
            m={m}
            conversationId={conversationId}
            contactName={name}
            editingId={editingId}
            setEditingId={setEditingId}
            editPendingId={editMut.isPending ? editMut.variables?.id ?? null : null}
            onEdit={(id, text) => editMut.mutateAsync({ id, text })}
            onRequestDelete={(msg) => setPendingMsgDelete(msg)}
            mediaUrl={mediaBlobUrlsRef.current.get(m.id)}
            onMediaLoaded={registerMediaBlobUrl}
          />
        ))}
      </div>

      {/* Composer */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (sendFileMut.isPending || sendMut.isPending) return;
          if (stagedFile) {
            sendFileMut.mutate({ file: stagedFile, caption: draft.trim() });
            return;
          }
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
          {stagedFile && (
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 10px",
                marginBottom: 10,
                border: "1px solid var(--border)",
                borderRadius: 8,
                background: "var(--bg-soft)",
                fontSize: 12,
                maxWidth: "100%",
              }}
            >
              <FileText size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 320,
                }}
              >
                {stagedFile.name}
              </span>
              <span className="muted" style={{ fontSize: 11 }}>
                · {formatBytes(stagedFile.size)}
              </span>
              <button
                type="button"
                aria-label="Remove attachment"
                onClick={() => {
                  setStagedFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = "";
                }}
                disabled={sendFileMut.isPending}
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--text-muted)",
                  cursor: "pointer",
                  padding: 2,
                  display: "inline-flex",
                }}
              >
                <X size={12} />
              </button>
            </div>
          )}
          <textarea
            className="textarea"
            placeholder={
              conv?.ai_enabled
                ? "Disable AI to send manually…"
                : stagedFile
                  ? "Add a caption (optional)…"
                  : "Type a message, or click a suggestion above"
            }
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={!conv || conv.ai_enabled || sendMut.isPending || sendFileMut.isPending}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                if (stagedFile) {
                  sendFileMut.mutate({ file: stagedFile, caption: draft.trim() });
                  return;
                }
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
            <input
              ref={fileInputRef}
              type="file"
              style={{ display: "none" }}
              onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className="tb__icon-btn"
              style={{ width: 32, height: 32 }}
              aria-label="Attach file"
              disabled={!conv || conv.ai_enabled || sendMut.isPending || sendFileMut.isPending}
              onClick={() => fileInputRef.current?.click()}
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
              disabled={
                !conv ||
                conv.ai_enabled ||
                sendMut.isPending ||
                sendFileMut.isPending ||
                (!stagedFile && !draft.trim())
              }
            >
              <Send size={12} />{" "}
              {sendFileMut.isPending
                ? "Uploading…"
                : sendMut.isPending
                  ? "Sending…"
                  : stagedFile
                    ? "Send file"
                    : "Send"}
            </button>
          </div>
        </div>
      </form>

      <AlertDialog
        open={pendingMsgDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingMsgDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this message for everyone?</AlertDialogTitle>
            <AlertDialogDescription>
              This message will be permanently removed from your chat and from{" "}
              {conv?.contact_name || "the recipient"}'s Telegram. This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingMsgDelete) deleteMsgMut.mutate(pendingMsgDelete.id);
                setPendingMsgDelete(null);
              }}
              style={{ background: "var(--danger)", color: "#fff" }}
            >
              Delete for everyone
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Fetches a message's media bytes. `disposition=inline` is used for the
// photo/video tap-to-view path (D-16 / 23-UI-SPEC Surface 4) — same lazy
// endpoint, the result is just rendered instead of saved to disk.
async function fetchMessageMedia(
  conversationId: string,
  messageId: string,
  disposition: "inline" | "attachment" = "attachment",
): Promise<{ blob: Blob; filename: string }> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  const res = await fetch(
    `${apiBaseUrl}/api/v1/conversations/${conversationId}/messages/${messageId}/download?disposition=${disposition}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) {
    let code = "DOWNLOAD_FAILED";
    let detail: Record<string, unknown> = {};
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (j?.detail && typeof j.detail === "object" && "code" in j.detail) {
        code = String((j.detail as { code: unknown }).code);
        detail = j.detail as Record<string, unknown>;
      }
    } catch {
      // ignore
    }
    throw new ApiError(res.status, code, errorMessageFromEnvelope(code, detail), detail);
  }
  const cd = res.headers.get("content-disposition") || "";
  const match =
    cd.match(/filename\*=(?:UTF-8'')?([^;]+)/i) ||
    cd.match(/filename="?([^";]+)"?/i);
  const filename = match ? decodeURIComponent(match[1].replace(/"/g, "")) : "file";
  const blob = await res.blob();
  return { blob, filename };
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function downloadMessageFile(
  conversationId: string,
  messageId: string,
  fallbackName: string,
): Promise<void> {
  const { blob, filename } = await fetchMessageMedia(conversationId, messageId, "attachment");
  saveBlob(blob, filename === "file" ? fallbackName : filename);
}

interface MessageBubbleProps {
  m: Message;
  conversationId: string;
  contactName: string;
  editingId: string | null;
  setEditingId: (id: string | null) => void;
  editPendingId: string | null;
  onEdit: (id: string, text: string) => Promise<unknown>;
  onRequestDelete: (m: Message) => void;
  /** Cached object URL for this message's media, if already viewed/sent (23-UI-SPEC C2). */
  mediaUrl?: string;
  /** Registers a freshly-fetched object URL in the parent's shared cache. */
  onMediaLoaded: (messageId: string, url: string) => void;
}

function MessageBubble({
  m,
  conversationId,
  editingId,
  setEditingId,
  editPendingId,
  onEdit,
  onRequestDelete,
  mediaUrl,
  onMediaLoaded,
}: MessageBubbleProps) {
  const isOutbound = m.direction === "outbound";
  const isAI = m.sent_by === "ai" || m.sent_by === "bot";
  const type = (m.message_type ?? "text") as
    | "text"
    | "photo"
    | "video"
    | "voice"
    | "document"
    | string;
  const isTextType = type === "text";
  const isMediaType = type === "photo" || type === "video";
  const isFileType = isMediaType || type === "voice" || type === "document";
  const isEditing = editingId === m.id;
  const isEditPending = editPendingId === m.id;

  const [hovered, setHovered] = useState(false);
  const [editText, setEditText] = useState(m.message_text ?? "");
  const [downloading, setDownloading] = useState(false);
  const [loadingMedia, setLoadingMedia] = useState(false);
  const [mediaGone, setMediaGone] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  useEffect(() => {
    if (isEditing) setEditText(m.message_text ?? "");
  }, [isEditing, m.message_text]);

  useEffect(() => {
    if (!lightboxOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightboxOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxOpen]);

  const startEdit = () => {
    setEditText(m.message_text ?? "");
    setEditingId(m.id);
  };
  const cancelEdit = () => setEditingId(null);
  const saveEdit = () => {
    const t = editText.trim();
    if (!t) return;
    onEdit(m.id, t).catch(() => {
      /* rollback handled by mutation */
    });
  };

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadMessageFile(conversationId, m.id, m.file_name ?? "file");
    } catch (e) {
      if (e instanceof ApiError && e.code === "MEDIA_UNAVAILABLE") {
        setMediaGone(true);
      } else {
        toast.error(e instanceof Error ? e.message : "Download failed");
      }
    } finally {
      setDownloading(false);
    }
  };

  // Tap-to-view for photo/video (23-UI-SPEC Surface 4): fetches once, caches
  // the object URL in the parent so scrolling away and back never re-fetches.
  // No auto-fetch on mount/scroll-into-view — D-16 stays intact, only the
  // *result* of the tap changed (inline render, not a disk save).
  const handleViewMedia = async () => {
    if (loadingMedia || mediaUrl) return;
    setLoadingMedia(true);
    try {
      const { blob } = await fetchMessageMedia(conversationId, m.id, "inline");
      onMediaLoaded(m.id, URL.createObjectURL(blob));
    } catch (e) {
      if (e instanceof ApiError && e.code === "MEDIA_UNAVAILABLE") {
        setMediaGone(true);
      } else {
        toast.error(e instanceof Error ? e.message : "Couldn't load the file. Try again.");
      }
    } finally {
      setLoadingMedia(false);
    }
  };

  // Reuses the already-cached blob to save it to disk — no second network fetch.
  const handleDownloadCachedMedia = () => {
    if (!mediaUrl) return;
    fetch(mediaUrl)
      .then((r) => r.blob())
      .then((blob) => saveBlob(blob, m.file_name || "file"));
  };

  const bubbleBg = isOutbound
    ? isAI
      ? "color-mix(in oklab, var(--ai-purple, #8774e1) 14%, white)"
      : "var(--tg-blue, #3390ec)"
    : "white";
  const bubbleColor = isOutbound && !isAI ? "white" : "var(--text)";
  const captionColor =
    isOutbound && !isAI ? "rgba(255,255,255,0.85)" : "var(--text-muted)";

  const renderBody = () => {
    if (isEditing && isTextType) {
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 220 }}>
          <textarea
            autoFocus
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                cancelEdit();
              } else if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                saveEdit();
              }
            }}
            disabled={isEditPending}
            style={{
              width: "100%",
              minHeight: 60,
              resize: "vertical",
              border: "none",
              outline: "none",
              background: "transparent",
              color: bubbleColor,
              fontSize: 13.5,
              lineHeight: 1.5,
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={cancelEdit}
              disabled={isEditPending}
              style={
                isOutbound && !isAI
                  ? { color: "white", background: "rgba(255,255,255,0.15)" }
                  : undefined
              }
            >
              <X size={12} /> Cancel
            </button>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={saveEdit}
              disabled={isEditPending || !editText.trim()}
            >
              <Check size={12} /> {isEditPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      );
    }

    if (isMediaType) {
      const caption = m.message_text || null;
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {mediaGone ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "10px 14px",
                borderRadius: 12,
                background: "rgba(0,0,0,0.05)",
                color: "var(--text-muted)",
                fontSize: 12.5,
              }}
            >
              {type === "photo" ? <ImageIcon size={16} /> : <Play size={16} />}
              No longer available in Telegram
            </div>
          ) : mediaUrl ? (
            <div style={{ position: "relative", maxWidth: 360 }}>
              {type === "photo" ? (
                <img
                  src={mediaUrl}
                  alt={m.file_name || "Photo"}
                  onClick={() => setLightboxOpen(true)}
                  style={{
                    display: "block",
                    maxWidth: "100%",
                    maxHeight: 420,
                    borderRadius: 12,
                    cursor: "zoom-in",
                  }}
                />
              ) : (
                <video
                  src={mediaUrl}
                  controls
                  style={{ display: "block", maxWidth: "100%", maxHeight: 420, borderRadius: 12 }}
                />
              )}
              <button
                type="button"
                className="tb__icon-btn"
                onClick={handleDownloadCachedMedia}
                aria-label="Download"
                style={{
                  position: "absolute",
                  top: 6,
                  right: 6,
                  background: "rgba(0,0,0,0.45)",
                  color: "white",
                }}
              >
                <Download size={14} />
              </button>
              {type === "photo" && lightboxOpen && (
                <div
                  className="modal__scrim"
                  role="dialog"
                  aria-modal="true"
                  aria-label="Photo"
                  onClick={() => setLightboxOpen(false)}
                  style={{ zIndex: 200 }}
                >
                  <img
                    src={mediaUrl}
                    alt={m.file_name || "Photo"}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      display: "block",
                      maxWidth: "min(92vw, 1100px)",
                      maxHeight: "92vh",
                      borderRadius: 8,
                      boxShadow: "var(--shadow-lg)",
                      cursor: "default",
                    }}
                  />
                  <button
                    type="button"
                    className="tb__icon-btn"
                    aria-label="Close"
                    onClick={() => setLightboxOpen(false)}
                    style={{
                      position: "fixed",
                      top: 16,
                      right: 16,
                      background: "rgba(0,0,0,0.45)",
                      color: "white",
                    }}
                  >
                    <X size={18} />
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button
              type="button"
              onClick={handleViewMedia}
              disabled={loadingMedia}
              aria-label={type === "photo" ? "View photo" : "View video"}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                width: 260,
                height: 190,
                border: "none",
                borderRadius: 12,
                background: "rgba(0,0,0,0.06)",
                color: captionColor,
                cursor: loadingMedia ? "default" : "pointer",
              }}
            >
              {loadingMedia ? (
                <Loader2 size={20} className="ob__spin" />
              ) : (
                <>
                  {type === "photo" ? <ImageIcon size={28} /> : <Play size={28} />}
                  <span style={{ fontSize: 12 }}>Tap to view</span>
                </>
              )}
            </button>
          )}
          {caption && (
            <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {caption}
            </div>
          )}
        </div>
      );
    }

    if (isFileType) {
      const caption = m.message_text || null;
      const fileName = m.file_name || "File";
      const size = formatBytes(m.size_bytes ?? null);
      let icon = <FileText size={16} />;
      let label = fileName;
      if (type === "voice") {
        icon = <Mic size={16} />;
        label = "Voice message";
      }
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 180 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ display: "inline-flex", flexShrink: 0 }}>{icon}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {label}
              </div>
              {size && (
                <div style={{ fontSize: 11, color: captionColor }}>{size}</div>
              )}
            </div>
            {mediaGone ? (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                No longer available in Telegram
              </span>
            ) : (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={handleDownload}
                disabled={downloading}
                aria-label="Download"
              >
                <Download size={12} /> {downloading ? "Downloading…" : "Download"}
              </button>
            )}
          </div>
          {caption && (
            <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {caption}
            </div>
          )}
        </div>
      );
    }

    return <>{m.message_text}</>;
  };

  return (
    <div
      style={{
        display: "flex",
        justifyContent: isOutbound ? "flex-end" : "flex-start",
        marginBottom: 14,
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div style={{ maxWidth: "70%", position: "relative" }}>
        {!isOutbound && (
          <div
            className="muted"
            style={{ fontSize: 11, marginBottom: 4, paddingLeft: 14 }}
          >
            {new Date(m.created_at).toLocaleString()}
            {m.edited_at && <span> · (edited)</span>}
          </div>
        )}
        {isOutbound && hovered && !isEditing && (
          <div
            style={{
              position: "absolute",
              top: -12,
              right: 8,
              display: "flex",
              gap: 4,
              padding: 3,
              background: "var(--bg)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              boxShadow: "0 2px 6px rgba(15,20,25,0.08)",
              zIndex: 2,
            }}
          >
            {isTextType && (
              <button
                type="button"
                onClick={startEdit}
                aria-label="Edit message"
                style={{
                  width: 26,
                  height: 26,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  border: "none",
                  background: "transparent",
                  color: "var(--text-muted)",
                  cursor: "pointer",
                  borderRadius: 6,
                }}
              >
                <Pencil size={13} />
              </button>
            )}
            <button
              type="button"
              onClick={() => onRequestDelete(m)}
              aria-label="Delete for everyone"
              style={{
                width: 26,
                height: 26,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                border: "none",
                background: "transparent",
                color: "var(--danger)",
                cursor: "pointer",
                borderRadius: 6,
              }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        )}
        <div
          style={{
            padding: "10px 14px",
            borderRadius: isOutbound ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
            background: bubbleBg,
            color: bubbleColor,
            fontSize: 13.5,
            lineHeight: 1.5,
            boxShadow: isOutbound
              ? "none"
              : "0 1px 1px rgba(15,20,25,0.04), 0 0 0 1px rgba(15,20,25,0.04)",
            whiteSpace: isEditing || isFileType ? "normal" : "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {renderBody()}
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
            {m.edited_at && <span>· (edited)</span>}
            {isAI && <span>· 🤖</span>}
            <Check size={11} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- RIGHT: details + thought trace ---------------- */

function RightPane({
  conversationId,
  campaigns,
}: {
  conversationId: string;
  campaigns: Campaign[];
}) {
  const [tab, setTab] = useState<"details" | "trace">("details");
  const convQ = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api<Conversation>(`/api/v1/conversations/${conversationId}`),
    staleTime: 10_000,
  });
  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<SenderList>("/api/v1/senders"),
    staleTime: 60_000,
  });
  const agentsQ = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<AgentList>("/api/v1/agents"),
    staleTime: 60_000,
  });

  const conv = convQ.data;
  const sender = sendersQ.data?.senders.find((s) => s.slug === conv?.sender_slug);
  const campaign = campaigns.find((c) => c.id === conv?.campaign_id);
  const agent = campaign?.agent_id
    ? agentsQ.data?.agents.find((a) => a.id === campaign.agent_id)
    : undefined;

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
      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "10px 12px 0",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {(["details", "trace"] as const).map((t) => {
          const active = tab === t;
          return (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              style={{
                padding: "8px 12px",
                fontSize: 12.5,
                fontWeight: 600,
                background: "transparent",
                border: "none",
                borderBottom: active
                  ? "2px solid var(--tg-blue, #3390ec)"
                  : "2px solid transparent",
                color: active ? "var(--text)" : "var(--text-muted)",
                cursor: "pointer",
                marginBottom: -1,
              }}
            >
              {t === "details" ? "Details" : "Thought trace"}
            </button>
          );
        })}
      </div>
      {tab === "details" ? (
        <DetailsTab conv={conv} sender={sender} agent={agent} campaign={campaign} />
      ) : (
        <TracePane conversationId={conversationId} />
      )}
    </aside>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "6px 0" }}>
      <span className="muted" style={{ fontSize: 11.5 }}>
        {label}
      </span>
      <span style={{ fontSize: 12.5, fontWeight: 500, textAlign: "right", wordBreak: "break-word" }}>
        {value ?? "—"}
      </span>
    </div>
  );
}

function DetailSection({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: "10px 14px",
        marginBottom: 12,
        background: "var(--bg)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--text-muted)",
          marginBottom: 6,
        }}
      >
        <span style={{ color: "var(--text-faint)" }}>{icon}</span>
        {title}
      </div>
      {children}
    </section>
  );
}

function DetailsTab({
  conv,
  sender,
  agent,
  campaign,
}: {
  conv?: Conversation;
  sender?: Sender;
  agent?: Agent;
  campaign?: Campaign;
}) {
  if (!conv) {
    return (
      <div className="scroll muted" style={{ padding: 16, fontSize: 12 }}>
        Loading…
      </div>
    );
  }
  return (
    <div className="scroll" style={{ flex: 1, padding: "14px 14px" }}>
      <DetailSection title="Agent" icon={<Bot size={12} />}>
        <DetailRow label="Name" value={agent?.name ?? "—"} />
      </DetailSection>

      <DetailSection title="Campaign" icon={<Flag size={12} />}>
        <DetailRow label="Name" value={campaign?.name ?? "—"} />
        {campaign?.status && <DetailRow label="Status" value={campaign.status} />}
      </DetailSection>

      <DetailSection title="Sender account" icon={<Phone size={12} />}>
        <DetailRow label="Name" value={sender?.name ?? (conv.sender_slug ? `@${conv.sender_slug}` : "—")} />
        <DetailRow label="Phone" value={sender?.phone ?? "—"} />
        {sender?.status && <DetailRow label="Status" value={sender.status} />}
        {sender?.role && <DetailRow label="Role" value={sender.role} />}
      </DetailSection>

      <DetailSection title="Recipient" icon={<UserIcon size={12} />}>
        <DetailRow label="Name" value={conv.contact_name ?? "—"} />
        <DetailRow label="Phone" value={conv.contact_phone ?? "—"} />
        {conv.contact_telegram_id != null && (
          <DetailRow label="Telegram ID" value={String(conv.contact_telegram_id)} />
        )}
        <DetailRow label="Status" value={conv.status ?? "—"} />
        <DetailRow
          label="Last active"
          value={conv.last_message_at ? new Date(conv.last_message_at).toLocaleString() : "—"}
        />
      </DetailSection>
    </div>
  );
}

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
