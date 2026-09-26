import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/engine";
import { bytes } from "../lib/format";
import { asEngineError, useAsync } from "../lib/hooks";
import { Badge, Button, Card, Failure, Progress, Row, Spinner, type BadgeTone } from "../components/ui";
import { DownloadIcon, TrashIcon } from "../components/icons";
import type { InstallTask, ModelEntry, Verdict } from "../lib/types";

const VERDICT_TONE: Record<Verdict, BadgeTone> = {
  runnable: "ok",
  marginal: "warn",
  blocked: "bad",
  unknown: "neutral",
};

const COMMERCIAL_TONE: Record<string, BadgeTone> = {
  allowed: "ok",
  conditional: "warn",
  not_allowed: "bad",
  unknown: "warn",
};

export default function Models() {
  const models = useAsync("models", () => api.models());
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<ReturnType<typeof asEngineError> | null>(null);
  const [task, setTask] = useState<InstallTask | null>(null);
  const abort = useRef<AbortController | null>(null);

  // A download runs in the engine, not in this window; closing the screen must
  // not cancel it, but the stream this screen opened has to be let go.
  useEffect(() => () => abort.current?.abort(), []);

  const install = useCallback(
    async (entry: ModelEntry) => {
      setFailure(null);
      setBusy(entry.metadata.id);
      try {
        setTask(await api.installModel(entry.metadata.id));
        abort.current?.abort();
        const controller = new AbortController();
        abort.current = controller;
        await api.installEvents(entry.metadata.id, setTask, controller.signal);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setFailure(asEngineError(error));
        }
      } finally {
        setBusy(null);
        models.reload();
      }
    },
    [models],
  );

  const remove = useCallback(
    async (entry: ModelEntry) => {
      setFailure(null);
      setBusy(entry.metadata.id);
      try {
        await api.removeModel(entry.metadata.id);
      } catch (error) {
        setFailure(asEngineError(error));
      } finally {
        setBusy(null);
        models.reload();
      }
    },
    [models],
  );

  return (
    <>
      <header className="py-6">
        <h1 className="text-xl font-semibold tracking-tight">Models</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          Nothing is downloaded until you ask for it. Every licence — including the restrictive
          ones inside a model’s own pipeline — is listed before you do.
        </p>
      </header>

      {failure && (
        <div className="mb-5">
          <Failure
            message={failure.message}
            technical={failure.technical}
            suggestions={failure.suggestions}
          />
        </div>
      )}

      {models.state === "failed" && (
        <Failure
          message={models.error.message}
          technical={models.error.technical}
          suggestions={models.error.suggestions}
        />
      )}

      {!models.value && models.state === "loading" && (
        <div className="flex items-center gap-2 py-16 text-[13px] text-ink-muted">
          <Spinner /> Reading the catalogue…
        </div>
      )}

      <div className="space-y-5">
        {models.value?.map((entry) => (
          <ModelCard
            key={entry.metadata.id}
            entry={entry}
            busy={busy === entry.metadata.id}
            task={task?.model_id === entry.metadata.id ? task : null}
            onInstall={() => void install(entry)}
            onRemove={() => void remove(entry)}
          />
        ))}
      </div>
    </>
  );
}

function ModelCard({
  entry,
  busy,
  task,
  onInstall,
  onRemove,
}: {
  entry: ModelEntry;
  busy: boolean;
  task: InstallTask | null;
  onInstall: () => void;
  onRemove: () => void;
}) {
  const { metadata, assessment, install_state, disk_bytes, has_implementation } = entry;
  const licence = metadata.license;
  const installed = install_state === "installed";
  const running = busy || task?.state === "running";

  return (
    <Card
      title={
        <span className="flex items-center gap-2.5">
          {metadata.name}
          <Badge tone={VERDICT_TONE[assessment.verdict]}>{assessment.verdict}</Badge>
          {installed && <Badge tone="ok">installed</Badge>}
          {install_state === "incomplete" && <Badge tone="warn">unfinished install</Badge>}
        </span>
      }
      subtitle={metadata.description}
      actions={
        installed ? (
          <Button tone="quiet" onClick={onRemove} disabled={running}>
            <TrashIcon className="text-[15px]" />
            Remove
          </Button>
        ) : (
          <Button
            tone="primary"
            onClick={onInstall}
            disabled={running || !has_implementation || assessment.verdict === "blocked"}
          >
            {running ? <Spinner /> : <DownloadIcon className="text-[15px]" />}
            {running ? "Installing…" : `Install · ${bytes(metadata.requirements.disk_bytes)}`}
          </Button>
        )
      }
    >
      {task && task.state !== "done" && (
        <div className="mb-4">
          <div className="mb-1.5 flex items-baseline justify-between gap-3 text-[12px]">
            <span className="text-ink-muted">{task.message || task.step}</span>
            <span className="text-ink-faint">{task.step}</span>
          </div>
          {/* Hugging Face reports no overall figure for a multi-file download,
              so this is an indeterminate bar rather than an invented number. */}
          <Progress fraction={null} />
          {task.error && (
            <div className="mt-3">
              <Failure
                message={task.error.message}
                technical={task.error.technical}
                suggestions={task.error.suggestions}
              />
            </div>
          )}
        </div>
      )}

      {!has_implementation && (
        <p className="mb-3 text-[12px] text-warn">
          Documented and licence-checked, but this build has no implementation for it yet.
        </p>
      )}

      <div className="grid gap-x-8 gap-y-0 md:grid-cols-2">
        <dl>
          <Row label="Version">
            <span className="num">{metadata.version}</span>
          </Row>
          <Row label="Can do">{metadata.capabilities.join(", ")}</Row>
          <Row label="Needs">
            <span className="num">{bytes(metadata.requirements.estimated_peak_memory_bytes)}</span>
            <span className="ml-1.5 text-ink-faint">peak memory</span>
          </Row>
          <Row label="Download">
            <span className="num">{bytes(metadata.requirements.disk_bytes)}</span>
            {installed && disk_bytes > 0 && (
              <span className="num ml-2 text-ink-faint">{bytes(disk_bytes)} on disk</span>
            )}
          </Row>
          {metadata.requirements.requires_gated_download && (
            <Row label="Gated">
              <span className="text-warn">
                needs a Hugging Face account and accepted terms
              </span>
            </Row>
          )}
        </dl>

        <dl>
          <Row label="Code licence">{licence.code_license}</Row>
          <Row label="Weights licence">{licence.weights_license}</Row>
          <Row label="Commercial use">
            <Badge tone={COMMERCIAL_TONE[licence.commercial_use] ?? "warn"}>
              {licence.commercial_use.replace("_", " ")}
            </Badge>
          </Row>
          {licence.territorial_restriction && (
            <Row label="Territory">
              <span className="text-bad">{licence.territorial_restriction}</span>
            </Row>
          )}
          {licence.attribution_required && (
            <Row label="Attribution">
              must display “{licence.attribution_required}”
            </Row>
          )}
          {licence.verified_on && (
            <Row label="Licence read on">
              <span className="num">{licence.verified_on}</span>
            </Row>
          )}
        </dl>
      </div>

      {licence.commercial_use_detail && (
        <p className="mt-3 border-t border-hairline pt-3 text-[12px] text-ink-muted">
          {licence.commercial_use_detail}
        </p>
      )}

      {Object.keys(licence.dependency_licenses).length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[12px] text-ink-faint">
            Licences inside this model’s pipeline (
            {Object.keys(licence.dependency_licenses).length})
          </summary>
          <ul className="mt-2 space-y-0.5">
            {Object.entries(licence.dependency_licenses).map(([name, text]) => {
              const flagged = /NON-COMMERCIAL|UNKNOWN/.test(text);
              return (
                <li key={name} className="text-[12px]">
                  <span className="text-ink">{name}</span>{" "}
                  <span className={flagged ? "text-bad" : "text-ink-faint"}>{text}</span>
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {assessment.reasons.length > 0 && (
        <ul className="mt-3 space-y-0.5 border-t border-hairline pt-3">
          {assessment.reasons.map((reason, index) => (
            <li
              key={`${metadata.id}-reason-${index}`}
              className={reason.blocking ? "text-[12px] text-bad" : "text-[12px] text-ink-muted"}
            >
              {reason.message}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
