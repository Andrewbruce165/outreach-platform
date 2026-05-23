import { createFileRoute } from "@tanstack/react-router";
import { useEffect } from "react";
import { Topbar } from "@/components/Topbar";
import { track } from "@/lib/telemetry";

export const Route = createFileRoute("/_authenticated/")({
  component: Dashboard,
});

function Dashboard() {
  useEffect(() => {
    if (sessionStorage.getItem("dashboard_viewed_once") !== "1") {
      sessionStorage.setItem("dashboard_viewed_once", "1");
      track("dashboard_viewed", {});
    }
  }, []);

  return (
    <>
      <Topbar title="Dashboard" />
      <div className="scroll" style={{ padding: 24, flex: 1 }}>
        <div className="card">
          <div className="card__body">
            <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Welcome to aimly</h2>
            <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>
              Dashboard wiring lands in screen 11 (per <code>screen-build-order.md</code>) — it depends on every other resource. For now jump into setup:
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <a href="/accounts" className="btn btn--primary">Connect a Telegram account</a>
              <a href="/contacts" className="btn btn--ghost">Import contacts</a>
              <a href="/campaigns" className="btn btn--ghost">Create a campaign</a>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
