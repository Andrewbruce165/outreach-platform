/**
 * Shared date+time formatter with an explicit timezone label ("GMT+2").
 * Backend timestamps are UTC; the browser renders them in its local zone —
 * the label makes that zone visible so times are unambiguous.
 */
export function fmtDateTimeTZ(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}
