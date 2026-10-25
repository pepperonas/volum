import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/engine";
import { bytes } from "../lib/format";
import { asEngineError, useAsync } from "../lib/hooks";
import { Badge, Button, Card, Failure, Progress, Row, Spinner, type BadgeTone } from "../components/ui";
import { DownloadIcon, TrashIcon } from "../components/icons";
import type { InstallTask, ModelEntry, ToolEntry, Verdict } from "../lib/types";

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
  const tools = useAsync("tools", () => api.tools());
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<ReturnType<typeof asEngineError> | null>(null);
  const [task, setTask] = useState<InstallTask | null>(null);
  const abort = useRef<AbortController | null>(null);

  // A download runs in the engine, not in this window; closing the screen must
  // not cancel it, but the stream this screen opened has to be let go.
  useEffect(() => () => abort.current?.abort(), []);

  // Keyed by id, not by entry: one pair of callbacks serves both catalogues,
  // because installing a tool is the same machinery as installing a model.
  const install = useCallback(
    async (id: string) => {
      setFailure(null);
      setBusy(id);
      try {
        setTask(await api.installModel(id));
        abort.current?.abort();
        const controller = new AbortController();
        abort.current = controller;
        await api.installEvents(id, setTask, controller.signal);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setFailure(asEngineError(error));
        }
      } finally {
        setBusy(null);
        models.reload();
        tools.reload();
      }
    },
    [models, tools],
  );

  const remove = useCallback(
    async (id: string) => {
      setFailure(null);
      setBusy(id);
      try {
        await api.removeModel(id);
      } catch (error) {
        setFailure(asEngineError(error));
      } finally {
        setBusy(null);
        models.reload();
        tools.reload();
      }
    },
    [models, tools],
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
            onInstall={() => void install(entry.metadata.id)}
            onRemove={() => void remove(entry.metadata.id)}
          />
        ))}
      </div>

      {tools.value && tools.value.length > 0 && (
        <>
          <header className="pb-4 pt-9">
            <h2 className="text-[15px] font-semibold tracking-tight">Preprocessing</h2>
            <p className="mt-1 text-[13px] text-ink-muted">
              Not generators — nothing here makes a mesh. Listed apart so the catalogue above
              stays a list of things you can generate with.
            </p>
          </header>
          <div className="space-y-5">
            {tools.value.map((entry) => (
              <ToolCard
                key={entry.metadata.id}
                entry={entry}
                busy={busy === entry.metadata.id}
                task={task?.model_id === entry.metadata.id ? task : null}
                onInstall={() => void install(entry.metadata.id)}
                onRemove={() => void remove(entry.metadata.id)}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function ToolCard({
  entry,
  busy,
  task,
  onInstall,
  onRemove,
}: {
  entry: ToolEntry;
  busy: boolean;
  task: InstallTask | null;
  onInstall: () => void;
  onRemove: () => void;
}) {
  const { metadata, install_state, disk_bytes } = entry;
  const installed = install_state === "installed";
  return (
    <Card
      title={
        // Badges in the title line, as on a model card: two kinds of card in
        // one list should not read their state in two different places.
        <span className="flex items-center gap-2.5">
          {metadata.name}
          {installed && <Badge tone="ok">installed</Badge>}
          {install_state === "incomplete" && <Badge tone="warn">unfinished install</Badge>}
          <Badge tone={COMMERCIAL_TONE[metadata.license.commercial_use] ?? "neutral"}>
            {metadata.license.commercial_use.replace("_", " ")}
          </Badge>
        </span>
      }
      subtitle={metadata.description}
      actions={
        installed ? (
          <Button tone="quiet" onClick={onRemove} disabled={busy}>
            <TrashIcon className="text-[15px]" />
            Remove
          </Button>
        ) : (
          <Button tone="primary" onClick={onInstall} disabled={busy}>
            {busy ? <Spinner /> : <DownloadIcon className="text-[15px]" />}
            {busy ? "Installing…" : `Install · ${bytes(metadata.disk_bytes)}`}
          </Button>
        )
      }
    >
      <dl className="space-y-1.5">
        <Row label="Stage">{metadata.stage}</Row>
        {installed && disk_bytes > 0 && (
          <Row label="On disk">
            <span className="num">{bytes(disk_bytes)}</span>
          </Row>
        )}
        <Row label="Licence">
          {metadata.license.code_license === metadata.license.weights_license
            ? metadata.license.code_license
            : `${metadata.license.code_license} / ${metadata.license.weights_license}`}
        </Row>
      </dl>

      {/* The figures behind the numbers above, as everywhere else: a size or a
          timing nobody can trace is a claim, not a measurement. */}
      <p className="mt-3 text-[12px] text-ink-faint">{metadata.numbers_source}</p>

      {task && task.state === "running" && (
        <div className="mt-4">
          <p className="mb-2 text-[12px] text-ink-muted">{task.message || task.step}</p>
          {/* Indeterminate for the same reason as a model's: the hub reports no
              overall figure for a multi-file download, and a made-up percentage
              would be worse than an honest sweep. */}
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
    </Card>
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
