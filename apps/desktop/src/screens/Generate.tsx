import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import type { Navigate } from "../App";
import { api } from "../lib/engine";
import { duration, elapsed, fileName, stageWord } from "../lib/format";
import { asEngineError, useAsync, useTicker } from "../lib/hooks";
import { Badge, Button, Card, Failure, Progress, Spinner, cx } from "../components/ui";
import { AlertIcon, FolderIcon, PrinterIcon, StopIcon } from "../components/icons";
import {
  isTerminal,
  type FrameMeasurement,
  type FrameSelection,
  type JobRecord,
  type ModelEntry,
} from "../lib/types";

/** What the result is *for*. It decides formats, repair and how strict validation is. */
type Target = "print" | "render";

const IMAGE_FILTER = { name: "Images", extensions: ["png", "jpg", "jpeg", "webp"] };

export default function Generate({ navigate }: { navigate: Navigate }) {
  const models = useAsync("models", () => api.models());
  const [images, setImages] = useState<string[]>([]);
  const [chosenModelId, setChosenModelId] = useState<string | null>(null);
  const [target, setTarget] = useState<Target>("print");
  const [sizeMm, setSizeMm] = useState(60);
  const [singlePart, setSinglePart] = useState(true);
  const [seed, setSeed] = useState<string>("");
  const [job, setJob] = useState<JobRecord | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [frames, setFrames] = useState<FrameSelection | null>(null);
  const [measuring, setMeasuring] = useState(false);
  const [failure, setFailure] = useState<ReturnType<typeof asEngineError> | null>(null);
  const abort = useRef<AbortController | null>(null);

  const installed = useMemo(
    () => (models.value ?? []).filter((m) => m.install_state === "installed" && m.has_implementation),
    [models.value],
  );

  // Derived, not stored: the first installed model stands in until one is
  // picked. Writing it into state from an effect would be a second render for
  // something already known while rendering the first.
  const modelId = chosenModelId ?? installed[0]?.metadata.id ?? null;

  const addImages = useCallback((paths: string[]) => {
    const wanted = paths.filter((path) => /\.(png|jpe?g|webp)$/i.test(path));
    if (wanted.length === 0) return;
    // Any measurement describes the set it was made from, so changing the set
    // discards it rather than leaving a stale ranking on screen.
    setFrames(null);
    setImages((current) => [...new Set([...current, ...wanted])]);
  }, []);

  // Dropping a file on the window gives real paths; a webview file input does not.
  useEffect(() => {
    const unlisten = getCurrentWebview().onDragDropEvent((event) => {
      if (event.payload.type === "drop") addImages(event.payload.paths);
    });
    return () => {
      void unlisten.then((stop) => stop());
    };
  }, [addImages]);

  useEffect(() => () => abort.current?.abort(), []);

  const choose = useCallback(async () => {
    const picked = await open({ multiple: true, filters: [IMAGE_FILTER] });
    if (picked) addImages(Array.isArray(picked) ? picked : [picked]);
  }, [addImages]);

  // Asking before committing: the measurement takes under a second, the
  // generation it informs takes minutes.
  const measure = useCallback(async () => {
    setMeasuring(true);
    try {
      setFrames(await api.analyse(images));
    } catch (error) {
      setFailure(asEngineError(error));
    } finally {
      setMeasuring(false);
    }
  }, [images]);

  const start = useCallback(async () => {
    if (!modelId || images.length === 0) return;
    setFailure(null);
    setWarnings([]);
    try {
      const submission = await api.createJob({
        model_id: modelId,
        images,
        seed: seed.trim() === "" ? null : Number(seed),
        for_print: target === "print",
        target_size_mm: target === "print" ? sizeMm : null,
        single_part: target === "print" && singlePart,
      });
      setWarnings(submission.warnings);
      setJob(submission.job);

      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      await api.jobEvents(submission.job.id, setJob, controller.signal);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setFailure(asEngineError(error));
      }
    }
  }, [images, modelId, seed, singlePart, sizeMm, target]);

  const cancel = useCallback(async () => {
    if (!job) return;
    try {
      setJob(await api.cancelJob(job.id));
    } catch (error) {
      setFailure(asEngineError(error));
    }
  }, [job]);

  if (job && !isTerminal(job.status)) {
    return <Running job={job} warnings={warnings} onCancel={() => void cancel()} />;
  }

  if (job?.status === "completed") {
    navigate({ name: "job", id: job.id });
    return null;
  }

  const chosen = installed.find((m) => m.metadata.id === modelId);
  const ready = images.length > 0 && modelId !== null;

  return (
    <>
      <header className="py-6">
        <h1 className="text-xl font-semibold tracking-tight">Generate</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          A photograph in, a 3D model out. Everything happens on this machine.
        </p>
      </header>

      {/* Anything still here is a job that failed or was cancelled: the two
          returns above have already taken the running and completed cases. */}
      {job && (
        <div className="mb-5">
          {job.error ? (
            <Failure
              message={job.error.message}
              technical={job.error.technical}
              suggestions={job.error.suggestions}
            />
          ) : (
            <p className="text-[13px] text-ink-muted">This job was {job.status}.</p>
          )}
        </div>
      )}

      {failure && (
        <div className="mb-5">
          <Failure
            message={failure.message}
            technical={failure.technical}
            suggestions={failure.suggestions}
          />
        </div>
      )}

      <div className="space-y-5">
        <Card
          title="Images"
          subtitle="A photograph with a clean, transparent or plain background works best."
          actions={
            <Button onClick={() => void choose()}>
              <FolderIcon className="text-[15px]" />
              Choose…
            </Button>
          }
        >
          {images.length === 0 ? (
            <div className="rounded-md border border-dashed border-hairline-strong px-5 py-10 text-center">
              <p className="text-[13px] text-ink-muted">Drop images here, or choose them.</p>
              <p className="mt-1 text-[12px] text-ink-faint">PNG, JPEG or WebP.</p>
            </div>
          ) : (
            <ul className="space-y-1">
              {images.map((path, index) => {
                // Matched by position, not by name: two files picked from
                // different folders can share a basename.
                const measured = frames?.frames[index];
                const isChosen = frames?.chosen_index === index;
                return (
                  <li key={path} className="rounded-md bg-raised px-3 py-1.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-[13px]" title={path}>
                          {fileName(path)}
                        </span>
                        {isChosen && images.length > 1 && <Badge tone="ok">will be used</Badge>}
                        {measured?.error && <Badge tone="bad">unreadable</Badge>}
                      </span>
                      <Button
                        tone="quiet"
                        className="px-1.5 py-0.5 text-[11px]"
                        onClick={() => {
                          setFrames(null);
                          setImages((current) => current.filter((p) => p !== path));
                        }}
                      >
                        Remove
                      </Button>
                    </div>
                    {measured && <FrameDetail frame={measured} />}
                  </li>
                );
              })}
            </ul>
          )}

          {images.length > 1 && chosen && !chosen.metadata.capabilities.includes("multi_image") && (
            <div className="mt-3 space-y-2">
              <p className="flex items-start gap-2 text-[12px] text-warn">
                <AlertIcon className="mt-0.5 shrink-0 text-[14px]" />
                {chosen.metadata.name} reconstructs from one image. All {images.length} will be
                measured and the best one used — they are not combined.
              </p>
              {frames ? (
                <p className="text-[12px] text-ink-faint">{frames.reason}</p>
              ) : (
                <Button
                  tone="quiet"
                  className="px-1.5 py-0.5 text-[12px]"
                  disabled={measuring}
                  onClick={() => void measure()}
                >
                  {measuring ? <Spinner /> : null}
                  {measuring ? "Measuring…" : "Measure them now"}
                </Button>
              )}
            </div>
          )}
        </Card>

        <Card title="Model">
          {installed.length === 0 ? (
            <p className="text-[13px] text-ink-muted">
              No model is installed yet. Install one under <span className="text-ink">Models</span>.
            </p>
          ) : (
            <div className="space-y-2">
              {installed.map((entry) => (
                <ModelChoice
                  key={entry.metadata.id}
                  entry={entry}
                  selected={entry.metadata.id === modelId}
                  onSelect={() => setChosenModelId(entry.metadata.id)}
                />
              ))}
            </div>
          )}
        </Card>

        <Card
          title="What it is for"
          subtitle="This is not a preference — it changes what VOLUM does to the mesh."
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <TargetChoice
              selected={target === "print"}
              onSelect={() => setTarget("print")}
              title="A 3D printer"
              body="Repairs the surface into one closed solid, exports STL and 3MF at a real size, and fails the job if it is not printable."
              icon={<PrinterIcon className="text-[18px]" />}
            />
            <TargetChoice
              selected={target === "render"}
              onSelect={() => setTarget("render")}
              title="A render or a game"
              body="Exports GLB with whatever surface the model produced. Holes are a blemish here, not a failure."
              icon={<span className="text-[18px]">◻</span>}
            />
          </div>

          {target === "print" && (
            <div className="mt-4 space-y-3 border-t border-hairline pt-4">
              <label className="flex items-center gap-4">
                <span className="w-44 shrink-0 text-[12px] text-ink-muted">Longest side</span>
                <input
                  type="range"
                  min={10}
                  max={250}
                  step={5}
                  value={sizeMm}
                  onChange={(event) => setSizeMm(Number(event.target.value))}
                  className="flex-1 accent-[var(--color-accent)]"
                />
                <span className="num w-16 text-right text-[13px]">{sizeMm} mm</span>
              </label>
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={singlePart}
                  onChange={(event) => setSinglePart(event.target.checked)}
                  className="mt-0.5 accent-[var(--color-accent)]"
                />
                <span>
                  <span className="text-[13px]">Keep only the largest piece</span>
                  <span className="block text-[12px] text-ink-faint">
                    Generated models routinely carry loose fragments that a slicer would print as
                    separate objects.
                  </span>
                </span>
              </label>
            </div>
          )}
        </Card>

        <details className="rounded-[var(--radius-card)] border border-hairline bg-surface px-5 py-3.5">
          <summary className="cursor-pointer text-[13px] font-semibold tracking-wide">
            Advanced
          </summary>
          <label className="mt-3 flex items-center gap-4">
            <span className="w-44 shrink-0 text-[12px] text-ink-muted">Seed</span>
            <input
              value={seed}
              onChange={(event) => setSeed(event.target.value.replace(/\D/g, ""))}
              placeholder="random"
              inputMode="numeric"
              className="num w-40 rounded-md border border-hairline-strong bg-ground px-2.5 py-1 text-[13px] selectable"
            />
          </label>
          <p className="mt-2 text-[12px] text-ink-faint">
            The same seed on the same machine gives the same model. Across machines it does not:
            the graphics kernels differ, and the Apple Silicon path substitutes several of them.
          </p>
        </details>

        <div className="flex items-center justify-end gap-3 pb-4">
          {!ready && (
            <span className="text-[12px] text-ink-faint">
              {images.length === 0 ? "Choose at least one image." : "Install a model first."}
            </span>
          )}
          <Button tone="primary" disabled={!ready} onClick={() => void start()} className="px-5 py-2">
            Generate
          </Button>
        </div>
      </div>
    </>
  );
}

function ModelChoice({
  entry,
  selected,
  onSelect,
}: {
  entry: ModelEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  const marginal = entry.assessment.verdict === "marginal";
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cx(
        "flex w-full items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
        selected ? "border-accent bg-raised" : "border-hairline hover:border-hairline-strong",
      )}
    >
      <span
        className={cx(
          "mt-1 size-2.5 shrink-0 rounded-full border",
          selected ? "border-accent bg-accent" : "border-hairline-strong",
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-[13px]">
          {entry.metadata.name}
          {marginal && <Badge tone="warn">tight on this machine</Badge>}
        </span>
        <span className="mt-0.5 block text-[12px] text-ink-faint">{entry.metadata.description}</span>
      </span>
    </button>
  );
}

function TargetChoice({
  selected,
  onSelect,
  title,
  body,
  icon,
}: {
  selected: boolean;
  onSelect: () => void;
  title: string;
  body: string;
  icon: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cx(
        "rounded-md border px-3.5 py-3 text-left transition-colors",
        selected ? "border-accent bg-raised" : "border-hairline hover:border-hairline-strong",
      )}
    >
      <span className={cx("flex items-center gap-2 text-[13px]", selected && "text-accent")}>
        {icon}
        {title}
      </span>
      <span className="mt-1.5 block text-[12px] text-ink-faint">{body}</span>
    </button>
  );
}

/**
 * A job in flight.
 *
 * Stages, never a percentage: the providers do not report one, and spec §18
 * forbids inventing it. The elapsed time is real and ticks, which is what
 * actually answers "is this still going?".
 */
function Running({
  job,
  warnings,
  onCancel,
}: {
  job: JobRecord;
  warnings: string[];
  onCancel: () => void;
}) {
  const now = useTicker(true);
  const seconds = elapsed(job.started_at, job.finished_at, now);
  const latest = job.progress[job.progress.length - 1];

  const STAGES = ["preprocessing", "reconstructing", "optimizing", "validating"] as const;
  const reached = new Set(job.progress.map((entry) => entry.stage));
  const currentIndex = STAGES.indexOf(job.status as (typeof STAGES)[number]);

  return (
    <>
      <header className="py-6">
        <h1 className="text-xl font-semibold tracking-tight">Working</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          <span className="num">{job.model_id}</span> on{" "}
          <span className="num">{job.runtime}</span> · job{" "}
          <span className="num">{job.id.slice(0, 12)}</span>
        </p>
      </header>

      {warnings.map((warning) => (
        <p key={warning} className="mb-3 flex items-start gap-2 text-[12px] text-warn">
          <AlertIcon className="mt-0.5 shrink-0 text-[14px]" />
          {warning}
        </p>
      ))}

      <Card>
        <div className="mb-5 flex items-baseline justify-between gap-4">
          <span className="flex items-center gap-2.5 text-[15px]">
            <Spinner />
            {stageWord(job.status)}
          </span>
          <span className="num text-[15px] text-ink-muted">{duration(seconds)}</span>
        </div>

        <Progress fraction={latest?.fraction ?? null} />

        <ol className="mt-5 space-y-2">
          {STAGES.map((stage, index) => {
            const done = currentIndex > index || reached.has(stage);
            const active = job.status === stage;
            return (
              <li
                key={stage}
                className={cx(
                  "flex items-center gap-2.5 text-[13px]",
                  active ? "text-ink" : done ? "text-ink-muted" : "text-ink-faint",
                )}
              >
                <span
                  className={cx(
                    "size-1.5 rounded-full",
                    active ? "bg-accent" : done ? "bg-accent-dim" : "bg-hairline-strong",
                  )}
                />
                {stageWord(stage)}
              </li>
            );
          })}
        </ol>

        {latest?.message && (
          <p className="mt-5 border-t border-hairline pt-3 text-[12px] text-ink-faint">
            {latest.message}
          </p>
        )}
      </Card>

      <div className="mt-5 flex justify-end">
        <Button tone="danger" onClick={onCancel}>
          <StopIcon className="text-[15px]" />
          Cancel
        </Button>
      </div>
    </>
  );
}


/**
 * What was measured about one frame.
 *
 * Deliberately not a score bar: the score is only comparable inside this set,
 * so a bar out of ten would read as a verdict on the photograph. The numbers
 * that mean something on their own are the pixels, and the notes say the rest.
 */
function FrameDetail({ frame }: { frame: FrameMeasurement }) {
  if (frame.error) {
    return <p className="mt-1 text-[11px] text-bad">{frame.error}</p>;
  }
  const notes = [...frame.notes];
  if (frame.near_identical_to) notes.push(`Near-identical to ${frame.near_identical_to}.`);
  return (
    <div className="mt-1 space-y-0.5 text-[11px] text-ink-faint">
      <p className="num">
        {frame.width}&times;{frame.height} · detail {frame.detail.toFixed(3)}
        {frame.highlight_clipping > 0.005 &&
          ` · ${(frame.highlight_clipping * 100).toFixed(0)}% blown out`}
      </p>
      {notes.length > 0 && <p>{notes.join(" ")}</p>}
    </div>
  );
}
