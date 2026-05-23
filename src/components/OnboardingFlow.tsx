import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Loader2, Phone, ShieldCheck, CheckCircle2, QrCode, ArrowLeft } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/telemetry";
import type { components } from "@/types/api";

type StartResponse = components["schemas"]["StartResponse"];
type SenderResponse = components["schemas"]["SenderResponse"];

type Tab = "phone" | "qr";
type PhoneStep = "phone" | "code" | "2fa" | "done";

interface Props {
  /** Optional pre-filled phone (re-auth flow) */
  initialPhone?: string;
  /** Called once a sender row is materialized */
  onComplete?: (sender: SenderResponse | { slug?: string }) => void;
  /** Render compact (inside modal) vs full-page */
  compact?: boolean;
}

const phoneSchema = z.object({
  phone: z
    .string()
    .trim()
    .regex(/^\+\d[\d\s\-()]{7,}$/i, "Phone number is invalid. Use +1 415 555 2810 format."),
});
const codeSchema = z.object({
  code: z.string().trim().regex(/^\d{4,6}$/, "Enter the 5–6 digit code."),
  name: z.string().trim().min(1, "Give this account a name").max(40),
});
const twofaSchema = z.object({
  password: z.string().min(1, "Enter your 2FA password"),
});

export function OnboardingFlow({ initialPhone, onComplete, compact = false }: Props) {
  const [tab, setTab] = useState<Tab>("phone");

  return (
    <div className={compact ? "ob ob--compact" : "ob"}>
      {!compact && (
        <header className="ob__head">
          <h2 className="ob__title">Connect a Telegram account</h2>
          <p className="muted ob__sub">
            Add a number that aimly will send messages from. We never post or read DMs that aren&apos;t part of your campaigns.
          </p>
        </header>
      )}
      <div className="ob__tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "phone"}
          className={`ob__tab ${tab === "phone" ? "is-active" : ""}`}
          onClick={() => setTab("phone")}
        >
          <Phone size={14} /> Phone + code
        </button>
        <button
          role="tab"
          aria-selected={tab === "qr"}
          className={`ob__tab ${tab === "qr" ? "is-active" : ""}`}
          onClick={() => setTab("qr")}
        >
          <QrCode size={14} /> Scan QR
        </button>
      </div>

      {tab === "phone" ? (
        <PhoneFlow initialPhone={initialPhone} onComplete={onComplete} />
      ) : (
        <QrFlow onComplete={onComplete} />
      )}
    </div>
  );
}

/* ---------------- Phone flow ---------------- */
function PhoneFlow({
  initialPhone,
  onComplete,
}: {
  initialPhone?: string;
  onComplete?: Props["onComplete"];
}) {
  const [step, setStep] = useState<PhoneStep>("phone");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [phone, setPhone] = useState<string>(initialPhone ?? "");
  const [busy, setBusy] = useState(false);
  const [accountName, setAccountName] = useState<string>("");

  const phoneForm = useForm({
    resolver: zodResolver(phoneSchema),
    defaultValues: { phone: initialPhone ?? "" },
  });
  const codeForm = useForm({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "", name: "" },
  });
  const twofaForm = useForm({
    resolver: zodResolver(twofaSchema),
    defaultValues: { password: "" },
  });

  async function submitPhone(values: { phone: string }) {
    setBusy(true);
    try {
      const res = await api<StartResponse>("/api/v1/onboarding/start", {
        method: "POST",
        body: { phone: values.phone.replace(/\s+/g, ""), role: "sender" },
      });
      setSessionId(res.session_id);
      setPhone(res.phone);
      setStep("code");
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(values: { code: string; name: string }) {
    if (!sessionId) return;
    setBusy(true);
    setAccountName(values.name);
    try {
      const res = await api<unknown>("/api/v1/onboarding/verify-code", {
        method: "POST",
        body: {
          session_id: sessionId,
          code: values.code,
          name: values.name,
          role: "sender",
        },
      });
      const sender = (res as { sender?: SenderResponse }).sender;
      track("sender_added", { sender_id: sender?.id ?? sessionId, method: "phone" });
      setStep("done");
      onComplete?.(sender ?? { slug: undefined });
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === "TWO_FA_REQUIRED" || e.status === 428) {
          setStep("2fa");
          return;
        }
        toast.error(e.message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function submit2fa(values: { password: string }) {
    if (!sessionId) return;
    setBusy(true);
    try {
      const res = await api<unknown>("/api/v1/onboarding/verify-2fa", {
        method: "POST",
        body: { session_id: sessionId, password: values.password, name: accountName || phone },
      });
      const sender = (res as { sender?: SenderResponse }).sender;
      track("sender_added", { sender_id: sender?.id ?? sessionId, method: "phone" });
      setStep("done");
      onComplete?.(sender ?? { slug: undefined });
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ob__panel">
      <Stepper step={step} />
      {step === "phone" && (
        <form className="ob__form" onSubmit={phoneForm.handleSubmit(submitPhone)}>
          <label className="field">
            <span className="field__label">Phone number</span>
            <input
              className="input"
              autoFocus
              placeholder="+1 415 555 2810"
              {...phoneForm.register("phone")}
            />
            {phoneForm.formState.errors.phone && (
              <span className="ob__err">{phoneForm.formState.errors.phone.message}</span>
            )}
            <span className="field__hint">
              Telegram will text a 5-digit code to confirm.
            </span>
          </label>
          <button className="btn btn--primary" type="submit" disabled={busy}>
            {busy && <Loader2 size={14} className="ob__spin" />} Send code
          </button>
        </form>
      )}

      {step === "code" && (
        <form className="ob__form" onSubmit={codeForm.handleSubmit(submitCode)}>
          <div className="ob__phoneRecap">
            <span className="muted text-sm">Code sent to</span>
            <span className="mono fw5">{phone}</span>
            <button
              type="button"
              className="ob__link"
              onClick={() => {
                setStep("phone");
                setSessionId(null);
              }}
            >
              <ArrowLeft size={12} /> change
            </button>
          </div>
          <label className="field">
            <span className="field__label">Verification code</span>
            <input
              className="input mono"
              autoFocus
              inputMode="numeric"
              placeholder="12345"
              {...codeForm.register("code")}
            />
            {codeForm.formState.errors.code && (
              <span className="ob__err">{codeForm.formState.errors.code.message}</span>
            )}
          </label>
          <label className="field">
            <span className="field__label">Account name</span>
            <input
              className="input"
              placeholder="Sales account 1"
              {...codeForm.register("name")}
            />
            {codeForm.formState.errors.name && (
              <span className="ob__err">{codeForm.formState.errors.name.message}</span>
            )}
            <span className="field__hint">Only visible inside your aimly workspace.</span>
          </label>
          <button className="btn btn--primary" type="submit" disabled={busy}>
            {busy && <Loader2 size={14} className="ob__spin" />} Verify &amp; connect
          </button>
        </form>
      )}

      {step === "2fa" && (
        <form className="ob__form" onSubmit={twofaForm.handleSubmit(submit2fa)}>
          <label className="field">
            <span className="field__label">Two-step verification password</span>
            <input
              className="input"
              type="password"
              autoFocus
              autoComplete="off"
              {...twofaForm.register("password")}
            />
            {twofaForm.formState.errors.password && (
              <span className="ob__err">{twofaForm.formState.errors.password.message}</span>
            )}
            <span className="field__hint">
              This account has Telegram&apos;s cloud password enabled. We never store it.
            </span>
          </label>
          <button className="btn btn--primary" type="submit" disabled={busy}>
            {busy && <Loader2 size={14} className="ob__spin" />} Unlock &amp; connect
          </button>
        </form>
      )}

      {step === "done" && (
        <div className="ob__success">
          <div className="ob__successIcon">
            <CheckCircle2 size={36} />
          </div>
          <h3 className="ob__successTitle">Account connected</h3>
          <p className="muted">
            {accountName || phone} is warming up. We&apos;ll throttle sends at 4/min · 20/hr · 150/day
            until Telegram trusts it.
          </p>
        </div>
      )}
    </div>
  );
}

/* ---------------- QR flow ---------------- */
function QrFlow({ onComplete }: { onComplete?: Props["onComplete"] }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("idle");
  const [busy, setBusy] = useState(false);
  const polling = useRef<number | null>(null);

  async function startQr() {
    setBusy(true);
    try {
      const res = await api<{ session_id: string; qr_code?: string; qr_url?: string }>(
        "/api/v1/onboarding/qr-start",
        { method: "POST", body: { role: "sender" } },
      );
      setSessionId(res.session_id);
      setQrUrl(res.qr_code ?? res.qr_url ?? null);
      setStatus("waiting");
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!sessionId) return;
    polling.current = window.setInterval(async () => {
      try {
        const res = await api<{
          status: string;
          qr_code?: string;
          sender?: SenderResponse;
        }>(`/api/v1/onboarding/qr-status/${sessionId}`);
        if (res.qr_code) setQrUrl(res.qr_code);
        setStatus(res.status);
        if (res.status === "ok" || res.status === "completed") {
          if (polling.current) clearInterval(polling.current);
          const sender = res.sender;
          track("sender_added", { sender_id: sender?.id ?? sessionId, method: "qr" });
          onComplete?.(sender ?? { slug: undefined });
        }
        if (res.status === "expired" || res.status === "failed") {
          if (polling.current) clearInterval(polling.current);
        }
      } catch {
        /* network blip — keep polling */
      }
    }, 2000);
    return () => {
      if (polling.current) clearInterval(polling.current);
    };
  }, [sessionId, onComplete]);

  return (
    <div className="ob__panel">
      <ol className="ob__qrSteps">
        <li>Open Telegram on your phone.</li>
        <li>Go to <b>Settings → Devices → Link Desktop Device</b>.</li>
        <li>Point your camera at the code below.</li>
      </ol>
      <div className="ob__qrBox">
        {!qrUrl && !busy && (
          <button className="btn btn--primary" onClick={startQr} disabled={busy}>
            <QrCode size={14} /> Generate QR code
          </button>
        )}
        {busy && <Loader2 size={20} className="ob__spin" />}
        {qrUrl && (
          <>
            <img src={qrUrl} alt="Telegram link QR code" className="ob__qrImg" />
            <p className="muted text-sm">
              {status === "ok" || status === "completed"
                ? "Linked!"
                : status === "expired"
                  ? "Code expired — generate a new one."
                  : "Waiting for scan…"}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/* ---------------- Stepper ---------------- */
function Stepper({ step }: { step: PhoneStep }) {
  const steps: { key: PhoneStep; label: string; Icon: typeof Phone }[] = [
    { key: "phone", label: "Phone", Icon: Phone },
    { key: "code", label: "Verify", Icon: ShieldCheck },
    { key: "done", label: "Done", Icon: CheckCircle2 },
  ];
  const activeIdx = step === "2fa" ? 1 : steps.findIndex((s) => s.key === step);
  return (
    <ol className="ob__stepper" aria-label="Onboarding steps">
      {steps.map((s, i) => {
        const state = i < activeIdx ? "done" : i === activeIdx ? "active" : "todo";
        return (
          <li key={s.key} className={`ob__step is-${state}`}>
            <span className="ob__stepDot">
              <s.Icon size={12} />
            </span>
            <span className="ob__stepLabel">{s.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
