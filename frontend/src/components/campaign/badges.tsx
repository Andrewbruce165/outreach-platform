/**
 * Campaign detail redesign — shared status badges on reui <Badge>.
 *
 * Semantics are unchanged from the pre-redesign page:
 *  - StatusBadge: campaign lifecycle status.
 *  - PoolBadge (POOLV-03 / D-09): 3-state pool health derived ON THE FRONTEND
 *    from the numeric pool_health aggregate (API stays presentation-free).
 *  - RestrictionBadge (POOLV-02): per-sender restriction chip.
 */
import { Badge } from "@/components/reui/badge";
import type { components } from "@/types/api";

type PoolHealth = components["schemas"]["PoolHealth"];
type AttachedSender = components["schemas"]["CampaignSenderAttach"];

type BadgeVariant = React.ComponentProps<typeof Badge>["variant"];

const STATUS_META: Record<string, { label: string; variant: BadgeVariant }> = {
  running:   { label: "Running",   variant: "success-light" },
  paused:    { label: "Paused",    variant: "warning-light" },
  draft:     { label: "Draft",     variant: "outline" },
  scheduled: { label: "Scheduled", variant: "info-light" },
  finished:  { label: "Finished",  variant: "secondary" },
  stopped:   { label: "Stopped",   variant: "destructive-light" },
};

export function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? STATUS_META.draft;
  return (
    <Badge variant={meta.variant} radius="full">
      <span className="size-1.5 rounded-full bg-current opacity-70" aria-hidden />
      {meta.label}
    </Badge>
  );
}

export function fmtUntil(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * paused === 0                  → 🟢 "Пул активен"
 * 0 < paused < total            → 🟡 "K из N на паузе до проверки в T"
 * paused === total && total > 0 → 🔴 "Весь пул на паузе"
 * earliest_resume_at is a recheck horizon (OQ#4) → wording "до проверки в T".
 */
export function PoolBadge({ health }: { health: PoolHealth | null | undefined }) {
  if (!health || health.total === 0) return null;
  const { active, paused, total, earliest_resume_at } = health;

  if (paused === 0) {
    return (
      <Badge variant="success-light" radius="full" title={`${active} из ${total} аккаунтов активны`}>
        <span className="size-1.5 rounded-full bg-current opacity-70" aria-hidden />
        Пул активен
      </Badge>
    );
  }
  if (paused === total) {
    return (
      <Badge
        variant="destructive-light"
        radius="full"
        title={
          earliest_resume_at
            ? `Все ${total} аккаунтов на паузе · проверка ${fmtUntil(earliest_resume_at)}`
            : `Все ${total} аккаунтов на паузе`
        }
      >
        <span className="size-1.5 rounded-full bg-current opacity-70" aria-hidden />
        Весь пул на паузе
      </Badge>
    );
  }
  return (
    <Badge
      variant="warning-light"
      radius="full"
      title={`${paused} из ${total} аккаунтов на паузе${
        earliest_resume_at ? ` · проверка ${fmtUntil(earliest_resume_at)}` : ""
      }`}
    >
      <span className="size-1.5 rounded-full bg-current opacity-70" aria-hidden />
      {paused} из {total} на паузе
      {earliest_resume_at ? ` · до проверки в ${fmtUntil(earliest_resume_at)}` : ""}
    </Badge>
  );
}

const RESTRICTION_META: Record<
  Exclude<AttachedSender["restriction_status"] & string, "none">,
  { label: string; variant: BadgeVariant }
> = {
  spam_limited: { label: "Спам-лимит", variant: "warning-light" },
  frozen: { label: "Заморожен", variant: "destructive-light" },
};

export function RestrictionBadge({ sender }: { sender: AttachedSender }) {
  if (!sender.restriction_status || sender.restriction_status === "none") return null;
  const meta = RESTRICTION_META[sender.restriction_status];
  if (!meta) return null;
  const until = sender.restricted_until ? fmtUntil(sender.restricted_until) : null;
  return (
    <Badge
      variant={meta.variant}
      size="sm"
      radius="full"
      title={until ? `${meta.label} · проверка ${until}` : meta.label}
    >
      {meta.label}
      {until ? ` · до ${until}` : ""}
    </Badge>
  );
}
