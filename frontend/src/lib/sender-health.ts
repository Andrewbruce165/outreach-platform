import type { components } from "@/types/api";

type Sender = components["schemas"]["SenderResponse"];

export type SenderHealth = "green" | "yellow" | "red";

export interface SenderHealthInfo {
  health: SenderHealth;
  /** CSS var token for the line color. */
  color: string;
  /** Short human label for a title/tooltip, RU (matches accounts.tsx tone). */
  label: string;
}

/**
 * Collapse a sender's account state into a 3-color traffic light.
 * Semantics mirror frontend/src/routes/_authenticated/accounts.tsx:
 *   - RED   (re-auth needed OR hard-dead): auth_status !== "ok"  → session expired,
 *           OR status "frozen" / "error"  → blocked/frozen.
 *           auth check takes precedence, exactly like accounts.tsx priorityTier.
 *   - YELLOW (recoverable / resting / отлежка): status "limited" (spam-limited),
 *           "paused" (resting), or "warmup" (warming up).
 *   - GREEN  (healthy): status "active".
 */
export function deriveSenderHealth(sender: Sender): SenderHealthInfo {
  // Re-auth takes precedence (accounts.tsx: auth_status !== "ok" ⇒ tier 0 / "session expired").
  if (sender.auth_status !== "ok") {
    return { health: "red", color: "var(--danger)", label: "Требует повторной авторизации" };
  }
  switch (sender.status) {
    case "frozen":
      return { health: "red", color: "var(--danger)", label: "Заморожен" };
    case "error":
      return { health: "red", color: "var(--danger)", label: "Ошибка аккаунта" };
    case "limited":
      return { health: "yellow", color: "var(--warning)", label: "Спам-лимит" };
    case "paused":
      return { health: "yellow", color: "var(--warning)", label: "На паузе (отлёжка)" };
    case "warmup":
      return { health: "yellow", color: "var(--warning)", label: "Прогрев" };
    case "active":
      return { health: "green", color: "var(--success)", label: "Активен" };
    default:
      // Unknown/future status → neutral, never crash.
      return { health: "yellow", color: "var(--warning)", label: sender.status };
  }
}
