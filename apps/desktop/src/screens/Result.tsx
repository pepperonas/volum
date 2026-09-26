import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import type { Navigate } from "../App";
import { api, artifact } from "../lib/engine";
import { duration, elapsed, fileName, jobLabel } from "../lib/format";
import { asEngineError, useAsync } from "../lib/hooks";
import { Viewer, ViewerLoading } from "../components/Viewer";
import { Badge, Button, Card, Failure, Row, Spinner } from "../components/ui";
import { DownloadIcon } from "../components/icons";
import type { JobRecord } from "../lib/types";

interface QualityIssue {
  severity: "info" | "warning" | "fatal";
  message: string;
}

interface QualityReport {
  valid: boolean;
  vertices: number;
  triangles: number;
  dimensions: number[];
  watertight: boolean | null;
  manifold: boolean | null;
  solid_count: number | null;
  has_vertex_colors: boolean;
  checked_for_printing: boolean;
  issues: QualityIssue[];
}

interface AssetMetadata {
  model: string;
  model_version: string | null;
  runtime: string | null;
  seed: number | null;
  target_size_mm: number | null;
  repair_strategy: string | null;
  export_formats: string[];
  processing_time_seconds: number | null;
  source_image_sha256: string[];
  reproducibility_note: string;
  hardware: Record<string, unknown>;
}

/** Artifacts the window offers to save, in the order it offers them. */
const SAVEABLE: { key: string; label: string; extension: string }[] = [
  { key: "export_stl", label: "STL", extension: "stl" },
  { key: "export_3mf", label: "3MF", extension: "3mf" },
  { key: "mesh", label: "GLB", extension: "glb" },
  { key: "quality_report", label: "Quality report", extension: "json" },
  { key: "asset", label: "Asset metadata", extension: "json" },
];

export default function Result({ jobId, navigate }: { jobId: string; navigate: Navigate }) {
  const job = useAsync(`job:${jobId}`, () => api.job(jobId));

  return (
    <>
      <header className="flex items-end justify-between gap-4 py-6">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight">
            {job.value ? jobLabel(job.value) : "Result"}
          </h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            job <span className="num">{jobId.slice(0, 12)}</span>
          </p>
        </div>
        <Button tone="quiet" onClick={() => navigate({ name: "library" })}>
          ← Library
        </Button>
      </header>

      {job.state === "failed" && (
        <Failure
          message={job.error.message}
          technical={job.error.technical}
          suggestions={job.error.suggestions}
        />
      )}
      {!job.value && job.state === "loading" && (
        <div className="flex items-center gap-2 py-16 text-[13px] text-ink-muted">
          <Spinner /> Loading…
        </div>
      )}
      {job.value && <Completed job={job.value} />}
    </>
  );
}

function Completed({ job }: { job: JobRecord }) {
  const [meshUrl, setMeshUrl] = useState<string | null>(null);
  const [meshError, setMeshError] = useState<string | null>(null);

  const report = useAsync(`report:${job.id}`, () =>
    artifact(job.id, "quality_report").then((r) => r.json() as Promise<QualityReport>),
  );
  const metadata = useAsync(`asset:${job.id}`, () =>
    artifact(job.id, "asset").then((r) => r.json() as Promise<AssetMetadata>),
  );

  // The viewer needs a URL, and the engine needs a bearer token on every
  // request — so the model is fetched here and handed to three.js as a blob.
  useEffect(() => {
    let url: string | null = null;
    let alive = true;
    artifact(job.id, "mesh")
      .then((response) => response.blob())
      .then((blob) => {
        if (!alive) return;
        url = URL.createObjectURL(blob);
        setMeshUrl(url);
      })
      .catch((error: unknown) => {
        if (alive) setMeshError(asEngineError(error).message);
      });
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [job.id]);

  if (job.status !== "completed") {
    return job.error ? (
      <Failure
        message={job.error.message}
        technical={job.error.technical}
        suggestions={job.error.suggestions}
      />
    ) : (
      <Card>
        <p className="text-[13px] text-ink-muted">This job is {job.status}.</p>
      </Card>
    );
  }

  const quality = report.value;
  const available = SAVEABLE.filter((entry) => entry.key in job.artifacts);

  return (
    <div className="space-y-5">
      {meshError ? (
        <Failure message="The model could not be loaded." technical={meshError} />
      ) : meshUrl ? (
        <Viewer url={meshUrl} className="h-[26rem]" />
      ) : (
        <ViewerLoading className="h-[26rem]" />
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="The model">
          {quality ? (
            <dl>
              <Row label="Geometry">
                <span className="num">{quality.vertices.toLocaleString()}</span> vertices ·{" "}
                <span className="num">{quality.triangles.toLocaleString()}</span> triangles
              </Row>
              <Row label="Size">
                {/* The report measures the mesh as the model produced it. The
                    scale to millimetres happens on the way out, so labelling
                    these numbers "mm" because a print size was asked for would
                    be a straight untruth — it read "1.1 × 0.6 × 0.6 mm" for a
                    chair exported at 60 mm. */}
                <span className="num">
                  {quality.dimensions.map((d) => d.toFixed(2)).join(" × ")}
                </span>
                <span className="ml-1.5 text-ink-faint">model units</span>
              </Row>
              {job.target_size_mm !== null && (
                <Row label="Exported at">
                  <span className="num">{job.target_size_mm} mm</span>
                  <span className="ml-1.5 text-ink-faint">on its longest side</span>
                </Row>
              )}
              <Row label="Surface">
                <span className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={quality.watertight ? "ok" : "warn"}>
                    {quality.watertight ? "watertight" : "has holes"}
                  </Badge>
                  <Badge tone={quality.manifold ? "ok" : "warn"}>
                    {quality.manifold ? "manifold" : "non-manifold"}
                  </Badge>
                  {quality.solid_count !== null && (
                    <Badge tone={quality.solid_count === 1 ? "ok" : "warn"}>
                      {quality.solid_count === 1 ? "one piece" : `${quality.solid_count} pieces`}
                    </Badge>
                  )}
                </span>
              </Row>
              <Row label="Checked as">
                {quality.checked_for_printing ? "a printable solid" : "a render asset"}
              </Row>
            </dl>
          ) : (
            <p className="text-[13px] text-ink-muted">No quality report was written.</p>
          )}

          {quality && quality.issues.length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-hairline pt-3">
              {quality.issues.map((issue, index) => (
                <li
                  key={`${issue.severity}-${index}`}
                  className={`text-[12px] ${ISSUE_COLOUR[issue.severity]}`}
                >
                  {issue.message}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="How it was made">
          {metadata.value ? (
            <dl>
              <Row label="Model">
                <span className="num">{metadata.value.model}</span>
                {metadata.value.model_version && (
                  <span className="num ml-1.5 text-ink-faint">
                    {metadata.value.model_version}
                  </span>
                )}
              </Row>
              <Row label="Runtime">
                <span className="num">{metadata.value.runtime ?? "—"}</span>
              </Row>
              <Row label="Seed">
                <span className="num">{metadata.value.seed ?? "not fixed"}</span>
              </Row>
              <Row label="Took">
                <span className="num">
                  {duration(
                    metadata.value.processing_time_seconds ??
                      elapsed(job.started_at, job.finished_at),
                  )}
                </span>
              </Row>
              {metadata.value.repair_strategy && (
                <Row label="Repair">{metadata.value.repair_strategy}</Row>
              )}
              <Row label="From">
                <span className="selectable num break-all text-[11px] text-ink-faint">
                  {metadata.value.source_image_sha256[0]?.slice(0, 24) ?? "—"}
                </span>
              </Row>
            </dl>
          ) : (
            <p className="text-[13px] text-ink-muted">No metadata was written.</p>
          )}
          {metadata.value && (
            <p className="mt-3 border-t border-hairline pt-3 text-[12px] text-ink-faint">
              {metadata.value.reproducibility_note}
            </p>
          )}
        </Card>
      </div>

      <Card title="Files" subtitle="Already on disk. Saving makes a copy where you want it.">
        <ul className="space-y-1.5">
          {available.map((entry) => (
            <SaveRow
              key={entry.key}
              jobId={job.id}
              artifactKey={entry.key}
              label={entry.label}
              extension={entry.extension}
              path={job.artifacts[entry.key]!}
            />
          ))}
        </ul>
      </Card>
    </div>
  );
}

const ISSUE_COLOUR: Record<QualityIssue["severity"], string> = {
  info: "text-ink-faint",
  warning: "text-warn",
  fatal: "text-bad",
};

function SaveRow({
  jobId,
  artifactKey,
  label,
  extension,
  path,
}: {
  jobId: string;
  artifactKey: string;
  label: string;
  extension: string;
  path: string;
}) {
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [failure, setFailure] = useState<string | null>(null);

  const onSave = useCallback(async () => {
    setFailure(null);
    const destination = await save({
      defaultPath: `${fileName(path)}`,
      filters: [{ name: label, extensions: [extension] }],
    });
    if (!destination) return;
    setState("saving");
    try {
      await invoke<number>("save_artifact", { jobId, name: artifactKey, destination });
      setState("saved");
    } catch (error) {
      setState("idle");
      const shaped = error as { message?: string };
      setFailure(shaped?.message ?? String(error));
    }
  }, [artifactKey, extension, jobId, label, path]);

  return (
    <li className="flex items-center gap-3 rounded-md bg-raised px-3 py-2">
      <span className="w-28 shrink-0 text-[13px]">{label}</span>
      <span className="selectable num min-w-0 flex-1 truncate text-[11px] text-ink-faint" title={path}>
        {path}
      </span>
      {failure && <span className="text-[11px] text-bad">{failure}</span>}
      <Button tone="quiet" onClick={() => void onSave()} disabled={state === "saving"}>
        {state === "saving" ? <Spinner /> : <DownloadIcon className="text-[15px]" />}
        {state === "saved" ? "Saved" : "Save as…"}
      </Button>
    </li>
  );
}
