import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Topbar } from "@/components/Topbar";
import { OnboardingFlow } from "@/components/OnboardingFlow";

export const Route = createFileRoute("/_authenticated/onboarding")({
  component: Page,
});

function Page() {
  const navigate = useNavigate();
  return (
    <>
      <Topbar title="Connect a Telegram account" />
      <div className="scroll" style={{ flex: 1, padding: 24 }}>
        <div className="card" style={{ maxWidth: 560, margin: "0 auto" }}>
          <div className="card__body">
            <OnboardingFlow
              onComplete={() => {
                setTimeout(() => navigate({ to: "/accounts" }), 1200);
              }}
            />
          </div>
        </div>
      </div>
    </>
  );
}
