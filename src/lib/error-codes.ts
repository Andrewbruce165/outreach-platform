// Backend `{code, message}` envelope → friendly UI strings (en-US).
// Source: docs/error-codes.md.

const CODE_MAP: Record<string, (d: Record<string, unknown>) => string> = {
  TOKEN_EXPIRED: () => "Your session expired. Sign in again.",
  TOKEN_INVALID: () => "Sign-in could not be validated. Open the latest email link or sign in again.",
  AUTH_REQUIRED: () => "Sign-in is still syncing. Refresh the page in a moment.",
  WORKSPACE_NOT_FOUND: () => "Workspace not found",
  CAMPAIGN_NOT_FOUND: () => "Campaign not found",
  SENDER_NOT_FOUND: () => "Account not found",
  AGENT_NOT_FOUND: () => "Agent not found",
  FOLDER_NOT_FOUND: () => "Folder not found",
  CONVERSATION_NOT_FOUND: () => "Conversation not found",
  INVALID_TRANSITION: (d) =>
    `Can't change from ${String(d.from ?? "?")} to ${String(d.to ?? "?")}`,
  SENDER_LOCK_CONFLICT: (d) => {
    const conflicts = (d.conflicts as Array<{ campaign_name?: string }>) ?? [];
    const names = conflicts
      .map((c) => c.campaign_name)
      .filter(Boolean)
      .join(", ");
    return names
      ? `Account is already in running campaign(s): ${names}. Stop them to free the account.`
      : "Account is locked by another running campaign.";
  },
  MIN_POOL_GUARD: () =>
    "Can't remove the last account from a running campaign. Pause it first.",
  DETACH_BLOCKED_PENDING: () =>
    "This account still has un-sent contacts. Pause the campaign or wait for the queue to drain.",
  NO_SENDERS_ATTACHED: () => "Attach at least one account before launching",
  KEY_REQUIRED: () =>
    "Введите API-ключ, чтобы переключить провайдера или модель.",
  CONNECTION_INVALID: (d) =>
    d.detail
      ? `Ключ не прошёл проверку: ${String(d.detail)}`
      : "Ключ провайдера не прошёл проверку. Проверьте ключ и провайдера.",
  UNKNOWN_EVENT: () => "Internal: unknown telemetry event",
  ID_REQUIRED: () => "Missing id parameter",
  INVALID_PHONE: () => "Phone number is invalid. Use +1 415 555 2810 format.",
  SERVER_ERROR: () => "Server is unreachable. Retry.",
  // ── File uploads (Phase 21 account import, Phase 24 campaign attachment) ─
  FILE_TOO_LARGE: () => "That file is too large. Use a smaller one and try again.",
  ZIP_TOO_LARGE: () =>
    "The archive expands to more than we allow. Split it into smaller batches and try again.",
  TOO_MANY_ACCOUNTS: () =>
    "This ZIP has too many accounts. Split it into smaller batches and try again.",
  BAD_ZIP: () => "We couldn't read that file. Upload a valid .zip of account files.",
  IMPORT_NOT_FOUND: () => "That import session is gone. Upload the ZIP again to start over.",
  IMPORT_EXPIRED: () => "This import session expired. Upload the ZIP again to start over.",
  JOB_NOT_FOUND: () => "Import job not found.",
  INVALID_ROLE: () => "Pick a role (sender or checker) before confirming.",
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
