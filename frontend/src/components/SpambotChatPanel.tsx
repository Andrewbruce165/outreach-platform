import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Send } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { components } from "@/types/api";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";

type MessageList = components["schemas"]["MessageListResponse"];
type Message = components["schemas"]["MessageResponse"];

interface SpambotChatPanelProps {
  slug: string;
  senderLabel: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}

/**
 * Side-panel live chat with the official Telegram @SpamBot (id 178220800) from a
 * specific sender account (quick task 260713-hiw). A slimmed Inbox Thread —
 * message list + composer only, no AI-trace tabs / tags / RightPane.
 *
 * On open it get-or-creates the dedicated status='spambot' conversation via
 * POST /senders/{slug}/spambot-conversation, then polls
 * GET /conversations/{id}/messages every 10s and sends through the reused
 * POST /conversations/{id}/send.
 */
export function SpambotChatPanel({
  slug,
  senderLabel,
  open,
  onOpenChange,
}: SpambotChatPanelProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const convQ = useQuery({
    queryKey: ["spambot-conversation", slug],
    enabled: open,
    queryFn: () =>
      api<{ conversation_id: string; status: string }>(
        `/api/v1/senders/${slug}/spambot-conversation`,
        { method: "POST" },
      ),
  });

  const conversationId = convQ.data?.conversation_id;

  const messagesQ = useQuery({
    queryKey: ["messages", conversationId],
    enabled: open && !!conversationId,
    queryFn: () =>
      api<MessageList>(`/api/v1/conversations/${conversationId}/messages`, {
        query: { limit: 200 },
      }),
    refetchInterval: 10_000,
  });

  const sendMut = useMutation({
    mutationFn: (message: string) =>
      api(`/api/v1/conversations/${conversationId}/send`, {
        method: "POST",
        body: { message },
      }),
    onSuccess: () => {
      setDraft("");
      void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : "Не удалось отправить"),
  });

  const canSend = !!conversationId && !sendMut.isPending && draft.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    sendMut.mutate(draft.trim());
  };

  const messages: Message[] = messagesQ.data?.messages ?? [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>@SpamBot</SheetTitle>
          <SheetDescription>{senderLabel}</SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto py-4">
          {convQ.isLoading ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 animate-spin" size={16} /> Открываем чат…
            </div>
          ) : convQ.isError ? (
            <div className="flex h-full items-center justify-center text-sm text-destructive">
              Не удалось открыть чат с @SpamBot
            </div>
          ) : messages.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Пока нет сообщений. Напишите @SpamBot, например /start.
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {messages.map((m) => {
                const isOutbound = m.direction === "outbound";
                return (
                  <div
                    key={m.id}
                    className={
                      isOutbound
                        ? "ml-auto max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                        : "mr-auto max-w-[80%] rounded-lg bg-muted px-3 py-2 text-sm text-foreground"
                    }
                  >
                    {m.message_text ?? "<media>"}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-t pt-3">
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="Сообщение @SpamBot…"
              rows={2}
              disabled={!conversationId}
              className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            />
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              className="inline-flex h-10 items-center justify-center rounded-md bg-primary px-3 text-primary-foreground disabled:opacity-50"
            >
              {sendMut.isPending ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <Send size={16} />
              )}
            </button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
