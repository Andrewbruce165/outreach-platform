/**
 * Campaign detail redesign — senders / пул panel (D-10/D-11, brief item 3).
 *
 * Logic is a straight port of the pre-redesign SendersPanel (attach/detach,
 * POOL-09 lock surfacing, MIN_POOL_GUARD errors bubble through the caller's
 * error banner). Presentation moved to shadcn Card + reui Badge, and each
 * attached sender now shows its restriction state inline.
 */
import { Lock, Plus, X } from "lucide-react";

import { Badge } from "@/components/reui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RestrictionBadge } from "@/components/campaign/badges";
import type { components } from "@/types/api";

type Campaign = components["schemas"]["CampaignResponse"];
type Sender = components["schemas"]["SenderResponse"];

function SenderAvatar({ label, size = 32 }: { label: string; size?: number }) {
  return (
    <div
      aria-hidden
      className="flex shrink-0 items-center justify-center rounded-full bg-[var(--tg-blue)] font-medium text-white"
      style={{ width: size, height: size, fontSize: size * 0.42 }}
    >
      {label.slice(0, 1).toUpperCase()}
    </div>
  );
}

export function CampaignSenders({
  campaign,
  senders,
  attaching,
  detaching,
  onAttach,
  onDetach,
}: {
  campaign: Campaign;
  senders: Sender[];
  attaching: boolean;
  detaching: boolean;
  onAttach: (senderId: string) => void;
  onDetach: (senderId: string) => void;
}) {
  const attached = campaign.attached_senders ?? [];
  const attachedIds = new Set(attached.map((s) => s.sender_id));
  const byId = new Map(senders.map((s) => [s.id, s]));
  const busy = attaching || detaching;

  // Eligible to add: workspace senders not already attached and not in error.
  // Locked senders are still listed but their add control is disabled (POOL-09).
  const eligible = senders.filter((s) => !attachedIds.has(s.id) && s.status !== "error");

  return (
    <Card>
      <CardHeader className="pb-4">
        <CardTitle className="text-sm">Senders ({attached.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Attached pool */}
        {attached.length === 0 ? (
          <p className="text-sm text-muted-foreground">No senders attached</p>
        ) : (
          <ul className="space-y-1.5">
            {attached.map((s) => {
              const sender = byId.get(s.sender_id);
              const locked = !!s.locked_by_campaign_name;
              const label = sender ? sender.name || sender.slug : `${s.sender_id.slice(0, 8)}…`;
              return (
                <li
                  key={s.sender_id}
                  className="flex items-center gap-3 rounded-lg border bg-muted/30 px-3 py-2"
                >
                  <SenderAvatar label={label} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="truncate text-sm font-medium">{label}</span>
                      <RestrictionBadge sender={s} />
                    </div>
                    {sender?.phone && (
                      <div className="text-xs tabular-nums text-muted-foreground">{sender.phone}</div>
                    )}
                    {locked && (
                      <div className="mt-0.5 flex items-center gap-1 text-xs text-[var(--danger)]">
                        <Lock size={11} /> Locked by {s.locked_by_campaign_name}
                      </div>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0 text-muted-foreground hover:text-[var(--danger)]"
                    aria-label={`Remove ${label}`}
                    title={
                      locked
                        ? `Also in running campaign "${s.locked_by_campaign_name}". Detaching here only removes it from this campaign — the running one is untouched.`
                        : "Remove from pool"
                    }
                    disabled={busy}
                    onClick={() => onDetach(s.sender_id)}
                  >
                    <X size={14} />
                  </Button>
                </li>
              );
            })}
          </ul>
        )}

        {/* Add to pool */}
        <div>
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Add account
          </div>
          {eligible.length === 0 ? (
            <p className="text-xs text-muted-foreground">No more accounts available to add.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {eligible.map((s) => {
                const active = s.status === "active";
                const locked = !!s.locked_by_campaign_name;
                return (
                  <button
                    key={s.id}
                    type="button"
                    disabled={busy || locked}
                    onClick={() => onAttach(s.id)}
                    title={
                      locked
                        ? `Locked by running campaign: ${s.locked_by_campaign_name}. Pause it first.`
                        : `Add ${s.name || s.slug} to the pool`
                    }
                    className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border bg-background py-1 pl-1 pr-2 text-xs transition-colors hover:border-[var(--tg-blue)] disabled:cursor-default disabled:opacity-55 disabled:hover:border-border"
                  >
                    <SenderAvatar label={s.name || s.slug} size={20} />
                    <span className="max-w-32 truncate">{s.name || s.slug}</span>
                    {locked ? (
                      <span className="inline-flex items-center gap-1 text-[10px] text-[var(--danger)]">
                        <Lock size={10} /> {s.locked_by_campaign_name}
                      </span>
                    ) : (
                      <>
                        <Badge variant={active ? "success-light" : "destructive-light"} size="xs" radius="full">
                          {s.status}
                        </Badge>
                        <Plus size={12} className="text-muted-foreground" />
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <p className="text-xs leading-relaxed text-muted-foreground">
          Sender selection in the campaign wizard only seeds the initial pool — manage the live
          pool here.
        </p>
      </CardContent>
    </Card>
  );
}
