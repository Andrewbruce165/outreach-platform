// Backend `{code, message}` envelope → friendly UI strings (en-US).
// Source: docs/error-codes.md.

const CODE_MAP: Record<string, (d: Record<string, unknown>) => string> = {
  TOKEN_EXPIRED: () => "Your session expired. Sign in again.",
  TOKEN_INVALID: () =>
    "Sign-in succeeded, but the API rejected the auth token. Check the auth JWT algorithm/secret, then sign in again.",
  AUTH_REQUIRED: () => "Sign-in is still syncing. Refresh the page in a moment.",
  WORKSPACE_NOT_FOUND: () => "Workspace not found",
  CAMPAIGN_NOT_FOUND: () => "Campaign not found",
  SENDER_NOT_FOUND: () => "Account not found",
  AGENT_NOT_FOUND: () => "Agent not found",
  FOLDER_NOT_FOUND: () => "Folder not found",
  CONVERSATION_NOT_FOUND: () => "Conversation not found",
  INVALID_TRANSITION: (d) =>
    `Can't change from ${String(d.from ?? "?")} to ${String(d.to ?? "?")}`,
  SENDER_LOCK_CONFLICT: (d) =>
    `Sender ${String(d.name ?? "")} is locked by campaign ${String(
      d.other ?? "another campaign",
    )}. Stop that campaign to free the sender.`,
  NO_SENDERS_ATTACHED: () => "Attach at least one account before launching",
  UNKNOWN_EVENT: () => "Internal: unknown telemetry event",
  ID_REQUIRED: () => "Missing id parameter",
  INVALID_PHONE: () => "Phone number is invalid. Use +1 415 555 2810 format.",
  SERVER_ERROR: () => "Server is unreachable. Retry.",
};

export function errorMessageFromEnvelope(
  code: string,
  detail: Record<string, unknown>,
): string {
  const fn = CODE_MAP[code];
  if (fn) return fn(detail);
  if ((detail.message as string)?.length) {
    // Last-resort fallback. Per AGENTS.md we should not normally show raw
    // backend messages, but for unknown codes the dev needs *something*.
    return String(detail.message);
  }
  return "Something went wrong. Try again.";
}
