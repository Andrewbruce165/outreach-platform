// Telemetry — POST events to /api/v1/telemetry/events.
// Uses navigator.sendBeacon on pagehide (Pitfall 4 / AGENTS.md rule 10).
import { supabase } from "./supabase";

const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export type TelemetryEvent =
  | "magic_link_requested"
  | "signup_completed"
  | "sender_added"
  | "contacts_imported"
  | "csv_import_completed"
  | "agent_created"
  | "agent_updated"
  | "agent_duplicated"
  | "agent_deleted"
  | "campaign_created"
  | "campaign_launched"
  | "campaign_paused"
  | "campaign_resumed"
  | "campaign_stopped"
  | "campaign_launched"
  | "campaign_paused"
  | "campaign_resumed"
  | "conversation_taken_over_by_human"
  | "llm_trace_opened"
  | "workspace_api_key_created"
  | "settings_changed"
  | "agent_voice_changed"
  | "custom_tool_added"
  | "dashboard_viewed";

interface QueuedEvent {
  event: TelemetryEvent;
  props: Record<string, unknown>;
  client_ts: number;
}

const queue: QueuedEvent[] = [];
let flushScheduled = false;

async function waitForAccessToken(): Promise<string | null> {
  for (let i = 0; i < 20; i++) {
    try {
      const { data } = await supabase.auth.getSession();
      const token = data.session?.access_token ?? null;
      if (token) return token;
    } catch {
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}

async function flush(beacon = false) {
  if (queue.length === 0) return;
  const batch = queue.splice(0, queue.length);
  const url = `${BACKEND_URL}/api/v1/telemetry/events`;

  if (beacon && typeof navigator !== "undefined" && "sendBeacon" in navigator) {
    for (const evt of batch) {
      const blob = new Blob([JSON.stringify(evt)], { type: "application/json" });
      navigator.sendBeacon(url, blob);
    }
    return;
  }

  try {
    const token = await waitForAccessToken();
    await Promise.all(
      batch.map((evt) =>
        fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(evt),
          keepalive: true,
        }).catch(() => undefined),
      ),
    );
  } catch {
    /* swallow — telemetry must never break the UI */
  }
}

export function track(event: TelemetryEvent, props: Record<string, unknown> = {}) {
  queue.push({ event, props, client_ts: Date.now() });
  if (!flushScheduled) {
    flushScheduled = true;
    setTimeout(() => {
      flushScheduled = false;
      void flush();
    }, 1500);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => void flush(true));
}
