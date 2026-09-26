import { api } from "../lib/engine";
import { bytes } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Badge, Card, Failure, Row, Spinner, type BadgeTone } from "../components/ui";
import { AlertIcon } from "../components/icons";
import type { Availability, Verdict } from "../lib/types";

const VERDICT_TONE: Record<Verdict, BadgeTone> = {
  runnable: "ok",
  marginal: "warn",
  blocked: "bad",
  unknown: "neutral",
};

const AVAILABILITY_TONE: Record<Availability, BadgeTone> = {
  available: "ok",
  expected: "warn",
  unavailable: "bad",
  unknown: "neutral",
};

/**
 * `available` and `expected` are not the same claim, and the difference is the
 * whole reason the engine has four states instead of two: one was measured on
 * this machine, the other is what the hardware says should work once the
 * software is installed. Collapsing them into "yes" is how a doctor starts
 * lying.
 */
const AVAILABILITY_WORD: Record<Availability, string> = {
  available: "available",
  expected: "expected",
  unavailable: "not available",
  unknown: "unknown",
};

export default function System() {
  const report = useAsync("doctor", () => api.doctor(true));

  if (report.state === "failed") {
    return (
      <Screen>
        <Failure
          message={report.error.message}
          technical={report.error.technical}
          suggestions={report.error.suggestions}
        />
      </Screen>
    );
  }
  if (!report.value) {
    return (
      <Screen>
        <div className="flex items-center gap-2 py-16 text-[13px] text-ink-muted">
          <Spinner /> Looking at this machine…
        </div>
      </Screen>
    );
  }

  const { hardware, assessments, warnings, recommended_runtime, volum_version } = report.value;
  const gpu = hardware.gpus[0];

  return (
    <Screen>
      {warnings.length > 0 && (
        <div className="mb-5 space-y-2">
          {warnings.map((warning) => (
            <div
              key={warning}
              className="flex items-start gap-2.5 rounded-[var(--radius-card)] border border-[color-mix(in_srgb,var(--color-warn)_30%,transparent)] bg-[color-mix(in_srgb,var(--color-warn)_8%,transparent)] px-4 py-2.5 text-[13px] text-warn"
            >
              <AlertIcon className="mt-0.5 shrink-0 text-[15px]" />
              <span>{warning}</span>
            </div>
          ))}
        </div>
      )}

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Card title="This machine">
          <dl>
            <Row label="Operating system">
              {hardware.os} {hardware.os_version}
            </Row>
            <Row label="Architecture">
              {hardware.arch}
              {hardware.is_apple_silicon && <span className="ml-2 text-ink-faint">Apple Silicon</span>}
            </Row>
            <Row label="Processor">
              {hardware.cpu_name}
              {hardware.cpu_cores_physical !== null && (
                <span className="num ml-2 text-ink-faint">
                  {hardware.cpu_cores_physical} cores
                </span>
              )}
            </Row>
            <Row label="Graphics">
              {gpu ? gpu.name : "unknown"}
              {gpu?.unified_memory && <span className="ml-2 text-ink-faint">unified memory</span>}
            </Row>
            <Row label="Memory">
              <span className="num">{bytes(hardware.ram_total_bytes)}</span>
              {hardware.ram_available_bytes !== null && (
                <span className="num ml-2 text-ink-faint">
                  {bytes(hardware.ram_available_bytes)} free
                </span>
              )}
            </Row>
            <Row label="Disk">
              <span className="num">{bytes(hardware.disk_free_bytes)}</span>
              <span className="ml-2 text-ink-faint">free for models</span>
            </Row>
          </dl>
        </Card>

        <Card
          title="Runtimes"
          subtitle={`VOLUM will use ${recommended_runtime} on this machine`}
        >
          <ul className="space-y-3">
            {hardware.runtimes.map((runtime) => (
              <li key={runtime.kind}>
                {/* Name and verdict on one line, the reasoning under it: the
                    explanations are sentences, and squeezing them between the
                    two made both hard to read. */}
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[12px] uppercase tracking-wide">{runtime.kind}</span>
                  <span className="shrink-0 whitespace-nowrap">
                    <Badge tone={AVAILABILITY_TONE[runtime.availability]}>
                      {AVAILABILITY_WORD[runtime.availability]}
                    </Badge>
                  </span>
                </div>
                {runtime.detail && (
                  <p className="mt-0.5 text-[11px] text-ink-faint">{runtime.detail}</p>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-4 border-t border-hairline pt-3 text-[11px] text-ink-faint">
            “expected” means the hardware supports it but nothing has been able to prove it yet —
            the machine learning stack lives in each model’s own environment, so VOLUM cannot
            confirm it until a model is installed.
          </p>
        </Card>
      </div>

      <Card className="mt-5" title="What this machine can run">
        <ul className="divide-y divide-hairline">
          {assessments.map((assessment) => (
            <li key={assessment.provider_id} className="flex items-start gap-4 py-2.5 first:pt-0">
              <span className="w-28 shrink-0 text-[13px]">{assessment.provider_id}</span>
              <div className="min-w-0 flex-1 space-y-0.5">
                {assessment.reasons.map((reason, index) => (
                  <p
                    key={`${assessment.provider_id}-${index}`}
                    className="text-[12px] text-ink-muted"
                  >
                    {reason.message}
                  </p>
                ))}
              </div>
              <Badge tone={VERDICT_TONE[assessment.verdict]}>{assessment.verdict}</Badge>
            </li>
          ))}
        </ul>
      </Card>

      <p className="mt-5 text-[11px] text-ink-faint">
        VOLUM <span className="num">{volum_version}</span>
      </p>
    </Screen>
  );
}

function Screen({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="py-6">
        <h1 className="text-xl font-semibold tracking-tight">System</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          What VOLUM found on this machine, and what it can honestly run here.
        </p>
      </header>
      {children}
    </>
  );
}
