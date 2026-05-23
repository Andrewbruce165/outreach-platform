import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Topbar } from "@/components/Topbar";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type Conversation = components["schemas"]["ConversationResponse"];
type ConversationList = components["schemas"]["ConversationListResponse"];
type Message = components["schemas"]["MessageResponse"];
type MessageList = components["schemas"]["MessageListResponse"];
type LLMCall = components["schemas"]["LLMCallResponse"];
type LLMCallList = components["schemas"]["LLMCallListResponse"];

export const Route = createFileRoute("/_authenticated/inbox")({
  component: InboxPage,
});

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

function InboxPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [showTrace, setShowTrace] = useState(false);

  const listQ = useQuery({
    queryKey: ["conversations", { search }],
    queryFn: () =>
      api<ConversationList>("/api/v1/conversations", {
        query: { limit: 100, ...(search ? { search } : {}) },
      }),
    refetchInterval: 10_000, // UI-SPEC §5.7
  });

  const conversations = listQ.data?.conversations ?? [];

  // Auto-select first on initial load
  useEffect(() => {
    if (!selectedId && conversations.length > 0) {
      setSelectedId(conversations[0].id);
    }
  }, [conversations, selectedId]);

  return (
    <>
      <Topbar
        title="Inbox"
        right={
          <button
            className={`btn btn--sm ${showTrace ? "btn--primary" : "btn--ghost"}`}
            onClick={() => setShowTrace((v) => !v)}
          >
            Thought trace
          </button>
        }
      />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: showTrace ? "320px 1fr 380px" : "320px 1fr",
          flex: 1,
          minHeight: 0,
          background: "var(--bg-soft, #f7f8fa)",
        }}
      >
        <ConvList
          loading={listQ.isLoading}
          error={listQ.error ? errMsg(listQ.error) : null}
          items={conversations}
          activeId={selectedId}
          onSelect={setSelectedId}
          search={search}
          onSearch={setSearch}
        />
        {selectedId ? (
          <Thread conversationId={selectedId} />
        ) : (
          <EmptyMid />
        )}
        {showTrace && selectedId && <TracePane conversationId={selectedId} />}
      </div>
    </>
  );
}

/* ---------------- LEFT: conversation list ---------------- */

function ConvList({
  loading,
  error,
  items,
  activeId,
  onSelect,
  search,
  onSearch,
}: {
  loading: boolean;
  error: string | null;
  items: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  search: string;
  onSearch: (s: string) => void;
}) {
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
      <div style={{ padding: 12, borderBottom: "1px solid var(--border)" }}>
        <input
          className="input"
          placeholder="Search conversations…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </div>
      <div className="scroll" style={{ flex: 1 }}>
        {loading && <div className="muted" style={{ padding: 16 }}>Loading…</div>}
        {error && (
          <div style={{ padding: 16, color: "var(--danger, #c0392b)", fontSize: 13 }}>
            {error}
          </div>
        )}
        {!loading && !error && items.length === 0 && (
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
        {items.map((c) => {
          const active = c.id === activeId;
          return (
            <button
              key={c.id}
              onClick={() => onSelect(c.id)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "10px 14px",
                background: active ? "var(--bg-soft, #f3f4f6)" : "transparent",
                borderLeft: active ? "3px solid var(--tg-blue)" : "3px solid transparent",
                border: "none",
                borderBottom: "1px solid var(--border)",
                cursor: "pointer",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginBottom: 4,
                }}
              >
                <span style={{ fontWeight: 600, fontSize: 13, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.contact_name || c.contact_phone}
                </span>
                {!c.ai_enabled && (
                  <span
                    title="Human takeover"
                    style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 4,
                      background: "#fff4d6",
                      color: "#8a6a00",
                    }}
                  >
                    Human
                  </span>
                )}
                {c.unread_count > 0 && (
                  <span
                    style={{
                      minWidth: 18,
                      height: 18,
                      padding: "0 5px",
                      borderRadius: 9,
                      background: "var(--tg-blue)",
                      color: "#fff",
                      fontSize: 11,
                      fontWeight: 600,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {c.unread_count}
                  </span>
                )}
              </div>
              <div
                className="muted"
                style={{
                  fontSize: 12,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {c.last_message ?? "—"}
              </div>
              <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
                {c.status} · {c.last_message_at ? new Date(c.last_message_at).toLocaleString() : "—"}
              </div>
            </button>
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

function Thread({ conversationId }: { conversationId: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const convQ = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () =>
      api<Conversation>(`/api/v1/conversations/${conversationId}`),
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

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        background: "var(--bg)",
      }}
    >
      {/* Header */}
      <header
        style={{
          padding: "12px 18px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            {conv?.contact_name || conv?.contact_phone || "—"}
          </div>
          <div className="muted" style={{ fontSize: 11 }}>
            {conv?.sender_slug ? `via @${conv.sender_slug}` : ""}
            {conv?.status ? ` · ${conv.status}` : ""}
          </div>
        </div>
        {conv && (
          conv.ai_enabled ? (
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => disableAiMut.mutate()}
              disabled={disableAiMut.isPending}
            >
              Take over (disable AI)
            </button>
          ) : (
            <button
              className="btn btn--primary btn--sm"
              onClick={() => enableAiMut.mutate()}
              disabled={enableAiMut.isPending}
            >
              Hand back to AI
            </button>
          )
        )}
      </header>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="scroll"
        style={{ flex: 1, padding: "16px 18px", background: "var(--bg-soft, #f7f8fa)" }}
      >
        {messagesQ.isLoading && <div className="muted">Loading messages…</div>}
        {messagesQ.error && (
          <div style={{ color: "var(--danger, #c0392b)" }}>
            {errMsg(messagesQ.error)}
          </div>
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
          padding: 12,
          borderTop: "1px solid var(--border)",
          background: "var(--bg)",
        }}
      >
        {sendError && (
          <div
            style={{
              color: "var(--danger, #c0392b)",
              fontSize: 12,
              marginBottom: 6,
            }}
          >
            {sendError}
          </div>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          <textarea
            className="textarea"
            rows={2}
            placeholder={
              conv?.ai_enabled
                ? "Disable AI to send manually…"
                : "Type a message…"
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
            style={{ flex: 1, resize: "none" }}
          />
          <button
            type="submit"
            className="btn btn--primary"
            disabled={!conv || conv.ai_enabled || !draft.trim() || sendMut.isPending}
          >
            {sendMut.isPending ? "Sending…" : "Send"}
          </button>
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          ⌘+Enter to send
        </div>
      </form>
    </section>
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
        marginBottom: 8,
      }}
    >
      <div
        style={{
          maxWidth: "70%",
          padding: "8px 12px",
          borderRadius: 12,
          background: isOutbound
            ? isAI
              ? "color-mix(in oklab, var(--ai-purple, #8774e1) 14%, white)"
              : "var(--tg-blue)"
            : "var(--bg)",
          color: isOutbound && !isAI ? "#fff" : "var(--text)",
          border: !isOutbound ? "1px solid var(--border)" : "none",
          fontSize: 13,
          lineHeight: 1.4,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        <div>{m.message_text}</div>
        <div
          style={{
            fontSize: 10,
            opacity: 0.7,
            marginTop: 4,
            textAlign: "right",
          }}
        >
          {isAI && "🤖 "}
          {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </div>
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
      }}
    >
      <header
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 13 }}>Thought trace</div>
        <div className="muted" style={{ fontSize: 11 }}>
          AI calls for this conversation
        </div>
      </header>
      <div className="scroll" style={{ flex: 1, padding: 12 }}>
        {tracesQ.isLoading && <div className="muted">Loading…</div>}
        {tracesQ.error && (
          <div style={{ color: "var(--danger, #c0392b)", fontSize: 12 }}>
            {errMsg(tracesQ.error)}
          </div>
        )}
        {tracesQ.data && tracesQ.data.llm_calls.length === 0 && (
          <div className="muted" style={{ fontSize: 12 }}>
            No AI calls yet.
          </div>
        )}
        {tracesQ.data?.llm_calls.map((call) => (
          <TraceCard key={call.id} call={call} />
        ))}
      </div>
    </aside>
  );
}

function TraceCard({ call }: { call: LLMCall }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderLeft: "3px solid var(--ai-purple, #8774e1)",
        borderRadius: 8,
        padding: 10,
        marginBottom: 8,
        background: "var(--bg)",
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          textAlign: "left",
          background: "none",
          border: 0,
          padding: 0,
          cursor: "pointer",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>{call.model}</span>
          <span className="muted" style={{ fontSize: 10 }}>
            {call.latency_ms != null ? `${call.latency_ms}ms` : "—"} ·{" "}
            {call.total_tokens ?? 0}t
          </span>
        </div>
        <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
          {new Date(call.created_at).toLocaleTimeString()}
        </div>
        {call.response_text && (
          <div
            style={{
              fontSize: 12,
              marginTop: 6,
              color: "var(--text)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "-webkit-box",
              WebkitLineClamp: open ? undefined : 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {call.response_text}
          </div>
        )}
        {call.error && (
          <div style={{ fontSize: 11, marginTop: 4, color: "var(--danger, #c0392b)" }}>
            {call.error}
          </div>
        )}
      </button>
      {open && (
        <details open style={{ marginTop: 8 }}>
          <summary style={{ fontSize: 11, color: "var(--text-soft)", cursor: "pointer" }}>
            Prompt
          </summary>
          <pre
            style={{
              fontSize: 10,
              maxHeight: 200,
              overflow: "auto",
              background: "var(--bg-soft, #f7f8fa)",
              padding: 8,
              borderRadius: 4,
              marginTop: 4,
            }}
          >
            {JSON.stringify(call.prompt, null, 2)}
          </pre>
          {call.tool_calls && (
            <>
              <summary style={{ fontSize: 11, color: "var(--text-soft)", marginTop: 6 }}>
                Tool calls
              </summary>
              <pre
                style={{
                  fontSize: 10,
                  background: "var(--bg-soft, #f7f8fa)",
                  padding: 8,
                  borderRadius: 4,
                  marginTop: 4,
                }}
              >
                {JSON.stringify(call.tool_calls, null, 2)}
              </pre>
            </>
          )}
        </details>
      )}
    </div>
  );
}
