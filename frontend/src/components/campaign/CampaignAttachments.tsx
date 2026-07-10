/**
 * Campaign detail redesign — first-message attachment previews (brief item 4).
 *
 * GET /campaigns/{id}/attachment          → ordered metadata list
 * GET /campaigns/{id}/attachment/{att_id} → raw bytes (auth-only)
 *
 * Bytes are fetched through apiBlob (Bearer header) and rendered via object
 * URLs — a plain <img src> cannot carry Authorization. Object URLs are revoked
 * on unmount. Renders nothing at all when the campaign has no attachments.
 */
import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, Paperclip } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, apiBlob } from "@/lib/api";
import type { components } from "@/types/api";

type AttachmentMeta = components["schemas"]["CampaignAttachmentMeta"];
type AttachmentsResponse = components["schemas"]["CampaignAttachmentsResponse"];

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function AttachmentTile({ campaignId, att }: { campaignId: string; att: AttachmentMeta }) {
  const isImage = (att.content_type ?? "").startsWith("image/");
  const blobQ = useQuery({
    queryKey: ["campaign-attachment-blob", campaignId, att.id],
    queryFn: () => apiBlob(`/api/v1/campaigns/${campaignId}/attachment/${att.id}`),
    enabled: isImage, // non-images render a file tile; bytes fetched on click
    staleTime: Infinity,
    retry: 1,
  });

  const objectUrl = useMemo(
    () => (blobQ.data ? URL.createObjectURL(blobQ.data) : null),
    [blobQ.data],
  );
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  const open = async () => {
    // For images the blob is already here; for other files fetch on demand.
    const blob = blobQ.data ?? (await apiBlob(`/api/v1/campaigns/${campaignId}/attachment/${att.id}`));
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    // Give the new tab a moment to grab the resource before revoking.
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  };

  return (
    <button
      type="button"
      onClick={() => void open()}
      title={`${att.file_name} · ${fmtBytes(att.size_bytes)}`}
      className="group w-24 shrink-0 cursor-pointer text-left"
    >
      <div className="flex h-24 w-24 items-center justify-center overflow-hidden rounded-lg border bg-muted/40 transition-colors group-hover:border-[var(--tg-blue)]">
        {isImage ? (
          blobQ.isLoading ? (
            <Skeleton className="h-full w-full" />
          ) : objectUrl ? (
            <img src={objectUrl} alt={att.file_name} className="h-full w-full object-cover" />
          ) : (
            <FileText size={22} className="text-muted-foreground" />
          )
        ) : (
          <FileText size={22} className="text-muted-foreground" />
        )}
      </div>
      <div className="mt-1 truncate text-[11px] text-muted-foreground">{att.file_name}</div>
    </button>
  );
}

/** Horizontal strip of attachment previews. Null when there are none. */
export function CampaignAttachments({ campaignId }: { campaignId: string }) {
  const listQ = useQuery({
    queryKey: ["campaign-attachments", campaignId],
    queryFn: () => api<AttachmentsResponse>(`/api/v1/campaigns/${campaignId}/attachment`),
    staleTime: 60_000,
  });

  if (listQ.isLoading) {
    return (
      <div className="flex gap-3">
        <Skeleton className="h-24 w-24 rounded-lg" />
      </div>
    );
  }
  const atts = listQ.data?.attachments ?? [];
  if (atts.length === 0) return null;

  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Paperclip size={12} />
        Вложения первого сообщения ({atts.length})
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {atts.map((a) => (
          <AttachmentTile key={a.id} campaignId={campaignId} att={a} />
        ))}
      </div>
    </div>
  );
}
