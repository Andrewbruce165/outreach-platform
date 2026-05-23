import { createFileRoute } from "@tanstack/react-router";
import { Topbar } from "@/components/Topbar";

export const Route = createFileRoute("/_authenticated/inbox")({
  component: Page,
});

function Page() {
  return (
    <>
      <Topbar title="Inbox" />
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        <div className="card"><div className="card__body">
          <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Inbox — coming next</h2>
          <p className="muted" style={{ fontSize: 13 }}>
            This screen is scheduled per <code>docs/screen-build-order.md</code>. Foundation (tokens, types, API client, auth, sidebar) is in place — wiring lands turn by turn.
          </p>
        </div></div>
      </div>
    </>
  );
}
